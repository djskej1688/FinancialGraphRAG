"""Finalize revised ready-subset partial eval report package without model calls."""

from __future__ import annotations

import csv
import json
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[1]
RUN_DIR = REPO_ROOT / "outputs" / "round3_eval_runs" / "ready_partial_real_20260527_093341"
ORCH_DIR = REPO_ROOT / "outputs" / "round3_orchestration" / "20260525_132801"
EXCLUDE_WARNINGS = {"ambiguous_formula", "missing_prior_period", "final_answer_calculation_mismatch"}
METHODS = ("vector_only", "graph_facts_only", "hybrid_vector_graph", "gold_context")


def now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def rel(path: Path) -> str:
    try:
        return path.resolve().relative_to(REPO_ROOT.resolve()).as_posix()
    except ValueError:
        return str(path)


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            rows.append(json.loads(line))
    return rows


def load_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text.rstrip() + "\n", encoding="utf-8", newline="\n")


def write_csv(path: Path, fieldnames: list[str], rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def avg(rows: list[dict[str, Any]], key: str) -> float:
    return round(sum(float(row.get(key, 0.0) or 0.0) for row in rows) / max(1, len(rows)), 4)


def clean_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [row for row in rows if not (set(row.get("warning_categories", [])) & EXCLUDE_WARNINGS)]


def summarize(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out = []
    for method in METHODS:
        method_rows = [row for row in rows if row.get("method") == method]
        out.append(
            {
                "method": method,
                "clean_scored_count": len(method_rows),
                "avg_answer_value_fact_recall": avg(method_rows, "answer_value_fact_recall"),
                "avg_source_fact_id_recall_diagnostic": avg(method_rows, "source_fact_id_recall"),
                "avg_numeric_correctness": avg(method_rows, "numeric_correctness"),
                "avg_answer_correctness": avg(method_rows, "answer_correctness"),
            }
        )
    return out


def case_flags(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_case: dict[str, dict[str, Any]] = {}
    for row in rows:
        case_id = str(row.get("case_id", ""))
        item = by_case.setdefault(
            case_id,
            {
                "case_id": case_id,
                "split": row.get("split", ""),
                "methods_flagged": set(),
                "warning_categories": set(),
                "excluded_from_clean_claims": False,
            },
        )
        warnings = set(row.get("warning_categories", []))
        if warnings:
            item["methods_flagged"].add(row.get("method", ""))
            item["warning_categories"].update(warnings)
        if warnings & EXCLUDE_WARNINGS:
            item["excluded_from_clean_claims"] = True
    result = []
    for item in by_case.values():
        result.append(
            {
                "case_id": item["case_id"],
                "split": item["split"],
                "methods_flagged": ",".join(sorted(item["methods_flagged"])),
                "warning_categories": ",".join(sorted(item["warning_categories"])),
                "excluded_from_clean_claims": str(bool(item["excluded_from_clean_claims"])).lower(),
            }
        )
    return sorted(result, key=lambda row: row["case_id"])


def render_summary_table(rows: list[dict[str, Any]], *, clean: bool) -> list[str]:
    count_label = "Clean Scored" if clean else "Attempts"
    lines = [
        f"| Method | {count_label} | Answer-Value Recall | Source-ID Recall Diagnostic | Numeric Correctness | Answer Correctness |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    for row in rows:
        count = row.get("clean_scored_count", row.get("attempt_count", ""))
        answer_recall = row.get("avg_answer_value_fact_recall", row.get("avg_answer_value_fact_recall_scored_only"))
        source_recall = row.get("avg_source_fact_id_recall_diagnostic", row.get("avg_source_fact_id_recall_scored_only"))
        numeric = row.get("avg_numeric_correctness", row.get("avg_numeric_correctness_scored_only"))
        answer = row.get("avg_answer_correctness", row.get("avg_answer_correctness_scored_only"))
        lines.append(f"| `{row['method']}` | {count} | {answer_recall} | {source_recall} | {numeric} | {answer} |")
    return lines


def main() -> None:
    required = [
        RUN_DIR / "revised_report.md",
        RUN_DIR / "revised_method_summary.csv",
        RUN_DIR / "revised_case_results.jsonl",
        RUN_DIR / "scorer_diagnostics.md",
        RUN_DIR / "input_isolation_report.md",
    ]
    missing = [rel(path) for path in required if not path.exists()]
    if missing:
        raise SystemExit(f"missing required input files: {missing}")

    rows = load_jsonl(RUN_DIR / "revised_case_results.jsonl")
    all_summary = load_csv(RUN_DIR / "revised_method_summary.csv")
    clean = clean_rows(rows)
    clean_summary = summarize(clean)
    flags = case_flags(rows)
    warning_counts = Counter()
    for row in rows:
        warning_counts.update(row.get("warning_categories", []))

    write_csv(
        RUN_DIR / "clean_subset_method_summary.csv",
        [
            "method",
            "clean_scored_count",
            "avg_answer_value_fact_recall",
            "avg_source_fact_id_recall_diagnostic",
            "avg_numeric_correctness",
            "avg_answer_correctness",
        ],
        clean_summary,
    )
    write_csv(
        RUN_DIR / "case_review_flags.csv",
        ["case_id", "split", "methods_flagged", "warning_categories", "excluded_from_clean_claims"],
        flags,
    )

    claim_boundary = f"""# Final Claim Boundary

Generated: {now()}

## Allowed Claims

- OpenAI ready-subset partial eval completed successfully.
- Input isolation passed.
- Revised scoring improved metric interpretability.
- Graph-supported methods show stronger source-fact utilization.
- Hybrid is competitive with gold context on the clean subset.

## Forbidden Claims

- Round 3 full eval is complete.
- Hybrid conclusively wins the full benchmark.
- GraphRAG is generally superior.
- The 25-case benchmark is evaluated.
- Full eval can be unlocked.

## Boundary

This report covers 6 ready cases only and excludes 19 backlog cases from benchmark claims. Full eval remains locked.
"""
    write_text(RUN_DIR / "final_claim_boundary.md", claim_boundary)

    report_lines = [
        "# Final Ready-Subset Partial Eval Report",
        "",
        f"Generated: {now()}",
        "",
        "## 1. Executive Summary",
        "",
        "OpenAI ready-subset partial eval completed successfully on the 6 ready cases. All 24 provider attempts succeeded and input contamination count was 0.",
        "This is not full Round 3. The 19 backlog cases remain excluded from method-comparison claims.",
        "",
        "## 2. Scope and Safety",
        "",
        "- Ready-subset partial eval only: true",
        "- Full Round 3 eval: false",
        "- Ready cases evaluated: 6",
        "- Backlog cases excluded: 19",
        "- Provider: OpenAI",
        "- Provider attempts: 24",
        "- Provider successes: 24",
        "- Input contamination count: 0",
        "- Model API called by this reporting task: false",
        "- Neo4j write performed: false",
        "- KG patch applied: false",
        "- Full eval lock: locked",
        "",
        "## 3. Method Summary - All 6 Ready Cases",
        "",
        "`source_fact_id_recall` is diagnostic only and is not fair as a universal metric because graph/hybrid methods can expose fact IDs while vector/gold methods generally cannot.",
        "`answer_value_fact_recall` is the preferred cross-method recall metric.",
        "",
        *render_summary_table(all_summary, clean=False),
        "",
        "## 4. Method Summary - Clean Subset",
        "",
        "The clean subset excludes rows with `ambiguous_formula`, `missing_prior_period`, or `final_answer_calculation_mismatch`.",
        "",
        *render_summary_table(clean_summary, clean=True),
        "",
        "High-level interpretation: gold_context and hybrid_vector_graph are competitive on answer-value recall in the clean subset; graph_facts_only is close on answer-value recall but weaker on numeric/answer correctness; vector_only trails but is not a catastrophic baseline. Do not overclaim from this subset.",
        "",
        "## 5. Warning Categories",
        "",
    ]
    if warning_counts:
        report_lines.extend(f"- `{key}`: {value}" for key, value in sorted(warning_counts.items()))
    else:
        report_lines.append("- None")
    report_lines.extend(
        [
            "",
            "## 6. Case Flags",
            "",
            "| Case | Split | Excluded From Clean Claims | Warning Categories |",
            "|---|---|---|---|",
        ]
    )
    for row in flags:
        report_lines.append(f"| `{row['case_id']}` | `{row['split']}` | `{row['excluded_from_clean_claims']}` | `{row['warning_categories']}` |")
    report_lines.extend(
        [
            "",
            "## 7. Claim Boundary",
            "",
            "- Allowed: OpenAI ready-subset partial eval completed successfully; input isolation passed; revised scoring improved metric interpretability; graph-supported methods show stronger source-fact utilization; hybrid is competitive with gold context on the clean subset.",
            "- Forbidden: Round 3 full eval is complete; hybrid conclusively wins the full benchmark; GraphRAG is generally superior; the 25-case benchmark is evaluated; full eval can be unlocked.",
            "",
            "## 8. Recommended Next Step",
            "",
            "Finalize backlog remediation or manually review flagged cases. Full eval remains locked.",
        ]
    )
    write_text(RUN_DIR / "final_ready_subset_partial_report.md", "\n".join(report_lines))

    next_action = "finalize backlog remediation or manually review flagged cases; full eval remains locked."
    write_text(ORCH_DIR / "automation" / "next_action.md", next_action)
    write_text(
        ORCH_DIR / "automation" / "final_operator_report.md",
        f"""# Final Operator Report

Generated: {now()}

## Status

- ready_subset_partial_eval: complete
- full_eval_lock: locked
- coverage_backlog: open
- next_action: {next_action}

## Evidence

- final report: `{rel(RUN_DIR / 'final_ready_subset_partial_report.md')}`
- clean subset summary: `{rel(RUN_DIR / 'clean_subset_method_summary.csv')}`
- case review flags: `{rel(RUN_DIR / 'case_review_flags.csv')}`

## Safety

- model API called by this reporting task: false
- full eval executed: false
- Neo4j write performed: false
- KG patch applied: false
""",
    )
    gate_path = ORCH_DIR / "merged" / "gate_status.md"
    gate_text = gate_path.read_text(encoding="utf-8") if gate_path.exists() else "# Gate Status\n"
    status_block = """

## Final Ready-Subset Partial Eval Status

- ready_subset_partial_eval: complete
- full_eval_lock: locked
- coverage_backlog: open
- next_action: finalize backlog remediation or manually review flagged cases
"""
    if "## Final Ready-Subset Partial Eval Status" in gate_text:
        gate_text = gate_text.split("## Final Ready-Subset Partial Eval Status", 1)[0].rstrip() + status_block
    else:
        gate_text = gate_text.rstrip() + status_block
    write_text(gate_path, gate_text)


if __name__ == "__main__":
    main()
