from __future__ import annotations

from graphlib import CycleError, TopologicalSorter
from pathlib import Path, PurePosixPath
from typing import TYPE_CHECKING

from pydantic import Field, model_validator

from ..config import StrictModel
from ..paths import relative_file

if TYPE_CHECKING:
    from .service import Institution


class CheckCommand(StrictModel):
    argv: list[str] = Field(min_length=1, max_length=30)

    @model_validator(mode="after")
    def valid(self) -> CheckCommand:
        if any(not arg or "\0" in arg for arg in self.argv):
            raise ValueError("check_command_arguments_must_not_be_empty_or_contain_nul")
        return self


class TaskDefinition(StrictModel):
    id: str = Field(pattern=r"^[a-z][a-z0-9_]*$", max_length=64)
    description: str = Field(min_length=1, max_length=10000)
    scope: str
    dependencies: list[str] = Field(default_factory=list, max_length=100)
    files: list[str] = Field(min_length=1, max_length=50)
    acceptance: list[str] = Field(min_length=1, max_length=20)
    checks: list[CheckCommand] = Field(default_factory=list, max_length=4)
    revision_of: str | None = None

    @model_validator(mode="after")
    def paths(self) -> TaskDefinition:
        for name in self.files:
            path = PurePosixPath(relative_file(name))
            if not name.startswith("project/") or path.as_posix() != name:
                raise ValueError("task_outputs_must_be_relative_files_under_project")
            if any(part.startswith(".") for part in path.parts):
                raise ValueError("hidden_output_paths_are_not_supported")
            if name.endswith("/") or len(path.parts) < 2:
                raise ValueError("output_path_must_name_a_file")
            if any(Path(part).is_reserved() for part in path.parts):
                raise ValueError("reserved_output_filename")
        if len(set(self.files)) != len(self.files):
            raise ValueError("duplicate_output_file")
        if any(not item.strip() for item in self.acceptance):
            raise ValueError("acceptance_criteria_must_not_be_blank")
        return self


class ReviseTask(TaskDefinition):
    reason: str = Field(min_length=1)


class WithdrawTask(StrictModel):
    ticket: str
    reason: str = Field(min_length=1)


class GoalSubmission(StrictModel):
    evidence: list[str] = Field(min_length=1, max_length=30)

    @model_validator(mode="after")
    def nonblank_evidence(self) -> GoalSubmission:
        if any(not item.strip() for item in self.evidence):
            raise ValueError("goal_evidence_must_not_be_blank")
        return self


class GoalReview(StrictModel):
    completion: str
    approve: bool
    comment: str = Field(min_length=1)


class GoalFinish(StrictModel):
    completion: str


class ResearchInput(StrictModel):
    url: str


