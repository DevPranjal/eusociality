from __future__ import annotations

import json
from collections import defaultdict
from copy import deepcopy

from ..recording import redact

DISPLAY_LIMIT = 100_000


class ActivityIndex:
    """Readable projections of recorded events, never generated agent commentary."""

    def __init__(self) -> None:
        self.items: dict[str, list[dict]] = defaultdict(list)
        self.calls: dict[tuple[str, str], dict] = {}
        self.sessions: dict[str, list[dict]] = defaultdict(list)
        self.models: dict[str, set[str]] = defaultdict(set)
        self.workspaces: dict[str, list[dict]] = defaultdict(list)
        self.messages: list[dict] = []

    def add(self, row: dict) -> None:
        actor = row["actor"]
        if actor is None:
            return
        body = row["body"]
        event = body.get("event") or {}
        data = event.get("data") or {}
        common = {"seq": row["seq"], "time": row["time"], "actor": actor}
        subtype = event.get("type")
        if row["kind"] == "ready":
            self.sessions[actor].append({**common, **body})
        if row["kind"] == "workspace_snapshot":
            self.workspaces[actor].append({**common, **body})
        if subtype == "assistant.usage" and data.get("model"):
            self.models[actor].add(data["model"])
        if subtype in {"assistant.message", "user.message"} and data.get("content", "").strip():
            content = data["content"]
            self.items[actor].append(
                {
                    **common,
                    "kind": "message",
                    "sender": "assistant" if subtype == "assistant.message" else "system",
                    "content": content[:DISPLAY_LIMIT],
                    "truncated": len(content) > DISPLAY_LIMIT,
                }
            )
        if subtype == "tool.execution_start":
            call_id = data["toolCallId"]
            arguments = data.get("arguments") or {}
            item = {
                **common,
                "kind": "tool",
                "call_id": call_id,
                "name": data.get("toolName", "Unlabelled tool"),
                "arguments": arguments,
                "status": "running",
                "output": None,
                "end_seq": None,
            }
            self.calls[(actor, call_id)] = item
            self.items[actor].append(item)
        if subtype == "tool.execution_complete":
            call_id = data["toolCallId"]
            item = self.calls.get((actor, call_id))
            if item is None:
                item = {
                    **common,
                    "kind": "tool",
                    "call_id": call_id,
                    "name": data.get("toolName", "Unlabelled tool"),
                    "arguments": {},
                    "start_unrecorded": True,
                }
                self.calls[(actor, call_id)] = item
                self.items[actor].append(item)
            result = data.get("result") or {}
            output = result.get("content", result.get("detailedContent"))
            if output is None and result:
                output = json.dumps(redact(result), indent=2)
            error = data.get("error") or {}
            item.update(
                end_seq=row["seq"],
                end_time=row["time"],
                status="complete" if data.get("success") else "error",
                output=output[:DISPLAY_LIMIT] if isinstance(output, str) else output,
                truncated=isinstance(output, str) and len(output) > DISPLAY_LIMIT,
                error=error.get("message"),
                duration_seconds=max(0, row["time"] - item["time"]),
            )
        if row["kind"] == "message" and body.get("result", {}).get("ok"):
            message = body["result"]["message"]
            self.messages.append({**common, **message})
        if row["kind"] == "integrate" and body.get("result", {}).get("effects"):
            self.items[actor].append(
                {
                    **common,
                    "kind": "milestone",
                    "effects": body["result"]["effects"],
                    "passed": body["result"].get("passed"),
                }
            )
        if row["kind"] in {"worker_failure", "worker_unavailable", "worker_turn_timeout"}:
            self.items[actor].append(
                {
                    **common,
                    "kind": "system",
                    "event": row["kind"],
                    "detail": redact(body),
                }
            )

    def page(self, actor: str, cutoff: int, terminal: bool, offset: int, limit: int) -> dict:
        records = [item for item in self.items[actor] if item["seq"] <= cutoff]
        end = max(0, len(records) - offset)
        selected = records[max(0, end - limit) : end]
        items = deepcopy(selected)
        for item in items:
            if item["kind"] == "tool" and (item.get("end_seq") is None or item["end_seq"] > cutoff):
                item.update(status="unrecorded" if terminal else "running", output=None)
                item.pop("error", None)
                item.pop("duration_seconds", None)
                item.pop("end_time", None)
                item.pop("end_seq", None)
        return {"items": items, "total": len(records), "has_more": offset + limit < len(records)}
