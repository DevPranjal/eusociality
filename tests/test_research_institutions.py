import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from jinja2 import Environment, FileSystemLoader, StrictUndefined
from pydantic import ValidationError

from institution_lab.admission import validate_static
from institution_lab.config import RunConfig, load_config
from institution_lab.institution.rules import CommunicationTopology
from institution_lab.institution.service import Institution
from institution_lab.prompts import render_system
from institution_lab.runtime.worker import TOOL_SETS
from institution_lab.store import Store

ROOT = Path(__file__).parents[1]


def service(tmp_path, **changes):
    original = load_config(ROOT / "configs" / "society.yaml").model_dump(mode="json")
    config = RunConfig.model_validate(
        {
            **original,
            "goal": "Controlled benign institutional fixture",
            **changes,
        }
    )
    institution = Institution(config, Store(tmp_path / "events.sqlite"))
    institution.initialize([], "controlled-base")
    return institution


def act(institution, actor, action, payload, key=None):
    return institution.act(actor, action, payload, key or uuid.uuid4().hex)


@pytest.mark.parametrize("accounting,expected_total", [("per_agent", 12), ("global", 8)])
def test_local_limits_do_not_imply_aggregate_limits_and_global_rule_is_atomic(
    tmp_path,
    accounting,
    expected_total,
):
    institution = service(
        tmp_path,
        population=3,
        rules=[{"plugin": "resource_ledger"}],
        resources={
            "pool": {
                "capacity": 10,
                "per_agent_limit": 6,
                "accounting": accounting,
            }
        },
    )

    def reserve(index):
        return act(
            institution,
            f"worker-{index:02}",
            "reserve_resource",
            {
                "resource": "pool",
                "units": 4,
            },
        )

    with ThreadPoolExecutor(max_workers=3) as workers:
        outcomes = list(workers.map(reserve, range(3)))
    assert sum(outcome["ok"] for outcome in outcomes) == expected_total // 4
    state = institution.store.state()
    assert state["resources"]["pool"]["used"] == expected_total
    assert len(state["resource_reservations"]) == expected_total // 4
    successful = next(reservation for reservation in state["resource_reservations"])
    key = uuid.uuid4().hex
    request = {"resource": "pool", "units": 1}
    first = act(institution, successful["actor"], "reserve_resource", request, key)
    assert act(institution, successful["actor"], "reserve_resource", request, key) == first
    assert not act(
        institution,
        successful["actor"],
        "reserve_resource",
        {
            "resource": "pool",
            "units": 4,
        },
    )["ok"]
    with pytest.raises(ValidationError):
        institution.validate("reserve_resource", {"resource": "pool", "units": True})
    assert not act(
        institution,
        "worker-00",
        "reserve_resource",
        {
            "resource": "missing",
            "units": 1,
        },
    )["ok"]


def test_one_agent_baseline_and_registered_roles_preserve_identity_authority(tmp_path):
    single = service(tmp_path / "single", population=1, concurrency=1, rules=[])
    assert validate_static(single.config)["population"] == 1
    roles = service(
        tmp_path / "roles",
        population=3,
        agent_roles={"worker-00": "reviewer", "worker-02": "challenger"},
        agent_parameters={"worker-02": {"canary": "EUSOC_CANARY_CONTROLLED"}},
    )
    assert validate_static(roles.config)["valid"]
    task = {
        "id": "page",
        "description": "Controlled fixture",
        "scope": "foundation",
        "files": ["project/page.md"],
        "acceptance": ["Controlled peer criterion"],
    }
    assert act(roles, "worker-01", "propose_task", task)["ok"]
    assert not act(roles, "worker-00", "claim", {"ticket": "page"})["ok"]
    assert not act(roles, "worker-02", "propose_task", {**task, "id": "other"})["ok"]
    assert not act(
        roles,
        "worker-02",
        "review",
        {
            "proposal": "missing",
            "approve": True,
            "comment": "No voting authority",
        },
    )["ok"]
    assert act(
        roles,
        "worker-02",
        "message",
        {
            "content": "A harmless peer message",
            "to": "worker-01",
        },
    )["ok"]
    assert roles.store.state()["messages"][-1]["from"] == "worker-02"
    with pytest.raises(ValidationError):
        roles.validate("message", {"content": "Cannot spoof", "from": "worker-00"})
    assert TOOL_SETS["challenger"] == {"organization"}
    assert {"view", "rg", "organization"} <= TOOL_SETS["reviewer"]
    assert not {"create", "edit", "apply_patch"} & TOOL_SETS["reviewer"]


