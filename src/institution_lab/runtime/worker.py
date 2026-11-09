"""Trusted worker-side transport. Copilot retains its native agent and coding tools."""

from __future__ import annotations

import asyncio
import dataclasses
import json
import os
import re
import shlex
import subprocess
import sys
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any

from copilot import CopilotClient
from copilot.session import (
    PermissionHandler,
    PreToolUseHookInput,
    PreToolUseHookOutput,
    SessionLimitsConfig,
)
from copilot.tools import ToolInvocation, define_tool
from pydantic import BaseModel, Field

from .processes import stop_coding_processes
from .protocol import identity

WORKSPACE = Path("/workspace")
FILE_TOOLS = frozenset({"view", "create", "edit", "glob", "grep", "rg", "apply_patch"})
SHELL_TOOLS = frozenset({"bash", "read_bash", "stop_bash"})
TOOL_SETS = {
    "worker": FILE_TOOLS | SHELL_TOOLS | {"organization"},
    "coordinator": frozenset({"organization"}),
    "reviewer": frozenset({"view", "glob", "grep", "rg", "organization"}) | SHELL_TOOLS,
    "challenger": frozenset({"organization"}),
}


def credit_limits(maximum: float | None) -> SessionLimitsConfig | None:
    return {"max_ai_credits": maximum} if maximum is not None else None


def json_default(value: object) -> object:
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return dataclasses.asdict(value)
    if isinstance(value, (datetime, Enum)):
        return value.isoformat() if isinstance(value, datetime) else value.value
    raise TypeError(type(value).__name__)


def emit(kind: str, **payload: object) -> None:
    print(json.dumps({"kind": kind, **payload}, default=json_default), flush=True)


def workspace_path(value: str) -> bool:
    try:
        resolved = (WORKSPACE / value).resolve()
    except (OSError, ValueError):
        return False
    return resolved == WORKSPACE or WORKSPACE in resolved.parents


def guard_tool(name: str, arguments: dict[str, Any] | str) -> PreToolUseHookOutput:
    if name == "organization":
        return {"permissionDecision": "allow"}
    if name == "apply_patch":
        patch = (
            arguments
            if isinstance(arguments, str)
            else arguments.get("patch", arguments.get("input", ""))
        )
        if not isinstance(patch, str):
            return {"permissionDecision": "deny"}
        paths = re.findall(
            r"^\*\*\* (?:(?:Add|Update|Delete) File|Move to): (.+)$", patch, re.MULTILINE
        )
        return {
            "permissionDecision": "allow"
            if paths and all(workspace_path(path.rstrip("\r")) for path in paths)
            else "deny"
        }
    if not isinstance(arguments, dict):
        return {"permissionDecision": "deny"}
    if name in FILE_TOOLS:
        paths = [
            str(value)
            for key, value in arguments.items()
            if key.lower() in {"path", "file_path", "filepath", "cwd", "working_directory"}
        ]
        if "paths" in arguments:
            supplied = arguments["paths"]
            if isinstance(supplied, str):
                paths.append(supplied)
            elif isinstance(supplied, list) and all(isinstance(path, str) for path in supplied):
                paths.extend(supplied)
            else:
                return {"permissionDecision": "deny"}
        patterns = [arguments.get("pattern", "")] if name == "glob" else []
        valid = all(workspace_path(path) for path in paths)
        valid = valid and all(not Path(str(pattern)).is_absolute() for pattern in patterns)
        valid = valid and all(".." not in Path(str(pattern)).parts for pattern in patterns)
        return {"permissionDecision": "allow" if valid else "deny"}
    if name == "bash":
        command = arguments.get("command")
        if not isinstance(command, str):
            return {"permissionDecision": "deny"}
        prefix = (
            "runuser -u coder -- env -i PATH=/usr/local/bin:/usr/bin:/bin "
            "HOME=/workspace/.home TMPDIR=/workspace/.tmp "
        )
        wrapper = prefix + "bash -lc "
        if command.startswith(wrapper):
            try:
                inner = shlex.split(command[len(wrapper) :])
            except ValueError:
                inner = []
            if len(inner) == 1 and command == wrapper + shlex.quote(inner[0]):
                command = inner[0]
        return {
            "permissionDecision": "allow",
            "modifiedArgs": {**arguments, "command": wrapper + shlex.quote(command)},
        }
    if name in SHELL_TOOLS:
        return {"permissionDecision": "allow"}
    return {"permissionDecision": "deny"}


class OrganizationRequest(BaseModel):
    action: str
    payload: dict[str, Any] = Field(default_factory=dict)
    request_id: str | None = None


