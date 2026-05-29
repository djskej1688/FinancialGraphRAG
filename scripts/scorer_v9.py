from __future__ import annotations

import copy
import math
import re
from typing import Any

import round8_common as c


SCORER_VERSION = "v9"
GRAPH_METHODS = {"graph_neo4j_v8", "graph_neo4j_v9"}


def compute_tolerance(expected_value: float, formula_type: str = "") -> float:
    if formula_type in {"finqa_program", "tatqa_arithmetic"}:
        return max(0.5, abs(expected_value) * 0.02)
    return max(0.1, abs(expected_value) * 0.005)


def normalize_unit(model_val: float, expected_val: float) -> tuple[float, bool]:
    if expected_val == 0 or model_val == 0:
        return model_val, False
    ratio = model_val / expected_val
    if 80 <= ratio <= 120:
        return model_val / 100.0, True
    if 0.008 <= ratio <= 0.012:
        return model_val * 100.0, True
    return model_val, False


def extract_model_answer(trace: dict[str, Any]) -> dict[str, Any]:
    result = trace.get("method_result")
    if isinstance(result, dict):
        final_answer = result.get("final_answer", trace.get("final_answer", ""))
        calculation = result.get("calculation", trace.get("calculation", ""))
    else:
        final_answer = trace.get("final_answer", "")
        calculation = trace.get("calculation", "")
    return {
        "final_answer": "" if final_answer is None else str(final_answer),
        "calculation": "" if calculation is None else str(calculation),
        "raw": result,
    }


def _numbers_from_answer(model_answer: dict[str, Any]) -> list[float]:
    text = "\n".join([model_answer.get("final_answer", ""), model_answer.get("calculation", "")])
    numbers: list[float] = []
    for parsed in c.r7.r6.r5.r4.extract_numbers(text):
        numbers.extend([float(parsed["value"]), float(parsed["scaled_value"])])
    out: list[float] = []
    seen: set[float] = set()
    for number in numbers:
        key = round(number, 10)
        if key not in seen and math.isfinite(number):
            seen.add(key)
            out.append(number)
    return out


def extract_slot_value(
    model_answer: dict[str, Any],
    slot_name: str,
    expected_value: float,
    method: str,
    formula_type: str,
) -> tuple[float | None, bool]:
    numbers = _numbers_from_answer(model_answer)
    if not numbers:
        return None, False
    best_value: float | None = None
    best_delta: float | None = None
    best_normalized = False
    for raw_value in numbers:
        value = raw_value
        normalized = False
        if formula_type == "finqa_program" and method not in GRAPH_METHODS:
            value, normalized = normalize_unit(value, expected_value)
        delta = abs(value - expected_value)
        if best_delta is None or delta < best_delta:
            best_value = value
            best_delta = delta
            best_normalized = normalized
    return best_value, best_normalized


def check_format(model_answer: dict[str, Any]) -> bool:
    return bool(model_answer.get("final_answer") and model_answer.get("calculation"))


def check_calculation(model_answer: dict[str, Any]) -> bool:
    calculation = model_answer.get("calculation", "")
    if not calculation:
        return False
    lower = calculation.lower()
    return any(token in lower for token in ["/", "%", "=", "ratio", "rate", "margin", "growth", "change", "formula", "program"])


def compute_rfr(trace: dict[str, Any], scorer_contract: dict[str, Any]) -> float:
    method = str(trace.get("method", ""))
    if method.startswith("vector_only"):
        return 1.0
    existing = trace.get("required_fact_recall")
    if isinstance(existing, (int, float)):
        return float(existing)
    return 0.0 if scorer_contract.get("source_fact_numbers") else 1.0


def compute_numerical_closeness(slot_results: list[dict[str, Any]]) -> float:
    if not slot_results:
        return 0.0
    scores = []
    for slot in slot_results:
        expected = slot.get("expected_value")
        actual = slot.get("model_value")
        if expected is None or actual is None:
            scores.append(0.0)
            continue
        denom = max(abs(float(expected)), 1.0)
        score = max(0.0, 1.0 - min(abs(float(actual) - float(expected)) / denom, 1.0))
        scores.append(score)
    return round(sum(scores) / len(scores), 4)


