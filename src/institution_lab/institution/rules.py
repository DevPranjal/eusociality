from __future__ import annotations

import fnmatch
import hashlib
import inspect
from copy import deepcopy
from dataclasses import dataclass
from importlib import import_module
from pathlib import Path
from typing import Literal

from pydantic import Field

from ..config import RunConfig, StrictModel


@dataclass(frozen=True)
class Decision:
    rule: str
    allowed: bool
    reason: str
    evidence: dict


@dataclass(frozen=True)
class Effect:
    rule: str
    field: Literal["score", "level"]
    value: int | str


class Rule:
    def decide(self, action: str, actor: str, payload: dict, state: dict) -> list[Decision]:
        return []

    def completion(self, actor: str, state: dict) -> list[Effect]:
        return []

    def contacts(self, actor: str) -> list[str] | None:
        return None

    def artifact_checks(self, source: Path) -> list[Decision]:
        return []


class OutputAudit(Rule):
    def __init__(self, config: RunConfig, parameters: dict) -> None:
        if parameters:
            raise ValueError("output_audit_uses_configured_outcome_suite")
        if config.outcome_suite is None:
            raise ValueError("output_audit_requires_outcome_suite")


class Capabilities(Rule):
    def __init__(self, config: RunConfig, parameters: dict) -> None:
        if parameters:
            raise ValueError("capabilities_accepts_no_parameters")
        self.config = config

    def decide(self, action: str, actor: str, payload: dict, state: dict) -> list[Decision]:
        if action not in {"claim", "accept"}:
            return []
        recipient = actor
        ticket = state["tickets"][payload["ticket"]]
        worker = state["agents"][recipient]
        level = next(level for level in self.config.levels if level.name == worker["level"])
        held = sum(
            t["owner"] == recipient and t["status"] != "complete" for t in state["tickets"].values()
        )
        return [
            Decision(
                "capabilities",
                ticket["scope"] in level.scopes,
                "scope",
                {"level": level.name, "scope": ticket["scope"]},
            ),
            Decision(
                "capabilities",
                held < level.max_active,
                "active_ticket_allowance",
                {"held": held, "maximum": level.max_active},
            ),
        ]


class Delegation(Rule):
    def __init__(self, config: RunConfig, parameters: dict) -> None:
        if parameters:
            raise ValueError("delegation_accepts_no_parameters")
        self.config = config

    def decide(self, action: str, actor: str, payload: dict, state: dict) -> list[Decision]:
        if action != "offer" or state["agents"][actor]["role"] == "coordinator":
            return []
        level = next(
            level for level in self.config.levels if level.name == state["agents"][actor]["level"]
        )
        return [
            Decision("delegation", level.delegate, "delegation_authority", {"level": level.name})
        ]


class ReviewGateParameters(StrictModel):
    independent_approvals: int = Field(default=1, ge=1)
    veto_rejections: bool = False


class ReviewGate(Rule):
    def __init__(self, config: RunConfig, parameters: dict) -> None:
        self.parameters = ReviewGateParameters.model_validate(parameters)
        if self.parameters.independent_approvals >= config.population:
            raise ValueError("publication_approvals_exceed_independent_population")

    def decide(self, action: str, actor: str, payload: dict, state: dict) -> list[Decision]:
        if action != "integrate":
            return []
        proposal = state["proposals"][payload["proposal"]]
        approved = sum(
            review["approve"] and reviewer != proposal["actor"]
            for reviewer, review in proposal["reviews"].items()
        )
        return [
            Decision(
                "review_gate",
                approved >= self.parameters.independent_approvals,
                "independent_peer_approval",
                {"proposal": payload["proposal"]},
            ),
            Decision(
                "review_gate",
                proposal["evaluation"]["passed"],
                "protected_checks",
                {"evaluation": proposal["evaluation"]},
            ),
            *(
                [
                    Decision(
                        "review_gate",
                        not any(not review["approve"] for review in proposal["reviews"].values()),
                        "no_unresolved_publication_rejection",
                        {},
                    )
                ]
                if self.parameters.veto_rejections
                else []
            ),
        ]


class ResourceLedger(Rule):
    def __init__(self, config: RunConfig, parameters: dict) -> None:
        if parameters:
            raise ValueError("resource_ledger_uses_declared_pools")

    def decide(self, action: str, actor: str, payload: dict, state: dict) -> list[Decision]:
        if action != "reserve_resource":
            return []
        pool = state["resources"][payload["resource"]]
        amount = payload["units"]
        held = pool["by_actor"].get(actor, 0)
        decisions = [
            Decision(
                "resource_ledger",
                held + amount <= pool["per_agent_limit"],
                "per_identity_resource_limit",
                {"held": held, "requested": amount, "limit": pool["per_agent_limit"]},
            )
        ]
        if pool["accounting"] == "global":
            decisions.append(
                Decision(
                    "resource_ledger",
                    pool["used"] + amount <= pool["capacity"],
                    "aggregate_resource_limit",
                    {"used": pool["used"], "requested": amount, "capacity": pool["capacity"]},
                )
            )
        return decisions


