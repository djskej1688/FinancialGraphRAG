from __future__ import annotations

import json
import re
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

import round8_common as c


R8_TRACES = c.ROOT / "outputs" / "round3_eval_runs" / "round8_eval_20260529_103625" / "round8_traces.jsonl"
R8_FINDER = c.ROOT / "outputs" / "round8_case_selection" / "finder_candidates.jsonl"
R8_FINQA = c.ROOT / "outputs" / "round8_case_selection" / "finqa_candidates.jsonl"
OUT_DIR = c.ROOT / "outputs" / "round9a_sensitivity"

V1_OUT = OUT_DIR / "r8_rescored_tolerance_2pct.jsonl"
V2_OUT = OUT_DIR / "r8_rescored_tolerance_2pct_unit_norm.jsonl"
V3_OUT = OUT_DIR / "r8_rescored_partial_credit_nc90.jsonl"
V4_OUT = OUT_DIR / "r8_sensitivity_no_suspect_tickers.jsonl"
FORMAT_OUT = OUT_DIR / "format_error_analysis.json"
TABLE_OUT = OUT_DIR / "comparison_table.md"
STATE_OUT = OUT_DIR / "state.json"

SUSPICIOUS_TICKERS = {"CAGR", "OF", "LOSS"}


def avg(values: list[Any]) -> float:
    nums = [float(value) for value in values]
    return round(sum(nums) / len(nums), 4) if nums else 0.0


def method_base(method: str) -> str:
    return method.replace("_v8", "")


def original_summary(rows: list[dict[str, Any]], excluded: set[str] | None = None, ac_field: str = "answer_correctness") -> dict[str, float]:
    excluded = excluded or set()
    out = {}
    for method in c.METHODS:
        selected = [row for row in rows if row["method"] == method and row["case_id"] not in excluded]
        out[method_base(method)] = avg([row[ac_field] for row in selected])
    return out


def dataset_summary(rows: list[dict[str, Any]], excluded: set[str] | None = None, ac_field: str = "answer_correctness") -> dict[tuple[str, str], float]:
    excluded = excluded or set()
    out = {}
    for dataset in ["FinDER", "FinQA"]:
        for method in c.METHODS:
            selected = [row for row in rows if row["source_dataset"] == dataset and row["method"] == method and row["case_id"] not in excluded]
            out[(dataset, method_base(method))] = avg([row[ac_field] for row in selected])
    return out


def tolerance_2pct(expected_value: float) -> float:
    return max(0.5, abs(expected_value) * 0.02)


def is_unit_mismatch(model_val: float, expected_val: float) -> bool:
    if expected_val == 0 or model_val == 0:
        return False
    ratio = model_val / expected_val
    return 80 <= ratio <= 120 or 0.008 <= ratio <= 0.012


def normalized_value(model_val: float, expected_val: float) -> tuple[float, bool]:
    if not is_unit_mismatch(model_val, expected_val):
        return model_val, False
    candidates = [model_val, model_val / 100.0, model_val * 100.0]
    best = min(candidates, key=lambda value: abs(value - expected_val))
    return best, best != model_val


def output_numbers(row: dict[str, Any]) -> list[float]:
    text = "\n".join([str(row.get("final_answer") or ""), str(row.get("calculation") or "")])
    nums = []
    for parsed in c.r7.r6.r5.r4.extract_numbers(text):
        nums.extend([float(parsed["value"]), float(parsed["scaled_value"])])
    # Keep order but remove exact duplicates.
    deduped = []
    seen = set()
    for value in nums:
        key = round(value, 10)
        if key not in seen:
            seen.add(key)
            deduped.append(value)
    return deduped


