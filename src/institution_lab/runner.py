from __future__ import annotations

import asyncio
import hashlib
import importlib.metadata
import json
import shutil
import subprocess
import sys
import time
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import asdict
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, StrictUndefined
from pydantic import ValidationError

from .admission import preflight, validate_static
from .config import RunConfig
from .evaluation.commands import CommandSuite
from .evaluation.evaluator import EvaluationInfrastructureFailure, Evaluator
from .evaluation.workload import Workload
from .host import AwakeLease
from .institution.rules import GoalGate
from .institution.service import Institution, Rejected
from .project import Project, ProjectRejected, docker
from .prompts import render_system
from .recording import redact
from .runtime.docker import DockerImages, DockerNetwork, DockerWorker, WorkerRuntimeFailure
from .sources import SourceReader, SourceRejected
from .store import RequestConflict, Store


async def within_deadline(operation: Callable[[], Awaitable[str]], seconds: float) -> str:
    if seconds <= 0:
        return "deadline"
    deadline = asyncio.timeout(seconds)
    try:
        async with deadline:
            return await operation()
    except TimeoutError:
        if not deadline.expired():
            raise
        return "deadline"


def unseen_updates(actor: str, seen: int, state: dict, *, inbox_seen: int | None = None) -> bool:
    return state.get("inbox_activity", {}).get(actor, -1) > (
        seen if inbox_seen is None else inbox_seen
    ) or any(
        version > seen for source, version in state.get("activity", {}).items() if source != actor
    )


def workflow_priority(
    actor: str, activations: int, state: dict, minimum_approvals: int = 1
) -> tuple[int, int, str]:
    completion_ready = any(
        proposal["actor"] == actor
        and proposal["status"] == "pending"
        and proposal["head"] == state["head"]
        and proposal["work_revision"] == state["work_revision"]
        and sum(review["approve"] for review in proposal["reviews"].values()) >= minimum_approvals
        and not any(not review["approve"] for review in proposal["reviews"].values())
        for proposal in state.get("goal_proposals", {}).values()
    )
    integration_ready = any(
        proposal["actor"] == actor
        and proposal["status"] == "pending"
        and proposal["evaluation"]["passed"]
        and any(review["approve"] for review in proposal["reviews"].values())
        for proposal in state.get("proposals", {}).values()
    )
    return (0 if completion_ready else 1 if integration_ready else 2, activations, actor)


