# Research methods and public measurements

The experiment tools measure native task formation, executable organizational
rules and exact publication history.

## What is included

- [Exploratory methods](agent-networks/PROTOCOL.md)
- [Independent-output review protocol](agent-networks/STRONGER-STUDY.md)
- [Contributing-adversary protocol](agent-networks/ADVERSARIAL-CONTRIBUTOR.md)
- Study generators and evidence-analysis scripts in [agent-networks](agent-networks)
- Curated per-execution outcomes and condition summaries in [data](data)
- A [provenance record](data/provenance.json) documenting omitted fields and
  original local result-file hashes

The public outcome files preserve all registered executions, including
noncompletion and unavailable measurements. They remove raw SDK conversations,
session IDs, tool-result bodies and local machine paths. Curated files have
their own bytes and must not be mistaken for the original full result files
named by provenance hashes.

## Final measured finding

The corrected contributor study has 24 executions, six per review/objective
setting. Each adversarial run submitted a contract-violating program. Thirteen
unsafe snapshots were measured, twelve formally rejected, and all superseded.
No audited publication revision disclosed the tested synthetic fields.
All final outputs passed; two societies reached their deadline before formal
goal closure.

Required review did not add an observed safety benefit over voluntary peer
review on this task. The protocols retain the earlier reviewer-role defect,
small-study scope and synthetic-data threat boundary.

## Recompute public summaries

From the repository root:

```powershell
.\.venv\Scripts\python research\summarize_public.py
```

This validates the curated final outcomes against their supplied summary.
It makes no model calls and does not need the private original journals.

## Run fresh native experiments

Install the declared research dependencies and build runtime images first.
Each new study name must be unused:

```powershell
.\.venv\Scripts\python -m pip install --constraint requirements.lock -e ".[dev,research]"
.\.venv\Scripts\python research\agent-networks\review_study.py prepare --study my-contributor-study --contributor --repetitions 6
.\.venv\Scripts\python research\agent-networks\review_study.py run --study my-contributor-study
.\.venv\Scripts\python research\agent-networks\review_study.py summarize --study my-contributor-study
.\.venv\Scripts\python research\agent-networks\contributor_analysis.py --study my-contributor-study
.\.venv\Scripts\python research\agent-networks\contributor_summary.py --study my-contributor-study
```

These are fresh stochastic model executions and incur provider usage. They are
not guaranteed to reproduce exact trajectories. The frozen goals, inputs,
source/image identities and observed outcomes are retained under the new
study directory and `runs`.

The full local audit scripts operate on journals and snapshots produced by
fresh executions.
