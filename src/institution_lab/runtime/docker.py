from __future__ import annotations

import asyncio
import json
import subprocess
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from .protocol import identity

RequestHandler = Callable[[str, dict[str, Any], str], Awaitable[object]]
EventHandler = Callable[[str, dict[str, Any]], None]


class WorkerRuntimeFailure(RuntimeError):
    pass


class WorkerWatchdogExpired(WorkerRuntimeFailure):
    pass


def docker(*arguments: str, timeout: int = 120, strip: bool = True) -> str:
    result = subprocess.run(
        ["docker", *arguments], capture_output=True, encoding="utf-8", timeout=timeout, check=False
    )
    if result.returncode:
        raise RuntimeError(
            result.stderr.strip()
            or result.stdout.strip()
            or f"docker_command_failed:{arguments[0]}:exit_{result.returncode}"
        )
    return result.stdout.strip() if strip else result.stdout


def github_credential() -> str:
    result = subprocess.run(
        ["gh", "auth", "token"], capture_output=True, text=True, timeout=20, check=False
    )
    if result.returncode or not result.stdout.strip():
        raise RuntimeError("supported_github_credential_unavailable")
    return result.stdout.strip()


@dataclass(frozen=True)
class DockerImages:
    worker: str = "institution-lab-worker:dev"
    proxy: str = "institution-lab-proxy:dev"


class DockerNetwork:
    def __init__(self, images: DockerImages, run_id: str) -> None:
        self.images = images
        self.name = f"il-{run_id}"
        self.proxy = f"{self.name}-egress"
        self.created = False
        self.proxy_created = False

    def start(self) -> None:
        docker("network", "create", "--internal", "--label", "institution-lab=true", self.name)
        self.created = True
        docker(
            "run",
            "-d",
            "--name",
            self.proxy,
            "--label",
            "institution-lab=true",
            "--cap-drop",
            "ALL",
            "--cap-add",
            "SETUID",
            "--cap-add",
            "SETGID",
            "--security-opt",
            "no-new-privileges",
            "--memory",
            "128m",
            self.images.proxy,
        )
        self.proxy_created = True
        docker("network", "connect", "--alias", "egress", self.name, self.proxy)
        if docker("inspect", "--format", "{{.State.Running}}", self.proxy) != "true":
            raise RuntimeError("egress_proxy_startup_failed")

    def close(self) -> None:
        if self.proxy_created:
            docker("rm", "-f", self.proxy)
        if self.created:
            docker("network", "rm", self.name)


