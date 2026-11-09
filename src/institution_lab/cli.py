from __future__ import annotations

import asyncio
import io
import json
import sys
from pathlib import Path
from typing import Annotated

import typer

from .config import load_config
from .runner import Runner
from .store import Store
from .terminal import OutputFormat

FormatOption = Annotated[
    OutputFormat,
    typer.Option("--format", help="Auto: live in a terminal, JSON when redirected."),
]
CaptureOption = Annotated[
    Path | None,
    typer.Option(
        "--capture", help="Save actual terminal frames and their event cursors to a new directory."
    ),
]

app = typer.Typer(no_args_is_help=True, pretty_exceptions_show_locals=False)


@app.command()
def setup(
    project: Path | None = None,
    base: Path | None = None,
    save_only: bool = False,
) -> None:
    """Create a society through a guided terminal setup."""
    from .guided import setup as guided_setup

    try:
        guided_setup(project, base, save_only)
    except (ValueError, OSError, RuntimeError) as error:
        raise typer.BadParameter(str(error)) from error


@app.command()
def timeline(run: Path) -> None:
    """Follow meaningful society events without raw runtime JSON."""
    from .timeline import watch as watch_timeline

    asyncio.run(watch_timeline(run))


def interactive_main() -> None:
    for stream in (sys.stdin, sys.stdout, sys.stderr):
        if isinstance(stream, io.TextIOWrapper):
            stream.reconfigure(encoding="utf-8")
    app(args=["setup"] if len(sys.argv) == 1 else None, prog_name="eusoc")


@app.command()
def validate(config: Path) -> None:
    from .admission import validate_static

    resolved = load_config(config)
    typer.echo(json.dumps(validate_static(resolved), indent=2))


@app.command()
def run(
    config: Path,
    out: Path,
    paused: bool = False,
    format: FormatOption = OutputFormat.auto,
    capture: CaptureOption = None,
    timeline: bool = False,
) -> None:
    """Run a society from a saved configuration."""
    from .terminal import execute

    if timeline and format == OutputFormat.json:
        raise typer.BadParameter("Choose --timeline or --format json, not both.")
    if timeline and capture is not None:
        raise typer.BadParameter("Timeline retains timeline.txt; --capture is for the dashboard.")
    controller = Runner(load_config(config), out)
    if paused:
        controller.store.control("pause")
    summary, displayed = asyncio.run(
        execute(controller, mode=format, capture=capture, timeline=timeline)
    )
    if not displayed:
        typer.echo(json.dumps(summary, indent=2))


@app.command()
def status(run: Path, format: FormatOption = OutputFormat.auto) -> None:
    """Inspect a society's current state and progress."""
    from rich.console import Console

    from .terminal import TerminalView

    console = Console(force_terminal=True if format == OutputFormat.live else None)
    if format == OutputFormat.json or (format == OutputFormat.auto and not console.is_terminal):
        typer.echo(json.dumps(Store(run / "events.sqlite", readonly=True).state(), indent=2))
        return
    view = TerminalView(run)
    console.print(
        view.render(
            view.sample(),
            console.width,
            view.summary(),
            ascii_only=console.options.ascii_only,
            height=console.height,
        )
    )


@app.command()
def watch(run: Path, once: bool = False, capture: CaptureOption = None) -> None:
    """Watch an existing company's real journal without starting or controlling agents."""
    from .terminal import watch as watch_terminal

    asyncio.run(watch_terminal(run, once=once, capture=capture))


@app.command()
def control(run: Path, action: str, target: str | None = None) -> None:
    """Pause, resume or stop an active society, or mark an agent unavailable."""
    if action not in {"pause", "resume", "stop", "unavailable"}:
        raise typer.BadParameter("unsupported_control")
    if not (run / "events.sqlite").is_file():
        raise typer.BadParameter("unknown_run")
    current = Store(run / "events.sqlite", readonly=True).state()
    if current["status"] not in {"ready", "running", "draining", "paused"}:
        raise typer.BadParameter("run_not_active")
    if action == "unavailable" and not target:
        raise typer.BadParameter("unavailable_requires_target")
    if (
        action == "unavailable"
        and target not in Store(run / "events.sqlite", readonly=True).state()["agents"]
    ):
        raise typer.BadParameter("unknown_worker")
    Store(run / "events.sqlite").control(action, target)
    typer.echo("queued")


@app.command()
def observe(run: Path, port: int = 8765) -> None:
    """Open the local visual board for a society's recorded evidence."""
    import uvicorn

    from .observer.server import create_app

    project = Path(__file__).parents[2]
    runs = (project / "runs").resolve()
    root = runs if run.resolve().is_relative_to(runs) else run.resolve()
    uvicorn.run(
        create_app(run, runs_root=root, config_root=project / "configs"),
        host="127.0.0.1",
        port=port,
    )


@app.command()
def replay(run: Path, port: int = 8765) -> None:
    """Observe archived events without creating any agent or model session."""
    observe(run, port)


