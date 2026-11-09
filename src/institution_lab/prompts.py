import json

from jinja2 import Environment

from .config import RunConfig
from .evaluation.workload import Workload


def render_system(
    templates: Environment,
    config: RunConfig,
    workload: Workload | None,
    actor: str,
    role: str,
) -> str:
    active = [rule for rule in config.rules if rule.enabled]
    coordination = (
        "coordination/distributed.md.j2"
        if config.coordination == "distributed"
        else "coordination/central-worker.md.j2"
    )
    role_template = f"roles/{role}.md.j2" if workload else f"roles/society/{role}.md.j2"
    return templates.get_template(role_template).render(
        agent_id=actor,
        population=config.population,
        agent_parameters=config.agent_parameters.get(actor, {}),
        society_goal=config.goal,
        project_description=workload.description if workload else config.goal,
        coordination_description=templates.get_template(coordination).render(),
        institution_description=json.dumps(
            {
                "enforcement": config.enforcement,
                "rules": [rule.agent_description() for rule in active],
                "levels": [level.model_dump() for level in config.levels] if active else [],
            },
            indent=2,
        ),
        tool_instructions=templates.get_template(
            "tools/organization.md.j2" if workload else "tools/society.md.j2"
        ).render(research=config.research.model_dump()),
    )
