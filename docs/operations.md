# eusociality operations

A configurable research organization around existing Copilot coding agents.

The [overview](../README.md) introduces the shared-goal interface.
The [validation report](validation.md) records tested runtime, institutional
and historical-observation behavior. Research measurements are available in
the [experiment tools and curated data](../research/README.md).

Agent prompts live in [prompts](../prompts/README.md), separate from runtime source.
They are configuration inputs, not Python literals.
Core rules must never be changed merely to force a particular run to succeed.

## Development

```powershell
python -m venv .venv
.\.venv\Scripts\python -m pip install -e ".[dev]"
.\.venv\Scripts\python -m pytest
```

### Guided command

The `eusoc` entry point launches guided setup with no arguments; existing CLI
commands are also available as `eusoc run`, `eusoc status`, `eusoc observe`, etc.
The older `institution_lab` module and `institution-lab` command remain supported.

Install the entry point through the project's editable install, or isolate it
with `uv tool install --editable . --constraint requirements.lock`.
The wizard uses the local harness's defaults and separate prompt repository.
`--project PATH` chooses where `.eusoc` drafts and `runs` are saved.
`--base CONFIG` chooses the starting configuration.

The wizard supports a common starting level and per-agent overrides. Starting
authority does not award verified completions or score. Automatic progression
only promotes; it does not demote an explicitly chosen starting level.
The resolved configuration records the original choices in `initial_levels`.

Goal entry is one plain-text shared objective. The task board starts empty;
registered agents propose work, files, criteria and dependencies. No extra
planning session or model call is made before launch. `--save-only` saves the
configuration without creating any agents.

`configs\society.yaml` defines the defaults. `workload: null` selects society
mode; fixed workload paths remain available through the existing CLI for
reproducible baselines. Society members create and revise tasks through
`propose_task`, `revise_task` and `withdraw_task`. Task ids/output ownership and
dependency cycles are checked transactionally. Revision invalidates prior pending
delivery evidence. There is no hidden task allocator in distributed mode.

The guided setup does not ask for websites or access permission. Legacy
`research.enabled` and `research.allowed_hosts` remain readable for compatibility
but do not gate public reads. The `research` tool reads public HTML/plaintext/
JSON over HTTP(S), supports normal query strings and public redirects, rejects
credentials and private-address resolution, and retains content/URL/time/hash records.
It does not give worker shells unrestricted egress or deployment access.

Tasks publish declared UTF-8 files under `project/`. Their optional command checks
run in the separate unprivileged, read-only, no-network evaluator. File presence
and command results are measured; semantic quality/factual accuracy must be
assessed by agents during peer review. Agents may use the installed Python/Git
tools; arbitrary dependency downloads are not configured.

Task completion alone does not end society mode. An agent proposes completion
against the original goal with evidence, another reviews it, and the proposer
requests `finish_goal`. The default `goal_gate` requires at least one independent
approval and no unresolved rejection. Its `independent_approvals` parameter is
configurable; minimal/advisory experiments must report their different assurance.
New work or published revisions invalidate old completion evidence. Empty work
cannot certify a goal; incomplete/withdrawn work remains recorded.
Accepted completion is canonical: concurrent pending completion proposals are
superseded and cannot overwrite the accepted ID.

Delivered files can be corrected by a new task with `revision_of` naming the
latest completed owner task and that task included in `dependencies`. Normal
ownership, checks and independent review still apply. Prior tasks, reviews and
checks remain retained; active or unrelated overlapping ownership is rejected.

New society presets use `integration_mode: disjoint_files`. An unchanged reviewed
patch may integrate after unrelated merges only when the trusted controller finds
no overlap with its changed output paths or declared dependency files. Protected
checks still run, overlapping changes still require new review, and agents cannot
override this decision. `strict_base` remains the schema default for existing
configs and reproducible strict-freshness comparisons. Undeclared semantic
dependencies are not certified by path disjointness.
Submission also evaluates the exact candidate patch in the current published
context when changes are disjoint. Evaluation records the reviewed/evaluated
bases and preview path, without changing the original diff. Synchronization
recognizes already-published edits even when unrelated files were added, while
preserving genuine unpublished changes.

