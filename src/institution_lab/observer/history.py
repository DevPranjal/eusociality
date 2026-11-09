from __future__ import annotations

import json

from ..recording import redact
from ..timeline import Timeline
from .view import ReadView

ACTIVITY_KINDS = (
    "initialized",
    "ready",
    "activation",
    "propose_task",
    "revise_task",
    "withdraw_task",
    "claim",
    "offer",
    "accept",
    "refuse",
    "cancel",
    "submit",
    "reserve_integration",
    "integrate",
    "review",
    "sync",
    "message",
    "propose_completion",
    "review_completion",
    "finish_goal",
    "research",
    "worker_failure",
    "transport_error",
    "fatal",
    "worker_unavailable",
    "worker_turn_timeout",
    "recovery",
    "workspace_snapshot",
    "workspace_capture_error",
    "cleanup_error",
    "invalid_action",
    "final_evaluation",
    "evaluation_startup_retry",
    "run_end",
)


def event_page(
    view: ReadView,
    *,
    offset: int = 0,
    limit: int = 100,
    actor: str | None = None,
    all_events: bool = False,
    around: int | None = None,
) -> dict:
    clauses = []
    parameters: list = []
    if not all_events:
        placeholders = ",".join("?" for _ in ACTIVITY_KINDS)
        clauses.append(
            f"(kind IN ({placeholders}) OR "
            "(kind='runtime' AND (json_extract(body,'$.event.type')='assistant.message' OR "
            "(json_extract(body,'$.event.type')='tool.execution_start' "
            "AND json_extract(body,'$.event.data.toolName')!='organization'))))"
        )
        parameters.extend(ACTIVITY_KINDS)
    if actor:
        clauses.append("actor=?")
        parameters.append(actor)
    where = " AND ".join(clauses) or "1"
    with view.store.connect() as connection:
        connection.execute("BEGIN")
        latest, started = connection.execute(
            "SELECT coalesce(max(seq),0),min(time) FROM events"
        ).fetchone()
        total = connection.execute(
            f"SELECT count(*) FROM events WHERE {where}",
            parameters,
        ).fetchone()[0]
        if around is not None:
            before = connection.execute(
                f"SELECT count(*) FROM events WHERE {where} AND seq<?",
                [*parameters, around],
            ).fetchone()[0]
            offset = (before // limit) * limit
        offset = min(offset, max(0, ((total - 1) // limit) * limit))
        rows = connection.execute(
            f"SELECT * FROM events WHERE {where} ORDER BY seq LIMIT ? OFFSET ?",
            [*parameters, limit, offset],
        ).fetchall()
        actors = [
            row[0]
            for row in connection.execute(
                "SELECT DISTINCT actor FROM events WHERE actor IS NOT NULL ORDER BY actor",
            )
        ]
        metadata = connection.execute(
            "SELECT json_extract(body,'$.goal'),json_extract(body,'$.status') "
            "FROM state WHERE id=1",
        ).fetchone()
        presenter = Timeline(view.run)
        presenter.proposals = {
            row[0]: row[1]
            for row in connection.execute(
                "SELECT json_extract(body,'$.result.proposal.id'),"
                "json_extract(body,'$.result.proposal.ticket') "
                "FROM events WHERE kind='submit' AND json_extract(body,'$.result.ok')=1",
            )
        }
    items = []
    for row in rows:
        record = {**dict(row), "body": json.loads(row["body"])}
        description = presenter.description(record)
        body = record["body"]
        subtype = (body.get("event") or {}).get("type")
        data = (body.get("event") or {}).get("data") or {}
        result = body.get("result") or {}
        label = subtype or row["kind"]
        title = description[0] if description else label.replace(".", " / ").replace("_", " ")
        if row["kind"] == "workspace_snapshot":
            title = f"Workspace checkpoint / {body.get('label', 'Recorded files')} / " + (
                f"{body['file_count']} files" if "file_count" in body else "count not recorded"
            )
        elif row["kind"] == "final_evaluation":
            title = "Final checks " + ("passed" if body.get("passed") else "failed")
        elif row["kind"] == "reserve_integration":
            title = "Preparing publication" + (
                f" / {result['proposal']['ticket']}" if result.get("proposal") else ""
            )
        tone = (
            "warning"
            if result.get("ok") is False
            else "error"
            if row["kind"]
            in {
                "worker_failure",
                "fatal",
                "transport_error",
                "cleanup_error",
            }
            or data.get("success") is False
            else "success"
            if row["kind"] in {"integrate", "finish_goal"}
            and result.get("ok")
            and result.get("passed", True)
            else "neutral"
        )
        items.append(
            {
                "seq": row["seq"],
                "time": row["time"],
                "actor": row["actor"] or "system",
                "kind": row["kind"],
                "label": label,
                "title": redact(" ".join(title.split())[:400]),
                "tone": tone,
            }
        )
    return {
        "items": items,
        "latest": latest,
        "total": total,
        "offset": offset,
        "limit": limit,
        "has_more": offset + len(items) < total,
        "started_at": started,
        "actors": actors,
        "goal": metadata[0] if metadata else None,
        "status": metadata[1] if metadata else "setup_failed",
    }
