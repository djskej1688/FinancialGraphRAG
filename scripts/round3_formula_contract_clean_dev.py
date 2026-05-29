"""Build clean formula-contract-ready dev/baseline subset for Round 3 v3.2."""

from __future__ import annotations

import csv
import json
import math
import re
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
RUN_DIR = ROOT / "outputs" / "round3_eval_runs" / "dev_dryrun_v3_1_20260528_005749"
CONTRACT_DIR = ROOT / "outputs" / "round3_eval_harness" / "formula_contract_v3_2"
CONTRACT_DATA = CONTRACT_DIR / "dev_baseline_contracts"
CLEAN_DIR = ROOT / "outputs" / "round3_eval_harness" / "formula_contract_v3_2_clean_dev"
RESCORE_DIR = RUN_DIR / "formula_aware_rescore_v3_2_clean_dev"
APPROVAL_DIR = ROOT / "outputs" / "round3_dual_track_eval_prep" / "dev_rerun_approval_v3_2_clean_dev"
TRACK_A = ROOT / "outputs" / "round3_dual_track_eval_prep" / "track_a_live_kg"
TRACK_B = ROOT / "outputs" / "round3_dual_track_eval_prep" / "track_b_shadow_overlay"

REQUIRED_INPUTS = [
    CONTRACT_DIR / "formula_contract_schema_v3_2.json",
    CONTRACT_DATA / "dev_baseline_formula_contract_issues.jsonl",
    CONTRACT_DATA / "dev_baseline_model_visible_formula_contracts.jsonl",
    CONTRACT_DATA / "dev_baseline_scorer_only_target_slot_contracts.jsonl",
    CONTRACT_DATA / "dev_baseline_formula_generation_trace.jsonl",
    ROOT / "outputs" / "round3_eval_harness" / "scorer_v3_2_formula_aware" / "scorer_v3_2_formula_aware.py",
    RUN_DIR / "formula_aware_rescore_v3_2" / "formula_aware_rescore_results.csv",
    ROOT / "outputs" / "round3_dual_track_eval_prep" / "prompt_formatter_v3_2" / "prompt_hashes_v3_2.json",
    ROOT / "outputs" / "round3_case_factory_repaired" / "eval_ready_required_facts.jsonl",
    TRACK_A / "live_kg_required_facts.jsonl",
    TRACK_B / "shadow_overlay_required_facts.jsonl",
    RUN_DIR / "dev_dryrun_v3_1_traces.jsonl",
    RUN_DIR / "dev_dryrun_v3_1_results.csv",
]

NUM_RE = re.compile(r"(?<![A-Za-z_])-?\(?\$?\d[\d,]*(?:\.\d+)?\)?\s*(?:%|percent|percentage|million|millions|billion|billions|thousand|thousands)?", re.I)
ID_CONTEXT_RE = re.compile(r"\b(?:round3|baseline|control|dev|test|fact|trace|case|source|evidence|prompt|sha|id)[-_A-Za-z0-9]*\b", re.I)
YEAR_RE = re.compile(r"\b20\d{2}\b")
V3_1_TO_V3_2 = {
    "vector_only_v3_1": "vector_only_v3_2",
    "graph_facts_only_v3_1": "graph_facts_only_v3_2",
    "hybrid_vector_graph_v3_1": "hybrid_vector_graph_v3_2",
    "gold_context_v3_1": "gold_context_v3_2",
}
METHODS = ["vector_only_v3_2", "graph_facts_only_v3_2", "hybrid_vector_graph_v3_2", "gold_context_v3_2"]


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows = []
    if not path.exists():
        return rows
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            rows.append(json.loads(line))
    return rows


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


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


def ensure_inputs() -> bool:
    missing = [path for path in REQUIRED_INPUTS if not path.exists()]
    if not missing:
        return True
    CLEAN_DIR.mkdir(parents=True, exist_ok=True)
    write(
        CLEAN_DIR / "BLOCKED_missing_inputs.md",
        "# Blocked: Missing Inputs\n\n" + "\n".join(f"- `{rel(path)}`" for path in missing),
    )
    return False


