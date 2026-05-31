"""R15-correct portfolio visuals. Numbers are hardcoded from the pinned canonical
set (no data-file dependency) so this renders anywhere with matplotlib.

Produces 6 PNGs under outputs/portfolio_r15/. Each figure uses ONE consistent
metric basis (no mixing scorer-AC with judge, or prompt-tokens with total-tokens).

Run:  python scripts/round15_portfolio_visuals.py
"""
from __future__ import annotations

import sys
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

OUT = Path(__file__).resolve().parents[1] / "outputs" / "portfolio_r15"
OUT.mkdir(parents=True, exist_ok=True)

GRAPH = "#1f6f3a"      # graph = green
VEC = "#b3261e"        # vector = red
NEUTRAL = "#5b6770"    # source/text = grey
GOLD = "#9a7b00"

plt.rcParams.update({"font.size": 11, "axes.grid": True, "grid.alpha": 0.3,
                     "axes.axisbelow": True, "figure.dpi": 150})


def _bars(ax, labels, values, colors, fmt="{:.3f}"):
    bars = ax.bar(labels, values, color=colors, edgecolor="black", linewidth=0.6)
    for b, v in zip(bars, values):
        ax.text(b.get_x() + b.get_width() / 2, v + max(values) * 0.012,
                fmt.format(v), ha="center", va="bottom", fontsize=9)
    return bars


def fig1_headline():
    methods = ["graph\nstructured", "graph\nguided", "source\nconcat",
               "vector\nmulti", "vector\nsingle"]
    ac = [0.825, 0.800, 0.338, 0.088, 0.063]
    colors = [GRAPH, GRAPH, NEUTRAL, VEC, VEC]
    fig, ax = plt.subplots(figsize=(8, 4.6))
    _bars(ax, methods, ac, colors)
    ax.set_ylabel("Answer Correctness")
    ax.set_ylim(0, 0.95)
    ax.set_title("R14 Cross-Company Comparison — Graph ≫ Vector (n=80)")
    fig.tight_layout(); fig.savefig(OUT / "fig1_r14_headline.png"); plt.close(fig)


def fig2_both_found():
    methods = ["graph\nstructured", "graph\nguided", "source\nconcat",
               "vector\nmulti", "vector\nsingle"]
    bf = [1.0, 1.0, 1.0, 0.225, 0.125]
    colors = [GRAPH, GRAPH, NEUTRAL, VEC, VEC]
    fig, ax = plt.subplots(figsize=(8, 4.6))
    _bars(ax, methods, bf, colors, fmt="{:.2f}")
    ax.set_ylabel("Both companies found (coverage)")
    ax.set_ylim(0, 1.1)
    ax.set_title("Why Vector Fails: it rarely retrieves BOTH companies")
    ax.axhline(1.0, color="black", lw=0.7, ls="--", alpha=0.5)
    fig.tight_layout(); fig.savefig(OUT / "fig2_both_companies_found.png"); plt.close(fig)


def fig3_efficiency():
    # R14 basis: total tokens vs scorer AC
    pts = [("graph_structured", 2357, 0.825, GRAPH),
           ("graph_guided", 3594, 0.800, GRAPH),
           ("source_concat", 3549, 0.338, NEUTRAL),
           ("vector_multi", 6436, 0.088, VEC),
           ("vector_single", 5745, 0.063, VEC)]
    fig, ax = plt.subplots(figsize=(8, 5))
    for name, tok, ac, c in pts:
        ax.scatter(tok, ac, s=130, color=c, edgecolor="black", zorder=3)
        ax.annotate(name, (tok, ac), textcoords="offset points", xytext=(8, 6), fontsize=9)
    ax.set_xlabel("Avg context tokens (R14, total)")
    ax.set_ylabel("Answer Correctness")
    ax.set_title("Accurate AND Cheap: graph is top-left, vector bottom-right")
    ax.set_ylim(0, 0.95)
    fig.tight_layout(); fig.savefig(OUT / "fig3_accuracy_vs_tokens.png"); plt.close(fig)