The society preset also uses `scheduler_mode: workflow`: current approved-goal
owners and approved delivery owners get critical-path priority among eligible
identities; equal-priority identities remain least-activated first. This changes
scheduling, not ownership, review gates or agent decisions. `fair` remains the
schema default for existing configs. Private messages wake only their recipient,
with inbox acknowledgements separate from board observations; public updates
remain shared. Journal connections are explicitly closed after each transaction.

The draft saves the complete configuration, goal and tool boundaries. Launch
checks Linux Docker, models and worker/source/SDK identity. You may edit the
saved configuration at the final prompt; launch reloads and validates it.
Rules remain fixed while agents evolve the work through recorded transactions.

Execution uses the existing society runtime. The append-only terminal timeline
is a filtered view of the actual journal: claims, native tool activity, agent
messages, review/transfer decisions, integration effects and errors. Raw SDK
streaming deltas are not printed. `timeline.txt` retains the readable view and
event sequence references; the SQLite journal remains authoritative.
Use `eusoc run CONFIG OUTPUT --timeline` for the same view when launching a saved
configuration. Dashboard frame capture and JSON output remain separate modes.

Only synthetic workload code may be supplied to experimental agents. Existing
research, user home directories, credentials, and unrelated workspaces must not
be mounted into worker/evaluation environments.

## Build the isolated runtime

Docker Desktop must be running with a Linux engine. Authentication is obtained
through the supported `gh auth token` interface and supplied privately to the
SDK session. Do not put credentials in configuration, command arguments, images,
workspaces, or prompt files.

```powershell
docker build -f docker\proxy.Dockerfile -t institution-lab-proxy:dev .
docker build -f docker\worker.Dockerfile -t institution-lab-worker:dev .
```

The worker build accepts `--build-arg PIP_INDEX_URL=...` for an approved package
mirror. This machine required its configured Microsoft mirror rather than the
public Python package CDN. Do not disable TLS verification.

When a session credit ceiling is configured, the SDK/runtime minimum is 30 AI
credits. The current default is 200 credits per session. Smaller-than-minimum
limits and nonfinite numbers are rejected. Set
`runtime.max_ai_credits: null` to omit `sessionLimits` from the SDK request rather
than substituting a very large number or silently retaining 30. Account/provider
quotas, billing and rate limits still apply. Wall-clock and concurrency limits
are separate. The frozen configuration and worker readiness record expose the
selected mode.

Run `python -m institution_lab doctor configs\smoke.yaml` before an experiment.
It checks model/reasoning availability, plugins, prompts, workload reachability,
Docker images, and the worker's source/protocol/SDK identity. Rebuild the worker
after changing its runtime code; stale images are rejected, not silently used.
Plain `run` performs the same runtime admission against its frozen image IDs
before starting the population. Content-addressed `institution-lab-recorded`
image tags are retained locally for run reproducibility; workers and networks
are still cleaned up. No shared-image pruning is performed.

Protected evaluators signal readiness before candidate work. Missing images or
driver startup failures are infrastructure errors, not failed artifact checks.
`runtime.evaluation_retries` defaults to two bounded, logged startup retries;
executed check failures are not retried. Final evaluation runs before worker
cleanup and is journalled before `run_end`. Infrastructure failure or failed
final checks cannot produce a completed run status.

## Run a company

`project_inputs` optionally seeds a protected `inputs/` directory alongside the
empty society project. Agents can read those UTF-8 fixtures, but submitted changes
to them are rejected; their original bytes are retained in frozen evidence.

Enable `output_audit` with an `outcome_suite` YAML file to measure completed
command-line artifacts against operator-defined input/expected-output cases.
Expected outputs remain on the host. The evaluator receives only commands and
test inputs, and returns actual results for host comparison. It separately records
raw-output disclosure markers so malformed JSON is not mistaken for safe output.
This audit is observational: it does not give adaptive feedback to agents or
change publication/goal permissions. A completed society may still fail the
independent outcome audit; read both fields in the summary.

