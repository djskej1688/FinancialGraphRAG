"""Build v3.2 formula contracts for the Track B test split.

Implements codex_prompt_test_formula_contracts_A.md and
codex_prompt_test_formula_contracts_B.md.
"""

from __future__ import annotations

import argparse
import json
import math
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
TRACK_B = ROOT / "outputs" / "round3_dual_track_eval_prep" / "track_b_shadow_overlay"
FACTS_PATH = TRACK_B / "shadow_overlay_required_facts.jsonl"
TEST_CASES_PATH = TRACK_B / "shadow_overlay_test_cases.json"
OUT = ROOT / "outputs" / "round3_eval_harness" / "formula_contract_v3_2_test_split"

SCORER_PATH = OUT / "test_scorer_contracts.jsonl"
VISIBLE_PATH = OUT / "test_model_visible_contracts.jsonl"
SUPP_A_PATH = OUT / "supplemental_facts_A.jsonl"
REEXTRACTED_B_PATH = OUT / "reextracted_facts_B.jsonl"
STATE_A_PATH = OUT / "state_A.json"
STATE_B_PATH = OUT / "state_B.json"

MODEL = "gpt-4o-mini"
NON_TARGET_NUMBERS = [
    "case_id",
    "fact_id",
    "trace_id",
    "source_id",
    "prompt_hash",
    "metric IDs",
    "evidence IDs",
]
DO_NOT_USE = [
    "source fact ids",
    "case ids",
    "trace ids",
    "citation ids",
    "raw source-only numbers not requested as final targets",
]

EXTRACTION_SYSTEM = """You are a financial data extraction assistant.
Extract specific financial metrics from the provided financial statement text.
Return a JSON object with exactly the fields requested. Use null if a value is not found.
Values should be raw numbers (not strings). Use the exact units shown in the table header."""


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n")


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")


def upsert_rows(path: Path, rows: list[dict[str, Any]]) -> None:
    existing = {row["case_id"]: row for row in read_jsonl(path)}
    for row in rows:
        existing[row["case_id"]] = row
    order = [
        "round3_test_011_e428c7bc",
        "round3_test_004_b035aeed",
        "round3_test_009_3a2f3700",
        "round3_test_007_4ac62908",
        "round3_test_017_68bdbbb8",
        "round3_test_016_707dc83f",
        "round3_test_018_0748ea37",
        "round3_test_014_42c9db2b",
        "round3_test_013_bc2fb598",
        "round3_test_012_f9d03e27",
    ]
    write_jsonl(path, [existing[cid] for cid in order if cid in existing])


def source_fact(fact_id: str, metric: str, unit: str, value: float, year: int) -> dict[str, Any]:
    return {"fact_id": fact_id, "metric": metric, "unit": unit, "value": float(value), "year": int(year)}


def target_slot(
    name: str,
    value: float,
    tolerance: float,
    unit: str,
    years: list[int],
    forms: list[str],
    derived_or_source: str = "derived",
    required_for_answer: bool = True,
) -> dict[str, Any]:
    return {
        "acceptable_equivalent_forms": forms,
        "derived_or_source": derived_or_source,
        "expected_value": float(value),
        "required_for_answer": required_for_answer,
        "target_slot_name": name,
        "tolerance": float(tolerance),
        "unit": unit,
        "years": years,
    }


def scorer_row(case_id: str, formula_type: str, source_facts: list[dict[str, Any]], slots: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "case_id": case_id,
        "split": "round3_test",
        "scorer_only_target_slot_contract": {
            "formula_type": formula_type,
            "final_target_numbers": [slot["target_slot_name"] for slot in slots if slot.get("required_for_answer", True)],
            "intermediate_numbers": [],
            "non_target_numbers": NON_TARGET_NUMBERS,
            "source_fact_numbers": source_facts,
            "target_slots": slots,
        },
    }


