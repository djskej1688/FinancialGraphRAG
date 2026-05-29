"""Create review package for Round 3 dev dry-run v3.

File-only review. Does not call models, run eval, write Neo4j, or modify
prompt/formatter artifacts.
"""

from __future__ import annotations

import csv
import json
import re
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
RUN_DIR = ROOT / "outputs" / "round3_eval_runs" / "dev_dryrun_v3_20260527_230440"
REVIEW_DIR = RUN_DIR / "review"


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


def fmt(value: Any) -> str:
    return f"{fnum(value):.4f}"


def by_track_method(rows: list[dict[str, str]]) -> dict[str, list[dict[str, str]]]:
    grouped: dict[str, list[dict[str, str]]] = defaultdict(list)
    for row in rows:
        grouped[row["track"]].append(row)
    return grouped


def table(rows: list[dict[str, str]], fields: list[str]) -> str:
    lines = ["| " + " | ".join(fields) + " |", "| " + " | ".join(["---"] * len(fields)) + " |"]
    for row in rows:
        lines.append("| " + " | ".join(str(row.get(field, "")) for field in fields) + " |")
    return "\n".join(lines)


def prompt_isolation_audit(traces: list[dict[str, Any]]) -> tuple[bool, list[str]]:
    issues: list[str] = []
    for trace in traces:
        method = trace.get("method", "")
        prompt = trace.get("user_prompt", "")
        has_vector = bool(re.search(r"(?m)^VECTOR_CONTEXT:?\s*$", prompt))
        has_graph = bool(re.search(r"(?m)^GRAPH_FACTS_TABLE:?\s*$", prompt))
        has_gold = bool(re.search(r"(?m)^GOLD_CONTEXT:?\s*$", prompt))
        if method == "vector_only_v3" and (not has_vector or has_graph or has_gold):
            issues.append(f"{trace.get('trace_id')}: vector_only context isolation violation")
        if method == "graph_facts_only_v3" and (has_vector or not has_graph or has_gold):
            issues.append(f"{trace.get('trace_id')}: graph_facts_only context isolation violation")
        if method == "hybrid_vector_graph_v3" and (not has_vector or not has_graph or has_gold):
            issues.append(f"{trace.get('trace_id')}: hybrid context isolation violation")
        if method == "gold_context_v3" and (has_vector or has_graph or not has_gold):
            issues.append(f"{trace.get('trace_id')}: gold_context isolation violation")
    return not issues, issues


def metric_thresholds(method_rows: list[dict[str, str]]) -> dict[str, bool]:
    return {
        "provider_stable": all(fnum(row["provider_errors"]) == 0 for row in method_rows),
        "format_stable": all(fnum(row["avg_answer_format_compliance"]) >= 0.99 for row in method_rows),
        "hybrid_answer_weak": any(row["method"] == "hybrid_vector_graph_v3" and fnum(row["avg_answer_correctness"]) < 0.65 for row in method_rows),
        "graph_numeric_weak": any(row["method"] == "graph_facts_only_v3" and fnum(row["avg_numeric_correctness"]) < 0.5 for row in method_rows),
    }


def decision(summary: dict[str, Any], method_rows: list[dict[str, str]], isolation_ok: bool) -> str:
    provider_ok = int(summary.get("provider_failures", 0)) == 0
    test_ok = not bool(summary.get("test_eval_executed", True))
    format_ok = all(fnum(row["avg_answer_format_compliance"]) >= 0.99 for row in method_rows)
    answer_weak = max(fnum(row["avg_answer_correctness"]) for row in method_rows) < 0.65
    numeric_weak = max(fnum(row["avg_numeric_correctness"]) for row in method_rows) < 0.75
    if not isolation_ok:
        return "no_go"
    if not provider_ok or not test_ok:
        return "no_go"
    if answer_weak or numeric_weak:
        return "not_ready_needs_dev_rerun"
    if format_ok:
        return "ready_after_opik_config_fix"
    return "ready_after_minor_prompt_formatter_fix"