class TopologyParameters(StrictModel):
    shape: Literal["complete", "ring", "line", "star"] = "complete"
    hub: str = "worker-00"
    public_messages: bool = True


class CommunicationTopology(Rule):
    def __init__(self, config: RunConfig, parameters: dict) -> None:
        self.parameters = TopologyParameters.model_validate(parameters)
        self.agents = [f"worker-{index:02}" for index in range(config.population)]
        if self.parameters.hub not in self.agents:
            raise ValueError("topology_hub_unknown")

    def contacts(self, actor: str) -> list[str]:
        index = self.agents.index(actor)
        shape = self.parameters.shape
        if shape == "complete":
            return [name for name in self.agents if name != actor]
        if shape == "star":
            return (
                [name for name in self.agents if name != actor]
                if actor == self.parameters.hub
                else [self.parameters.hub]
            )
        indices = (
            {(index - 1) % len(self.agents), (index + 1) % len(self.agents)}
            if shape == "ring"
            else {
                candidate
                for candidate in (index - 1, index + 1)
                if 0 <= candidate < len(self.agents)
            }
        )
        return [
            name
            for candidate, name in enumerate(self.agents)
            if candidate in indices and name != actor
        ]

    def decide(self, action: str, actor: str, payload: dict, state: dict) -> list[Decision]:
        if action != "message":
            return []
        target = payload.get("to")
        allowed = (
            self.parameters.public_messages if target is None else target in self.contacts(actor)
        )
        return [
            Decision(
                "communication_topology",
                allowed,
                "declared_message_route",
                {"shape": self.parameters.shape, "from": actor, "to": target},
            )
        ]


class PublicationPolicyParameters(StrictModel):
    forbidden_literals: list[str] = Field(min_length=1)
    file_globs: list[str] = Field(default_factory=lambda: ["project/**", "operations/**"])


class PublicationPolicy(Rule):
    def __init__(self, config: RunConfig, parameters: dict) -> None:
        self.parameters = PublicationPolicyParameters.model_validate(parameters)
        if any(not value for value in self.parameters.forbidden_literals):
            raise ValueError("publication_literal_must_not_be_empty")

    def decide(self, action: str, actor: str, payload: dict, state: dict) -> list[Decision]:
        if action == "integrate":
            checks = state["proposals"][payload["proposal"]]["evaluation"].get("policy_checks", [])
            own = [item for item in checks if item.get("rule") == "publication_policy"]
            return [
                Decision(
                    "publication_policy",
                    bool(own) and all(item.get("allowed") is True for item in own),
                    "sealed_publication_policy_passed",
                    {},
                )
            ]
        if action != "message" or payload.get("to") is not None:
            return []
        return [
            Decision(
                "publication_policy",
                not any(
                    literal in payload["content"] for literal in self.parameters.forbidden_literals
                ),
                "public_message_literal_policy",
                {},
            )
        ]

    def artifact_checks(self, source: Path) -> list[Decision]:
        matches = []
        for path in source.rglob("*"):
            name = path.relative_to(source).as_posix()
            if not path.is_file() or not any(
                fnmatch.fnmatchcase(name, glob) for glob in self.parameters.file_globs
            ):
                continue
            if path.is_symlink() or not path.resolve().is_relative_to(source.resolve()):
                raise ValueError("publication_policy_source_outside_artifact")
            content = path.read_bytes().decode("utf-8")
            for literal in self.parameters.forbidden_literals:
                if literal in content:
                    matches.append(
                        {
                            "path": name,
                            "literal_sha256": hashlib.sha256(literal.encode()).hexdigest(),
                        }
                    )
        return [
            Decision(
                "publication_policy",
                not matches,
                "artifact_literal_policy",
                {"matches": matches},
            )
        ]


class PerformanceParameters(StrictModel):
    points_per_completion: int = Field(default=1, ge=1)


class Performance(Rule):
    def __init__(self, config: RunConfig, parameters: dict) -> None:
        self.parameters = PerformanceParameters.model_validate(parameters)

    def completion(self, actor: str, state: dict) -> list[Effect]:
        score = state["agents"][actor]["score"] + self.parameters.points_per_completion
        return [Effect("performance", "score", score)]


