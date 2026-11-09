from pathlib import Path

from fastapi.testclient import TestClient

from institution_lab.config import load_config
from institution_lab.institution.service import Institution
from institution_lab.observer.server import create_app
from institution_lab.observer.view import ReadView
from institution_lab.project import COAUTHOR, Project, git
from institution_lab.store import Store


def test_completed_run_event_pages_and_published_files_follow_exact_history(tmp_path):
    project = Project(tmp_path / "run", None)
    first = project.initialize(None)
    store = Store(project.root / "events.sqlite")
    store.event("controller", "admission", {"detail": "Controlled pre-initialization event"})
    config = load_config(Path(__file__).parents[1] / "configs" / "society.yaml").model_copy(
        update={"goal": "Controlled historical deliverable fixture"},
    )
    institution = Institution(config, store)
    institution.initialize([], first)
    initialized = store.events()[-1]["seq"]
    outputs = project.canonical / "project"
    outputs.mkdir(exist_ok=True)
    (outputs / "welcome.md").write_text("First published version\n", newline="")
    (outputs / "old.md").write_text("An earlier deliverable\n", newline="")
    git(project.canonical, "add", "-A")
    git(project.canonical, "commit", "-m", "Controlled first publication", "-m", COAUTHOR)
    version_one = git(project.canonical, "rev-parse", "HEAD")
    store.update(lambda state: state.update(head=version_one))
    store.event(
        "worker-00",
        "runtime",
        {
            "event": {"type": "assistant.message", "data": {"content": "First version delivered"}},
        },
    )
    before_edit = store.events()[-1]["seq"]
    (outputs / "welcome.md").write_text("Revised published version\n", newline="")
    (outputs / "old.md").unlink()
    (outputs / "later.md").write_text("Only exists after the second publication\n")
    git(project.canonical, "add", "-A")
    git(project.canonical, "commit", "-m", "Controlled second publication", "-m", COAUTHOR)
    version_two = git(project.canonical, "rev-parse", "HEAD")
    store.update(lambda state: state.update(head=version_two))
    for index in range(105):
        store.event(
            "worker-01",
            "runtime",
            {
                "event": {
                    "type": "assistant.message",
                    "data": {"content": f"Recorded item {index}"},
                },
            },
        )
    store.event(
        "worker-01",
        "runtime",
        {
            "event": {"type": "assistant.message_delta", "data": {"deltaContent": "Token delta"}},
        },
    )
    store.update(lambda state: state.update(status="completed"))
    store.event("controller", "run_end", {"status": "completed"})
    original = store.events(limit=500)
    with TestClient(create_app(project.root, runs_root=tmp_path)) as client:
        page = client.get("/api/history?run=run&limit=5").json()
        assert page["goal"] == config.goal and page["status"] == "completed"
        assert page["has_more"] and len(page["items"]) == 5
        assert [row["seq"] for row in page["items"]] == sorted(row["seq"] for row in page["items"])
        filtered = client.get("/api/history?run=run&actor=worker-00").json()
        assert [item["title"] for item in filtered["items"]] == ["First version delivered"]
        around = client.get(
            f"/api/history?run=run&around={before_edit}&limit=2",
        ).json()
        assert before_edit in [item["seq"] for item in around["items"]]
        all_events = client.get("/api/history?run=run&all_events=true&limit=200").json()
        assert all_events["total"] == len(original)
        assert page["total"] < all_events["total"]
        assert client.get("/api/history?offset=-1").status_code == 422
        assert client.get("/api/history?run=../outside").status_code == 404
        assert client.get("/api/event/999999?run=run").status_code == 404
        assert client.get("/api/files?run=run&at=1").json()["files"] == []
        assert not any(
            item["path"].startswith("project/")
            for item in client.get(f"/api/files?run=run&at={initialized}").json()["files"]
        )
        earlier = client.get(f"/api/files?run=run&at={before_edit}").json()
        assert earlier["revision"] == version_one and earlier["historical_exact"]
        assert "project/old.md" in [item["path"] for item in earlier["files"]]
        assert "project/later.md" not in [item["path"] for item in earlier["files"]]
        assert (
            client.get(
                f"/api/file?run=run&path=project/welcome.md&at={before_edit}",
            ).json()["content"]
            == "First published version\n"
        )
        assert (
            client.get(
                f"/api/file?run=run&path=project/later.md&at={before_edit}",
            ).status_code
            == 404
        )
        latest = client.get("/api/files?run=run").json()
        assert latest["revision"] == version_two
        assert "project/old.md" not in [item["path"] for item in latest["files"]]
        assert client.get("/api/file?run=run&path=project/welcome.md").json()["content"] == (
            "Revised published version\n"
        )
    assert store.events(limit=500) == original
    thin = ReadView(project.root)
    assert thin.published_at(before_edit)[0] == version_one
    assert thin.entries == [] and thin.cursor == 0
