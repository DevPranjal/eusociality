from __future__ import annotations

import asyncio
import io
import json
import math
import re
import time
from collections import Counter
from contextlib import nullcontext
from enum import StrEnum
from pathlib import Path

from rich.console import Console, Group, RenderableType
from rich.live import Live
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from .observer.view import ReadView
from .runner import Runner

ACCENT = "#e7bd77"
MUTED = "#9ca6b5"
SUCCESS = "#79cfaa"
WARNING = "#edc283"
ERROR = "#ed8d92"
ACTIVE = {"created", "ready", "running", "draining", "paused"}


class OutputFormat(StrEnum):
    auto = "auto"
    live = "live"
    json = "json"


SVG_FORMAT = """<svg class="rich-terminal" viewBox="0 0 {width} {height}"
    xmlns="http://www.w3.org/2000/svg">
<style>
.{unique_id}-matrix {{
    font-family: Consolas, "Courier New", monospace;
    font-size: {char_height}px;
    line-height: {line_height}px;
    font-variant-east-asian: full-width;
}}
.{unique_id}-title {{ font-family: "Segoe UI", sans-serif; font-size: 18px; }}
{styles}
</style>
<defs>
<clipPath id="{unique_id}-clip-terminal">
<rect x="0" y="0" width="{terminal_width}" height="{terminal_height}" />
</clipPath>
{lines}
</defs>
{chrome}
<g transform="translate({terminal_x}, {terminal_y})"
    clip-path="url(#{unique_id}-clip-terminal)">
{backgrounds}
<g class="{unique_id}-matrix">{matrix}</g>
</g>
</svg>"""


def compact(value: object, limit: int = 100) -> str:
    value = re.sub(r"[\x00-\x08\x0b-\x1f\x7f-\x9f]", "", str(value))
    return " ".join(value.split())[:limit]


def amount(value: float | None) -> str:
    return f"{value:,.2f}" if value is not None else "-"


def state_text(value: str) -> Text:
    color = {
        "completed": SUCCESS,
        "complete": SUCCESS,
        "integrated": SUCCESS,
        "running": ACCENT,
        "active": ACCENT,
        "owned": ACCENT,
        "failed": ERROR,
        "unavailable": ERROR,
        "deadline": WARNING,
        "stalled": WARNING,
        "draining": WARNING,
        "paused": WARNING,
    }.get(value, MUTED)
    return Text(value.replace("_", " ").upper(), style=color)


