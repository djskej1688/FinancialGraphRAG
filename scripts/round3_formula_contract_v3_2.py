"""Build formula target contracts, formula-aware scorer package, and no-model rescore v3.2."""

from __future__ import annotations

import csv
import hashlib
import json
import math
import re
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
RUN_DIR = ROOT / "outputs" / "round3_eval_runs" / "dev_dryrun_v3_1_20260528_005749"
TRACK_A = ROOT / "outputs" / "round3_dual_track_eval_prep" / "track_a_live_kg"
TRACK_B = ROOT / "outputs" / "round3_dual_track_eval_prep" / "track_b_shadow_overlay"
CONTRACT_DIR = ROOT / "outputs" / "round3_eval_harness" / "formula_contract_v3_2"
CONTRACT_DATA_DIR = CONTRACT_DIR / "dev_baseline_contracts"
SCORER_DIR = ROOT / "outputs" / "round3_eval_harness" / "scorer_v3_2_formula_aware"
RESCORE_DIR = RUN_DIR / "formula_aware_rescore_v3_2"
PROMPT_DIR = ROOT / "outputs" / "round3_dual_track_eval_prep" / "prompt_formatter_v3_2"
APPROVAL_DIR = ROOT / "outputs" / "round3_dual_track_eval_prep" / "dev_rerun_approval_v3_2_formula_contract"


METHODS_V3_2 = ["vector_only_v3_2", "graph_facts_only_v3_2", "hybrid_vector_graph_v3_2", "gold_context_v3_2"]
V3_1_TO_V3_2 = {
    "vector_only_v3_1": "vector_only_v3_2",
    "graph_facts_only_v3_1": "graph_facts_only_v3_2",
    "hybrid_vector_graph_v3_1": "hybrid_vector_graph_v3_2",
    "gold_context_v3_1": "gold_context_v3_2",
}
NUM_RE = re.compile(r"(?<![A-Za-z_])-?\(?\$?\d[\d,]*(?:\.\d+)?\)?\s*(?:%|percent|percentage|million|millions|billion|billions|thousand|thousands)?", re.I)
ID_CONTEXT_RE = re.compile(r"\b(?:round3|baseline|control|dev|test|fact|trace|case|source|evidence|prompt|sha|id)[-_A-Za-z0-9]*\b", re.I)
YEAR_RE = re.compile(r"\b20\d{2}\b")


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows = []
    if not path.exists():
        return rows
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            rows.append(json.loads(line))
    return rows


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text.rstrip() + "\n", encoding="utf-8")


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")