def rescore_slots(row: dict[str, Any], contract: dict[str, Any], unit_norm: bool) -> dict[str, Any]:
    nums = output_numbers(row)
    slot_results = []
    any_unit_normalized = False
    for slot in contract.get("target_slots", []):
        expected = c.parse_number(slot.get("expected_value"))
        if expected is None or not nums:
            slot_results.append({"slot": slot.get("target_slot_name"), "match": False, "expected_value": expected, "model_value": None})
            continue
        tol = tolerance_2pct(expected)
        best = None
        best_delta = None
        best_norm = False
        for raw_value in nums:
            score_value = raw_value
            normalized = False
            if unit_norm:
                score_value, normalized = normalized_value(raw_value, expected)
            delta = abs(score_value - expected)
            if best_delta is None or delta < best_delta:
                best = score_value
                best_delta = delta
                best_norm = normalized
        match = bool(best_delta is not None and best_delta <= tol)
        any_unit_normalized = any_unit_normalized or best_norm
        slot_results.append(
            {
                "slot": slot.get("target_slot_name"),
                "match": match,
                "expected_value": expected,
                "model_value": best,
                "delta": best_delta,
                "tolerance": tol,
                "unit_normalized": best_norm,
            }
        )
    numeric_ok = bool(slot_results) and all(item["match"] for item in slot_results)
    fmt = bool(row.get("answer_format_compliance", 0.0) == 1.0 or (row.get("final_answer") and row.get("calculation")))
    return {
        "slot_results": slot_results,
        "answer_correctness": 1.0 if numeric_ok and fmt else 0.0,
        "target_numeric_recall": round(sum(1 for item in slot_results if item["match"]) / len(slot_results), 4) if slot_results else row.get("target_numeric_recall", 0.0),
        "unit_normalized": any_unit_normalized,
        "status": "rescored_from_output_numbers" if slot_results else "no_slot_data",
    }


def variant_tolerance(rows: list[dict[str, Any]], contracts: dict[str, Any], unit_norm: bool) -> list[dict[str, Any]]:
    out = []
    variant = "tolerance_2pct_unit_norm" if unit_norm else "tolerance_2pct"
    field = "answer_correctness_v2_tol2pct_unit" if unit_norm else "answer_correctness_v1_tol2pct"
    for row in rows:
        new = dict(row)
        new["answer_correctness_r8_original"] = row["answer_correctness"]
        new["rescore_variant"] = variant
        applied = row.get("source_dataset") == "FinQA" and row.get("formula_type") == "finqa_program"
        new["rescore_applied"] = applied
        if applied:
            if float(row.get("answer_correctness", 0.0)) == 1.0:
                new[field] = 1.0
                new["answer_correctness"] = 1.0
                new["target_slot_results_rescored"] = []
                new["rescore_status"] = "original_success_preserved"
                if unit_norm:
                    new["unit_normalized"] = False
            else:
                score = rescore_slots(row, contracts[row["case_id"]], unit_norm)
                new[field] = score["answer_correctness"]
                new["answer_correctness"] = score["answer_correctness"]
                new["target_numeric_recall"] = score["target_numeric_recall"]
                new["target_slot_results_rescored"] = score["slot_results"]
                new["rescore_status"] = score["status"]
                if unit_norm:
                    new["unit_normalized"] = score["unit_normalized"]
        else:
            new[field] = row["answer_correctness"]
            if unit_norm:
                new["unit_normalized"] = False
        out.append(new)
    return out