def fig4_judge_invariance():
    judges = ["gpt-4o", "DeepSeek", "Kimi", "Grok"]
    series = {
        "graph_structured": ([0.60, 0.58, 0.61, 0.49], GRAPH),
        "graph_guided":     ([0.58, 0.58, 0.57, 0.54], "#3a9d5d"),
        "source_concat":    ([0.26, 0.22, 0.25, 0.12], NEUTRAL),
        "vector_single":    ([0.09, 0.14, 0.13, 0.04], VEC),
        "vector_multi":     ([0.08, 0.09, 0.12, 0.03], "#e0635a"),
    }
    import numpy as np
    x = np.arange(len(judges)); w = 0.16
    fig, ax = plt.subplots(figsize=(9, 4.8))
    for i, (name, (vals, c)) in enumerate(series.items()):
        ax.bar(x + (i - 2) * w, vals, w, label=name, color=c, edgecolor="black", linewidth=0.4)
    ax.set_xticks(x); ax.set_xticklabels(judges)
    ax.set_ylabel("Mean judge_score")
    ax.set_title("Judge-Invariant: graph > every vector arm under ALL 4 vendors (Fleiss' κ=0.53)")
    ax.legend(fontsize=8, ncol=5, loc="upper center", bbox_to_anchor=(0.5, -0.08))
    fig.tight_layout(); fig.savefig(OUT / "fig4_judge_invariance.png"); plt.close(fig)


def fig5_single_company_reversal():
    import numpy as np
    cats = ["graph", "text/case-text"]
    orig = [0.610, 0.570]
    v2 = [0.498, 0.673]
    x = np.arange(len(cats)); w = 0.35
    fig, ax = plt.subplots(figsize=(7, 4.6))
    b1 = ax.bar(x - w / 2, orig, w, label="original (year-bug)", color="#bbbbbb", edgecolor="black")
    b2 = ax.bar(x + w / 2, v2, w, label="v2 (corrected)", color=[GRAPH, VEC], edgecolor="black")
    for bars in (b1, b2):
        for b in bars:
            ax.text(b.get_x() + b.get_width() / 2, b.get_height() + 0.008,
                    f"{b.get_height():.3f}", ha="center", va="bottom", fontsize=9)
    ax.set_xticks(x); ax.set_xticklabels(cats)
    ax.set_ylabel("Overall Answer Correctness")
    ax.set_title("Self-Correction: single-company graph>vector REVERSED after the scorer fix")
    ax.legend(fontsize=9); ax.set_ylim(0, 0.78)
    fig.tight_layout(); fig.savefig(OUT / "fig5_single_company_reversal.png"); plt.close(fig)


def fig6_by_level():
    levels = ["L1 direct", "L2 derived/ratio", "L3 multi-year trend"]
    ac = [0.925, 0.867, 0.300]
    colors = [GRAPH, GRAPH, VEC]
    fig, ax = plt.subplots(figsize=(7.5, 4.4))
    _bars(ax, levels, ac, colors)
    ax.set_ylabel("graph_structured Answer Correctness")
    ax.set_ylim(0, 1.0)
    ax.set_title("Honest weak spot: multi-year TREND is hard even for graph (L3=0.30)")
    fig.tight_layout(); fig.savefig(OUT / "fig6_by_level.png"); plt.close(fig)


def main():
    # Report uses 3 core charts: result -> mechanism -> robustness.
    # fig3 (efficiency), fig5 (reversal), fig6 (by-level) remain defined above as
    # optional extras — add their calls here only if you want them in the report.
    fig1_headline(); fig2_both_found(); fig4_judge_invariance()
    pngs = sorted(p.name for p in OUT.glob("*.png"))
    print(f"Wrote {len(pngs)} figures to {OUT}:")
    for p in pngs:
        print(f"  {p}")


if __name__ == "__main__":
    main()
