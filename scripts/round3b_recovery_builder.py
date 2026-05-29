"""Round 3B ticker repair + formula-contract retrofit builder.

Deterministic file construction only:
- no model/API calls
- no Opik logging
- no Neo4j access
- no KG patch
- no full/test eval
- source files are read-only
"""

from __future__ import annotations

import json
import math
import re
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "outputs" / "round3b_recovery"
CHECK = OUT / "checkpoints"
SOURCE_CASES = ROOT / "outputs" / "round3_case_factory" / "round3_selected_cases.jsonl"
SOURCE_FACTS = ROOT / "outputs" / "round3_case_factory_review" / "required_fact_semantic_issues.jsonl"

DROPPED = {
    "baseline_control_001_7b6ec08b": ("unresolvable_identity", "Long-Term Debt", "LT"),
    "round3_dev_005_25638981": ("unresolvable_identity", "NA", "NA"),
    "round3_test_002_d3d8efd4": ("unresolvable_identity", "Unknown", ""),
    "round3_test_005_4330b7f9": ("unresolvable_identity", "Unknown", ""),
}

PROCESSING = [
    "baseline_control_002_bc20b319",
    "round3_dev_001_da2a2fad",
    "round3_dev_002_1e2ee4b4",
    "round3_dev_004_11abd756",
    "round3_dev_006_8b9544ef",
    "round3_dev_008_e69805f4",
    "round3_dev_013_6c9047e6",
    "round3_dev_015_df730bd7",
    "round3_test_001_2529e04e",
    "round3_test_003_b8a1383c",
    "round3_test_006_08117364",
    "round3_test_008_19b392a0",
    "round3_test_010_6d2cfe43",
    "round3_test_015_536b783d",
    "round3_test_019_3734e04b",
    "round3_test_020_6ee222c0",
]

CORRECTIONS = {
    "round3_dev_001_da2a2fad": ("Ameren Corporation", "AEE"),
    "round3_dev_002_1e2ee4b4": ("Fastenal Company", "FAST"),
    "round3_dev_006_8b9544ef": ("ConocoPhillips", "COP"),
    "round3_dev_008_e69805f4": ("Prudential Financial, Inc.", "PRU"),
    "round3_dev_013_6c9047e6": ("Western Digital Corporation", "WDC"),
    "round3_dev_015_df730bd7": ("Broadcom Inc.", "AVGO"),
    "round3_test_003_b8a1383c": ("Cboe Global Markets, Inc.", "CBOE"),
    "round3_test_006_08117364": ("FirstEnergy Corp.", "FE"),
    "round3_test_015_536b783d": ("W. R. Berkley Corporation", "WRB"),
    "round3_test_019_3734e04b": ("Ventas, Inc.", "VTR"),
}

BAD_COMPANY = {None, "", "Unknown", "Employees", "NA", "Long-Term Debt", "Employees and Collective Bargaining Agreements", "Values, Principles and Governance"}
BAD_TICKER = {None, "", "NA", "NP", "LT", "GPM", "BRCM"}
BAD_ISSUES = {
    "synthetic_quote",
    "evidence_quote_is_synthetic_not_exact_excerpt",
    "parser_artifact",
    "non_reporting_year_used_as_period",
    "pre_2000_reporting_period",
    "metric_header_phrase",
    "negative_percentage_range_artifact",
    "usd_per_share_revenue_conflict",
    "company_ticker_issue",
    "unit_mismatch",
    "ambiguous_metric",
    "ambiguous_year",
    "derived_answer_value",
    "unit_conflicts_with_metric",
    "metric_looks_like_header_or_section_phrase",
    "year_is_before_2000_reporting_period",
    "year_looks_like_birth_maturity_or_expiration_date",
}


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


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


def norm(s: Any) -> str:
    return re.sub(r"[^a-z0-9]+", "_", str(s or "").lower()).strip("_")


def fnum(v: Any) -> float | None:
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def value_patterns(value: Any) -> list[str]:
    val = fnum(value)
    if val is None:
        return []
    patterns = set()
    raw = str(value)
    patterns.add(raw.rstrip("0").rstrip(".") if "." in raw else raw)
    abs_val = abs(val)
    if abs_val.is_integer():
        i = int(abs_val)
        patterns.add(str(i))
        patterns.add(f"{i:,}")
        patterns.add(f"({i:,})")
        patterns.add(f"({i})")
    else:
        patterns.add(str(abs_val))
        patterns.add(f"{abs_val:,.2f}".rstrip("0").rstrip("."))
    if val < 0:
        patterns |= {f"({p})" for p in list(patterns) if not p.startswith("(")}
        patterns |= {f"-{p}" for p in list(patterns) if not p.startswith("-")}
    return sorted(patterns, key=len, reverse=True)


