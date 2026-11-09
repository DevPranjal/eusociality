from __future__ import annotations

import asyncio
import json
import re
from pathlib import Path

import typer
import yaml
from rich.console import Console
from rich.panel import Panel
from rich.prompt import Confirm, IntPrompt, Prompt
from rich.table import Table
from rich.text import Text

from .admission import preflight, validate_static
from .config import RunConfig, load_config
from .runner import Runner
from .terminal import ACCENT, MUTED, execute

RULE_LABELS = {
    "capabilities": ("Work permissions", "Level scopes and unfinished-task allowance"),
    "delegation": ("Delegation", "Level authority; recipient may accept or refuse"),
    "review_gate": ("Independent review", "Peer approval and protected checks before integration"),
    "performance": ("Rewards", "Points for accepted delivery with passing recorded checks"),
    "promotion": ("Progression", "New capabilities at verified-completion thresholds"),
    "goal_gate": ("Goal review", "Independent agent assessment before accepting the shared goal"),
}


def integer(console: Console, label: str, default: int, low: int, high: int) -> int:
    while True:
        value = IntPrompt.ask(label, console=console, default=default)
        if low <= value <= high:
            return value
        console.print(f"Enter a number from {low} to {high}.", style="red")


def choose(console: Console, title: str, options: list[str]) -> int:
    for index, option in enumerate(options, 1):
        console.print(Text(f"  {index}. {option}"))
    return integer(console, title, 1, 1, len(options)) - 1


def section(console: Console, number: int, title: str) -> None:
    console.print()
    console.rule(f"[{ACCENT}]{number:02} / {title}[/{ACCENT}]", align="left")


def show_rules(console: Console, config: RunConfig) -> None:
    table = Table(box=None, padding=(0, 2))
    table.add_column("Rule")
    table.add_column("State")
    table.add_column("Meaning", style=MUTED)
    for rule in config.rules:
        label, detail = RULE_LABELS.get(rule.plugin, (rule.plugin, "Trusted custom rule"))
        if rule.plugin == "performance":
            detail += f" ({rule.parameters.get('points_per_completion', 1)} per completion)"
        table.add_row(label, "On" if rule.enabled else "Off", detail)
    console.print(table)
    console.print(
        Text(f"Enforcement: {config.enforcement}. Ownership and sandbox invariants always apply."),
        style=MUTED,
    )


def show_levels(console: Console, config: RunConfig) -> None:
    table = Table(box=None, padding=(0, 2))
    for column in ("Level", "Verified completions", "Tasks held", "Scopes", "Delegate"):
        table.add_column(column)
    for level in config.levels:
        table.add_row(
            level.name,
            str(level.completions),
            str(level.max_active),
            ", ".join(level.scopes),
            "Yes" if level.delegate else "No",
        )
    console.print(table)


