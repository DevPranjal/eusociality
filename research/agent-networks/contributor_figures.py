"""Academic candidate/publication plot bound to the complete replicated evidence."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib import font_manager
from matplotlib.patches import FancyArrowPatch, Rectangle

HOME = Path(__file__).resolve().parent


def main():
    study = HOME / "adversarial-contributor-main"
    summary_path = study / "contributor-summary.json"
    summary = json.loads(summary_path.read_text())
    if summary["experiments"] != 24:
        raise ValueError("full_contributor_evidence_required")
    font_manager.fontManager.addfont(r"C:\Windows\Fonts\times.ttf")
    font = font_manager.FontProperties(fname=r"C:\Windows\Fonts\times.ttf").get_name()
    plt.rcParams.update(
        {
            "font.family": font,
            "font.size": 9,
            "mathtext.fontset": "stix",
            "pdf.fonttype": 42,
            "svg.fonttype": "path",
            "axes.spines.top": False,
            "axes.spines.right": False,
        }
    )
    figure, axes = plt.subplots(1, 2, figsize=(6.5, 2.35), sharey=True)
    labels = [
        "Voluntary / benign",
        "Voluntary / adversarial",
        "Required / benign",
        "Required / adversarial",
    ]
    for i, condition in enumerate(summary["conditions"]):
        axes[0].scatter(
            condition["unsafe_candidate_experiments"], i, s=30, color="#37546b", marker="o"
        )
        axes[0].scatter(
            condition["unsafe_publication_experiments"],
            i + 0.12,
            s=28,
            edgecolor="#20282e",
            facecolor="white",
            marker="s",
        )
        axes[1].scatter(
            condition["joint_final_output_passes"], i, s=30, color="#37546b", marker="o"
        )
        axes[1].scatter(
            condition["goal_completions"],
            i + 0.12,
            s=28,
            edgecolor="#20282e",
            facecolor="white",
            marker="s",
        )
    axes[0].set(yticks=range(4), yticklabels=labels, title="(a) Unsafe candidate ≠ unsafe release")
    axes[1].set(title="(b) Correct output ≠ goal closure")
    for axis in axes:
        axis.set(xlim=(-0.25, 6.35), xticks=[0, 2, 4, 6], xlabel="Executions / six per setting")
        axis.grid(axis="x", color="#d8dde0", linewidth=0.45)
        axis.set_axisbelow(True)
    axes[0].invert_yaxis()
    axes[0].scatter([], [], color="#37546b", marker="o", label="Unsafe candidate")
    axes[0].scatter(
        [], [], edgecolor="#20282e", facecolor="white", marker="s", label="Unsafe release"
    )
    axes[1].scatter([], [], color="#37546b", marker="o", label="Correct final output")
    axes[1].scatter([], [], edgecolor="#20282e", facecolor="white", marker="s", label="Goal closed")
    for axis in axes:
        axis.legend(
            frameon=False, fontsize=8, loc="upper center", bbox_to_anchor=(0.5, -0.3), ncol=2
        )
    figure.tight_layout(w_pad=1.5)
    out = study / "figures"
    out.mkdir(exist_ok=True)
    for extension in ("pdf", "svg", "png"):
        figure.savefig(
            out / f"candidate-publication.{extension}",
            dpi=250,
            bbox_inches="tight",
            pad_inches=0.05,
        )
    plt.close(figure)
    process, ax = plt.subplots(figsize=(6.5, 2.5))
    ax.set(xlim=(0, 1), ylim=(0, 1))
    ax.axis("off")
    stages = [
        (0.01, "13", "unsafe submitted\nsnapshots"),
        (0.36, "12 + 1", "formal rejections +\nearlier resubmission"),
        (0.71, "0", "unsafe publication\nrevisions observed"),
    ]
    for x, value, description in stages:
        ax.add_patch(
            Rectangle((x, 0.46), 0.28, 0.38, edgecolor="#a6afb6", facecolor="white", linewidth=0.65)
        )
        ax.text(x + 0.14, 0.70, value, ha="center", va="center", fontsize=20, color="#37546b")
        ax.text(x + 0.14, 0.54, description, ha="center", va="center", fontsize=9)
        if x < 0.70:
            ax.add_patch(
                FancyArrowPatch(
                    (x + 0.28, 0.65),
                    (x + 0.35, 0.65),
                    arrowstyle="-|>",
                    mutation_scale=8,
                    color="#737b83",
                    linewidth=0.7,
                )
            )
    ax.text(0.01, 0.95, "Observed correction path", fontsize=10.5, fontweight="bold")
    ax.text(
        0.15,
        0.37,
        "10 of 13 had passed\nsubmission checks",
        fontsize=9,
        color="#737b83",
        ha="center",
        va="center",
    )
    ax.text(
        0.5,
        0.27,
        "All 13 superseded; 24 of 24 final outputs passed independent tests",
        fontsize=9,
        color="#37546b",
        ha="center",
    )
    ax.plot([0, 1], [0.18, 0.18], color="#a6afb6", linewidth=0.6)
    ax.text(
        0.01,
        0.08,
        "Unsafe-candidate runs: voluntary 6/6, required 6/6. Unsafe releases: 0/6 in each.",
        fontsize=8.5,
        va="center",
    )
    process.tight_layout()
    for extension in ("pdf", "svg", "png"):
        process.savefig(
            out / f"publication-path.{extension}", dpi=250, bbox_inches="tight", pad_inches=0.05
        )
    plt.close(process)
    (out / "figure-provenance.json").write_text(
        json.dumps(
            {
                "summary_sha256": hashlib.sha256(summary_path.read_bytes()).hexdigest(),
                "candidate_evidence_sha256": summary["candidate_revision_evidence_sha256"],
                "metrics_sha256": summary["metrics_sha256"],
                "font": font,
                "scope": "All registered executions retained; observed counts, not intent.",
                "interpretation": (
                    "Six runs per setting; no general compromise or equivalence claim."
                ),
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    print(f"Rendered original evidence-bound contributor figures in {out}")


if __name__ == "__main__":
    main()
