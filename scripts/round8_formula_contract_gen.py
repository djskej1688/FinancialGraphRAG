from __future__ import annotations

import json
import re
from typing import Any

import round8_common as c


def clean_metric(value: str) -> str:
    value = re.sub(r"[^a-zA-Z0-9]+", "_", value.lower()).strip("_")
    return value or "value"


def fallback_contract(case: dict[str, Any], dataset: str) -> tuple[dict[str, Any], dict[str, Any], str]:
    expected = c.parse_number(case.get("expected_answer"))
    if expected is None:
        raise RuntimeError("expected answer not numeric")
    unit = c.infer_unit_from_answer(str(case.get("expected_answer", "")))
    year = max(case.get("years") or [0])
    nums = c.all_numbers(case.get("evidence_text", ""))[:6]
    source = [
        {"fact_id": f"{case['case_id']}_fact_{idx:02d}", "metric": f"source_value_{idx:02d}", "unit": "amount", "value": num, "year": year}
        for idx, num in enumerate(nums, 1)
    ]
    if not source:
        source = [{"fact_id": f"{case['case_id']}_fact_01", "metric": "source_value_01", "unit": unit, "value": expected, "year": year}]
    scorer = {
        "formula_type": "finqa_program" if dataset == "FinQA" else "other",
        "source_fact_numbers": source,
        "target_slots": [{
            "target_slot_name": "final_answer",
            "expected_value": expected,
            "unit": unit,
            "tolerance": c.target_tolerance(expected, unit),
            "required_for_answer": True,
            "acceptable_equivalent_forms": [unit, "number"],
            "derived_or_source": "derived",
            "years": [year] if year else [],
        }],
        "final_target_numbers": ["final_answer"],
        "intermediate_numbers": [],
        "non_target_numbers": ["case_id", "source_id", "fact_id", "trace_id"],
    }
    visible = {
        "formula_type": scorer["formula_type"],
        "required_outputs": ["final_answer"],
        "output_units": {"final_answer": unit},
        "output_format_hints": "Return the requested final numeric answer and show the calculation.",
        "target_formula_template": case.get("program") or "derive final_answer from the provided source facts",
        "target_years": [year] if year else [],
        "required_steps": ["identify source facts", "compute final_answer"],
    }
    return scorer, visible, "fallback"


def finqa_contract(case: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any], str]:
    expected = c.parse_number(case["expected_answer"])
    if expected is None:
        raise RuntimeError("FinQA answer not numeric")
    nums = c.program_numbers(case.get("program", ""))
    if len(nums) < 2:
        raise RuntimeError("FinQA program lacks numeric operands")
    year = max(case.get("years") or [0])
    source = []
    for idx, num in enumerate(nums[:8], 1):
        source.append({
            "fact_id": f"{case['case_id']}_fact_{idx:02d}",
            "metric": f"program_operand_{idx:02d}",
            "unit": "percentage" if "%" in str(case.get("program", "")).split(str(num))[0][-2:] else "amount",
            "value": num,
            "year": year,
        })
    unit = c.infer_unit_from_answer(case["expected_answer"])
    scorer = {
        "formula_type": "finqa_program",
        "source_fact_numbers": source,
        "target_slots": [{
            "target_slot_name": "finqa_program_answer",
            "expected_value": expected,
            "unit": unit,
            "tolerance": c.target_tolerance(expected, unit),
            "required_for_answer": True,
            "acceptable_equivalent_forms": [unit, "number"],
            "derived_or_source": "derived",
            "years": [year] if year else [],
        }],
        "final_target_numbers": ["finqa_program_answer"],
        "intermediate_numbers": [],
        "non_target_numbers": ["case_id", "source_id", "fact_id", "trace_id"],
    }
    visible = {
        "formula_type": "finqa_program",
        "required_outputs": ["finqa_program_answer"],
        "output_units": {"finqa_program_answer": unit},
        "output_format_hints": "Return the final program result and show the calculation.",
        "target_formula_template": case.get("program", ""),
        "target_years": [year] if year else [],
        "required_steps": ["use the program expression", "compute the final answer"],
    }
    return scorer, visible, "deterministic_program"


def finder_prompt(case: dict[str, Any]) -> str:
    return f"""You are a financial formula contract extractor.
Given a financial question, evidence text, and expected answer, extract a scoring contract.

Rules:
- source_fact_numbers must contain ONLY values explicitly present in EVIDENCE_TEXT.
- target_slots may use the EXPECTED_ANSWER final numeric value.
- Use snake_case metric names.
- Return JSON only with keys: formula_type, source_fact_numbers, target_slots.

QUESTION:
{case['question']}

EVIDENCE_TEXT:
{case['evidence_text'][:3500]}

EXPECTED_ANSWER:
{str(case.get('expected_answer', ''))[:1000]}
"""