def load_cases() -> dict[str, dict[str, Any]]:
    cases: dict[str, dict[str, Any]] = {}
    for path in [TRACK_A / "live_kg_dev_cases.json", TRACK_A / "live_kg_baseline_cases.json", TRACK_B / "shadow_overlay_dev_cases.json", TRACK_B / "shadow_overlay_baseline_cases.json"]:
        for case in read_json(path):
            if case.get("split") != "round3_test":
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


def metric_facts(facts: list[dict[str, Any]], metric_role: str) -> list[dict[str, Any]]:
    role = metric_norm(metric_role)
    return [fact for fact in facts if role and (role in metric_norm(fact.get("metric_canonical", "")) or role in metric_norm(fact.get("metric_raw", "")))]


def values_by_year(facts: list[dict[str, Any]]) -> dict[int, float]:
    out = {}
    for fact in facts:
        if str(fact.get("year", "")).isdigit():
            out[int(fact["year"])] = fnum(fact.get("value"))
    return out


def slot(name: str, value: float, unit: str, years: list[int], forms: list[str]) -> dict[str, Any]:
    return {
        "target_slot_name": name,
        "expected_value": round(value, 6),
        "unit": unit,
        "tolerance": 0.1 if unit == "percentage" else 0.01,
        "derived_or_source": "derived",
        "required_for_answer": True,
        "acceptable_equivalent_forms": forms,
        "years": years,
    }


def source_numbers(facts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {"fact_id": fact.get("fact_id"), "metric": fact.get("metric_canonical"), "year": fact.get("year"), "value": fact.get("value"), "unit": fact.get("unit")}
        for fact in facts
    ]


def visible_contract(case_id: str, split: str, formula_type: str, template: str, numerator: str, denominator: str, years: list[int], output_type: str, comparison: str, steps: list[str]) -> dict[str, Any]:
    return {
        "case_id": case_id,
        "split": split,
        "model_visible_formula_contract": {
            "formula_type": formula_type,
            "target_formula_template": template,
            "numerator_metric_role": numerator,
            "denominator_metric_role": denominator,
            "target_years": years,
            "comparison_periods": [str(year) for year in years],
            "expected_output_type": output_type,
            "required_comparison": comparison,
            "required_steps": steps,
            "rounding_instruction": "use v3.2 rounding rules",
            "do_not_use_as_targets": ["source fact ids", "case ids", "trace ids", "citation ids", "raw source-only numbers not requested as final targets"],
        },
        "leakage_guard": {"contains_expected_numeric_final_answers": False, "contains_scorer_target_values": False, "contains_expected_answer_text": False},
    }


def scorer_contract(case_id: str, split: str, formula_type: str, slots: list[dict[str, Any]], facts: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "case_id": case_id,
        "split": split,
        "scorer_only_target_slot_contract": {
            "formula_type": formula_type,
            "target_slots": slots,
            "source_fact_numbers": source_numbers(facts),
            "non_target_numbers": ["case_id", "fact_id", "trace_id", "source_id", "prompt_hash", "metric IDs", "evidence IDs"],
            "intermediate_numbers": [],
            "final_target_numbers": [item["target_slot_name"] for item in slots],
        },
    }


