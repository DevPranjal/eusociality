from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Level(StrictModel):
    name: str
    completions: int = Field(ge=0)
    max_active: int = Field(ge=1)
    scopes: list[str]
    delegate: bool = False


class RuleSpec(StrictModel):
    plugin: str
    enabled: bool = True
    parameters: dict = Field(default_factory=dict)
    private_parameters: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def private_keys(self) -> RuleSpec:
        if not set(self.private_parameters) <= set(self.parameters):
            raise ValueError("private_rule_parameter_not_defined")
        return self

    def agent_description(self) -> dict:
        description = self.model_dump()
        description["parameters"] = {
            key: (
                {
                    "withheld": True,
                    "sha256": hashlib.sha256(
                        json.dumps(value, sort_keys=True).encode(),
                    ).hexdigest(),
                }
                if key in self.private_parameters
                else value
            )
            for key, value in self.parameters.items()
        }
        return description


class ModelChoice(StrictModel):
    model: str
    reasoning_effort: Literal["none", "low", "medium", "high", "xhigh", "max"]


class RuntimeConfig(ModelChoice):
    max_ai_credits: float | None = Field(ge=30, allow_inf_nan=False)
    turn_timeout_seconds: int = Field(ge=10, le=1800)
    worker_image: str
    proxy_image: str
    max_tool_output_bytes: int = Field(default=20480, ge=1024, le=1048576)
    evaluation_retries: int = Field(default=2, ge=0, le=3)


class ResearchConfig(StrictModel):
    enabled: bool = True
    allowed_hosts: list[str] = Field(default_factory=list)


class ResourcePool(StrictModel):
    capacity: int = Field(ge=1, strict=True)
    per_agent_limit: int = Field(ge=1, strict=True)
    accounting: Literal["global", "per_agent"] = "global"
    unit: str = Field(default="allocation_units", min_length=1)