`disclose_peer_roles: false` withholds peers' configured authority-role labels
from the member board, displaying `participant` instead. It never changes
session identity, actual permissions or operator-side records. The default
preserves the existing full-role board.

Research configurations also support a true `population: 1` control (disable
independent-review rules that cannot be satisfied), `agent_roles` for registered
reviewer/message-only challenger identities, and per-actor `agent_parameters`.
These remain real native sessions, not outside planners or scripted task workers.

`communication_topology` is an explicit rule with complete/ring/line/star shapes,
hub identity and public-message permission. `board` reports actual permitted
contacts. It restricts peer messages; shared proposal/artifact visibility is a
separate channel, not implicitly isolated by the graph.

Declare synthetic `resources` with capacity, per-agent limit, accounting scope
(`global` default or explicit `per_agent`) and unit; enable `resource_ledger`.
`reserve_resource` is attributable and idempotent, and concurrent requests are
serialized through the institutional transaction. Actor-local limits intentionally
do not enforce the aggregate parent cap; that mode is an experimental control.

`review_gate` supports `independent_approvals` and optional `veto_rejections`.
Defaults retain the prior one-approval behavior. `publication_policy` can screen
configured forbidden literals before publication, independently of peer-review
settings. Rule-specification `private_parameters` withhold operator-side values
from member prompt descriptions while exposing hashes/withholding flags.
Do not mistake exact-literal screening for general semantic injection resistance.

Reviewer sessions have read/search/shell/organization tools but no public authoring
authority. Challenger sessions expose organization only and are message-only.
Native tool-set registration is checked before population startup.

The observer opens on **Timeline**. It works on completed, failed and live runs
using their retained journal and canonical Git history. Choose a run, select an
event, and the deliverables pane resolves that event's published revision.
Added files do not appear before publication; deleted files and previous versions
remain readable at earlier events. **Preview** renders Markdown; **Source** shows
the retained bytes as text. All-event mode includes raw runtime events; activity
mode removes token-delta and administrative noise. Pagination, agent filters and
event-number jumps work beyond the former 400-event overview window.

Published-file resolution is a thin read-only history query and does not materialize
all agent conversations/graphs. Old recordings without revision history explicitly
report unavailable historical state rather than showing today's files as history.
Private edits between workspace checkpoints cannot be reconstructed. Event detail
retains the original recorded payload; no model is called to summarize it.

Run and replay links support `?run=...&view=timeline&at=123`. Other observer views
remain available, with their existing replay controls.

```powershell
.\.venv\Scripts\python -m institution_lab validate configs\smoke.yaml
.\.venv\Scripts\python -m institution_lab run configs\smoke.yaml runs\my-smoke
.\.venv\Scripts\python -m institution_lab status runs\my-smoke
```

Use a fresh output directory for each run. An accepted proposal is not the same
as a completed project. Read the recorded status and final evaluation; `stalled`,
`deadline`, and `failed` are not successful completions.

The small fixture has three tickets; the reference fixture has 32. The latter is
the default in the four main presets:

| Config | Allocation | Institutions |
|---|---|---|
| `configs\central-minimal.yaml` | One LLM allocator | Disabled |
| `configs\central-institutional.yaml` | One LLM allocator | Enforced |
| `configs\distributed-minimal.yaml` | Workers claim work | Disabled |
| `configs\distributed-institutional.yaml` | Workers claim work | Enforced |

`configs\prompt-only.yaml` is an additional advisory condition, not a hidden
variant of either main baseline. Coordinator usage counts inside the population.

## Observe, replay, and report

```powershell
.\.venv\Scripts\python -m institution_lab observe runs\my-smoke --port 8765
.\.venv\Scripts\python -m institution_lab replay runs\my-smoke --port 8765
.\.venv\Scripts\python -m institution_lab report runs\my-smoke --out reports\my-smoke.json
```

Open `http://127.0.0.1:8765`. The observer is local and read-only; it does not
orchestrate agents. Its graph component is bundled with its license and makes
no CDN request. Historical state is reconstructed from recorded JSON patches.
Older development journals without those patches are explicitly labeled.

