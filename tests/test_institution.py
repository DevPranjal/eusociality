import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from institution_lab.config import load_config
from institution_lab.institution.rules import Rule
from institution_lab.institution.service import Institution, Rejected
from institution_lab.store import Store

CONFIG = Path(__file__).parents[1] / "configs" / "smoke.yaml"


def company(tmp_path, mode="hard", rules=True):
    config = load_config(CONFIG).model_copy(update={"enforcement": mode})
    if not rules:
        config = config.model_copy(update={"rules": []})
    store = Store(tmp_path / "events.sqlite")
    institution = Institution(config, store)
    institution.initialize(
        [
            {"id": "a", "scope": "foundation", "dependencies": [], "description": "First"},
            {"id": "b", "scope": "feature", "dependencies": ["a"], "description": "Second"},
            {"id": "c", "scope": "foundation", "dependencies": [], "description": "Third"},
        ],
        "base",
    )
    return institution


def act(company, actor, action, payload, request_id=None):
    return company.act(actor, action, payload, request_id or uuid.uuid4().hex)


def test_claim_race_idempotency_and_voluntary_transfer(tmp_path):
    institution = company(tmp_path)
    with ThreadPoolExecutor(2) as pool:
        results = list(
            pool.map(
                lambda actor: act(institution, actor, "claim", {"ticket": "a"}),
                ["worker-00", "worker-01"],
            )
        )
    assert sum(result["ok"] for result in results) == 1
    owner = institution.store.state()["tickets"]["a"]["owner"]
    other = "worker-01" if owner == "worker-00" else "worker-00"
    key = uuid.uuid4().hex
    first = act(institution, owner, "message", {"content": "hello"}, key)
    assert act(institution, owner, "message", {"content": "hello"}, key) == first
    assert len(institution.store.state()["messages"]) == 1
    assert not act(institution, owner, "message", {"content": "changed"}, key)["ok"]
    assert not act(institution, owner, "offer", {"ticket": "a", "to": other})["ok"]
    minimal = company(tmp_path / "minimal", rules=False)
    assert act(minimal, owner, "claim", {"ticket": "a"})["ok"]
    assert act(minimal, owner, "offer", {"ticket": "a", "to": other})["ok"]
    assert minimal.store.state()["tickets"]["a"]["owner"] == owner
    assert act(minimal, other, "refuse", {"ticket": "a"})["ok"]
    assert act(minimal, owner, "offer", {"ticket": "a", "to": other})["ok"]
    assert act(minimal, owner, "cancel", {"ticket": "a"})["ok"]
    assert minimal.store.state()["tickets"]["a"]["owner"] == owner
    assert act(minimal, owner, "offer", {"ticket": "a", "to": other})["ok"]
    assert act(minimal, other, "accept", {"ticket": "a"})["ok"]
    assert minimal.store.state()["tickets"]["a"]["owner"] == other


def test_exact_proposal_review_credit_and_configured_promotion(tmp_path):
    institution = company(tmp_path)
    assert act(institution, "worker-00", "claim", {"ticket": "a"})["ok"]
    proposal = institution.register_proposal(
        "worker-00",
        "a",
        "base",
        "digest",
        "diff",
        {"passed": True, "checks": []},
        "artifact",
    )["proposal"]
    assert not institution.integration_decision("worker-00", proposal["id"])["ok"]
    assert not act(
        institution,
        "worker-00",
        "review",
        {
            "proposal": proposal["id"],
            "approve": True,
            "comment": "self",
        },
    )["ok"]
    assert act(
        institution,
        "worker-01",
        "review",
        {
            "proposal": proposal["id"],
            "approve": True,
            "comment": "checked exact diff",
        },
    )["ok"]
    assert institution.integration_decision("worker-00", proposal["id"])["ok"]
    assert institution.reserve_integration("worker-00", proposal["id"], "reserve")["ok"]
    institution.record_integration(proposal["id"], "next", {"passed": True})
    institution.record_integration(proposal["id"], "next", {"passed": True})
    worker = institution.store.state()["agents"]["worker-00"]
    assert {key: worker[key] for key in ("role", "score", "verified", "level")} == {
        "role": "worker",
        "score": 1,
        "verified": 1,
        "level": "L1",
    }
    assert act(institution, "worker-00", "claim", {"ticket": "b"})["ok"]