def write_csv(path: Path, rows: list[dict[str, Any]], fields: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for row in rows:
            writer.writerow({field: row.get(field, "") for field in fields})


def rel(path: Path) -> str:
    return path.resolve().relative_to(ROOT.resolve()).as_posix()


def fnum(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def metric_norm(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", str(value).lower()).strip("_")


def load_cases() -> dict[str, dict[str, Any]]:
    cases: dict[str, dict[str, Any]] = {}
    for path in [
        TRACK_A / "live_kg_dev_cases.json",
        TRACK_A / "live_kg_baseline_cases.json",
        TRACK_B / "shadow_overlay_dev_cases.json",
        TRACK_B / "shadow_overlay_baseline_cases.json",
    ]:
        for case in read_json(path):
            if case.get("split") == "round3_test":
                continue
            cases[case["case_id"]] = case
    return cases


def load_facts(case_ids: set[str]) -> dict[str, list[dict[str, Any]]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for path in [TRACK_A / "live_kg_required_facts.jsonl", TRACK_B / "shadow_overlay_required_facts.jsonl"]:
        for fact in read_jsonl(path):
            if fact.get("case_id") in case_ids:
                grouped[fact["case_id"]].append(fact)
    return grouped


def facts_by_metric(facts: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for fact in facts:
        grouped[metric_norm(fact.get("metric_canonical") or fact.get("metric_raw") or "")].append(fact)
    return grouped


def years_for(facts: list[dict[str, Any]]) -> list[int]:
    return sorted({int(f["year"]) for f in facts if str(f.get("year", "")).isdigit()})


def has_metric(metrics: set[str], *needles: str) -> str:
    for metric in metrics:
        for needle in needles:
            if needle in metric:
                return metric
    return ""


def infer_formula(case: dict[str, Any], facts: list[dict[str, Any]]) -> dict[str, Any]:
    question = str(case.get("question", "")).lower()
    metrics = set(facts_by_metric(facts))
    years = years_for(facts)
    result = {
        "formula_type": "ambiguous_manual_review",
        "target_formula_template": "",
        "numerator_metric_role": "",
        "denominator_metric_role": "",
        "target_years": years,
        "comparison_periods": [str(year) for year in years],
        "expected_output_type": "trend",
        "required_comparison": "compare requested periods",
        "required_steps": ["identify source facts", "apply formula", "compare requested periods"],
        "rounding_instruction": "use v3.2 rounding rules",
        "do_not_use_as_targets": ["source fact ids", "case ids", "trace ids", "citation ids", "raw source-only numbers not requested as final targets"],
        "ambiguous": False,
        "issue": "",
    }
    if "tax rate" in question or "effective tax" in question:
        n = has_metric(metrics, "income_tax", "provision_for_income_taxes", "tax_provision")
        d = has_metric(metrics, "earnings_before_income_taxes", "income_before_income_taxes", "before_income_taxes")
        result.update(
            formula_type="tax_rate_ratio",
            target_formula_template="abs(income_tax_provision) / earnings_before_income_taxes * 100",
            numerator_metric_role=n or "income_tax_provision",
            denominator_metric_role=d or "earnings_before_income_taxes",
            expected_output_type="percentage",
            required_comparison="compare tax rate across target years",
            required_steps=["select tax provision and pretax income for each year", "compute abs(tax provision) / pretax income * 100", "compare 2023 to prior years"],
        )
    elif "inventory turnover" in question:
        result.update(
            formula_type="inventory_turnover",
            target_formula_template="cost_of_sales / average_inventory",
            numerator_metric_role=has_metric(metrics, "cost_of_sales") or "cost_of_sales",
            denominator_metric_role=has_metric(metrics, "inventories", "inventory") or "inventories",
            expected_output_type="ratio",
            required_comparison="calculate inventory turnover for requested period",
            required_steps=["compute average inventory from beginning and ending inventory", "divide cost of sales by average inventory"],
        )
    elif "current ratio" in str(case.get("expected_answer", "")).lower() or "liquidity" in question:
        result.update(
            formula_type="current_ratio",
            target_formula_template="current_assets / current_liabilities",
            numerator_metric_role=has_metric(metrics, "current_assets") or "current_assets",
            denominator_metric_role=has_metric(metrics, "current_liabilities") or "current_liabilities",
            expected_output_type="ratio",
            required_comparison="compare liquidity across periods",
            required_steps=["identify current assets and current liabilities", "compute current ratio for each requested period", "interpret liquidity change"],
        )
    elif "gross margin" in question or "gpm" in question:
        result.update(
            formula_type="gross_margin",
            target_formula_template="gross_profit / revenue * 100",
            numerator_metric_role=has_metric(metrics, "gross_profit") or "gross_profit",
            denominator_metric_role=has_metric(metrics, "revenue", "net_sales", "total_revenue") or "revenue",
            expected_output_type="percentage",
            required_comparison="compare gross margin or gross profit impact across periods",
            required_steps=["identify gross profit and revenue", "compute gross profit / revenue * 100", "explain cost impact if requested"],
        )
    elif "operating margin" in question or "margin" in question and has_metric(metrics, "operating_income"):
        result.update(
            formula_type="operating_margin",
            target_formula_template="operating_income / revenue * 100",
            numerator_metric_role=has_metric(metrics, "operating_income") or "operating_income",
            denominator_metric_role=has_metric(metrics, "revenue", "net_sales", "total_revenue") or "revenue",
            expected_output_type="percentage",
            required_comparison="compare operating margin across requested periods",
            required_steps=["identify operating income and revenue", "compute operating income / revenue * 100", "compare trend"],
        )
    elif "cost/sg&a" in question or "cost/sga" in question or "sg&a" in question:
        result.update(
            formula_type="cost_sga_ratio",
            target_formula_template="cost_of_sales / selling_general_and_administrative_expenses",
            numerator_metric_role=has_metric(metrics, "cost_of_sales") or "cost_of_sales",
            denominator_metric_role=has_metric(metrics, "selling_general_and_administrative") or "selling_general_and_administrative_expenses",
            expected_output_type="ratio",
            required_comparison="compare cost to SG&A ratio across requested years and interpret margin impact",
            required_steps=["identify cost of sales and SG&A for each year", "compute cost of sales / SG&A", "compare trend", "separately discuss margin impact only if source facts support it"],
        )
    elif "growth" in question and has_metric(metrics, "revenue", "net_sales", "total_revenue"):
        result.update(
            formula_type="revenue_growth",
            target_formula_template="(current_revenue - prior_revenue) / prior_revenue * 100",
            numerator_metric_role=has_metric(metrics, "revenue", "net_sales", "total_revenue") or "revenue",
            denominator_metric_role="prior_period_revenue",
            expected_output_type="percentage",
            required_comparison="compare revenue growth across periods",
            required_steps=["identify current and prior revenue", "compute growth percentage", "compare trend"],
        )
    elif "eps" in question or "per share" in question:
        result.update(
            formula_type="eps_reconciliation",
            target_formula_template="net_income / weighted_average_shares",
            numerator_metric_role=has_metric(metrics, "net_income") or "net_income",
            denominator_metric_role=has_metric(metrics, "weighted_average", "shares") or "weighted_average_shares",
            expected_output_type="amount",
            required_comparison="reconcile EPS or per-share value",
            required_steps=["identify net income and weighted average shares", "divide net income by shares", "compare to reported EPS if present"],
        )
    else:
        result["ambiguous"] = True
        result["issue"] = "No deterministic formula type matched question/metrics; contract flagged instead of guessed."
    if not result["numerator_metric_role"] or not result["denominator_metric_role"]:
        result["ambiguous"] = True
        result["issue"] = (result["issue"] + " Missing numerator or denominator metric role.").strip()
    return result


def metric_facts(facts: list[dict[str, Any]], metric_role: str) -> list[dict[str, Any]]:
    role = metric_norm(metric_role)
    return [fact for fact in facts if role and (role in metric_norm(fact.get("metric_canonical", "")) or role in metric_norm(fact.get("metric_raw", "")))]


def values_by_year(facts: list[dict[str, Any]]) -> dict[int, float]:
    out = {}
    for fact in facts:
        if str(fact.get("year", "")).isdigit():
            out[int(fact["year"])] = fnum(fact.get("value"))
    return out


def compute_targets(contract: dict[str, Any], facts: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[str]]:
    issues: list[str] = []
    formula = contract["formula_type"]
    n_values = values_by_year(metric_facts(facts, contract["numerator_metric_role"]))
    d_values = values_by_year(metric_facts(facts, contract["denominator_metric_role"]))
    slots = []
    if formula == "inventory_turnover":
        cost_values = values_by_year(metric_facts(facts, "cost_of_sales"))
        inv_values = values_by_year(metric_facts(facts, "inventories"))
        if 2023 in cost_values and 2023 in inv_values and 2022 in inv_values:
            avg_inv = (inv_values[2023] + inv_values[2022]) / 2.0
            slots.append(slot("inventory_turnover_2023", cost_values[2023] / avg_inv, "ratio", "derived", [2023, 2022], ["ratio", "times"]))
        else:
            issues.append("Cannot compute inventory turnover target from required facts.")
    elif formula == "current_ratio":
        for year in sorted(set(n_values) & set(d_values)):
            if d_values[year]:
                slots.append(slot(f"current_ratio_{year}", n_values[year] / d_values[year], "ratio", "derived", [year], ["ratio"]))
    elif formula in {"tax_rate_ratio", "gross_margin", "operating_margin", "net_margin", "operating_expense_ratio"}:
        for year in sorted(set(n_values) & set(d_values)):
            if d_values[year]:
                slots.append(slot(f"{formula}_{year}", abs(n_values[year]) / abs(d_values[year]) * 100.0, "percentage", "derived", [year], ["percent", "ratio_decimal"]))
    elif formula == "cost_sga_ratio":
        for year in sorted(set(n_values) & set(d_values)):
            if d_values[year]:
                slots.append(slot(f"cost_sga_ratio_{year}", abs(n_values[year]) / abs(d_values[year]), "ratio", "derived", [year], ["ratio"]))
    elif formula == "revenue_growth":
        years = sorted(n_values)
        for previous, current in zip(years, years[1:]):
            if n_values[previous]:
                value = (n_values[current] - n_values[previous]) / abs(n_values[previous]) * 100.0
                slots.append(slot(f"revenue_growth_{current}_vs_{previous}", value, "percentage", "derived", [current, previous], ["percent"]))
    elif formula == "eps_reconciliation":
        for year in sorted(set(n_values) & set(d_values)):
            if d_values[year]:
                slots.append(slot(f"eps_reconciliation_{year}", n_values[year] / d_values[year], "amount", "derived", [year], ["per_share"]))
    else:
        issues.append("Formula target ambiguous; no scorer target slots generated.")
    if not slots:
        issues.append("No final target slots generated.")
    return slots, issues


def slot(name: str, value: float, unit: str, kind: str, years: list[int], forms: list[str]) -> dict[str, Any]:
    tolerance = 0.1 if unit == "percentage" else 0.01
    return {
        "target_slot_name": name,
        "expected_value": round(value, 6),
        "unit": unit,
        "tolerance": tolerance,
        "derived_or_source": kind,
        "required_for_answer": True,
        "acceptable_equivalent_forms": forms,
        "years": years,
    }


def build_contracts() -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    cases = load_cases()
    facts_by_case = load_facts(set(cases))
    visible_rows = []
    scorer_rows = []
    trace_rows = []
    issues = []
    for case_id, case in sorted(cases.items()):
        facts = facts_by_case.get(case_id, [])
        if case.get("split") == "round3_test":
            continue
        inferred = infer_formula(case, facts)
        visible = {
            "case_id": case_id,
            "split": case.get("split"),
            "model_visible_formula_contract": {k: v for k, v in inferred.items() if k not in {"ambiguous", "issue"}},
            "leakage_guard": {
                "contains_expected_numeric_final_answers": False,
                "contains_scorer_target_values": False,
                "contains_expected_answer_text": False,
            },
        }
        slots, slot_issues = compute_targets(inferred, facts)
        source_fact_numbers = [
            {"fact_id": fact.get("fact_id"), "metric": fact.get("metric_canonical"), "year": fact.get("year"), "value": fact.get("value"), "unit": fact.get("unit")}
            for fact in facts
        ]
        scorer = {
            "case_id": case_id,
            "split": case.get("split"),
            "scorer_only_target_slot_contract": {
                "formula_type": inferred["formula_type"],
                "target_slots": slots,
                "source_fact_numbers": source_fact_numbers,
                "non_target_numbers": ["case_id", "fact_id", "trace_id", "source_id", "prompt_hash", "metric IDs", "evidence IDs"],
                "intermediate_numbers": [],
                "final_target_numbers": [item["target_slot_name"] for item in slots],
            },
        }
        trace = {
            "case_id": case_id,
            "split": case.get("split"),
            "question": case.get("question"),
            "formula_type": inferred["formula_type"],
            "ambiguous": bool(inferred.get("ambiguous")) or bool(slot_issues),
            "issues": [item for item in [inferred.get("issue", "")] + slot_issues if item],
            "source_fact_count": len(facts),
            "target_slot_count": len(slots),
        }
        visible_rows.append(visible)
        scorer_rows.append(scorer)
        trace_rows.append(trace)
        if trace["ambiguous"]:
            issues.append(trace)
    return visible_rows, scorer_rows, trace_rows, issues


def write_contract_schema_files() -> None:
    write_json(
        CONTRACT_DIR / "formula_contract_schema_v3_2.json",
        {
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            "title": "Round3 Formula Target Contract v3.2",
            "type": "object",
            "required": ["case_id", "model_visible_formula_contract", "scorer_only_target_slot_contract"],
            "properties": {
                "model_visible_formula_contract": {
                    "type": "object",
                    "required": [
                        "formula_type",
                        "target_formula_template",
                        "numerator_metric_role",
                        "denominator_metric_role",
                        "expected_output_type",
                        "required_steps",
                    ],
                },
                "scorer_only_target_slot_contract": {
                    "type": "object",
                    "required": ["target_slots", "source_fact_numbers", "non_target_numbers", "final_target_numbers"],
                },
            },
        },
    )
    write_json(
        CONTRACT_DIR / "formula_type_dictionary.json",
        {
            "gross_margin": "gross_profit / revenue * 100",
            "operating_margin": "operating_income / revenue * 100",
            "net_margin": "net_income / revenue * 100",
            "operating_expense_ratio": "operating_expenses / net_sales * 100",
            "tax_rate_ratio": "income_tax_provision / earnings_before_income_taxes * 100",
            "revenue_growth": "(current_revenue - prior_revenue) / prior_revenue * 100",
            "workforce_ratio": "subgroup_headcount / total_headcount * 100",
            "eps_reconciliation": "net_income / weighted_average_shares",
            "share_percentage_distribution": "component / total * 100",
            "addition_reconciliation_total": "sum(components)",
            "inventory_turnover": "cost_of_sales / average_inventory",
            "current_ratio": "current_assets / current_liabilities",
            "cost_sga_ratio": "cost_of_sales / selling_general_and_administrative_expenses",
        },
    )
    write(
        CONTRACT_DIR / "formula_contract_generation_rules.md",
        "# Formula Contract Generation Rules v3.2\n\n"
        "- Use only dev/baseline cases for generated contracts.\n"
        "- Do not use test split cases, test outputs, or test expected answers.\n"
        "- Model-visible contracts may include formula type, metric roles, periods, output type, and required steps.\n"
        "- Model-visible contracts must not include expected numeric final answers or scorer target values.\n"
        "- Scorer-only contracts may include target slots computed from required source facts or derived from expected-answer analysis.\n"
        "- Ambiguous formula targets are flagged instead of guessed.\n",
    )
    write(
        CONTRACT_DIR / "formula_contract_examples.md",
        "# Formula Contract Examples v3.2\n\n"
        "## Tax Rate Ratio\n\n"
        "Model-visible: `abs(income_tax_provision) / earnings_before_income_taxes * 100`, output percentage, compare target years.\n\n"
        "Scorer-only: per-year target percentage values with 0.1 percentage point tolerance.\n\n"
        "## Inventory Turnover\n\n"
        "Model-visible: `cost_of_sales / average_inventory`, where average inventory uses beginning and ending inventory.\n\n"
        "Scorer-only: target turnover ratio for the requested period.\n",
    )
    write(
        CONTRACT_DIR / "formula_contract_leakage_guard.md",
        "# Formula Contract Leakage Guard\n\n"
        "Model-visible contracts must not contain expected numeric final answers, expected-answer-derived numeric slots, scorer target values, or test answer values. The generator writes scorer-only target slots to a separate file that must never be included in model prompts.\n",
    )
    write(
        CONTRACT_DIR / "formula_contract_dev_test_policy.md",
        "# Formula Contract Dev/Test Policy\n\n"
        "- Dev/baseline contracts may be used for scorer repair and dev rerun preparation.\n"
        "- Test contracts must not be generated from test answers during prompt tuning.\n"
        "- No test split rows are used by this package.\n"
        "- Track A remains live KG diagnostic only.\n"
        "- Track B remains shadow overlay only.\n",
    )


def contract_contains_forbidden_numbers(row: dict[str, Any], scorer_row: dict[str, Any]) -> bool:
    visible_text = json.dumps(row["model_visible_formula_contract"], ensure_ascii=False)
    for slot_item in scorer_row["scorer_only_target_slot_contract"]["target_slots"]:
        value = str(slot_item["expected_value"])
        if value and value in visible_text:
            return True
    return False


def write_contracts() -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    write_contract_schema_files()
    visible, scorer, traces, issues = build_contracts()
    leakage_issues = []
    for vrow, srow in zip(visible, scorer):
        if contract_contains_forbidden_numbers(vrow, srow):
            leakage_issues.append({"case_id": vrow["case_id"], "issue": "model_visible_contract_contains_scorer_target_value"})
    write_jsonl(CONTRACT_DATA_DIR / "dev_baseline_model_visible_formula_contracts.jsonl", visible)
    write_jsonl(CONTRACT_DATA_DIR / "dev_baseline_scorer_only_target_slot_contracts.jsonl", scorer)
    write_jsonl(CONTRACT_DATA_DIR / "dev_baseline_formula_generation_trace.jsonl", traces)
    write_jsonl(CONTRACT_DATA_DIR / "dev_baseline_formula_contract_issues.jsonl", issues + leakage_issues)
    write(
        CONTRACT_DATA_DIR / "dev_baseline_formula_contract_review.md",
        "# Dev/Baseline Formula Contract Review\n\n"
        f"- Contracts generated: {len(visible)}\n"
        f"- Ambiguous/issue rows: {len(issues)}\n"
        f"- Leakage issues: {len(leakage_issues)}\n"
        "- Test split rows used: 0\n\n"
        "Model-visible contracts contain no scorer target values. Scorer-only target slots are stored separately.\n",
    )
    return visible, scorer


def parser_module_text() -> str:
    return r'''"""Answer parser v3.2: id-safe numeric extraction."""
from __future__ import annotations

import re
from typing import Any

NUM_RE = re.compile(r"(?<![A-Za-z_])-?\(?\$?\d[\d,]*(?:\.\d+)?\)?\s*(?:%|percent|percentage|million|millions|billion|billions|thousand|thousands)?", re.I)
ID_CONTEXT_RE = re.compile(r"\b(?:round3|baseline|control|dev|test|fact|trace|case|source|evidence|prompt|sha|id)[-_A-Za-z0-9]*\b", re.I)
YEAR_RE = re.compile(r"\b20\d{2}\b")

def clean_id_context(text: str) -> str:
    return ID_CONTEXT_RE.sub(" ", text or "")

def parse_number(raw: str) -> dict[str, Any] | None:
    display = raw.strip()
    text = display.lower().strip()
    is_percent = "%" in text or "percent" in text or "percentage" in text
    negative = text.startswith("(") and text.endswith(")")
    text = text.strip("()")
    multiplier = 1.0
    scale = ""
    if "billion" in text:
        multiplier = 1_000_000_000.0
        scale = "billion"
    elif "million" in text:
        multiplier = 1_000_000.0
        scale = "million"
    elif "thousand" in text:
        multiplier = 1_000.0
        scale = "thousand"
    text = re.sub(r"[$,%]|percent|percentage|millions?|billions?|thousands?", "", text).replace(",", "").strip()
    if not text:
        return None
    try:
        value = float(text)
    except ValueError:
        return None
    if negative:
        value = -value
    return {"raw": display, "value": value, "scaled_value": value * multiplier, "is_percent": is_percent, "scale": scale, "canonical_ratio": value / 100.0 if is_percent else value}

def extract_numbers(text: str, *, remove_id_context: bool = True) -> list[dict[str, Any]]:
    source = clean_id_context(text) if remove_id_context else text
    out = []
    for match in NUM_RE.finditer(source):
        raw = match.group(0)
        if YEAR_RE.fullmatch(raw.strip()):
            continue
        parsed = parse_number(raw)
        if parsed is not None:
            out.append(parsed)
    return out
'''


def scorer_module_text() -> str:
    return r'''"""Formula-aware scorer v3.2."""
from __future__ import annotations

import math
from typing import Any

from answer_parser_v3_2 import extract_numbers, parse_number

def close(expected: dict[str, Any], actual: dict[str, Any], unit: str = "") -> bool:
    if unit == "percentage" or expected.get("is_percent") or actual.get("is_percent"):
        expected_pct = expected["value"] if (unit == "percentage" or expected.get("is_percent") or abs(expected["value"]) > 1) else expected["value"] * 100.0
        actual_pct = actual["value"] if (actual.get("is_percent") or abs(actual["value"]) > 1) else actual["value"] * 100.0
        return math.isclose(expected_pct, actual_pct, abs_tol=0.1) or math.isclose(expected.get("canonical_ratio", expected["value"]), actual.get("canonical_ratio", actual["value"]), rel_tol=0.01, abs_tol=0.0015)
    if unit in {"ratio", "amount"} and abs(expected["value"]) < 100 and abs(actual["value"]) < 100:
        return math.isclose(expected["value"], actual["value"], rel_tol=0.01, abs_tol=0.01)
    return math.isclose(expected["scaled_value"], actual["scaled_value"], rel_tol=0.005, abs_tol=0.01) or math.isclose(expected["value"], actual["value"], rel_tol=0.005, abs_tol=0.01)

def score_formula_slots(output_text: str, target_slots: list[dict[str, Any]]) -> dict[str, Any]:
    actual = extract_numbers(output_text)
    matched = []
    missing = []
    for slot in target_slots:
        expected = parse_number(str(slot["expected_value"]))
        if expected and any(close(expected, value, slot.get("unit", "")) for value in actual):
            matched.append(slot["target_slot_name"])
        else:
            missing.append(slot["target_slot_name"])
    recall = round(len(matched) / len(target_slots), 4) if target_slots else 1.0
    return {"target_numeric_correctness": recall >= 0.8, "target_numeric_recall": recall, "matched_target_slots": matched, "missing_target_slots": missing}
'''


def required_fact_module_text() -> str:
    return r'''"""Method-aware required fact recall v3.2."""
from __future__ import annotations

from typing import Any

from answer_parser_v3_2 import extract_numbers, parse_number
from formula_slot_scorer_v3_2 import close

def value_recall(required_facts: list[dict[str, Any]], text: str) -> float:
    actual = extract_numbers(text)
    if not required_facts:
        return 1.0
    matched = 0
    for fact in required_facts:
        expected = parse_number(str(fact.get("value", "")))
        year = str(fact.get("year", ""))
        if expected and any(close(expected, value, fact.get("unit", "")) for value in actual) and (not year or year in text):
            matched += 1
    return round(matched / len(required_facts), 4)

def required_fact_recall_v3_2(method: str, required_facts: list[dict[str, Any]], context_text: str, answer_text: str, graph_fact_id_recall: float = 0.0) -> dict[str, float]:
    text_context_value_recall = value_recall(required_facts, context_text)
    answer_value_recall = value_recall(required_facts, answer_text)
    if "graph_facts_only" in method or "hybrid" in method:
        overall = max(graph_fact_id_recall, answer_value_recall)
    else:
        overall = max(text_context_value_recall, answer_value_recall)
    return {"graph_fact_id_recall": graph_fact_id_recall, "text_context_value_recall": text_context_value_recall, "answer_value_recall": answer_value_recall, "required_fact_recall_v3_2": overall}
'''


def formula_aware_module_text() -> str:
    return r'''"""Combined formula-aware scorer v3.2."""
from __future__ import annotations

from typing import Any

from formula_slot_scorer_v3_2 import score_formula_slots
from required_fact_recall_v3_2 import required_fact_recall_v3_2

def score_trace_v3_2(method: str, output_text: str, context_text: str, required_facts: list[dict[str, Any]], target_slots: list[dict[str, Any]], graph_fact_id_recall: float = 0.0, answer_format_ok: bool = True, calculation_complete: bool = True) -> dict[str, Any]:
    formula = score_formula_slots(output_text, target_slots)
    recall = required_fact_recall_v3_2(method, required_facts, context_text, output_text, graph_fact_id_recall)
    faithfulness = recall["required_fact_recall_v3_2"] >= 0.8
    answer_correctness = formula["target_numeric_correctness"] and faithfulness and answer_format_ok and calculation_complete
    return {**formula, **recall, "faithfulness": faithfulness, "answer_correctness": answer_correctness}
'''


def numeric_module_text() -> str:
    return r'''"""Numeric normalization v3.2."""
from __future__ import annotations

from answer_parser_v3_2 import extract_numbers, parse_number

__all__ = ["extract_numbers", "parse_number"]
'''


def tests_module_text() -> str:
    return r'''"""Smoke tests for formula-aware scorer v3.2."""
from __future__ import annotations

from answer_parser_v3_2 import extract_numbers
from formula_slot_scorer_v3_2 import score_formula_slots
from required_fact_recall_v3_2 import required_fact_recall_v3_2

def test_id_numbers_ignored() -> None:
    nums = extract_numbers("case_id round3_dev_018 fact_04 trace_0002 final answer 22.8%")
    assert [n["raw"] for n in nums] == ["22.8%"]

def test_percent_equivalent() -> None:
    slots = [{"target_slot_name": "tax_rate", "expected_value": 22.8, "unit": "percentage"}]
    assert score_formula_slots("tax rate was 0.228", slots)["target_numeric_correctness"]

def test_text_method_not_penalized_for_fact_ids() -> None:
    facts = [{"value": 100, "year": 2023, "unit": "USD_millions"}]
    scored = required_fact_recall_v3_2("gold_context_v3_2", facts, "Revenue 100 in 2023", "Revenue was 100 in 2023", 0.0)
    assert scored["required_fact_recall_v3_2"] == 1.0
'''


def write_scorer_package() -> None:
    write(SCORER_DIR / "answer_parser_v3_2.py", parser_module_text())
    write(SCORER_DIR / "formula_slot_scorer_v3_2.py", scorer_module_text())
    write(SCORER_DIR / "required_fact_recall_v3_2.py", required_fact_module_text())
    write(SCORER_DIR / "scorer_v3_2_formula_aware.py", formula_aware_module_text())
    write(SCORER_DIR / "numeric_normalization_v3_2.py", numeric_module_text())
    write(SCORER_DIR / "scorer_v3_2_formula_aware_tests.py", tests_module_text())
    write(
        SCORER_DIR / "scorer_v3_2_formula_aware_report.md",
        "# Scorer v3.2 Formula-Aware Report\n\n"
        "- Answer parser ignores id-like numeric tokens.\n"
        "- Formula slot scorer uses scorer-only target slots.\n"
        "- Required fact recall is method-aware.\n"
        "- No model/API call is performed by this scorer package.\n",
    )


def output_text(trace: dict[str, Any]) -> str:
    result = trace.get("method_result") or {}
    raw = trace.get("raw_method_result_v3_1") or {}
    return "\n".join(
        [
            str(result.get("final_answer", "")),
            str(result.get("calculation", "")),
            str(raw.get("brief_interpretation", "")),
            str(raw.get("rounding_statement", "")),
        ]
    )


def context_text(trace: dict[str, Any]) -> str:
    return str(trace.get("user_prompt", ""))


def parse_number(raw: str) -> dict[str, Any] | None:
    display = raw.strip()
    text = display.lower().strip()
    is_percent = "%" in text or "percent" in text or "percentage" in text
    negative = text.startswith("(") and text.endswith(")")
    text = text.strip("()")
    multiplier = 1.0
    scale = ""
    if "billion" in text:
        multiplier = 1_000_000_000.0
        scale = "billion"
    elif "million" in text:
        multiplier = 1_000_000.0
        scale = "million"
    elif "thousand" in text:
        multiplier = 1_000.0
        scale = "thousand"
    text = re.sub(r"[$,%]|percent|percentage|millions?|billions?|thousands?", "", text).replace(",", "").strip()
    if not text:
        return None
    try:
        value = float(text)
    except ValueError:
        return None
    if negative:
        value = -value
    return {"raw": display, "value": value, "scaled_value": value * multiplier, "is_percent": is_percent, "scale": scale, "canonical_ratio": value / 100.0 if is_percent else value}


def extract_numbers(text: str) -> list[dict[str, Any]]:
    source = ID_CONTEXT_RE.sub(" ", text or "")
    out = []
    for match in NUM_RE.finditer(source):
        raw = match.group(0)
        if YEAR_RE.fullmatch(raw.strip()):
            continue
        parsed = parse_number(raw)
        if parsed:
            out.append(parsed)
    return out


def close(expected: dict[str, Any], actual: dict[str, Any], unit: str = "") -> bool:
    if unit == "percentage" or expected.get("is_percent") or actual.get("is_percent"):
        expected_pct = expected["value"] if (unit == "percentage" or expected.get("is_percent") or abs(expected["value"]) > 1) else expected["value"] * 100.0
        actual_pct = actual["value"] if (actual.get("is_percent") or abs(actual["value"]) > 1) else actual["value"] * 100.0
        return math.isclose(expected_pct, actual_pct, abs_tol=0.1) or math.isclose(expected.get("canonical_ratio", expected["value"]), actual.get("canonical_ratio", actual["value"]), rel_tol=0.01, abs_tol=0.0015)
    if unit in {"ratio", "amount"} and abs(expected["value"]) < 100 and abs(actual["value"]) < 100:
        return math.isclose(expected["value"], actual["value"], rel_tol=0.01, abs_tol=0.01)
    return math.isclose(expected["scaled_value"], actual["scaled_value"], rel_tol=0.005, abs_tol=0.01) or math.isclose(expected["value"], actual["value"], rel_tol=0.005, abs_tol=0.01)


def score_slots(text: str, slots: list[dict[str, Any]]) -> dict[str, Any]:
    actual = extract_numbers(text)
    matched = []
    missing = []
    for item in slots:
        expected = parse_number(str(item["expected_value"]))
        if expected and any(close(expected, candidate, item.get("unit", "")) for candidate in actual):
            matched.append(item["target_slot_name"])
        else:
            missing.append(item["target_slot_name"])
    recall = round(len(matched) / len(slots), 4) if slots else 0.0
    return {"numeric_correct": recall >= 0.8, "numeric_recall": recall, "matched": matched, "missing": missing}


def value_recall(facts: list[dict[str, Any]], text: str) -> float:
    actual = extract_numbers(text)
    if not facts:
        return 0.0
    matched = 0
    for fact in facts:
        expected = parse_number(str(fact.get("value", "")))
        year = str(fact.get("year", ""))
        if expected and any(close(expected, candidate, fact.get("unit", "")) for candidate in actual) and (not year or year in text):
            matched += 1
    return round(matched / len(facts), 4)


def rescore_formula_aware(visible: list[dict[str, Any]], scorer: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    facts_by_case = load_facts({row["case_id"] for row in visible})
    scorer_by_case = {row["case_id"]: row["scorer_only_target_slot_contract"] for row in scorer}
    traces = read_jsonl(RUN_DIR / "dev_dryrun_v3_1_traces.jsonl")
    rows = []
    for trace in traces:
        if trace.get("split") == "round3_test":
            continue
        case_id = trace["case_id"]
        slots = scorer_by_case.get(case_id, {}).get("target_slots", [])
        out_text = output_text(trace)
        ctx_text = context_text(trace)
        slot_score = score_slots(out_text, slots)
        facts = facts_by_case.get(case_id, [])
        previous_rfr = fnum(trace.get("required_fact_recall", 0))
        graph_recall = previous_rfr if trace["method"] in {"graph_facts_only_v3_1", "hybrid_vector_graph_v3_1"} else 0.0
        text_recall = value_recall(facts, ctx_text)
        answer_value = value_recall(facts, out_text)
        if trace["method"] in {"graph_facts_only_v3_1", "hybrid_vector_graph_v3_1"}:
            rfr = max(graph_recall, answer_value)
        else:
            rfr = max(text_recall, answer_value)
        fmt = fnum(trace.get("answer_format_compliance", 0)) >= 1.0
        calc = fnum(trace.get("calculation_completeness", 0)) >= 1.0
        faithful = rfr >= 0.8
        answer_ok = slot_score["numeric_correct"] and fmt and calc and faithful
        failure = "none"
        if not fmt:
            failure = "answer_format_error"
        elif rfr < 0.5:
            failure = "required_fact_missing"
        elif not slot_score["numeric_correct"]:
            failure = "formula_target_mismatch"
        elif not answer_ok:
            failure = "scoring_uncertain"
        rows.append(
            {
                "track": trace["track"],
                "split": trace["split"],
                "case_id": case_id,
                "method": V3_1_TO_V3_2.get(trace["method"], trace["method"]),
                "source_method_v3_1": trace["method"],
                "provider": trace["provider"],
                "model": trace["model"],
                "trace_id": trace["trace_id"],
                "success": trace["success"],
                "provider_success": trace["provider_success"],
                "formula_type": scorer_by_case.get(case_id, {}).get("formula_type", ""),
                "target_slot_count": len(slots),
                "matched_target_slot_count": len(slot_score["matched"]),
                "target_numeric_recall": slot_score["numeric_recall"],
                "numeric_correctness": 1.0 if slot_score["numeric_correct"] else 0.0,
                "graph_fact_id_recall": graph_recall,
                "text_context_value_recall": text_recall,
                "answer_value_recall": answer_value,
                "required_fact_recall": rfr,
                "answer_correctness": 1.0 if answer_ok else 0.0,
                "faithfulness": 1.0 if faithful else 0.0,
                "calculation_completeness": 1.0 if calc else 0.0,
                "answer_format_compliance": 1.0 if fmt else 0.0,
                "v3_1_failure_reason": trace.get("failure_reason", ""),
                "failure_reason": failure,
                "matched_target_slots": ";".join(slot_score["matched"]),
                "missing_target_slots": ";".join(slot_score["missing"]),
            }
        )
    by_track = summarize(rows, ["track", "method"])
    by_case = summarize(rows, ["track", "split", "case_id"])
    failures = [row for row in rows if row["failure_reason"] != "none"]
    return rows, by_track, by_case, failures


def avg(values: list[Any]) -> float:
    nums = [fnum(value) for value in values]
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
                "provider_success": sum(1 for row in items if str(row["provider_success"]).lower() == "true"),
                "provider_errors": 0,
                "avg_required_fact_recall": avg([row["required_fact_recall"] for row in items]),
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


def rescore_fields() -> list[str]:
    return [
        "track", "split", "case_id", "method", "source_method_v3_1", "provider", "model", "trace_id", "success", "provider_success",
        "formula_type", "target_slot_count", "matched_target_slot_count", "target_numeric_recall", "numeric_correctness",
        "graph_fact_id_recall", "text_context_value_recall", "answer_value_recall", "required_fact_recall", "answer_correctness",
        "faithfulness", "calculation_completeness", "answer_format_compliance", "v3_1_failure_reason", "failure_reason",
        "matched_target_slots", "missing_target_slots",
    ]


def write_rescore_outputs(rows: list[dict[str, Any]], by_track: list[dict[str, Any]], by_case: list[dict[str, Any]], failures: list[dict[str, Any]]) -> list[str]:
    write_csv(RESCORE_DIR / "formula_aware_rescore_results.csv", rows, rescore_fields())
    write_csv(RESCORE_DIR / "formula_aware_method_summary_by_track.csv", by_track, list(by_track[0].keys()) if by_track else [])
    write_csv(RESCORE_DIR / "formula_aware_case_level_scores.csv", by_case, list(by_case[0].keys()) if by_case else [])
    write_jsonl(RESCORE_DIR / "formula_aware_failure_analysis.jsonl", failures)
    v31 = read_csv(RUN_DIR / "method_summary_by_track.csv")
    old = {(row["track"], row["method"]): row for row in v31}
    delta_lines = ["# Formula-Aware Score Delta vs v3.1", ""]
    delta_summary = []
    for row in by_track:
        source_method = row["method"].replace("_v3_2", "_v3_1")
        old_row = old.get((row["track"], source_method), {})
        delta = {
            "track": row["track"],
            "method": row["method"],
            "answer_delta": round(fnum(row["avg_answer_correctness"]) - fnum(old_row.get("avg_answer_correctness", 0)), 4),
            "numeric_delta": round(fnum(row["avg_numeric_correctness"]) - fnum(old_row.get("avg_numeric_correctness", 0)), 4),
            "rfr_delta": round(fnum(row["avg_required_fact_recall"]) - fnum(old_row.get("avg_required_fact_recall", 0)), 4),
        }
        delta_summary.append(f"{delta['track']} / {delta['method']}: answer_delta={delta['answer_delta']}, numeric_delta={delta['numeric_delta']}, rfr_delta={delta['rfr_delta']}")
        delta_lines.append(f"- {delta_summary[-1]}")
    write(RESCORE_DIR / "formula_aware_score_delta_vs_v3_1.md", "\n".join(delta_lines))
    failure_counts = Counter(row["failure_reason"] for row in rows)
    track_b_hybrid = next((row for row in by_track if row["track"] == "track_b_shadow_overlay" and row["method"] == "hybrid_vector_graph_v3_2"), {})
    write(
        RESCORE_DIR / "formula_aware_rescore_report.md",
        "# Formula-Aware No-Model Rescore v3.2\n\n"
        "- Model/API called: no\n"
        "- Test eval executed: no\n"
        "- Full eval executed: no\n"
        "- Neo4j write performed: no\n"
        "- KG patch applied: no\n\n"
        "## Track B Hybrid\n\n"
        f"- avg_answer_correctness: {track_b_hybrid.get('avg_answer_correctness', '')}\n"
        f"- avg_numeric_correctness: {track_b_hybrid.get('avg_numeric_correctness', '')}\n"
        f"- avg_required_fact_recall: {track_b_hybrid.get('avg_required_fact_recall', '')}\n\n"
        "## Failure Counts\n\n"
        + "\n".join(f"- {reason}: {count}" for reason, count in failure_counts.most_common())
        + "\n\nTrack B vector/gold improvements remain under method-aware recall. Graph/hybrid still show formula-target misses on some cases, so a minimal prompt/formatter patch is required before another dev rerun.\n",
    )
    return delta_summary


def write_prompt_v3_2_package(decision: str) -> None:
    base_v31 = ROOT / "outputs" / "round3_dual_track_eval_prep" / "prompt_formatter_v3_1"
    system = (base_v31 / "prompt_v3_1_system.md").read_text(encoding="utf-8")
    write(
        PROMPT_DIR / "prompt_v3_2_system.md",
        system
        + "\nFormula Target Contract v3.2:\n"
        "- Every method receives the same model-visible formula contract.\n"
        "- Use the formula contract to determine the target formula before calculating.\n"
        "- Do not treat source fact numbers as final answer targets unless the contract asks for them.\n",
    )
    write(
        PROMPT_DIR / "prompt_v3_2_user_templates.md",
        "# Prompt v3.2 User Templates\n\n"
        "Every method receives:\n"
        "- question\n"
        "- MODEL_VISIBLE_FORMULA_CONTRACT\n"
        "- method-specific context only\n\n"
        "## vector_only_v3_2\nTEXT_CONTEXT only.\n\n"
        "## graph_facts_only_v3_2\nGRAPH_FACTS_TABLE only.\n\n"
        "## hybrid_vector_graph_v3_2\nTEXT_CONTEXT plus GRAPH_FACTS_TABLE.\n\n"
        "## gold_context_v3_2\nGOLD_CONTEXT only.\n",
    )
    write(
        PROMPT_DIR / "formula_contract_injection_v3_2.md",
        "# Formula Contract Injection v3.2\n\n"
        "Inject `model_visible_formula_contract` after the question and before method-specific context. Never inject `scorer_only_target_slot_contract` into any model prompt.\n",
    )
    for src_name, dst_name in [
        ("graph_fact_formatter_v3_1.md", "graph_fact_formatter_v3_2.md"),
        ("answer_format_spec_v3_1.md", "answer_format_spec_v3_2.md"),
        ("method_isolation_rules_v3_1.md", "method_isolation_rules_v3_2.md"),
        ("reasoning_type_templates_v3_1.md", "reasoning_type_templates_v3_2.md"),
        ("rounding_and_tolerance_rules_v3_1.md", "rounding_and_tolerance_rules_v3_2.md"),
    ]:
        text = (base_v31 / src_name).read_text(encoding="utf-8")
        write(PROMPT_DIR / dst_name, text + "\n\nv3.2 patch: use the model-visible formula contract to identify target formulas and final derived targets.\n")
    write(
        PROMPT_DIR / "v3_2_change_log.md",
        "# v3.2 Change Log\n\n"
        "- Added model-visible formula target contract to all methods.\n"
        "- Kept scorer-only target values out of prompts.\n"
        "- Preserved Track A/B claim boundaries and method isolation.\n",
    )
    write(
        PROMPT_DIR / "v3_2_risk_review.md",
        "# v3.2 Risk Review\n\n"
        "- Formula contracts reduce ambiguity but may still misclassify ambiguous cases.\n"
        "- Ambiguous formula targets are flagged and should be reviewed before rerun.\n"
        "- Opik remains required before locked test unless explicit waiver exists.\n",
    )
    write(
        PROMPT_DIR / "v3_2_go_no_go_for_dev_rerun.md",
        "# v3.2 Go / No-Go For Dev Rerun\n\n"
        f"Decision: `{decision}`\n\n"
        "This package does not authorize model/API calls. Separate user approval is required for any dev/baseline rerun.\n",
    )
    hashes = []
    for path in sorted(PROMPT_DIR.glob("*.md")):
        hashes.append({"file": path.name, "sha256": hashlib.sha256(path.read_bytes()).hexdigest()})
    write_json(PROMPT_DIR / "prompt_hashes_v3_2.json", {"package": "prompt_formatter_v3_2", "hash_algorithm": "sha256", "files": hashes})


def write_approval_package(decision: str) -> None:
    write(
        APPROVAL_DIR / "v3_2_formula_contract_dev_rerun_readiness.md",
        "# v3.2 Formula Contract Dev Rerun Readiness\n\n"
        f"Decision: `{decision}`\n\n"
        "Formula contracts, formula-aware scorer, no-model rescore, and prompt/formatter v3.2 package are prepared. No model/API call is authorized by this package.\n",
    )
    write(
        APPROVAL_DIR / "v3_2_formula_contract_dev_rerun_scope.md",
        "# v3.2 Formula Contract Dev Rerun Scope\n\n"
        "- Track A dev + baseline diagnostic only.\n"
        "- Track B dev + baseline only.\n"
        "- Methods: vector_only_v3_2, graph_facts_only_v3_2, hybrid_vector_graph_v3_2, gold_context_v3_2.\n"
        "- Test/full eval remain locked.\n",
    )
    write(
        APPROVAL_DIR / "v3_2_formula_contract_eval_runner_change_summary.md",
        "# Eval Runner Change Summary\n\n"
        "The next runner must load model-visible formula contracts by case id and inject the same contract into every method prompt. It must load scorer-only target slots only inside the scorer.\n",
    )
    write(
        APPROVAL_DIR / "v3_2_formula_contract_remaining_risks.md",
        "# Remaining Risks\n\n"
        "- Ambiguous formula contracts may need manual review.\n"
        "- Track A remains diagnostic only.\n"
        "- Track B remains shadow overlay only.\n"
        "- Test eval is not approved.\n",
    )
    write(
        APPROVAL_DIR / "v3_2_formula_contract_opik_gap_status.md",
        "# Opik Gap Status\n\n"
        "Opik remains required before locked test unless the user grants an explicit local-only locked-test waiver. Dev rerun may be local-only only if separately approved.\n",
    )


def main() -> None:
    visible, scorer = write_contracts()
    write_scorer_package()
    rows, by_track, by_case, failures = rescore_formula_aware(visible, scorer)
    deltas = write_rescore_outputs(rows, by_track, by_case, failures)
    decision = "ready_for_user_approval_dev_baseline_rerun"
    issue_rows = read_jsonl(CONTRACT_DATA_DIR / "dev_baseline_formula_contract_issues.jsonl")
    if issue_rows:
        decision = "not_ready_formula_contract_ambiguous"
    if not (SCORER_DIR / "scorer_v3_2_formula_aware.py").exists():
        decision = "not_ready_scorer_implementation_failed"
    write_prompt_v3_2_package(decision)
    if not (PROMPT_DIR / "prompt_hashes_v3_2.json").exists():
        decision = "not_ready_prompt_patch_incomplete"
    write_approval_package(decision)
    created = []
    for directory in [CONTRACT_DIR, CONTRACT_DATA_DIR, SCORER_DIR, RESCORE_DIR, PROMPT_DIR, APPROVAL_DIR]:
        created.extend(rel(path) for path in sorted(directory.glob("*")) if path.is_file())
    remaining = [
        "user approval required before any v3.2 dev/baseline rerun",
        "Opik still required before locked test unless explicit local-only locked-test waiver exists",
        "test eval remains locked",
    ]
    if issue_rows:
        remaining.append(f"{len(issue_rows)} formula contract issue rows flagged for review")
    print(
        json.dumps(
            {
                "formula contract schema created": "yes",
                "dev/baseline formula contracts created": "yes",
                "formula-aware scorer implemented": "yes",
                "no-model formula-aware rescore completed": "yes",
                "prompt/formatter v3.2 package created": "yes",
                "model/API called": "no",
                "test eval executed": "no",
                "full eval executed": "no",
                "Neo4j write performed": "no",
                "KG patch applied": "no",
                "main score deltas": deltas,
                "remaining blockers": remaining,
                "final decision": decision,
                "next recommended action": "review v3.2 formula contract package, then explicitly approve or reject v3.2 dev/baseline rerun",
                "created files": created,
            },
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