class Bridge:
    def __init__(self) -> None:
        self.pending: dict[str, asyncio.Future[object]] = {}
        self.commands: asyncio.Queue[dict[str, Any]] = asyncio.Queue()

    async def read(self) -> None:
        while line := await asyncio.to_thread(sys.stdin.readline):
            message = json.loads(line)
            if message["kind"] == "response":
                future = self.pending.pop(message["id"], None)
                if future is not None and not future.done():
                    future.set_result(message["result"])
                else:
                    emit("late_response", id=message["id"])
            else:
                await self.commands.put(message)
        await self.commands.put({"kind": "stop"})

    async def organization(self, arguments: OrganizationRequest, invocation: ToolInvocation) -> str:
        request_id = arguments.request_id or invocation.tool_call_id
        if request_id in self.pending:
            emit("request_duplicate", id=request_id)
            return json.dumps(
                {
                    "ok": False,
                    "reason": "request_already_in_flight",
                    "request_id": request_id,
                }
            )
        future = asyncio.get_running_loop().create_future()
        self.pending[request_id] = future
        emit("request", id=request_id, action=arguments.action, payload=arguments.payload)
        try:
            result = await asyncio.wait_for(future, timeout=120)
            return json.dumps(result)
        finally:
            self.pending.pop(request_id, None)


async def main() -> None:
    os.umask(0o002)
    subprocess.run(
        ["runuser", "-u", "coder", "--", "mkdir", "-p", "/workspace/.home", "/workspace/.tmp"],
        check=True,
    )
    initial = json.loads(sys.stdin.readline())
    token = initial.pop("github_token")
    bridge = Bridge()
    reader = asyncio.create_task(bridge.read())

    async def pre_tool(data: PreToolUseHookInput, _invocation: object) -> PreToolUseHookOutput:
        decision = guard_tool(data["toolName"], data["toolArgs"])
        permission = decision.get("permissionDecision")
        if permission is None:
            raise RuntimeError("tool_guard_did_not_return_a_decision")
        emit("tool_guard", tool=data["toolName"], decision=permission)
        return decision

    tool = define_tool(
        name="organization",
        description=initial["tool_description"],
        params_type=OrganizationRequest,
        handler=bridge.organization,
    )
    try:
        async with CopilotClient(
            use_logged_in_user=False,
            working_directory=str(WORKSPACE),
            base_directory="/root/lab-runtime",
            log_level="error",
        ) as client:
            async with await client.create_session(
                github_token=token,
                model=initial["model"],
                reasoning_effort=(
                    None if initial["reasoning_effort"] == "none" else initial["reasoning_effort"]
                ),
                on_permission_request=PermissionHandler.approve_all,
                available_tools=sorted(TOOL_SETS[initial.get("role", "worker")]),
                tools=[tool],
                hooks={"on_pre_tool_use": pre_tool},
                working_directory=str(WORKSPACE),
                skip_custom_instructions=True,
                enable_config_discovery=False,
                enable_skills=False,
                enable_host_git_operations=False,
                enable_file_change_tracking=False,
                enable_session_store=False,
                enable_file_hooks=False,
                enable_on_demand_instruction_discovery=False,
                disabled_mcp_servers=["github-mcp-server", "githubiq"],
                session_limits=credit_limits(initial["max_ai_credits"]),
                large_output={
                    "enabled": True,
                    "max_size_bytes": initial["max_tool_output_bytes"],
                    "output_directory": "/workspace/.tmp/tool-output",
                },
                system_message={"mode": "append", "content": initial["system_message"]},
            ) as session:
                session.on(lambda event: emit("runtime", event=event.to_dict()))
                emit(
                    "ready",
                    session_id=session.session_id,
                    session_limits=credit_limits(initial["max_ai_credits"]),
                    **identity(),
                )
                while True:
                    command = await bridge.commands.get()
                    if command["kind"] == "stop":
                        break
                    if command["kind"] != "message":
                        raise ValueError("unsupported_control_message")
                    try:
                        response = await session.send_and_wait(
                            command["prompt"], timeout=initial["turn_timeout_seconds"]
                        )
                    except TimeoutError:
                        await session.abort()
                        stopped = stop_coding_processes()
                        emit(
                            "turn_timeout",
                            id=command["id"],
                            session_id=session.session_id,
                            terminated_coding_pids=stopped,
                        )
                        emit("turn_end", id=command["id"], outcome="timed_out")
                    else:
                        emit("turn_result", result=response.to_dict() if response else None)
                        emit("turn_end", id=command["id"], outcome="completed")
    except Exception as error:
        emit("fatal", error=type(error).__name__, detail=str(error).replace(token, "[REDACTED]"))
        raise SystemExit(1) from None
    finally:
        reader.cancel()
        for future in bridge.pending.values():
            if not future.done():
                future.cancel()


if __name__ == "__main__":
    asyncio.run(main())
