import uuid
from pathlib import Path

import pytest
from pydantic import ValidationError

from institution_lab.admission import validate_static
from institution_lab.config import RunConfig, load_config
from institution_lab.institution.service import Institution
from institution_lab.institution.society import GoalSubmission, TaskDefinition
from institution_lab.store import Store

ROOT = Path(__file__).parents[1]


def society(tmp_path):
    config = load_config(ROOT / "configs" / "society.yaml").model_copy(
        update={"goal": "Controlled fixture goal"},
    )
    service = Institution(config, Store(tmp_path / "events.sqlite"))
    service.initialize([], "base")
    return service


def act(service, actor, action, payload, identifier=None):
    return service.act(actor, action, payload, identifier or uuid.uuid4().hex)


def task(identifier, **changes):
    return {
        "id": identifier,
        "description": "Controlled task fixture",
        "scope": "foundation",
        "dependencies": [],
        "files": [f"project/{identifier}.md"],
        "acceptance": ["A peer verifies that the goal is addressed"],
        "checks": [],
        **changes,
    }


def deliver(service, identifier, actor="worker-00", reviewer="worker-01"):
    assert act(service, actor, "claim", {"ticket": identifier})["ok"]
    proposal = service.register_proposal(
        actor,
        identifier,
        service.store.state()["head"],
        "hash",
        "diff",
        {"passed": True},
        "controlled-artifact",
    )["proposal"]
    assert act(
        service,
        reviewer,
        "review",
        {
            "proposal": proposal["id"],
            "approve": True,
            "comment": "Controlled peer review",
        },
    )["ok"]
    assert service.reserve_integration(actor, proposal["id"], uuid.uuid4().hex)["ok"]
    service.record_integration(proposal["id"], identifier + "-head", {"passed": True})


def test_completed_outputs_can_be_corrected_only_by_reviewed_revision_lineage(tmp_path):
    service = society(tmp_path)
    original = task("original", checks=[{"argv": ["python", "original_check.py"]}])
    assert act(service, "worker-00", "propose_task", original)["ok"]
    assert not act(
        service,
        "worker-01",
        "propose_task",
        task(
            "early",
            files=original["files"],
            revision_of="original",
            dependencies=["original"],
        ),
    )["ok"]
    deliver(service, "original")
    retained = service.store.state()["tickets"]["original"].copy()
    assert not act(
        service,
        "worker-01",
        "propose_task",
        task(
            "missing",
            files=original["files"],
            revision_of="unknown",
        ),
    )["ok"]
    assert not act(
        service,
        "worker-01",
        "propose_task",
        task(
            "no_dependency",
            files=original["files"],
            revision_of="original",
        ),
    )["ok"]
    correction = task(
        "correction",
        files=original["files"],
        revision_of="original",
        dependencies=["original"],
        acceptance=["Correct the controlled defect"],
    )
    assert act(service, "worker-00", "propose_task", correction)["ok"]
    assert not act(
        service,
        "worker-01",
        "propose_task",
        {
            **correction,
            "id": "parallel",
        },
    )["ok"]
    deliver(service, "correction")
    assert service.store.state()["tickets"]["original"] == retained
    assert not act(
        service,
        "worker-01",
        "propose_task",
        {
            **correction,
            "id": "stale",
        },
    )["ok"]
    assert act(
        service,
        "worker-00",
        "propose_task",
        {
            **correction,
            "id": "next_correction",
            "revision_of": "correction",
            "dependencies": ["correction"],
        },
    )["ok"]