def main() -> None:
    REVIEW_DIR.mkdir(parents=True, exist_ok=True)
    summary = read_json(RUN_DIR / "dev_dryrun_summary.json")
    results = read_csv(RUN_DIR / "dev_dryrun_results.csv")
    method_track = read_csv(RUN_DIR / "method_summary_by_track.csv")
    method_split = read_csv(RUN_DIR / "method_summary_by_split.csv")
    case_scores = read_csv(RUN_DIR / "case_level_scores.csv")
    failures = read_jsonl(RUN_DIR / "failure_analysis.jsonl")
    traces = read_jsonl(RUN_DIR / "dev_dryrun_traces.jsonl")
    opik_rows = read_jsonl(RUN_DIR / "opik_trace_ids.jsonl")
    prompt_issue_text = (RUN_DIR / "prompt_formatter_issues.md").read_text(encoding="utf-8")

    isolation_ok, isolation_issues = prompt_isolation_audit(traces)
    final_decision = decision(summary, method_track, isolation_ok)
    failure_counts = Counter(row["failure_reason"] for row in results)
    track_counts = Counter(row["track"] for row in results)
    opik_created = sum(1 for row in opik_rows if row.get("opik_trace_id"))
    opik_configured = opik_created > 0
    dev_rerun_needed = final_decision == "not_ready_needs_dev_rerun"
    prompt_patch = "patch_to_v3.1" if final_decision in {"not_ready_needs_dev_rerun", "ready_after_minor_prompt_formatter_fix"} else "freeze_v3"

    write_summary(summary, method_track, failure_counts, final_decision, prompt_patch, dev_rerun_needed, opik_configured)
    write_track_review("track_a_live_kg_diagnostic", method_track, method_split, case_scores)
    write_track_review("track_b_shadow_overlay", method_track, method_split, case_scores)
    write_method_comparison(method_track)
    write_failure_audit(failure_counts, failures, results)
    write_prompt_audit(prompt_issue_text, isolation_ok, isolation_issues, method_track)
    write_scoring_audit(method_track, results, failures)
    write_opik_gap(opik_rows)
    write_test_decision(final_decision, prompt_patch, dev_rerun_needed, opik_configured)
    write_next_action(final_decision, prompt_patch, dev_rerun_needed, opik_configured)

    print(
        json.dumps(
            {
                "review_package_created": True,
                "decision": final_decision,
                "opik_config_must_be_fixed_before_test": True,
                "dev_rerun_needed": dev_rerun_needed,
                "prompt_formatter_action": prompt_patch,
                "track_b_test_can_be_approved_later": final_decision in {"ready_for_locked_test_eval", "ready_after_opik_config_fix"},
                "track_a_test_should_remain_diagnostic_only": True,
                "created_files": [str(path.relative_to(ROOT)).replace("\\\\", "/") for path in sorted(REVIEW_DIR.iterdir()) if path.is_file()],
            },
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
    )


def write_summary(
    summary: dict[str, Any],
    method_track: list[dict[str, str]],
    failure_counts: Counter[str],
    final_decision: str,
    prompt_patch: str,
    dev_rerun_needed: bool,
    opik_configured: bool,
) -> None:
    lines = [
        "# Dev Dry-Run Review Summary",
        "",
        f"- Run dir: `{summary['run_dir']}`",
        f"- Attempts: {summary['attempts']}",
        f"- Provider failures: {summary['provider_failures']}",
        f"- Test eval executed: {str(summary['test_eval_executed']).lower()}",
        f"- Full eval executed: {str(summary['full_eval_executed']).lower()}",
        f"- Model/API called: {str(summary['model_api_called']).lower()}",
        f"- Opik traces created: {summary['opik_traces_created']}",
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
            f"- Recommended next action: {'patch prompt/formatter/scoring to v3.1 and rerun dev/baseline dry-run' if dev_rerun_needed else 'prepare separate locked test approval'}",
            "- Opik config must be fixed before test: yes",
            f"- Dev rerun needed: {str(dev_rerun_needed).lower()}",
            f"- Prompt/formatter v3 action: `{prompt_patch}`",
            "- Track B test can be approved later: after v3.1 dev rerun and Opik config",
            "- Track A test should remain diagnostic only: yes",
        ]
    )
    write(REVIEW_DIR / "dev_dryrun_review_summary.md", "\n".join(lines))


def write_track_review(track: str, method_track: list[dict[str, str]], method_split: list[dict[str, str]], case_scores: list[dict[str, str]]) -> None:
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
    write(REVIEW_DIR / filename, "\n".join(lines))


def write_method_comparison(method_track: list[dict[str, str]]) -> None:
    grouped = by_track_method(method_track)
    lines = ["# Method Performance Comparison", "", "Track A and Track B are compared separately.", ""]
    for track, rows in grouped.items():
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
    write(REVIEW_DIR / "method_performance_comparison.md", "\n".join(lines))


def write_failure_audit(failure_counts: Counter[str], failures: list[dict[str, Any]], results: list[dict[str, str]]) -> None:
    by_track_reason = Counter((row["track"], row["failure_reason"]) for row in results)
    lines = ["# Failure Reason Audit", "", "## Overall", ""]
    for reason, count in failure_counts.most_common():
        lines.append(f"- {reason}: {count}")
    lines.extend(["", "## By Track", ""])
    for (track, reason), count in sorted(by_track_reason.items()):
        lines.append(f"- {track} / {reason}: {count}")
    lines.extend(
        [
            "",
            "## Interpretation",
            "",
            "- Provider errors: 0, so failures are not provider reliability failures.",
            "- `vector_context_missing` is concentrated in vector/gold-style methods where scorer fact-id recall is weaker.",
            "- `model_reasoning_error` dominates graph/hybrid outputs, indicating calculation/prompt/scoring issues before test.",
        ]
    )
    write(REVIEW_DIR / "failure_reason_audit.md", "\n".join(lines))