class RunConfig(StrictModel):
    schema_version: Literal[1]
    name: str
    coordination: Literal["distributed", "central"]
    enforcement: Literal["hard", "advisory"]
    population: int = Field(ge=1, le=50)
    concurrency: int = Field(ge=1, le=30)
    deadline_seconds: int = Field(ge=30)
    max_activations: int = Field(ge=1)
    recoveries: int = Field(ge=0, le=3)
    integration_mode: Literal["strict_base", "disjoint_files"] = "strict_base"
    scheduler_mode: Literal["fair", "workflow"] = "fair"
    quiescent_rechecks: int = Field(default=2, ge=0, le=10)
    keep_awake: bool = True
    model_overrides: dict[str, ModelChoice] = Field(default_factory=dict)
    initial_levels: dict[str, str] = Field(default_factory=dict)
    agent_roles: dict[str, Literal["worker", "reviewer", "challenger"]] = Field(
        default_factory=dict,
    )
    agent_parameters: dict[str, dict] = Field(default_factory=dict)
    disclose_peer_roles: bool = True
    resources: dict[str, ResourcePool] = Field(default_factory=dict)
    goal: str | None = None
    prompt_repository: Path
    workload: Path | None = None
    project_inputs: Path | None = None
    outcome_suite: Path | None = None
    research: ResearchConfig = Field(default_factory=ResearchConfig)
    runtime: RuntimeConfig
    levels: list[Level]
    rules: list[RuleSpec]

    @model_validator(mode="after")
    def coherent(self) -> RunConfig:
        if self.concurrency > self.population:
            raise ValueError("concurrency_exceeds_population")
        known_agents = {f"worker-{index:02}" for index in range(self.population)}
        if not set(self.model_overrides) <= known_agents:
            raise ValueError("model_override_for_unknown_agent")
        if not set(self.initial_levels) <= known_agents:
            raise ValueError("initial_level_for_unknown_agent")
        if (
            not set(self.agent_roles) <= known_agents
            or not set(self.agent_parameters) <= known_agents
        ):
            raise ValueError("role_configuration_for_unknown_agent")
        if self.coordination == "central" and "worker-00" in self.agent_roles:
            raise ValueError("central_allocator_role_cannot_be_overridden")
        if not self.levels or self.levels[0].completions != 0:
            raise ValueError("initial_level_must_start_at_zero")
        thresholds = [level.completions for level in self.levels]
        if thresholds != sorted(set(thresholds)):
            raise ValueError("promotion_thresholds_must_increase")
        level_names = [level.name for level in self.levels]
        if len(level_names) != len(set(level_names)):
            raise ValueError("duplicate_level_name")
        if not set(self.initial_levels.values()) <= set(level_names):
            raise ValueError("unknown_initial_level")
        if self.goal is not None and not self.goal.strip():
            raise ValueError("goal_must_not_be_blank")
        names = [rule.plugin for rule in self.rules]
        if len(names) != len(set(names)):
            raise ValueError("duplicate_rule_plugin")
        enabled = {rule.plugin for rule in self.rules if rule.enabled}
        if self.outcome_suite and "output_audit" not in enabled:
            raise ValueError("outcome_suite_requires_output_audit_rule")
        if "output_audit" in enabled and not self.outcome_suite:
            raise ValueError("output_audit_requires_outcome_suite")
        if self.resources and "resource_ledger" not in enabled:
            raise ValueError("resource_pools_require_resource_ledger")
        if self.resources and self.enforcement != "hard":
            raise ValueError("resource_accounting_requires_hard_enforcement")
        if "promotion" in enabled and "performance" not in enabled:
            raise ValueError("promotion_requires_performance")
        if self.coordination == "central" and "review_gate" in enabled and self.population < 3:
            raise ValueError("independent_review_requires_two_coding_workers")
        return self

    def runtime_for(self, actor: str) -> RuntimeConfig:
        override = self.model_overrides.get(actor)
        return self.runtime.model_copy(update=override.model_dump()) if override else self.runtime

    def role_for(self, actor: str) -> str:
        return (
            "coordinator"
            if self.coordination == "central" and actor == "worker-00"
            else self.agent_roles.get(actor, "worker")
        )

    def starting_level(self, actor: str) -> Level:
        name = self.initial_levels.get(actor, self.levels[0].name)
        return next(level for level in self.levels if level.name == name)

    def fingerprint(self) -> str:
        raw = json.dumps(self.model_dump(mode="json"), sort_keys=True).encode()
        return hashlib.sha256(raw).hexdigest()


def load_config(path: Path) -> RunConfig:
    def merge(base: dict, changes: dict) -> dict:
        result = dict(base)
        for key, value in changes.items():
            result[key] = (
                merge(result[key], value)
                if isinstance(value, dict) and isinstance(result.get(key), dict)
                else value
            )
        return result

    def read(current: Path, seen: set[Path]) -> dict:
        current = current.resolve()
        if current in seen:
            raise ValueError("configuration_inheritance_cycle")
        document = yaml.safe_load(current.read_text(encoding="utf-8"))
        if not isinstance(document, dict):
            raise ValueError("configuration_must_be_a_mapping")
        parent = document.pop("extends", None)
        for key in ("prompt_repository", "workload", "project_inputs", "outcome_suite"):
            if key in document and document[key] is not None:
                document[key] = str((current.parent / document[key]).resolve())
        if parent:
            return merge(read(current.parent / parent, seen | {current}), document)
        return document

    config = RunConfig.model_validate(read(path, set()))
    root = path.resolve().parent
    return config.model_copy(
        update={
            "prompt_repository": (root / config.prompt_repository).resolve(),
            "workload": (root / config.workload).resolve() if config.workload else None,
            "project_inputs": (root / config.project_inputs).resolve()
            if config.project_inputs
            else None,
            "outcome_suite": (root / config.outcome_suite).resolve()
            if config.outcome_suite
            else None,
        }
    )
