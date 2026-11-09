from __future__ import annotations

import asyncio
import json
import re
from pathlib import Path

from rich.console import Console
from rich.text import Text

from .store import Store
from .terminal import ACCENT, ERROR, MUTED, SUCCESS, WARNING, compact


class Timeline:
    def __init__(self, run: Path) -> None:
        self.store = Store(run / "events.sqlite", readonly=True)
        self.cursor = 0
        self.first_time: float | None = None
        self.proposals: dict[str, str] = {}

    def description(self, row: dict) -> tuple[str, str] | None:
        kind, body = row["kind"], row["body"]
        result = body.get("result") or {}
        payload = body.get("payload") or {}
        if kind == "initialized":
            return f"Society ready / {len(body['state']['agents'])} agents", ACCENT
        if kind == "ready":
            return "Session connected", MUTED
        if kind == "activation":
            return f"Turn {body['number']} started", MUTED
        if kind in {
            "propose_task",
            "revise_task",
            "withdraw_task",
            "propose_completion",
            "review_completion",
            "finish_goal",
            "research",
        }:
            if not result.get("ok"):
                return f"{kind} denied / {result.get('reason', 'rule decision')}", WARNING
            if kind == "research":
                source = result["source"]
                return f"Read {source['url']} / retrieved {source['retrieved_at']}", MUTED
            if kind == "propose_completion":
                return "Proposed goal completion; awaiting independent assessment", ACCENT
            if kind == "review_completion":
                return f"{'Approved' if payload['approve'] else 'Rejected'} goal completion", ACCENT
            if kind == "finish_goal":
                return "Goal accepted / recorded agent judgement and checks", SUCCESS
            return f"{kind.replace('_', ' ').capitalize()} / {result['ticket']['id']}", ACCENT
        if kind == "message" and result.get("ok"):
            message = result["message"]
            return f"to {message.get('to') or 'everyone'}: {message['content']}", ""
        if kind == "submit" and result.get("ok"):
            proposal = result["proposal"]
            self.proposals[proposal["id"]] = proposal["ticket"]
            passed = proposal["evaluation"]["passed"]
            return (
                f"Submitted {proposal['ticket']} / checks {'passed' if passed else 'failed'}",
                SUCCESS if passed else WARNING,
            )
        if kind == "integrate":
            identifier = payload.get("proposal", "")
            name = self.proposals.get(identifier, identifier[:8])
            if not result.get("ok"):
                return f"Integration denied / {result.get('reason', 'rule decision')}", WARNING
            passed = result.get("passed") is True
            effects = result.get("effects", [])
            changes = ", ".join(f"{item['field']}={item['value']}" for item in effects)
            return (
                f"{'Verified' if passed else 'Integrated but checks failed'} {name}"
                + (f" / {changes}" if changes else ""),
                SUCCESS if passed else WARNING,
            )
        if kind in {"claim", "offer", "accept", "refuse", "cancel", "review", "sync"}:
            if result.get("ok") is False:
                return f"{kind} denied / {result.get('reason', 'rule decision')}", WARNING
            if not result.get("ok"):
                return None
            if kind == "review":
                name = self.proposals.get(payload["proposal"], payload["proposal"][:8])
                return f"{'Approved' if payload['approve'] else 'Rejected'} proposal for {name}", ""
            if kind == "sync":
                return "Synchronized published code", MUTED
            name = payload.get("ticket", "")
            target = f" to {payload['to']}" if payload.get("to") else ""
            return f"{kind.capitalize()} {name}{target}", ACCENT
        if kind in {
            "worker_failure",
            "transport_error",
            "fatal",
            "worker_unavailable",
            "workspace_capture_error",
            "cleanup_error",
        }:
            detail = body.get("detail") or body.get("error") or ""
            return f"{kind.replace('_', ' ')} / {detail}", ERROR
        if kind in {"worker_turn_timeout", "recovery"}:
            return kind.replace("_", " "), WARNING
        if kind == "run_end":
            return f"Society {body['status']}", ACCENT
        if kind == "invalid_action":
            return f"{body['action']} denied / {result.get('reason')}", WARNING
        event = body.get("event") or {}
        data = event.get("data") or {}
        if event.get("type") == "assistant.message" and data.get("content", "").strip():
            return data["content"], ""
        if event.get("type") == "tool.execution_start" and data.get("toolName") != "organization":
            args = data.get("arguments") or {}
            detail = (
                args.get("description") or args.get("path") or args.get("command") or ""
                if isinstance(args, dict)
                else args
                if isinstance(args, str)
                else json.dumps(args)
            )
            if data.get("toolName") == "apply_patch" and isinstance(detail, str):
                paths = re.findall(r"^\*\*\* (?:Add|Update|Delete) File: (.+)$", detail, re.M)
                detail = ", ".join(paths) if paths else detail.splitlines()[0] if detail else ""
            return f"{data.get('toolName', 'tool')} / {detail}", MUTED
        return None

    def poll(self) -> list[Text]:
        lines = []
        for row in self.store.events(self.cursor, limit=500):
            self.cursor = row["seq"]
            if self.first_time is None:
                self.first_time = row["time"]
            description = self.description(row)
            if description is None:
                continue
            message, style = description
            seconds = int(max(0, row["time"] - self.first_time))
            prefix = f"{seconds // 60:02}:{seconds % 60:02}  {(row['actor'] or 'system'):<12}"
            rendered = compact(message, 10000)
            if len(rendered) > 240:
                rendered = rendered[:237] + "..."
            lines.append(
                Text.assemble(
                    (prefix, MUTED),
                    ("  ", ""),
                    (rendered, style),
                    (f"  #{row['seq']}", MUTED),
                )
            )
        return lines


async def execute(controller) -> tuple[dict, bool]:
    console = Console()
    view = Timeline(controller.output)
    log = controller.output / "timeline.txt"
    company = asyncio.create_task(controller.run())

    async def display() -> None:
        while True:
            lines = await asyncio.to_thread(view.poll)
            for line in lines:
                console.print(line)
            if lines:
                with log.open("a", encoding="utf-8") as stream:
                    stream.write("\n".join(line.plain for line in lines) + "\n")
            if company.done():
                with view.store.connect() as connection:
                    latest = connection.execute(
                        "SELECT coalesce(max(seq),0) FROM events"
                    ).fetchone()[0]
                if view.cursor >= latest:
                    return
            await asyncio.wait({company}, timeout=0.5)

    results = await asyncio.gather(company, display(), return_exceptions=True)
    errors = [item for item in results if isinstance(item, BaseException)]
    if len(errors) > 1:
        raise BaseExceptionGroup("company_and_timeline_errors", errors)
    if errors:
        raise errors[0]
    summary = results[0]
    assert isinstance(summary, dict)
    return summary, True


async def watch(run: Path) -> None:
    console = Console()
    view = Timeline(run)
    while True:
        for line in await asyncio.to_thread(view.poll):
            console.print(line)
        with view.store.connect() as connection:
            row = connection.execute("SELECT body FROM state WHERE id=1").fetchone()
            latest = connection.execute("SELECT coalesce(max(seq),0) FROM events").fetchone()[0]
        if row:
            status = json.loads(row[0])["status"]
            if status not in {"ready", "running", "draining", "paused"} and view.cursor >= latest:
                return
        await asyncio.sleep(0.5)
