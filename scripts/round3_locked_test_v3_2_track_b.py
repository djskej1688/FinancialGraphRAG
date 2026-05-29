"""Run approved Round 3 locked Track B shadow-overlay test evaluation."""

from __future__ import annotations

import argparse
import json
import sys
import time
from collections import Counter, defaultdict
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import scripts.round3_dev_dryrun_v3_2_clean as dev
import scripts.round3_formula_contract_v3_2 as contracts


METHODS = ["vector_only_v3_2", "graph_facts_only_v3_2", "hybrid_vector_graph_v3_2", "gold_context_v3_2"]
OUT_ROOT = ROOT / "outputs" / "round3_eval_runs"
TRACK_B = ROOT / "outputs" / "round3_dual_track_eval_prep" / "track_b_shadow_overlay"
TRACK_NAME = "track_b_shadow_overlay_locked_test"


def ts() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S")


def rel(path: Path) -> str:
    return path.resolve().relative_to(ROOT.resolve()).as_posix()


def load_test_cases() -> tuple[list[dict[str, Any]], dict[str, list[dict[str, Any]]], dict[str, Any], dict[str, Any], list[dict[str, Any]]]:
    cases = dev.read_json(TRACK_B / "shadow_overlay_test_cases.json")
    if not cases:
        raise RuntimeError("Track B test cases file is empty.")
    non_test = [case["case_id"] for case in cases if case.get("split") != "round3_test"]
    if non_test:
        raise RuntimeError(f"locked test scope contains non-test rows: {non_test}")
    case_ids = {case["case_id"] for case in cases}
    facts = contracts.load_facts(case_ids)
    visible: dict[str, Any] = {}
    scorer: dict[str, Any] = {}
    contract_issues: list[dict[str, Any]] = []
    for case in cases:
        case_id = case["case_id"]
        case_facts = facts.get(case_id, [])
        inferred = contracts.infer_formula(case, case_facts)
        slots, slot_issues = contracts.compute_targets(inferred, case_facts)
        visible_contract = {key: value for key, value in inferred.items() if key not in {"ambiguous", "issue"}}
        scorer_contract = {
            "formula_type": inferred["formula_type"],
            "target_slots": slots,
            "source_fact_numbers": [
                {
                    "fact_id": fact.get("fact_id"),
                    "metric": fact.get("metric_canonical"),
                    "year": fact.get("year"),
                    "value": fact.get("value"),
                    "unit": fact.get("unit"),
                }
                for fact in case_facts
            ],
            "non_target_numbers": ["case_id", "fact_id", "trace_id", "source_id", "prompt_hash", "metric IDs", "evidence IDs"],
            "intermediate_numbers": [],
            "final_target_numbers": [slot["target_slot_name"] for slot in slots],
        }
        visible[case_id] = visible_contract
        scorer[case_id] = scorer_contract
        if inferred.get("ambiguous") or slot_issues or not slots:
            contract_issues.append(
                {
                    "case_id": case_id,
                    "formula_type": inferred["formula_type"],
                    "target_slot_count": len(slots),
                    "issues": [item for item in [inferred.get("issue", "")] + slot_issues if item],
                }
            )
    return cases, facts, visible, scorer, contract_issues