def finder_contract(case: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any], str, dict[str, Any]]:
    expected = c.parse_number(case.get("expected_answer"))
    if expected is None:
        raise RuntimeError("FinDER answer not numeric")
    last_raw: dict[str, Any] = {}
    for attempt, temp in enumerate([0.0, 0.1]):
        try:
            raw, usage = c.call_openai_json(finder_prompt(case), temperature=temp)
            last_raw = {"raw": raw, "usage": usage, "attempt": attempt + 1}
            source = []
            for idx, sf in enumerate(raw.get("source_fact_numbers", [])[:10], 1):
                value = c.parse_number(sf.get("value"))
                if value is None:
                    continue
                source.append({
                    "fact_id": f"{case['case_id']}_fact_{idx:02d}",
                    "metric": clean_metric(str(sf.get("metric") or f"source_value_{idx:02d}")),
                    "unit": str(sf.get("unit") or "amount"),
                    "value": value,
                    "year": int(sf.get("year") or max(case.get("years") or [0])),
                })
            slots = []
            for idx, slot in enumerate(raw.get("target_slots", [])[:5], 1):
                val = c.parse_number(slot.get("expected_value"))
                if val is None:
                    continue
                unit = str(slot.get("unit") or c.infer_unit_from_answer(str(case.get("expected_answer", ""))))
                slots.append({
                    "target_slot_name": clean_metric(str(slot.get("target_slot_name") or f"final_answer_{idx}")),
                    "expected_value": val,
                    "unit": unit,
                    "tolerance": float(slot.get("tolerance") or c.target_tolerance(val, unit)),
                    "required_for_answer": True,
                    "acceptable_equivalent_forms": slot.get("acceptable_equivalent_forms") or [unit, "number"],
                    "derived_or_source": slot.get("derived_or_source") or "derived",
                    "years": slot.get("years") or case.get("years", []),
                })
            if not slots:
                unit = c.infer_unit_from_answer(str(case.get("expected_answer", "")))
                slots = [{
                    "target_slot_name": "final_answer",
                    "expected_value": expected,
                    "unit": unit,
                    "tolerance": c.target_tolerance(expected, unit),
                    "required_for_answer": True,
                    "acceptable_equivalent_forms": [unit, "number"],
                    "derived_or_source": "derived",
                    "years": case.get("years", []),
                }]
            if source and slots:
                scorer = {
                    "formula_type": clean_metric(str(raw.get("formula_type") or "other")),
                    "source_fact_numbers": source,
                    "target_slots": slots,
                    "final_target_numbers": [s["target_slot_name"] for s in slots],
                    "intermediate_numbers": [],
                    "non_target_numbers": ["case_id", "source_id", "fact_id", "trace_id"],
                }
                visible = {
                    "formula_type": scorer["formula_type"],
                    "required_outputs": [s["target_slot_name"] for s in slots],
                    "output_units": {s["target_slot_name"]: s["unit"] for s in slots},
                    "output_format_hints": "Return every required output explicitly and show the calculation.",
                    "target_formula_template": scorer["formula_type"],
                    "target_years": sorted({y for s in slots for y in s.get("years", []) if y}),
                    "required_steps": ["identify source facts", "compute every required output"],
                }
                return scorer, visible, "gpt_contract", last_raw
        except Exception as exc:  # noqa: BLE001
            last_raw = {"error": str(exc), "attempt": attempt + 1}
    scorer, visible, mode = fallback_contract(case, "FinDER")
    return scorer, visible, mode, last_raw


def validate_contract(case: dict[str, Any], scorer: dict[str, Any]) -> dict[str, Any]:
    ok = bool(scorer.get("source_fact_numbers")) and bool(scorer.get("target_slots"))
    expected = c.parse_number(case.get("expected_answer"))
    target_vals = [float(slot.get("expected_value")) for slot in scorer.get("target_slots", []) if c.parse_number(slot.get("expected_value")) is not None]
    if expected is not None and target_vals:
        close = any(abs(v - expected) <= max(0.1, abs(expected) * 0.02) for v in target_vals)
    else:
        close = ok
    return {"case_id": case["case_id"], "source_dataset": case["source_dataset"], "ok": ok and close, "target_close_to_answer": close, "n_source_facts": len(scorer.get("source_fact_numbers", [])), "n_target_slots": len(scorer.get("target_slots", []))}


def main() -> None:
    c.CONTRACT_DIR.mkdir(parents=True, exist_ok=True)
    for path in [c.SCORER_CONTRACTS, c.VISIBLE_CONTRACTS, c.GEN_TRACE, c.VALIDATION_REPORT]:
        if path.exists():
            path.unlink()
    cases = c.read_jsonl(c.FINDER_CANDIDATES) + c.read_jsonl(c.FINQA_CANDIDATES)
    rows_scorer = []
    rows_visible = []
    validations = []
    finder_ok = finqa_ok = 0
    finder_fail = finqa_fail = 0
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
            rows_scorer.append({"case_id": case["case_id"], "source_dataset": case["source_dataset"], "scorer_only_target_slot_contract": scorer, "split": "round8_test"})
            rows_visible.append({"case_id": case["case_id"], "source_dataset": case["source_dataset"], "model_visible_formula_contract": visible, "split": "round8_test"})
        else:
            if case["source_dataset"] == "FinDER":
                finder_fail += 1
            else:
                finqa_fail += 1
    c.write_jsonl(c.SCORER_CONTRACTS, rows_scorer)
    c.write_jsonl(c.VISIBLE_CONTRACTS, rows_visible)
    state = {
        "phase": "C_done",
        "finder_total": 30,
        "finder_contract_ok": finder_ok,
        "finder_validation_failed": finder_fail,
        "finqa_total": 20,
        "finqa_contract_ok": finqa_ok,
        "finqa_validation_failed": finqa_fail,
        "total_eval_ready": len(rows_scorer),
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