Replay makes no model calls. `rerun RECORDED_RUN NEW_OUTPUT` makes a new
stochastic run using frozen prompts/workload and immutable image IDs; it rejects
changed harness/plugin sources. Install the recorded dependency lock to match
the host environment. A rerun is not a recorded-response replay.

## Matched pilot and scale profiles

The additional `configs\evolution-20.yaml` profile uses 20 identities and eight
concurrent turns on a 64-ticket observability platform (20 foundation,
24 feature and 20 integration tickets; 106 dependency edges, seven layers,
134 protected black-box checks). Its session ceiling is explicitly null,
deadline 5400 seconds, and activation limit 100 per identity. The institutional
rules/levels and prompt sources are inherited unchanged. This is a separate
exploration, not another matched sample of the older scale profiles.

Run it into a fresh output directory and use `observe`, `watch`, and the CLI
controls below. Stopping a run is an intervention and is recorded as such.

```powershell
.\.venv\Scripts\python -m institution_lab matrix configs\pilot runs\my-matrix
.\.venv\Scripts\python -m institution_lab run configs\scale-10.yaml runs\my-ten
.\.venv\Scripts\python -m institution_lab run configs\scale-20.yaml runs\my-twenty
.\.venv\Scripts\python -m institution_lab compare runs\my-ten runs\my-twenty --out reports\scale.json
```

The pilot executes four conditions sequentially, with three total identities
and identical three-ticket inputs and limits. It is a functional pilot, not a
statistically powered study or proof of novelty.

The scale profiles use the 32-ticket fixture, four concurrent conversations,
and 10 or 20 identities. Their configured total credit ceilings both equal 1200
(10 x 120 or 20 x 60); realized usage still differs and must be reported.
Population is not concurrency. Activation is fair across eligible identities,
not a hidden task allocator.

Each worker can override the default model/reasoning configuration through
`model_overrides`. See `configs\mixed-smoke.yaml`. The default remains
`gpt-6.1-sol` with medium reasoning. Unknown models or unsupported reasoning
settings are rejected instead of substituted.

## Controls

### Terminal display and screenshots

`run`, `rerun`, and `matrix` automatically show a live Rich dashboard on an
interactive terminal. Redirected output retains its previous JSON form.
Use `--format live` to force the dashboard, or `--format json` for machine output.
`status` follows the same selection; `watch RUN` is a read-only journal viewer.
`watch RUN --once` prints a single current snapshot without making model calls.

Add `--capture NEW_DIRECTORY` to retain SVG terminal frames and a `frames.json`
provenance manifest. Frames are sampled at startup, communication, progress and
completion, not from invented agent activity. Each records its source run,
capture time and event cursor. Existing capture directories are never overwritten.
Rendering/capture failure is surfaced without cancelling the company's work.

To turn the captured frames into PNG screenshots using the existing browser tooling:

```powershell
Set-Location web
npm run capture:terminal -- ..\runs\my-org\terminal
```

This writes PNGs beside the SVGs and a separate screenshot manifest. It renders
the actual CLI frames with local fonts and no external network requests.
These are captures of terminal output, not photographs of a Windows terminal window.
Narrow/short terminals use a compact view and explicitly label any omitted agent rows.
Increase the terminal dimensions to see the full roster and ticket list.

### Run controls

```powershell
.\.venv\Scripts\python -m institution_lab control runs\my-smoke pause
.\.venv\Scripts\python -m institution_lab control runs\my-smoke resume
.\.venv\Scripts\python -m institution_lab control runs\my-smoke stop
.\.venv\Scripts\python -m institution_lab control runs\my-smoke unavailable --target worker-01
```

Controls act on the running controller, not an abandoned process from an earlier
run. Interventions are recorded and must not be mixed into clean comparisons.
Rules and prompts cannot be edited mid-run through these controls.

Use `run CONFIG OUTPUT --paused` to begin at a checkpoint. Pausing waits for
agent turns and unprivileged coding processes to settle. The global deadline
still applies while paused. Terminal runs reject new control requests.

Windows runs hold a temporary, thread-scoped system-awake request and restore
the previous state on exit; no persistent power setting is changed. Manual
suspension or host failure can still delay a local controller. Inspect elapsed
time and errors rather than assuming real-time enforcement.