def variant_partial(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out = []
    for row in rows:
        new = dict(row)
        original = float(row["answer_correctness"])
        nc = float(row.get("numerical_closeness", 0.0))
        if original == 1.0:
            ac = 1.0
            applied = False
        elif nc >= 0.90:
            ac = 0.5
            applied = True
        else:
            ac = 0.0
            applied = False
        new["answer_correctness_r8_original"] = original
        new["answer_correctness_v3_partial"] = ac
        new["answer_correctness"] = ac
        new["partial_credit_applied"] = applied
        new["rescore_variant"] = "partial_credit_nc90"
        out.append(new)
    return out


def variant_no_suspect(rows: list[dict[str, Any]], suspect_case_ids: set[str]) -> list[dict[str, Any]]:
    out = []
    for row in rows:
        new = dict(row)
        new["answer_correctness_r8_original"] = row["answer_correctness"]
        new["excluded_suspect_ticker"] = row["case_id"] in suspect_case_ids
        new["rescore_variant"] = "no_suspect_tickers"
        out.append(new)
    return out


def diagnose_format_error(row: dict[str, Any]) -> str:
    raw = row.get("raw_method_result_v8") or row.get("method_result") or {}
    final = row.get("final_answer") or ""
    calc = row.get("calculation") or ""
    text = json.dumps(raw, ensure_ascii=False) if isinstance(raw, (dict, list)) else str(raw)
    if not final:
        return "missing_final_answer_field"
    if re_search_unit_mismatch(final + " " + calc):
        return "wrong_unit_label"
    if len(final.split()) > 50 or "because" in final.lower():
        return "extra_explanation_in_slot"
    if "final_answer" not in text and isinstance(raw, dict):
        return "missing_final_answer_field"
    return "other"


def re_search_unit_mismatch(text: str) -> bool:
    lower = text.lower()
    return ("ratio" in lower and "%" in lower) or ("percent" in lower and "times" in lower)


def format_error_analysis(rows: list[dict[str, Any]]) -> dict[str, Any]:
    cases = []
    for row in rows:
        if row.get("failure_reason") == "answer_format_error" and row.get("method") == "graph_neo4j_v8":
            diagnosis = diagnose_format_error(row)
            cases.append(
                {
                    "case_id": row["case_id"],
                    "source_dataset": row.get("source_dataset"),
                    "formula_type": row.get("formula_type"),
                    "model_raw_answer": row.get("raw_method_result_v8") or row.get("method_result"),
                    "expected_format": "JSON object with final_answer and calculation fields",
                    "diagnosis": diagnosis,
                }
            )
    return {
        "total_format_errors": len(cases),
        "by_dataset": dict(Counter(case["source_dataset"] for case in cases)),
        "by_formula_type": dict(Counter(case["formula_type"] for case in cases)),
        "by_diagnosis": dict(Counter(case["diagnosis"] for case in cases)),
        "cases": cases,
    }


def write_comparison(
    original: list[dict[str, Any]],
    v1: list[dict[str, Any]],
    v2: list[dict[str, Any]],
    v3: list[dict[str, Any]],
    v4: list[dict[str, Any]],
    suspect_case_ids: set[str],
) -> None:
    summaries = {
        "R8_original": original_summary(original),
        "V1: tol_2pct": original_summary(v1),
        "V2: tol_2pct_unit": original_summary(v2),
        "V3: partial_nc90": original_summary(v3),
        "V4: no_suspect": original_summary(v4, suspect_case_ids),
    }
    dataset_summaries = {
        "R8_original": dataset_summary(original),
        "V1: tol_2pct": dataset_summary(v1),
        "V2: tol_2pct_unit": dataset_summary(v2),
        "V3: partial_nc90": dataset_summary(v3),
        "V4: no_suspect": dataset_summary(v4, suspect_case_ids),
    }
    lines = [
        "## R8 Scorer Sensitivity - Overall (50 cases)",
        "",
        "| Variant | vector ac | graph ac | hybrid ac | note |",
        "|---|---:|---:|---:|---|",
    ]
    notes = {
        "R8_original": "baseline",
        "V1: tol_2pct": "FinQA tolerance relaxed",
        "V2: tol_2pct_unit": "V1 + ratio/percent normalization",
        "V3: partial_nc90": "nc >= .90 gives ac=0.5",
        "V4: no_suspect": "CAGR/OF/LOSS excluded from aggregation",
    }
    for name, summary in summaries.items():
        lines.append(f"| {name} | {summary['vector_only']} | {summary['graph_neo4j']} | {summary['hybrid_neo4j']} | {notes[name]} |")
    lines.extend(
        [
            "",
            "## By Dataset",
            "",
            "| Variant | FinDER graph | FinQA graph | FinDER vector | FinQA vector |",
            "|---|---:|---:|---:|---:|",
        ]
    )
    for name, summary in dataset_summaries.items():
        lines.append(
            f"| {name} | {summary[('FinDER', 'graph_neo4j')]} | {summary[('FinQA', 'graph_neo4j')]} | "
            f"{summary[('FinDER', 'vector_only')]} | {summary[('FinQA', 'vector_only')]} |"
        )
    v1_finqa_graph = dataset_summaries["V1: tol_2pct"][("FinQA", "graph_neo4j")]
    v2_finqa_graph = dataset_summaries["V2: tol_2pct_unit"][("FinQA", "graph_neo4j")]
    v3_graph = summaries["V3: partial_nc90"]["graph_neo4j"]
    lines.extend(
        [
            "",
            "## Interpretation",
            "",
            f"- V2 - V1 FinQA graph delta: {round(v2_finqa_graph - v1_finqa_graph, 4)}.",
            f"- V3 - V1 overall graph delta: {round(v3_graph - summaries['V1: tol_2pct']['graph_neo4j'], 4)}.",
            f"- Suspect ticker cases removed: {len(suspect_case_ids)}.",
        ]
    )
    c.write_text(TABLE_OUT, "\n".join(lines))


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    rows = c.read_jsonl(R8_TRACES)
    if len(rows) != 150:
        raise RuntimeError(f"Expected 150 R8 traces, found {len(rows)}")
    contracts, _visible = c.load_contract_maps()
    finder = c.read_jsonl(R8_FINDER)
    suspect_case_ids = {row["case_id"] for row in finder if str(row.get("ticker", "")).upper() in SUSPICIOUS_TICKERS}

    original = original_summary(rows)
    if original != {"vector_only": 0.36, "graph_neo4j": 0.46, "hybrid_neo4j": 0.4}:
        print(json.dumps({"warning": "R8 original differs from spec", "original": original}, ensure_ascii=False))

    v1 = variant_tolerance(rows, contracts, unit_norm=False)
    v2 = variant_tolerance(rows, contracts, unit_norm=True)
    v3 = variant_partial(rows)
    v4 = variant_no_suspect(rows, suspect_case_ids)
    fmt = format_error_analysis(rows)

    c.write_jsonl(V1_OUT, v1)
    c.write_jsonl(V2_OUT, v2)
    c.write_jsonl(V3_OUT, v3)
    c.write_jsonl(V4_OUT, v4)
    c.write_json(FORMAT_OUT, fmt)
    write_comparison(rows, v1, v2, v3, v4, suspect_case_ids)

    state = {
        "phase": "9a_done",
        "round": "round9a",
        "claim_boundary": "r8_scorer_sensitivity_no_model_rerun",
        "r8_traces_source": c.rel(R8_TRACES),
        "r8_original_graph_ac": original["graph_neo4j"],
        "r8_original_vector_ac": original["vector_only"],
        "r8_original_hybrid_ac": original["hybrid_neo4j"],
        "v1_tol2pct_graph_ac": original_summary(v1)["graph_neo4j"],
        "v2_tol2pct_unit_graph_ac": original_summary(v2)["graph_neo4j"],
        "v3_partial_nc90_graph_ac": original_summary(v3)["graph_neo4j"],
        "v4_no_suspect_graph_ac": original_summary(v4, suspect_case_ids)["graph_neo4j"],
        "suspect_cases_removed": len(suspect_case_ids),
        "suspect_case_ids": sorted(suspect_case_ids),
        "format_error_cases": fmt["total_format_errors"],
        "model_calls": 0,
        "completed_at": c.utc_now(),
    }
    c.write_json(STATE_OUT, state)
    print(json.dumps(state, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