class Runner:
    def __init__(self, config: RunConfig, output: Path) -> None:
        self.admission = validate_static(config)
        self.config = config
        self.output = output.resolve()
        self.output.mkdir(parents=True, exist_ok=False)
        self.workload = Workload.load(config.workload) if config.workload else None
        self.outcome_suite = (
            CommandSuite.load(config.outcome_suite) if config.outcome_suite else None
        )
        self.store = Store(self.output / "events.sqlite")
        self.institution = Institution(config, self.store)
        self.templates = Environment(
            loader=FileSystemLoader(config.prompt_repository),
            undefined=StrictUndefined,
        )
        self.images = DockerImages(
            docker("image", "inspect", "--format", "{{.Id}}", config.runtime.worker_image),
            docker("image", "inspect", "--format", "{{.Id}}", config.runtime.proxy_image),
        )
        self.network = DockerNetwork(self.images, uuid.uuid4().hex[:12])
        self.project = Project(self.output, self.workload)
        self.evaluator = Evaluator(
            self.workload,
            self.images.worker,
            retries=config.runtime.evaluation_retries,
            on_retry=lambda attempt, detail: self.store.event(
                "controller",
                "evaluation_startup_retry",
                {"attempt": attempt, "detail": detail},
            ),
        )
        self.image_pins: list[str] = []
        self.workers: dict[str, DockerWorker] = {}
        self.workspace_locks: dict[str, asyncio.Lock] = {}
        self.integration_lock = asyncio.Lock()
        self.active: dict[str, asyncio.Task] = {}
        self.activations: dict[str, int] = {}
        self.seen: dict[str, int] = {}
        self.last_board: dict[str, int] = {}
        self.seen_inbox: dict[str, int] = {}
        self.last_inbox: dict[str, int] = {}
        self.failed_workers: set[str] = set()
        self.recoveries: dict[str, int] = {}
        self.resume_pending: set[str] = set()
        self.quiescent_rechecks: dict[str, int] = {}
        self.stop_reason: str | None = None
        self.closing = False
        self.started = time.time()
        self.initialized = False
        self.sources = SourceReader(config.research, self.output / "sources")
        self.io_actions = {
            "submit": self.submit,
            "integrate": self.integrate,
            "sync": self.sync,
            "research": self.research,
        }

    def event(self, actor: str, message: dict) -> None:
        self.store.event(actor, message["kind"], redact(message))
        event = message.get("event") or {}
        if event.get("type") == "assistant.usage":
            actual = (event.get("data") or {}).get("model")
            if actual and actual != self.config.runtime_for(actor).model:
                raise RuntimeError(f"actual_model_mismatch:{actor}:{actual}")

    async def capture_workspace(self, actor: str, label: str) -> None:
        from .paths import relative_file

        helper = Path(__file__).parent / "runtime" / "workspace_reader.py"
        worker = self.workers[actor]
        try:
            captured = json.loads(
                await asyncio.to_thread(
                    docker,
                    "exec",
                    "--user",
                    "1001:1001",
                    worker.name,
                    "python",
                    "-I",
                    "-c",
                    helper.read_text(encoding="utf-8"),
                )
            )
        except (RuntimeError, json.JSONDecodeError, subprocess.TimeoutExpired, OSError) as error:
            self.store.event(actor, "workspace_capture_error", {"detail": str(error)})
            return
        identifier = uuid.uuid4().hex
        directory = self.output / "workspaces" / actor / identifier
        directory.mkdir(parents=True)
        errors = list(captured["errors"])
        written = 0
        for name, content in captured["files"].items():
            try:
                relative = relative_file(name)
                target = directory / relative
                if target.is_reserved():
                    raise ValueError("reserved_filename")
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(redact(content), encoding="utf-8", newline="")
                written += 1
            except (ValueError, OSError) as error:
                errors.append({"path": name, "reason": type(error).__name__})
        self.store.event(
            actor,
            "workspace_snapshot",
            {
                "id": identifier,
                "label": label,
                "errors": errors,
                "file_count": written,
            },
        )

    async def request(self, actor: str, action: str, payload: dict, key: str) -> dict:
        try:
            arguments = self.institution.validate(action, payload)
            previous = self.store.outcome(actor, key, action, arguments)
            if previous is not None:
                self.store.event(actor, "request_replay", {"action": action, "request_id": key})
                return previous
            if self.closing:
                raise Rejected("run_stopping")
            self.institution.preconditions(actor, action, arguments, self.store.state())
            handler = self.io_actions.get(action)
            result = (
                await handler(actor, arguments, key)
                if handler
                else self.institution.act(actor, action, arguments, key)
            )
            if action == "board" and result["ok"]:
                self.last_board[actor] = result["version"]
            if action == "inbox" and result["ok"]:
                self.last_inbox[actor] = result["version"]
            return result
        except (
            Rejected,
            ValidationError,
            ProjectRejected,
            RequestConflict,
            SourceRejected,
        ) as error:
            result = {"ok": False, "reason": str(error)}
            self.store.event(actor, "invalid_action", {"action": action, "result": result})
            return result

    def selected_checks(self, ticket: str, state: dict) -> list[str]:
        return [
            key
            for key, value in state["tickets"].items()
            if key == ticket or value["status"] == "complete"
        ]

    def dependency_files(self, ticket: str, state: dict) -> set[str]:
        pending = [ticket]
        seen = set()
        result = set()
        while pending:
            identifier = pending.pop()
            if identifier in seen:
                continue
            seen.add(identifier)
            definition = state["tickets"][identifier]
            result.update(definition.get("files", []))
            if definition.get("module"):
                result.add(definition["module"])
            pending.extend(definition["dependencies"])
        return result

    async def research(self, actor: str, args: dict, key: str) -> dict:
        result = await self.sources.fetch(args["url"])
        return self.store.transact(
            actor,
            key,
            "research",
            args,
            lambda state: ({"ok": True, "source": result}, False),
        )

    async def evaluate(self, source: Path, tickets: list[str], state: dict) -> dict:
        if self.workload:
            result = await self.evaluator.evaluate(source, tickets)
        else:
            result = await self.evaluator.evaluate(source, tickets, state["tickets"])
        policies = await asyncio.to_thread(self.institution.engine.artifact_checks, source)
        if policies:
            result["policy_checks"] = [asdict(decision) for decision in policies]
            result["passed"] = result["passed"] and all(decision.allowed for decision in policies)
        return result

    async def submit(self, actor: str, args: dict, key: str) -> dict:
        async with self.workspace_locks[actor]:
            previous = self.store.outcome(actor, key, "submit", args)
            if previous is not None:
                return previous
            state = self.store.state()
            task = state["tickets"][args["ticket"]]
            self.institution.require(task["owner"] == actor, "not_ticket_owner")
            if self.workload:
                artifact, base, digest, diff = await asyncio.to_thread(
                    self.project.snapshot,
                    self.workers[actor],
                )
            else:
                artifact, base, digest, diff = await asyncio.to_thread(
                    self.project.snapshot,
                    self.workers[actor],
                    state["tickets"],
                    args["ticket"],
                )
            evaluation_source = artifact
            evaluated_base = base
            if self.config.integration_mode == "disjoint_files":
                async with self.integration_lock:
                    state = self.store.state()
                    evaluated_base = await asyncio.to_thread(
                        self.project.compatible_integration_base,
                        artifact,
                        base,
                        self.dependency_files(args["ticket"], state),
                    )
                    evaluation_source = await asyncio.to_thread(
                        self.project.evaluation_preview,
                        artifact,
                        base,
                        evaluated_base,
                    )
            evaluation = await self.evaluate(
                evaluation_source,
                self.selected_checks(args["ticket"], state),
                state,
            )
            evaluation["evaluated_base"] = evaluated_base
            evaluation["reviewed_base"] = base
            if evaluation_source != artifact:
                evaluation["preview_artifact"] = evaluation_source.name
                self.store.event(
                    actor,
                    "disjoint_evaluation_preview",
                    {
                        "reviewed_base": base,
                        "evaluated_base": evaluated_base,
                        "artifact": artifact.name,
                        "preview_artifact": evaluation_source.name,
                    },
                )
            return self.institution.register_proposal(
                actor,
                args["ticket"],
                base,
                digest,
                diff,
                evaluation,
                artifact.name,
                request_id=key,
            )

    async def integrate(self, actor: str, args: dict, key: str) -> dict:
        async with self.integration_lock:
            previous = self.store.outcome(actor, key, "integrate", args)
            if previous is not None:
                return previous
            state = self.store.state()
            candidate = state["proposals"][args["proposal"]]
            artifact = self.project.artifacts / candidate["artifact"]
            current_base = candidate["base"]
            compatible_base = False
            read_files: set[str] = set()
            if self.config.integration_mode == "disjoint_files":
                read_files = self.dependency_files(candidate["ticket"], state)
                current_base = await asyncio.to_thread(
                    self.project.compatible_integration_base,
                    artifact,
                    candidate["base"],
                    read_files,
                )
                compatible_base = current_base != candidate["base"]
            policy_checks = await asyncio.to_thread(
                self.institution.engine.artifact_checks,
                artifact,
            )
            if policy_checks and current_base != candidate["base"]:
                policy_source = await asyncio.to_thread(
                    self.project.evaluation_preview,
                    artifact,
                    candidate["base"],
                    current_base,
                )
                policy_checks = await asyncio.to_thread(
                    self.institution.engine.artifact_checks,
                    policy_source,
                )
            if any(not decision.allowed for decision in policy_checks):
                result = {
                    "ok": False,
                    "reason": "current_publication_policy_denied",
                    "rules": [asdict(decision) for decision in policy_checks],
                }
                return self.store.transact(
                    actor,
                    key,
                    "integrate",
                    args,
                    lambda state: (result, False),
                )
            result = self.institution.reserve_integration(
                actor, args["proposal"], "reserve:" + key, compatible_base=compatible_base
            )
            if not result["ok"]:
                return self.store.transact(
                    actor, key, "integrate", args, lambda state: (result, False)
                )
            proposal = result["proposal"]
            if compatible_base:
                self.store.event(
                    actor,
                    "disjoint_integration",
                    {
                        "proposal": proposal["id"],
                        "reviewed_base": proposal["base"],
                        "integration_base": current_base,
                        "dependency_files_checked": sorted(read_files),
                    },
                )
            head = await asyncio.to_thread(
                self.project.integrate, artifact, proposal["base"], current_base=current_base
            )
            state = self.store.state()
            evaluation = await self.evaluate(
                self.project.canonical,
                self.selected_checks(proposal["ticket"], state),
                state,
            )
            return self.institution.record_integration(
                proposal["id"], head, evaluation, actor=actor, request_id=key
            )

    async def sync(self, actor: str, args: dict, key: str) -> dict:
        async with self.integration_lock, self.workspace_locks[actor]:
            previous = self.store.outcome(actor, key, "sync", args)
            if previous is not None:
                return previous
            result = await asyncio.to_thread(self.project.sync, self.workers[actor])
            return self.store.transact(actor, key, "sync", args, lambda state: (result, False))

    def system_message(self, actor: str) -> str:
        state = self.store.state()
        role = state["agents"][actor]["role"]
        return render_system(self.templates, self.config, self.workload, actor, role)

    def freeze(self) -> None:
        resolved = self.config.model_dump(mode="json")
        resolved["images"] = asdict(self.images)
        resolved["image_pins"] = self.image_pins
        resolved["network_name"] = self.network.name
        resolved["config_hash"] = self.config.fingerprint()
        if self.outcome_suite:
            encoded_suite = json.dumps(
                self.outcome_suite.model_dump(mode="json"),
                sort_keys=True,
                indent=2,
            )
            (self.output / "outcome-suite.json").write_text(encoded_suite, encoding="utf-8")
            resolved["outcome_suite_sha256"] = hashlib.sha256(encoded_suite.encode()).hexdigest()
        if self.config.project_inputs:
            inputs = self.project.canonical / "inputs"
            shutil.copytree(inputs, self.output / "frozen-project-inputs")
            resolved["project_input_hashes"] = {
                path.relative_to(inputs).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
                for path in inputs.rglob("*")
                if path.is_file()
            }
        resolved["admission"] = self.admission
        resolved["started_at"] = self.started
        resolved["runtime_scheduling"] = self.config.scheduler_mode
        resolved["plugin_sources"] = self.institution.engine.sources
        resolved["prompt_hashes"] = {
            path.relative_to(self.config.prompt_repository).as_posix(): hashlib.sha256(
                path.read_bytes()
            ).hexdigest()
            for path in self.config.prompt_repository.rglob("*.j2")
        }
        if self.config.workload:
            resolved["workload_hash"] = hashlib.sha256(
                self.config.workload.read_bytes()
            ).hexdigest()
        package_root = Path(__file__).parent
        resolved["source_hashes"] = {
            path.relative_to(package_root).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in package_root.rglob("*.py")
        }
        resolved["versions"] = {
            "python": sys.version.split()[0],
            "copilot_sdk": importlib.metadata.version("github-copilot-sdk"),
            "harness": importlib.metadata.version("institution-lab"),
        }
        resolved["dependency_versions"] = {
            package: importlib.metadata.version(package)
            for package in ("pydantic", "PyYAML", "jinja2", "jsonpatch", "fastapi", "httpx")
        }
        lockfile = Path(__file__).parents[2] / "requirements.lock"
        if lockfile.is_file():
            shutil.copyfile(lockfile, self.output / "dependencies.lock")
        source_snapshot = self.output / "source-snapshot"
        shutil.copytree(package_root, source_snapshot, ignore=shutil.ignore_patterns("__pycache__"))
        if self.config.workload and self.workload:
            frozen_workload = self.output / "frozen-workload"
            frozen_workload.mkdir()
            shutil.copyfile(self.config.workload, frozen_workload / "manifest.yaml")
            shutil.copyfile(
                self.config.workload.parent / "stub.py.template",
                frozen_workload / "stub.py.template",
            )
            shutil.copytree(
                self.config.workload.parent / self.workload.scaffold,
                frozen_workload / self.workload.scaffold,
            )
        frozen_templates = self.output / "prompt-source"
        for source in self.config.prompt_repository.rglob("*.j2"):
            target = frozen_templates / source.relative_to(self.config.prompt_repository)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, target)
        self.templates = Environment(
            loader=FileSystemLoader(frozen_templates),
            undefined=StrictUndefined,
            auto_reload=False,
        )
        (self.output / "resolved.json").write_text(json.dumps(resolved, indent=2), encoding="utf-8")
        prompts = self.output / "prompts"
        prompts.mkdir()
        for actor in self.store.state()["agents"]:
            (prompts / f"{actor}.md").write_text(self.system_message(actor), encoding="utf-8")
        (prompts / "start.md").write_text(
            self.templates.get_template(
                "events/start.md.j2" if self.workload else "events/society/start.md.j2"
            ).render(),
            encoding="utf-8",
        )

    async def start_worker(self, actor: str) -> None:
        async def handler(action: str, payload: dict, key: str) -> dict:
            return await self.request(actor, action, payload, key)

        worker = DockerWorker(self.network, actor, handler, self.event)
        self.workers[actor] = worker
        self.workspace_locks[actor] = asyncio.Lock()
        await worker.start(
            {
                **self.config.runtime_for(actor).model_dump(),
                "role": self.store.state()["agents"][actor]["role"],
                "system_message": (self.output / "prompts" / f"{actor}.md").read_text(
                    encoding="utf-8"
                ),
                "tool_description": self.templates.get_template(
                    "tools/organization.md.j2" if self.workload else "tools/society.md.j2"
                ).render(research=self.config.research.model_dump()),
            }
        )
        await asyncio.to_thread(self.project.seed, worker)
        await self.capture_workspace(actor, "Initial workspace")
        self.seen[actor] = -1
        self.seen_inbox[actor] = -1
        self.last_inbox[actor] = -1
        self.activations[actor] = 0
        self.recoveries[actor] = 0

    async def activate(self, actor: str) -> None:
        self.resume_pending.discard(actor)
        state = self.store.state()
        self.last_board[actor] = state["version"]
        folder = "events" if self.workload else "events/society"
        template = (
            f"{folder}/start.md.j2" if self.activations[actor] == 0 else f"{folder}/continue.md.j2"
        )
        prompt = self.templates.get_template(template).render(
            updates=json.dumps({"public_state_version": state["version"]})
        )
        self.activations[actor] += 1
        self.store.event(actor, "activation", {"number": self.activations[actor]})
        try:
            result = await self.workers[actor].message(
                prompt, timeout=self.config.runtime.turn_timeout_seconds + 20
            )
        except WorkerRuntimeFailure as error:
            self.store.event(
                actor,
                "worker_failure",
                {
                    "error": str(error),
                    "session_resume_confirmed": False,
                },
            )
            self.failed_workers.add(actor)
            self.institution.worker_unavailable(actor)
            await self.workers[actor].close()
            return
        self.seen[actor] = self.last_board[actor]
        self.seen_inbox[actor] = self.last_inbox[actor]
        await self.capture_workspace(actor, f"After turn {self.activations[actor]}")
        if result["outcome"] == "timed_out":
            self.store.event(actor, "worker_turn_timeout", {"session_preserved": True})
            if self.recoveries[actor] < self.config.recoveries:
                self.recoveries[actor] += 1
                self.resume_pending.add(actor)
                self.store.event(
                    actor,
                    "recovery",
                    {
                        "attempt": self.recoveries[actor],
                        "kind": "same_session_after_abort",
                    },
                )
            else:
                self.failed_workers.add(actor)
                self.institution.worker_unavailable(actor)
                await self.workers[actor].close()

    async def loop(self) -> str:
        while time.time() - self.started < self.config.deadline_seconds:
            for control in self.store.controls():
                self.store.event("operator", "intervention", control)
                self.store.update(lambda current: current["interventions"].append(control))
                if control["kind"] == "stop":
                    return "cancelled"
                if control["kind"] == "pause":
                    self.store.update(lambda state: state.update(status="draining"))
                if control["kind"] == "resume":
                    self.store.update(lambda state: state.update(status="running"))
                if control["kind"] == "unavailable":
                    actor = control["target"]
                    self.failed_workers.add(actor)
                    if actor in self.active:
                        self.active[actor].cancel()
                    async with self.integration_lock:
                        self.institution.worker_unavailable(actor)
                    await self.capture_workspace(actor, "Before worker shutdown")
                    await self.workers[actor].close()

            state = self.store.state()
            complete = (
                all(task["status"] == "complete" for task in state["tickets"].values())
                if self.workload
                else state["goal_status"] == "completed"
            )
            if complete:
                return "completed"
            if state["status"] == "draining" and not self.active:
                idle = await asyncio.gather(
                    *(
                        worker.code_idle()
                        for actor, worker in self.workers.items()
                        if actor not in self.failed_workers
                    )
                )
                if all(idle):
                    self.store.update(lambda current: current.update(status="paused"))
                    state["status"] = "paused"
            if state["status"] == "running":
                minimum_approvals = next(
                    (
                        rule.parameters.independent_approvals
                        for rule in self.institution.engine.rules
                        if isinstance(rule, GoalGate)
                    ),
                    0,
                )
                for actor in sorted(
                    state["agents"],
                    key=lambda item: (
                        workflow_priority(item, self.activations[item], state, minimum_approvals)
                        if self.config.scheduler_mode == "workflow"
                        else (2, self.activations[item], item)
                    ),
                ):
                    eligible = (
                        actor not in self.active
                        and actor not in self.failed_workers
                        and self.activations[actor] < self.config.max_activations
                        and (
                            self.activations[actor] == 0
                            or actor in self.resume_pending
                            or (
                                self.config.scheduler_mode == "workflow"
                                and workflow_priority(
                                    actor, self.activations[actor], state, minimum_approvals
                                )[0]
                                < 2
                            )
                            or unseen_updates(
                                actor,
                                self.seen[actor],
                                state,
                                inbox_seen=self.seen_inbox[actor],
                            )
                        )
                    )
                    if eligible and len(self.active) < self.config.concurrency:
                        self.active[actor] = asyncio.create_task(self.activate(actor))
                if not self.active:
                    recheck = (
                        next(
                            (
                                actor
                                for actor in sorted(
                                    state["agents"], key=lambda item: (self.activations[item], item)
                                )
                                if actor not in self.failed_workers
                                and self.activations[actor] < self.config.max_activations
                                and self.quiescent_rechecks.get(actor, 0)
                                < self.config.quiescent_rechecks
                            ),
                            None,
                        )
                        if self.config.scheduler_mode == "workflow"
                        else None
                    )
                    if recheck is not None:
                        self.quiescent_rechecks[recheck] = (
                            self.quiescent_rechecks.get(recheck, 0) + 1
                        )
                        self.store.event(
                            recheck,
                            "quiescent_recheck",
                            {
                                "attempt": self.quiescent_rechecks[recheck],
                                "reason": "goal_remains_open_without_new_activity",
                            },
                        )
                        self.active[recheck] = asyncio.create_task(self.activate(recheck))
                        continue
                    self.stop_reason = (
                        "activation_limit"
                        if any(
                            self.activations[a] >= self.config.max_activations
                            and (
                                unseen_updates(
                                    a, self.seen[a], state, inbox_seen=self.seen_inbox[a]
                                )
                                or (
                                    self.config.scheduler_mode == "workflow"
                                    and workflow_priority(
                                        a, self.activations[a], state, minimum_approvals
                                    )[0]
                                    < 2
                                )
                            )
                            for a in state["agents"]
                            if a not in self.failed_workers
                        )
                        else "no_unseen_updates"
                    )
                    return "stalled"
            if self.active:
                finished, _ = await asyncio.wait(
                    self.active.values(), timeout=0.5, return_when=asyncio.FIRST_COMPLETED
                )
                for actor, task in list(self.active.items()):
                    if task in finished:
                        del self.active[actor]
                        if not task.cancelled():
                            error = task.exception()
                            if error is not None:
                                raise error
            else:
                await asyncio.sleep(0.5)
        return "deadline"

    async def run(self) -> dict:
        with AwakeLease(self.config.keep_awake):
            return await self._run()

    async def _run(self) -> dict:
        terminal = "failed"
        final = None
        final_error = None
        independent_outcome = None
        tickets: list[str] = []
        try:
            for image in asdict(self.images).values():
                tag = "institution-lab-recorded:" + image.removeprefix("sha256:")
                await asyncio.to_thread(docker, "tag", image, tag)
                self.image_pins.append(tag)
            frozen_runtime = self.config.runtime.model_copy(
                update={"worker_image": self.images.worker, "proxy_image": self.images.proxy},
            )
            admission_config = self.config.model_copy(update={"runtime": frozen_runtime})
            self.admission["runtime_preflight"] = await preflight(admission_config)
            head = await asyncio.to_thread(
                self.project.initialize,
                self.config.workload,
                self.config.project_inputs,
            )
            self.institution.initialize(
                [task.public() for task in self.workload.tickets] if self.workload else [],
                head,
            )
            self.initialized = True
            self.freeze()
            await asyncio.to_thread(self.network.start)
            startup_slots = asyncio.Semaphore(self.config.concurrency)

            async def start(actor: str) -> None:
                async with startup_slots:
                    await self.start_worker(actor)

            async with asyncio.TaskGroup() as group:
                for actor in self.store.state()["agents"]:
                    group.create_task(start(actor))
            self.store.update(lambda state: state.update(status="running"))
            remaining = self.config.deadline_seconds - (time.time() - self.started)
            terminal = await within_deadline(self.loop, remaining)
        finally:
            error = sys.exception()
            self.closing = True
            try:
                for task in self.active.values():
                    task.cancel()
                await asyncio.gather(*self.active.values(), return_exceptions=True)
                async with self.integration_lock:
                    pass
                for lock in self.workspace_locks.values():
                    async with lock:
                        pass
                for actor, worker in self.workers.items():
                    if (
                        actor not in self.failed_workers
                        and worker.process is not None
                        and worker.process.returncode is None
                    ):
                        await self.capture_workspace(actor, "Run checkpoint")
                if self.initialized and error is None:
                    state = self.store.state()
                    tickets = (
                        [task.id for task in self.workload.tickets]
                        if self.workload
                        else [
                            key
                            for key, task in state["tickets"].items()
                            if task["status"] != "withdrawn"
                        ]
                    )
                    try:
                        final = await self.evaluate(self.project.canonical, tickets, state)
                    except EvaluationInfrastructureFailure as evaluation_error:
                        final_error = evaluation_error
                        final = {
                            "passed": False,
                            "checks": [],
                            "exit_code": None,
                            "basis": "infrastructure_failure",
                            "failure": str(evaluation_error),
                        }
                    self.store.event("controller", "final_evaluation", final)
                    if self.outcome_suite:
                        try:
                            independent_outcome = await self.evaluator.evaluate(
                                self.project.canonical,
                                [case.id for case in self.outcome_suite.cases],
                                commands=self.outcome_suite.cases,
                            )
                        except EvaluationInfrastructureFailure as audit_error:
                            final_error = audit_error
                            independent_outcome = {
                                "passed": None,
                                "checks": [],
                                "basis": "infrastructure_failure",
                                "failure": str(audit_error),
                                "available": False,
                            }
                        self.store.event(
                            "controller", "independent_output_audit", independent_outcome
                        )
                    if final_error or (terminal == "completed" and not final["passed"]):
                        terminal = "failed"
                        self.stop_reason = (
                            "independent_output_audit_infrastructure_failure"
                            if independent_outcome and independent_outcome.get("available") is False
                            else "final_evaluation_infrastructure_failure"
                            if final_error
                            else "final_checks_failed"
                        )
            finally:
                cleanup_cause = sys.exception() or error or final_error
                try:
                    if cleanup_cause:
                        terminal = "failed"
                    if self.initialized:
                        self.store.update(lambda state: state.update(status=terminal))
                        self.store.event(
                            "controller",
                            "run_end",
                            {
                                "status": terminal,
                                "error": repr(cleanup_cause) if cleanup_cause else None,
                            },
                        )
                finally:
                    results = await asyncio.gather(
                        *(worker.close() for worker in self.workers.values()),
                        return_exceptions=True,
                    )
                    try:
                        for result in results:
                            if isinstance(result, BaseException):
                                self.store.event(
                                    "controller",
                                    "cleanup_error",
                                    {"error": repr(result)},
                                )
                    finally:
                        await asyncio.to_thread(self.network.close)
        state = self.store.state()
        assert final is not None
        summary = {
            "status": state["status"],
            "stop_reason": self.stop_reason,
            "population": self.config.population,
            "coordination": self.config.coordination,
            "elapsed_seconds": time.time() - self.started,
            "verified_tickets": sum(
                task["status"] == "complete" for task in state["tickets"].values()
            ),
            "total_tickets": len(tickets),
            "final_evaluation": final,
            "independent_outcome": independent_outcome,
            "activations": self.activations,
            "failed_workers": sorted(self.failed_workers),
            "intervened": bool(state["interventions"]),
            "goal": self.config.goal,
            "completion_basis": "fixed_workload_checks"
            if self.workload
            else "agent_goal_attestation_and_recorded_checks",
            "accepted_completion": state.get("accepted_completion"),
        }
        (self.output / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
        return summary
