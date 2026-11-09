import json
import subprocess
import uuid
from pathlib import Path

from fastapi.testclient import TestClient

from institution_lab.config import load_config
from institution_lab.institution.service import Institution
from institution_lab.observer.server import create_app
from institution_lab.store import Store


def test_read_only_api_exact_replay_and_reported_credit_units(tmp_path):
    store = Store(tmp_path / "events.sqlite")
    config = load_config(Path(__file__).parents[1] / "configs" / "smoke.yaml")
    institution = Institution(config, store)
    institution.initialize(
        [{"id": "a", "scope": "foundation", "dependencies": [], "description": "Fixture"}],
        "base",
    )
    initial_sequence = store.events()[-1]["seq"]
    institution.act("worker-00", "claim", {"ticket": "a"}, uuid.uuid4().hex)
    usage = {
        "event": {
            "type": "assistant.usage",
            "data": {
                "accounting": {"sourceSessionId": "fixture", "sequence": 1},
                "inputTokens": 10,
                "outputTokens": 2,
                "cost": 999,
                "aiCreditsStatus": "complete",
                "copilotUsage": {"totalNanoAiu": 500000000},
            },
        },
    }
    store.event("worker-00", "runtime", usage)
    store.event("worker-00", "runtime", usage)
    before = store.events()
    with TestClient(create_app(tmp_path)) as client:
        assert client.get("/").status_code == 200
        assert client.get("/assets/cytoscape.min.js").status_code == 200
        current = client.get("/api/view").json()
        assert current["historical_state_exact"]
        assert current["state"]["tickets"]["a"]["owner"] == "worker-00"
        assert current["usage"]["worker-00"]["ai_credits"] == 0.5
        assert current["usage"]["worker-00"]["observed_calls"] == 1
        earlier = client.get(f"/api/view?at={initial_sequence}").json()
        assert earlier["state"]["tickets"]["a"]["owner"] is None
        assert client.get("/api/event/1").status_code == 200
        assert client.post("/api/view", json={"action": "claim"}).status_code == 405
    assert store.events() == before