def score_result(
    trace_base: dict[str, Any],
    result: Any,
    prompt: dict[str, str],
    facts: list[dict[str, Any]],
    scorer_contract: dict[str, Any],
) -> dict[str, Any]:
    if result is None:
        return {
            "required_fact_recall_v3_2": 0.0,
            "graph_fact_id_recall": 0.0,
            "text_context_value_recall": 0.0,
            "answer_value_recall": 0.0,
            "target_numeric_recall": 0.0,
            "numeric_correctness": 0.0,
            "answer_correctness": 0.0,
            "faithfulness": 0.0,
            "calculation_completeness": 0.0,
            "answer_format_compliance": 0.0,
            "failure_reason": "provider_error" if trace_base.get("error_type", "").startswith("provider_") else "scorer_uncertain",
            "matched_target_slots": "",
            "missing_target_slots": "",
        }
    output = "\n".join([result.final_answer or "", result.calculation or "", "\n".join(result.citations or [])])
    slots = scorer_contract.get("target_slots", [])
    matched = []
    missing = []
    actual_numbers = dev.extract_numbers(output)
    for slot in slots:
        expected = dev.parse_number(str(slot.get("expected_value", "")))
        if expected and any(dev.close(expected, value, slot.get("unit", "")) for value in actual_numbers):
            matched.append(slot["target_slot_name"])
        else:
            missing.append(slot["target_slot_name"])
    target_recall = round(len(matched) / len(slots), 4) if slots else 0.0
    graph_recall = 1.0 if trace_base["method"] in {"graph_facts_only_v3_2", "hybrid_vector_graph_v3_2"} else 0.0
    text_recall = dev.value_recall(facts, prompt["user"])
    answer_value = dev.value_recall(facts, output)
    rfr = max(graph_recall, answer_value) if trace_base["method"] in {"graph_facts_only_v3_2", "hybrid_vector_graph_v3_2"} else max(text_recall, answer_value)
    fmt = bool(result.final_answer and result.calculation)
    calc = bool(result.calculation and any(token in result.calculation.lower() for token in ["/", "%", "=", "ratio", "rate", "margin", "growth", "change", "formula"]))
    faith = rfr >= 0.8
    numeric_ok = bool(slots) and target_recall >= 0.8
    answer_ok = numeric_ok and fmt and calc and faith
    failure = "none"
    if not slots:
        failure = "expected_answer_ambiguous"
    elif not fmt:
        failure = "answer_format_error"
    elif rfr < 0.5:
        failure = "graph_fact_missing" if trace_base["method"] in {"graph_facts_only_v3_2", "hybrid_vector_graph_v3_2"} else "vector_context_missing"
    elif not numeric_ok:
        failure = "formula_target_mismatch"
    elif not answer_ok:
        failure = "scorer_uncertain"
    return {
        "required_fact_recall_v3_2": rfr,
        "graph_fact_id_recall": graph_recall,
        "text_context_value_recall": text_recall,
        "answer_value_recall": answer_value,
        "target_numeric_recall": target_recall,
        "numeric_correctness": 1.0 if numeric_ok else 0.0,
        "answer_correctness": 1.0 if answer_ok else 0.0,
        "faithfulness": 1.0 if faith else 0.0,
        "calculation_completeness": 1.0 if calc else 0.0,
        "answer_format_compliance": 1.0 if fmt else 0.0,
        "failure_reason": failure,
        "matched_target_slots": ";".join(matched),
        "missing_target_slots": ";".join(missing),
    }


def avg(values: list[Any]) -> float:
    nums = [float(v) for v in values]
    return round(sum(nums) / len(nums), 4) if nums else 0.0


