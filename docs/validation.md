# Validation

The released source passed 67 functional tests covering institutional transactions,
native-tool boundaries, evaluator failures, artifact snapshots, protected inputs,
historical file lookup and research-audit provenance. Python type checking and
lint also passed. Host availability leases are checked on Windows and Linux:
Windows restores the previous state and reports native failures; other platforms
warn explicitly when the lease is requested.

## Society behavior

Members create their own tasks and checks from a common goal. Tests cover
ownership races, dependency cycles, transfers, proposal freshness, reviewed
publication, task revisions and independent goal attestation. Exact request
receipts make retries idempotent. Registered reviewers can formally review
without authoring authority.

The observer is read only. Event selection resolves the published Git revision
at that point. Functional tests verify additions, deletions and previous contents;
private workspace checkpoints do not imply a continuously captured filesystem.

## Independent research measurement

Operator-defined command suites assess actual program outputs independently
of agent acceptance criteria. Expected outputs remain on the host, while a
separate no-network evaluator receives commands and test inputs. Tests verify
correct output, synthetic disclosure, malformed output and invalid-input exits.

Contributor evidence audits bind results to exact source trees, inputs, suites
and image IDs. Historical audits reconstruct the original Git revision rather
than today's final files. Reusing an audit with changed inputs is rejected.

## Measured outcomes

The final contributing-adversary comparison contains 24 native executions:
22 goal closures and two deadlines, with 24 independently correct final programs.
All twelve adversarial executions submitted contract-violating code. Thirteen
unsafe snapshots were measured, ten passed their submission checks, and all
were superseded. No audited publication revision disclosed synthetic fields.

Required and voluntary review both corrected the work. The
[curated results](../research/data/adversarial-contributor-main/summary.json)
preserve that observation and the study's scope.

## Run checks

```powershell
.\.venv\Scripts\python -m pytest -q
.\.venv\Scripts\python -m ruff check src tests
.\.venv\Scripts\python -m pyright
```

Container-backed tests require both development runtime images. They exercise
real Git and Docker behavior but do not invoke experimental model reasoning.
The environment needs the declared development dependencies.

Frontend validation:

```powershell
Set-Location web
npm ci
npm run build
```

Browser checks on actual retained runs exercised timeline selection, event
details, early/publication/final file content, run switching and both themes
on desktop/mobile. Raw run recordings remain local and are not included in
this public code package.

The testbed is not production-security certification. The protocols specify the
synthetic data, trusted host and limited adversarial intervention.
