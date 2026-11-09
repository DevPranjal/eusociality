import asyncio
from pathlib import Path

import pytest

import institution_lab.runner as runner_module
from institution_lab.config import load_config
from institution_lab.runner import Runner


@pytest.mark.asyncio
async def test_failed_startup_drains_siblings_before_cleanup(tmp_path, monkeypatch):
    """Controlled startup failure; this test does not synthesize AI outputs."""
    config = load_config(Path(__file__).parents[1] / "configs" / "smoke.yaml")
    config = config.model_copy(update={"population": 4, "concurrency": 1, "keep_awake": False})
    controller = Runner(config, tmp_path / "run")
    events = []

    async def catalog(_config):
        return {}

    class Network:
        name = "il-controlled-startup"

        def start(self):
            events.append("network-start")

        def close(self):
            events.append("network-close")

    class Worker:
        def __init__(self, actor):
            self.actor = actor
            self.process = None

        async def close(self):
            events.append("close:" + self.actor)

    async def start(actor):
        events.append("start:" + actor)
        controller.workers[actor] = Worker(actor)
        if actor == "worker-00":
            await asyncio.sleep(0)
            raise RuntimeError("controlled_startup_failure")
        await asyncio.sleep(0.02)

    monkeypatch.setattr(runner_module, "preflight", catalog)
    monkeypatch.setattr(controller, "network", Network())
    monkeypatch.setattr(controller, "start_worker", start)
    with pytest.raises(ExceptionGroup) as failure:
        await controller.run()
    assert any("controlled_startup_failure" in str(error) for error in failure.value.exceptions)
    await asyncio.sleep(0.1)
    closed_at = events.index("network-close")
    assert not any(event.startswith("start:") for event in events[closed_at + 1 :])
    for event in events:
        if event.startswith("start:"):
            assert events.count("close:" + event.removeprefix("start:")) == 1
    assert controller.store.state()["status"] == "failed"


@pytest.mark.asyncio
async def test_quiescent_society_gets_bounded_neutral_rechecks_not_hidden_work(tmp_path):
    config = load_config(Path(__file__).parents[1] / "configs" / "society-flow-check.yaml")
    config = config.model_copy(update={"scheduler_mode": "workflow", "quiescent_rechecks": 1})
    controller = Runner(config, tmp_path / "run")
    controller.institution.initialize([], "base")
    controller.store.update(lambda state: state.update(status="running"))
    controller.activations = {"worker-00": 1, "worker-01": 1}
    controller.seen = {"worker-00": 100, "worker-01": 100}
    controller.seen_inbox = {"worker-00": 100, "worker-01": 100}
    observed = []

    async def activate(actor):
        observed.append(actor)
        controller.activations[actor] += 1

    controller.activate = activate
    assert await controller.loop() == "stalled"
    assert observed == ["worker-00", "worker-01"]
    assert controller.store.state()["tickets"] == {}
    assert sum(row["kind"] == "quiescent_recheck" for row in controller.store.events()) == 2