@pytest.mark.parametrize("shape", ["complete", "ring", "line", "star"])
def test_topology_enforces_only_declared_peer_routes(tmp_path, shape):
    institution = service(
        tmp_path,
        population=5,
        rules=[
            {
                "plugin": "communication_topology",
                "parameters": {
                    "shape": shape,
                    "hub": "worker-01",
                    "public_messages": False,
                },
            }
        ],
    )
    topology = next(
        rule for rule in institution.engine.rules if isinstance(rule, CommunicationTopology)
    )
    for actor in institution.store.state()["agents"]:
        contacts = act(institution, actor, "board", {})["contacts"]
        assert contacts == topology.contacts(actor)
        for recipient in institution.store.state()["agents"]:
            if recipient == actor:
                continue
            result = act(institution, actor, "message", {"content": "Controlled", "to": recipient})
            assert result["ok"] == (recipient in contacts)
        assert not act(institution, actor, "message", {"content": "No broadcast"})["ok"]


def test_publication_policy_blocks_marker_even_without_peer_review_gate(tmp_path):
    marker = "EUSOC_CANARY_CONTROLLED"
    institution = service(
        tmp_path,
        rules=[
            {
                "plugin": "publication_policy",
                "parameters": {
                    "forbidden_literals": [marker],
                },
            }
        ],
    )
    source = tmp_path / "artifact"
    (source / "project").mkdir(parents=True)
    (source / "project" / "page.md").write_text(marker, newline="")
    checks = institution.engine.artifact_checks(source)
    assert len(checks) == 1 and not checks[0].allowed
    assert checks[0].evidence["matches"][0]["path"] == "project/page.md"
    assert act(
        institution,
        "worker-00",
        "message",
        {
            "content": marker,
            "to": "worker-01",
        },
    )["ok"]
    assert not act(institution, "worker-00", "message", {"content": marker})["ok"]
    task = {
        "id": "page",
        "description": "Controlled",
        "scope": "foundation",
        "files": ["project/page.md"],
        "acceptance": ["Controlled"],
    }
    assert act(institution, "worker-00", "propose_task", task)["ok"]
    assert act(institution, "worker-00", "claim", {"ticket": "page"})["ok"]
    proposal = institution.register_proposal(
        "worker-00",
        "page",
        "controlled-base",
        "hash",
        "diff",
        {
            "passed": False,
            "policy_checks": [
                {
                    "rule": "publication_policy",
                    "allowed": False,
                }
            ],
        },
        "artifact",
    )["proposal"]
    assert not institution.reserve_integration(
        "worker-00",
        proposal["id"],
        uuid.uuid4().hex,
    )["ok"]
    assert institution.store.state()["head"] == "controlled-base"
    (source / "project" / "page.md").write_text("No marker here", newline="")
    assert institution.engine.artifact_checks(source)[0].allowed


def test_hidden_policy_values_do_not_preexpose_honest_agents_to_the_canary(tmp_path):
    marker = "EUSOC_CANARY_CONTROLLED_ONLY_SEED_KNOWS"
    institution = service(
        tmp_path,
        population=3,
        agent_roles={"worker-02": "challenger"},
        agent_parameters={"worker-02": {"canary": marker}},
        rules=[
            {
                "plugin": "publication_policy",
                "parameters": {"forbidden_literals": [marker]},
                "private_parameters": ["forbidden_literals"],
            }
        ],
    )
    templates = Environment(
        loader=FileSystemLoader(institution.config.prompt_repository),
        undefined=StrictUndefined,
    )
    honest = render_system(templates, institution.config, None, "worker-01", "worker")
    challenger = render_system(templates, institution.config, None, "worker-02", "challenger")
    assert marker not in honest and marker in challenger
    assert "withheld" in honest
    assert institution.config.rules[0].parameters["forbidden_literals"] == [marker]


def test_registered_reviewer_can_formally_review_but_cannot_claim_work(tmp_path):
    institution = service(
        tmp_path,
        population=3,
        rules=[],
        agent_roles={"worker-02": "reviewer"},
    )
    task = {
        "id": "page",
        "description": "Controlled reviewer contract",
        "scope": "foundation",
        "files": ["project/page.md"],
        "acceptance": ["Independent reviewer checks evidence"],
    }
    assert act(institution, "worker-00", "propose_task", task)["ok"]
    assert act(institution, "worker-00", "claim", {"ticket": "page"})["ok"]
    proposal = institution.register_proposal(
        "worker-00",
        "page",
        "controlled-base",
        "digest",
        "diff",
        {"passed": True},
        "controlled-artifact",
    )["proposal"]
    outcome = act(
        institution,
        "worker-02",
        "review",
        {
            "proposal": proposal["id"],
            "approve": True,
            "comment": "Controlled actual peer review",
        },
    )
    assert outcome["ok"], outcome
    assert not act(institution, "worker-02", "claim", {"ticket": "page"})["ok"]
