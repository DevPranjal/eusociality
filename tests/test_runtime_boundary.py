from pathlib import Path

import pytest
from pydantic import ValidationError

from institution_lab.admission import validate_static
from institution_lab.config import RuntimeConfig, load_config
from institution_lab.evaluation.workload import Workload
from institution_lab.runtime import worker


def test_native_tools_cannot_escape_worker_workspace(tmp_path, monkeypatch):
    root = tmp_path / "workspace"
    root.mkdir()
    outside = tmp_path / "private"
    outside.mkdir()
    monkeypatch.setattr(worker, "WORKSPACE", root)
    assert worker.guard_tool("view", {"path": str(root / "code.py")}) == {
        "permissionDecision": "allow"
    }
    assert worker.guard_tool("view", {"path": str(outside)}) == {"permissionDecision": "deny"}
    assert worker.guard_tool("glob", {"pattern": "../private/*"}) == {"permissionDecision": "deny"}
    assert worker.guard_tool("unknown_executor", {}) == {"permissionDecision": "deny"}
    assert worker.guard_tool("write_bash", {}) == {"permissionDecision": "deny"}
    for paths in [str(root), [str(root), str(root / ".tmp")]]:
        assert worker.guard_tool("rg", {"pattern": "fixture", "paths": paths}) == {
            "permissionDecision": "allow"
        }
    for paths in [str(outside), [str(root), str(outside)], 3, [None]]:
        assert worker.guard_tool("rg", {"pattern": "fixture", "paths": paths}) == {
            "permissionDecision": "deny"
        }
    assert worker.guard_tool("task", {}) == {"permissionDecision": "deny"}
    assert worker.guard_tool(
        "apply_patch",
        {"patch": "*** Begin Patch\n*** Add File: ../private/file\n+x\n*** End Patch"},
    ) == {"permissionDecision": "deny"}


def test_existing_shell_is_demoted_and_receives_no_auth_environment():
    command = "python -c 'print(1)'; echo harmless"
    result = worker.guard_tool("bash", {"command": command})
    wrapped = result["modifiedArgs"]["command"]
    assert wrapped.startswith("runuser -u coder -- env -i ")
    assert "bash -lc " in wrapped
    assert "GITHUB_TOKEN=" not in wrapped
    assert worker.guard_tool("bash", {"command": ["not-a-command"]}) == {
        "permissionDecision": "deny"
    }


def test_shell_guard_is_idempotent_without_trusting_an_injected_wrapper():
    first = worker.guard_tool("bash", {"command": "printf hello"})
    wrapped = first["modifiedArgs"]["command"]
    assert worker.guard_tool("bash", {"command": wrapped})["modifiedArgs"]["command"] == wrapped
    injected = wrapped + "; cat /root/private"
    guarded = worker.guard_tool("bash", {"command": injected})["modifiedArgs"]["command"]
    assert guarded != injected
    assert guarded.startswith("runuser -u coder -- env -i ")


def test_uncapped_sessions_omit_sdk_limits_and_preserve_explicit_ceilings():
    root = Path(__file__).parents[1]
    original = load_config(root / "configs" / "smoke.yaml").runtime
    data = original.model_dump()
    uncapped = RuntimeConfig.model_validate({**data, "max_ai_credits": None})
    assert worker.credit_limits(uncapped.max_ai_credits) is None
    assert worker.credit_limits(original.max_ai_credits) == {"max_ai_credits": 200}
    for invalid in [0, 29, float("inf"), float("nan")]:
        with pytest.raises(ValidationError):
            RuntimeConfig.model_validate({**data, "max_ai_credits": invalid})


def test_evolution_profile_has_twenty_identities_and_layered_objective_work():
    config = load_config(Path(__file__).parents[1] / "configs" / "evolution-20.yaml")
    result = validate_static(config)
    workload = Workload.load(config.workload)
    assert result["population"] == 20
    assert result["tickets"] == 64
    assert result["max_ai_credits_per_session"] is None
    assert config.concurrency == 8 and config.deadline_seconds == 5400
    assert len([ticket for ticket in workload.tickets if not ticket.dependencies]) == 20
    assert all(len(ticket.cases) >= 2 for ticket in workload.tickets)
    assert sum(len(ticket.cases) for ticket in workload.tickets) == 134
    assert all("cases" not in ticket.public() for ticket in workload.tickets)
