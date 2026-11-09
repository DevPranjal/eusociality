from __future__ import annotations

import json
import threading
from collections import Counter
from copy import deepcopy
from pathlib import Path

import jsonpatch

from ..recording import redact
from ..store import Store
from .activity import ActivityIndex


class ReadView:
    def __init__(self, run: Path) -> None:
        self.store = Store(run / "events.sqlite", readonly=True)
        self.run = run
        self.entries: list[dict] = []
        self.cursor = 0
        self.initial: dict | None = None
        self.projected: dict | None = None
        self.lock = threading.Lock()
        self.activity = ActivityIndex()
        self.head_cursor = 0
        self.heads: list[tuple[int, str | None]] = []

    def published_at(self, at: int | None) -> tuple[str | None, int, bool]:
        """Resolve file history without materializing conversations or graph state."""
        with self.lock, self.store.connect() as connection:
            latest = connection.execute("SELECT coalesce(max(seq),0) FROM events").fetchone()[0]
            cutoff = latest if at is None else min(max(0, at), latest)
            changes = connection.execute(
                """
                SELECT seq,json_extract(body,'$.state.head') AS head
                FROM events WHERE kind='initialized' AND seq>? AND seq<=?
                UNION ALL
                SELECT e.seq,json_extract(p.value,'$.value') AS head
                FROM events e,json_each(e.body,'$.patch') p
                WHERE e.seq>? AND e.seq<=? AND json_extract(p.value,'$.path')='/head'
                ORDER BY seq
                """,
                (self.head_cursor, latest, self.head_cursor, latest),
            ).fetchall()
            self.heads.extend((row["seq"], row["head"]) for row in changes)
            self.head_cursor = latest
            if not self.heads:
                if cutoff != latest:
                    raise LookupError("historical_project_revision_not_recorded")
                state = connection.execute(
                    "SELECT json_extract(body,'$.head') FROM state WHERE id=1"
                ).fetchone()
                if state is None:
                    raise LookupError("project_state_not_recorded")
                return state[0], cutoff, False
            head = next((head for seq, head in reversed(self.heads) if seq <= cutoff), None)
            return head, cutoff, True

    def refresh(self) -> None:
        with self.store.connect() as connection:
            target = connection.execute("SELECT coalesce(max(seq),0) FROM events").fetchone()[0]
        while self.cursor < target and (rows := self.store.events(self.cursor)):
            for row in rows:
                if row["seq"] > target:
                    return
                body = row["body"]
                self.activity.add(row)
                subtype = body.get("event", {}).get("type")
                item = {
                    "seq": row["seq"],
                    "time": row["time"],
                    "actor": row["actor"],
                    "kind": row["kind"],
                    "label": subtype or row["kind"],
                    "summary": self.summary(body),
                }
                if row["kind"] == "initialized":
                    self.initial = deepcopy(body["state"])
                    self.projected = deepcopy(self.initial)
                if "patch" in body:
                    item["patch"] = body["patch"]
                    if self.projected is not None:
                        jsonpatch.apply_patch(self.projected, body["patch"], in_place=True)
                result = body.get("result", {})
                if "rules" in result:
                    item["decision"] = {
                        "denied": result.get("ok") is False,
                        "advisory_violation": bool(result.get("advisory_violation")),
                        "checks": result["rules"],
                    }
                if row["kind"] in {
                    "message",
                    "offer",
                    "accept",
                    "refuse",
                    "cancel",
                    "review",
                    "sync",
                    "propose_task",
                    "revise_task",
                    "withdraw_task",
                    "propose_completion",
                    "review_completion",
                    "finish_goal",
                    "research",
                }:
                    item["data"] = {
                        "payload": body.get("payload", {}),
                        "result": body.get("result", {}),
                    }
                if subtype == "assistant.usage":
                    item["usage"] = body["event"]["data"]
                self.entries.append(item)
                self.cursor = row["seq"]

    @staticmethod
    def summary(body: dict) -> str:
        event = body.get("event", {})
        data = event.get("data", {})
        if event.get("type") == "assistant.message":
            return str(data.get("content", ""))[:180]
        result = body.get("result", {})
        return str(result.get("reason", body.get("detail", body.get("payload", ""))))[:180]

    def state_at(self, cutoff: int) -> tuple[dict, bool]:
        if self.initial is None:
            return self.store.state(), False
        if cutoff == self.cursor:
            assert self.projected is not None
            return deepcopy(self.projected), True
        state = None
        for item in self.entries:
            if item["seq"] > cutoff:
                break
            if item["kind"] == "initialized":
                state = deepcopy(self.initial)
            if state is not None and "patch" in item:
                jsonpatch.apply_patch(state, item["patch"], in_place=True)
        return state or {}, True

    @staticmethod
    def usage(entries: list[dict], agents: dict) -> dict:
        values = {
            actor: {
                "observed_calls": 0,
                "input_tokens": None,
                "output_tokens": None,
                "ai_credits": None,
                "credits_complete": True,
            }
            for actor in agents
        }
        seen = set()
        for entry in entries:
            data = entry.get("usage")
            if data is None:
                continue
            accounting = data.get("accounting") or {}
            identifier = (
                accounting.get("sourceSessionId"),
                accounting.get("sequence", data.get("apiCallId", entry["seq"])),
            )
            if identifier in seen:
                continue
            seen.add(identifier)
            actor = entry["actor"]
            value = values.setdefault(
                actor,
                {
                    "observed_calls": 0,
                    "input_tokens": None,
                    "output_tokens": None,
                    "ai_credits": None,
                    "credits_complete": True,
                },
            )
            value["observed_calls"] += 1
            for target, source in [
                ("input_tokens", "inputTokens"),
                ("output_tokens", "outputTokens"),
            ]:
                number = data.get(source)
                if isinstance(number, (int, float)):
                    value[target] = (value[target] or 0) + number
            nano = (data.get("copilotUsage") or {}).get("totalNanoAiu")
            if isinstance(nano, (int, float)):
                value["ai_credits"] = (value["ai_credits"] or 0) + nano / 1_000_000_000
            else:
                value["credits_complete"] = False
            if data.get("aiCreditsStatus") != "complete":
                value["credits_complete"] = False
        return values

    @staticmethod
    def graph(entries: list[dict], state: dict) -> dict:
        agents = state.get("agents", {})
        edges = Counter()
        offers = {}
        proposals = state.get("proposals", {})
        for entry in entries:
            data = entry.get("data")
            if data is None or not data["result"].get("ok"):
                continue
            actor, kind, payload = entry["actor"], entry["kind"], data["payload"]
            if kind == "message":
                edges[(actor, payload.get("to") or "public", "message")] += 1
            elif kind == "offer":
                offers[payload["ticket"]] = (actor, payload["to"])
                edges[(actor, payload["to"], "offer")] += 1
            elif kind == "accept" and payload["ticket"] in offers:
                source, recipient = offers.pop(payload["ticket"])
                edges[(source, recipient, "accepted_transfer")] += 1
            elif kind in {"refuse", "cancel"}:
                offers.pop(payload["ticket"], None)
            elif kind == "review" and payload["proposal"] in proposals:
                author = proposals[payload["proposal"]]["actor"]
                edges[(actor, author, "approval" if payload["approve"] else "rejection")] += 1
            elif kind == "sync":
                edges[("project", actor, "published_code")] += 1
        nodes = [
            {"data": {"id": actor, "label": actor + "\n" + str(worker["level"]), **worker}}
            for actor, worker in agents.items()
        ]
        endpoints = {endpoint for source, target, _ in edges for endpoint in (source, target)}
        for endpoint in sorted(endpoints - set(agents)):
            nodes.append({"data": {"id": endpoint, "label": endpoint, "role": "channel"}})
        connections = [
            {
                "data": {
                    "id": f"edge-{index}",
                    "source": source,
                    "target": target,
                    "kind": kind,
                    "count": count,
                    "label": f"{kind} · {count}",
                }
            }
            for index, ((source, target, kind), count) in enumerate(edges.items())
        ]
        return {"nodes": nodes, "edges": connections}

    def snapshot(self, at: int | None = None) -> dict:
        with self.lock:
            self.refresh()
            cutoff = self.cursor if at is None else min(max(0, at), self.cursor)
            entries = [item for item in self.entries if item["seq"] <= cutoff]
            state, exact = self.state_at(cutoff)
            timeline = [
                {key: item[key] for key in ("seq", "time", "actor", "kind", "label", "summary")}
                for item in entries[-400:]
            ]
            return {
                "run": self.run.name,
                "cursor": cutoff,
                "latest": self.cursor,
                "historical_state_exact": exact,
                "state": state,
                "usage": self.usage(entries, state.get("agents", {})),
                "graph": self.graph(entries, state),
                "timeline": timeline,
                "counts": dict(Counter(item["kind"] for item in entries)),
                "rule_summary": {
                    "enforced_denials": sum(
                        item["decision"]["denied"] for item in entries if "decision" in item
                    ),
                    "advisory_violations": sum(
                        item["decision"]["advisory_violation"]
                        for item in entries
                        if "decision" in item
                    ),
                },
            }

    def detail(self, sequence: int) -> dict | None:
        with self.store.connect() as connection:
            row = connection.execute("SELECT * FROM events WHERE seq=?", (sequence,)).fetchone()
            return {**dict(row), "body": redact(json.loads(row["body"]))} if row else None

    def agent(
        self,
        actor: str,
        at: int | None = None,
        offset: int = 0,
        limit: int = 50,
    ) -> dict:
        with self.lock:
            self.refresh()
            cutoff = self.cursor if at is None else min(max(0, at), self.cursor)
            state, exact = self.state_at(cutoff)
            profile = state.get("agents", {}).get(actor)
            if profile is None:
                raise LookupError("unknown_agent")
            entries = [
                item for item in self.entries if item["actor"] == actor and item["seq"] <= cutoff
            ]
            terminal = state.get("status") not in {
                "created",
                "ready",
                "running",
                "draining",
                "paused",
            }
            return {
                "actor": actor,
                "profile": profile,
                "cursor": cutoff,
                "historical_state_exact": exact,
                "archived": terminal,
                "observed_models": sorted(
                    {
                        item["usage"]["model"]
                        for item in entries
                        if item.get("usage", {}).get("model")
                    }
                ),
                "usage": self.usage(entries, {actor: profile})[actor],
                "sessions": [
                    item for item in self.activity.sessions[actor] if item["seq"] <= cutoff
                ],
                "workspaces": [
                    item for item in self.activity.workspaces[actor] if item["seq"] <= cutoff
                ],
                "tickets": [item for item in state["tickets"].values() if item["owner"] == actor],
                "proposals": [
                    item for item in state["proposals"].values() if item["actor"] == actor
                ],
                **self.activity.page(actor, cutoff, terminal, offset, limit),
            }

    def workspace(self, actor: str, identifier: str | None, at: int | None) -> dict:
        with self.lock:
            self.refresh()
            cutoff = self.cursor if at is None else min(at, self.cursor)
            records = [item for item in self.activity.workspaces[actor] if item["seq"] <= cutoff]
            if identifier:
                records = [item for item in records if item["id"] == identifier]
            if not records:
                raise FileNotFoundError("workspace_snapshot_not_recorded")
            return records[-1]

    def communications(self, at: int | None = None, actor: str | None = None) -> dict:
        with self.lock:
            self.refresh()
            cutoff = self.cursor if at is None else min(max(0, at), self.cursor)
            messages = [
                item
                for item in self.activity.messages
                if item["seq"] <= cutoff
                and (actor is None or actor in {item["from"], item.get("to")})
            ]
            return {"messages": messages, "cursor": cutoff}