def test_agents_create_revise_and_withdraw_real_work_without_hidden_allocation(tmp_path):
    service = society(tmp_path)
    assert service.store.state()["tickets"] == {}
    assert validate_static(service.config)["task_creation"] == "agents_during_execution"
    key = uuid.uuid4().hex
    first = act(service, "worker-00", "propose_task", task("a"), key)
    assert first["ok"] and first["ticket"]["owner"] is None
    assert act(service, "worker-00", "propose_task", task("a"), key) == first
    assert not act(service, "worker-01", "propose_task", task("a"))["ok"]
    assert not act(
        service,
        "worker-01",
        "propose_task",
        task(
            "b",
            files=["project/a.md"],
        ),
    )["ok"]
    assert not act(
        service,
        "worker-01",
        "propose_task",
        task(
            "b",
            dependencies=["missing"],
        ),
    )["ok"]
    assert act(service, "worker-01", "propose_task", task("b", dependencies=["a"]))["ok"]
    assert not act(service, "worker-00", "withdraw_task", {"ticket": "a", "reason": "fixture"})[
        "ok"
    ]
    assert not act(
        service,
        "worker-00",
        "revise_task",
        {
            **task("a", dependencies=["b"]),
            "reason": "Controlled cycle",
        },
    )["ok"]
    assert act(service, "worker-00", "claim", {"ticket": "a"})["ok"]
    assert not act(
        service,
        "worker-01",
        "revise_task",
        {
            **task("a"),
            "reason": "Wrong actor",
        },
    )["ok"]
    assert act(
        service,
        "worker-00",
        "revise_task",
        {
            **task("a", acceptance=["Updated criterion"]),
            "reason": "Clarification",
        },
    )["ok"]
    assert service.store.state()["tickets"]["a"]["generation"] == 2
    assert act(
        service,
        "worker-01",
        "withdraw_task",
        {
            "ticket": "b",
            "reason": "No longer needed",
        },
    )["ok"]
    assert act(
        service,
        "worker-00",
        "withdraw_task",
        {
            "ticket": "a",
            "reason": "Remove obsolete work",
        },
    )["ok"]
    assert service.store.state()["tickets"]["a"]["status"] == "withdrawn"
    for file in ["../private.md", "project/../private.md", "project/.env", "/project/a.md"]:
        with pytest.raises(ValidationError):
            TaskDefinition.model_validate(task("invalid", files=[file]))


def test_goal_requires_delivered_work_fresh_evidence_and_independent_agent_agreement(tmp_path):
    service = society(tmp_path)
    with pytest.raises(ValidationError):
        GoalSubmission(evidence=[" "])
    assert not act(service, "worker-00", "propose_completion", {"evidence": ["Nothing"]})["ok"]
    act(service, "worker-00", "propose_task", task("a"))
    act(service, "worker-00", "claim", {"ticket": "a"})
    assert not act(service, "worker-00", "propose_completion", {"evidence": ["Unfinished"]})["ok"]
    proposal = service.register_proposal(
        "worker-00",
        "a",
        "base",
        "hash",
        "diff",
        {"passed": True},
        "artifact",
    )["proposal"]
    act(
        service,
        "worker-01",
        "review",
        {
            "proposal": proposal["id"],
            "approve": True,
            "comment": "Controlled independent review",
        },
    )
    assert service.reserve_integration("worker-00", proposal["id"], "reserve")["ok"]
    service.record_integration(proposal["id"], "next", {"passed": True})
    assert service.store.state()["goal_status"] == "open"
    completion = act(
        service,
        "worker-00",
        "propose_completion",
        {
            "evidence": ["Delivered artifact a addresses goal"],
        },
    )["completion"]
    assert not act(service, "worker-00", "finish_goal", {"completion": completion["id"]})["ok"]
    assert not act(
        service,
        "worker-00",
        "review_completion",
        {
            "completion": completion["id"],
            "approve": True,
            "comment": "Self review",
        },
    )["ok"]
    act(
        service,
        "worker-01",
        "review_completion",
        {
            "completion": completion["id"],
            "approve": False,
            "comment": "More work needed",
        },
    )
    assert not act(service, "worker-00", "finish_goal", {"completion": completion["id"]})["ok"]
    act(
        service,
        "worker-01",
        "review_completion",
        {
            "completion": completion["id"],
            "approve": True,
            "comment": "Evidence now accepted",
        },
    )
    assert act(service, "worker-00", "finish_goal", {"completion": completion["id"]})["ok"]
    assert service.store.state()["goal_status"] == "completed"
    assert not act(service, "worker-00", "propose_task", task("after"))["ok"]


