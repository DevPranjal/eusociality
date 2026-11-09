"""Validate and summarize curated public data without reading native session logs."""

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent / "data" / "adversarial-contributor-main"


def main():
    rows = json.loads((ROOT / "trials.json").read_text(encoding="utf-8"))
    summary = json.loads((ROOT / "summary.json").read_text(encoding="utf-8"))
    if len(rows) != summary["experiments"] or len({row["id"] for row in rows}) != 24:
        raise ValueError("public_trial_denominator_mismatch")
    for condition in summary["conditions"]:
        group = [
            row
            for row in rows
            if row["treatment"] == condition["review"]
            and ("adversarial" if row["guidance"] == "conflicting" else "benign")
            == condition["instruction"]
        ]
        if len(group) != 6:
            raise ValueError("public_condition_denominator_mismatch")
        if (
            sum(row["joint_output_success"] is True for row in group)
            != condition["joint_final_output_passes"]
        ):
            raise ValueError("public_output_summary_mismatch")
        if sum(row["status"] == "completed" for row in group) != condition["goal_completions"]:
            raise ValueError("public_closure_summary_mismatch")
        print(
            f"{condition['review']:9} / {condition['instruction']:11}: "
            f"{condition['unsafe_candidate_experiments']}/6 unsafe candidate runs; "
            f"{condition['unsafe_publication_experiments']}/6 unsafe releases; "
            f"{condition['joint_final_output_passes']}/6 correct final outputs; "
            f"{condition['goal_completions']}/6 goal closures"
        )


if __name__ == "__main__":
    main()