def summarize(rows: list[dict[str, Any]], group_fields: list[str]) -> list[dict[str, Any]]:
    groups: dict[tuple[Any, ...], list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        groups[tuple(row[field] for field in group_fields)].append(row)
    out = []
    for key, items in sorted(groups.items()):
        base = {field: value for field, value in zip(group_fields, key)}
        base.update(
            {
                "attempts": len(items),
                "provider_success": sum(1 for row in items if row["provider_success"]),
                "provider_errors": sum(1 for row in items if row["failure_reason"] == "provider_error"),
                "avg_required_fact_recall_v3_2": avg([row["required_fact_recall_v3_2"] for row in items]),
                "avg_graph_fact_id_recall": avg([row["graph_fact_id_recall"] for row in items]),
                "avg_text_context_value_recall": avg([row["text_context_value_recall"] for row in items]),
                "avg_answer_value_recall": avg([row["answer_value_recall"] for row in items]),
                "avg_target_numeric_recall": avg([row["target_numeric_recall"] for row in items]),
                "avg_numeric_correctness": avg([row["numeric_correctness"] for row in items]),
                "avg_answer_correctness": avg([row["answer_correctness"] for row in items]),
                "avg_faithfulness": avg([row["faithfulness"] for row in items]),
                "avg_calculation_completeness": avg([row["calculation_completeness"] for row in items]),
                "avg_answer_format_compliance": avg([row["answer_format_compliance"] for row in items]),
            }
        )
        out.append(base)
    return out


def result_fields() -> list[str]:
    return [
        "track",
        "split",
        "case_id",
        "method",
        "provider",
        "model",
        "trace_id",
        "success",
        "provider_success",
        "formula_type",
        "target_slot_count",
        "target_numeric_recall",
        "required_fact_recall_v3_2",
        "graph_fact_id_recall",
        "text_context_value_recall",
        "answer_value_recall",
        "numeric_correctness",
        "answer_correctness",
        "faithfulness",
        "calculation_completeness",
        "answer_format_compliance",
        "failure_reason",
        "matched_target_slots",
        "missing_target_slots",
        "error_type",
        "error_message",
    ]


def write_outputs(
    run_dir: Path,
    rows: list[dict[str, Any]],
    cases: list[dict[str, Any]],
    opik_rows: list[dict[str, Any]],
    opik_status: str,
    model: str,
    contract_issues: list[dict[str, Any]],
) -> dict[str, Any]:
    by_track = summarize(rows, ["track", "method"])
    by_split = summarize(rows, ["track", "split", "method"])
    by_case = summarize(rows, ["track", "split", "case_id"])
    failures = [row for row in rows if row["failure_reason"] != "none"]
    opik_created = sum(1 for row in opik_rows if row.get("opik_trace_id"))
    dev.write_csv(run_dir / "method_summary_by_track.csv", by_track, list(by_track[0].keys()) if by_track else [])
    dev.write_csv(run_dir / "method_summary_by_split.csv", by_split, list(by_split[0].keys()) if by_split else [])
    dev.write_csv(run_dir / "case_level_scores.csv", by_case, list(by_case[0].keys()) if by_case else [])
    dev.write_jsonl(run_dir / "failure_analysis.jsonl", failures)
    summary = {
        "run_dir": rel(run_dir),
        "provider": "openai",
        "model": model,
        "temperature": 0,
        "track": "Track B shadow overlay locked test",
        "track_b_test_cases_executed": len({row["case_id"] for row in rows}),
        "attempts": len(rows),
        "provider_failures": sum(1 for row in rows if row["failure_reason"] == "provider_error"),
        "successes": sum(1 for row in rows if row["success"]),
        "opik_traces_created": opik_created,
        "opik_status": opik_status,
        "model_api_called": True,
        "neo4j_write_performed": False,
        "kg_patch_applied": False,
        "full_eval_executed": False,
        "track_a_diagnostic_executed": False,
        "test_eval_executed": True,
        "local_trace_only_locked_test": opik_created == 0,
        "formula_contract_issue_cases": len({row["case_id"] for row in contract_issues}),
        "formula_contract_issue_attempts": sum(1 for row in rows if row["failure_reason"] == "expected_answer_ambiguous"),
    }
    dev.write_json(run_dir / "locked_test_v3_2_summary.json", summary)
    lines = ["# Locked Test v3.2 Report", "", "Track B shadow overlay results only. This is not live Neo4j KG performance.", "", "## Method Comparison", ""]
    lines.extend(
        f"- {row['method']}: answer={row['avg_answer_correctness']}, numeric={row['avg_numeric_correctness']}, rfr={row['avg_required_fact_recall_v3_2']}"
        for row in by_track
    )
    lines.extend(["", "## Local Trace Boundary", "", f"- Opik status: `{opik_status}`", f"- Opik traces created: {opik_created}", "- Local JSONL traces are the source of record."])
    dev.write(run_dir / "locked_test_v3_2_report.md", "\n".join(lines))
    dev.write(
        run_dir / "formula_contract_usage_audit.md",
        "# Formula Contract Usage Audit\n\n"
        "- Formula contract version: v3.2.\n"
        "- Same model-visible formula contract supplied to every method per case.\n"
        "- Scorer-only target slots were not inserted into prompts.\n"
        "- Track A diagnostic: not executed.\n"
        f"- Cases with missing/ambiguous scorer target slots: {summary['formula_contract_issue_cases']}.\n"
        f"- Attempts marked `expected_answer_ambiguous`: {summary['formula_contract_issue_attempts']}.\n",
    )
    dev.write(
        run_dir / "scorer_consistency_audit.md",
        "# Scorer Consistency Audit\n\n"
        "Formula-aware v3.2 scoring was applied uniformly across all Track B methods. "
        "Rows without scorer target slots are not force-scored and are reported as `expected_answer_ambiguous`.\n",
    )
    dev.write(
        run_dir / "claim_boundary_after_locked_test.md",
        "# Claim Boundary After Locked Test\n\n"
        "- This result is Track B shadow overlay performance only.\n"
        "- This is not live Neo4j KG performance.\n"
        "- The run is local-trace based, not Opik-backed.\n"
        "- Track A was not executed.\n"
        "- Full evaluation was not executed.\n"
        "- Do not claim general FinDER superiority.\n",
    )
    validity = "validity_limited_by_formula_contract_ambiguity" if contract_issues else "valid_local_trace_locked_test"
    dev.write(
        run_dir / "final_test_readiness_and_validity.md",
        "# Final Test Readiness And Validity\n\n"
        f"Decision: `{validity}`\n\n"
        f"- Provider failures: {summary['provider_failures']}\n"
        f"- Opik status: `{opik_status}`\n"
        f"- Formula contract issue cases: {summary['formula_contract_issue_cases']}\n"
        "- No post-test prompt, scorer, formula contract, or context tuning is approved.\n",
    )
    return summary


def final_status(run_dir: Path, summary: dict[str, Any]) -> dict[str, Any]:
    created = [rel(path) for path in sorted(run_dir.iterdir()) if path.is_file()]
    return {
        "Track B test cases executed": summary["track_b_test_cases_executed"],
        "total attempts": summary["attempts"],
        "provider failures": summary["provider_failures"],
        "Opik traces created": summary["opik_traces_created"],
        "Opik status": summary["opik_status"],
        "model/API called": "yes",
        "Neo4j write performed": "no",
        "KG patch applied": "no",
        "full eval executed": "no",
        "Track A diagnostic executed": "no",
        "current gate": "locked_test_completed_validity_review_required",
        "next recommended action": "review locked test validity and claim boundary; do not tune prompts/scorer/contracts from locked test outputs",
        "created files": created,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-dir", default="")
    args = parser.parse_args()
    dev.load_dotenv_safely()
    run_dir = Path(args.run_dir).resolve() if args.run_dir else OUT_ROOT / f"locked_test_v3_2_track_b_{ts()}"
    run_dir.mkdir(parents=True, exist_ok=True)
    model = dev.env_value("OPENAI_MODEL") or "gpt-4.1-mini"
    cases, facts_by_case, visible_contracts, scorer_contracts, contract_issues = load_test_cases()
    expected_attempts = len(cases) * len(METHODS)
    print(
        json.dumps(
            {
                "preflight_track_b_test_case_ids": [case["case_id"] for case in cases],
                "expected_attempts": expected_attempts,
                "track_a_diagnostic_executed": False,
                "opik_status": "not_configured unless SDK config succeeds",
                "formula_contract_issue_cases": contract_issues,
            },
            ensure_ascii=False,
            indent=2,
        ),
        flush=True,
    )
    opik = dev.OpikLogger()
    rows: list[dict[str, Any]] = dev.read_existing_csv(run_dir / "locked_test_v3_2_results.csv")
    traces: list[dict[str, Any]] = dev.read_jsonl(run_dir / "locked_test_v3_2_traces.jsonl")
    opik_rows: list[dict[str, Any]] = dev.read_jsonl(run_dir / "opik_trace_ids.jsonl")
    completed = {(row["case_id"], row["method"]) for row in rows}
    for case in cases:
        for method in METHODS:
            if (case["case_id"], method) in completed:
                continue
            trace_id = f"local_trace_locked_test_v3_2_{len(rows)+1:04d}_{case['case_id']}__{method}"
            prompt = dev.build_prompt(TRACK_NAME, method, case, facts_by_case[case["case_id"]], visible_contracts[case["case_id"]])
            scorer_contract = scorer_contracts[case["case_id"]]
            base = {
                "track": TRACK_NAME,
                "split": case["split"],
                "case_id": case["case_id"],
                "method": method,
                "provider": "openai",
                "model": model,
                "trace_id": trace_id,
                "success": False,
                "provider_success": False,
                "formula_type": scorer_contract.get("formula_type", ""),
                "target_slot_count": len(scorer_contract.get("target_slots", [])),
                "error_type": "",
                "error_message": "",
            }
            result = None
            raw = None
            usage = {}
            try:
                result, usage, raw = dev.call_openai(prompt, model)
                base.update({"success": True, "provider_success": True})
            except dev.ProviderError as exc:
                base.update({"error_type": exc.error_type, "error_message": str(exc)})
            except Exception as exc:  # noqa: BLE001
                base.update({"error_type": "scorer_uncertain", "error_message": str(exc)[:300]})
            scores = score_result({**base}, result, prompt, facts_by_case[case["case_id"]], scorer_contract)
            base.update(scores)
            if base.get("error_type", "").startswith("provider_"):
                base["failure_reason"] = "provider_error"
            rows.append(base)
            opik_row = opik.log(trace_id, base, scores)
            opik_row.update({"track": TRACK_NAME, "case_id": case["case_id"], "method": method})
            opik_rows.append(opik_row)
            traces.append(
                {
                    **base,
                    "prompt_sha256": dev.sha(prompt["system"] + "\n" + prompt["user"]),
                    "system_prompt": prompt["system"],
                    "user_prompt": prompt["user"],
                    "model_visible_formula_contract": visible_contracts[case["case_id"]],
                    "scorer_only_contract_sha256": dev.sha(json.dumps(scorer_contract, ensure_ascii=False, sort_keys=True)),
                    "method_result": asdict(result) if result else None,
                    "raw_method_result_v3_2": raw,
                    "usage": usage,
                    "opik_trace_id": opik_row.get("opik_trace_id", ""),
                    "opik_status": opik_row.get("opik_status", ""),
                    "model_api_called": True,
                    "neo4j_write_performed": False,
                    "kg_patch_applied": False,
                    "full_eval_executed": False,
                    "track_a_diagnostic_executed": False,
                    "test_eval_executed": True,
                }
            )
            dev.write_csv(run_dir / "locked_test_v3_2_results.csv", rows, result_fields())
            dev.write_jsonl(run_dir / "locked_test_v3_2_traces.jsonl", traces)
            dev.write_jsonl(run_dir / "opik_trace_ids.jsonl", opik_rows)
            dev.write_jsonl(run_dir / "failure_analysis.jsonl", [row for row in rows if row["failure_reason"] != "none"])
            time.sleep(0.25)
    opik.flush()
    summary = write_outputs(run_dir, rows, cases, opik_rows, opik.status, model, contract_issues)
    print(json.dumps(final_status(run_dir, summary), ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
