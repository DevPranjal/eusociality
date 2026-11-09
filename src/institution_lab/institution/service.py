from __future__ import annotations

import uuid
from copy import deepcopy
from dataclasses import asdict

from pydantic import Field

from ..config import RunConfig, StrictModel
from ..store import Store
from .rules import RuleEngine
from .society import (
    GoalFinish,
    GoalReview,
    GoalSubmission,
    ResearchInput,
    ReviseTask,
    Society,
    TaskDefinition,
    WithdrawTask,
)


class TicketInput(StrictModel):
    ticket: str


class OfferInput(TicketInput):
    to: str


class ProposalInput(StrictModel):
    proposal: str


class ReviewInput(ProposalInput):
    approve: bool
    comment: str


class MessageInput(StrictModel):
    content: str
    to: str | None = None


class ResourceInput(StrictModel):
    resource: str
    units: int = Field(ge=1, strict=True)


class EmptyInput(StrictModel):
    pass


class ReceiptInput(StrictModel):
    request: str


INPUTS = {
    "receipt": ReceiptInput,
    "board": EmptyInput,
    "inbox": EmptyInput,
    "claim": TicketInput,
    "offer": OfferInput,
    "accept": TicketInput,
    "refuse": TicketInput,
    "cancel": TicketInput,
    "review": ReviewInput,
    "message": MessageInput,
    "proposal": ProposalInput,
    "integrate": ProposalInput,
    "submit": TicketInput,
    "sync": EmptyInput,
    "propose_task": TaskDefinition,
    "revise_task": ReviseTask,
    "withdraw_task": WithdrawTask,
    "research": ResearchInput,
    "propose_completion": GoalSubmission,
    "review_completion": GoalReview,
    "finish_goal": GoalFinish,
    "reserve_resource": ResourceInput,
}


class Rejected(ValueError):
    pass


