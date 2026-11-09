import asyncio
import uuid
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml
from typer.testing import CliRunner

from institution_lab.cli import app
from institution_lab.config import load_config
from institution_lab.institution.service import Institution
from institution_lab.store import Store
from institution_lab.timeline import Timeline, execute

ROOT = Path(__file__).parents[1]


def test_one_plaintext_goal_saves_society_without_any_model_call(tmp_path, monkeypatch):
    import institution_lab.guided as guided

    async def no_model(*_args):
        raise AssertionError("No agent or planner is created until launch")

    monkeypatch.setattr(guided, "preflight", no_model)
    entered = "\n".join(
        [
            "../escape",
            "guided-fixture",
            "1",
            "2",
            "L0",
            "y",
            "L0",
            "L1",
            "y",
            "y",
            "Research PyTorch and create beginner and advanced documentation.",
            "n",
            "",
        ]
    )
    result = CliRunner().invoke(
        app,
        ["setup", "--project", str(tmp_path), "--save-only"],
        input=entered,
    )
    assert result.exit_code == 0, result.output
    config = load_config(tmp_path / ".eusoc" / "guided-fixture" / "config.yaml")
    assert config.population == 2
    assert config.initial_levels == {"worker-00": "L0", "worker-01": "L1"}
    assert config.goal.startswith("Research PyTorch")
    assert config.workload is None
    assert "Goal source" not in result.output
    assert "No separate planner" in result.output
    assert not (tmp_path / "runs").exists()
    assert not (tmp_path / ".eusoc" / "guided-fixture" / "planning").exists()


def test_launch_reloads_config_and_begins_without_prepared_tasks(tmp_path, monkeypatch):
    import institution_lab.guided as guided

    observed = []
    original_confirm = guided.Confirm.ask

    def confirm(prompt, **options):
        if prompt == "Start this society?":
            path = tmp_path / ".eusoc" / "edited-review" / "config.yaml"
            document = yaml.safe_load(path.read_text())
            document["runtime"]["max_ai_credits"] = 60
            path.write_text(yaml.safe_dump(document))
            return True
        return original_confirm(prompt, **options)

    async def ready(config):
        observed.append(config.runtime.max_ai_credits)

    class Controller:
        def __init__(self, config, output):
            observed.append(config.workload)
            self.output = output
            output.mkdir(parents=True)

    async def execution(_controller, **_options):
        return {"status": "cancelled", "verified_tickets": 0, "total_tickets": 0}, True

    monkeypatch.setattr(guided.Confirm, "ask", confirm)
    monkeypatch.setattr(guided, "preflight", ready)
    monkeypatch.setattr(guided, "Runner", Controller)
    monkeypatch.setattr(guided, "execute", execution)
    result = CliRunner().invoke(
        app,
        ["setup", "--project", str(tmp_path)],
        input="\n".join(
            [
                "edited-review",
                "1",
                "2",
                "L0",
                "n",
                "y",
                "y",
                "Build a documentation site",
                "n",
                "",
            ]
        ),
    )
    assert result.exit_code == 0, result.output
    assert observed == [60, None]


@pytest.mark.asyncio
async def test_timeline_follows_recorded_actions_and_text_tool_arguments(tmp_path):
    config = load_config(ROOT / "configs" / "smoke.yaml")
    store = Store(tmp_path / "events.sqlite")
    institution = Institution(config, store)

    async def company():
        institution.initialize(
            [{"id": "a", "scope": "foundation", "dependencies": [], "description": "Fixture"}],
            "base",
        )
        institution.act("worker-00", "claim", {"ticket": "a"}, uuid.uuid4().hex)
        institution.act(
            "worker-00",
            "message",
            {
                "to": "worker-01",
                "content": "[bold]literal collaboration[/bold]",
            },
            uuid.uuid4().hex,
        )
        store.event(
            "worker-00",
            "runtime",
            {
                "event": {
                    "type": "tool.execution_start",
                    "data": {
                        "toolName": "apply_patch",
                        "toolCallId": "controlled-native-patch",
                        "arguments": (
                            "*** Begin Patch\n*** Add File: /workspace/operations/a.py\n+x=1"
                        ),
                    },
                },
            },
        )
        await asyncio.sleep(0.01)
        store.update(lambda state: state.update(status="cancelled"))
        store.event("controller", "run_end", {"status": "cancelled"})
        return {"status": "cancelled"}

    summary, visible = await execute(SimpleNamespace(output=tmp_path, run=company))
    assert visible and summary["status"] == "cancelled"
    log = (tmp_path / "timeline.txt").read_text(encoding="utf-8")
    assert "Claim a" in log and "[bold]literal collaboration[/bold]" in log
    assert "apply_patch / /workspace/operations/a.py" in log
    assert "Society cancelled" in log
    timeline = Timeline(tmp_path)
    before = store.events()
    assert timeline.poll() and timeline.poll() == []
    assert store.events() == before