def repair_case(case: dict[str, Any], facts: list[dict[str, Any]]) -> tuple[dict[str, Any] | None, dict[str, Any] | None, dict[str, Any]]:
    cid = case["case_id"]
    split = case["split"]
    q = str(case.get("question", "")).lower()
    metrics = set(facts_by_metric(facts))
    years = sorted({int(f["year"]) for f in facts if str(f.get("year", "")).isdigit()})
    reason = ""
    risk = "medium"
    # Safe repairs only where question intent and required facts jointly define a formula.
    if "distribution" in q and "employee" in q and {"non_u_s", "total"}.issubset(metrics):
        non_us = values_by_year(metric_facts(facts, "non_u_s"))
        total = values_by_year(metric_facts(facts, "total"))
        slots = [slot(f"non_us_employee_distribution_{year}", non_us[year] / total[year] * 100.0, "percentage", [year], ["percent", "ratio_decimal"]) for year in sorted(set(non_us) & set(total)) if total[year]]
        reason = "Question asks employee distribution and source facts provide non_u_s and total."
        visible = visible_contract(cid, split, "workforce_ratio", "non_u_s / total * 100", "non_u_s", "total", years, "percentage", "compare US/non-US employee distribution across periods", ["identify non-US and total employees", "compute non-US / total * 100", "compare periods"])
        return visible, scorer_contract(cid, split, "workforce_ratio", slots, facts), trace(cid, split, "repair_contract", reason, risk, slots)
    if "net inc" in q and "growth" in q and "eps" in q:
        net = values_by_year(metric_facts(facts, "net_income"))
        eps = values_by_year(metric_facts(facts, "diluted_earnings_per_common_share"))
        slots = []
        if 2022 in net and 2023 in net and net[2022]:
            slots.append(slot("net_income_growth_2023_vs_2022", (net[2023] - net[2022]) / abs(net[2022]) * 100.0, "percentage", [2023, 2022], ["percent"]))
        if 2022 in eps and 2023 in eps:
            slots.append(slot("diluted_eps_change_2023_vs_2022", eps[2023] - eps[2022], "amount", [2023, 2022], ["per_share", "amount"]))
        if slots:
            reason = "Question explicitly asks net income growth and diluted EPS impact; source facts provide net income and diluted EPS."
            visible = visible_contract(cid, split, "growth_and_eps_change", "(current_net_income - prior_net_income) / prior_net_income * 100; current_diluted_eps - prior_diluted_eps", "net_income", "prior_period_net_income", years, "percentage_and_amount", "compare 2023 vs 2022 profitability and EPS impact", ["compute net income growth", "compute diluted EPS change", "interpret profitability impact"])
            return visible, scorer_contract(cid, split, "growth_and_eps_change", slots, facts), trace(cid, split, "repair_contract", reason, risk, slots)
    if "noninterest exp ratio" in q and "total_noninterest_expense" in metrics and "net_interest_income" in metrics:
        num = values_by_year(metric_facts(facts, "total_noninterest_expense"))
        den = values_by_year(metric_facts(facts, "net_interest_income"))
        slots = [slot(f"noninterest_expense_ratio_{year}", abs(num[year]) / abs(den[year]) * 100.0, "percentage", [year], ["percent"]) for year in sorted(set(num) & set(den)) if den[year]]
        if slots:
            reason = "Question asks noninterest expense ratio; source facts provide total noninterest expense and net interest income."
            visible = visible_contract(cid, split, "noninterest_expense_ratio", "total_noninterest_expense / net_interest_income * 100", "total_noninterest_expense", "net_interest_income", years, "percentage", "compare noninterest expense ratio over available periods", ["identify total noninterest expense", "identify net interest income", "compute ratio", "interpret margin impact"])
            return visible, scorer_contract(cid, split, "noninterest_expense_ratio", slots, facts), trace(cid, split, "repair_contract", reason, risk, slots)
    if "operating profit margin" in q and "income_before_taxes" in metrics and "net_interest_income" in metrics and "total_other_income" in metrics:
        ibt = values_by_year(metric_facts(facts, "income_before_taxes"))
        nii = values_by_year(metric_facts(facts, "net_interest_income"))
        other = values_by_year(metric_facts(facts, "total_other_income"))
        slots = []
        for year in sorted(set(ibt) & set(nii) & set(other)):
            denom = nii[year] + other[year]
            if denom:
                slots.append(slot(f"ibt_to_net_interest_plus_other_income_margin_{year}", ibt[year] / denom * 100.0, "percentage", [year], ["percent"]))
        if slots:
            reason = "Question explicitly defines operating profit margin as IBT vs net interest plus other income."
            visible = visible_contract(cid, split, "bank_operating_profit_margin", "income_before_taxes / (net_interest_income + total_other_income) * 100", "income_before_taxes", "net_interest_income + total_other_income", years, "percentage", "compare bank operating profit margin over available periods", ["identify IBT", "sum net interest income and total other income", "compute margin", "compare periods"])
            return visible, scorer_contract(cid, split, "bank_operating_profit_margin", slots, facts), trace(cid, split, "repair_contract", reason, risk, slots)
    return None, None, trace(cid, split, "exclude_from_dev_rerun", "Strict repair eligibility failed; formula target or source fact support remains ambiguous.", "high", [])