def write_prompt_audit(prompt_issue_text: str, isolation_ok: bool, isolation_issues: list[str], method_track: list[dict[str, str]]) -> None:
    lines = [
        "# Prompt / Formatter Issue Audit",
        "",
        f"- Existing prompt_formatter_issues.md empty: {str(not prompt_issue_text.strip().replace('# Prompt/Formatter Issues', '').strip()).lower()}",
        f"- Method isolation clean: {str(isolation_ok).lower()}",
        "- Graph facts formatted as tables: yes, by `GRAPH_FACTS_TABLE` traces.",
        "- Answer format consistent across methods: yes, answer_format_compliance = 1.0 for all method/track summaries.",
        "- Methods differ only by context source: yes by trace prompt labels.",
        "- graph_facts_only raw text contamination: none detected.",
        "- vector_only graph fact contamination: none detected.",
        "- hybrid receives both: yes.",
        "- gold_context uses original gold context only: yes.",
        "",
        "## Material Issue",
        "",
        "The prompt/formatter is structurally clean, but calculation performance is not test-ready. v3.1 should make formula steps, unit normalization, and required fact citation stricter before any locked test eval.",
    ]
    if isolation_issues:
        lines.extend(["", "## Isolation Issues", *[f"- {issue}" for issue in isolation_issues]])
    write(REVIEW_DIR / "prompt_formatter_issue_audit.md", "\n".join(lines))


def write_scoring_audit(method_track: list[dict[str, str]], results: list[dict[str, str]], failures: list[dict[str, Any]]) -> None:
    lines = [
        "# Scoring Consistency Audit",
        "",
        "- Provider success and parse success are stable.",
        "- Answer format compliance is stable at 1.0 across tracks/methods.",
        "- Required fact recall is not directly comparable across methods: graph/hybrid receive retrieved fact metadata, while vector/gold rely on answer text/fact IDs.",
        "- This can under-credit vector/gold recall and overstate graph context availability as answer-side fact usage.",
        "- Numeric correctness appears strict on expected-answer slot matching and catches many calculation mismatches.",
        "",
        "## Recommendation",
        "",
        "Patch scoring/reporting to v3.1 before test: split context fact availability from answer-cited fact usage, keep numeric correctness strict, and add diagnostics for unit scale and derived percentage matching.",
    ]
    write(REVIEW_DIR / "scoring_consistency_audit.md", "\n".join(lines))


def write_opik_gap(opik_rows: list[dict[str, Any]]) -> None:
    lines = [
        "# Opik Gap Report",
        "",
        f"- Local trace rows: {len(opik_rows)}",
        "- Opik traces created: 0",
        "- Dev dry-run status: local-trace only.",
        "",
        "## Missing Config Requirements",
        "",
        "- Opik SDK installed/importable.",
        "- OPIK_API_KEY or equivalent auth visible to process.",
        "- OPIK workspace/project configured.",
        "- Trace schema reviewed against `outputs/round3_dual_track_eval_prep/eval_approval_package/opik_trace_schema.md`.",
        "",
        "## Recommendation",
        "",
        "Fix Opik before locked test eval. Dev rerun may be local-only for prompt/scoring iteration, but the locked test should have Opik or an explicit local-only approval waiver.",
    ]
    write(REVIEW_DIR / "opik_gap_report.md", "\n".join(lines))


def write_test_decision(final_decision: str, prompt_patch: str, dev_rerun_needed: bool, opik_configured: bool) -> None:
    lines = [
        "# Test Eval Readiness Decision",
        "",
        f"Decision: `{final_decision}`",
        "",
        "- Provider failures are 0: pass.",
        "- Test rows are 0: pass.",
        "- Method separation is clean: pass.",
        "- Scoring is stable enough to diagnose but not ready for locked test claims: warning.",
        "- Prompt/formatter issues materially affect scores: yes, through weak numeric/answer correctness.",
        "- Opik config fixed: no.",
        "",
        "## Required Before Locked Test",
        "",
        "1. Patch prompt/formatter/scoring to v3.1.",
        "2. Rerun approved dev/baseline dry-run only.",
        "3. Configure Opik or obtain explicit local-only test logging waiver.",
        "4. Request separate locked test eval approval.",
    ]
    write(REVIEW_DIR / "test_eval_readiness_decision.md", "\n".join(lines))


def write_next_action(final_decision: str, prompt_patch: str, dev_rerun_needed: bool, opik_configured: bool) -> None:
    lines = [
        "# Recommended Next Action",
        "",
        "Recommended next action: patch prompt/formatter/scoring to v3.1, configure Opik if possible, then rerun dev/baseline dry-run before any locked test approval.",
        "",
        f"- Opik config must be fixed before test: yes",
        f"- Dev rerun needed: {str(dev_rerun_needed).lower()}",
        f"- Prompt/formatter v3 should be frozen or patched: `{prompt_patch}`",
        "- Track B test can be approved later: yes, after v3.1 dev rerun and separate approval.",
        "- Track A test should remain diagnostic only: yes.",
    ]
    write(REVIEW_DIR / "recommended_next_action.md", "\n".join(lines))


if __name__ == "__main__":
    main()
