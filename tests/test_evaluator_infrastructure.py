from pathlib import Path

import pytest

from institution_lab.config import load_config
from institution_lab.evaluation.evaluator import EvaluationInfrastructureFailure, Evaluator
from institution_lab.runner import Runner


@pytest.mark.asyncio
async def test_missing_image_is_an_evaluator_startup_failure_not_bad_artifact(tmp_path):
    source = tmp_path / "source"
    (source / "project").mkdir(parents=True)
    (source / "project" / "page.md").write_text("Controlled functional fixture\n")
    definitions = {
        "page": {
            "id": "page",
            "files": ["project/page.md"],
            "checks": [],
            "acceptance": ["Controlled presence check"],
        }
    }
    attempts = []
    evaluator = Evaluator(
        None,
        "sha256:" + "0" * 64,
        on_retry=lambda number, detail: attempts.append((number, detail)),
    )
    with pytest.raises(EvaluationInfrastructureFailure, match="evaluator_startup"):
        await evaluator.evaluate(source, ["page"], definitions)
    assert [number for number, _ in attempts] == [1, 2]
    assert all("evaluator_startup" in detail for _, detail in attempts)


@pytest.mark.asyncio
@pytest.mark.parametrize("infrastructure_failure", [False, True, "unexpected"])
async def test_final_verification_precedes_cleanup_and_cannot_report_false_completion(
    tmp_path,
    monkeypatch,
    infrastructure_failure,
):
    config = load_config(Path(__file__).parents[1] / "configs" / "society.yaml").model_copy(
        update={"goal": "Controlled lifecycle fixture"},
    )
    runner = Runner(config, tmp_path / "run")
    closed = []

    async def no_models(*_args):
        return None

    async def controlled_loop():
        return "completed"

    async def controlled_evaluation(*_args):
        assert not closed
        if infrastructure_failure == "unexpected":
            raise RuntimeError("controlled unexpected verifier failure")
        if infrastructure_failure:
            raise EvaluationInfrastructureFailure("evaluator_startup: controlled failure")
        return {"passed": False, "checks": [], "failure": "controlled_check"}

    monkeypatch.setattr("institution_lab.runner.preflight", no_models)
    monkeypatch.setattr(runner, "start_worker", no_models)
    monkeypatch.setattr(runner, "loop", controlled_loop)
    monkeypatch.setattr(runner, "evaluate", controlled_evaluation)
    monkeypatch.setattr(runner.network, "start", lambda: None)
    monkeypatch.setattr(runner.network, "close", lambda: closed.append(True))
    if infrastructure_failure == "unexpected":
        with pytest.raises(RuntimeError, match="unexpected verifier failure"):
            await runner.run()
        assert closed == [True]
        assert runner.store.state()["status"] == "failed"
        return
    result = await runner.run()
    assert closed == [True]
    assert result["status"] == runner.store.state()["status"] == "failed"
    assert result["stop_reason"] == (
        "final_evaluation_infrastructure_failure"
        if infrastructure_failure
        else "final_checks_failed"
    )
    with runner.store.connect() as connection:
        events = connection.execute(
            "SELECT kind FROM events WHERE kind IN ('final_evaluation','run_end') ORDER BY seq"
        ).fetchall()
    assert [row["kind"] for row in events] == ["final_evaluation", "run_end"]