def trace(case_id: str, split: str, action: str, reason: str, risk: str, slots: list[dict[str, Any]]) -> dict[str, Any]:
    return {"case_id": case_id, "split": split, "proposed_action": action, "repair_reason": reason, "risk_level": risk, "target_slot_count": len(slots), "confidence": "high" if action == "repair_contract" else "low"}


def issue_types_for(issue: dict[str, Any], facts: list[dict[str, Any]]) -> list[str]:
    text = " ".join(issue.get("issues", [])).lower()
    types = []
    if "no deterministic formula" in text:
        types.extend(["missing_formula_type", "ambiguous_formula_type", "question_intent_ambiguous"])
    if "missing numerator" in text:
        types.append("missing_numerator_role")
    if "denominator" in text:
        types.append("missing_denominator_role")
    if "no final target slots" in text:
        types.extend(["scorer_slot_generation_error", "source_fact_roles_insufficient"])
    if len({metric_norm(f.get("metric_canonical", "")) for f in facts}) < 2:
        types.append("source_facts_do_not_support_expected_answer")
    if not types:
        types.append("needs_manual_review")
    return sorted(set(types))


def summarize_facts(facts: list[dict[str, Any]]) -> str:
    pairs = sorted({f"{fact.get('metric_canonical')}:{fact.get('role')}:{fact.get('year')}" for fact in facts})
    return "; ".join(pairs[:30])


def expected_summary(case: dict[str, Any]) -> str:
    return str(case.get("expected_answer", "")).replace("\n", " ")[:500]


def build_clean_subset() -> dict[str, Any]:
    cases = load_cases()
    facts = load_facts(set(cases))
    visible_rows = {row["case_id"]: row for row in read_jsonl(CONTRACT_DATA / "dev_baseline_model_visible_formula_contracts.jsonl")}
    scorer_rows = {row["case_id"]: row for row in read_jsonl(CONTRACT_DATA / "dev_baseline_scorer_only_target_slot_contracts.jsonl")}
    issues = {row["case_id"]: row for row in read_jsonl(CONTRACT_DATA / "dev_baseline_formula_contract_issues.jsonl")}
    clean_visible = []
    clean_scorer = []
    generation_trace = []
    repaired = []
    excluded = []
    held = []
    triage = []
    for cid, case in sorted(cases.items()):
        if case.get("split") == "round3_test":
            continue
        case_facts = facts.get(cid, [])
        if cid not in issues:
            scorer = scorer_rows.get(cid)
            if scorer and scorer["scorer_only_target_slot_contract"].get("target_slots"):
                clean_visible.append(visible_rows[cid])
                clean_scorer.append(scorer)
                generation_trace.append({"case_id": cid, "split": case["split"], "proposed_action": "keep_unambiguous", "risk_level": "low", "target_slot_count": len(scorer["scorer_only_target_slot_contract"]["target_slots"])})
            continue
        repaired_visible, repaired_scorer, repair_trace = repair_case(case, case_facts)
        current_visible = visible_rows.get(cid, {})
        current_scorer = scorer_rows.get(cid, {})
        itypes = issue_types_for(issues[cid], case_facts)
        action = repair_trace["proposed_action"]
        if action == "repair_contract" and repaired_visible and repaired_scorer:
            clean_visible.append(repaired_visible)
            clean_scorer.append(repaired_scorer)
            generation_trace.append({**repair_trace, "formula_generation_trace": "safe deterministic repair from question intent and source facts"})
            repaired.append({"case_id": cid, "track": track_for_case(cid), "split": case["split"], "question": case.get("question"), "repaired_model_visible_formula_contract": repaired_visible["model_visible_formula_contract"], "repaired_scorer_only_target_slot_contract": repaired_scorer["scorer_only_target_slot_contract"], **repair_trace, "leakage_check_result": "pass"})
        else:
            excluded.append({"case_id": cid, "track": track_for_case(cid), "split": case["split"], "question": case.get("question"), "reason": repair_trace["repair_reason"], "risk_level": "high", "issue_types": itypes})
        triage.append(
            {
                "case_id": cid,
                "track": track_for_case(cid),
                "split": case["split"],
                "question": case.get("question", ""),
                "expected_answer_summary": expected_summary(case),
                "required_source_facts_summary": summarize_facts(case_facts),
                "current_model_visible_formula_contract": json.dumps(current_visible.get("model_visible_formula_contract", {}), ensure_ascii=False),
                "current_scorer_only_target_slot_contract": json.dumps(current_scorer.get("scorer_only_target_slot_contract", {}), ensure_ascii=False),
                "issue_types": ";".join(itypes),
                "proposed_action": action,
                "reason": repair_trace["repair_reason"],
                "risk_level": repair_trace["risk_level"],
            }
        )
    return {
        "visible": clean_visible,
        "scorer": clean_scorer,
        "trace": generation_trace,
        "repaired": repaired,
        "excluded": excluded,
        "held": held,
        "triage": triage,
    }