@app.command()
def rerun(
    recorded: Path,
    out: Path,
    format: FormatOption = OutputFormat.auto,
    capture: CaptureOption = None,
) -> None:
    import hashlib

    from .config import RunConfig
    from .institution.rules import RuleEngine

    metadata = json.loads((recorded / "resolved.json").read_text(encoding="utf-8"))
    package = Path(__file__).parent
    current = {
        path.relative_to(package).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in package.rglob("*.py")
    }
    if current != metadata.get("source_hashes"):
        raise typer.BadParameter("recorded_harness_source_differs_use_its_source_snapshot")
    document = {key: value for key, value in metadata.items() if key in RunConfig.model_fields}
    config = RunConfig.model_validate(document)
    if RuleEngine(config).sources != metadata.get("plugin_sources"):
        raise typer.BadParameter("recorded_plugin_sources_differ")
    runtime = config.runtime.model_copy(
        update={
            "worker_image": metadata["images"]["worker"],
            "proxy_image": metadata["images"]["proxy"],
        }
    )
    config = config.model_copy(
        update={
            "workload": (recorded / "frozen-workload" / "manifest.yaml").resolve()
            if config.workload
            else None,
            "prompt_repository": (recorded / "prompt-source").resolve(),
            "runtime": runtime,
        }
    )
    controller = Runner(config, out)
    (out / "rerun.json").write_text(
        json.dumps(
            {
                "parent_run": str(recorded.resolve()),
                "note": "New stochastic run from frozen inputs, not recorded-response replay.",
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    from .terminal import execute

    summary, displayed = asyncio.run(execute(controller, mode=format, capture=capture))
    if not displayed:
        typer.echo(json.dumps(summary, indent=2))


@app.command()
def report(run: Path, out: Path | None = None) -> None:
    from .observer.view import ReadView

    view = ReadView(run).snapshot()
    state = view["state"]
    result = {
        "run": view["run"],
        "status": state.get("status"),
        "goal": state.get("goal"),
        "goal_status": state.get("goal_status"),
        "accepted_completion": state.get("accepted_completion"),
        "goal_assessments": state.get("goal_proposals", {}),
        "historical_state_exact": view["historical_state_exact"],
        "verified_tickets": sum(
            task["status"] == "complete" for task in state.get("tickets", {}).values()
        ),
        "total_tickets": sum(
            task["status"] != "withdrawn" for task in state.get("tickets", {}).values()
        ),
        "agents": state.get("agents"),
        "usage": view["usage"],
        "rules": view["rule_summary"],
        "event_counts": view["counts"],
    }
    text = json.dumps(result, indent=2)
    if out:
        out.parent.mkdir(parents=True, exist_ok=True)
        with out.open("x", encoding="utf-8") as stream:
            stream.write(text)
    typer.echo(text)


@app.command("check-controls")
def check_controls_command(config: Path, out: Path) -> None:
    from .validation import check_controls

    result = asyncio.run(check_controls(load_config(config), out))
    typer.echo(json.dumps(result, indent=2))


@app.command("check-recovery")
def check_recovery_command(config: Path, out: Path) -> None:
    from .validation import check_recovery

    typer.echo(json.dumps(asyncio.run(check_recovery(load_config(config), out)), indent=2))


@app.command("check-outage")
def check_outage_command(config: Path, out: Path) -> None:
    from .validation import check_outage

    typer.echo(json.dumps(asyncio.run(check_outage(load_config(config), out)), indent=2))


@app.command()
def doctor(config: Path) -> None:
    """Check models, Docker and runtime compatibility before execution."""
    from .admission import preflight

    typer.echo(json.dumps(asyncio.run(preflight(load_config(config))), indent=2))


@app.command()
def matrix(
    configs: Path,
    out: Path,
    format: FormatOption = OutputFormat.auto,
    capture: CaptureOption = None,
) -> None:
    paths = sorted(configs.glob("*.yaml"))
    if not paths:
        raise typer.BadParameter("no_matrix_configurations")
    resolved = [(path, load_config(path)) for path in paths]
    from .admission import validate_static

    for _, config in resolved:
        validate_static(config)
    out.mkdir(parents=True, exist_ok=False)
    (out / "manifest.json").write_text(
        json.dumps(
            {
                "expected_conditions": [path.name for path, _ in resolved],
                "order": "lexicographic_sequential",
                "purpose": "functional_pilot_not_an_inferential_research_result",
            },
            indent=2,
        ),
        encoding="utf-8",
    )

    async def execute() -> list[dict]:
        from .terminal import execute as execute_company

        results = []
        for path, config in resolved:
            result, displayed = await execute_company(
                Runner(config, out / path.stem),
                mode=format,
                capture=capture / path.stem if capture else None,
            )
            results.append({"config": path.name, **result})
            (out / "matrix.json").write_text(json.dumps(results, indent=2), encoding="utf-8")
            if not displayed:
                typer.echo(
                    json.dumps(
                        {
                            "config": path.name,
                            "status": result["status"],
                            "verified": result["verified_tickets"],
                            "total": result["total_tickets"],
                        }
                    )
                )
        return results

    asyncio.run(execute())


@app.command()
def compare(runs: list[Path], out: Path | None = None) -> None:
    from .observer.view import ReadView

    results = []
    for directory in runs:
        view = ReadView(directory).snapshot()
        summary_path = directory / "summary.json"
        summary = json.loads(summary_path.read_text()) if summary_path.exists() else {}
        results.append(
            {
                "run": directory.name,
                "status": view["state"].get("status"),
                "summary": summary,
                "usage": view["usage"],
                "rules": view["rule_summary"],
            }
        )
    text = json.dumps(results, indent=2)
    if out:
        out.parent.mkdir(parents=True, exist_ok=True)
        with out.open("x", encoding="utf-8") as stream:
            stream.write(text)
    typer.echo(text)


if __name__ == "__main__":
    app()
