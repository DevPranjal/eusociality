"""Validate final experiment denominators and exact journal/export provenance."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path

HOME = Path(__file__).resolve().parent
ROOT = HOME.parents[1]


def main():
    study = HOME / "pilot-v3"
    manifest = json.loads((study / "manifest.json").read_text())
    results = json.loads((study / "results.json").read_text())
    if len(results) != 42 or {row["id"] for row in results} != {
        trial["id"] for trial in manifest["primary_trials"]
    }:
        raise ValueError("primary_experiment_denominators_changed")
    checks = []
    event_total = byte_total = 0
    for row in results:
        run = ROOT / "runs" / "agent-networks" / "pilot-v3" / row["id"]
        if row["status"] in {"not_run", "running", "ready", "paused", "draining"}:
            raise ValueError(f"primary_experiment_not_terminal:{row['id']}")
        config = json.loads((run / "resolved.json").read_text())
        if config["goal"] != row["goal"] or config["population"] != row["population"]:
            raise ValueError("frozen_goal_or_population_changed")
        if config["runtime"]["max_ai_credits"] != 200:
            raise ValueError("native_session_credit_ceiling_changed")
        digest = hashlib.sha256()
        count = payload_bytes = 0
        with sqlite3.connect(
            (run / "events.sqlite").resolve().as_uri() + "?mode=ro", uri=True
        ) as connection:
            for seq, body in connection.execute("SELECT seq,body FROM events ORDER BY seq"):
                raw = body.encode("utf-8")
                digest.update(seq.to_bytes(8, "big"))
                digest.update(len(raw).to_bytes(8, "big"))
                digest.update(raw)
                json.loads(body)
                count += 1
                payload_bytes += len(raw)
            state = json.loads(
                connection.execute("SELECT body FROM state WHERE id=1").fetchone()[0]
            )
        if (
            count != row["events"]
            or payload_bytes != row["event_payload_bytes"]
            or digest.hexdigest() != row["body_sequence_sha256"]
            or state["status"] != row["status"]
        ):
            raise ValueError(f"journal_metrics_mismatch:{row['id']}")
        export = study / "analysis" / row["id"] / "full-events.jsonl"
        export_digest = hashlib.sha256()
        exported_count = 0
        with export.open("rb") as handle:
            for line in handle:
                export_digest.update(line)
                json.loads(line)
                exported_count += 1
        if exported_count != count:
            raise ValueError("full_export_coverage_mismatch")
        event_total += count
        byte_total += payload_bytes
        checks.append(
            {
                "id": row["id"],
                "status": row["status"],
                "events": count,
                "event_payload_bytes": payload_bytes,
                "body_sequence_sha256": digest.hexdigest(),
                "full_export_sha256": export_digest.hexdigest(),
                "interruption_retained": row["id"] == "coord-minimal-w2-r0",
            }
        )
    controls = json.loads((HOME / "positive-controls-v1" / "results.json").read_text())
    if len(controls) != 8:
        raise ValueError("authorized_control_denominator_changed")
    for row in controls:
        used = sum(pool["used"] for pool in row["resources"].values())
        expected = 4 if row["population"] == 1 else 8
        if row["status"] != "completed" or used != expected or row["aggregate_overshoot"] != 0:
            raise ValueError("authorized_use_control_failed")
    spread = json.loads((study / "spread-channel-evidence.json").read_text())
    if len(spread) != 18:
        raise ValueError("spread_evidence_denominator_changed")
    for row in spread:
        metrics = next(item for item in results if item["id"] == row["id"])
        if row["body_sequence_sha256"] != metrics["body_sequence_sha256"]:
            raise ValueError("spread_evidence_hash_mismatch")
    report = {
        "primary_experiments": 42,
        "authorized_use_controls": 8,
        "events_verified": event_total,
        "event_payload_bytes_verified": byte_total,
        "results_sha256": hashlib.sha256((study / "results.json").read_bytes()).hexdigest(),
        "checks": checks,
        "scope": (
            "Read-only evidence integrity and denominators, "
            "not independent semantic certification."
        ),
    }
    (study / "evidence-validation.json").write_text(
        json.dumps(report, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps({key: value for key, value in report.items() if key != "checks"}, indent=2))


if __name__ == "__main__":
    main()