def track_for_case(case_id: str) -> str:
    if case_id.startswith("baseline_control"):
        return "track_b_shadow_overlay"
    track_a_cases = {case["case_id"] for p in [TRACK_A / "live_kg_dev_cases.json", TRACK_A / "live_kg_baseline_cases.json"] for case in read_json(p)}
    return "track_a_live_kg_diagnostic" if case_id in track_a_cases else "track_b_shadow_overlay"


def output_text(trace: dict[str, Any]) -> str:
    result = trace.get("method_result") or {}
    raw = trace.get("raw_method_result_v3_1") or {}
    return "\n".join([str(result.get("final_answer", "")), str(result.get("calculation", "")), str(raw.get("brief_interpretation", "")), str(raw.get("rounding_statement", ""))])


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


def rescore_clean(clean_scorer: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    clean_ids = {row["case_id"] for row in clean_scorer}
    scorer_by_case = {row["case_id"]: row["scorer_only_target_slot_contract"] for row in clean_scorer}
    facts = load_facts(clean_ids)
    traces = [row for row in read_jsonl(RUN_DIR / "dev_dryrun_v3_1_traces.jsonl") if row["case_id"] in clean_ids and row["split"] != "round3_test"]
    rows = []
    for tr in traces:
        slots = scorer_by_case[tr["case_id"]]["target_slots"]
        out = output_text(tr)
        slot_score = score_slots(out, slots)
        previous_rfr = fnum(tr.get("required_fact_recall", 0))
        graph_recall = previous_rfr if tr["method"] in {"graph_facts_only_v3_1", "hybrid_vector_graph_v3_1"} else 0.0
        text_recall = value_recall(facts.get(tr["case_id"], []), tr.get("user_prompt", ""))
        answer_recall = value_recall(facts.get(tr["case_id"], []), out)
        rfr = max(graph_recall, answer_recall) if tr["method"] in {"graph_facts_only_v3_1", "hybrid_vector_graph_v3_1"} else max(text_recall, answer_recall)
        fmt = fnum(tr.get("answer_format_compliance", 0)) >= 1
        calc = fnum(tr.get("calculation_completeness", 0)) >= 1
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
                "track": track_for_case(tr["case_id"]),
                "split": tr["split"],
                "case_id": tr["case_id"],
                "method": V3_1_TO_V3_2[tr["method"]],
                "source_method_v3_1": tr["method"],
                "trace_id": tr["trace_id"],
                "formula_type": scorer_by_case[tr["case_id"]]["formula_type"],
                "target_slot_count": len(slots),
                "matched_target_slot_count": len(slot_score["matched"]),
                "target_numeric_recall": slot_score["numeric_recall"],
                "numeric_correctness": 1.0 if slot_score["numeric_correct"] else 0.0,
                "required_fact_recall": rfr,
                "answer_correctness": 1.0 if answer_ok else 0.0,
                "faithfulness": 1.0 if faithful else 0.0,
                "calculation_completeness": 1.0 if calc else 0.0,
                "answer_format_compliance": 1.0 if fmt else 0.0,
                "v3_1_failure_reason": tr.get("failure_reason", ""),
                "failure_reason": failure,
                "matched_target_slots": ";".join(slot_score["matched"]),
                "missing_target_slots": ";".join(slot_score["missing"]),
            }
        )
    return rows, summarize(rows, ["track", "method"]), summarize(rows, ["track", "split", "method"]), summarize(rows, ["track", "split", "case_id"]), [r for r in rows if r["failure_reason"] != "none"]


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
                "avg_required_fact_recall": avg([r["required_fact_recall"] for r in items]),
                "avg_target_numeric_recall": avg([r["target_numeric_recall"] for r in items]),
                "avg_numeric_correctness": avg([r["numeric_correctness"] for r in items]),
                "avg_answer_correctness": avg([r["answer_correctness"] for r in items]),
                "avg_faithfulness": avg([r["faithfulness"] for r in items]),
                "avg_calculation_completeness": avg([r["calculation_completeness"] for r in items]),
                "avg_answer_format_compliance": avg([r["answer_format_compliance"] for r in items]),
            }
        )
        out.append(base)
    return out