def test_advisory_and_disabled_rules_do_not_fake_enforcement(tmp_path):
    advisory = company(tmp_path / "advisory", mode="advisory")
    act(advisory, "worker-00", "claim", {"ticket": "a"})
    result = act(advisory, "worker-00", "offer", {"ticket": "a", "to": "worker-01"})
    assert result["ok"] and result["advisory_violation"]
    minimal = company(tmp_path / "minimal", rules=False)
    act(minimal, "worker-00", "claim", {"ticket": "a"})
    proposal = minimal.register_proposal(
        "worker-00", "a", "base", "digest", "", {"passed": False}, "artifact"
    )["proposal"]
    assert minimal.integration_decision("worker-00", proposal["id"])["ok"]
    assert minimal.reserve_integration("worker-00", proposal["id"], "reserve")["ok"]
    minimal.record_integration(proposal["id"], "next", {"passed": False})
    assert minimal.store.state()["agents"]["worker-00"]["score"] == 0
    assert minimal.store.state()["tickets"]["a"]["status"] != "complete"


def test_stale_ownership_cannot_integrate_and_private_inbox_is_scoped(tmp_path):
    institution = company(tmp_path)
    act(institution, "worker-00", "claim", {"ticket": "a"})
    proposal = institution.register_proposal(
        "worker-00", "a", "base", "digest", "", {"passed": True}, "artifact"
    )["proposal"]
    act(institution, "worker-00", "message", {"content": "private", "to": "worker-00"})
    assert not act(institution, "worker-01", "inbox", {})["messages"]
    assert act(institution, "worker-00", "inbox", {})["messages"][0]["content"] == "private"
    institution.worker_unavailable("worker-00")
    assert institution.store.state()["tickets"]["a"]["generation"] == 2
    with pytest.raises(Rejected, match="worker_unavailable"):
        institution.integration_decision("worker-00", proposal["id"])


def test_invalid_plugin_cannot_mutate_state_and_commit_an_action(tmp_path):
    class MutatingRule(Rule):
        def decide(self, action, actor, payload, state):
            state["agents"][actor]["score"] = 100
            return []

    institution = company(tmp_path)
    institution.engine.rules.append(MutatingRule())
    with pytest.raises(ValueError, match="rule_mutated"):
        act(institution, "worker-00", "claim", {"ticket": "a"})
    assert institution.store.state()["tickets"]["a"]["owner"] is None
    assert institution.store.state()["agents"]["worker-00"]["score"] == 0


def test_own_acknowledged_writes_do_not_trigger_spurious_wakeups(tmp_path):
    from institution_lab.runner import unseen_updates

    institution = company(tmp_path)
    act(institution, "worker-00", "message", {"content": "My acknowledged update."})
    state = institution.store.state()
    assert not unseen_updates("worker-00", 0, state)
    assert unseen_updates("worker-01", 0, state)
    act(institution, "worker-01", "message", {"content": "An actual peer update."})
    assert unseen_updates("worker-00", 0, institution.store.state())


def test_model_inventory_is_configurable_without_changing_institutions():
    from pydantic import ValidationError

    from institution_lab.admission import validate_static
    from institution_lab.config import RunConfig

    original = load_config(CONFIG)
    document = original.model_dump(mode="json")
    document["model_overrides"] = {
        "worker-01": {"model": "gpt-6.1-terra", "reasoning_effort": "medium"}
    }
    configured = RunConfig.model_validate(document)
    assert configured.runtime_for("worker-00").model == original.runtime.model
    assert configured.runtime_for("worker-01").model == "gpt-6.1-terra"
    assert validate_static(configured)["valid"]
    document["model_overrides"]["unknown-worker"] = document["model_overrides"]["worker-01"]
    with pytest.raises(ValidationError, match="unknown_agent"):
        RunConfig.model_validate(document)
    invalid = original.model_dump(mode="json")
    invalid["rules"].append({"plugin": "unimplemented-mechanism"})
    with pytest.raises(ValueError, match="unknown_rule"):
        validate_static(RunConfig.model_validate(invalid))
