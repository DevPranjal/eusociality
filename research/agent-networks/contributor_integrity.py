"""Verify replicated outcomes, journal coverage and every recorded audit binding."""

from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
from pathlib import Path

from contributor_analysis import source_digest

HOME = Path(__file__).resolve().parent
ROOT = HOME.parents[1]


def main(study):
    manifest = json.loads((study / "manifest.json").read_text())
    results = json.loads((study / "review-results.json").read_text())
    evidence = json.loads((study / "contributor-evidence.json").read_text())
    if not (len(results) == len(evidence) == len(manifest["primary_trials"]) == 24):
        raise ValueError("replicated_contributor_denominator_mismatch")
    identifiers = {trial["id"] for trial in manifest["primary_trials"]}
    if {row["id"] for row in results} != identifiers or {
        row["id"] for row in evidence
    } != identifiers:
        raise ValueError("replicated_contributor_identifier_mismatch")
    report = []
    for trial in manifest["primary_trials"]:
        row = next(item for item in results if item["id"] == trial["id"])
        record = next(item for item in evidence if item["id"] == trial["id"])
        run = ROOT / "runs" / "agent-networks" / study.name / trial["id"]
        count = size = 0
        digest = hashlib.sha256()
        with sqlite3.connect(
            (run / "events.sqlite").resolve().as_uri() + "?mode=ro", uri=True
        ) as connection:
            for seq, raw in connection.execute("SELECT seq,body FROM events ORDER BY seq"):
                body = raw.encode()
                digest.update(seq.to_bytes(8, "big"))
                digest.update(len(body).to_bytes(8, "big"))
                digest.update(body)
                json.loads(raw)
                count += 1
                size += len(body)
            state = json.loads(
                connection.execute("SELECT body FROM state WHERE id=1").fetchone()[0]
            )
        if state["status"] in {"running", "ready", "paused", "draining"}:
            raise ValueError("nonterminal_contributor_execution")
        if (
            count != row["events"]
            or size != row["event_payload_bytes"]
            or digest.hexdigest() != row["body_sequence_sha256"]
        ):
            raise ValueError("contributor_journal_accounting_mismatch")
        for filename, recorded in (
            ("summary.json", record["final_summary_sha256"]),
            ("outcome-suite.json", record["frozen_suite_sha256"]),
            ("resolved.json", record["frozen_config_sha256"]),
        ):
            if hashlib.sha256((run / filename).read_bytes()).hexdigest() != recorded:
                raise ValueError("contributor_frozen_evidence_hash_mismatch")
        for candidate in record["actual_contributor_submissions"]:
            if not candidate.get("available"):
                continue
            artifact = state["proposals"][candidate["proposal"]]["artifact"]
            cache = (
                study
                / "audited-outcomes"
                / trial["id"]
                / ("candidate-" + candidate["proposal"] + ".json")
            )
            cached = json.loads(cache.read_text())
            if source_digest(run / "artifacts" / artifact) != cached["key"]["source_tree_sha256"]:
                raise ValueError("candidate_audit_source_changed")
            if cached["result"] != candidate["outcome"]:
                raise ValueError("candidate_audit_result_mismatch")
        for revision in record["publication_revision_audits"]:
            if not revision.get("available"):
                continue
            cache = (
                study
                / "audited-outcomes"
                / trial["id"]
                / ("published-" + revision["head"] + ".json")
            )
            cached = json.loads(cache.read_text())
            source = study / "historical-sources" / trial["id"] / revision["head"]
            if source_digest(source) != cached["key"]["source_tree_sha256"]:
                raise ValueError("publication_audit_source_changed")
            if cached["result"] != revision["outcome"]:
                raise ValueError("publication_audit_result_mismatch")
        report.append(
            {
                "id": trial["id"],
                "events": count,
                "body_bytes": size,
                "body_sequence_sha256": digest.hexdigest(),
            }
        )
    result = {
        "experiments_verified": 24,
        "events_verified": sum(row["events"] for row in report),
        "body_bytes_verified": sum(row["body_bytes"] for row in report),
        "journals": report,
        "manifest_sha256": hashlib.sha256((study / "manifest.json").read_bytes()).hexdigest(),
        "results_sha256": hashlib.sha256((study / "review-results.json").read_bytes()).hexdigest(),
        "contributor_evidence_sha256": hashlib.sha256(
            (study / "contributor-evidence.json").read_bytes()
        ).hexdigest(),
        "scope": (
            "Evidence-integrity checks, not independent factual "
            "or production-security certification."
        ),
    }
    (study / "evidence-validation.json").write_text(
        json.dumps(result, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps({key: value for key, value in result.items() if key != "journals"}, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--study", default="adversarial-contributor-main")
    main(HOME / parser.parse_args().study)