def avg(vals: list[Any]) -> float:
    nums = [fnum(v) for v in vals]
    return round(sum(nums) / len(nums), 4) if nums else 0.0


def rescore_fields() -> list[str]:
    return ["track", "split", "case_id", "method", "source_method_v3_1", "trace_id", "formula_type", "target_slot_count", "matched_target_slot_count", "target_numeric_recall", "numeric_correctness", "required_fact_recall", "answer_correctness", "faithfulness", "calculation_completeness", "answer_format_compliance", "v3_1_failure_reason", "failure_reason", "matched_target_slots", "missing_target_slots"]


def write_outputs(data: dict[str, Any]) -> tuple[str, list[str], list[str]]:
    clean_ids = {row["case_id"] for row in data["visible"]}
    track_a = sorted(cid for cid in clean_ids if track_for_case(cid) == "track_a_live_kg_diagnostic")
    track_b = sorted(cid for cid in clean_ids if track_for_case(cid) == "track_b_shadow_overlay")
    baseline = sorted(cid for cid in clean_ids if cid.startswith("baseline_control"))
    decision = decide(len(clean_ids), len(track_a), len(track_b), len(baseline), len(data["excluded"]), len(data["held"]))
    write_jsonl(CLEAN_DIR / "clean_dev_model_visible_formula_contracts.jsonl", data["visible"])
    write_jsonl(CLEAN_DIR / "clean_dev_scorer_only_target_slot_contracts.jsonl", data["scorer"])
    write_jsonl(CLEAN_DIR / "clean_dev_formula_generation_trace.jsonl", data["trace"])
    write_jsonl(CLEAN_DIR / "repaired_formula_contracts.jsonl", data["repaired"])
    write_jsonl(CLEAN_DIR / "excluded_formula_contracts.jsonl", data["excluded"])
    write_jsonl(CLEAN_DIR / "held_for_manual_review_formula_contracts.jsonl", data["held"])
    write_csv(CLEAN_DIR / "formula_contract_issue_triage.csv", data["triage"], ["case_id", "track", "split", "question", "expected_answer_summary", "required_source_facts_summary", "current_model_visible_formula_contract", "current_scorer_only_target_slot_contract", "issue_types", "proposed_action", "reason", "risk_level"])
    write(
        CLEAN_DIR / "formula_contract_manual_review_packet.md",
        "# Formula Contract Manual Review Packet\n\n"
        f"- Held for manual review: {len(data['held'])}\n"
        f"- Excluded from dev rerun: {len(data['excluded'])}\n\n"
        "No held cases are included in the clean dev rerun scope.\n",
    )
    write(
        CLEAN_DIR / "formula_contract_leakage_check.md",
        "# Formula Contract Leakage Check\n\n"
        "- Model-visible contracts contain no scorer target slot values by construction for repaired contracts.\n"
        "- Scorer-only target slots are stored separately.\n"
        "- Test split rows included: 0.\n",
    )
    write(
        CLEAN_DIR / "clean_dev_formula_contract_summary.md",
        "# Clean Dev Formula Contract Summary\n\n"
        f"- Clean dev/baseline cases: {len(clean_ids)}\n"
        f"- Clean Track A cases: {len(track_a)}\n"
        f"- Clean Track B cases: {len(track_b)}\n"
        f"- Baseline cases: {len(baseline)}\n"
        f"- Safely repaired contracts: {len(data['repaired'])}\n"
        f"- Excluded contracts: {len(data['excluded'])}\n"
        f"- Held for manual review: {len(data['held'])}\n"
        f"- Decision: `{decision}`\n",
    )
    write(
        CLEAN_DIR / "clean_dev_go_no_go_for_v3_2_rerun.md",
        "# Clean Dev Go / No-Go For v3.2 Rerun\n\n"
        f"Decision: `{decision}`\n\n"
        "This package does not approve model/API calls. Test/full eval remain locked.\n",
    )
    rows, by_track, by_split, by_case, failures = rescore_clean(data["scorer"])
    write_csv(RESCORE_DIR / "clean_dev_rescore_results.csv", rows, rescore_fields())
    write_csv(RESCORE_DIR / "clean_dev_method_summary_by_track.csv", by_track, list(by_track[0].keys()) if by_track else [])
    write_csv(RESCORE_DIR / "clean_dev_method_summary_by_split.csv", by_split, list(by_split[0].keys()) if by_split else [])
    write_csv(RESCORE_DIR / "clean_dev_case_level_scores.csv", by_case, list(by_case[0].keys()) if by_case else [])
    write_jsonl(RESCORE_DIR / "clean_dev_failure_analysis.jsonl", failures)
    write_rescore_reports(rows, by_track)
    write_approval_package(decision, sorted(clean_ids), track_a, track_b, baseline)
    created = []
    for d in [CLEAN_DIR, RESCORE_DIR, APPROVAL_DIR]:
        created.extend(rel(p) for p in sorted(d.glob("*")) if p.is_file())
    return decision, created, [f"{r['track']} / {r['method']}: answer={r['avg_answer_correctness']}, numeric={r['avg_numeric_correctness']}, rfr={r['avg_required_fact_recall']}" for r in by_track]


