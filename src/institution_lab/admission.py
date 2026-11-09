from __future__ import annotations

import asyncio
import json

from copilot import CopilotClient
from jinja2 import Environment, FileSystemLoader, StrictUndefined

from .config import RunConfig
from .evaluation.commands import CommandSuite
from .evaluation.workload import Workload
from .institution.rules import RuleEngine
from .prompts import render_system
from .runtime.docker import docker, github_credential
from .runtime.protocol import identity
from .runtime.worker import TOOL_SETS


def validate_static(config: RunConfig) -> dict:
    workload = Workload.load(config.workload) if config.workload else None
    if workload is None and not config.goal:
        raise ValueError("society_requires_a_shared_goal")
    RuleEngine(config)
    if config.outcome_suite:
        CommandSuite.load(config.outcome_suite)
    if config.project_inputs and not config.project_inputs.is_dir():
        raise ValueError("project_inputs_must_be_a_directory")
    templates = Environment(
        loader=FileSystemLoader(config.prompt_repository),
        undefined=StrictUndefined,
        auto_reload=False,
    )
    for index in range(config.population):
        actor = f"worker-{index:02}"
        if config.role_for(actor) not in TOOL_SETS:
            raise ValueError(f"registered_role_has_no_native_tool_set:{config.role_for(actor)}")
        render_system(templates, config, workload, actor, config.role_for(actor))
    folder = "events" if workload else "events/society"
    templates.get_template(f"{folder}/start.md.j2").render()
    templates.get_template(f"{folder}/continue.md.j2").render(updates={})
    coding_actors = [
        f"worker-{index:02}"
        for index in range(config.population)
        if config.coordination != "central" or index != 0
    ]
    initial_scopes = set().union(*(config.starting_level(actor).scopes for actor in coding_actors))
    restrictions = {rule.plugin for rule in config.rules if rule.enabled}
    if "capabilities" in restrictions and config.enforcement == "hard":
        scopes = (
            set().union(*(level.scopes for level in config.levels))
            if "promotion" in restrictions
            else initial_scopes
        )
        if workload and not {ticket.scope for ticket in workload.tickets} <= scopes:
            raise ValueError("unreachable_ticket_scope")
        if workload and not any(
            not ticket.dependencies and ticket.scope in initial_scopes
            for ticket in workload.tickets
        ):
            raise ValueError("no_initial_eligible_work")
    return {
        "valid": True,
        "tickets": len(workload.tickets) if workload else 0,
        "task_creation": "fixed_workload" if workload else "agents_during_execution",
        "population": config.population,
        "coordination": config.coordination,
        "enforcement": config.enforcement,
        "enabled_rules": sorted(restrictions),
        "max_ai_credits_per_session": config.runtime.max_ai_credits,
        "config_hash": config.fingerprint(),
    }


async def validate_models(config: RunConfig) -> dict:
    choices = {
        actor: config.runtime_for(actor)
        for actor in (f"worker-{index:02}" for index in range(config.population))
    }
    async with CopilotClient(
        github_token=github_credential(),
        use_logged_in_user=False,
        log_level="error",
    ) as client:
        catalog = {model.id: model for model in await client.list_models()}
    for actor, choice in choices.items():
        if choice.model not in catalog:
            raise ValueError(f"unknown_model:{actor}:{choice.model}")
        model = catalog[choice.model]
        if model.policy is not None and model.policy.state != "enabled":
            raise ValueError(f"model_not_enabled:{actor}:{choice.model}")
        supported = model.supported_reasoning_efforts or []
        if choice.reasoning_effort != "none" and choice.reasoning_effort not in supported:
            raise ValueError(f"unsupported_reasoning:{actor}:{choice.reasoning_effort}")
        if choice.reasoning_effort == "none" and model.capabilities.supports.reasoning_effort:
            raise ValueError(f"reasoning_model_requires_explicit_effort:{actor}")
    return {
        actor: {"model": choice.model, "reasoning_effort": choice.reasoning_effort}
        for actor, choice in choices.items()
    }


async def preflight(config: RunConfig) -> dict:
    result = validate_static(config)
    result["docker"] = json.loads(
        await asyncio.to_thread(
            docker,
            "info",
            "--format",
            '{"ServerVersion":"{{.ServerVersion}}","OSType":"{{.OSType}}",'
            '"MemTotal":{{.MemTotal}},"NCPU":{{.NCPU}}}',
        )
    )
    if result["docker"]["OSType"] != "linux":
        raise ValueError("Docker Desktop must use Linux containers")
    result["models"] = await validate_models(config)
    for image in (config.runtime.worker_image, config.runtime.proxy_image):
        await asyncio.to_thread(docker, "image", "inspect", "--format", "{{.Id}}", image)
    result["worker_protocol"] = json.loads(
        await asyncio.to_thread(
            docker,
            "run",
            "--rm",
            "--network",
            "none",
            "--cap-drop",
            "ALL",
            "--read-only",
            "--user",
            "1001:1001",
            "--entrypoint",
            "python",
            config.runtime.worker_image,
            "-I",
            "-m",
            "institution_lab.runtime.protocol",
        )
    )
    if result["worker_protocol"] != identity():
        raise ValueError("worker_image_is_stale_rebuild_it")
    return result
