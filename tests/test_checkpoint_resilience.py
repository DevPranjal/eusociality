import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from institution_lab.config import load_config
from institution_lab.runner import Runner
from institution_lab.runtime import workspace_reader


@pytest.mark.asyncio
async def test_best_effort_checkpoint_timeout_is_logged_not_a_society_failure(
    tmp_path, monkeypatch
):
    config = load_config(Path(__file__).parents[1] / "configs" / "society-flow-check.yaml")
    runner = Runner(config, tmp_path / "run")
    runner.workers["worker-00"] = SimpleNamespace(name="il-controlled-worker")

    def timeout(*_args, **_kwargs):
        raise subprocess.TimeoutExpired(["docker", "exec", "controlled"], 120)

    monkeypatch.setattr("institution_lab.runner.docker", timeout)
    await runner.capture_workspace("worker-00", "Controlled timeout")
    rows = runner.store.events()
    assert rows[-1]["kind"] == "workspace_capture_error"
    assert "timed out" in rows[-1]["body"]["detail"]


def test_checkpoint_traversal_prunes_excluded_trees_before_descending(tmp_path, monkeypatch):
    root = tmp_path / "workspace"
    (root / "project").mkdir(parents=True)
    (root / "project" / "note.md").write_text("Controlled file", newline="")
    (root / "node_modules" / "deep").mkdir(parents=True)
    (root / "node_modules" / "deep" / "never_read").write_text("Excluded")
    monkeypatch.setattr(workspace_reader, "ROOT", root)

    def forbidden_traversal(*_args, **_kwargs):
        raise AssertionError("Do not walk excluded trees with an unpruned rglob")

    monkeypatch.setattr(Path, "rglob", forbidden_traversal)
    result = workspace_reader.capture()
    assert result["files"] == {"project/note.md": "Controlled file"}