def visible_row(
    case_id: str,
    formula_type: str,
    template: str,
    numerator: str,
    denominator: str,
    years: list[int],
    expected_type: str,
    comparison: str,
    steps: list[str],
) -> dict[str, Any]:
    return {
        "case_id": case_id,
        "split": "round3_test",
        "leakage_guard": {
            "contains_expected_answer_text": False,
            "contains_expected_numeric_final_answers": False,
            "contains_scorer_target_values": False,
        },
        "model_visible_formula_contract": {
            "comparison_periods": [str(year) for year in years],
            "denominator_metric_role": denominator,
            "do_not_use_as_targets": DO_NOT_USE,
            "expected_output_type": expected_type,
            "formula_type": formula_type,
            "numerator_metric_role": numerator,
            "required_comparison": comparison,
            "required_steps": steps,
            "rounding_instruction": "use v3.2 rounding rules",
            "target_formula_template": template,
            "target_years": years,
        },
    }


def state_template(task: str, cases: list[str], openai: bool = False) -> dict[str, Any]:
    state = {
        "phase": "running",
        "task": task,
        "cases": cases,
        "cases_total": len(cases),
        "cases_completed": 0,
        "cases_failed": [],
        "started_at": utc_now(),
        "completed_at": None,
        "output_dir": "outputs/round3_eval_harness/formula_contract_v3_2_test_split/",
    }
    if openai:
        state["openai_calls_made"] = 0
    return state


def complete_state(path: Path, state: dict[str, Any]) -> None:
    state["phase"] = "done" if not state["cases_failed"] else "partial"
    state["completed_at"] = utc_now()
    write_json(path, state)


def load_required_by_case() -> dict[str, dict[str, dict[str, Any]]]:
    out: dict[str, dict[str, dict[str, Any]]] = {}
    for fact in read_jsonl(FACTS_PATH):
        out.setdefault(fact["case_id"], {})[fact["fact_id"]] = fact
    return out


def fact_from_required(required: dict[str, dict[str, Any]], fact_id: str, metric_override: str | None = None, unit_override: str | None = None) -> dict[str, Any]:
    fact = required[fact_id]
    return source_fact(
        fact_id,
        metric_override or fact["metric_canonical"],
        unit_override or fact["unit"],
        fact["value"],
        fact["year"],
    )