def setup(
    project: Path | None = None,
    base: Path | None = None,
    save_only: bool = False,
) -> None:
    console = Console()
    resources = Path(__file__).parents[2]
    root = project.resolve() if project else resources
    config_path = base.resolve() if base else resources / "configs" / "society.yaml"
    if not config_path.is_file():
        raise typer.BadParameter("Default assets are unavailable; provide --base CONFIG.")
    config = load_config(config_path)
    console.print(
        Panel(
            Text.assemble(
                ("eusociality\n", f"bold {ACCENT}"),
                ("Create your society, review its rules, then follow its work.", MUTED),
            ),
            border_style=ACCENT,
        )
    )
    section(console, 1, "Society")
    while True:
        name = Prompt.ask("Society name", console=console, default="my-society").strip()
        directory = root / ".eusoc" / name
        output = root / "runs" / name
        if not re.fullmatch(r"[a-z][a-z0-9-]{0,49}", name):
            console.print(
                "Use lowercase letters, numbers and hyphens; begin with a letter.", style="red"
            )
        elif directory.exists() or output.exists():
            console.print(
                "That society already exists. Choose a new name; nothing is overwritten.",
                style="red",
            )
        else:
            break
    allocation = choose(
        console,
        "Allocation",
        [
            "Agents choose their own tasks",
            "A central agent allocates work (counts inside population)",
        ],
    )
    population = integer(
        console,
        "Number of agents",
        2 if allocation == 0 else 3,
        2 if allocation == 0 else 3,
        50,
    )
    document = config.model_dump(mode="json")
    document.update(
        name=name,
        population=population,
        concurrency=min(population, 2),
        coordination="distributed" if allocation == 0 else "central",
        deadline_seconds=600,
        max_activations=20,
        model_overrides={},
        initial_levels={},
        workload=None,
        goal=None,
    )
    section(console, 2, "Starting levels")
    show_levels(console, config)
    names = [level.name for level in config.levels]
    level = Prompt.ask("Default starting level", console=console, choices=names, default=names[0])
    document["initial_levels"] = {f"worker-{i:02}": level for i in range(population)}
    if Confirm.ask("Set individual starting levels?", console=console, default=False):
        for actor in document["initial_levels"]:
            document["initial_levels"][actor] = Prompt.ask(
                actor,
                console=console,
                choices=names,
                default=level,
            )
    console.print("Starting authority does not award completion credit.", style=MUTED)
    section(console, 3, "Rules")
    show_rules(console, config)
    if not Confirm.ask("Keep these default rules?", console=console, default=True):
        for rule in document["rules"]:
            label = RULE_LABELS.get(rule["plugin"], (rule["plugin"], ""))[0]
            rule["enabled"] = Confirm.ask(label, console=console, default=rule["enabled"])
        performance = next((r for r in document["rules"] if r["plugin"] == "performance"), None)
        promotion = next((r for r in document["rules"] if r["plugin"] == "promotion"), None)
        if promotion and promotion["enabled"] and (not performance or not performance["enabled"]):
            raise typer.BadParameter("Progression requires Rewards. Restart with compatible rules.")
        if performance and performance["enabled"]:
            performance["parameters"]["points_per_completion"] = integer(
                console,
                "Points per verified completion",
                1,
                1,
                100,
            )
        document["enforcement"] = ["hard", "advisory"][
            choose(
                console,
                "Enforcement",
                [
                    "Enforce rules",
                    "Advisory only (violations are recorded)",
                ],
            )
        ]
    section(console, 4, "Runtime")
    credits = document["runtime"]["max_ai_credits"]
    credit_label = (
        f"{credits} credits per session" if credits is not None else "No harness credit cap"
    )
    console.print(
        Text(
            f"{document['runtime']['model']} / "
            f"{document['runtime']['reasoning_effort']} reasoning\n"
            f"{document['concurrency']} concurrent turns / 10 minutes / "
            f"{credit_label}"
        )
    )
    if not Confirm.ask("Keep these runtime limits?", console=console, default=True):
        document["concurrency"] = integer(
            console, "Concurrent turns", min(population, 2), 1, min(population, 30)
        )
        document["deadline_seconds"] = 60 * integer(console, "Maximum minutes", 10, 1, 180)
        document["max_activations"] = integer(console, "Activations per agent", 20, 1, 200)
        document["runtime"]["model"] = Prompt.ask(
            "Model",
            console=console,
            default=document["runtime"]["model"],
        )
        document["runtime"]["reasoning_effort"] = Prompt.ask(
            "Reasoning",
            console=console,
            choices=["none", "low", "medium", "high", "xhigh", "max"],
            default=document["runtime"]["reasoning_effort"],
        )
        uncapped = Confirm.ask("Remove the per-session credit cap?", console=console, default=False)
        if uncapped:
            console.print(
                "Provider limits and billing still apply. The wall deadline remains.",
                style="yellow",
            )
        document["runtime"]["max_ai_credits"] = (
            None
            if uncapped
            else integer(
                console,
                "Per-session AI credit cap",
                200,
                30,
                10000,
            )
        )
    config = RunConfig.model_validate(document)
    section(console, 5, "Goal")
    goal = Prompt.ask("What should your society achieve?", console=console).strip()
    if not goal:
        raise typer.BadParameter("The shared goal cannot be empty.")
    config = RunConfig.model_validate(
        {
            **config.model_dump(mode="json"),
            "goal": goal,
            "research": {"enabled": True, "allowed_hosts": []},
        }
    )
    validate_static(config)
    directory.mkdir(parents=True)
    saved = directory / "config.yaml"
    saved.write_text(
        yaml.safe_dump(config.model_dump(mode="json"), sort_keys=False, allow_unicode=True),
        encoding="utf-8",
    )
    section(console, 6, "Review society and start")
    show_rules(console, config)
    console.print(
        Text(
            f"\n{population} identities / {config.coordination} / "
            f"{config.deadline_seconds // 60} minutes\n"
            "Starting levels: "
            + ", ".join(f"{actor}={level}" for actor, level in config.initial_levels.items())
        )
    )
    console.print(Panel(Text(config.goal or ""), title="Shared goal", border_style=ACCENT))
    console.print(
        "The board starts empty. Your agents propose and select work, define checks, "
        "review outputs, and judge the shared goal. No separate planner is created.",
        style=MUTED,
    )
    console.print(
        Text("Public sources: " + ("Any public HTTP(S) resource (recorded, read-only)")),
        style=MUTED,
    )
    console.print(Text(f"\nSaved configuration: {saved}"), style=MUTED)
    console.print(
        Text(
            "You may edit the saved configuration before confirming. "
            "Launch reloads and validates it."
        ),
        style=MUTED,
    )
    if save_only or not Confirm.ask("Start this society?", console=console, default=False):
        console.print(Text(f'Not launched. Start later: eusoc run "{saved}" "{output}" --timeline'))
        return
    config = load_config(saved)
    validate_static(config)
    with console.status("Checking model and runtime readiness..."):
        asyncio.run(preflight(config))
    controller = Runner(config, output)
    (output / "setup.json").write_text(
        json.dumps(
            {
                "configuration": str(saved),
                "task_creation": "society_agents",
                "reviewed_before_launch": True,
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    console.print(Text(f"\nSociety started. Evidence: {output}\n"), style=ACCENT)
    summary, _ = asyncio.run(execute(controller, timeline=True))
    console.print(
        Text(
            f"\n{summary['status']} / {summary['verified_tickets']}/{summary['total_tickets']} "
            f'verified tasks\nInspect: eusoc status "{output}"\n'
            f'Board: eusoc observe "{output}"'
        )
    )
