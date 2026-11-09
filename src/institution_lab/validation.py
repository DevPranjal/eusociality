from __future__ import annotations

import asyncio
import json
import sys
import uuid
from collections.abc import Callable
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, StrictUndefined

from .config import RunConfig
from .host import AwakeLease
from .recording import redact
from .runner import Runner
from .runtime.docker import DockerImages, DockerNetwork, DockerWorker


async def wait_for_checkpoint(
    task: asyncio.Task,
    predicate: Callable[[], bool],
    timeout: int,
) -> None:
    async with asyncio.timeout(timeout):
        while not predicate():
            if task.done():
                await task
                raise RuntimeError("run_ended_before_control_checkpoint")
            await asyncio.sleep(0.2)


async def control_command(output: Path, action: str, target: str | None = None) -> None:
    arguments = [sys.executable, "-m", "institution_lab", "control", str(output), action]
    if target is not None:
        arguments.extend(["--target", target])
    process = await asyncio.create_subprocess_exec(
        *arguments,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await process.communicate()
    if process.returncode:
        raise RuntimeError(stderr.decode())
    if stdout.decode().strip() != "queued":
        raise RuntimeError("control_was_not_queued")


async def check_controls(config: RunConfig, output: Path) -> dict:
    controller = Runner(config, output)
    controller.store.control("pause")
    task = asyncio.create_task(controller.run())

    def usage_observed() -> bool:
        with controller.store.connect() as connection:
            return bool(
                connection.execute(
                    "SELECT 1 FROM events WHERE kind='runtime' "
                    "AND json_extract(body,'$.event.type')='assistant.usage' LIMIT 1"
                ).fetchone()
            )

    try:
        await wait_for_checkpoint(task, lambda: controller.initialized, 60)
        await wait_for_checkpoint(task, lambda: controller.store.state()["status"] == "paused", 120)
        with controller.store.connect() as connection:
            activations = connection.execute(
                "SELECT count(*) FROM events WHERE kind='activation'"
            ).fetchone()[0]
        if activations:
            raise RuntimeError("paused_run_started_agent_work")
        await control_command(output, "resume")
        await wait_for_checkpoint(task, usage_observed, 120)
        await control_command(output, "stop")
        result = await task
        if result["status"] != "cancelled" or not result["intervened"]:
            raise RuntimeError("control_roundtrip_did_not_cancel_the_run")
        return {
            "paused_before_work": True,
            "actual_model_usage_observed": True,
            "cli_resume": True,
            "cli_stop": True,
            "result": result,
        }
    finally:
        if not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)


async def check_outage(config: RunConfig, output: Path) -> dict:
    controller = Runner(config, output)
    (output / "validation.json").write_text(
        json.dumps(
            {
                "intervention": "unavailable",
                "trigger": "first_successful_claim_or_accept",
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    task = asyncio.create_task(controller.run())
    target: str | None = None

    def ownership_observed() -> bool:
        nonlocal target
        with controller.store.connect() as connection:
            row = connection.execute(
                "SELECT actor FROM events WHERE kind IN ('claim','accept') "
                "AND json_extract(body,'$.result.ok')=1 ORDER BY seq LIMIT 1"
            ).fetchone()
        if row:
            target = row["actor"]
        return target is not None

    try:
        await wait_for_checkpoint(task, ownership_observed, 180)
        assert target is not None
        await control_command(output, "unavailable", target)
        result = await task
        if target not in result["failed_workers"] or not result["intervened"]:
            raise RuntimeError("outage_intervention_not_observed")
        return {"outage_target": target, "result": result}
    finally:
        if not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)


async def check_recovery(config: RunConfig, output: Path) -> dict:
    output.mkdir(parents=True, exist_ok=False)
    templates = Environment(
        loader=FileSystemLoader(config.prompt_repository),
        undefined=StrictUndefined,
    )
    events: list[dict] = []
    identifier = "continuity-" + uuid.uuid4().hex

    async def request(action: str, payload: dict, key: str) -> dict:
        return {"ok": False, "reason": "no_institution_in_runtime_probe"}

    def record(actor: str, message: dict) -> None:
        item = redact({"actor": actor, **message})
        events.append(item)
        with (output / "events.jsonl").open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(item) + "\n")

    network = DockerNetwork(
        DockerImages(config.runtime.worker_image, config.runtime.proxy_image),
        uuid.uuid4().hex[:12],
    )
    worker = DockerWorker(network, "recovery", request, record)
    with AwakeLease(config.keep_awake):
        try:
            network.start()
            await worker.start(
                {
                    **config.runtime.model_dump(),
                    "turn_timeout_seconds": 20,
                    "system_message": "",
                    "tool_description": templates.get_template("tools/probe.md.j2").render(),
                }
            )
            first = await worker.message(
                templates.get_template("probes/recovery-start.md.j2").render(identifier=identifier),
                timeout=45,
            )
            if first["outcome"] != "timed_out":
                raise RuntimeError("recovery_probe_did_not_exercise_watchdog")
            native_started = any(
                event.get("event", {}).get("type") == "tool.execution_start"
                and event["event"].get("data", {}).get("toolName") == "bash"
                for event in events
            )
            if not native_started or not await worker.code_idle():
                raise RuntimeError("coding_process_not_started_or_not_quiescent_after_abort")
            second = await worker.message(
                templates.get_template("probes/recovery-finish.md.j2").render(),
                timeout=45,
            )
            replies = [
                event["result"].get("data", {}).get("content", "")
                for event in events
                if event["kind"] == "turn_result" and event.get("result")
            ]
            ready = [event for event in events if event["kind"] == "ready"]
            if (
                second["outcome"] != "completed"
                or len(ready) != 1
                or not any(identifier in reply for reply in replies)
            ):
                raise RuntimeError("same_session_memory_continuity_not_verified")
            result = {
                "watchdog_exercised": True,
                "coding_process_quiescent": True,
                "same_session": ready[0]["session_id"],
                "memory_continuity_verified": True,
            }
            (output / "report.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
            return result
        finally:
            try:
                await worker.close()
            finally:
                network.close()