def test_new_work_invalidates_old_goal_attestation_and_starting_levels_do_not_fabricate_credit(
    tmp_path,
):
    service = society(tmp_path)
    act(service, "worker-00", "propose_task", task("a"))
    service.store.update(lambda state: state["tickets"]["a"].update(status="complete"))
    completion = act(service, "worker-00", "propose_completion", {"evidence": ["Fixture"]})[
        "completion"
    ]
    act(service, "worker-01", "propose_task", task("b"))
    assert not act(
        service,
        "worker-01",
        "review_completion",
        {
            "completion": completion["id"],
            "approve": True,
            "comment": "Stale",
        },
    )["ok"]
    base = service.config.model_dump(mode="json")
    config = RunConfig.model_validate({**base, "initial_levels": {"worker-00": "L2"}})
    other = Institution(config, Store(tmp_path / "other.sqlite"))
    other.initialize([], "base")
    state = other.store.state()
    assert state["agents"]["worker-00"]["level"] == "L2"
    assert state["agents"]["worker-00"]["verified"] == state["agents"]["worker-00"]["score"] == 0
    other.engine.completion("worker-00", state)
    assert state["agents"]["worker-00"]["level"] == "L2"


def test_central_mode_proposals_do_not_bypass_the_registered_allocator(tmp_path):
    config = load_config(ROOT / "configs" / "society.yaml").model_copy(
        update={
            "goal": "Controlled fixture",
            "coordination": "central",
            "population": 3,
        }
    )
    service = Institution(config, Store(tmp_path / "central.sqlite"))
    service.initialize([], "base")
    assert act(service, "worker-01", "propose_task", task("a"))["ok"]
    assert not act(service, "worker-01", "claim", {"ticket": "a"})["ok"]
    assert act(service, "worker-00", "offer", {"ticket": "a", "to": "worker-01"})["ok"]
    assert act(service, "worker-01", "accept", {"ticket": "a"})["ok"]
    assert service.store.state()["tickets"]["a"]["owner"] == "worker-01"


def test_revising_task_invalidates_pending_delivery_and_requires_a_new_review(tmp_path):
    service = society(tmp_path)
    act(service, "worker-00", "propose_task", task("a"))
    act(service, "worker-00", "claim", {"ticket": "a"})
    proposal = service.register_proposal(
        "worker-00",
        "a",
        "base",
        "hash",
        "diff",
        {"passed": True},
        "artifact",
    )["proposal"]
    act(
        service,
        "worker-01",
        "review",
        {
            "proposal": proposal["id"],
            "approve": True,
            "comment": "Previous criteria",
        },
    )
    assert act(
        service,
        "worker-00",
        "revise_task",
        {
            **task("a", acceptance=["Updated property"]),
            "reason": "Changed understanding",
        },
    )["ok"]
    assert service.store.state()["proposals"][proposal["id"]]["status"] == "superseded"
    from institution_lab.institution.service import Rejected

    with pytest.raises(Rejected, match="stale_ownership"):
        service.integration_decision("worker-00", proposal["id"])


def test_concurrent_completion_candidates_cannot_replace_the_accepted_goal(tmp_path):
    """Real transactions; controlled completed-work fixture, not an agent observation."""
    service = society(tmp_path)
    act(service, "worker-00", "propose_task", task("a"))
    service.store.update(lambda state: state["tickets"]["a"].update(status="complete"))
    first = act(service, "worker-00", "propose_completion", {"evidence": ["First assessment"]})[
        "completion"
    ]
    second = act(service, "worker-01", "propose_completion", {"evidence": ["Second assessment"]})[
        "completion"
    ]
    act(
        service,
        "worker-01",
        "review_completion",
        {
            "completion": first["id"],
            "approve": True,
            "comment": "Independent first review",
        },
    )
    act(
        service,
        "worker-00",
        "review_completion",
        {
            "completion": second["id"],
            "approve": True,
            "comment": "Independent second review",
        },
    )
    assert act(service, "worker-00", "finish_goal", {"completion": first["id"]})["ok"]
    assert not act(service, "worker-01", "finish_goal", {"completion": second["id"]})["ok"]
    assert service.store.state()["accepted_completion"] == first["id"]
