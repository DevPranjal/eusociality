"""Reproduce descriptive study figures and tables from retained trial metrics."""

from __future__ import annotations

import argparse
import csv
import json
import math
from collections import defaultdict
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
from matplotlib import font_manager

HOME = Path(__file__).resolve().parent
COLORS = {
    "central": "#536b87",
    "minimal": "#a16b43",
    "institutional": "#4e765f",
    "review": "#536b87",
    "literal_filter": "#4e765f",
}


def wilson(success: int, count: int) -> tuple[float, float]:
    if count == 0:
        return math.nan, math.nan
    z = 1.96
    p = success / count
    denominator = 1 + z * z / count
    centre = (p + z * z / (2 * count)) / denominator
    radius = z * math.sqrt(p * (1 - p) / count + z * z / (4 * count * count)) / denominator
    return max(0, centre - radius), min(1, centre + radius)


def save(figure, destination: Path, name: str):
    figure.tight_layout()
    for suffix in ("pdf", "svg", "png"):
        figure.savefig(destination / f"{name}.{suffix}", dpi=250, bbox_inches="tight")
    plt.close(figure)


def render(study: Path):
    rows = json.loads((study / "results.json").read_text())
    observed = [
        row
        for row in rows
        if row["status"] not in {"not_run", "running", "ready", "paused", "draining"}
    ]
    destination = study / "figures"
    destination.mkdir(exist_ok=True)
    font = Path(r"C:\Windows\Fonts\times.ttf")
    font_manager.fontManager.addfont(str(font))
    plt.rcParams.update(
        {
            "font.family": font_manager.FontProperties(fname=str(font)).get_name(),
            "font.size": 9,
            "mathtext.fontset": "stix",
            "axes.spines.top": False,
            "axes.spines.right": False,
            "axes.labelsize": 9,
            "legend.fontsize": 8.5,
            "pdf.fonttype": 42,
            "svg.fonttype": "path",
        }
    )
    groups = defaultdict(list)
    for row in observed:
        groups[(row["family"], row["treatment"], row.get("workers"), row.get("topology"))].append(
            row
        )
    summaries = []
    for key, values in sorted(groups.items(), key=lambda item: str(item[0])):
        family, treatment, workers, topology = key
        completed = sum(value["status"] == "completed" for value in values)
        lower, upper = wilson(completed, len(values))
        summaries.append(
            {
                "family": family,
                "treatment": treatment,
                "workers": workers,
                "topology": topology,
                "n": len(values),
                "completed": completed,
                "completion_rate": completed / len(values),
                "completion_wilson_lower": lower,
                "completion_wilson_upper": upper,
                "credits_mean": float(
                    np.mean(
                        [
                            value["observed_credits"]
                            for value in values
                            if value.get("observed_credits") is not None
                        ]
                    )
                )
                if any(value.get("observed_credits") is not None for value in values)
                else None,
                "aggregate_overshoot_runs": sum(
                    value.get("aggregate_overshoot", 0) > 0 for value in values
                ),
                "secondary_canary_release_runs": sum(
                    bool(value.get("canary_publication_actors")) for value in values
                ),
            }
        )
    (study / "group-summaries.json").write_text(json.dumps(summaries, indent=2) + "\n")
    with (study / "trial-table.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=[
                "id",
                "family",
                "treatment",
                "repeat",
                "population",
                "coding_capacity",
                "status",
                "elapsed_seconds",
                "observed_credits",
                "delivered_tasks",
                "distinct_authors",
                "message_hhi",
                "aggregate_overshoot",
            ],
            extrasaction="ignore",
        )
        writer.writeheader()
        writer.writerows(rows)
    figure, axes = plt.subplots(1, 2, figsize=(8, 3.1))
    for treatment in ("central", "minimal", "institutional"):
        for workers in (2, 5):
            values = groups.get(("coordination", treatment, workers, None), [])
            if not values:
                continue
            successes = sum(value["status"] == "completed" for value in values)
            mean = successes / len(values)
            lower, upper = wilson(successes, len(values))
            shift = {"central": -0.16, "minimal": 0, "institutional": 0.16}[treatment]
            axes[0].errorbar(
                workers + shift,
                mean,
                yerr=[[mean - lower], [upper - mean]],
                marker="o",
                capsize=4,
                color=COLORS[treatment],
                label=treatment if workers == 2 else None,
            )
            for value in values:
                if value.get("observed_credits") is not None:
                    axes[1].scatter(
                        workers + shift, value["observed_credits"], color=COLORS[treatment], s=35
                    )
    axes[0].set(
        xlabel="Matched coding identities",
        ylabel="Completed / observed trials",
        xticks=[2, 5],
        ylim=(-0.04, 1.08),
        title="Completion (95% Wilson interval)",
    )
    axes[1].set(
        xlabel="Matched coding identities",
        ylabel="Observed society AI credits",
        xticks=[2, 5],
        title="Every observed trial",
    )
    handles, labels = axes[0].get_legend_handles_labels()
    if handles:
        figure.legend(
            handles,
            labels,
            frameon=False,
            loc="lower center",
            bbox_to_anchor=(0.5, -0.04),
            ncol=3,
        )
    save(figure, destination, "coordination")
    figure, axis = plt.subplots(figsize=(6, 3))
    labels, amounts, colours = [], [], []
    for row in sorted((r for r in observed if r["family"] == "composition"), key=lambda r: r["id"]):
        labels.append(f"{row['treatment']}\nn={row['population']}, r={row['repeat']}")
        amounts.append(sum(pool["used"] for pool in row.get("resources", {}).values()))
        colours.append("#a16b43" if row["treatment"] == "per_agent" else "#4e765f")
    if labels:
        axis.bar(range(len(labels)), amounts, color=colours, width=0.65)
        axis.set_xticks(range(len(labels)), labels, fontsize=8)
    axis.axhline(10, color="#555555", linestyle="--", linewidth=1, label="Parent capacity")
    axis.set(
        ylabel="Actually reserved allocation units",
        title="Composition under a shared parent budget",
    )
    axis.legend(frameon=False)
    save(figure, destination, "resource-composition")
    figure, axes = plt.subplots(1, 2, figsize=(8, 3))
    positions = {"complete": 0, "ring": 1, "star": 2}
    for defense in ("minimal", "review", "literal_filter"):
        shift = {"minimal": -0.18, "review": 0, "literal_filter": 0.18}[defense]
        for shape in positions:
            values = groups.get(("spread", defense, None, shape), [])
            if not values:
                continue
            for value in values:
                honest = value["population"] - 1
                x = positions[shape] + shift
                axes[0].scatter(
                    x, len(value.get("canary_observed", {})) / honest, color=COLORS[defense], s=36
                )
                axes[1].scatter(
                    x,
                    len(value.get("canary_publication_actors", [])) / honest,
                    color=COLORS[defense],
                    s=36,
                )
    for axis, title in zip(axes, ("Observed exact-code exposure", "Actual public code release")):
        axis.set(
            xticks=[0, 1, 2],
            xticklabels=["Complete", "Ring", "Star"],
            ylabel="Fraction of nonchallenger identities",
            ylim=(-0.04, 1.04),
            title=title,
        )
    save(figure, destination, "canary-spread")
    figure, axes = plt.subplots(1, 3, figsize=(8, 2.5))
    points = np.array(
        [(math.cos(t), math.sin(t)) for t in np.linspace(0, 2 * math.pi, 6, endpoint=False)]
    )
    for axis, shape in zip(axes, ("complete", "ring", "star")):
        edges = (
            [(i, j) for i in range(6) for j in range(i + 1, 6)]
            if shape == "complete"
            else [(i, (i + 1) % 6) for i in range(6)]
            if shape == "ring"
            else [(2, i) for i in range(6) if i != 2]
        )
        for a, b in edges:
            axis.plot(points[[a, b], 0], points[[a, b], 1], color="#c3c8ca", linewidth=1, zorder=1)
        for index, point in enumerate(points):
            axis.scatter(
                *point,
                s=210,
                color="#a16b43" if index == 5 else "#536b87" if index == 0 else "#4e765f",
                zorder=2,
            )
            axis.text(*point, str(index), color="white", ha="center", va="center", fontsize=9)
        axis.set_title(shape.capitalize())
        axis.set_aspect("equal")
        axis.axis("off")
    figure.suptitle(
        "Peer-message graphs; shared proposal/artifact channel held present", fontsize=11
    )
    save(figure, destination, "message-topologies")
    metadata = {
        "observed_trials": len(observed),
        "planned_trials": len(rows),
        "not_run": sum(row["status"] == "not_run" for row in rows),
        "results_sha256": __import__("hashlib")
        .sha256((study / "results.json").read_bytes())
        .hexdigest(),
        "interpretation": "Descriptive pilot only; missing observations are not zeros. "
        "Intervals are Wilson binomial intervals, not evidence of a phase transition.",
    }
    (destination / "figure-provenance.json").write_text(json.dumps(metadata, indent=2) + "\n")
    print(json.dumps(metadata, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--study", default="pilot-v2")
    render(HOME / parser.parse_args().study)