def metric_tokens(metric: str) -> list[str]:
    stop = {"of", "the", "and", "or", "at", "in", "for", "from", "to", "a", "an"}
    return [tok for tok in norm(metric).split("_") if len(tok) > 2 and tok not in stop]


def infer_unit(case: dict[str, Any], fact: dict[str, Any]) -> str:
    text = str(case.get("evidence_text", "")).lower()
    metric = norm(fact.get("metric_canonical"))
    if "employees" in str(fact.get("unit", "")).lower() or "headcount" in metric or "employees" in metric or "personnel" in metric:
        return "count"
    if "per share" in text or "per-share" in text:
        if metric in {"revenue", "total_revenue", "net_income", "operating_income", "gross_profit", "earned_premiums"}:
            if "in thousands" in text:
                return "currency_thousands"
            if "in millions" in text or "$ in millions" in text:
                return "currency_millions"
        return "currency_per_share"
    if "in thousands" in text:
        return "currency_thousands"
    if "in millions" in text or "$ in millions" in text or "(millions" in text:
        return "currency_millions"
    return str(fact.get("unit") or "")


def find_exact_quote(case: dict[str, Any], fact: dict[str, Any]) -> tuple[str, bool, str]:
    text = str(case.get("evidence_text", ""))
    raw_quote = str(fact.get("evidence_quote") or fact.get("evidence_quote_exact") or "").strip()
    if raw_quote and raw_quote in text and "|" not in raw_quote:
        return raw_quote, True, "source_quote_exact"
    vals = value_patterns(fact.get("value"))
    tokens = metric_tokens(fact.get("metric_raw") or fact.get("metric_canonical") or "")
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    candidates = []
    for line in lines:
        low = norm(line)
        value_hit = any(p in line or p.replace(",", "") in line.replace(",", "") for p in vals)
        token_hit = sum(1 for tok in tokens if tok in low)
        if value_hit and (token_hit or not tokens):
            candidates.append((token_hit, len(line), line))
    if candidates:
        candidates.sort(key=lambda item: (-item[0], item[1]))
        return candidates[0][2], True, "line_value_metric_exact"
    for line in lines:
        value_hit = any(p in line or p.replace(",", "") in line.replace(",", "") for p in vals)
        if value_hit and str(fact.get("year") or "") and str(fact.get("year")) in line:
            return line, True, "line_value_year_exact"
    return raw_quote, False, "no_exact_substring_found"


def load_inputs() -> tuple[dict[str, dict[str, Any]], dict[str, list[dict[str, Any]]]]:
    cases = {row["case_id"]: row for row in read_jsonl(SOURCE_CASES)}
    facts: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in read_jsonl(SOURCE_FACTS):
        facts[row["case_id"]].append(row)
    return cases, facts


def phase0() -> list[dict[str, Any]]:
    rows = [{"case_id": cid, "reason": reason, "company": company, "ticker": ticker} for cid, (reason, company, ticker) in DROPPED.items()]
    write_json(OUT / "dropped_cases.json", rows)
    if len(rows) != 4:
        raise RuntimeError("Gate 0 failed: dropped_cases.json must contain exactly 4 entries")
    return rows


