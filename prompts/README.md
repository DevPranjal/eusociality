# eusociality prompts

Versioned prompt inputs for the separate InstitutionLab harness.

## Layout

- `roles/worker.md.j2`: an autonomous software worker.
- `roles/coordinator.md.j2`: the optional central task allocator.
- `events/continue.md.j2`: a neutral wake-up after an observable state change.
- `probes/runtime.md.j2`: a bounded compatibility check, not an experiment.
- `roles/society/`: members receiving one shared goal, without a task graph.
- `events/society/`: neutral startup/update notifications.
- `tools/society.md.j2`: agent task creation, delivery, research and goal review.

Templates use Jinja with strict undefined variables. The harness must freeze
the rendered inputs and a content hash for each run. Prompt edits apply only to
new runs. Do not tailor a prompt to force a previously observed run to succeed.

The institution's resolved policy, task descriptions, capabilities, and tool
catalog are supplied from the run configuration and authoritative state.
The prompts do not grant rights, decide promotions, or replace enforcement.

Task planning and goal assessment happen inside society agents, not an external
planner. Templates explain interfaces and invariants without assigning tasks
or roles. Agents create/revise work and choose appropriate checks; peer review
assesses semantic quality and completion. Automated artifact/check results must
not be presented as independent proof of factual accuracy.

Review criteria:

- Explain the goal and interfaces, not a secretly assigned decentralized plan.
- State observable facts and distinguish instructions from enforced rules.
- Never ask agents to invent evidence, bypass a denied operation, impersonate
  another worker, or manufacture a successful result.
- Preserve refusal, idle time, disagreement, and failure as possible outcomes.