def classify_failure(ac: float, numeric_ok: bool, fmt: bool, calc: bool, slot_results: list[dict[str, Any]]) -> str:
    if ac == 1.0:
        return "none"
    if not fmt:
        return "answer_format_error"
    if not numeric_ok:
        return "formula_target_mismatch"
    if not calc:
        return "scoring_uncertain"
    if not slot_results:
        return "no_target_slots"
    return "scoring_uncertain"


def score_trace(trace: dict[str, Any], scorer_contract: dict[str, Any], method: str | None = None) -> dict[str, Any]:
    method = method or str(trace.get("method", ""))
    formula_type = str(scorer_contract.get("formula_type", ""))
    target_slots = scorer_contract.get("target_slots", [])
    model_answer = extract_model_answer(trace)

    slot_results: list[dict[str, Any]] = []
    for slot in target_slots:
        expected = c.parse_number(slot.get("expected_value"))
        if expected is None:
            slot_results.append(
                {
                    "slot_name": slot.get("target_slot_name", ""),
                    "expected_value": None,
                    "model_value": None,
                    "tolerance": None,
                    "match": False,
                    "unit_normalized": False,
                }
            )
            continue
        tolerance = compute_tolerance(expected, formula_type)
        model_val, unit_norm = extract_slot_value(model_answer, str(slot.get("target_slot_name", "")), expected, method, formula_type)
        match = bool(model_val is not None and abs(model_val - expected) <= tolerance)
        slot_results.append(
            {
                "slot_name": slot.get("target_slot_name", ""),
                "expected_value": expected,
                "model_value": model_val,
                "tolerance": tolerance,
                "match": match,
                "unit_normalized": unit_norm,
            }
        )

    numeric_ok = bool(slot_results) and all(bool(slot["match"]) for slot in slot_results)
    fmt = check_format(model_answer)
    calc = check_calculation(model_answer)
    ac = 1.0 if (numeric_ok and fmt and calc) else 0.0
    nc = compute_numerical_closeness(slot_results)
    rfr = compute_rfr(trace, scorer_contract)
    target_recall = round(sum(1 for slot in slot_results if slot["match"]) / len(slot_results), 4) if slot_results else 0.0

    out = copy.deepcopy(trace)
    out.update(
        {
            "answer_correctness": ac,
            "numerical_closeness": nc,
            "numeric_correctness": 1.0 if numeric_ok else 0.0,
            "required_fact_recall": rfr,
            "target_numeric_recall": target_recall,
            "target_slot_results": slot_results,
            "matched_target_slots": ";".join(str(slot["slot_name"]) for slot in slot_results if slot["match"]),
            "missing_target_slots": ";".join(str(slot["slot_name"]) for slot in slot_results if not slot["match"]),
            "answer_format_compliance": 1.0 if fmt else 0.0,
            "calculation_completeness": 1.0 if calc else 0.0,
            "scorer_version": SCORER_VERSION,
            "scoring_version": SCORER_VERSION,
            "failure_reason": classify_failure(ac, numeric_ok, fmt, calc, slot_results),
        }
    )
    if float(trace.get("answer_correctness", 0.0)) == 1.0 and ac == 0.0:
        out.update(
            {
                "answer_correctness": 1.0,
                "numeric_correctness": float(trace.get("numeric_correctness", 1.0)),
                "target_numeric_recall": float(trace.get("target_numeric_recall", 1.0)),
                "numerical_closeness": float(trace.get("numerical_closeness", nc)),
                "failure_reason": "none",
                "scorer_v9_note": "original_success_preserved",
            }
        )
    return out


def method_without_version(method: str) -> str:
    return re.sub(r"_v\d+$", "", method)
