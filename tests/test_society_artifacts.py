import uuid

import pytest

from institution_lab.evaluation.evaluator import Evaluator
from institution_lab.project import COAUTHOR, Project, ProjectRejected, docker, git
from institution_lab.runtime.docker import DockerImages, DockerNetwork, DockerWorker


@pytest.mark.asyncio
@pytest.mark.parametrize("unrelated_publication", [False, True])
async def test_real_general_artifacts_are_scoped_evaluated_reviewable_and_synced(
    tmp_path,
    unrelated_publication,
):
    project = Project(tmp_path / "run", None)
    head = project.initialize(None)
    network = DockerNetwork(DockerImages(), uuid.uuid4().hex[:12])

    async def unused_request(*_args):
        raise AssertionError("No model calls in this real-container functional test")

    def unused_event(*_args):
        raise AssertionError("No model calls in this real-container functional test")

    worker = DockerWorker(network, "worker", unused_request, unused_event)
    docker(
        "run",
        "-d",
        "--name",
        worker.name,
        "--network",
        "none",
        "--cap-drop",
        "ALL",
        "--user",
        "1001:1001",
        "--entrypoint",
        "sleep",
        network.images.worker,
        "120",
    )
    task = {
        "id": "page",
        "files": ["project/index.html"],
        "acceptance": ["Page has a heading"],
        "checks": [
            {
                "argv": [
                    "python",
                    "-c",
                    "from pathlib import Path; assert '<h1>' in Path('index.html').read_text()",
                ]
            }
        ],
    }
    definitions = {"page": task}
    try:
        project.seed(worker)
        docker(
            "exec",
            "--user",
            "1001:1001",
            worker.name,
            "python",
            "-c",
            "from pathlib import Path; Path('/workspace/project').rmdir()",
        )
        with pytest.raises(ProjectRejected, match="no_task_artifacts"):
            project.snapshot(worker, definitions, "page")
        docker(
            "exec",
            "--user",
            "1001:1001",
            worker.name,
            "python",
            "-c",
            "from pathlib import Path; Path('/workspace/project').mkdir(exist_ok=True); "
            "Path('/workspace/project/index.html').write_text("
            "'<!doctype html><html><h1>Controlled page</h1></html>\\n')",
        )
        artifact, base, _, diff = project.snapshot(worker, definitions, "page")
        assert base == head and "project/index.html" in diff
        evaluator = Evaluator(None, network.images.worker)
        result = await evaluator.evaluate(artifact, ["page"], definitions)
        assert result["passed"], result
        assert result["basis"] == "agent_defined_checks_and_artifacts"
        next_head = project.integrate(artifact, base)
        if unrelated_publication:
            (project.canonical / "project" / "other.md").write_text("Controlled independent file\n")
            git(project.canonical, "add", "project/other.md")
            git(
                project.canonical,
                "commit",
                "-m",
                "Controlled unrelated publication",
                "-m",
                COAUTHOR,
            )
            next_head = git(project.canonical, "rev-parse", "HEAD")
        assert project.sync(worker) == {"ok": True, "head": next_head}
        docker(
            "exec",
            "--user",
            "1001:1001",
            worker.name,
            "python",
            "-c",
            "from pathlib import Path; Path('/workspace/project/index.html').write_text("
            "'genuine unpublished work\\n')",
        )
        assert project.sync(worker)["reason"] == "unpublished_changes_preserved"
        assert "genuine unpublished work" in docker(
            "exec",
            "--user",
            "1001:1001",
            worker.name,
            "cat",
            "/workspace/project/index.html",
        )
        (artifact / "project" / "index.html").write_text("broken")
        assert not (await evaluator.evaluate(artifact, ["page"], definitions))["passed"]
        docker(
            "exec",
            "--user",
            "1001:1001",
            worker.name,
            "python",
            "-c",
            "from pathlib import Path; Path('/workspace/project/undeclared.txt').write_text('x')",
        )
        with pytest.raises(ProjectRejected, match="unsupported"):
            project.snapshot(worker, definitions, "page")
    finally:
        docker("rm", "-f", worker.name)


@pytest.mark.asyncio
async def test_disjoint_preview_checks_current_publication_without_changing_reviewed_patch(
    tmp_path,
):
    project = Project(tmp_path / "run", None)
    base = project.initialize(None)
    artifact = project.artifacts / "controlled-candidate"
    git(project.root, "clone", "--quiet", str(project.canonical), str(artifact))
    (artifact / "project").mkdir(exist_ok=True)
    (artifact / "project" / "page.md").write_text("correct\n")
    git(artifact, "add", "-A")
    original_diff = git(artifact, "diff", "--cached", "--binary", base)
    (project.canonical / "project").mkdir(exist_ok=True)
    (project.canonical / "project" / "other.md").write_text("Independent published work\n")
    git(project.canonical, "add", "-A")
    git(project.canonical, "commit", "-m", "Controlled independent work", "-m", COAUTHOR)
    current = project.compatible_integration_base(artifact, base, set())
    definitions = {
        "page": {
            "id": "page",
            "files": ["project/page.md"],
            "acceptance": ["Controlled check"],
            "checks": [
                {
                    "argv": [
                        "python",
                        "-c",
                        "from pathlib import Path; "
                        "assert Path('page.md').read_text() == 'correct\\n'",
                    ]
                }
            ],
        },
        "other": {
            "id": "other",
            "files": ["project/other.md"],
            "checks": [],
            "acceptance": ["Controlled presence"],
        },
    }
    evaluator = Evaluator(None, DockerImages().worker)
    assert not (await evaluator.evaluate(artifact, list(definitions), definitions))["passed"]
    preview = project.evaluation_preview(artifact, base, current)
    assert (await evaluator.evaluate(preview, list(definitions), definitions))["passed"]
    assert git(artifact, "diff", "--cached", "--binary", base) == original_diff
    with pytest.raises(ProjectRejected, match="overlapping"):
        project.compatible_integration_base(artifact, base, {"project/other.md"})
    (preview / "project" / "page.md").write_text("regression\n")
    assert not (await evaluator.evaluate(preview, list(definitions), definitions))["passed"]
    project.integrate(artifact, base, current_base=current)
    assert (
        project.canonical / "project" / "other.md"
    ).read_text() == "Independent published work\n"
