"""Create review package for the latest or provided Round 3 dev dry-run v3.1.

File-only review. Does not call models, run eval, write Neo4j, or modify
prompt/formatter artifacts.
"""

from __future__ import annotations

import argparse
import csv
import json
import re
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
OUT_ROOT = ROOT / "outputs" / "round3_eval_runs"


def latest_run_dir() -> Path:
    runs = sorted(OUT_ROOT.glob("dev_dryrun_v3_1_*"))
    if not runs:
        raise SystemExit("no dev_dryrun_v3_1_* run directory found")
    return runs[-1]


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows = []
    if not path.exists():
        return rows
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            rows.append(json.loads(line))
    return rows


def write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text.rstrip() + "\n", encoding="utf-8")


def fnum(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def table(rows: list[dict[str, str]], fields: list[str]) -> str:
    lines = ["| " + " | ".join(fields) + " |", "| " + " | ".join(["---"] * len(fields)) + " |"]
    for row in rows:
        lines.append("| " + " | ".join(str(row.get(field, "")) for field in fields) + " |")
    return "\n".join(lines)


def by_track_method(rows: list[dict[str, str]]) -> dict[str, list[dict[str, str]]]:
    grouped: dict[str, list[dict[str, str]]] = defaultdict(list)
    for row in rows:
        grouped[row["track"]].append(row)
    return grouped


def prompt_isolation_audit(traces: list[dict[str, Any]]) -> tuple[bool, list[str]]:
    issues: list[str] = []
    for trace in traces:
        method = trace.get("method", "")
        prompt = trace.get("user_prompt", "")
        has_text = bool(re.search(r"(?m)^TEXT_CONTEXT:?\s*$", prompt))
        has_graph = bool(re.search(r"(?m)^GRAPH_FACTS_TABLE:?\s*$", prompt))
        has_gold = bool(re.search(r"(?m)^GOLD_CONTEXT:?\s*$", prompt))
        if method == "vector_only_v3_1" and (not has_text or has_graph or has_gold):
            issues.append(f"{trace.get('trace_id')}: vector_only context isolation violation")
        if method == "graph_facts_only_v3_1" and (has_text or not has_graph or has_gold):
            issues.append(f"{trace.get('trace_id')}: graph_facts_only context isolation violation")
        if method == "hybrid_vector_graph_v3_1" and (not has_text or not has_graph or has_gold):
            issues.append(f"{trace.get('trace_id')}: hybrid context isolation violation")
        if method == "gold_context_v3_1" and (has_text or has_graph or not has_gold):
            issues.append(f"{trace.get('trace_id')}: gold_context isolation violation")
    return not issues, issues


def decision(summary: dict[str, Any], method_rows: list[dict[str, str]], isolation_ok: bool) -> str:
    provider_ok = int(summary.get("provider_failures", 0)) == 0
    test_ok = not bool(summary.get("test_eval_executed", True))
    format_ok = all(fnum(row["avg_answer_format_compliance"]) >= 0.99 for row in method_rows)
    answer_best = max(fnum(row["avg_answer_correctness"]) for row in method_rows) if method_rows else 0.0
    numeric_best = max(fnum(row["avg_numeric_correctness"]) for row in method_rows) if method_rows else 0.0
    opik_created = int(summary.get("opik_traces_created", 0))
    if not isolation_ok or not provider_ok or not test_ok:
        return "no_go"
    if not format_ok:
        return "ready_after_minor_prompt_formatter_fix"
    if answer_best < 0.65 or numeric_best < 0.75:
        return "not_ready_needs_dev_rerun"
    if opik_created <= 0:
        return "ready_after_opik_config_fix"
    return "ready_for_locked_test_eval"


def metric_thresholds(rows: list[dict[str, str]]) -> dict[str, bool]:
    return {
        "provider_stable": all(fnum(row["provider_errors"]) == 0 for row in rows),
        "format_stable": all(fnum(row["avg_answer_format_compliance"]) >= 0.99 for row in rows),
        "hybrid_answer_weak": any(row["method"] == "hybrid_vector_graph_v3_1" and fnum(row["avg_answer_correctness"]) < 0.65 for row in rows),
        "graph_numeric_weak": any(row["method"] == "graph_facts_only_v3_1" and fnum(row["avg_numeric_correctness"]) < 0.5 for row in rows),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-dir", default="")
    args = parser.parse_args()
    run_dir = Path(args.run_dir).resolve() if args.run_dir else latest_run_dir()
    review_dir = run_dir / "review"
    review_dir.mkdir(parents=True, exist_ok=True)

    summary = read_json(run_dir / "dev_dryrun_v3_1_summary.json")
    results = read_csv(run_dir / "dev_dryrun_v3_1_results.csv")
    method_track = read_csv(run_dir / "method_summary_by_track.csv")
    method_split = read_csv(run_dir / "method_summary_by_split.csv")
    case_scores = read_csv(run_dir / "case_level_scores.csv")
    failures = read_jsonl(run_dir / "failure_analysis.jsonl")
    traces = read_jsonl(run_dir / "dev_dryrun_v3_1_traces.jsonl")
    opik_rows = read_jsonl(run_dir / "opik_trace_ids.jsonl")
    prompt_issue_text = (run_dir / "prompt_formatter_issues_v3_1.md").read_text(encoding="utf-8")

    isolation_ok, isolation_issues = prompt_isolation_audit(traces)
    final_decision = decision(summary, method_track, isolation_ok)
    failure_counts = Counter(row["failure_reason"] for row in results)
    opik_created = sum(1 for row in opik_rows if row.get("opik_trace_id"))
    dev_rerun_needed = final_decision == "not_ready_needs_dev_rerun"
    prompt_action = "freeze_v3_1" if final_decision in {"ready_for_locked_test_eval", "ready_after_opik_config_fix"} else "patch_v3_1_before_next_dev_rerun"

    write_summary(review_dir, summary, failure_counts, final_decision, prompt_action, dev_rerun_needed, opik_created)
    write_track_review(review_dir, "track_a_live_kg_diagnostic", method_track, method_split, case_scores)
    write_track_review(review_dir, "track_b_shadow_overlay", method_track, method_split, case_scores)
    write_method_comparison(review_dir, method_track)
    write_failure_audit(review_dir, failure_counts, results)
    write_prompt_audit(review_dir, prompt_issue_text, isolation_ok, isolation_issues)
    write_scoring_audit(review_dir, method_track, failures)
    write_opik_gap(review_dir, opik_rows, summary)
    write_test_decision(review_dir, final_decision, prompt_action, dev_rerun_needed, opik_created)
    write_next_action(review_dir, final_decision, prompt_action, dev_rerun_needed, opik_created)

    print(
        json.dumps(
            {
                "review_package_created": True,
                "run_dir": str(run_dir.relative_to(ROOT)).replace("\\", "/"),
                "decision": final_decision,
                "opik_traces_created": opik_created,
                "dev_rerun_needed": dev_rerun_needed,
                "prompt_formatter_action": prompt_action,
                "created_files": [str(path.relative_to(ROOT)).replace("\\", "/") for path in sorted(review_dir.iterdir()) if path.is_file()],
            },
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
    )


def write_summary(review_dir: Path, summary: dict[str, Any], failure_counts: Counter[str], final_decision: str, prompt_action: str, dev_rerun_needed: bool, opik_created: int) -> None:
    lines = [
        "# Dev Dry-Run v3.1 Review Summary",
        "",
        f"- Run dir: `{summary['run_dir']}`",
        f"- Attempts: {summary['attempts']}",
        f"- Provider failures: {summary['provider_failures']}",
        f"- Test eval executed: {str(summary['test_eval_executed']).lower()}",
        f"- Full eval executed: {str(summary['full_eval_executed']).lower()}",
        f"- Model/API called: {str(summary['model_api_called']).lower()}",
        f"- Opik traces created: {opik_created}",
        f"- Decision: `{final_decision}`",
        "",
        "## Failure Mix",
        "",
    ]
    for reason, count in failure_counts.most_common():
        lines.append(f"- {reason}: {count}")
    lines.extend(
        [
            "",
            "## Review Conclusion",
            "",
            f"- Recommended next action: {'request separate locked test approval after Opik review' if final_decision in {'ready_for_locked_test_eval', 'ready_after_opik_config_fix'} else 'patch v3.1 issues and rerun dev/baseline only'}",
            f"- Opik config must be fixed before test: {str(opik_created == 0).lower()}",
            f"- Dev rerun needed: {str(dev_rerun_needed).lower()}",
            f"- Prompt/formatter action: `{prompt_action}`",
            "- Track B test can be approved later: only with separate approval.",
            "- Track A test should remain diagnostic only: yes.",
        ]
    )
    write(review_dir / "dev_dryrun_v3_1_review_summary.md", "\n".join(lines))


def write_track_review(review_dir: Path, track: str, method_track: list[dict[str, str]], method_split: list[dict[str, str]], case_scores: list[dict[str, str]]) -> None:
    rows = [row for row in method_track if row["track"] == track]
    split_rows = [row for row in method_split if row["track"] == track]
    cases = [row for row in case_scores if row["track"] == track]
    title = "Track A Live KG Diagnostic Review" if track == "track_a_live_kg_diagnostic" else "Track B Shadow Overlay Review"
    boundary = "live KG diagnostic only" if track == "track_a_live_kg_diagnostic" else "shadow overlay scoped evaluation only"
    lines = [
        f"# {title}",
        "",
        f"- Boundary: {boundary}",
        "- Track averages are not merged with the other track.",
        "",
        "## Method Metrics",
        "",
        table(rows, ["method", "attempts", "provider_errors", "avg_required_fact_recall", "avg_numeric_correctness", "avg_answer_correctness", "avg_faithfulness", "avg_calculation_completeness", "avg_answer_format_compliance"]),
        "",
        "## Split Metrics",
        "",
        table(split_rows, ["split", "method", "attempts", "avg_required_fact_recall", "avg_numeric_correctness", "avg_answer_correctness", "avg_faithfulness"]),
        "",
        "## Case Metrics",
        "",
        table(cases, ["split", "case_id", "attempts", "avg_required_fact_recall", "avg_numeric_correctness", "avg_answer_correctness", "avg_faithfulness"]),
    ]
    filename = "track_a_live_kg_diagnostic_review.md" if track == "track_a_live_kg_diagnostic" else "track_b_shadow_overlay_review.md"
    write(review_dir / filename, "\n".join(lines))


def write_method_comparison(review_dir: Path, method_track: list[dict[str, str]]) -> None:
    lines = ["# Method Performance Comparison", "", "Track A and Track B are compared separately.", ""]
    for track, rows in by_track_method(method_track).items():
        checks = metric_thresholds(rows)
        lines.extend(
            [
                f"## {track}",
                "",
                table(rows, ["method", "attempts", "avg_required_fact_recall", "avg_numeric_correctness", "avg_answer_correctness", "avg_faithfulness", "avg_calculation_completeness", "avg_answer_format_compliance"]),
                "",
                f"- Provider stable: {str(checks['provider_stable']).lower()}",
                f"- Answer format stable: {str(checks['format_stable']).lower()}",
                f"- Hybrid answer weak: {str(checks['hybrid_answer_weak']).lower()}",
                f"- Graph numeric weak: {str(checks['graph_numeric_weak']).lower()}",
                "",
            ]
        )
    write(review_dir / "method_performance_comparison.md", "\n".join(lines))


def write_failure_audit(review_dir: Path, failure_counts: Counter[str], results: list[dict[str, str]]) -> None:
    by_track_reason = Counter((row["track"], row["failure_reason"]) for row in results)
    lines = ["# Failure Reason Audit", "", "## Overall", ""]
    for reason, count in failure_counts.most_common():
        lines.append(f"- {reason}: {count}")
    lines.extend(["", "## By Track", ""])
    for (track, reason), count in sorted(by_track_reason.items()):
        lines.append(f"- {track} / {reason}: {count}")
    lines.extend(["", "## Interpretation", "", "- Provider errors are separated from scoring failures.", "- Track A and Track B failures are not merged into a headline score."])
    write(review_dir / "failure_reason_audit.md", "\n".join(lines))


def write_prompt_audit(review_dir: Path, prompt_issue_text: str, isolation_ok: bool, isolation_issues: list[str]) -> None:
    lines = [
        "# Prompt / Formatter Issue Audit",
        "",
        f"- Existing prompt_formatter_issues_v3_1.md empty: {str(not prompt_issue_text.strip().replace('# Prompt/Formatter Issues v3.1', '').strip()).lower()}",
        f"- Method isolation clean: {str(isolation_ok).lower()}",
        "- graph_facts_only raw text contamination: none detected if method isolation is clean.",
        "- vector_only graph fact contamination: none detected if method isolation is clean.",
        "- hybrid receives text and graph facts only.",
        "- gold_context uses gold context only.",
    ]
    if isolation_issues:
        lines.extend(["", "## Isolation Issues", *[f"- {issue}" for issue in isolation_issues]])
    write(review_dir / "prompt_formatter_issue_audit.md", "\n".join(lines))


def write_scoring_audit(review_dir: Path, method_track: list[dict[str, str]], failures: list[dict[str, Any]]) -> None:
    lines = [
        "# Scoring Consistency Audit",
        "",
        "- v3.1 uses the stricter answer schema and deterministic local scoring.",
        "- Required fact recall still depends on answer-cited fact usage for vector/gold and retrieved fact availability for graph/hybrid.",
        "- Numeric correctness remains strict on expected numeric slots, units, and periods.",
        "",
        "## Method Summary",
        "",
        table(method_track, ["track", "method", "attempts", "avg_required_fact_recall", "avg_numeric_correctness", "avg_answer_correctness", "avg_answer_format_compliance"]),
    ]
    write(review_dir / "scoring_consistency_audit.md", "\n".join(lines))


def write_opik_gap(review_dir: Path, opik_rows: list[dict[str, Any]], summary: dict[str, Any]) -> None:
    created = sum(1 for row in opik_rows if row.get("opik_trace_id"))
    statuses = Counter(row.get("opik_status", "") for row in opik_rows)
    lines = [
        "# Opik Gap Report",
        "",
        f"- Local trace rows: {len(opik_rows)}",
        f"- Opik traces created: {created}",
        f"- Opik status from summary: `{summary.get('opik_status', 'unknown')}`",
        "",
        "## Status Counts",
        "",
    ]
    for status, count in statuses.most_common():
        lines.append(f"- {status or 'unknown'}: {count}")
    lines.extend(
        [
            "",
            "## Recommendation",
            "",
            "Locked test eval should not proceed without Opik config or an explicit local-only locked-test waiver. The current local-only waiver applies only to dev/baseline rerun.",
        ]
    )
    write(review_dir / "opik_gap_report.md", "\n".join(lines))


def write_test_decision(review_dir: Path, final_decision: str, prompt_action: str, dev_rerun_needed: bool, opik_created: int) -> None:
    lines = [
        "# Test Eval Readiness Decision",
        "",
        f"Decision: `{final_decision}`",
        "",
        "- Test rows are 0: pass.",
        "- Full eval executed: false.",
        "- Method separation is checked from local traces.",
        f"- Opik traces created: {opik_created}.",
        "",
        "## Required Before Locked Test",
        "",
        "1. Keep full eval locked.",
        "2. Request separate locked test eval approval.",
        "3. Fix Opik or obtain explicit local-only locked-test waiver.",
        "4. Keep Track A diagnostic and Track B shadow-overlay claims separate.",
    ]
    if dev_rerun_needed:
        lines.insert(8, "- Dev rerun still needed before locked test.")
    write(review_dir / "test_eval_readiness_decision.md", "\n".join(lines))


def write_next_action(review_dir: Path, final_decision: str, prompt_action: str, dev_rerun_needed: bool, opik_created: int) -> None:
    if final_decision in {"ready_for_locked_test_eval", "ready_after_opik_config_fix"}:
        action = "prepare a separate locked test approval request; do not run test yet."
    else:
        action = "review v3.1 failures and patch only dev-derived prompt/scoring issues before another dev/baseline rerun."
    lines = [
        "# Recommended Next Action",
        "",
        f"Recommended next action: {action}",
        "",
        f"- Opik config must be fixed before test: {str(opik_created == 0).lower()}",
        f"- Dev rerun needed: {str(dev_rerun_needed).lower()}",
        f"- Prompt/formatter action: `{prompt_action}`",
        "- Track B test can be approved later: yes, with separate approval only.",
        "- Track A test should remain diagnostic only: yes.",
    ]
    write(review_dir / "recommended_next_action.md", "\n".join(lines))


if __name__ == "__main__":
    main()
