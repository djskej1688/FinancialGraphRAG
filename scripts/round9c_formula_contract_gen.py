from __future__ import annotations

import json
import re
from collections import Counter
from typing import Any

import round8_common as r8
import round9c_common as c


def clean_metric(value: str) -> str:
    value = re.sub(r"[^a-zA-Z0-9]+", "_", value.lower()).strip("_")
    return value or "value"


def infer_formula_type(question: str, evidence: str = "") -> str:
    text = f"{question} {evidence[:600]}".lower()
    if "gross margin" in text:
        return "gross_margin"
    if "operating margin" in text:
        return "operating_margin"
    if "net margin" in text:
        return "net_margin"
    if "tax rate" in text or "effective tax" in text:
        return "effective_tax_rate"
    if "revenue" in text and any(term in text for term in ["change", "growth", "increase", "decrease", "year-over-year", "yoy"]):
        return "yoy_revenue_change"
    if "segment" in text or "business unit" in text:
        return "segment_comparison"
    if "eps" in text or "earnings per share" in text:
        return "eps_dilution"
    if "debt" in text or "interest coverage" in text or "leverage" in text:
        return "debt_metrics"
    if "capital expenditure" in text or "capex" in text:
        return "capex_intensity"
    if "ratio" in text or "margin" in text:
        years = set(r8.years_in_text(text))
        return "multi_year_margin" if len(years) >= 2 and "margin" in text else "ratio_trend"
    if "income" in text and "operating" in text:
        return "income_vs_ops"
    return "other"


def build_model_visible_contract(scorer_contract: dict[str, Any], case_id: str | None = None) -> dict[str, Any]:
    formula_type = scorer_contract.get("formula_type", "other")
    target_slots = scorer_contract.get("target_slots", [])
    required_outputs = [slot["target_slot_name"] for slot in target_slots]
    output_units = {slot["target_slot_name"]: slot.get("unit", "") for slot in target_slots}
    if formula_type == "other":
        output_format_hints = (
            "Compute the requested financial metric(s) from the evidence. "
            "Place the final numeric result(s) in the 'final_answer' field as a string "
            "in the format '<value> <unit>' (e.g., '42.3%', '$1.2 billion', '0.85x'). "
            "If multiple values are required, list them each on a new line with labels."
        )
    elif formula_type == "finqa_program":
        output_format_hints = (
            "Compute the result of the described financial calculation. "
            "Place the single numeric result in 'final_answer' with appropriate unit."
        )
    else:
        output_format_hints = f"Express as {json.dumps(output_units, sort_keys=True)} to 1 decimal place."
    return {
        "case_id": case_id,
        "formula_type": formula_type,
        "required_outputs": required_outputs,
        "output_units": output_units,
        "output_format_hints": output_format_hints,
        "target_formula_template": formula_type if formula_type != "other" else "derive final_answer from the provided source facts",
        "target_years": sorted({year for slot in target_slots for year in slot.get("years", []) if year}),
        "required_steps": ["identify source facts", "compute every required output"],
    }


def fallback_contract(case: dict[str, Any], dataset: str) -> tuple[dict[str, Any], dict[str, Any], str]:
    expected = r8.parse_number(case.get("expected_answer"))
    if expected is None:
        raise RuntimeError("expected answer not numeric")
    unit = r8.infer_unit_from_answer(str(case.get("expected_answer", "")))
    year = max(case.get("years") or [0])
    nums = r8.all_numbers(case.get("evidence_text", ""))[:8]
    source = [
        {"fact_id": f"{case['case_id']}_fact_{idx:02d}", "metric": f"source_value_{idx:02d}", "unit": "amount", "value": num, "year": year}
        for idx, num in enumerate(nums, 1)
    ]
    if not source:
        source = [{"fact_id": f"{case['case_id']}_fact_01", "metric": "source_value_01", "unit": unit, "value": expected, "year": year}]
    formula_type = "finqa_program" if dataset == "FinQA" else infer_formula_type(case.get("question", ""), case.get("evidence_text", ""))
    scorer = {
        "formula_type": formula_type,
        "source_fact_numbers": source,
        "target_slots": [{
            "target_slot_name": "finqa_program_answer" if dataset == "FinQA" else "final_answer",
            "expected_value": expected,
            "unit": unit,
            "tolerance": r8.target_tolerance(expected, unit),
            "required_for_answer": True,
            "acceptable_equivalent_forms": [unit, "number"],
            "derived_or_source": "derived",
            "years": [year] if year else [],
        }],
        "final_target_numbers": ["finqa_program_answer" if dataset == "FinQA" else "final_answer"],
        "intermediate_numbers": [],
        "non_target_numbers": ["case_id", "source_id", "fact_id", "trace_id"],
    }
    return scorer, build_model_visible_contract(scorer, case["case_id"]), "fallback"