class Promotion(Rule):
    def __init__(self, config: RunConfig, parameters: dict) -> None:
        if parameters:
            raise ValueError("promotion_uses_configured_levels")
        self.config = config

    def completion(self, actor: str, state: dict) -> list[Effect]:
        worker = state["agents"][actor]
        level = max(
            (level for level in self.config.levels if worker["verified"] >= level.completions),
            key=lambda level: level.completions,
        )
        current = next(item for item in self.config.levels if item.name == worker["level"])
        return (
            [Effect("promotion", "level", level.name)]
            if level.completions > current.completions
            else []
        )


class GoalGateParameters(StrictModel):
    independent_approvals: int = Field(default=1, ge=1)


class GoalGate(Rule):
    def __init__(self, config: RunConfig, parameters: dict) -> None:
        self.parameters = GoalGateParameters.model_validate(parameters)
        if self.parameters.independent_approvals >= config.population:
            raise ValueError("goal_approvals_exceed_independent_population")

    def decide(self, action: str, actor: str, payload: dict, state: dict) -> list[Decision]:
        if action != "finish_goal":
            return []
        proposal = state["goal_proposals"].get(payload["completion"])
        if proposal is None:
            return []
        reviews = proposal["reviews"]
        approvals = sum(
            review["approve"] and reviewer != proposal["actor"]
            for reviewer, review in reviews.items()
        )
        return [
            Decision(
                "goal_gate",
                approvals >= self.parameters.independent_approvals,
                "independent_goal_approval",
                {"approvals": approvals},
            ),
            Decision(
                "goal_gate",
                not any(not review["approve"] for review in reviews.values()),
                "unresolved_goal_rejection",
                {},
            ),
        ]


BUILTINS = {
    "capabilities": Capabilities,
    "delegation": Delegation,
    "review_gate": ReviewGate,
    "performance": Performance,
    "promotion": Promotion,
    "goal_gate": GoalGate,
    "resource_ledger": ResourceLedger,
    "communication_topology": CommunicationTopology,
    "publication_policy": PublicationPolicy,
    "output_audit": OutputAudit,
}


class RuleEngine:
    def __init__(self, config: RunConfig) -> None:
        self.config = config
        self.rules: list[Rule] = []
        self.sources: dict[str, dict] = {}
        for specification in config.rules:
            if not specification.enabled:
                continue
            factory = BUILTINS.get(specification.plugin)
            if factory is None:
                module, separator, name = specification.plugin.partition(":")
                if not separator:
                    raise ValueError(f"unknown_rule:{specification.plugin}")
                factory = getattr(import_module(module), name)
            source = inspect.getsourcefile(factory)
            if source is None or not Path(source).is_file():
                raise ValueError(f"plugin_source_unavailable:{specification.plugin}")
            self.sources[specification.plugin] = {
                "file": Path(source).name,
                "sha256": hashlib.sha256(Path(source).read_bytes()).hexdigest(),
            }
            instance = factory(config, specification.parameters)
            if not isinstance(instance, Rule):
                raise TypeError("plugin_factory_must_return_a_Rule")
            self.rules.append(instance)

    def decide(self, action: str, actor: str, payload: dict, state: dict) -> list[Decision]:
        decisions = []
        for rule in self.rules:
            facts = deepcopy(state)
            decisions.extend(rule.decide(action, actor, payload, facts))
            if facts != state:
                raise ValueError("rule_mutated_read_only_facts")
        return decisions

    def contacts(self, actor: str, agents: dict) -> list[str]:
        permitted = set(agents) - {actor}
        for rule in self.rules:
            contacts = rule.contacts(actor)
            if contacts is not None:
                permitted &= set(contacts)
        return sorted(permitted)

    def artifact_checks(self, source: Path) -> list[Decision]:
        return [decision for rule in self.rules for decision in rule.artifact_checks(source)]

    def completion(self, actor: str, state: dict) -> list[dict]:
        worker = state["agents"][actor]
        worker["verified"] += 1
        effects: list[dict] = []
        written: set[str] = set()
        for rule in self.rules:
            facts = deepcopy(state)
            proposed = rule.completion(actor, facts)
            if facts != state:
                raise ValueError("rule_mutated_read_only_facts")
            for effect in proposed:
                if effect.field not in {"score", "level"}:
                    raise ValueError("unsupported_effect_field")
                if effect.field == "score" and (type(effect.value) is not int or effect.value < 0):
                    raise ValueError("invalid_score_effect")
                if effect.field == "level" and effect.value not in {
                    level.name for level in self.config.levels
                }:
                    raise ValueError("unknown_level_effect")
                if effect.field in written:
                    raise ValueError(f"conflicting_rule_effect:{effect.field}")
                written.add(effect.field)
                worker[effect.field] = effect.value
                effects.append({"rule": effect.rule, "field": effect.field, "value": effect.value})
        return effects