def phase1(cases: dict[str, dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    repaired = []
    failures = []
    for cid in PROCESSING:
        case = dict(cases.get(cid) or {})
        if not case:
            failures.append({"case_id": cid, "gate": "1", "reason": "case_missing_from_source"})
            continue
        if cid in CORRECTIONS:
            case["company"], case["ticker"] = CORRECTIONS[cid]
            case["round3b_identity_repair_applied"] = True
        else:
            case["round3b_identity_repair_applied"] = False
        reasons = []
        if case.get("company") in BAD_COMPANY:
            reasons.append("bad_company")
        if len(str(case.get("company") or "")) >= 60:
            reasons.append("company_length_ge_60")
        if case.get("ticker") in BAD_TICKER:
            reasons.append("bad_ticker")
        if len(str(case.get("evidence_text") or "")) <= 100:
            reasons.append("evidence_text_too_short")
        if reasons:
            failures.append({"case_id": cid, "gate": "1", "reason": "|".join(reasons), "company": case.get("company"), "ticker": case.get("ticker")})
        else:
            repaired.append(case)
    write_jsonl(OUT / "repaired_cases.jsonl", repaired)
    write_json(OUT / "gate1_failures.json", failures)
    write(CHECK / "gate1_report.md", "# Gate 1 Report\n\n" f"- Passing cases: {len(repaired)}\n" f"- Failures: {len(failures)}\n\n" + "\n".join(f"- `{f['case_id']}`: {f['reason']}" for f in failures) + "\n")
    if not (CHECK / "gate1_report.md").exists():
        raise RuntimeError("Gate 1 checkpoint missing")
    return repaired, failures


def phase1_5(repaired: list[dict[str, Any]], facts_by_case: dict[str, list[dict[str, Any]]]) -> tuple[dict[str, list[dict[str, Any]]], list[dict[str, Any]]]:
    pass_facts: dict[str, list[dict[str, Any]]] = {}
    failures = []
    all_facts = []
    case_map = {case["case_id"]: case for case in repaired}
    for cid, case in case_map.items():
        out_facts = []
        fact_failures = []
        for idx, fact in enumerate(facts_by_case.get(cid, []), start=1):
            issue_codes = set(fact.get("issue_codes") or [])
            quote, exact, quote_reason = find_exact_quote(case, fact)
            unit = infer_unit(case, fact)
            year = fact.get("year")
            value = fnum(fact.get("value"))
            bad_issue_without_repair = bool(issue_codes & BAD_ISSUES) and not exact
            if bad_issue_without_repair:
                fact_failures.append(f"{fact.get('fact_id')}:bad_issue_without_exact_quote")
                continue
            if value is None or fact.get("derived_answer_value"):
                fact_failures.append(f"{fact.get('fact_id')}:invalid_or_derived_value")
                continue
            if not exact:
                fact_failures.append(f"{fact.get('fact_id')}:quote_not_exact")
                continue
            role = fact.get("fact_role") or "component"
            if role in {"current_year_value"}:
                role = "component"
            out_fact = {
                "fact_id": f"{cid}_fact_{len(out_facts)+1:02d}",
                "source_fact_id": fact.get("fact_id"),
                "case_id": cid,
                "company": case.get("company"),
                "ticker": case.get("ticker"),
                "metric_raw": fact.get("metric_raw") or fact.get("metric_canonical"),
                "metric_canonical": fact.get("metric_canonical"),
                "year": year,
                "period_role": "reporting_period" if year and int(year) >= 2000 else "non_reporting_or_context_period",
                "value": value,
                "unit": unit,
                "fact_role": role,
                "evidence_quote_exact": quote,
                "quote_is_exact_excerpt": True,
                "issue_codes": sorted(issue_codes),
                "quote_recovery_method": quote_reason,
            }
            out_facts.append(out_fact)
        out_facts.extend(extract_secondary_facts(case, out_facts))
        # division-style numeric cases need at least two facts; narrative can survive with one.
        numeric_hint = any(word in str(case.get("question", "")).lower() for word in ["margin", "ratio", "growth", "rate", "turnover", "coverage", "eps", "profit"])
        if len(out_facts) < (2 if numeric_hint else 1):
            failures.append({"case_id": cid, "gate": "1.5", "reason": "insufficient_exact_required_facts", "fact_failures": fact_failures})
        else:
            pass_facts[cid] = out_facts
            all_facts.extend(out_facts)
    write_jsonl(OUT / "required_facts.jsonl", all_facts)
    write_json(OUT / "gate1_5_failures.json", failures)
    write(CHECK / "gate1_5_report.md", "# Gate 1.5 Report\n\n" f"- Passing cases: {len(pass_facts)}\n" f"- Failures: {len(failures)}\n\n" + "\n".join(f"- `{f['case_id']}`: {f['reason']}" for f in failures) + "\n")
    if not pass_facts:
        return pass_facts, failures
    return pass_facts, failures


def extract_secondary_facts(case: dict[str, Any], existing: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Recover required source facts from exact source text when review JSONL missed them."""
    text = str(case.get("evidence_text", ""))
    cid = case["case_id"]
    out: list[dict[str, Any]] = []
    existing_keys = {(f.get("metric_canonical"), f.get("year"), f.get("value")) for f in existing}

    def add(metric: str, year: int | None, value: float, unit: str, quote: str, role: str = "component") -> None:
        key = (metric, year, value)
        if key in existing_keys or quote not in text:
            return
        out.append(
            {
                "fact_id": f"{cid}_fact_{len(existing)+len(out)+1:02d}",
                "source_fact_id": "secondary_evidence_parse",
                "case_id": cid,
                "company": case.get("company"),
                "ticker": case.get("ticker"),
                "metric_raw": metric,
                "metric_canonical": metric,
                "year": year,
                "period_role": "reporting_period" if year else "context_period",
                "value": value,
                "unit": unit,
                "fact_role": role,
                "evidence_quote_exact": quote,
                "quote_is_exact_excerpt": True,
                "issue_codes": [],
                "quote_recovery_method": "secondary_direct_regex_exact",
            }
        )
        existing_keys.add(key)

    turnover = re.search(r"PPL had a turnover rate of\s+([\d.]+)%\s+for the year ended December 31,\s+(20\d{2})", text)
    if turnover:
        add("turnover_rate", int(turnover.group(2)), float(turnover.group(1)), "percentage", turnover.group(0), "numerator")
    union_pct = re.search(
        r"At the end of (20\d{2}), we employed ([\d,]+) employees worldwide\..*?Approximately\s+([\d.]+)% are represented by unions\.",
        text,
        re.S,
    )
    if union_pct:
        add("total_employees", int(union_pct.group(1)), float(union_pct.group(2).replace(",", "")), "count", union_pct.group(0), "denominator")
        add("union_representation_rate", int(union_pct.group(1)), float(union_pct.group(3)), "percentage", union_pct.group(0), "numerator")
    return out


def infer_formula(case: dict[str, Any], facts: list[dict[str, Any]]) -> dict[str, Any]:
    q = str(case.get("question", "")).lower()
    metrics = {norm(f["metric_canonical"]) for f in facts}
    years = sorted({int(f["year"]) for f in facts if isinstance(f.get("year"), int) or str(f.get("year", "")).isdigit() if int(f["year"]) >= 2000})

    def has(*needles: str) -> str | None:
        for metric in sorted(metrics):
            if any(n in metric for n in needles):
                return metric
        return None

    contract = {
        "formula_type": "ambiguous_manual_review",
        "target_formula_template": "",
        "numerator_metric_role": None,
        "denominator_metric_role": None,
        "target_years": years,
        "expected_output_type": "percentage",
        "ambiguity_flags": [],
    }
    if "convertible" in q or "notes" in q:
        contract.update(formula_type="narrative_only", target_formula_template="narrative assessment of convertible note terms", expected_output_type="narrative", target_years=[], ambiguity_flags=[])
    elif "net profit margin" in q or "np margin" in q or ("net profit" in q and "margin" in q):
        contract.update(formula_type="net_profit_margin", target_formula_template="net_income / total_revenue * 100", numerator_metric_role=has("net_income", "net_loss_income"), denominator_metric_role=has("total_revenue", "revenue"))
    elif "gpm" in q or "gross" in q:
        contract.update(formula_type="gross_profit_margin", target_formula_template="gross_profit / revenue * 100", numerator_metric_role=has("gross_profit"), denominator_metric_role=has("revenue", "net_sales"))
    elif "op" in q and "margin" in q:
        contract.update(formula_type="operating_margin", target_formula_template="operating_income / revenue * 100", numerator_metric_role=has("operating_income"), denominator_metric_role=has("revenue", "net_sales"))
    elif "earned prem" in q or "rev. growth" in q or "revenue growth" in q:
        contract.update(formula_type="revenue_growth_rate", target_formula_template="(current_revenue - prior_revenue) / prior_revenue * 100", numerator_metric_role=has("earned_premiums", "total_revenue", "revenue"), denominator_metric_role="prior_period_same_metric")
    elif "dividend" in q:
        contract.update(formula_type="dividend_growth_rate", target_formula_template="(current_dividend - prior_dividend) / prior_dividend * 100", numerator_metric_role=has("dividends_declared"), denominator_metric_role="prior_period_dividend")
    elif "eps" in q:
        contract.update(formula_type="eps_growth_rate", target_formula_template="(current_eps - prior_eps) / prior_eps * 100", numerator_metric_role=has("earnings_per_share", "eps"), denominator_metric_role="prior_period_eps")
    elif "union" in q or "collective bargaining" in q or "cba" in q:
        contract.update(formula_type="union_coverage_ratio", target_formula_template="total_employee_count * union_representation_rate", numerator_metric_role=has("union_representation_rate", "fesc", "union", "represented", "united_states"), denominator_metric_role=has("total_employees", "total", "employees"), expected_output_type="count")
    elif "turnover" in q or "attrition" in q:
        contract.update(formula_type="turnover_rate", target_formula_template="total_employee_count * turnover_rate", numerator_metric_role=has("turnover_rate", "attrition", "turnover"), denominator_metric_role=has("ppl", "employees", "total"), expected_output_type="count")
    elif "headcount" in q or "employee" in q or "workforce" in q or "sales force" in q:
        contract.update(formula_type="headcount_ratio", target_formula_template="subgroup_headcount / total_headcount * 100", numerator_metric_role=has("selling", "millennials", "generation_x", "other_international"), denominator_metric_role=has("total_personnel", "total", "employees"))

    if contract["expected_output_type"] != "narrative":
        if not contract["target_years"]:
            contract["ambiguity_flags"].append("missing_target_years")
        if not contract["numerator_metric_role"]:
            contract["role_binding_flags"] = contract.get("role_binding_flags", []) + ["missing_numerator_role_deferred_to_gate3"]
        if not contract["denominator_metric_role"]:
            contract["role_binding_flags"] = contract.get("role_binding_flags", []) + ["missing_denominator_role_deferred_to_gate3"]
    return contract


def phase2(pass_cases: dict[str, dict[str, Any]], facts: dict[str, list[dict[str, Any]]]) -> tuple[dict[str, dict[str, Any]], list[dict[str, Any]]]:
    contracts = {}
    ambiguous = []
    rows = []
    for cid, case in pass_cases.items():
        contract = infer_formula(case, facts[cid])
        row = {"case_id": cid, "split": case.get("split"), "model_visible_formula_contract": contract}
        rows.append(row)
        if contract["formula_type"] == "ambiguous_manual_review" or contract.get("ambiguity_flags"):
            ambiguous.append({"case_id": cid, "gate": "2", "formula_type": contract["formula_type"], "reason": "|".join(contract.get("ambiguity_flags") or ["ambiguous_manual_review"])})
        else:
            contracts[cid] = contract
    write_jsonl(OUT / "formula_contracts.jsonl", rows)
    write_json(OUT / "gate2_ambiguous.json", ambiguous)
    numeric = sum(1 for c in contracts.values() if c["expected_output_type"] != "narrative")
    narrative = sum(1 for c in contracts.values() if c["expected_output_type"] == "narrative")
    write(CHECK / "gate2_report.md", "# Gate 2 Report\n\n" f"- Passing contracts: {len(contracts)}\n- Numeric: {numeric}\n- Narrative-only: {narrative}\n- Ambiguous/excluded: {len(ambiguous)}\n\n" + "\n".join(f"- `{a['case_id']}`: {a['reason']}" for a in ambiguous) + "\n")
    return contracts, ambiguous


def facts_for_role(facts: list[dict[str, Any]], role: str | None) -> list[dict[str, Any]]:
    if not role:
        return []
    r = norm(role)
    if role.startswith("prior_period"):
        return []
    return [f for f in facts if r and (r in norm(f.get("metric_canonical")) or norm(f.get("metric_canonical")) in r)]


def first_facts(facts: list[dict[str, Any]], *needles: str) -> list[dict[str, Any]]:
    out = []
    for fact in facts:
        metric = norm(fact.get("metric_canonical"))
        if any(needle in metric for needle in needles):
            out.append(fact)
    return out


def largest_count_facts(facts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted([f for f in facts if str(f.get("unit")) == "count" and f.get("value")], key=lambda f: abs(float(f["value"])), reverse=True)


def phase3(contracts: dict[str, dict[str, Any]], facts: dict[str, list[dict[str, Any]]], cases: dict[str, dict[str, Any]]) -> tuple[dict[str, dict[str, Any]], list[dict[str, Any]]]:
    scorer = {}
    failures = []
    rows = []
    for cid, contract in contracts.items():
        if contract["expected_output_type"] == "narrative":
            sc = {"target_slots": [], "scorer_note": "narrative_only - no numeric target slots"}
            scorer[cid] = sc
            rows.append({"case_id": cid, "split": cases[cid].get("split"), "scorer_only_target_slot_contract": sc})
            continue
        case_facts = facts[cid]
        slots = []
        n_facts = facts_for_role(case_facts, contract["numerator_metric_role"])
        d_facts = facts_for_role(case_facts, contract["denominator_metric_role"])
        if contract["formula_type"] in {"turnover_rate", "union_coverage_ratio"}:
            slots.extend(count_from_rate_slots(contract, case_facts))
        elif contract["denominator_metric_role"] and str(contract["denominator_metric_role"]).startswith("prior_period"):
            n_by_year = {int(f["year"]): f for f in n_facts if str(f.get("year", "")).isdigit()}
            for year in sorted(n_by_year)[1:]:
                prev = max(y for y in n_by_year if y < year)
                cur_f = n_by_year[year]
                prev_f = n_by_year[prev]
                if prev_f["value"]:
                    val = (cur_f["value"] - prev_f["value"]) / abs(prev_f["value"]) * 100
                    slots.append(make_slot(f"{contract['formula_type']}_{year}_vs_{prev}", val, "percentage", contract, [cur_f, prev_f], "growth"))
        else:
            n_by_year = {int(f["year"]): f for f in n_facts if str(f.get("year", "")).isdigit()}
            d_by_year = {int(f["year"]): f for f in d_facts if str(f.get("year", "")).isdigit()}
            for year in sorted(set(n_by_year) & set(d_by_year)):
                n = n_by_year[year]
                d = d_by_year[year]
                if d["value"]:
                    val = n["value"] / d["value"]
                    unit = "ratio"
                    if contract["expected_output_type"] == "percentage":
                        val *= 100
                        unit = "percentage"
                    slots.append(make_slot(f"{contract['formula_type']}_{year}", val, unit, contract, [n, d], "division"))
        if not slots:
            failures.append({"case_id": cid, "gate": "3", "reason": "target_slot_count_zero"})
            sc = {"target_slots": []}
        else:
            sc = {"target_slots": slots}
            scorer[cid] = sc
        rows.append({"case_id": cid, "split": cases[cid].get("split"), "scorer_only_target_slot_contract": sc})
    write_jsonl(OUT / "scorer_contracts.jsonl", rows)
    write_json(OUT / "gate3_failures.json", failures)
    write(CHECK / "gate3_report.md", "# Gate 3 Report\n\n" f"- Passing numeric/narrative contracts: {len(scorer)}\n- Failures: {len(failures)}\n\n" + "\n".join(f"- `{f['case_id']}`: {f['reason']}" for f in failures) + "\n")
    return scorer, failures


def count_from_rate_slots(contract: dict[str, Any], facts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    slots = []
    if contract["formula_type"] == "turnover_rate":
        rates = first_facts(facts, "turnover_rate", "attrition")
        totals = first_facts(facts, "total_employees", "employees", "ppl")
    else:
        rates = first_facts(facts, "union_representation_rate")
        totals = first_facts(facts, "total_employees", "employees", "total")
    if not rates:
        # Some parser rows encode the rate as a small percentage-like component in a table row.
        rates = [f for f in facts if f.get("value") is not None and 0 < abs(float(f["value"])) <= 100 and str(f.get("unit")) in {"percentage", "count"}]
    if not totals:
        totals = largest_count_facts(facts)
    for rate in rates:
        rate_year = rate.get("year")
        eligible_totals = [f for f in totals if abs(float(f.get("value", 0))) > 100]
        if not eligible_totals:
            continue
        if contract["formula_type"] == "turnover_rate":
            # PPL's row contains total employees, union employees, and union percent.
            # The turnover count formula uses the total employee base, not the same-row
            # union employee count, so pick the largest plausible employee base.
            total = max(eligible_totals, key=lambda f: abs(float(f["value"])))
        else:
            same_year_totals = [f for f in eligible_totals if str(rate_year or "").isdigit() and f.get("year") == rate_year]
            total = max(same_year_totals or eligible_totals, key=lambda f: abs(float(f["value"])))
        year = rate_year if str(rate_year or "").isdigit() else total.get("year")
        if not str(year or "").isdigit():
            continue
        value = float(total["value"]) * float(rate["value"]) / 100.0
        slots.append(make_slot(f"{contract['formula_type']}_count_{year}", value, "count", contract, [total, rate], "count_from_rate"))
    return slots


def make_slot(name: str, value: float, unit: str, contract: dict[str, Any], inputs: list[dict[str, Any]], kind: str) -> dict[str, Any]:
    tol = 0.5 if unit == "percentage" else 0.005 if unit == "ratio" else 1.0
    return {
        "target_slot_name": name,
        "expected_value": round(value, 6),
        "unit": unit,
        "tolerance": tol,
        "required_for_answer": True,
        "computation_trace": {
            "slot_name": name,
            "formula": contract["target_formula_template"],
            "inputs": [{"fact_id": f["fact_id"], "role": f.get("fact_role"), "value": f["value"], "unit": f["unit"], "year": f.get("year")} for f in inputs],
            "computed_value": round(value, 6),
            "unit": unit,
            "tolerance": tol,
            "kind": kind,
        },
    }


def score_answer(answer: str, slots: list[dict[str, Any]]) -> float:
    if not answer.strip() or not slots:
        return 0.0
    score = 0
    for slot in slots:
        expected = float(slot["expected_value"])
        tol = float(slot["tolerance"])
        # Require an explicit slot/year anchor. Generic "final answer" or "result" must not bypass
        # wrong-year and wrong-denominator near-miss checks.
        name_anchor = slot["target_slot_name"].lower().replace("_", " ")
        years = {str(inp.get("year")) for inp in slot.get("computation_trace", {}).get("inputs", []) if inp.get("year") is not None}
        has_slot_anchor = slot["target_slot_name"] in answer or name_anchor in answer.lower()
        has_year_anchor = bool(years) and any(year in answer for year in years)
        if not (has_slot_anchor or has_year_anchor):
            continue
        lower_answer = answer.lower()
        unit = str(slot.get("unit", "")).lower()
        if " ratio" in lower_answer and unit != "ratio":
            continue
        if unit == "percentage" and not ("%" in lower_answer or "percentage" in lower_answer):
            continue
        if unit == "count" and (" ratio" in lower_answer or "percentage" in lower_answer or "%" in lower_answer):
            continue
        nums = [float(x.replace(",", "")) for x in re.findall(r"-?\d+(?:,\d{3})*(?:\.\d+)?", answer)]
        if any(abs(num - expected) <= tol for num in nums):
            score += 1
    return round(score / len(slots), 4)


def oracle_rows_for(cid: str, scorer: dict[str, Any]) -> tuple[list[dict[str, Any]], bool]:
    slots = scorer.get("target_slots", [])
    if not slots:
        return [], True
    correct = "final answer result " + "; ".join(f"{s['target_slot_name']} {s['expected_value']} {s['unit']}" for s in slots)
    def wrong_value(slot: dict[str, Any]) -> float:
        expected = float(slot["expected_value"])
        tol = float(slot["tolerance"])
        if abs(expected) <= max(tol * 2, 1.0):
            return expected + max(tol * 4, 5.0)
        return round(expected / 2, 6)

    wrong_formula = "final answer result " + "; ".join(f"{s['target_slot_name']} {wrong_value(s)} {s['unit']}" for s in slots)
    wrong_year = "final answer result " + "; ".join(f"{s['target_slot_name'].replace('2023','2022').replace('2024','2023').replace('2022','2021')} {s['expected_value']} {s['unit']}" for s in slots)
    wrong_denominator = "final answer result " + "; ".join(f"{s['target_slot_name']} {wrong_value(s)} {s['unit']}" for s in slots)
    unit_mismatch = "final answer result " + "; ".join(f"{s['target_slot_name']} {round(s['expected_value'] / 100, 6)} ratio" for s in slots)
    source_only = "source facts only " + "; ".join(str(inp["value"]) for s in slots for inp in s["computation_trace"]["inputs"])
    tests = [
        ("oracle_correct", correct, ">=0.8"),
        ("blank", "", "==0.0"),
        ("wrong_formula", wrong_formula, "<correct"),
        ("wrong_year", wrong_year, "<correct"),
        ("wrong_denominator", wrong_denominator, "<correct"),
        ("unit_mismatch", unit_mismatch, "<correct"),
        ("source_facts_only_no_calculation", source_only, "<correct"),
    ]
    correct_score = score_answer(correct, slots)
    rows = []
    passed = correct_score >= 0.8
    for name, answer, req in tests:
        s = score_answer(answer, slots)
        ok = s >= 0.8 if name == "oracle_correct" else s < correct_score
        rows.append({"case_id": cid, "oracle_test": name, "answer_correctness": s, "requirement": req, "passed": ok})
        passed = passed and ok
    return rows, passed


def phase4(scorer: dict[str, dict[str, Any]]) -> tuple[set[str], list[dict[str, Any]], list[dict[str, Any]]]:
    passing = set()
    failures = []
    all_rows = []
    for cid, sc in scorer.items():
        rows, ok = oracle_rows_for(cid, sc)
        all_rows.extend(rows)
        if not sc.get("target_slots"):
            passing.add(cid)  # narrative only; not numeric benchmark
        elif ok:
            passing.add(cid)
        else:
            failures.append({"case_id": cid, "gate": "4", "reason": "oracle_sanity_failed", "rows": rows})
    write_json(OUT / "gate4_failures.json", failures)
    write(CHECK / "gate4_report.md", "# Gate 4 Report\n\n" f"- Passing cases: {len(passing)}\n- Failures: {len(failures)}\n\n" + "\n".join(f"- `{f['case_id']}`: {f['reason']}" for f in failures) + "\n")
    return passing, failures, all_rows


def phase5(cases: dict[str, dict[str, Any]], facts: dict[str, list[dict[str, Any]]], contracts: dict[str, dict[str, Any]], scorer: dict[str, dict[str, Any]], oracle_pass: set[str], all_failures: dict[str, list[dict[str, Any]]]) -> dict[str, Any]:
    numeric = []
    narrative = []
    excluded = []
    for cid in PROCESSING:
        if cid not in oracle_pass or cid not in scorer or cid not in contracts:
            excluded.append({"case_id": cid, "reasons": all_failures.get(cid, [{"gate": "unknown", "reason": "not_passed"}])})
            continue
        case = dict(cases[cid])
        case["required_facts"] = facts.get(cid, [])
        case["model_visible_formula_contract"] = contracts[cid]
        case["scorer_only_target_slot_contract"] = scorer[cid]
        if contracts[cid]["expected_output_type"] == "narrative":
            narrative.append(case)
        elif scorer[cid].get("target_slots"):
            numeric.append(case)
        else:
            excluded.append({"case_id": cid, "reasons": [{"gate": "5", "reason": "numeric_without_target_slots"}]})
    dev = [c for c in numeric if c.get("split") == "round3_dev"]
    test = [c for c in numeric if c.get("split") == "round3_test"]
    baseline = [c for c in numeric if c.get("split") == "baseline_control"]
    write_jsonl(OUT / "numeric_eval_ready_cases.jsonl", numeric)
    write_jsonl(OUT / "narrative_diagnostic_cases.jsonl", narrative)
    write_json(OUT / "excluded_cases_summary.json", excluded)
    if len(test) < 4:
        write(OUT / "insufficient_test_cases.md", f"# Insufficient Test Cases\n\nRecovery test numeric cases: {len(test)}. Minimum required: 4.\nNo model calls are approved.\n")
    else:
        stale_insufficient = OUT / "insufficient_test_cases.md"
        if stale_insufficient.exists():
            stale_insufficient.unlink()
    summary = {
        "total_source_candidates": 20,
        "dropped_red": 4,
        "post_drop_candidates": 16,
        "gate1_failures": len(all_failures.get("gate1", [])),
        "gate1_5_failures": len(all_failures.get("gate1_5", [])),
        "gate2_ambiguous": len(all_failures.get("gate2", [])),
        "gate3_failures": len(all_failures.get("gate3", [])),
        "gate4_failures": len(all_failures.get("gate4", [])),
        "numeric_eval_ready": len(numeric),
        "narrative_diagnostic": len(narrative),
        "recovery_dev_count": len(dev),
        "recovery_test_count": len(test),
        "recovery_baseline_count": len(baseline),
    }
    write_json(OUT / "recovery_summary.json", summary)
    return summary


def main() -> None:
    cases, source_facts = load_inputs()
    phase0()
    repaired, g1 = phase1(cases)
    all_failures: dict[str, list[dict[str, Any]]] = defaultdict(list)
    all_failures["gate1"] = g1
    repaired_by_id = {c["case_id"]: c for c in repaired}
    if not repaired:
        raise SystemExit("Gate 1 produced zero passing cases")
    facts, g15 = phase1_5(repaired, source_facts)
    all_failures["gate1_5"] = g15
    if not facts:
        summary = phase5(repaired_by_id, {}, {}, {}, set(), all_failures)
        print_final(summary, all_failures)
        return
    pass_cases = {cid: repaired_by_id[cid] for cid in facts}
    contracts, g2 = phase2(pass_cases, facts)
    all_failures["gate2"] = g2
    if not contracts:
        summary = phase5(repaired_by_id, facts, {}, {}, set(), all_failures)
        print_final(summary, all_failures)
        return
    scorer, g3 = phase3(contracts, facts, pass_cases)
    all_failures["gate3"] = g3
    if not scorer:
        summary = phase5(repaired_by_id, facts, contracts, {}, set(), all_failures)
        print_final(summary, all_failures)
        return
    oracle_pass, g4, _oracle_rows = phase4(scorer)
    all_failures["gate4"] = g4
    summary = phase5(pass_cases, facts, contracts, scorer, oracle_pass, all_failures)
    print_final(summary, all_failures)


def print_final(summary: dict[str, Any], all_failures: dict[str, list[dict[str, Any]]]) -> None:
    lines = [
        "Round 3B Recovery Complete",
        "--------------------------",
        f"Source candidates:      {summary['total_source_candidates']}",
        f"Dropped (RED):           {summary['dropped_red']}",
        f"Post-drop candidates:   {summary['post_drop_candidates']}",
        "",
        f"Gate 1  failures:        {summary['gate1_failures']}",
        f"Gate 1.5 failures:       {summary['gate1_5_failures']}",
        f"Gate 2  ambiguous:       {summary['gate2_ambiguous']}",
        f"Gate 3  failures:        {summary['gate3_failures']}",
        f"Gate 4  failures:        {summary['gate4_failures']}",
        "",
        f"Numeric eval-ready:      {summary['numeric_eval_ready']}",
        f"  recovery_dev:          {summary['recovery_dev_count']}",
        f"  recovery_test:         {summary['recovery_test_count']}  (numeric only)",
        f"  recovery_baseline:     {summary['recovery_baseline_count']}",
        "",
        f"Narrative diagnostic:    {summary['narrative_diagnostic']}  (excluded from benchmark)",
        "",
        "Checkpoint reports: outputs/round3b_recovery/checkpoints/",
        "Full summary:       outputs/round3b_recovery/recovery_summary.json",
    ]
    affected = []
    for gate, rows in all_failures.items():
        for row in rows:
            if row.get("case_id"):
                affected.append(f"- {gate}: `{row['case_id']}` - {row.get('reason')}")
    if affected:
        lines.extend(["", "Affected case_ids:", *affected])
    print("\n".join(lines))


if __name__ == "__main__":
    main()
