from __future__ import annotations

import asyncio
import json
import logging
import uuid
from collections.abc import Callable
from pathlib import Path

from ..runtime.docker import docker
from .commands import CommandCase
from .workload import Workload


class EvaluationInfrastructureFailure(RuntimeError):
    pass


class Evaluator:
    def __init__(
        self,
        workload: Workload | None,
        image: str,
        timeout: int = 30,
        *,
        retries: int = 2,
        on_retry: Callable[[int, str], None] | None = None,
    ) -> None:
        self.workload = workload
        self.image = image
        self.timeout = timeout
        self.retries = retries
        self.on_retry = on_retry

    async def evaluate(
        self,
        source: Path,
        tickets: list[str],
        definitions: dict | None = None,
        *,
        commands: list[CommandCase] | None = None,
    ) -> dict:
        if commands and (
            len({case.id for case in commands}) != len(commands)
            or {case.id for case in commands} != set(tickets)
        ):
            raise ValueError("audit_command_identifiers_must_match_selected_checks")
        for attempt in range(self.retries + 1):
            try:
                return await self._evaluate(source, tickets, definitions, commands)
            except EvaluationInfrastructureFailure as error:
                if attempt == self.retries:
                    raise
                if self.on_retry:
                    self.on_retry(attempt + 1, str(error))
                else:
                    logging.getLogger(__name__).warning("Evaluator startup retry: %s", error)
                await asyncio.sleep(attempt + 1)
        raise AssertionError("unreachable evaluation attempt")

    async def _evaluate(
        self,
        source: Path,
        tickets: list[str],
        definitions: dict | None,
        commands: list[CommandCase] | None = None,
    ) -> dict:
        selected = (
            [ticket for ticket in self.workload.tickets if ticket.id in tickets]
            if self.workload
            else []
        )
        inputs = (
            [{"id": case.id, "argv": case.argv, "input": case.input} for case in commands]
            if commands
            else [
                {"operation": ticket.id, "inputs": [case.input for case in ticket.cases]}
                for ticket in selected
            ]
            if self.workload
            else [definitions[key] for key in tickets]
            if definitions
            else []
        )
        if not inputs:
            return {
                "passed": False,
                "checks": [],
                "failure": "no_work_defined",
                "exit_code": None,
                "basis": "no_artifacts_or_checks",
            }
        identifier = "il-evaluation-" + uuid.uuid4().hex[:12]
        driver = Path(__file__).with_name(
            "command_driver.py"
            if commands
            else "driver.py"
            if self.workload
            else "artifact_driver.py"
        )
        process = await asyncio.create_subprocess_exec(
            "docker",
            "run",
            "--rm",
            "--pull",
            "never",
            "-i",
            "--name",
            identifier,
            "--label",
            "institution-lab=true",
            "--network",
            "none",
            "--read-only",
            "--tmpfs",
            "/tmp:rw,noexec,nosuid,size=64m",
            "--cap-drop",
            "ALL",
            "--security-opt",
            "no-new-privileges",
            "--memory",
            "256m",
            "--cpus",
            "1",
            "--pids-limit",
            "32",
            "--user",
            "1001:1001",
            "--workdir",
            "/workspace",
            "--mount",
            f"type=bind,src={source.resolve()},dst=/workspace,readonly",
            "--mount",
            f"type=bind,src={driver.resolve()},dst=/runner.py,readonly",
            "--env",
            "PYTHONPATH=/workspace",
            "--entrypoint",
            "python",
            self.image,
            "/runner.py",
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        assert process.stdin is not None and process.stdout is not None
        stdout = process.stdout
        stdin = process.stdin
        collected = bytearray()
        transport_error = None

        async def collect() -> bytes:
            nonlocal transport_error
            try:
                stdin.write(json.dumps(inputs).encode())
                await stdin.drain()
            except (BrokenPipeError, ConnectionResetError) as error:
                transport_error = repr(error)
            finally:
                stdin.close()
            while chunk := await stdout.read(16384):
                collected.extend(chunk)
                if len(collected) > 262144:
                    raise ValueError("candidate_output_limit")
            await process.wait()
            return bytes(collected)

        failure = None
        try:
            raw = await asyncio.wait_for(collect(), timeout=self.timeout)
        except (TimeoutError, ValueError, asyncio.CancelledError) as error:
            failure = type(error).__name__ + ":" + str(error)
            docker("rm", "-f", identifier)
            await process.wait()
            if isinstance(error, asyncio.CancelledError):
                raise
            raw = bytes(collected)
        outputs = []
        header, separator, payload = raw.partition(b"\n")
        try:
            ready = json.loads(header.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError):
            ready = None
        if transport_error or not separator or ready != {"evaluator_ready": True}:
            raise EvaluationInfrastructureFailure(
                "evaluator_startup_failed: trusted driver did not receive input or signal "
                f"readiness; {transport_error or failure or ''}; "
                + raw.decode("utf-8", errors="replace")[:2000]
            )
        if failure is None:
            try:
                outputs = json.loads(payload.decode("utf-8"))
            except (json.JSONDecodeError, UnicodeDecodeError) as error:
                failure = (
                    type(error).__name__ + ":" + payload.decode("utf-8", errors="replace")[:2000]
                )
        if not isinstance(outputs, list) or any(
            not isinstance(item, dict) or not isinstance(item.get("operation"), str)
            for item in outputs
        ):
            failure = "candidate_result_schema"
            outputs = []
        by_id = {item["operation"]: item for item in outputs}
        expected_ids = (
            set(tickets)
            if commands
            else {ticket.id for ticket in selected}
            if self.workload
            else set(tickets)
        )
        if set(by_id) != expected_ids or len(outputs) != len(by_id):
            failure = failure or "candidate_result_cardinality"
        checks = []
        for identifier in tickets:
            actual = by_id.get(identifier, {})
            expected = (
                next(case.expected for case in commands if case.id == identifier)
                if commands
                else [
                    case.expected
                    for ticket in selected
                    if ticket.id == identifier
                    for case in ticket.cases
                ]
            )
            checks.append(
                {
                    "ticket": identifier,
                    "passed": failure is None
                    and process.returncode == 0
                    and (
                        actual.get("failure") is None
                        and actual.get("exit_code")
                        == next(
                            case.expected_exit_code for case in commands if case.id == identifier
                        )
                        and actual.get("outputs") == expected
                        if commands
                        else actual.get("outputs") == expected
                        if self.workload
                        else actual.get("passed") is True
                    ),
                    "actual": actual,
                    "expected": expected,
                    **(
                        {
                            "group": next(case.group for case in commands if case.id == identifier),
                            "policy_assessment": next(
                                case.policy_assessment(
                                    actual.get("outputs"),
                                    failure is None
                                    and process.returncode == 0
                                    and isinstance(actual.get("stdout"), str)
                                    and not actual.get("output_truncated"),
                                    actual.get("stdout"),
                                )
                                for case in commands
                                if case.id == identifier
                            ),
                        }
                        if commands
                        else {}
                    ),
                }
            )
        return {
            "basis": "protected_command_outputs"
            if commands
            else "fixed_cases"
            if self.workload
            else "agent_defined_checks_and_artifacts",
            "passed": failure is None and bool(checks) and all(c["passed"] for c in checks),
            "checks": checks,
            "failure": failure,
            "exit_code": process.returncode,
        }