class Society:
    """Agent decisions expressed as transactions; no planning or semantic judgement."""

    def __init__(self, institution: Institution) -> None:
        self.institution = institution

    def validate_task(self, actor: str, task: dict, state: dict) -> None:
        service = self.institution
        service.require(service.config.workload is None, "dynamic_tasks_require_society_mode")
        service.require(state["goal_status"] == "open", "goal_already_completed")
        service.require(
            task["scope"] in {scope for level in service.config.levels for scope in level.scopes},
            "unknown_task_scope",
        )
        service.require(
            task["id"] not in task["dependencies"],
            "task_cannot_depend_on_itself",
        )
        active = {
            key: item
            for key, item in state["tickets"].items()
            if item["status"] != "withdrawn" and key != task["id"]
        }
        service.require(set(task["dependencies"]) <= set(active), "unknown_dependency")
        lineage = set()
        target = task.get("revision_of")
        if target:
            service.require(target in active, "unknown_revision_target")
            service.require(
                active[target]["status"] == "complete",
                "revision_requires_delivered_task",
            )
            service.require(target in task["dependencies"], "revision_must_depend_on_original_task")
            while target:
                lineage.add(target)
                target = active[target].get("revision_of")
        for key, item in active.items():
            if set(item.get("files", [])).intersection(task["files"]):
                service.require(
                    key in lineage and item["status"] == "complete",
                    "output_owned_by_another_task",
                )
        graph = {key: item["dependencies"] for key, item in active.items()}
        graph[task["id"]] = task["dependencies"]
        try:
            list(TopologicalSorter(graph).static_order())
        except CycleError:
            service.require(False, "task_dependency_cycle")

    def propose_task(self, actor: str, args: dict, state: dict) -> tuple[dict, bool]:
        self.institution.require(args["id"] not in state["tickets"], "task_id_already_exists")
        self.validate_task(actor, args, state)
        task = {
            **args,
            "created_by": actor,
            "owner": None,
            "status": "available",
            "generation": 0,
            "offer": None,
            "completed_by": None,
        }
        state["tickets"][args["id"]] = task
        state["work_revision"] += 1
        return {"ticket": task}, True

    def revise_task(self, actor: str, args: dict, state: dict) -> tuple[dict, bool]:
        service = self.institution
        service.require(args["id"] in state["tickets"], "unknown_ticket")
        task = state["tickets"][args["id"]]
        service.require(task["status"] in {"available", "owned"}, "task_not_revisable")
        service.require(
            task["owner"] == actor or (task["owner"] is None and task["created_by"] == actor),
            "only_owner_or_unclaimed_task_creator_can_revise",
        )
        service.require(task["offer"] is None, "transfer_pending")
        self.validate_task(actor, args, state)
        for proposal in state["proposals"].values():
            if proposal["ticket"] == task["id"] and proposal["status"] == "pending":
                proposal["status"] = "superseded"
        task.update({key: value for key, value in args.items() if key != "reason"})
        task["generation"] += 1
        state["work_revision"] += 1
        return {"ticket": task, "reason": args["reason"]}, True

    def withdraw_task(self, actor: str, args: dict, state: dict) -> tuple[dict, bool]:
        service = self.institution
        task = state["tickets"][args["ticket"]]
        service.require(service.config.workload is None, "fixed_workload_cannot_be_withdrawn")
        service.require(task["status"] in {"available", "owned"}, "task_not_withdrawable")
        service.require(
            task["owner"] == actor or (task["owner"] is None and task["created_by"] == actor),
            "only_owner_or_creator_can_withdraw",
        )
        service.require(
            not any(
                task["id"] in item["dependencies"] and item["status"] != "withdrawn"
                for item in state["tickets"].values()
            ),
            "task_has_dependents",
        )
        service.require(task["offer"] is None, "transfer_pending")
        task.update(status="withdrawn", owner=None, withdrawal_reason=args["reason"])
        for proposal in state["proposals"].values():
            if proposal["ticket"] == task["id"] and proposal["status"] == "pending":
                proposal["status"] = "superseded"
        state["work_revision"] += 1
        return {"ticket": task}, True

    def goal_ready(self, state: dict) -> None:
        self.institution.require(
            self.institution.config.workload is None,
            "goal_review_requires_society_mode",
        )
        tasks = [task for task in state["tickets"].values() if task["status"] != "withdrawn"]
        self.institution.require(bool(tasks), "goal_requires_delivered_work")
        self.institution.require(
            all(task["status"] == "complete" for task in tasks),
            "goal_has_unfinished_tasks",
        )

    def propose_completion(self, actor: str, args: dict, state: dict) -> tuple[dict, bool]:
        import uuid

        self.institution.require(state["goal_status"] == "open", "goal_already_completed")
        self.goal_ready(state)
        identifier = uuid.uuid4().hex
        proposal = {
            "id": identifier,
            "actor": actor,
            "evidence": args["evidence"],
            "head": state["head"],
            "work_revision": state["work_revision"],
            "reviews": {},
            "status": "pending",
        }
        state["goal_proposals"][identifier] = proposal
        return {"completion": proposal}, True

    def current_completion(self, identifier: str, state: dict) -> dict:
        service = self.institution
        service.require(state["goal_status"] == "open", "goal_already_completed")
        service.require(identifier in state["goal_proposals"], "unknown_goal_completion")
        proposal = state["goal_proposals"][identifier]
        service.require(proposal["status"] == "pending", "goal_completion_not_pending")
        service.require(
            proposal["head"] == state["head"]
            and proposal["work_revision"] == state["work_revision"],
            "stale_goal_evidence",
        )
        self.goal_ready(state)
        return proposal

    def review_completion(self, actor: str, args: dict, state: dict) -> tuple[dict, bool]:
        proposal = self.current_completion(args["completion"], state)
        self.institution.require(actor != proposal["actor"], "self_review_not_independent")
        proposal["reviews"][actor] = {"approve": args["approve"], "comment": args["comment"]}
        return {"completion": proposal}, True

    def finish_goal(self, actor: str, args: dict, state: dict) -> tuple[dict, bool]:
        proposal = self.current_completion(args["completion"], state)
        self.institution.require(actor == proposal["actor"], "only_goal_proposer_can_finish")
        proposal["status"] = "accepted"
        state["goal_status"] = "completed"
        state["accepted_completion"] = proposal["id"]
        for other in state["goal_proposals"].values():
            if other["id"] != proposal["id"] and other["status"] == "pending":
                other["status"] = "superseded"
        return {"completion": proposal, "completion_basis": "agent_attestation_and_checks"}, True