def finqa_contract(case: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any], str]:
    expected = r8.parse_number(case["expected_answer"])
    if expected is None:
        raise RuntimeError("FinQA answer not numeric")
    nums = r8.program_numbers(case.get("program", ""))
    if len(nums) < 2:
        raise RuntimeError("FinQA program lacks numeric operands")
    year = max(case.get("years") or [0])
    source = []
    for idx, num in enumerate(nums[:8], 1):
        source.append(
            {
                "fact_id": f"{case['case_id']}_fact_{idx:02d}",
                "metric": f"program_operand_{idx:02d}",
                "unit": "percentage" if "%" in str(case.get("program", "")) else "amount",
                "value": num,
                "year": year,
            }
        )
    unit = r8.infer_unit_from_answer(case["expected_answer"])
    scorer = {
        "formula_type": "finqa_program",
        "source_fact_numbers": source,
        "target_slots": [{
            "target_slot_name": "finqa_program_answer",
            "expected_value": expected,
            "unit": unit,
            "tolerance": r8.target_tolerance(expected, unit),
            "required_for_answer": True,
            "acceptable_equivalent_forms": [unit, "number"],
            "derived_or_source": "derived",
            "years": [year] if year else [],
        }],
        "final_target_numbers": ["finqa_program_answer"],
        "intermediate_numbers": [],
        "non_target_numbers": ["case_id", "source_id", "fact_id", "trace_id"],
    }
    visible = build_model_visible_contract(scorer, case["case_id"])
    visible["target_formula_template"] = case.get("program", "")
    visible["required_steps"] = ["use the program expression", "compute the final answer"]
    return scorer, visible, "deterministic_program"


def finder_prompt(case: dict[str, Any]) -> str:
    formula_lines = "\n".join(f"   - {name}" for name in c.FORMULA_TYPES)
    return f"""You are a financial formula contract extractor.
Given a financial question, evidence text, and expected answer, extract:

1. formula_type: Choose the BEST matching type from this list:
{formula_lines}

Pick the MOST SPECIFIC type possible. Only use "other" if genuinely no other type fits.

2. source_fact_numbers: values explicitly present in EVIDENCE_TEXT only.
3. target_slots: final numeric answer slots. target_slots may use EXPECTED_ANSWER.

Return JSON only with keys: formula_type, source_fact_numbers, target_slots.
Use snake_case metric names.

QUESTION:
{case['question']}

EVIDENCE_TEXT:
{case['evidence_text'][:4500]}

EXPECTED_ANSWER:
{str(case.get('expected_answer', ''))[:1000]}
"""


def finder_contract(case: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any], str, dict[str, Any]]:
    expected = r8.parse_number(case.get("expected_answer"))
    if expected is None:
        raise RuntimeError("FinDER answer not numeric")
    last_raw: dict[str, Any] = {}
    for attempt, temp in enumerate([0.0, 0.1], 1):
        try:
            raw, usage = r8.call_openai_json(finder_prompt(case), temperature=temp)
            last_raw = {"raw": raw, "usage": usage, "attempt": attempt}
            source = []
            for idx, sf in enumerate(raw.get("source_fact_numbers", [])[:12], 1):
                value = r8.parse_number(sf.get("value"))
                if value is None:
                    continue
                source.append(
                    {
                        "fact_id": f"{case['case_id']}_fact_{idx:02d}",
                        "metric": clean_metric(str(sf.get("metric") or f"source_value_{idx:02d}")),
                        "unit": str(sf.get("unit") or "amount"),
                        "value": value,
                        "year": int(sf.get("year") or max(case.get("years") or [0])),
                    }
                )
            slots = []
            for idx, slot in enumerate(raw.get("target_slots", [])[:5], 1):
                value = r8.parse_number(slot.get("expected_value"))
                if value is None:
                    continue
                unit = str(slot.get("unit") or r8.infer_unit_from_answer(str(case.get("expected_answer", ""))))
                slots.append(
                    {
                        "target_slot_name": clean_metric(str(slot.get("target_slot_name") or f"final_answer_{idx}")),
                        "expected_value": value,
                        "unit": unit,
                        "tolerance": float(slot.get("tolerance") or r8.target_tolerance(value, unit)),
                        "required_for_answer": True,
                        "acceptable_equivalent_forms": slot.get("acceptable_equivalent_forms") or [unit, "number"],
                        "derived_or_source": slot.get("derived_or_source") or "derived",
                        "years": slot.get("years") or case.get("years", []),
                    }
                )
            if not slots:
                unit = r8.infer_unit_from_answer(str(case.get("expected_answer", "")))
                slots = [{
                    "target_slot_name": "final_answer",
                    "expected_value": expected,
                    "unit": unit,
                    "tolerance": r8.target_tolerance(expected, unit),
                    "required_for_answer": True,
                    "acceptable_equivalent_forms": [unit, "number"],
                    "derived_or_source": "derived",
                    "years": case.get("years", []),
                }]
            formula_type = clean_metric(str(raw.get("formula_type") or "other"))
            if formula_type not in c.FORMULA_TYPES or formula_type == "other":
                inferred = infer_formula_type(case.get("question", ""), case.get("evidence_text", ""))
                formula_type = inferred if inferred in c.FORMULA_TYPES else "other"
            if source and slots:
                scorer = {
                    "formula_type": formula_type,
                    "source_fact_numbers": source,
                    "target_slots": slots,
                    "final_target_numbers": [slot["target_slot_name"] for slot in slots],
                    "intermediate_numbers": [],
                    "non_target_numbers": ["case_id", "source_id", "fact_id", "trace_id"],
                }
                visible = build_model_visible_contract(scorer, case["case_id"])
                return scorer, visible, "gpt_contract", last_raw
        except Exception as exc:  # noqa: BLE001
            last_raw = {"error": str(exc), "attempt": attempt}
    scorer, visible, mode = fallback_contract(case, "FinDER")
    return scorer, visible, mode, last_raw


