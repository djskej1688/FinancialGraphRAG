from __future__ import annotations

import json
import re
from collections import Counter
from typing import Any

import round10_common as c
import round8_common as r8
import round9c_formula_contract_gen as r9c_gen


UNIT_MAP = {
    "percent": "percentage",
    "million": "USD_millions",
    "billion": "USD_billions",
    "thousand": "USD_thousands",
    "": "amount",
}


def build_tatqa_contract(case: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any], str]:
    derivation = str(case.get("derivation", "")).strip()
    expected = r8.parse_number(case.get("expected_answer_numeric"))
    if expected is None:
        expected = r8.parse_number(case.get("expected_answer"))
    if not derivation or expected is None:
        raise RuntimeError("TAT-QA case lacks derivation or expected numeric answer")
    unit = UNIT_MAP.get(str(case.get("scale") or ""), "amount")
    numbers = [float(x.replace(",", "")) for x in re.findall(r"-?\d[\d,]*(?:\.\d+)?", derivation)]
    if len(numbers) < 2:
        raise RuntimeError("TAT-QA derivation lacks numeric operands")
    year = max(case.get("years") or [0])
    source_facts = [
        {
            "fact_id": f"{case['case_id']}_fact_{idx:02d}",
            "metric": f"operand_{idx:02d}",
            "value": value,
            "unit": unit,
            "year": year,
        }
        for idx, value in enumerate(numbers[:12], 1)
    ]
    scorer = {
        "formula_type": "tatqa_arithmetic",
        "source_fact_numbers": source_facts,
        "target_slots": [{
            "target_slot_name": "final_result",
            "expected_value": expected,
            "unit": unit,
            "tolerance": max(0.5, abs(expected) * 0.02),
            "required_for_answer": True,
            "acceptable_equivalent_forms": [unit, "number"],
            "derived_or_source": "derived",
            "years": [year] if year else [],
        }],
        "final_target_numbers": ["final_result"],
        "intermediate_numbers": [],
        "non_target_numbers": ["case_id", "source_id", "fact_id", "trace_id"],
    }
    visible = {
        "case_id": case["case_id"],
        "formula_type": "tatqa_arithmetic",
        "required_outputs": ["final_result"],
        "output_units": {"final_result": unit},
        "output_format_hints": "Compute the arithmetic derivation and place the single numeric result in final_answer with the appropriate unit.",
        "target_formula_template": derivation,
        "target_years": [year] if year else [],
        "required_steps": ["use the arithmetic derivation", "compute final_result"],
    }
    return scorer, visible, "tatqa_derivation"


def validate_contract(case: dict[str, Any], scorer: dict[str, Any]) -> dict[str, Any]:
    expected = r8.parse_number(case.get("expected_answer_numeric") or case.get("expected_answer"))
    target_vals = [r8.parse_number(slot.get("expected_value")) for slot in scorer.get("target_slots", [])]
    target_vals = [float(x) for x in target_vals if x is not None]
    ok = bool(scorer.get("source_fact_numbers")) and bool(target_vals)
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
    c.assert_round9c_done()
    c.CONTRACT_DIR.mkdir(parents=True, exist_ok=True)
    for path in [c.SCORER_CONTRACTS, c.VISIBLE_CONTRACTS, c.GEN_TRACE, c.VALIDATION_REPORT]:
        if path.exists():
            path.unlink()
    cases = c.load_all_round10_cases()
    rows_scorer = []
    rows_visible = []
    validations = []
    counts = Counter()
    for case in cases:
        try:
            if case["source_dataset"] == "FinQA":
                scorer, visible, mode = r9c_gen.finqa_contract(case)
            elif case["source_dataset"] == "TAT-QA":
                scorer, visible, mode = build_tatqa_contract(case)
            else:
                scorer, visible, mode, raw = r9c_gen.finder_contract(case)
                c.append_jsonl(c.GEN_TRACE, {"case_id": case["case_id"], "mode": mode, **raw})
            if case["source_dataset"] != "FinDER":
                c.append_jsonl(c.GEN_TRACE, {"case_id": case["case_id"], "mode": mode})
            validation = validate_contract(case, scorer)
        except Exception as exc:  # noqa: BLE001
            scorer, visible = {}, {}
            validation = {"case_id": case["case_id"], "source_dataset": case["source_dataset"], "ok": False, "error": str(exc)}
            c.append_jsonl(c.GEN_TRACE, {"case_id": case["case_id"], "mode": "failed", "error": str(exc)})
        validations.append(validation)
        c.append_jsonl(c.VALIDATION_REPORT, validation)
        if validation.get("ok"):
            counts[(case["source_dataset"], "ok")] += 1
            rows_scorer.append({"case_id": case["case_id"], "source_dataset": case["source_dataset"], "scorer_only_target_slot_contract": scorer, "split": "round10_test"})
            rows_visible.append({"case_id": case["case_id"], "source_dataset": case["source_dataset"], "model_visible_formula_contract": visible, "split": "round10_test"})
        else:
            counts[(case["source_dataset"], "fail")] += 1
    c.write_jsonl(c.SCORER_CONTRACTS, rows_scorer)
    c.write_jsonl(c.VISIBLE_CONTRACTS, rows_visible)
    ftypes_all = Counter(row["scorer_only_target_slot_contract"].get("formula_type", "") for row in rows_scorer)
    ftypes_finder = Counter(row["scorer_only_target_slot_contract"].get("formula_type", "") for row in rows_scorer if row["source_dataset"] == "FinDER")
    finder_total = sum(ftypes_finder.values())
    other_pct = round(ftypes_finder.get("other", 0) / finder_total, 4) if finder_total else 0.0
    state = {
        "phase": "D_done",
        "round": c.ROUND,
        "cases_input": len(cases),
        "total_eval_ready": len(rows_scorer),
        "finder_contract_ok": counts[("FinDER", "ok")],
        "finqa_contract_ok": counts[("FinQA", "ok")],
        "tatqa_contract_ok": counts[("TAT-QA", "ok")],
        "finder_validation_failed": counts[("FinDER", "fail")],
        "finqa_validation_failed": counts[("FinQA", "fail")],
        "tatqa_validation_failed": counts[("TAT-QA", "fail")],
        "formula_type_distribution": dict(ftypes_all),
        "formula_type_distribution_finder": dict(ftypes_finder),
        "formula_type_other_pct_finder": other_pct,
        "formula_type_other_warning": other_pct > 0.1,
        "scorer_contracts": c.rel(c.SCORER_CONTRACTS),
        "model_visible_contracts": c.rel(c.VISIBLE_CONTRACTS),
        "completed_at": c.utc_now(),
    }
    c.write_json(c.GEN_STATE, state)
    if len(rows_scorer) < 200:
        raise RuntimeError(f"Round10 contract-ready cases below 200: {len(rows_scorer)}")
    print(json.dumps(state, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