class DockerWorker:
    def __init__(
        self,
        network: DockerNetwork,
        agent_id: str,
        on_request: RequestHandler,
        on_event: EventHandler,
    ) -> None:
        self.network = network
        self.agent_id = agent_id
        self.name = f"{network.name}-{agent_id}"
        self.on_request = on_request
        self.on_event = on_event
        self.process: asyncio.subprocess.Process | None = None
        self.reader: asyncio.Task[None] | None = None
        self.errors: asyncio.Task[None] | None = None
        self.ready = asyncio.Event()
        self.turns: dict[str, asyncio.Future[dict]] = {}
        self.fatal: str | None = None
        self.token = ""

    async def write(self, message: dict[str, Any]) -> None:
        if self.process is None or self.process.stdin is None:
            raise RuntimeError("worker_not_started")
        try:
            self.process.stdin.write((json.dumps(message) + "\n").encode())
            await self.process.stdin.drain()
        except (BrokenPipeError, ConnectionResetError) as error:
            raise WorkerRuntimeFailure("worker_transport_closed") from error

    async def start(self, configuration: dict[str, Any]) -> None:
        self.token = github_credential()
        self.process = await asyncio.create_subprocess_exec(
            "docker",
            "run",
            "--rm",
            "--init",
            "-i",
            "--name",
            self.name,
            "--label",
            "institution-lab=true",
            "--network",
            self.network.name,
            "--label",
            "institution-lab.run=" + self.network.name,
            "--cap-drop",
            "ALL",
            "--cap-add",
            "SETUID",
            "--cap-add",
            "SETGID",
            "--cap-add",
            "KILL",
            "--security-opt",
            "no-new-privileges",
            "--memory",
            "1024m",
            "--cpus",
            "1",
            "--pids-limit",
            "128",
            "--user",
            "0:1001",
            "--env",
            "HTTPS_PROXY=http://egress:8888",
            "--env",
            "HTTP_PROXY=http://egress:8888",
            self.network.images.worker,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            limit=8 * 1024 * 1024,
        )
        self.reader = asyncio.create_task(self._read())
        self.reader.add_done_callback(self._reader_finished)
        self.errors = asyncio.create_task(self._read_errors())
        await self.write({**configuration, "github_token": self.token})
        try:
            await asyncio.wait_for(self.ready.wait(), timeout=120)
        except TimeoutError as error:
            raise WorkerRuntimeFailure("worker_readiness_timeout") from error
        if self.fatal:
            raise WorkerRuntimeFailure(self.fatal)

    def _reader_finished(self, task: asyncio.Task[None]) -> None:
        if task.cancelled():
            return
        error = task.exception()
        if error is not None:
            self.fatal = f"{type(error).__name__}:{error}"
            self.on_event(self.agent_id, {"kind": "transport_error", "detail": self.fatal})
            self.ready.set()
            for future in self.turns.values():
                if not future.done():
                    future.set_exception(error)

    async def _read(self) -> None:
        assert self.process is not None and self.process.stdout is not None
        async for line in self.process.stdout:
            message = json.loads(line.decode().replace(self.token, "[REDACTED]"))
            kind = message["kind"]
            if kind == "request":
                result = await self.on_request(message["action"], message["payload"], message["id"])
                await self.write({"kind": "response", "id": message["id"], "result": result})
            elif kind == "ready":
                expected = identity()
                if any(message.get(key) != value for key, value in expected.items()):
                    self.fatal = "worker_image_protocol_or_source_mismatch_rebuild_image"
                self.ready.set()
            elif kind == "turn_end":
                future = self.turns.get(message["id"])
                if future is not None and not future.done():
                    future.set_result(message)
            elif kind == "fatal":
                self.fatal = message["detail"]
                self.ready.set()
                for future in self.turns.values():
                    if not future.done():
                        future.set_exception(WorkerRuntimeFailure(self.fatal))
            self.on_event(self.agent_id, {**message, "container_name": self.name})
        self.ready.set()
        if self.fatal is None:
            self.fatal = "worker_transport_closed"
        for future in self.turns.values():
            if not future.done():
                future.set_exception(WorkerRuntimeFailure(self.fatal))

    async def _read_errors(self) -> None:
        assert self.process is not None and self.process.stderr is not None
        async for line in self.process.stderr:
            self.on_event(
                self.agent_id,
                {"kind": "stderr", "detail": line.decode().replace(self.token, "[REDACTED]")},
            )

    async def message(self, prompt: str, timeout: float) -> dict:
        if self.fatal:
            raise WorkerRuntimeFailure(self.fatal)
        identifier = str(uuid.uuid4())
        self.turns[identifier] = asyncio.get_running_loop().create_future()
        try:
            await self.write({"kind": "message", "id": identifier, "prompt": prompt})
            try:
                return await asyncio.wait_for(self.turns[identifier], timeout)
            except TimeoutError as error:
                raise WorkerWatchdogExpired("worker_abort_not_confirmed") from error
        finally:
            self.turns.pop(identifier, None)

    async def close(self) -> None:
        if self.process is not None and self.process.returncode is None:
            try:
                await self.write({"kind": "stop"})
            except (BrokenPipeError, ConnectionResetError, WorkerRuntimeFailure) as error:
                self.on_event(
                    self.agent_id,
                    {"kind": "shutdown_transport_closed", "detail": type(error).__name__},
                )
            try:
                await asyncio.wait_for(self.process.wait(), timeout=15)
            except TimeoutError:
                docker("stop", "--time", "2", self.name)
                await self.process.wait()
        for task in (self.reader, self.errors):
            if task is not None:
                task.cancel()
        tasks = [task for task in (self.reader, self.errors) if task is not None]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        for result in results:
            if isinstance(result, BaseException) and not isinstance(result, asyncio.CancelledError):
                self.on_event(
                    self.agent_id,
                    {
                        "kind": "transport_error",
                        "detail": str(result).replace(self.token, "[REDACTED]"),
                    },
                )
        self.token = ""

    async def code_idle(self) -> bool:
        if self.fatal or self.process is None or self.process.returncode is not None:
            raise WorkerRuntimeFailure("worker_not_available")
        output = await asyncio.to_thread(
            docker,
            "exec",
            self.name,
            "python",
            "-I",
            "-m",
            "institution_lab.runtime.processes",
        )
        return not json.loads(output)