class TerminalView:
    """A read-only presentation of the same journal used by the web observer."""

    def __init__(self, run: Path, configuration: dict | None = None) -> None:
        self.run = run
        self.view = ReadView(run)
        path = run / "resolved.json"
        self.configuration = configuration or (
            json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
        )
        self.processed = 0
        self.turns: Counter[str] = Counter()
        self.active: set[str] = set()
        self.unavailable: set[str] = set()
        self.feed: list[tuple[int, str, str]] = []

    def sample(self) -> dict:
        with self.view.store.connect() as connection:
            initialized = connection.execute("SELECT 1 FROM state WHERE id=1").fetchone()
        if initialized is None:
            return {"cursor": 0, "state": {"status": "preparing"}, "usage": {}}
        snapshot = self.view.snapshot()
        for event in self.view.entries[self.processed :]:
            actor, kind = event["actor"], event["kind"]
            if kind == "activation":
                self.turns[actor] += 1
                self.active.add(actor)
            if kind == "turn_end":
                self.active.discard(actor)
            if kind in {"worker_unavailable", "worker_failure"}:
                self.unavailable.add(actor)
                self.active.discard(actor)
            data = event.get("data") or {}
            result = data.get("result") or {}
            payload = data.get("payload") or {}
            detail = None
            if kind == "message" and result.get("ok"):
                message = result["message"]
                recipient = message.get("to") or "public"
                detail = f"{actor} -> {recipient}: {message['content']}"
            elif kind in {"offer", "accept", "review", "sync"} and result.get("ok"):
                target = payload.get("to") or payload.get("ticket") or payload.get("proposal", "")
                detail = f"{actor} / {kind}" + (f" / {compact(target, 16)}" if target else "")
            elif kind == "integrate":
                record = self.view.detail(event["seq"])
                integration = (record or {}).get("body", {}).get("result", {})
                detail = (
                    f"{actor} / integrate / {'accepted' if integration.get('ok') else 'rejected'}"
                )
            elif kind in {"worker_failure", "worker_turn_timeout", "worker_unavailable"}:
                detail = f"{actor} / {kind}"
            if detail:
                self.feed.append((event["seq"], kind, compact(detail, 180)))
        self.processed = len(self.view.entries)
        return snapshot

    def summary(self) -> dict | None:
        path = self.run / "summary.json"
        return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None

    def render(
        self,
        snapshot: dict,
        width: int,
        summary: dict | None = None,
        *,
        ascii_only: bool = False,
        height: int = 48,
    ) -> RenderableType:
        state = snapshot["state"]
        status = state["status"]
        agents = state.get("agents", {})
        tickets = [
            ticket
            for ticket in state.get("tickets", {}).values()
            if ticket["status"] != "withdrawn"
        ]
        verified = sum(ticket["status"] == "complete" for ticket in tickets)
        total = len(tickets)
        configuration = self.configuration
        first = self.view.entries[0]["time"] if self.view.entries else time.time()
        elapsed = (summary or {}).get("elapsed_seconds")
        if elapsed is None:
            last = self.view.entries[-1]["time"] if self.view.entries else first
            elapsed = max(0, (time.time() if status in ACTIVE else last) - first)
        runtime = configuration.get("runtime", {})
        subtitle = (
            f"{configuration.get('coordination', 'allocation not recorded')} allocation"
            f"  /  {configuration.get('enforcement', 'enforcement not recorded')} enforcement"
            f"  /  {len(agents) or configuration.get('population', 0)} identities"
            f"  /  {configuration.get('concurrency', '?')} concurrent turns"
        )
        compact_view = width < 125 or height < 32
        if compact_view:
            subtitle = (
                f"{configuration.get('coordination', '?')} / "
                f"{configuration.get('enforcement', '?')}  /  "
                f"{len(agents)} agents  /  {configuration.get('concurrency', '?')} turn slots"
            )
        header = Text.assemble(
            ("EUSOCIALITY", f"bold {ACCENT}"),
            ("   /   ", MUTED),
            (self.run.name, "bold"),
            "\n",
            (subtitle, MUTED),
            "\n",
            (str(runtime.get("model", "model not recorded")), ""),
            (f"  /  {runtime.get('reasoning_effort', '?')} reasoning", MUTED),
        )
        stats = Table.grid(expand=True, padding=(0, 2))
        for _ in range(4):
            stats.add_column(ratio=1)
        credits = [item["ai_credits"] for item in snapshot["usage"].values()]
        reported = [value for value in credits if value is not None]
        partial = any(
            value.get("ai_credits") is None or not value.get("credits_complete", True)
            for value in snapshot["usage"].values()
        )
        stats.add_row(
            state_text(status),
            Text(f"{verified}/{total} VERIFIED", style=f"bold {SUCCESS if verified else MUTED}"),
            Text(
                f"{amount(sum(reported) if reported else None)} AI credits"
                + (" (partial)" if partial else ""),
                style=MUTED,
            ),
            Text(f"{int(elapsed) // 60:02d}:{int(elapsed) % 60:02d} elapsed", style=MUTED),
        )
        bar_width = max(12, min(48, width // 3))
        completed = int(bar_width * verified / total) if total else 0
        progress = Text.assemble(
            (("=" if ascii_only else "━") * completed, SUCCESS),
            (("-" if ascii_only else "─") * (bar_width - completed), MUTED),
            (f"  {verified}/{total} objectively verified tickets", MUTED),
        )
        row_limit = max(2, min(20, height - (20 if compact_view else 22)))
        roster = self.roster(snapshot, width, row_limit)
        board = Table(box=None, expand=True, padding=(0, 1))
        board.add_column("TICKET", style="bold", no_wrap=True)
        board.add_column("OWNER", style=MUTED)
        board.add_column("STATE")
        visible_tickets = sorted(
            tickets,
            key=lambda ticket: (ticket["status"] != "owned", ticket["status"] == "complete"),
        )[:6]
        for ticket in visible_tickets:
            board.add_row(
                Text(compact(ticket["id"], 24)),
                Text(ticket["owner"] or "unclaimed"),
                state_text(ticket["status"]),
            )
        if len(tickets) > len(visible_tickets):
            board.caption = f"Showing {len(visible_tickets)} of {len(tickets)} tickets"
        counts = Counter(ticket["status"] for ticket in tickets)
        work = Group(
            Text("  /  ".join(f"{count} {key}" for key, count in counts.items()), style=MUTED),
            Text(""),
            board,
        )
        panels = [
            Panel(roster, title="AGENTS / RECORDED ACTIVITY", border_style=MUTED, padding=(1, 1)),
            Panel(work, title="WORK BOARD", border_style=MUTED, padding=(1, 1)),
        ]
        middle: RenderableType = Group(
            Panel(roster, title="AGENTS / RECORDED ACTIVITY", border_style=MUTED, padding=0),
            Panel(
                Text("  /  ".join(f"{count} {key}" for key, count in counts.items()), MUTED),
                title="WORK BOARD / COUNTS",
                border_style=MUTED,
            ),
        )
        if not compact_view:
            columns = Table.grid(expand=True, padding=(0, 1))
            columns.add_column(ratio=2)
            columns.add_column(ratio=1)
            columns.add_row(*panels)
            middle = columns
        feed = Table.grid(expand=True, padding=(0, 1))
        feed.add_column(style=MUTED, width=7, no_wrap=True)
        feed.add_column(ratio=1)
        for sequence, kind, detail in self.feed[-(3 if compact_view else 5) :]:
            color = ERROR if kind in {"worker_failure", "worker_unavailable"} else ""
            feed.add_row(
                f"#{sequence}", Text(detail, style=color, overflow="ellipsis", no_wrap=True)
            )
        if not self.feed:
            feed.add_row(
                "", Text("Waiting for recorded communication or institutional actions.", MUTED)
            )
        evaluation = (summary or {}).get("final_evaluation")
        if evaluation is None:
            result = Text("Final evaluation: not recorded yet", style=MUTED)
        else:
            passed = evaluation.get("passed") is True
            result = Text(
                f"Final evaluation: {'PASS' if passed else 'FAIL'}",
                style=f"bold {SUCCESS if passed else ERROR}",
            )
        denials = snapshot.get("rule_summary", {}).get("enforced_denials", 0)
        footer = Text.assemble(
            result,
            (f"    /    {denials} institutional denials", MUTED),
            (f"    /    event #{snapshot['cursor']}", MUTED),
            "\n",
            (
                "Read-only. Configured levels, not emergent leadership."
                if compact_view
                else (
                    "Read-only display. Levels are configured institutions, "
                    "not inferred leadership."
                ),
                MUTED,
            ),
        )
        if snapshot.get("controller_error"):
            footer.append(f"\nController error: {snapshot['controller_error']}", style=ERROR)
        return Group(
            Panel(
                Group(header, stats, progress)
                if compact_view
                else Group(header, Text(""), stats, progress),
                border_style=ACCENT,
                padding=(0, 1) if compact_view else (1, 2),
            ),
            middle,
            Panel(feed, title="COMMUNICATION & INSTITUTIONAL FLOW", border_style=MUTED),
            footer,
        )

    def roster(self, snapshot: dict, width: int, row_limit: int) -> Table:
        table = Table(box=None, expand=True, padding=(0, 1))
        for column in ["AGENT", "ROLE / LEVEL", "TURN", "WORK", "DONE", "CREDITS"]:
            table.add_column(column, no_wrap=True)
        table.add_column("LATEST OBSERVATION", max_width=max(16, min(42, width // 4)))
        agents = snapshot["state"].get("agents", {})
        tickets = snapshot["state"].get("tickets", {}).values()
        for actor, profile in list(agents.items())[:row_limit]:
            ended = snapshot["state"]["status"] not in ACTIVE
            phase = (
                "unavailable"
                if actor in self.unavailable
                else (
                    "ended"
                    if ended
                    else "active"
                    if actor in self.active
                    else "idle"
                    if self.view.activity.sessions[actor]
                    else "starting"
                )
            )
            records = self.view.activity.items[actor]
            latest = records[-1] if records else None
            observation = "No recorded message/tool activity"
            if latest and latest["kind"] == "tool":
                tool_status = latest["status"]
                if ended and tool_status == "running":
                    tool_status = "completion unrecorded"
                observation = f"{latest['name']} / {tool_status}"
            elif latest and latest["kind"] == "message":
                observation = compact(latest["content"], 100)
            elif latest:
                observation = latest["kind"]
            owned = [
                ticket["id"]
                for ticket in tickets
                if ticket["owner"] == actor and ticket["status"] != "complete"
            ]
            table.add_row(
                Text(actor, style="bold"),
                Text(f"{profile['role']} / {profile['level']}", style=MUTED),
                Text.assemble(state_text(phase), (f" #{self.turns[actor]}", MUTED)),
                Text(compact(", ".join(owned) or "-", 28)),
                Text(str(profile["verified"]), style=SUCCESS),
                Text(amount(snapshot["usage"].get(actor, {}).get("ai_credits")), style=MUTED),
                Text(observation, overflow="ellipsis", no_wrap=True),
            )
        if len(agents) > row_limit:
            table.caption = (
                f"Showing {row_limit}/{len(agents)} identities; enlarge the terminal for more."
            )
        return table


class FrameCapture:
    def __init__(self, directory: Path, run: Path, width: int, source: str) -> None:
        directory.mkdir(parents=True, exist_ok=False)
        self.directory = directory
        self.run = run
        self.width = width
        self.source = source
        self.saved: set[str] = set()
        self.frames: list[dict] = []

    def record(self, frame: RenderableType, snapshot: dict, final: bool = False) -> None:
        tickets = list(snapshot["state"].get("tickets", {}).values())
        verified = sum(ticket["status"] == "complete" for ticket in tickets)
        status = snapshot["state"]["status"]
        triggers = {
            "started": snapshot["cursor"] == 0,
            "active": status == "running",
            "communication": snapshot.get("counts", {}).get("message", 0) > 0,
            "progress": verified > 0,
            "halfway": bool(tickets) and verified >= math.ceil(len(tickets) / 2),
            "final": final,
        }
        reasons = [key for key, enabled in triggers.items() if enabled and key not in self.saved]
        if not reasons:
            return
        self.saved.update(reasons)
        filename = f"{len(self.frames) + 1:02d}-{'-'.join(reasons)}.svg"
        console = Console(
            file=io.StringIO(),
            record=True,
            width=self.width,
            force_terminal=True,
            color_system="truecolor",
        )
        console.print(frame)
        console.save_svg(
            str(self.directory / filename),
            title="eusociality",
            code_format=SVG_FORMAT,
        )
        self.frames.append(
            {
                "file": filename,
                "event_cursor": snapshot["cursor"],
                "status": status,
                "verified": verified,
                "total": len(tickets),
                "reasons": reasons,
                "captured_at": time.time(),
                "controller_error": snapshot.get("controller_error"),
            }
        )
        (self.directory / "frames.json").write_text(
            json.dumps(
                {
                    "source_run": str(self.run.resolve()),
                    "source": self.source,
                    "journal": "events.sqlite",
                    "width": self.width,
                    "frames": self.frames,
                },
                indent=2,
            ),
            encoding="utf-8",
        )


async def execute(
    controller: Runner,
    *,
    mode: OutputFormat = OutputFormat.auto,
    capture: Path | None = None,
    timeline: bool = False,
) -> tuple[dict, bool]:
    if timeline:
        if capture is not None:
            raise ValueError("timeline_mode_retains_text_log_not_dashboard_frame_captures")
        from .timeline import execute as execute_timeline

        return await execute_timeline(controller)
    console = Console(force_terminal=True if mode == OutputFormat.live else None)
    visible = mode == OutputFormat.live or (mode == OutputFormat.auto and console.is_terminal)
    if not visible and capture is None:
        return await controller.run(), False
    view = TerminalView(controller.output, controller.config.model_dump(mode="json"))
    captures = FrameCapture(capture, controller.output, console.width, "live") if capture else None
    company = asyncio.create_task(controller.run())

    async def display() -> None:
        context = (
            Live(console=console, auto_refresh=False, vertical_overflow="visible")
            if visible
            else nullcontext()
        )
        with context as screen:
            while not company.done():
                snapshot = await asyncio.to_thread(view.sample)
                frame = view.render(
                    snapshot,
                    console.width,
                    ascii_only=console.options.ascii_only,
                    height=console.height,
                )
                if screen is not None:
                    screen.update(frame, refresh=True)
                if captures:
                    await asyncio.to_thread(captures.record, frame, snapshot)
                await asyncio.wait({company}, timeout=0.75)
            if company.cancelled():
                return
            snapshot = await asyncio.to_thread(view.sample)
            error = company.exception()
            if error is not None:
                snapshot["controller_error"] = f"{type(error).__name__}: {error}"
            frame = view.render(
                snapshot,
                console.width,
                None if error else company.result(),
                ascii_only=console.options.ascii_only,
                height=console.height,
            )
            if screen is not None:
                screen.update(frame, refresh=True)
            if captures:
                await asyncio.to_thread(captures.record, frame, snapshot, True)

    results = await asyncio.gather(company, display(), return_exceptions=True)
    errors = [result for result in results if isinstance(result, BaseException)]
    if len(errors) > 1:
        raise BaseExceptionGroup("company_and_terminal_errors", errors)
    if errors:
        raise errors[0]
    summary = results[0]
    assert isinstance(summary, dict)
    return summary, visible


async def watch(run: Path, *, once: bool = False, capture: Path | None = None) -> None:
    view = TerminalView(run)
    console = Console()
    captures = FrameCapture(capture, run, console.width, "recorded-watch") if capture else None
    with Live(console=console, auto_refresh=False, vertical_overflow="visible") as screen:
        while True:
            snapshot = await asyncio.to_thread(view.sample)
            summary = await asyncio.to_thread(view.summary)
            frame = view.render(
                snapshot,
                console.width,
                summary,
                ascii_only=console.options.ascii_only,
                height=console.height,
            )
            screen.update(frame, refresh=True)
            finished = once or snapshot["state"]["status"] not in ACTIVE | {"preparing"}
            if captures:
                await asyncio.to_thread(captures.record, frame, snapshot, finished)
            if finished:
                return
            await asyncio.sleep(0.75)