def validate_contract(case: dict[str, Any], scorer: dict[str, Any]) -> dict[str, Any]:
    ok = bool(scorer.get("source_fact_numbers")) and bool(scorer.get("target_slots"))
    expected = r8.parse_number(case.get("expected_answer"))
    target_vals = [float(slot["expected_value"]) for slot in scorer.get("target_slots", []) if r8.parse_number(slot.get("expected_value")) is not None]
    close = any(abs(value - expected) <= max(0.5, abs(expected) * 0.02) for value in target_vals) if expected is not None and target_vals else ok
    return {
        "case_id": case["case_id"],
        "source_dataset": case["source_dataset"],
        "ok": ok and close,
        "target_close_to_answer": close,
        "formula_type": scorer.get("formula_type", ""),
        "n_source_facts": len(scorer.get("source_fact_numbers", [])),
        "n_target_slots": len(scorer.get("target_slots", [])),
    }


def main() -> None:
    c.assert_round9b_ready()
    c.CONTRACT_DIR.mkdir(parents=True, exist_ok=True)
    for path in [c.SCORER_CONTRACTS, c.VISIBLE_CONTRACTS, c.GEN_TRACE, c.VALIDATION_REPORT]:
        if path.exists():
            path.unlink()
    cases = c.read_jsonl(c.FINDER_CANDIDATES) + c.read_jsonl(c.FINQA_CANDIDATES)
    rows_scorer = []
    rows_visible = []
    validations = []
    finder_ok = finqa_ok = finder_fail = finqa_fail = 0
    for case in cases:
        try:
            if case["source_dataset"] == "FinQA":
                scorer, visible, mode = finqa_contract(case)
                trace = {"case_id": case["case_id"], "mode": mode}
            else:
                scorer, visible, mode, raw = finder_contract(case)
                trace = {"case_id": case["case_id"], "mode": mode, **raw}
            validation = validate_contract(case, scorer)
        except Exception as exc:  # noqa: BLE001
            scorer, visible, mode = {}, {}, "failed"
            validation = {"case_id": case["case_id"], "source_dataset": case["source_dataset"], "ok": False, "error": str(exc)}
            trace = {"case_id": case["case_id"], "mode": mode, "error": str(exc)}
        c.append_jsonl(c.GEN_TRACE, trace)
        c.append_jsonl(c.VALIDATION_REPORT, validation)
        validations.append(validation)
        if validation.get("ok"):
            if case["source_dataset"] == "FinDER":
                finder_ok += 1
            else:
                finqa_ok += 1
            rows_scorer.append({"case_id": case["case_id"], "source_dataset": case["source_dataset"], "scorer_only_target_slot_contract": scorer, "split": "round9c_test"})
            rows_visible.append({"case_id": case["case_id"], "source_dataset": case["source_dataset"], "model_visible_formula_contract": visible, "split": "round9c_test"})
        else:
            if case["source_dataset"] == "FinDER":
                finder_fail += 1
            else:
                finqa_fail += 1
    c.write_jsonl(c.SCORER_CONTRACTS, rows_scorer)
    c.write_jsonl(c.VISIBLE_CONTRACTS, rows_visible)
    ftypes = Counter(row["scorer_only_target_slot_contract"].get("formula_type", "") for row in rows_scorer if row["source_dataset"] == "FinDER")
    other_pct = round(ftypes.get("other", 0) / max(1, sum(ftypes.values())), 4)
    state = {
        "phase": "C_done",
        "round": c.ROUND,
        "finder_total": 30,
        "finder_contract_ok": finder_ok,
        "finder_validation_failed": finder_fail,
        "finqa_total": 20,
        "finqa_contract_ok": finqa_ok,
        "finqa_validation_failed": finqa_fail,
        "total_eval_ready": len(rows_scorer),
        "formula_type_distribution_finder": dict(ftypes),
        "formula_type_other_pct": other_pct,
        "formula_type_other_warning": other_pct > 0.5,
        "scorer_contracts": c.rel(c.SCORER_CONTRACTS),
        "model_visible_contracts": c.rel(c.VISIBLE_CONTRACTS),
        "completed_at": c.utc_now(),
    }
    c.write_json(c.GEN_STATE, state)
    if finder_ok < 25 or finqa_ok < 17:
        raise RuntimeError(f"Contract thresholds failed: FinDER={finder_ok}/30 FinQA={finqa_ok}/20")
    print(json.dumps(state, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