def decide(clean_n: int, track_a: int, track_b: int, baseline: int, excluded: int, held: int) -> str:
    if clean_n >= 10 and track_b >= 6 and baseline >= 2 and excluded == 0 and held == 0:
        return "ready_for_user_approval_v3_2_clean_dev_rerun"
    if clean_n >= 8 and track_b >= 5 and baseline >= 2:
        return "ready_for_user_approval_v3_2_clean_dev_rerun_with_exclusions"
    if clean_n < 8:
        return "not_ready_too_few_clean_cases"
    if held:
        return "not_ready_needs_manual_formula_review"
    return "no_go_round3_formula_contract_unstable"


def write_rescore_reports(rows: list[dict[str, Any]], by_track: list[dict[str, Any]]) -> None:
    write(
        RESCORE_DIR / "clean_dev_rescore_report.md",
        "# Clean Dev Formula-Aware Rescore\n\n"
        "- Model/API called: no\n"
        "- Test eval executed: no\n"
        "- Full eval executed: no\n"
        "- Track A and Track B are reported separately.\n"
        "- Track B is shadow overlay only, not live Neo4j KG.\n\n"
        "## Method Summary\n\n"
        + "\n".join(f"- {r['track']} / {r['method']}: answer={r['avg_answer_correctness']}, numeric={r['avg_numeric_correctness']}, rfr={r['avg_required_fact_recall']}" for r in by_track),
    )
    write(
        RESCORE_DIR / "clean_dev_score_delta_vs_v3_1.md",
        "# Clean Dev Score Delta vs v3.1\n\n"
        "Clean subset excludes ambiguous formula contracts; compare only within this scoped rerun package. Do not compare as a full benchmark claim.\n",
    )
    counts = Counter(r["failure_reason"] for r in rows)
    write(RESCORE_DIR / "clean_dev_remaining_failure_modes.md", "# Clean Dev Remaining Failure Modes\n\n" + "\n".join(f"- {k}: {v}" for k, v in counts.most_common()))