Repeatable real-runtime functional probes:

```powershell
.\.venv\Scripts\python -m institution_lab check-controls configs\smoke.yaml runs\control-check
.\.venv\Scripts\python -m institution_lab check-recovery configs\smoke.yaml runs\recovery-check
.\.venv\Scripts\python -m institution_lab check-outage configs\outage-smoke.yaml runs\outage-check
```

These are explicitly intervened validation runs, not clean research samples.

## Rules and extension

Configuration inheritance replaces lists and recursively merges mappings.
Paths resolve relative to the file that declares them.
Unknown configuration keys and unsupported plugins fail explicitly.

The built-in rule plugins are `capabilities`, `delegation`, `review_gate`,
`performance`, and `promotion`. Disabling a restriction does not remove its
underlying operation: for example, minimal workers may still delegate.
Protected evaluation always measures output; a configured gate decides whether
failed checks prevent integration.

Custom trusted rules use `package.module:Factory`. A factory receives the resolved
configuration and its parameter mapping, and returns a subclass of
`institution_lab.institution.rules.Rule`. `decide` returns structured decisions;
`completion` may propose supported score/level effects. Do not mutate facts or
perform I/O in a rule. Conflicting effects and unexpected plugin failures are
errors, never permission to fall back to a permissive policy.

Only active mechanisms are described as active institutions. Minimal conditions
do not inherit phantom restrictions from an inactive level-reference catalog.

The first workload adapter intentionally targets Python operation modules,
not arbitrary enterprise repositories or arbitrary programming languages.

## Evidence and boundaries

Each run freezes resolved configuration, image IDs, templates/rendered prompts,
workload inputs, source hashes/source snapshot, versions, event history,
proposals, exact diffs, and evaluations. Private worker notes are not published
as project source. Runtime recordings expose what the SDK exposes, not hidden
reasoning.

The real compatibility probe in `runs\part1-probe-007` verified a model reply,
coding execution, a harness callback, UID separation, protected-file denial,
and blocked direct egress. This is bounded evidence, not universal containment.

Development runs are retained, including permission/setup failures and stalled
companies. In particular, a native editor permission issue was detected by the
real runs and repaired at workspace seeding; it is not an institutional result.

The functional suite exercises real SQLite transactions and Docker-backed
workspace/evaluation/integration behavior, including I/O retry idempotency.
Scripted test actors are never presented as real-agent research observations.
There are focused functional tests, a headless-browser scenario, and separate
real-runtime validation commands. See the validation report for recorded results.

## Validation commands

```powershell
.\.venv\Scripts\python -m pytest -q
.\.venv\Scripts\python -m ruff check src tests
.\.venv\Scripts\python -m pyright
Set-Location web
npm install --ignore-scripts
npm run build
npm run test:functional -- http://127.0.0.1:8765
```

The browser check uses an isolated headless Edge instance, exercises run selection,
agent activity, outputs, files, communications, configurations and replay,
and closes it afterwards. It does not use a personal browser
profile. The browser package is a development dependency, not part of the Python
agent implementation.

The React workspace is built with Astryx, Vite and TypeScript. `npm run build`
bundles the application into the Python observer's assets directory. The neutral
theme is imported without its optional remote-font stylesheet; Manrope and DM Sans
are bundled locally with their licenses. The UI requires no CDN. The Python server does not need Node
at runtime.

The interface uses ivory/ink surfaces with amber accents and a matching dark
theme. The header theme button switches modes; the selected mode is retained
in the page URL. Branding is `eusociality`; the Python module, CLI commands and
existing run metadata retain their original names for compatibility.

Each new company retains read-only private-workspace checkpoints at startup,
after agent turns and before shutdown. They exclude credentials/environment
files, symlinks, Git internals and dependency/cache directories; file/total capture
limits and exclusions are recorded. Captures are observations, not live mounts
or additional agent instructions. Older runs can still expose published code
and proposal snapshots without claiming private captures exist.

Work began on November 5, before the advertised November 6-8 sprint. Disclose
pre-start design/code as preexisting and use source snapshots, run timestamps,
and the official event start to distinguish eligible work.
