"""Terminal-only review comparison: integrity, separate outcomes and paired estimates."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import sqlite3
from collections import Counter
from pathlib import Path
from statistics import mean

HOME = Path(__file__).resolve().parent
ROOT = HOME.parents[1]
TERMINAL = {"completed", "deadline", "stalled", "failed", "cancelled"}


def interval(successes, count):
    z = 1.959963984540054
    p = successes / count
    divisor = 1 + z * z / count
    center = (p + z * z / (2 * count)) / divisor
    radius = z * math.sqrt(p * (1 - p) / count + z * z / (4 * count * count)) / divisor
    return [max(0, center - radius), min(1, center + radius)]


def validate(study):
    manifest = json.loads((study / "manifest.json").read_text())
    rows = json.loads((study / "review-results.json").read_text())
    if {row["id"] for row in rows} != {row["id"] for row in manifest["primary_trials"]}:
        raise ValueError("review_experiment_identifiers_differ_from_manifest")
    if len(rows) != len(manifest["primary_trials"]):
        raise ValueError("duplicate_or_missing_review_experiments")
    coverage = []
    for row in rows:
        if row["status"] not in TERMINAL:
            raise ValueError("review_comparison_is_not_terminal:" + row["id"])
        run = ROOT / "runs" / "agent-networks" / study.name / row["id"]
        summary_path = run / "summary.json"
        summary = json.loads(summary_path.read_text()) if summary_path.exists() else None
        if summary is None and not (
            row["status"] == "failed"
            and row.get("kinds", {}).get("run_end") == 1
            and row.get("observed_credits") is None
        ):
            raise ValueError("terminal_summary_missing:" + row["id"])
        if summary is None:
            summary = {"status": "failed", "independent_outcome": None}
        if summary["status"] != row["status"]:
            raise ValueError("summary_status_disagrees")
        audit = summary.get("independent_outcome")
        if audit != row.get("independent_outcome"):
            raise ValueError("independent_outcome_does_not_match_summary")
        digest = hashlib.sha256()
        count = size = 0
        with sqlite3.connect(
            (run / "events.sqlite").resolve().as_uri() + "?mode=ro", uri=True
        ) as connection:
            for seq, body in connection.execute("SELECT seq,body FROM events ORDER BY seq"):
                raw = body.encode()
                digest.update(seq.to_bytes(8, "big"))
                digest.update(len(raw).to_bytes(8, "big"))
                digest.update(raw)
                json.loads(body)
                count += 1
                size += len(raw)
        if (
            count != row["events"]
            or size != row["event_payload_bytes"]
            or digest.hexdigest() != row["body_sequence_sha256"]
        ):
            raise ValueError("review_journal_coverage_mismatch:" + row["id"])
        coverage.append(
            {
                "id": row["id"],
                "rows": count,
                "body_bytes": size,
                "body_sequence_sha256": digest.hexdigest(),
            }
        )
    return manifest, rows, coverage


def analyze(study):
    manifest, rows, coverage = validate(study)
    expected = manifest["repetitions_per_setting"]
    conditions = []
    for treatment in ("voluntary", "enforced"):
        for guidance in ("consistent", "conflicting"):
            group = [
                row for row in rows if row["treatment"] == treatment and row["guidance"] == guidance
            ]
            if len(group) != expected or {row["repeat"] for row in group} != set(range(expected)):
                raise ValueError("replicated_condition_incomplete")
            joint = sum(row.get("joint_output_success") is True for row in group)
            joint_observed = sum(isinstance(row.get("joint_output_success"), bool) for row in group)
            statuses = dict(Counter(row["status"] for row in group))
            credits = [
                row["observed_credits"] for row in group if row["observed_credits"] is not None
            ]
            if not credits:
                raise ValueError("no_observed_cost_in_review_condition")
            conditions.append(
                {
                    "review": treatment,
                    "guidance": guidance,
                    "experiments": len(group),
                    "protocol_completions": statuses.get("completed", 0),
                    "statuses": statuses,
                    "joint_output_passes": joint,
                    "joint_rate_interval_95": interval(joint, len(group)),
                    "joint_output_observations": joint_observed,
                    "joint_observed_interval_95": interval(joint, joint_observed)
                    if joint_observed else None,
                    "registered_rate_interpretation": "Registered-denominator success lower bound; "
                    "unknown startup outcome is not classified as incorrect output.",
                    "functional_output_passes": sum(
                        row.get("functionality_passed") is True for row in group
                    ),
                    "correct_outputs_without_protocol_closure": sum(
                        row.get("joint_output_success") is True and row["status"] != "completed"
                        for row in group
                    ),
                    "protocol_closure_with_incorrect_outputs": sum(
                        row["status"] == "completed" and row.get("joint_output_success") is False
                        for row in group
                    ),
                    "observed_disclosures": sum(
                        row.get("disclosure_observed") is True for row in group
                    ),
                    "unknown_disclosure_outcomes": sum(
                        row.get("disclosure_observed") is None for row in group
                    ),
                    "mean_observed_credits": mean(credits),
                    "cost_observations": len(credits),
                    "median_credits": sorted(credits)[len(credits) // 2]
                    if len(credits) % 2
                    else sum(sorted(credits)[len(credits) // 2 - 1 : len(credits) // 2 + 1]) / 2,
                    "guidance_observed_experiments": sum(
                        bool(row.get("peer_guidance_observed_by")) for row in group
                    ),
                    "guidance_preceded_first_candidate": sum(
                        row.get("guidance_delivered_before_first_submission") is True
                        for row in group
                    ),
                }
            )
    contrasts = []
    for guidance in ("consistent", "conflicting"):
        deltas = []
        discordance = Counter()
        for repeat in range(expected):
            pair = {
                row["treatment"]: row
                for row in rows
                if row["repeat"] == repeat and row["guidance"] == guidance
            }
            voluntary, enforced = pair["voluntary"], pair["enforced"]
            if (
                enforced.get("observed_credits") is not None
                and voluntary.get("observed_credits") is not None
            ):
                deltas.append(enforced["observed_credits"] - voluntary["observed_credits"])
            difference = (
                enforced.get("joint_output_success"),
                voluntary.get("joint_output_success"),
            )
            discordance[str(difference)] += 1
        contrasts.append(
            {
                "guidance": guidance,
                "paired_cost_differences_enforced_minus_voluntary": deltas,
                "mean_paired_cost_difference": mean(deltas),
                "paired_cost_observations": len(deltas),
                "missing_cost_pairs": expected - len(deltas),
                "joint_success_pairs_enforced_voluntary": dict(discordance),
                "interpretation": "Matched repetition labels hold input fixtures fixed; "
                "native stochastic sampling and temporal/provider effects remain.",
            }
        )
    result = {
        "study": study.name,
        "experiments": len(rows),
        "conditions": conditions,
        "paired_contrasts": contrasts,
        "observed_credits": sum(row["observed_credits"] or 0 for row in rows),
        "credit_observations": sum(row["observed_credits"] is not None for row in rows),
        "coverage": coverage,
        "results_sha256": hashlib.sha256((study / "review-results.json").read_bytes()).hexdigest(),
        "measurements": "Independent frozen CLI outputs, not society self-attestation.",
        "protocol_output_agreement": {
            "correct_output_and_closed": sum(
                row["status"] == "completed" and row.get("joint_output_success") is True
                for row in rows
            ),
            "correct_output_but_not_closed": sum(
                row["status"] != "completed" and row.get("joint_output_success") is True
                for row in rows
            ),
            "closed_but_incorrect_output": sum(
                row["status"] == "completed" and row.get("joint_output_success") is False
                for row in rows
            ),
            "unknown_output": sum(row.get("joint_output_success") is None for row in rows),
        },
        "limitations": f"{expected} repetition(s) per setting, one workload/model setting, "
        "message-only intervention. "
        "A null release outcome does not prove defense effectiveness or equivalence.",
    }
    (study / "review-summary.json").write_text(
        json.dumps(result, indent=2) + "\n", encoding="utf-8"
    )
    lines = [
        "# Independent-output review comparison",
        "",
        "All registered experiments are included. Goal completion and output correctness "
        "are different measurements; unknown outcomes are not safe zeros.",
        "",
        "| Review | Guidance | Goal completion | Joint output pass | Disclosure | Mean credits |",
        "|---|---|---:|---:|---:|---:|",
    ]
    for group in conditions:
        n = group["experiments"]
        lines.append(
            f"| {group['review']} | {group['guidance']} | "
            f"{group['protocol_completions']}/{n} | {group['joint_output_passes']}/{n} | "
            f"{group['observed_disclosures']} ({group['unknown_disclosure_outcomes']} unknown) | "
            f"{group['mean_observed_credits']:.1f} |"
        )
    lines += [
        "",
        "## Interpretation",
        "",
        result["limitations"],
        "",
        f"Exact result-file SHA-256: `{result['results_sha256']}`.",
        "",
        "Paired cost differences and event/byte coverage are retained in review-summary.json.",
        "",
        "Goal-state/output agreement is recorded separately. A society that hits its deadline "
        "may still have independently correct published output; a declaration of completion "
        "does not substitute for those checks.",
    ]
    (study / "REPORT.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(json.dumps({"conditions": conditions, "paired_contrasts": contrasts}, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--study", default="export-review-main")
    args = parser.parse_args()
    analyze(HOME / args.study)
