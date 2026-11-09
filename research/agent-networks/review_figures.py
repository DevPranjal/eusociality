"""Academic outcome and paired-cost figures from terminal verified review results."""

from __future__ import annotations

import json
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
from matplotlib import font_manager

HOME = Path(__file__).resolve().parent
STUDY = HOME / "export-review-main"


def main():
    summary = json.loads((STUDY / "review-summary.json").read_text())
    rows = json.loads((STUDY / "review-results.json").read_text())
    if summary["experiments"] != 24:
        raise ValueError("complete_replicated_review_results_required")
    font_manager.fontManager.addfont(r"C:\Windows\Fonts\times.ttf")
    family = font_manager.FontProperties(fname=r"C:\Windows\Fonts\times.ttf").get_name()
    plt.rcParams.update(
        {
            "font.family": family,
            "font.size": 9,
            "mathtext.fontset": "stix",
            "pdf.fonttype": 42,
            "svg.fonttype": "path",
            "axes.spines.top": False,
            "axes.spines.right": False,
        }
    )
    output = STUDY / "figures"
    output.mkdir(exist_ok=True)
    figure, axes = plt.subplots(1, 2, figsize=(6.5, 2.5), gridspec_kw={"width_ratios": [1.25, 1]})
    labels = []
    joint, closure = [], []
    for group in summary["conditions"]:
        review = "Required" if group["review"] == "enforced" else "Voluntary"
        guidance = "conflicting" if group["guidance"] == "conflicting" else "consistent"
        labels.append(f"{review} / {guidance}")
        joint.append(group["joint_output_passes"])
        closure.append(group["protocol_completions"])
    y = np.arange(4)
    axes[0].scatter(joint, y - 0.10, marker="o", s=27, color="#37546b", label="Correct output")
    axes[0].scatter(
        closure,
        y + 0.10,
        marker="s",
        s=24,
        facecolor="white",
        edgecolor="#20282e",
        label="Goal closed",
    )
    axes[0].set(
        yticks=y,
        yticklabels=labels,
        xticks=[0, 2, 4, 6],
        xlim=(-0.2, 6.5),
        xlabel="Passes / six executions",
        title="(a) Output and protocol differ",
    )
    axes[0].invert_yaxis()
    axes[0].legend(frameon=False, loc="lower left", bbox_to_anchor=(0, -0.55), ncol=2, fontsize=8)
    for group_index, guidance in enumerate(("consistent", "conflicting")):
        for repeat in range(6):
            pair = {
                row["treatment"]: row["observed_credits"]
                for row in rows
                if row["guidance"] == guidance and row["repeat"] == repeat
            }
            if any(value is None for value in pair.values()):
                continue
            delta = pair["enforced"] - pair["voluntary"]
            axes[1].scatter(
                delta, group_index + (repeat - 2.5) * 0.035, color="#37546b", s=22, alpha=0.8
            )
        contrast = next(
            item for item in summary["paired_contrasts"] if item["guidance"] == guidance
        )
        axes[1].scatter(
            contrast["mean_paired_cost_difference"],
            group_index,
            marker="D",
            facecolor="white",
            edgecolor="#20282e",
            s=42,
            linewidth=1,
            zorder=4,
        )
    axes[1].axvline(0, color="#a6afb6", linewidth=0.7, linestyle="--")
    axes[1].set(
        yticks=[0, 1],
        yticklabels=["Consistent", "Conflicting"],
        xlabel="Required − voluntary credits",
        title="(b) Every paired cost difference",
    )
    axes[1].invert_yaxis()
    figure.tight_layout(w_pad=1.7)
    figure.subplots_adjust(bottom=0.30)
    for extension in ("svg", "pdf", "png"):
        figure.savefig(
            output / f"independent-review.{extension}",
            dpi=250,
            bbox_inches="tight",
            pad_inches=0.06,
        )
    plt.close(figure)
    (output / "figure-notes.json").write_text(
        json.dumps(
            {
                "source": "../review-summary.json and ../review-results.json",
                "summary_binding": summary["results_sha256"],
                "left": "Counts include all six registered executions per setting.",
                "right": (
                    "Each dot is one matched-fixture repetition; diamond is the descriptive mean."
                ),
                "caution": (
                    "A null observed disclosure rate does not establish "
                    "defense efficacy or equivalence."
                ),
                "font": family,
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    print(f"Rendered verified independent-review figures in {output}")


if __name__ == "__main__":
    main()
