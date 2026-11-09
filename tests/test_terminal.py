import asyncio
import io
import json
import uuid
from pathlib import Path
from types import SimpleNamespace
from xml.etree import ElementTree

import pytest
from rich.console import Console
from typer.testing import CliRunner

from institution_lab.cli import app
from institution_lab.config import load_config
from institution_lab.institution.service import Institution
from institution_lab.store import Store
from institution_lab.terminal import FrameCapture, OutputFormat, TerminalView, execute


def recorded_company(root):
    """Controlled protocol fixture, not a simulated model or research result."""
    root.mkdir()
    config = load_config(Path(__file__).parents[1] / "configs" / "smoke.yaml")
    config = config.model_copy(update={"population": 3})
    store = Store(root / "events.sqlite")
    institution = Institution(config, store)
    institution.initialize(
        [{"id": "a", "scope": "foundation", "dependencies": [], "description": "Fixture"}],
        "base",
    )
    (root / "resolved.json").write_text(json.dumps(config.model_dump(mode="json")))
    return config, store, institution


def test_terminal_tracks_recorded_activity_and_exports_provenance(tmp_path):
    run = tmp_path / "company"
    _, store, institution = recorded_company(run)
    store.event("worker-00", "activation", {"number": 1})
    institution.act(
        "worker-00",
        "message",
        {
            "to": "worker-01",
            "content": "[bold]literal[/bold] review request",
        },
        uuid.uuid4().hex,
    )
    before = store.events()
    view = TerminalView(run)
    snapshot = view.sample()
    assert view.active == {"worker-00"} and view.turns["worker-00"] == 1
    for width, height in ((72, 24), (150, 48)):
        output = io.StringIO()
        console = Console(file=output, width=width, height=height, color_system=None)
        console.print(view.render(snapshot, width, ascii_only=True, height=height))
        rendered = output.getvalue()
        assert len(rendered.splitlines()) <= height
        assert "worker-00" in rendered and "ACTIVE #1" in rendered
        assert "[bold]literal[/bold]" in rendered
        assert "Final evaluation: PASS" not in rendered
    capture = FrameCapture(tmp_path / "frames", run, 150, "controlled-protocol-test")
    capture.record(view.render(snapshot, 150), snapshot, final=True)
    manifest = json.loads((tmp_path / "frames" / "frames.json").read_text())
    frame = tmp_path / "frames" / manifest["frames"][0]["file"]
    ElementTree.parse(frame)
    assert "@font-face" not in frame.read_text(encoding="utf-8")
    assert manifest["frames"][0]["event_cursor"] == snapshot["cursor"]
    assert manifest["source"] == "controlled-protocol-test"
    assert store.events() == before
    store.event("worker-00", "turn_end", {"outcome": "completed"})
    view.sample()
    assert not view.active


def test_terminal_cli_preserves_json_and_formats_human_status(tmp_path):
    run = tmp_path / "company"
    _, store, _ = recorded_company(run)
    cli = CliRunner()
    default = cli.invoke(app, ["status", str(run)])
    explicit = cli.invoke(app, ["status", str(run), "--format", "json"])
    assert default.exit_code == explicit.exit_code == 0
    assert json.loads(default.output) == json.loads(explicit.output) == store.state()
    human = cli.invoke(app, ["status", str(run), "--format", "live"])
    assert human.exit_code == 0
    assert "EUSOCIALITY" in human.output and "WORK BOARD" in human.output
    assert "Final evaluation: PASS" not in human.output


@pytest.mark.asyncio
async def test_display_failure_does_not_cancel_company_work(tmp_path, monkeypatch):
    run = tmp_path / "company"
    config, _, _ = recorded_company(run)
    finished = []

    async def company():
        await asyncio.sleep(0.05)
        finished.append(True)
        return {"controlled": True}

    def failed_capture(*_args, **_kwargs):
        raise OSError("controlled_capture_failure")

    monkeypatch.setattr(FrameCapture, "record", failed_capture)
    controller = SimpleNamespace(output=run, config=config, run=company)
    with pytest.raises(OSError, match="controlled_capture_failure"):
        await execute(controller, mode=OutputFormat.json, capture=tmp_path / "frames")
    assert finished == [True]