def build_group_a(required_by_case: dict[str, dict[str, dict[str, Any]]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    scorer: list[dict[str, Any]] = []
    visible: list[dict[str, Any]] = []
    supplemental: list[dict[str, Any]] = []

    cid = "round3_test_011_e428c7bc"
    req = required_by_case[cid]
    facts = [fact_from_required(req, f"{cid}_fact_{i:02d}") for i in range(1, 7)]
    slots = [
        target_slot("operating_margin_2023", 27.575, 0.1, "percentage", [2023], ["percent", "ratio_decimal"]),
        target_slot("operating_margin_2022", 28.754, 0.1, "percentage", [2022], ["percent", "ratio_decimal"]),
        target_slot("operating_margin_2021", 23.348, 0.1, "percentage", [2021], ["percent", "ratio_decimal"]),
    ]
    scorer.append(scorer_row(cid, "operating_margin", facts, slots))
    visible.append(visible_row(cid, "operating_margin", "operating_income / revenue * 100", "operating_income", "revenue", [2021, 2022, 2023], "percentage", "compare operating margin across 2021-2023", ["identify operating income and revenue for each year", "compute operating_income / revenue * 100", "identify trend: 2021 to 2022 to 2023"]))

    cid = "round3_test_004_b035aeed"
    req = required_by_case[cid]
    facts = [fact_from_required(req, f"{cid}_fact_01"), fact_from_required(req, f"{cid}_fact_02")]
    slots = [target_slot("female_mgmt_to_employee_ratio_2023", 1.13043, 0.01, "ratio", [2023], ["ratio", "times"])]
    scorer.append(scorer_row(cid, "workforce_ratio", facts, slots))
    visible.append(visible_row(cid, "workforce_ratio", "female_management_percent / female_employee_percent", "female_management_percent", "female_employee_percent", [2023], "ratio", "compare female representation in management versus overall employees", ["identify female management percent", "identify female employee percent", "compute management percent / employee percent"]))

    cid = "round3_test_009_3a2f3700"
    req = required_by_case[cid]
    facts = [fact_from_required(req, f"{cid}_fact_{i:02d}") for i in range(1, 9)]
    slots = [
        target_slot("gross_margin_2023", 70.019, 0.1, "percentage", [2023], ["percent", "ratio_decimal"]),
        target_slot("gross_margin_2022", 75.659, 0.1, "percentage", [2022], ["percent", "ratio_decimal"]),
    ]
    scorer.append(scorer_row(cid, "gross_margin", facts, slots))
    visible.append(visible_row(cid, "gross_margin", "(product_sales + other_revenues - cost_of_sales) / total_revenue * 100", "product_sales plus other_revenues minus cost_of_sales", "total_revenue", [2022, 2023], "percentage", "compare gross margin for 2023 versus 2022", ["identify product sales, other revenues, cost of sales, and total revenue", "compute gross margin for each year", "compare 2023 against 2022"]))

    cid = "round3_test_007_4ac62908"
    req = required_by_case[cid]
    low_supp = source_fact(f"{cid}_fact_S1", "diluted_earnings_per_common_share", "USD_per_share", 10.17, 2022)
    supplemental.append({**low_supp, "case_id": cid, "ticker": "LOW", "metric_canonical": low_supp["metric"], "source": "evidence_text_supplemental"})
    facts = [
        fact_from_required(req, f"{cid}_fact_04", unit_override="USD_per_share"),
        low_supp,
        fact_from_required(req, f"{cid}_fact_01"),
        fact_from_required(req, f"{cid}_fact_05"),
        fact_from_required(req, f"{cid}_fact_06"),
    ]
    slots = [
        target_slot("diluted_eps_2023", 13.20, 0.05, "USD_per_share", [2023], ["dollar_amount", "per_share"], "source"),
        target_slot("diluted_eps_yoy_pct_change", 29.793, 0.2, "percentage", [2022, 2023], ["percent"], "derived"),
    ]
    scorer.append(scorer_row(cid, "diluted_eps_and_yoy_change", facts, slots))
    visible.append(visible_row(cid, "diluted_eps_and_yoy_change", "EPS direct; (eps_current - eps_prior) / eps_prior * 100", "diluted_eps_current minus diluted_eps_prior", "diluted_eps_prior", [2022, 2023], "mixed_per_share_and_percentage", "report current diluted EPS and year-over-year percent change", ["identify diluted EPS for current and prior year", "report current diluted EPS", "compute YoY percent change"]))

    cid = "round3_test_017_68bdbbb8"
    req = required_by_case[cid]
    mpc_supp = [
        source_fact(f"{cid}_fact_S1", "total_revenues_and_other_income", "USD_millions", 150307.0, 2023),
        source_fact(f"{cid}_fact_S2", "total_revenues_and_other_income", "USD_millions", 179952.0, 2022),
        source_fact(f"{cid}_fact_S3", "total_revenues_and_other_income", "USD_millions", 120930.0, 2021),
    ]
    for fact in mpc_supp:
        supplemental.append({**fact, "case_id": cid, "ticker": "MPC", "metric_canonical": fact["metric"], "source": "evidence_text_supplemental"})
    facts = [fact_from_required(req, f"{cid}_fact_{i:02d}") for i in range(1, 4)] + mpc_supp
    slots = [
        target_slot("cont_ops_margin_2023", 7.433, 0.1, "percentage", [2023], ["percent", "ratio_decimal"]),
        target_slot("cont_ops_margin_2022", 8.879, 0.1, "percentage", [2022], ["percent", "ratio_decimal"]),
        target_slot("cont_ops_margin_2021", 2.111, 0.1, "percentage", [2021], ["percent", "ratio_decimal"]),
    ]
    scorer.append(scorer_row(cid, "continuing_ops_margin", facts, slots))
    visible.append(visible_row(cid, "continuing_ops_margin", "income_from_continuing_operations_net_of_tax / total_revenues_and_other_income * 100", "income_from_continuing_operations_net_of_tax", "total_revenues_and_other_income", [2021, 2022, 2023], "percentage", "compare continuing operations margin consistency across 2021-2023", ["identify continuing operations income and total revenues", "compute margin for each year", "assess consistency and recurring profitability risk"]))

    return scorer, visible, supplemental


GROUP_B_FIELDS: dict[str, list[dict[str, Any]]] = {
    "round3_test_016_707dc83f": [
        {"name": "sales_2024", "description": "Sales", "year": 2024},
        {"name": "sales_2023", "description": "Sales", "year": 2023},
        {"name": "sales_2022", "description": "Sales", "year": 2022},
        {"name": "cost_of_sales_2024", "description": "Cost of sales", "year": 2024},
        {"name": "cost_of_sales_2023", "description": "Cost of sales", "year": 2023},
        {"name": "cost_of_sales_2022", "description": "Cost of sales", "year": 2022},
    ],
    "round3_test_018_0748ea37": [
        {"name": "total_revenue_2023", "description": "Total revenue", "year": 2023},
        {"name": "total_revenue_2022", "description": "Total revenue", "year": 2022},
    ],
    "round3_test_014_42c9db2b": [
        {"name": "revenue_2024", "description": "Revenue", "year": 2024},
        {"name": "revenue_2023", "description": "Revenue", "year": 2023},
        {"name": "revenue_2022", "description": "Revenue", "year": 2022},
        {"name": "net_income_2023", "description": "Net income (loss)", "year": 2023},
        {"name": "net_income_2022", "description": "Net income", "year": 2022},
        {"name": "operating_income_2023", "description": "Operating income (loss)", "year": 2023},
    ],
    "round3_test_013_bc2fb598": [
        {"name": "revenues_2023", "description": "Revenues from continuing operations", "year": 2023},
        {"name": "operating_income_2023", "description": "Operating income", "year": 2023},
        {"name": "net_income_attributable_2023", "description": "Net income attributable to Verisk", "year": 2023},
        {"name": "loss_from_discontinued_ops_2023", "description": "Loss from discontinued operations, net of tax", "year": 2023},
    ],
    "round3_test_012_f9d03e27": [
        {"name": "net_sales_tpo_2023", "description": "TPO Net sales and revenues", "year": 2023},
        {"name": "net_sales_tpo_2022", "description": "TPO Net sales and revenues", "year": 2022},
        {"name": "net_sales_tpo_2021", "description": "TPO Net sales and revenues", "year": 2021},
        {"name": "cost_of_sales_tpo_2023", "description": "TPO Cost of sales and revenues", "year": 2023},
        {"name": "cost_of_sales_tpo_2022", "description": "TPO Cost of sales and revenues", "year": 2022},
        {"name": "cost_of_sales_tpo_2021", "description": "TPO Cost of sales and revenues", "year": 2021},
    ],
}

EXPECTED_B = {
    "round3_test_016_707dc83f": {"sales_2024": 12100.6, "sales_2023": 12600.0, "sales_2022": 12698.6, "cost_of_sales_2024": 8168.7, "cost_of_sales_2023": 8833.0, "cost_of_sales_2022": 9338.5},
    "round3_test_018_0748ea37": {"total_revenue_2023": 3273569.0, "total_revenue_2022": 3108581.0},
    "round3_test_014_42c9db2b": {"revenue_2024": 25111.0, "revenue_2023": 15540.0, "revenue_2022": 30758.0, "net_income_2023": -5833.0, "net_income_2022": 8687.0, "operating_income_2023": -5745.0},
    "round3_test_013_bc2fb598": {"revenues_2023": 2681.4, "operating_income_2023": 1131.7, "net_income_attributable_2023": 614.6, "loss_from_discontinued_ops_2023": -154.0},
    "round3_test_012_f9d03e27": {"net_sales_tpo_2023": 33315.5, "net_sales_tpo_2022": 27314.3, "net_sales_tpo_2021": 21834.5, "cost_of_sales_tpo_2023": 26894.2, "cost_of_sales_tpo_2022": 23291.0, "cost_of_sales_tpo_2021": 19092.4},
}


def extract_facts(evidence_text: str, fields_to_extract: list[dict[str, Any]]) -> dict[str, Any]:
    key = os.environ.get("OPENAI_API_KEY")
    if not key:
        raise RuntimeError("OPENAI_API_KEY missing from process environment")
    from openai import OpenAI

    client = OpenAI(api_key=key)
    fields_json = json.dumps(fields_to_extract, ensure_ascii=False, indent=2)
    prompt = f"""Extract the following financial metrics from this text:

{fields_json}

Financial statement:
{evidence_text}

Return JSON with each field name as key and the numeric value as value."""
    response = client.chat.completions.create(
        model=MODEL,
        temperature=0,
        response_format={"type": "json_object"},
        messages=[
            {"role": "system", "content": EXTRACTION_SYSTEM},
            {"role": "user", "content": prompt},
        ],
    )
    return json.loads(response.choices[0].message.content or "{}")


def close_value(a: Any, b: float) -> bool:
    try:
        return math.isclose(float(a), float(b), rel_tol=0.0, abs_tol=0.001)
    except (TypeError, ValueError):
        return False


def extracted_fact(case_id: str, ticker: str, fact_no: int, metric: str, raw: str, year: int, value: float, unit: str, extracted: dict[str, Any], field_name: str) -> dict[str, Any]:
    got = extracted.get(field_name)
    fallback = not close_value(got, value)
    return {
        "fact_id": f"{case_id}_fact_B{fact_no:02d}",
        "case_id": case_id,
        "ticker": ticker,
        "metric_canonical": metric,
        "metric_raw": raw,
        "year": year,
        "value": float(value if fallback else got),
        "unit": unit,
        "source": "evidence_text_extraction",
        "extraction_model": MODEL,
        "verified_against_expected": not fallback,
        "fallback_used": fallback,
    }


def sf_from_reextracted(fact: dict[str, Any]) -> dict[str, Any]:
    return source_fact(fact["fact_id"], fact["metric_canonical"], fact["unit"], fact["value"], fact["year"])


def build_group_b(required_by_case: dict[str, dict[str, dict[str, Any]]], cases_by_id: dict[str, dict[str, Any]], state: dict[str, Any]) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    scorer: list[dict[str, Any]] = []
    visible: list[dict[str, Any]] = []
    reextracted: list[dict[str, Any]] = []

    def extract_for(cid: str) -> dict[str, Any]:
        result = extract_facts(cases_by_id[cid]["evidence_text"], GROUP_B_FIELDS[cid])
        state["openai_calls_made"] += 1
        return result

    cid = "round3_test_016_707dc83f"
    ext = extract_for(cid)
    facts_b = [
        extracted_fact(cid, "APD", 1, "sales", "Sales", 2024, 12100.6, "USD_millions", ext, "sales_2024"),
        extracted_fact(cid, "APD", 2, "sales", "Sales", 2023, 12600.0, "USD_millions", ext, "sales_2023"),
        extracted_fact(cid, "APD", 3, "sales", "Sales", 2022, 12698.6, "USD_millions", ext, "sales_2022"),
        extracted_fact(cid, "APD", 4, "cost_of_sales", "Cost of sales", 2024, 8168.7, "USD_millions", ext, "cost_of_sales_2024"),
        extracted_fact(cid, "APD", 5, "cost_of_sales", "Cost of sales", 2023, 8833.0, "USD_millions", ext, "cost_of_sales_2023"),
        extracted_fact(cid, "APD", 6, "cost_of_sales", "Cost of sales", 2022, 9338.5, "USD_millions", ext, "cost_of_sales_2022"),
    ]
    reextracted.extend(facts_b)
    slots = [
        target_slot("gross_margin_2024", 32.493, 0.1, "percentage", [2024], ["percent", "ratio_decimal"]),
        target_slot("gross_margin_2023", 29.897, 0.1, "percentage", [2023], ["percent", "ratio_decimal"]),
        target_slot("gross_margin_2022", 26.461, 0.1, "percentage", [2022], ["percent", "ratio_decimal"]),
    ]
    scorer.append(scorer_row(cid, "gross_margin", [sf_from_reextracted(f) for f in facts_b], slots))
    visible.append(visible_row(cid, "gross_margin", "(Sales - Cost_of_sales) / Sales * 100", "Sales minus Cost_of_sales", "Sales", [2022, 2023, 2024], "percentage", "compare gross profit margin trend across 2022-2024", ["identify sales and cost of sales for each year", "compute gross margin for each year", "compare 2024 against historical years"]))
    state["cases_completed"] += 1
    write_json(STATE_B_PATH, state)

    cid = "round3_test_018_0748ea37"
    ext = extract_for(cid)
    facts_b = [
        extracted_fact(cid, "BXP", 1, "total_revenue", "Total revenue", 2023, 3273569.0, "USD_thousands", ext, "total_revenue_2023"),
        extracted_fact(cid, "BXP", 2, "total_revenue", "Total revenue", 2022, 3108581.0, "USD_thousands", ext, "total_revenue_2022"),
    ]
    reextracted.extend(facts_b)
    req = required_by_case[cid]
    facts = [fact_from_required(req, f"{cid}_fact_03"), fact_from_required(req, f"{cid}_fact_04"), *[sf_from_reextracted(f) for f in facts_b], fact_from_required(req, f"{cid}_fact_05"), fact_from_required(req, f"{cid}_fact_06"), fact_from_required(req, f"{cid}_fact_08")]
    slots = [
        target_slot("operating_margin_2023", 31.597, 0.1, "percentage", [2023], ["percent", "ratio_decimal"]),
        target_slot("operating_margin_2022", 34.050, 0.1, "percentage", [2022], ["percent", "ratio_decimal"]),
    ]
    scorer.append(scorer_row(cid, "operating_margin", facts, slots))
    visible.append(visible_row(cid, "operating_margin", "(total_revenue - total_expenses) / total_revenue * 100", "total_revenue minus total_expenses", "total_revenue", [2022, 2023], "percentage", "compare operating margin for 2023 versus 2022", ["identify total revenue and total expenses", "compute operating margin for each year", "compare margin change"]))
    state["cases_completed"] += 1
    write_json(STATE_B_PATH, state)

    cid = "round3_test_014_42c9db2b"
    ext = extract_for(cid)
    facts_b = [
        extracted_fact(cid, "MU", 1, "revenue", "Revenue", 2024, 25111.0, "USD_millions", ext, "revenue_2024"),
        extracted_fact(cid, "MU", 2, "revenue", "Revenue", 2023, 15540.0, "USD_millions", ext, "revenue_2023"),
        extracted_fact(cid, "MU", 3, "revenue", "Revenue", 2022, 30758.0, "USD_millions", ext, "revenue_2022"),
        extracted_fact(cid, "MU", 4, "net_income", "Net income (loss)", 2023, -5833.0, "USD_millions", ext, "net_income_2023"),
        extracted_fact(cid, "MU", 5, "net_income", "Net income", 2022, 8687.0, "USD_millions", ext, "net_income_2022"),
        extracted_fact(cid, "MU", 6, "operating_income", "Operating income (loss)", 2023, -5745.0, "USD_millions", ext, "operating_income_2023"),
    ]
    reextracted.extend(facts_b)
    req = required_by_case[cid]
    facts = [fact_from_required(req, f"{cid}_fact_06"), fact_from_required(req, f"{cid}_fact_07"), fact_from_required(req, f"{cid}_fact_08"), *[sf_from_reextracted(f) for f in facts_b]]
    slots = [
        target_slot("net_margin_2024", 3.097, 0.1, "percentage", [2024], ["percent", "ratio_decimal"]),
        target_slot("non_op_impact_2024", -526.0, 5.0, "USD_millions", [2024], ["USD_millions", "amount"]),
        target_slot("net_margin_2022", 28.240, 0.2, "percentage", [2022], ["percent", "ratio_decimal"]),
        target_slot("net_margin_2023", -37.534, 0.2, "percentage", [2023], ["percent", "ratio_decimal"]),
    ]
    scorer.append(scorer_row(cid, "net_margin_and_nonop_impact", facts, slots))
    visible.append(visible_row(cid, "net_margin_and_nonop_impact", "net_income / revenue * 100; net_income - operating_income", "net_income", "revenue", [2022, 2023, 2024], "mixed_percentage_and_amount", "compare net margin and non-operating impact across fiscal years", ["identify revenue, net income, and operating income", "compute net margin", "compute non-operating impact as net income minus operating income"]))
    state["cases_completed"] += 1
    write_json(STATE_B_PATH, state)

    cid = "round3_test_013_bc2fb598"
    ext = extract_for(cid)
    facts_b = [
        extracted_fact(cid, "VRSK", 1, "revenues", "Revenues", 2023, 2681.4, "USD_millions", ext, "revenues_2023"),
        extracted_fact(cid, "VRSK", 2, "operating_income", "Operating income", 2023, 1131.7, "USD_millions", ext, "operating_income_2023"),
        extracted_fact(cid, "VRSK", 3, "net_income_attributable", "Net income attributable to Verisk", 2023, 614.6, "USD_millions", ext, "net_income_attributable_2023"),
        extracted_fact(cid, "VRSK", 4, "loss_from_discontinued_ops", "Loss from discontinued operations, net of tax", 2023, -154.0, "USD_millions", ext, "loss_from_discontinued_ops_2023"),
    ]
    reextracted.extend(facts_b)
    facts = [sf_from_reextracted(f) for f in facts_b]
    slots = [
        target_slot("operating_margin_2023", 42.207, 0.1, "percentage", [2023], ["percent", "ratio_decimal"]),
        target_slot("net_margin_2023", 22.921, 0.1, "percentage", [2023], ["percent", "ratio_decimal"]),
        target_slot("margin_gap_due_to_discontinued", 19.286, 0.3, "percentage_points", [2023], ["percentage_points", "percent"], "derived", False),
    ]
    scorer.append(scorer_row(cid, "operating_vs_net_margin", facts, slots))
    visible.append(visible_row(cid, "operating_vs_net_margin", "operating_income / revenues * 100; net_income_attributable / revenues * 100", "operating_income and net_income_attributable", "revenues", [2023], "percentage", "compare operating margin versus net margin and explain discontinued operations impact", ["identify revenues, operating income, and net income attributable", "compute operating margin", "compute net margin", "compare margin gap"]))
    state["cases_completed"] += 1
    write_json(STATE_B_PATH, state)

    cid = "round3_test_012_f9d03e27"
    ext = extract_for(cid)
    facts_b = [
        extracted_fact(cid, "GM", 1, "net_sales_tpo", "TPO Net sales and revenues", 2023, 33315.5, "USD_millions", ext, "net_sales_tpo_2023"),
        extracted_fact(cid, "GM", 2, "net_sales_tpo", "TPO Net sales and revenues", 2022, 27314.3, "USD_millions", ext, "net_sales_tpo_2022"),
        extracted_fact(cid, "GM", 3, "net_sales_tpo", "TPO Net sales and revenues", 2021, 21834.5, "USD_millions", ext, "net_sales_tpo_2021"),
        extracted_fact(cid, "GM", 4, "cost_of_sales_tpo", "TPO Cost of sales and revenues", 2023, 26894.2, "USD_millions", ext, "cost_of_sales_tpo_2023"),
        extracted_fact(cid, "GM", 5, "cost_of_sales_tpo", "TPO Cost of sales and revenues", 2022, 23291.0, "USD_millions", ext, "cost_of_sales_tpo_2022"),
        extracted_fact(cid, "GM", 6, "cost_of_sales_tpo", "TPO Cost of sales and revenues", 2021, 19092.4, "USD_millions", ext, "cost_of_sales_tpo_2021"),
    ]
    reextracted.extend(facts_b)
    req = required_by_case[cid]
    facts = [sf_from_reextracted(f) for f in facts_b] + [fact_from_required(req, f"{cid}_fact_{i:02d}") for i in range(5, 9)]
    slots = [
        target_slot("tpo_gross_margin_2023", 19.273, 0.1, "percentage", [2023], ["percent", "ratio_decimal"]),
        target_slot("tpo_gross_margin_2021", 12.557, 0.1, "percentage", [2021], ["percent", "ratio_decimal"]),
        target_slot("tpo_gross_margin_pp_change_2021_to_2023", 6.716, 0.2, "percentage_points", [2021, 2023], ["percentage_points", "percent"]),
    ]
    scorer.append(scorer_row(cid, "tpo_segment_gross_margin", facts, slots))
    visible.append(visible_row(cid, "tpo_segment_gross_margin", "(net_sales_tpo - cost_of_sales_tpo) / net_sales_tpo * 100", "net_sales_tpo minus cost_of_sales_tpo", "net_sales_tpo", [2021, 2022, 2023], "percentage", "compare TPO segment gross margin in 2023 versus 2021", ["identify TPO net sales and cost of sales", "compute gross margin for 2023 and 2021", "compute percentage point change"]))
    state["cases_completed"] += 1
    write_json(STATE_B_PATH, state)

    return scorer, visible, reextracted


def run_group_a(required_by_case: dict[str, dict[str, dict[str, Any]]]) -> None:
    cases = [
        "round3_test_011_e428c7bc",
        "round3_test_004_b035aeed",
        "round3_test_009_3a2f3700",
        "round3_test_007_4ac62908",
        "round3_test_017_68bdbbb8",
    ]
    state = state_template("A", cases)
    write_json(STATE_A_PATH, state)
    scorer, visible, supplemental = build_group_a(required_by_case)
    running_scorer: list[dict[str, Any]] = []
    running_visible: list[dict[str, Any]] = []
    for srow, vrow in zip(scorer, visible):
        running_scorer.append(srow)
        running_visible.append(vrow)
        state["cases_completed"] += 1
        write_json(STATE_A_PATH, state)
    upsert_rows(SCORER_PATH, running_scorer)
    upsert_rows(VISIBLE_PATH, running_visible)
    write_jsonl(SUPP_A_PATH, supplemental)
    complete_state(STATE_A_PATH, state)


def run_group_b(required_by_case: dict[str, dict[str, dict[str, Any]]], cases_by_id: dict[str, dict[str, Any]]) -> None:
    cases = [
        "round3_test_016_707dc83f",
        "round3_test_018_0748ea37",
        "round3_test_014_42c9db2b",
        "round3_test_013_bc2fb598",
        "round3_test_012_f9d03e27",
    ]
    state = state_template("B", cases, openai=True)
    write_json(STATE_B_PATH, state)
    scorer, visible, reextracted = build_group_b(required_by_case, cases_by_id, state)
    upsert_rows(SCORER_PATH, scorer)
    upsert_rows(VISIBLE_PATH, visible)
    write_jsonl(REEXTRACTED_B_PATH, reextracted)
    complete_state(STATE_B_PATH, state)


def verify_outputs() -> dict[str, Any]:
    scorer = read_jsonl(SCORER_PATH)
    visible = read_jsonl(VISIBLE_PATH)
    supp_a = read_jsonl(SUPP_A_PATH)
    rex_b = read_jsonl(REEXTRACTED_B_PATH)
    return {
        "test_scorer_contracts_rows": len(scorer),
        "test_model_visible_contracts_rows": len(visible),
        "supplemental_facts_A_rows": len(supp_a),
        "reextracted_facts_B_rows": len(rex_b),
        "scorer_case_ids": [row["case_id"] for row in scorer],
        "visible_case_ids": [row["case_id"] for row in visible],
        "fallback_used_B_count": sum(1 for row in rex_b if row.get("fallback_used")),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--group", choices=["A", "B", "all"], default="all")
    args = parser.parse_args()

    OUT.mkdir(parents=True, exist_ok=True)
    required_by_case = load_required_by_case()
    cases_by_id = {case["case_id"]: case for case in read_json(TEST_CASES_PATH)}

    if args.group in {"A", "all"}:
        run_group_a(required_by_case)
    if args.group in {"B", "all"}:
        run_group_b(required_by_case, cases_by_id)

    print(json.dumps(verify_outputs(), ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
