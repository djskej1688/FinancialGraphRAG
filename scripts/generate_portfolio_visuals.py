from __future__ import annotations

import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


try:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import numpy as np
    import pandas as pd
    import seaborn as sns
except ModuleNotFoundError as exc:  # pragma: no cover - exercised by environment preflight
    raise SystemExit(
        "Missing visualization dependency. Install matplotlib, seaborn, pandas, and numpy before running this script."
    ) from exc


ROOT = Path(__file__).resolve().parents[1]
OUT_DIR = ROOT / "outputs" / "portfolio"
R10_STATE = ROOT / "outputs" / "round10_eval" / "state.json"
NAIVE_STATE = ROOT / "outputs" / "naive_baseline" / "state.json"
R10_TRACES = ROOT / "outputs" / "round3_eval_runs" / "round10_eval_20260529_170409" / "round10_traces.jsonl"
VISUALS_STATE = OUT_DIR / "visuals_state.json"

FILES = {
    "round_progression": OUT_DIR / "round_progression.png",
    "dataset_method_comparison": OUT_DIR / "dataset_method_comparison.png",
    "formula_type_heatmap": OUT_DIR / "formula_type_heatmap.png",
    "naive_comparison": OUT_DIR / "naive_comparison.png",
}


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n")


def style() -> None:
    sns.set_theme(style="whitegrid", context="talk")
    plt.rcParams.update(
        {
            "figure.dpi": 150,
            "savefig.dpi": 180,
            "font.family": "DejaVu Sans",
            "axes.titleweight": "bold",
            "axes.labelsize": 12,
            "axes.titlesize": 16,
            "legend.fontsize": 11,
            "xtick.labelsize": 10,
            "ytick.labelsize": 10,
        }
    )