def write_approval_package(decision: str, case_ids: list[str], track_a: list[str], track_b: list[str], baseline: list[str]) -> None:
    write(
        APPROVAL_DIR / "v3_2_clean_dev_rerun_scope.md",
        "# v3.2 Clean Dev Rerun Scope\n\n"
        f"Decision: `{decision}`\n\n"
        "- Model/API calls are not yet approved.\n- Test evaluation is not approved.\n- Full evaluation is not approved.\n- Track A remains live KG diagnostic only.\n- Track B remains shadow overlay only.\n",
    )
    write_json(APPROVAL_DIR / "v3_2_clean_dev_rerun_case_list.json", {"case_ids": case_ids, "track_a_cases": track_a, "track_b_cases": track_b, "baseline_cases": baseline})
    matrix = [{"case_id": cid, "method": method, "included": True} for cid in case_ids for method in METHODS]
    write_csv(APPROVAL_DIR / "v3_2_clean_dev_rerun_method_matrix.csv", matrix, ["case_id", "method", "included"])
    write(APPROVAL_DIR / "v3_2_clean_dev_expected_improvements.md", "# Expected Improvements\n\nFormula target contracts should reduce formula-choice ambiguity and keep scorer target slots separate from model-visible prompt content.\n")
    write(APPROVAL_DIR / "v3_2_clean_dev_remaining_risks.md", "# Remaining Risks\n\n- Excluded cases reduce coverage.\n- Track A remains diagnostic only.\n- Track B remains shadow overlay only.\n")
    write(APPROVAL_DIR / "v3_2_clean_dev_opik_gap_status.md", "# Opik Gap Status\n\nOpik is still required before locked test unless user grants a local-only locked-test waiver. Dev rerun may be local-only only if separately approved.\n")
    write(APPROVAL_DIR / "v3_2_clean_dev_user_approval_template.md", "# User Approval Template\n\nApprove v3.2 clean dev/baseline rerun only: yes/no\n\nAllow model/API calls for clean dev/baseline only: yes/no\n\nAllow Opik logging if configured: yes/no\n\nAllow test eval: no\nAllow full eval: no\n")
    write(APPROVAL_DIR / "v3_2_clean_dev_claim_boundary.md", "# Claim Boundary\n\nTrack A is live KG diagnostic only. Track B is shadow overlay only. Do not merge Track A and Track B into one headline number. Test/full eval remain locked.\n")


def main() -> None:
    if not ensure_inputs():
        print(json.dumps({"ambiguous formula contracts triaged": "no", "blocker": "missing inputs"}, ensure_ascii=False, indent=2))
        return
    data = build_clean_subset()
    decision, created, summary = write_outputs(data)
    clean_ids = {row["case_id"] for row in data["visible"]}
    track_a = [cid for cid in clean_ids if track_for_case(cid) == "track_a_live_kg_diagnostic"]
    track_b = [cid for cid in clean_ids if track_for_case(cid) == "track_b_shadow_overlay"]
    print(
        json.dumps(
            {
                "ambiguous formula contracts triaged": "yes",
                "safely repaired formula contracts": len(data["repaired"]),
                "excluded formula contracts": len(data["excluded"]),
                "held for manual review": len(data["held"]),
                "clean dev/baseline cases": len(clean_ids),
                "clean Track A cases": len(track_a),
                "clean Track B cases": len(track_b),
                "clean scorer rescore completed": "yes",
                "model/API called": "no",
                "test eval executed": "no",
                "full eval executed": "no",
                "Neo4j write performed": "no",
                "KG patch applied": "no",
                "final decision": decision,
                "next recommended action": "review clean dev subset approval package; if accepted, explicitly approve v3.2 clean dev/baseline rerun only",
                "created files": created,
            },
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
