import uuid
from pathlib import Path

import pytest

from institution_lab.config import load_config
from institution_lab.evaluation.evaluator import Evaluator
from institution_lab.evaluation.workload import Workload
from institution_lab.project import Project, docker, git
from institution_lab.runner import Runner, within_deadline
from institution_lab.runtime.docker import DockerImages, DockerNetwork, DockerWorker


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("workload_name", "ticket", "source"),
    [
        ("smoke", "lower", "def run(data):\n    return [value.lower() for value in data]\n"),
        (
            "observability-platform",
            "utc_seconds",
            "from datetime import datetime\n"
            "def run(data):\n"
            "    return [int(datetime.fromisoformat(value.replace('Z', '+00:00'))"
            ".timestamp()) for value in data]\n",
        ),
    ],
)
async def test_real_workspace_snapshot_evaluation_integration_and_sync(
    tmp_path,
    monkeypatch,
    workload_name,
    ticket,
    source,
):
    import institution_lab.project as project_module

    manifest = Path(__file__).parents[1] / "workloads" / f"{workload_name}.yaml"
    workload = Workload.load(manifest)
    project = Project(tmp_path / "run", workload)
    initial = project.initialize(manifest)
    network = DockerNetwork(DockerImages(), uuid.uuid4().hex[:12])

    async def unused_request(action, payload, key):
        raise AssertionError("This functional workspace test does not run an AI actor.")

    def unused_event(actor, event):
        raise AssertionError("This functional workspace test does not run an AI actor.")

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
        "--memory",
        "256m",
        "--pids-limit",
        "32",
        "--user",
        "0:1001",
        "--entrypoint",
        "sleep",
        network.images.worker,
        "120",
    )
    try:
        project.seed(worker)
        docker(
            "exec",
            "--user",
            "0:1001",
            worker.name,
            "python",
            "-c",
            f"from pathlib import Path; Path('/workspace/operations/{ticket}.py')"
            f".write_text({source!r})",
        )
        calls = []
        original = project_module.container_git

        def recorded_git(*args, **kwargs):
            calls.append(args[1:])
            return original(*args, **kwargs)

        monkeypatch.setattr(project_module, "container_git", recorded_git)
        artifact, base, digest, diff = project.snapshot(worker)
        assert len(calls) == 6
        assert not any(arguments[0] == "cat-file" for arguments in calls)
        assert len(list((artifact / "operations").glob("*.py"))) == len(workload.tickets) + 1
        assert (artifact / "operations" / f"{ticket}.py").read_bytes() == source.encode()
        assert base == initial and len(digest) == 64
        assert f"operations/{ticket}.py" in diff
        assert (
            "operations/total.py" if ticket == "lower" else "operations/percentile.py"
        ) not in diff
        evaluator = Evaluator(workload, network.images.worker)
        result = await evaluator.evaluate(artifact, [ticket])
        assert result["passed"], result
        head = project.integrate(artifact, base)
        assert head != initial and git(project.canonical, "rev-parse", "HEAD") == head
        assert project.sync(worker) == {"ok": True, "head": head}
        (artifact / "operations" / f"{ticket}.py").write_text("def run(data):\n    return []\n")
        failed = await evaluator.evaluate(artifact, [ticket])
        assert not failed["passed"]
        assert failed["checks"][0]["ticket"] == ticket
    finally:
        docker("rm", "-f", worker.name)


@pytest.mark.asyncio
async def test_real_io_action_replay_preserves_proposal_approval_and_single_credit(tmp_path):
    import asyncio

    config = load_config(Path(__file__).parents[1] / "configs" / "smoke.yaml")
    runner = Runner(config, tmp_path / "run")
    head = runner.project.initialize(config.workload)
    runner.institution.initialize([t.public() for t in runner.workload.tickets], head)

    async def unused_request(action, payload, key):
        raise AssertionError("No model session is used by this functional test.")

    def unused_event(actor, event):
        raise AssertionError("No model session is used by this functional test.")

    worker = DockerWorker(runner.network, "worker-00", unused_request, unused_event)
    runner.workers["worker-00"] = worker
    runner.workspace_locks["worker-00"] = asyncio.Lock()
    docker(
        "run",
        "-d",
        "--name",
        worker.name,
        "--network",
        "none",
        "--cap-drop",
        "ALL",
        "--memory",
        "256m",
        "--pids-limit",
        "32",
        "--user",
        "1001:1001",
        "--entrypoint",
        "sleep",
        runner.images.worker,
        "120",
    )
    try:
        runner.project.seed(worker)
        assert (await runner.request("worker-00", "claim", {"ticket": "lower"}, "claim"))["ok"]
        docker(
            "exec",
            "--user",
            "1001:1001",
            worker.name,
            "python",
            "-c",
            "from pathlib import Path; Path('/workspace/operations/lower.py').write_text("
            "'def run(data):\\n    return [value.lower() for value in data]\\n')",
        )
        first = await runner.request("worker-00", "submit", {"ticket": "lower"}, "submit-once")
        repeated = await runner.request("worker-00", "submit", {"ticket": "lower"}, "submit-once")
        assert repeated == first
        proposal_id = first["proposal"]["id"]
        assert len(runner.store.state()["proposals"]) == 1
        assert (
            await runner.request(
                "worker-01",
                "review",
                {
                    "proposal": proposal_id,
                    "approve": True,
                    "comment": "Reviewed exact functional fixture.",
                },
                "review",
            )
        )["ok"]
        integrated = await runner.request(
            "worker-00", "integrate", {"proposal": proposal_id}, "integrate-once"
        )
        assert integrated["ok"]
        assert (
            await runner.request(
                "worker-00", "integrate", {"proposal": proposal_id}, "integrate-once"
            )
            == integrated
        )
        assert runner.store.state()["agents"]["worker-00"]["score"] == 1
        assert git(runner.project.canonical, "rev-parse", "HEAD") == integrated["head"]
        assert (await runner.request("worker-00", "claim", {"ticket": "total"}, "claim-total"))[
            "ok"
        ]
        docker(
            "exec",
            "--user",
            "1001:1001",
            worker.name,
            "python",
            "-c",
            "from pathlib import Path; p=Path('/workspace/operations/nested'); p.mkdir(); "
            "(p/'unsupported.py').write_text('x=1')",
        )
        rejected = await runner.request(
            "worker-00", "submit", {"ticket": "total"}, "unsupported-change"
        )
        assert not rejected["ok"] and "unsupported" in rejected["reason"]
        assert runner.store.state()["status"] == "ready"
    finally:
        docker("rm", "-f", worker.name)


@pytest.mark.asyncio
async def test_worker_timeout_is_not_reported_as_global_deadline():
    import asyncio

    async def worker_timeout():
        raise TimeoutError("inner_worker_timeout")

    with pytest.raises(TimeoutError, match="inner_worker_timeout"):
        await within_deadline(worker_timeout, 1)

    async def slow():
        await asyncio.sleep(1)
        return "completed"

    assert await within_deadline(slow, 0.01) == "deadline"