def save(fig: Any, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fig.tight_layout()
    fig.savefig(path, bbox_inches="tight")
    plt.close(fig)


def round_progression(r10_state: dict[str, Any]) -> None:
    rounds = ["R5", "R6", "R7*", "R8", "R9C", "R10"]
    x = np.arange(len(rounds))
    data = {
        "graph": [0.00, 0.50, 0.90, 0.46, 0.52, float(r10_state["test_ac_graph"])],
        "vector": [np.nan, 0.40, 0.60, 0.36, 0.50, float(r10_state["test_ac_vector"])],
        "hybrid": [np.nan, 0.40, 0.80, 0.40, 0.46, float(r10_state["test_ac_hybrid"])],
    }
    colors = {"graph": "#1f5fbf", "vector": "#e58a2a", "hybrid": "#238b45"}
    fig, ax = plt.subplots(figsize=(10.5, 6))
    ax.axvspan(3 - 0.45, 5 + 0.45, color="#eef4ff", zorder=0)
    ax.text(4, 0.97, "Clean Held-Out", ha="center", va="top", color="#335", fontsize=11, fontweight="bold")
    for name, values in data.items():
        ax.plot(
            x,
            values,
            marker="o",
            linewidth=3.2 if name == "graph" else 2.4,
            color=colors[name],
            label=name.title(),
            zorder=3 if name == "graph" else 2,
        )
        for xi, yi in zip(x, values):
            if np.isfinite(yi):
                ax.text(xi, yi + 0.035, f"{yi:.2f}", ha="center", va="bottom", fontsize=9, color=colors[name])
    ax.axvline(2, color="#777", linestyle="--", linewidth=1)
    ax.annotate(
        "R7: targeted diagnostic rerun\n(not clean held-out)",
        xy=(2, 0.90),
        xytext=(2.55, 0.78),
        arrowprops={"arrowstyle": "->", "color": "#666"},
        fontsize=9,
        color="#333",
    )
    ax.set_title("GraphRAG vs Vector RAG: Answer Correctness by Round")
    ax.set_ylabel("Answer Correctness")
    ax.set_xticks(x, rounds)
    ax.set_ylim(0, 1.05)
    ax.legend(loc="lower right", frameon=True)
    save(fig, FILES["round_progression"])


def dataset_method_comparison(r10_state: dict[str, Any]) -> None:
    rows = [
        {"dataset": "FinDER\n(130)", "method": "vector", "ac": 0.2692},
        {"dataset": "FinDER\n(130)", "method": "graph", "ac": float(r10_state["test_ac_graph_finder"])},
        {"dataset": "FinDER\n(130)", "method": "hybrid", "ac": 0.2692},
        {"dataset": "FinQA\n(56)", "method": "vector", "ac": 0.8214},
        {"dataset": "FinQA\n(56)", "method": "graph", "ac": float(r10_state["test_ac_graph_finqa"])},
        {"dataset": "FinQA\n(56)", "method": "hybrid", "ac": 0.8571},
        {"dataset": "TAT-QA\n(65)", "method": "vector", "ac": 0.9538},
        {"dataset": "TAT-QA\n(65)", "method": "graph", "ac": float(r10_state["test_ac_graph_tatqa"])},
        {"dataset": "TAT-QA\n(65)", "method": "hybrid", "ac": 0.9077},
        {"dataset": "Overall\n(251)", "method": "vector", "ac": float(r10_state["test_ac_vector"])},
        {"dataset": "Overall\n(251)", "method": "graph", "ac": float(r10_state["test_ac_graph"])},
        {"dataset": "Overall\n(251)", "method": "hybrid", "ac": float(r10_state["test_ac_hybrid"])},
    ]
    df = pd.DataFrame(rows)
    fig, ax = plt.subplots(figsize=(11, 6.2))
    palette = {"vector": "#e58a2a", "graph": "#1f5fbf", "hybrid": "#238b45"}
    sns.barplot(data=df, x="dataset", y="ac", hue="method", palette=palette, ax=ax, edgecolor="#333", linewidth=0.6)
    for patch, (_, row) in zip(ax.patches, df.iterrows()):
        if row["method"] == "graph":
            patch.set_linewidth(2.2)
            patch.set_edgecolor("#082d74")
        ax.text(
            patch.get_x() + patch.get_width() / 2,
            patch.get_height() + 0.018,
            f"{row['ac']:.2f}",
            ha="center",
            va="bottom",
            fontsize=9,
        )
    ax.annotate("vector > graph\n(table format)", xy=(1, 0.84), xytext=(1.05, 0.61), arrowprops={"arrowstyle": "->", "color": "#666"}, fontsize=9)
    ax.annotate("11% selection bias", xy=(2, 0.94), xytext=(2.1, 0.72), arrowprops={"arrowstyle": "->", "color": "#666"}, fontsize=9)
    ax.set_title("Round 10: Performance by Dataset and Method")
    ax.set_xlabel("")
    ax.set_ylabel("Answer Correctness")
    ax.set_ylim(0, 1.08)
    ax.legend(title="", loc="upper left", frameon=True)
    save(fig, FILES["dataset_method_comparison"])


def formula_type_heatmap(traces: list[dict[str, Any]]) -> None:
    methods = ["vector_only_v10", "graph_neo4j_v10", "hybrid_neo4j_v10"]
    labels = {"vector_only_v10": "Vector", "graph_neo4j_v10": "Graph", "hybrid_neo4j_v10": "Hybrid"}
    graph_counts = Counter(row.get("formula_type", "") for row in traces if row.get("method") == "graph_neo4j_v10")
    formula_types = [name for name, _count in graph_counts.most_common()]
    matrix = []
    ylabels = []
    for formula_type in formula_types:
        ylabels.append(f"{formula_type} ({graph_counts[formula_type]})")
        row_values = []
        for method in methods:
            selected = [row for row in traces if row.get("formula_type") == formula_type and row.get("method") == method]
            row_values.append(sum(float(row.get("answer_correctness", 0.0)) for row in selected) / len(selected) if selected else np.nan)
        matrix.append(row_values)
    df = pd.DataFrame(matrix, index=ylabels, columns=[labels[m] for m in methods])
    height = max(6.5, 0.45 * len(formula_types) + 2.5)
    fig, ax = plt.subplots(figsize=(8.5, height))
    sns.heatmap(df, cmap="RdYlGn", vmin=0, vmax=1, annot=True, fmt=".2f", linewidths=0.5, linecolor="white", cbar_kws={"label": "Answer Correctness"}, ax=ax)
    ax.set_title("Round 10: Answer Correctness by Formula Type")
    ax.set_xlabel("")
    ax.set_ylabel("")
    save(fig, FILES["formula_type_heatmap"])


def naive_comparison(naive_state: dict[str, Any]) -> None:
    labels = ["Naive\ngpt-4o-mini", "Naive\ngpt-4o", "GraphRAG\n(ours)"]
    values = [
        float(naive_state["naive_mini_ac"]),
        float(naive_state["naive_4o_ac"]),
        float(naive_state["r10_graph_ac_on_subset"]),
    ]
    colors = ["#b9c0cb", "#9099a8", "#1f5fbf"]
    fig, ax = plt.subplots(figsize=(9.5, 5.7))
    bars = ax.barh(labels, values, color=colors, edgecolor=["#777", "#666", "#082d74"], linewidth=[0.8, 0.8, 2.4])
    for bar, value in zip(bars, values):
        ax.text(value + 0.015, bar.get_y() + bar.get_height() / 2, f"{value:.2f}", va="center", fontsize=11)
    delta_mini = values[2] - values[0]
    delta_4o = values[2] - values[1]
    if delta_mini > 0:
        ax.annotate(f"+{delta_mini:.1%} vs mini", xy=(values[2], 2), xytext=(min(0.95, values[2] + 0.10), 2.25), arrowprops={"arrowstyle": "->", "color": "#333"}, fontsize=10)
    if delta_4o > 0:
        ax.annotate(f"+{delta_4o:.1%} vs gpt-4o", xy=(values[2], 2), xytext=(min(0.95, values[2] + 0.10), 1.75), arrowprops={"arrowstyle": "->", "color": "#333"}, fontsize=10)
    ax.set_title("GraphRAG vs Naive LLM Baseline (50-case subset)")
    ax.set_xlabel("Answer Correctness")
    ax.set_xlim(0, 1.05)
    ax.text(0.0, -0.85, "Same 50 cases, same scorer; only retrieval method differs", fontsize=10, color="#555")
    save(fig, FILES["naive_comparison"])


def validate_outputs() -> dict[str, int]:
    sizes = {}
    for name, path in FILES.items():
        if not path.exists():
            raise RuntimeError(f"Missing generated file: {path}")
        size = path.stat().st_size
        if size <= 10_000:
            raise RuntimeError(f"Generated file is too small to trust: {path} ({size} bytes)")
        sizes[f"{name}.png"] = size
    return sizes


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    if not NAIVE_STATE.exists():
        raise RuntimeError("Missing outputs/naive_baseline/state.json. Run scripts/naive_baseline_eval.py first.")
    r10_state = read_json(R10_STATE)
    naive_state = read_json(NAIVE_STATE)
    if naive_state.get("phase") != "done":
        raise RuntimeError("Naive baseline state is not done")
    traces = read_jsonl(R10_TRACES)
    style()
    round_progression(r10_state)
    dataset_method_comparison(r10_state)
    formula_type_heatmap(traces)
    naive_comparison(naive_state)
    sizes = validate_outputs()
    write_json(
        VISUALS_STATE,
        {
            "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"),
            "files": [path.name for path in FILES.values()],
            "file_sizes": sizes,
            "r10_graph_overall": r10_state["test_ac_graph"],
            "r10_vector_overall": r10_state["test_ac_vector"],
            "r10_hybrid_overall": r10_state["test_ac_hybrid"],
            "naive_mini_ac": naive_state["naive_mini_ac"],
            "naive_4o_ac": naive_state["naive_4o_ac"],
            "r10_graph_ac_on_subset": naive_state["r10_graph_ac_on_subset"],
            "all_generated": True,
        },
    )
    print(json.dumps(read_json(VISUALS_STATE), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