def test_real_evidence_catalog_tool_cutoff_files_and_configuration(tmp_path, monkeypatch):
    """Controlled protocol records test projections, not synthetic research results."""
    root = tmp_path / "runs"
    run = root / "matrix" / "distributed"
    run.mkdir(parents=True)
    project = run / "project"
    project.mkdir()
    content = b"print('retained output')\r\n"
    (project / "output.py").write_bytes(content)
    commands = [
        ["init"],
        ["add", "output.py"],
        [
            "-c",
            "user.name=InstitutionLab test",
            "-c",
            "user.email=test@example.invalid",
            "-c",
            "core.autocrlf=false",
            "commit",
            "-m",
            "Controlled observer fixture\n\n"
            "Co-authored-by: Copilot <223556219+Copilot@users.noreply.github.com>",
        ],
    ]
    for arguments in commands:
        subprocess.run(
            ["git", "-C", str(project), "-c", "core.autocrlf=false", *arguments],
            capture_output=True,
            check=True,
        )
    revision = subprocess.run(
        ["git", "-C", str(project), "rev-parse", "HEAD"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    config_root = Path(__file__).parents[1] / "configs"
    config = load_config(config_root / "smoke.yaml")
    metadata = {**config.model_dump(mode="json"), "network_name": "il-fixture"}
    (run / "resolved.json").write_text(json.dumps(metadata))
    store = Store(run / "events.sqlite")
    institution = Institution(config, store)
    institution.initialize(
        [{"id": "a", "scope": "foundation", "dependencies": [], "description": "Fixture"}],
        revision,
    )
    store.event(
        "worker-00",
        "ready",
        {
            "session_id": "controlled-session",
            "container_name": "il-fixture-worker-00",
        },
    )
    store.event(
        "worker-00",
        "runtime",
        {
            "event": {"type": "assistant.message", "data": {"content": "Recorded fixture message"}},
        },
    )
    store.event(
        "worker-00",
        "runtime",
        {
            "event": {
                "type": "tool.execution_start",
                "data": {
                    "toolCallId": "fixture-call",
                    "toolName": "bash",
                    "arguments": {"command": "printf retained-output"},
                },
            },
        },
    )
    started = store.events()[-1]["seq"]
    store.event(
        "worker-00",
        "runtime",
        {
            "event": {
                "type": "tool.execution_complete",
                "data": {
                    "toolCallId": "fixture-call",
                    "success": True,
                    "result": {"content": "retained-output"},
                },
            },
        },
    )
    workspace = run / "workspaces" / "worker-00" / "checkpoint"
    workspace.mkdir(parents=True)
    (workspace / "output.py").write_bytes(content)
    store.event(
        "worker-00",
        "workspace_snapshot",
        {
            "id": "checkpoint",
            "label": "Controlled checkpoint",
            "file_count": 1,
            "errors": [],
        },
    )
    before = store.events()
    with TestClient(create_app(run, runs_root=root, config_root=config_root)) as client:
        runs = client.get("/api/runs").json()
        assert runs["selected"] == "matrix/distributed"
        assert runs["runs"][0]["total"] == 1
        assert client.get("/api/view?run=../../outside").status_code == 404
        activity = client.get("/api/agent?actor=worker-00").json()
        assert activity["sessions"][0]["session_id"] == "controlled-session"
        assert activity["items"][-1]["output"] == "retained-output"
        replay = client.get(f"/api/agent?actor=worker-00&at={started}").json()["items"][-1]
        assert replay["status"] == "running" and replay["output"] is None
        assert "duration_seconds" not in replay
        assert client.get("/api/agent?actor=worker-00&offset=100").json()["items"] == []
        assert client.get("/api/agent?actor=unknown").status_code == 404
        assert client.get("/api/files").json()["revision"] == revision
        assert client.get("/api/file?path=output.py").json()["content"] == content.decode()
        assert client.get("/api/file?path=../events.sqlite").status_code == 400
        assert (
            client.get("/api/files?source=workspace&actor=worker-00").json()["files"][0]["path"]
            == "output.py"
        )
        assert (
            client.get(
                "/api/files",
                params={
                    "source": "workspace",
                    "actor": "worker-00",
                    "at": started,
                },
            ).status_code
            == 404
        )
        assert (
            client.get(
                "/api/file",
                params={
                    "source": "workspace",
                    "actor": "worker-00",
                    "path": "output.py",
                },
            ).json()["content"]
            == content.decode()
        )
        assert "smoke.yaml" in client.get("/api/configs").json()["files"]
        assert client.get("/api/config-file?path=smoke.yaml").json()["yaml"]
        assert client.get("/api/config-file?path=../README.md").status_code == 400
        assert client.get("/api/config").json()["configuration"]["population"] == 2
        detail = None
        retained = False
        run_label = "il-fixture"
        calls = []

        def inspection(*arguments, **_options):
            calls.append(arguments)
            if detail:
                raise RuntimeError(detail)
            if arguments[0] == "container":
                assert "label=institution-lab.run=il-fixture" in arguments
                return "il-fixture-worker-00" if retained else ""
            assert arguments[-1] == "il-fixture-worker-00"
            return json.dumps(
                {
                    "name": "/il-fixture-worker-00",
                    "run": run_label,
                    "status": "running",
                }
            )

        monkeypatch.setattr("institution_lab.observer.files.docker", inspection)
        systems = client.get("/api/system").json()["agents"]
        assert systems[0]["status"] == "removed"
        assert systems[1]["status"] == "not_recorded"
        retained = True
        calls.clear()
        assert client.get("/api/system").json()["agents"][0]["status"] == "running"
        assert len(calls) == 2
        run_label = "another-run"
        assert client.get("/api/system").json()["agents"][0]["status"] == "identity_mismatch"
        detail = "Docker daemon is not reachable"
        assert client.get("/api/system").json()["agents"][0]["status"] == "inspection_error"
    assert store.events() == before