class Institution:
    def __init__(self, config: RunConfig, store: Store) -> None:
        self.config = config
        self.store = store
        self.engine = RuleEngine(config)
        self.society = Society(self)
        self.handlers = {
            "receipt": self.receipt,
            "board": self.board,
            "inbox": self.inbox,
            "claim": self.claim,
            "offer": self.offer,
            "accept": self.accept,
            "refuse": self.refuse,
            "cancel": self.cancel,
            "review": self.review,
            "message": self.message,
            "proposal": self.proposal,
            "propose_task": self.society.propose_task,
            "revise_task": self.society.revise_task,
            "withdraw_task": self.society.withdraw_task,
            "propose_completion": self.society.propose_completion,
            "review_completion": self.society.review_completion,
            "finish_goal": self.society.finish_goal,
            "reserve_resource": self.reserve_resource,
        }

    def initialize(self, tasks: list[dict], head: str) -> None:
        agents = {
            f"worker-{index:02}": {
                "role": self.config.role_for(f"worker-{index:02}"),
                "model": self.config.runtime_for(f"worker-{index:02}").model,
                "reasoning_effort": self.config.runtime_for(f"worker-{index:02}").reasoning_effort,
                "score": 0,
                "verified": 0,
                "level": self.config.starting_level(f"worker-{index:02}").name,
            }
            for index in range(self.config.population)
        }
        tickets = {
            task["id"]: {
                **task,
                "owner": None,
                "status": "available",
                "generation": 0,
                "offer": None,
                "completed_by": None,
            }
            for task in tasks
        }
        self.store.initialize(
            {
                "status": "ready",
                "version": 0,
                "activity": {},
                "head": head,
                "agents": agents,
                "tickets": tickets,
                "proposals": {},
                "messages": [],
                "interventions": [],
                "goal": self.config.goal,
                "goal_status": "open",
                "goal_proposals": {},
                "work_revision": 0,
                "accepted_completion": None,
                "resources": {
                    name: {**pool.model_dump(), "used": 0, "by_actor": {}}
                    for name, pool in self.config.resources.items()
                },
                "resource_reservations": [],
            }
        )

    @staticmethod
    def require(condition: bool, reason: str) -> None:
        if not condition:
            raise Rejected(reason)

    def validate(self, action: str, payload: dict) -> dict:
        if action not in INPUTS:
            raise Rejected("unknown_action")
        return INPUTS[action].model_validate(payload).model_dump()

    def act(self, actor: str, action: str, payload: dict, request_id: str) -> dict:
        arguments = self.validate(action, payload)

        def operation(state: dict) -> tuple[dict, bool]:
            try:
                self.preconditions(actor, action, arguments, state)
                decisions = self.engine.decide(action, actor, arguments, state)
                denied = any(not decision.allowed for decision in decisions)
                evidence = [asdict(decision) for decision in decisions]
                if denied and self.config.enforcement == "hard":
                    return {"ok": False, "reason": "institution_denied", "rules": evidence}, False
                result, changed = self.handlers[action](actor, arguments, state)
                return {
                    "ok": True,
                    **result,
                    "rules": evidence,
                    "advisory_violation": denied,
                }, changed
            except Rejected as error:
                return {"ok": False, "reason": str(error)}, False

        return self.store.transact(actor, request_id, action, arguments, operation)

    def preconditions(self, actor: str, action: str, args: dict, state: dict) -> None:
        self.require(actor in state["agents"], "unknown_actor")
        role = state["agents"][actor]["role"]
        if role == "reviewer":
            self.require(
                action
                not in {
                    "claim",
                    "accept",
                    "submit",
                    "integrate",
                    "reserve_resource",
                },
                "reviewer_has_no_authoring_or_resource_authority",
            )
        if role == "challenger":
            self.require(
                action in {"board", "inbox", "message", "receipt", "proposal"},
                "challenger_is_message_only",
            )
        self.require(
            state["agents"][actor].get("availability", "available") == "available",
            "worker_unavailable",
        )
        self.require(state["status"] in {"ready", "running", "draining"}, "run_not_active")
        if "ticket" in args:
            self.require(args["ticket"] in state["tickets"], "unknown_ticket")
        if "proposal" in args:
            self.require(args["proposal"] in state["proposals"], "unknown_proposal")
        if "resource" in args:
            self.require(args["resource"] in state.get("resources", {}), "unknown_resource")

    def board(self, actor: str, args: dict, state: dict) -> tuple[dict, bool]:
        result = {
            key: state[key]
            for key in (
                "version",
                "head",
                "agents",
                "tickets",
                "proposals",
                "status",
                "goal",
                "goal_status",
                "goal_proposals",
            )
        }
        result["resources"] = state.get("resources", {})
        if not self.config.disclose_peer_roles:
            result["agents"] = deepcopy(result["agents"])
            for identity, profile in result["agents"].items():
                if identity != actor:
                    profile["role"] = "participant"
        result["contacts"] = self.engine.contacts(actor, state["agents"])
        return result, False

    def reserve_resource(self, actor: str, args: dict, state: dict) -> tuple[dict, bool]:
        self.require(args["units"] > 0, "resource_units_must_be_positive")
        pool = state["resources"][args["resource"]]
        pool["used"] += args["units"]
        pool["by_actor"][actor] = pool["by_actor"].get(actor, 0) + args["units"]
        reservation = {"id": uuid.uuid4().hex, "actor": actor, **args, "unit": pool["unit"]}
        state["resource_reservations"].append(reservation)
        return {
            "reservation": reservation,
            "used": pool["used"],
            "capacity": pool["capacity"],
        }, True

    def receipt(self, actor: str, args: dict, state: dict) -> tuple[dict, bool]:
        return self.store.receipt(actor, args["request"]), False

    def inbox(self, actor: str, args: dict, state: dict) -> tuple[dict, bool]:
        return {
            "messages": [m for m in state["messages"] if m["to"] in {None, actor}],
            "version": state["version"],
        }, False

    def claim(self, actor: str, args: dict, state: dict) -> tuple[dict, bool]:
        self.require(self.config.coordination == "distributed", "coordinator_allocates_work")
        self.require(state["agents"][actor]["role"] == "worker", "workers_only")
        task = state["tickets"][args["ticket"]]
        self.available(task, state)
        task.update(owner=actor, status="owned", generation=task["generation"] + 1)
        return {"ticket": task}, True

    def available(self, task: dict, state: dict) -> None:
        self.require(task["status"] == "available", "ticket_not_available")
        self.require(
            all(state["tickets"][key]["status"] == "complete" for key in task["dependencies"]),
            "dependencies_incomplete",
        )

    def offer(self, actor: str, args: dict, state: dict) -> tuple[dict, bool]:
        task = state["tickets"][args["ticket"]]
        target = args["to"]
        self.require(task["owner"] != target, "recipient_already_owns_ticket")
        self.require(target in state["agents"] and target != actor, "invalid_recipient")
        self.require(state["agents"][target]["role"] == "worker", "recipient_must_be_worker")
        coordinator = state["agents"][actor]["role"] == "coordinator"
        if self.config.coordination == "central":
            self.require(coordinator, "send_transfer_recommendation_to_coordinator")
            if task["owner"] is None:
                self.available(task, state)
        else:
            self.require(task["owner"] == actor and task["status"] == "owned", "not_ticket_owner")
        self.require(task["status"] in {"available", "owned"}, "ticket_not_transferable")
        self.require(task["offer"] is None, "offer_already_pending")
        task["offer"] = {"from": actor, "to": target}
        return {"ticket": task}, True

    def accept(self, actor: str, args: dict, state: dict) -> tuple[dict, bool]:
        task = state["tickets"][args["ticket"]]
        self.require(
            task["offer"] is not None and task["offer"]["to"] == actor, "no_offer_for_actor"
        )
        task.update(owner=actor, status="owned", generation=task["generation"] + 1, offer=None)
        return {"ticket": task}, True

    def refuse(self, actor: str, args: dict, state: dict) -> tuple[dict, bool]:
        task = state["tickets"][args["ticket"]]
        self.require(
            task["offer"] is not None and task["offer"]["to"] == actor, "no_offer_for_actor"
        )
        task["offer"] = None
        return {"ticket": task}, True

    def cancel(self, actor: str, args: dict, state: dict) -> tuple[dict, bool]:
        task = state["tickets"][args["ticket"]]
        self.require(
            task["offer"] is not None and task["offer"]["from"] == actor,
            "no_offer_from_actor",
        )
        task["offer"] = None
        return {"ticket": task}, True

    def message(self, actor: str, args: dict, state: dict) -> tuple[dict, bool]:
        self.require(args["to"] is None or args["to"] in state["agents"], "unknown_recipient")
        message = {"from": actor, **args}
        state["messages"].append(message)
        return {"message": message}, True

    def proposal(self, actor: str, args: dict, state: dict) -> tuple[dict, bool]:
        return {"proposal": state["proposals"][args["proposal"]]}, False

    def review(self, actor: str, args: dict, state: dict) -> tuple[dict, bool]:
        self.require(
            state["agents"][actor]["role"] in {"worker", "reviewer"},
            "workers_or_registered_reviewers_only",
        )
        proposal = state["proposals"][args["proposal"]]
        self.require(actor != proposal["actor"], "self_review_not_independent")
        self.require(proposal["status"] == "pending", "proposal_not_pending")
        proposal["reviews"][actor] = {"approve": args["approve"], "comment": args["comment"]}
        return {"proposal": proposal}, True

    def register_proposal(
        self,
        actor: str,
        ticket: str,
        base: str,
        digest: str,
        diff: str,
        evaluation: dict,
        artifact: str,
        request_id: str | None = None,
    ) -> dict:
        identifier = uuid.uuid4().hex

        def operation(state: dict) -> tuple[dict, bool]:
            self.preconditions(actor, "submit", {"ticket": ticket}, state)
            task = state["tickets"][ticket]
            self.require(task["owner"] == actor and task["status"] == "owned", "not_ticket_owner")
            for proposal in state["proposals"].values():
                if proposal["ticket"] == ticket and proposal["status"] == "pending":
                    proposal["status"] = "superseded"
            proposal = {
                "id": identifier,
                "actor": actor,
                "ticket": ticket,
                "generation": task["generation"],
                "base": base,
                "digest": digest,
                "diff": diff,
                "evaluation": evaluation,
                "artifact": artifact,
                "reviews": {},
                "status": "pending",
            }
            state["proposals"][identifier] = proposal
            return {"ok": True, "proposal": proposal}, True

        return self.store.transact(
            actor, request_id or identifier, "submit", {"ticket": ticket}, operation
        )

    def integration_decision(self, actor: str, proposal_id: str) -> dict:
        state = self.store.state()
        result = self._integration_result(actor, proposal_id, state)
        self.store.event(actor, "integration_decision", {"proposal": proposal_id, **result})
        return result

    def _integration_result(
        self, actor: str, proposal_id: str, state: dict, *, compatible_base: bool = False
    ) -> dict:
        self.preconditions(actor, "integrate", {"proposal": proposal_id}, state)
        proposal = state["proposals"][proposal_id]
        task = state["tickets"][proposal["ticket"]]
        self.require(proposal["actor"] == actor and task["owner"] == actor, "not_proposal_owner")
        self.require(proposal["generation"] == task["generation"], "stale_ownership")
        self.require(proposal["status"] == "pending", "proposal_not_pending")
        self.require(
            proposal["base"] == state["head"] or compatible_base, "stale_base_sync_and_resubmit"
        )
        self.require(task["offer"] is None, "transfer_pending")
        decisions = self.engine.decide("integrate", actor, {"proposal": proposal_id}, state)
        result = {
            "ok": self.config.enforcement != "hard" or all(d.allowed for d in decisions),
            "rules": [asdict(decision) for decision in decisions],
            "advisory_violation": any(not d.allowed for d in decisions),
        }
        return result

    def reserve_integration(
        self, actor: str, proposal_id: str, request_id: str, *, compatible_base: bool = False
    ) -> dict:
        def operation(state: dict) -> tuple[dict, bool]:
            result = self._integration_result(
                actor, proposal_id, state, compatible_base=compatible_base
            )
            if not result["ok"]:
                return result, False
            proposal = state["proposals"][proposal_id]
            proposal["status"] = "integrating"
            state["tickets"][proposal["ticket"]]["status"] = "integrating"
            return {**result, "proposal": proposal}, True

        return self.store.transact(
            actor, request_id, "reserve_integration", {"proposal": proposal_id}, operation
        )

    def record_integration(
        self,
        proposal_id: str,
        head: str,
        evaluation: dict,
        actor: str = "institution",
        request_id: str | None = None,
    ) -> dict:
        def operation(state: dict) -> tuple[dict, bool]:
            proposal = state["proposals"][proposal_id]
            self.require(proposal["status"] == "integrating", "proposal_not_reserved")
            task = state["tickets"][proposal["ticket"]]
            self.require(
                task["owner"] == proposal["actor"] and task["generation"] == proposal["generation"],
                "stale_ownership",
            )
            state["head"] = head
            state["work_revision"] += 1
            proposal["status"] = "integrated"
            proposal["integration_evaluation"] = evaluation
            effects = []
            if evaluation["passed"]:
                task.update(status="complete", completed_by=proposal["actor"], offer=None)
                effects = self.engine.completion(proposal["actor"], state)
            else:
                task["status"] = "owned"
            return {
                "ok": True,
                "passed": evaluation["passed"],
                "effects": effects,
                "head": head,
                "evaluation": evaluation,
                "workspace_sync_required": True,
            }, True

        return self.store.transact(
            actor,
            request_id or f"integration:{proposal_id}",
            "integrate",
            {"proposal": proposal_id},
            operation,
        )

    def worker_unavailable(self, actor: str) -> None:
        def update(state: dict) -> None:
            state["agents"][actor]["availability"] = "unavailable"
            for proposal in state["proposals"].values():
                if proposal["actor"] == actor and proposal["status"] == "pending":
                    proposal["status"] = "abandoned"
            for task in state["tickets"].values():
                if task["owner"] == actor and task["status"] not in {"complete", "integrating"}:
                    task.update(owner=None, status="available", generation=task["generation"] + 1)
                if task["offer"] and actor in task["offer"].values():
                    task["offer"] = None
            state["version"] += 1
            state.setdefault("activity", {})["institution"] = state["version"]

        self.store.update(update)
        self.store.event(actor, "worker_unavailable", {"ownership_fenced": True})
