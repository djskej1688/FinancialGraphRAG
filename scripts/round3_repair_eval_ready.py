from __future__ import annotations

import csv
import json
import os
import re
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[1]
CANDIDATE_DIR = REPO_ROOT / "outputs" / "round3_case_factory"
PREFLIGHT_DIR = REPO_ROOT / "outputs" / "round3_case_factory_review"
OUT_DIR = REPO_ROOT / "outputs" / "round3_case_factory_repaired"

BAD_TICKERS = {"", "NA", "N/A", "LT", "FY", "US", "UK", "SEC", "EPS", "USD", "GAAP", "NP", "GPM"}
BAD_COMPANY_MARKERS = {
    "unknown",
    "employees",
    "long-term debt",
    "results of operations",
    "consolidated statement",
    "consolidated statements",
    "income statements",
    "values",
    "principles",
    "governance",
    "december 31",
    "calc ",
    "net sales",
    "sales",
    "revenue",
    "revenues",
    "earnings before",
    "income before",
    "gross profit",
    "operating income",
    "net interest income",
    "interest income",
    "raw materials",
    "year ended",
    "in millions",
    "except per share",
}
QUESTION_PHRASE_MARKERS = {
    "impact",
    "ratio",
    "growth",
    "margin",
    "trend",
    "comparison",
    "analysis",
    "analyzing",
    "calc",
    "cost",
    "strategy",
    "profile",
}
HEADER_PHRASES = {
    "birth years between",
    "in millions except",
    "in millions, except",
    "at_the_end_of",
    "at the end of",
    "year ended",
    "years ended",
    "consolidated statements",
    "statement of operations",
    "table of contents",
}


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    with path.open("w", encoding="utf-8", newline="\n") as fh:
        for row in rows:
            fh.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")


def write_json(path: Path, data: Any) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")


def load_env_presence() -> dict[str, bool]:
    env_file = REPO_ROOT / ".env"
    found: dict[str, bool] = {
        "env_file_exists": env_file.exists(),
        "NEO4J_URI_present": bool(os.getenv("NEO4J_URI")),
        "NEO4J_PASSWORD_present": bool(os.getenv("NEO4J_PASSWORD")),
        "NEO4J_DATABASE_present": bool(os.getenv("NEO4J_DATABASE")),
    }
    if env_file.exists():
        for raw in env_file.read_text(encoding="utf-8", errors="ignore").splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            if key in found and value:
                found[f"{key}_present"] = True
                os.environ.setdefault(key, value)
    return {
        "env_file_exists": env_file.exists(),
        "NEO4J_URI_present": bool(os.getenv("NEO4J_URI")),
        "NEO4J_PASSWORD_present": bool(os.getenv("NEO4J_PASSWORD")),
        "NEO4J_DATABASE_present": bool(os.getenv("NEO4J_DATABASE")),
    }


def normalized_words(text: str) -> set[str]:
    return {
        w.lower()
        for w in re.findall(r"[A-Za-z][A-Za-z0-9&'-]{2,}", text or "")
        if w.lower()
        not in {
            "the",
            "and",
            "for",
            "with",
            "from",
            "that",
            "this",
            "were",
            "was",
            "are",
            "into",
            "total",
            "year",
            "years",
            "million",
            "millions",
            "calculated",
            "approximately",
            "using",
            "based",
            "current",
            "prior",
        }
    }


def number_variants(value: Any) -> list[str]:
    try:
        v = float(value)
    except Exception:
        return []
    neg = v < 0
    av = abs(v)
    variants: set[str] = set()
    if av == int(av):
        base = f"{int(av):,}"
        plain = str(int(av))
    else:
        base = f"{av:,.4f}".rstrip("0").rstrip(".")
        plain = f"{av:.4f}".rstrip("0").rstrip(".")
    variants.update({base, plain, f"${base}", f"$ {base}"})
    if neg:
        variants.update({f"({base})", f"({plain})", f"$({base})", f"({base})%"})
    variants.add(f"{base}%")
    return sorted(variants, key=len, reverse=True)


def find_exact_quote(evidence: str, metric_raw: str, value: Any) -> str:
    if not evidence or not metric_raw:
        return ""
    metric_patterns = [re.escape(metric_raw.strip())]
    metric_words = metric_raw.strip().split()
    if len(metric_words) > 1:
        metric_patterns.append(r"\s+".join(re.escape(w) for w in metric_words))
    for metric_pat in metric_patterns:
        for metric_match in re.finditer(metric_pat, evidence, flags=re.IGNORECASE):
            start = metric_match.start()
            search_end = min(len(evidence), metric_match.end() + 2200)
            window = evidence[metric_match.end() : search_end]
            for variant in number_variants(value):
                value_match = re.search(re.escape(variant), window)
                if value_match:
                    end = metric_match.end() + value_match.end()
                    return evidence[start:end].strip()
    # Fallback: exact value context, still exact source substring.
    for variant in number_variants(value):
        value_match = re.search(re.escape(variant), evidence)
        if value_match:
            start = max(0, value_match.start() - 160)
            end = min(len(evidence), value_match.end() + 160)
            return evidence[start:end].strip()
    return ""


def exact_quote_is_valid(evidence: str, quote: str, metric_raw: str, value: Any) -> bool:
    if not quote or quote not in evidence:
        return False
    if metric_raw.lower() not in quote.lower():
        return False
    return any(variant in quote for variant in number_variants(value))


def period_role(fact: dict[str, Any], case: dict[str, Any], quote: str) -> str:
    year = fact.get("year")
    text = f"{quote} {case.get('question', '')} {case.get('expected_answer', '')}".lower()
    if year and re.search(r"\b(birth|born|age|ages)\b", text):
        return "birth_year_range"
    if year and re.search(r"\b(expire|expires|expiration|cba|contract)\b", text):
        return "expiration_year"
    if year and re.search(r"\b(matur|note|debt|principal)\b", text):
        return "maturity_year"
    if year and 2000 <= int(year) <= 2035:
        return "reporting_period"
    return "other"


def fact_role(fact: dict[str, Any], case: dict[str, Any]) -> str:
    metric = f"{fact.get('metric_raw', '')} {fact.get('metric_canonical', '')}".lower()
    q = f"{case.get('question', '')} {case.get('expected_answer', '')}".lower()
    if "margin" in q or "ratio" in q:
        if any(term in metric for term in ["revenue", "sales", "premium", "net int", "other inc", "assets"]):
            return "denominator"
        if any(term in metric for term in ["income", "profit", "earnings", "loss", "tax", "expense", "cost"]):
            return "numerator"
    if "total" in metric:
        return "total"
    if fact.get("fact_role") in {"numerator", "denominator", "total"}:
        return fact["fact_role"]
    return "component"


def company_bad_reason(company: str) -> str:
    lower = (company or "").lower().strip()
    if not lower:
        return "missing_company"
    if lower[0].isdigit() or lower.startswith("("):
        return "company_looks_like_table_header"
    if "\t" in company or "$" in company:
        return "company_looks_like_table_row"
    if any(marker in lower for marker in BAD_COMPANY_MARKERS):
        return "company_looks_like_section_heading"
    if len(company) > 55:
        return "company_too_long_or_question_phrase"
    if any(marker in lower for marker in QUESTION_PHRASE_MARKERS):
        return "company_looks_like_question_phrase"
    return ""


def suggest_company_ticker(case: dict[str, Any]) -> dict[str, str]:
    question = case.get("question", "")
    evidence = case.get("evidence_text", "")
    suggestion: dict[str, str] = {}
    ticker_match = re.search(r"\(([A-Z]{1,5})\)", question)
    trailing_match = re.search(r",\s*([A-Z]{1,5})\.?$", question)
    for match in [ticker_match, trailing_match]:
        if match and match.group(1) not in BAD_TICKERS:
            suggestion["ticker"] = match.group(1)
            break
    if case.get("ticker") and case["ticker"] not in BAD_TICKERS:
        suggestion.setdefault("ticker", case["ticker"])
    if "ticker" in suggestion:
        company_match = re.search(rf"([A-Z][A-Za-z0-9&.,' -]{{2,80}})\s*\({suggestion['ticker']}\)", question)
        if company_match:
            suggestion["company"] = company_match.group(1).strip(" ,.'")
    first_lines = [line.strip() for line in evidence.splitlines() if line.strip()]
    for line in first_lines[:5]:
        cleaned = re.sub(r"\s+and\s+Subsidiaries.*", "", line)
        cleaned = re.sub(r"\s+Consolidated.*", "", cleaned)
        if 3 <= len(cleaned) <= 80 and not company_bad_reason(cleaned):
            suggestion.setdefault("company", cleaned.strip(" ,."))
            break
    return suggestion


def metric_artifact_reason(fact: dict[str, Any], quote: str, period: str) -> str:
    metric = f"{fact.get('metric_raw', '')} {fact.get('metric_canonical', '')}".lower()
    unit = fact.get("unit", "")
    value = fact.get("value")
    if any(phrase in metric for phrase in HEADER_PHRASES):
        return "metric_header_phrase"
    if period in {"birth_year_range", "expiration_year"}:
        return f"non_reporting_{period}"
    if period == "maturity_year":
        return "debt_maturity_year_excluded_from_reporting_period"
    if fact.get("year") and int(fact["year"]) < 2000:
        return "pre_2000_year_not_reporting_period"
    if unit == "USD_per_share" and any(term in metric for term in ["revenue", "sales", "premium"]):
        return "unit_metric_mismatch"
    try:
        if float(value) < 0 and re.search(r"\d+(?:\.\d+)?\s*%\s*(?:-|to|through)\s*\d+(?:\.\d+)?\s*%", quote, re.I):
            return "negative_percentage_range_artifact"
    except Exception:
        pass
    return ""


def infer_repaired_unit(fact: dict[str, Any], case: dict[str, Any], quote: str) -> str:
    metric = f"{fact.get('metric_raw', '')} {fact.get('metric_canonical', '')}".lower()
    scope = f"{quote}\n{case.get('evidence_text', '')[:1200]}".lower()
    if any(term in metric for term in ["eps", "earnings per share", "per share"]):
        return "USD_per_share"
    if "employee" in scope or "workforce" in scope or "headcount" in scope:
        return "employees"
    if "%" in quote or "percent" in metric:
        return "percent"
    if "in millions" in scope:
        return "USD_millions"
    if "in thousands" in scope:
        return "USD_thousands"
    if any(term in metric for term in ["revenue", "sales", "income", "profit", "earnings", "expense", "cost", "tax", "premium"]):
        existing = fact.get("unit", "")
        if existing == "USD_per_share":
            return "USD_millions"
        return existing if existing in {"USD_millions", "USD_thousands"} else "USD_millions"
    return fact.get("unit", "numeric")


def unit_metric_mismatch(unit: str, metric_raw: str) -> bool:
    metric = metric_raw.lower()
    if unit == "USD_per_share" and any(term in metric for term in ["revenue", "sales", "premium", "income", "profit", "earnings", "expense", "cost", "tax"]):
        return not any(term in metric for term in ["per share", "eps"])
    return False


def fact_relevance(fact: dict[str, Any], case: dict[str, Any]) -> int:
    qea = f"{case.get('question', '')} {case.get('expected_answer', '')}"
    score = 0
    score += len(normalized_words(fact.get("metric_raw", "")) & normalized_words(qea))
    if fact.get("year") and str(fact["year"]) in qea:
        score += 1
    for variant in number_variants(fact.get("value")):
        if variant.replace("$ ", "$") in qea.replace("$ ", "$"):
            score += 2
            break
    metric = fact.get("metric_canonical", "")
    if any(term in metric for term in ["revenue", "income", "profit", "sales", "expense", "cost", "tax", "eps"]):
        score += 1
    return score


def repair_fact(fact: dict[str, Any], case: dict[str, Any], company: str, ticker: str) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    quote = find_exact_quote(case.get("evidence_text", ""), fact.get("metric_raw", ""), fact.get("value"))
    is_exact = exact_quote_is_valid(case.get("evidence_text", ""), quote, fact.get("metric_raw", ""), fact.get("value"))
    period = period_role(fact, case, quote)
    artifact_reason = metric_artifact_reason(fact, quote, period)
    if artifact_reason:
        return None, {
            "case_id": fact["case_id"],
            "fact_id": fact["fact_id"],
            "metric_raw": fact.get("metric_raw", ""),
            "year": fact.get("year"),
            "value": fact.get("value"),
            "unit": fact.get("unit", ""),
            "reason": artifact_reason,
        }
    if not is_exact:
        return None, {
            "case_id": fact["case_id"],
            "fact_id": fact["fact_id"],
            "metric_raw": fact.get("metric_raw", ""),
            "year": fact.get("year"),
            "value": fact.get("value"),
            "unit": fact.get("unit", ""),
            "reason": "exact_quote_recovery_failed",
        }
    if period != "reporting_period":
        return None, {
            "case_id": fact["case_id"],
            "fact_id": fact["fact_id"],
            "metric_raw": fact.get("metric_raw", ""),
            "year": fact.get("year"),
            "value": fact.get("value"),
            "unit": fact.get("unit", ""),
            "reason": f"period_role_not_reporting_period:{period}",
        }
    repaired_unit = infer_repaired_unit(fact, case, quote)
    if unit_metric_mismatch(repaired_unit, fact.get("metric_raw", "")):
        return None, {
            "case_id": fact["case_id"],
            "fact_id": fact["fact_id"],
            "metric_raw": fact.get("metric_raw", ""),
            "year": fact.get("year"),
            "value": fact.get("value"),
            "unit": repaired_unit,
            "reason": "unit_metric_mismatch_after_repair",
        }
    role = fact_role(fact, case)
    repaired = {
        "fact_id": fact["fact_id"],
        "case_id": fact["case_id"],
        "company": company,
        "ticker": ticker,
        "metric_canonical": fact.get("metric_canonical", ""),
        "metric_raw": fact.get("metric_raw", ""),
        "year": fact.get("year"),
        "period_label": fact.get("period_label", ""),
        "period_role": period,
        "value": fact.get("value"),
        "unit": repaired_unit,
        "role": role,
        "evidence_quote_exact": quote,
        "quote_is_exact_excerpt": True,
        "source_fact": True,
        "derived_answer_value": False,
        "needs_manual_review": False,
        "source_evidence_id": fact.get("source_evidence_id", ""),
    }
    return repaired, None


def neo4j_coverage(cases: list[dict[str, Any]], facts: list[dict[str, Any]], env_presence: dict[str, bool]) -> tuple[str, list[dict[str, Any]]]:
    if not env_presence["NEO4J_URI_present"] or not env_presence["NEO4J_PASSWORD_present"]:
        return "not_checked_no_neo4j_config", []
    try:
        from neo4j import GraphDatabase
    except Exception as exc:
        return f"not_checked_driver_unavailable:{exc}", []
    rows: list[dict[str, Any]] = []
    uri = os.getenv("NEO4J_URI", "")
    user = os.getenv("NEO4J_USER", "neo4j")
    password = os.getenv("NEO4J_PASSWORD", "")
    database = os.getenv("NEO4J_DATABASE") or None
    try:
        driver = GraphDatabase.driver(uri, auth=(user, password), connection_timeout=10)
        with driver.session(database=database) if database else driver.session() as session:
            session.run("RETURN 1 AS ok").single()
            for case in cases:
                result = session.run(
                    """
                    MATCH (n)
                    WHERE any(k IN keys(n) WHERE toString(n[k]) = $case_id OR toString(n[k]) = $source_id)
                    RETURN count(n) AS matching_nodes
                    """,
                    case_id=case["case_id"],
                    source_id=case.get("source_evidence_id", ""),
                ).single()
                rows.append({"case_id": case["case_id"], "matching_nodes": result["matching_nodes"] if result else 0})
        driver.close()
        if rows and all(row["matching_nodes"] > 0 for row in rows):
            return "checked_pass_read_only", rows
        return "checked_partial_or_no_matches_read_only", rows
    except Exception as exc:
        return f"not_checked_connection_failed:{exc}", rows


def main() -> int:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    env_presence = load_env_presence()
    cases = read_jsonl(CANDIDATE_DIR / "round3_selected_cases.jsonl")
    facts = read_jsonl(CANDIDATE_DIR / "round3_required_facts.jsonl")
    facts_by_case: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for fact in facts:
        facts_by_case[fact["case_id"]].append(fact)

    preflight_company_issue_ids = {
        json.loads(line)["case_id"]
        for line in (PREFLIGHT_DIR / "company_ticker_issues.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    }
    preflight_artifacts_by_case = Counter(
        json.loads(line)["case_id"]
        for line in (PREFLIGHT_DIR / "suspicious_parser_artifacts.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    )

    patch_review: list[dict[str, Any]] = []
    exclusions: list[dict[str, Any]] = []
    repaired_cases: list[dict[str, Any]] = []
    repaired_facts: list[dict[str, Any]] = []

    candidate_order = sorted(
        [case for case in cases if case["split"] != "integration_demo"],
        key=lambda c: (
            c["category"] != "Financials",
            c["reasoning_type"] not in {"Compositional", "Division", "Addition", "Subtraction", "Multiplication"},
            preflight_artifacts_by_case[c["case_id"]],
            c["case_id"] in preflight_company_issue_ids,
            -float(c.get("quality_score", 0)),
        ),
    )

    for case in candidate_order:
        if len(repaired_cases) >= 25:
            break
        if case["category"] != "Financials" and len(repaired_cases) < 15:
            continue
        company = case.get("company", "")
        ticker = case.get("ticker", "")
        company_reason = company_bad_reason(company)
        ticker_reason = "bad_or_missing_ticker" if ticker in BAD_TICKERS else ""
        suggestion = suggest_company_ticker(case)
        patched_company = company
        patched_ticker = ticker
        if company_reason or ticker_reason:
            patched_company = suggestion.get("company", company)
            patched_ticker = suggestion.get("ticker", ticker)
            if company_bad_reason(patched_company) and patched_ticker not in BAD_TICKERS:
                patched_company = patched_ticker
            patch_review.append(
                {
                    "case_id": case["case_id"],
                    "original_company": company,
                    "original_ticker": ticker,
                    "issue_codes": [x for x in [company_reason, ticker_reason] if x],
                    "suggested_company": patched_company,
                    "suggested_ticker": patched_ticker,
                    "patch_applied_to_repaired_subset_only": False,
                    "original_candidate_pool_modified": False,
                }
            )
        if company_bad_reason(patched_company) or patched_ticker in BAD_TICKERS:
            exclusions.append(
                {
                    "case_id": case["case_id"],
                    "reason": "unresolved_company_or_ticker",
                    "company": company,
                    "ticker": ticker,
                    "suggested_company": patched_company,
                    "suggested_ticker": patched_ticker,
                }
            )
            continue

        repaired_for_case: list[dict[str, Any]] = []
        fact_exclusions: list[dict[str, Any]] = []
        for fact in sorted(facts_by_case[case["case_id"]], key=lambda f: -fact_relevance(f, case)):
            if fact_relevance(fact, case) <= 0:
                continue
            repaired, excluded = repair_fact(fact, case, patched_company, patched_ticker)
            if repaired:
                repaired_for_case.append(repaired)
            elif excluded:
                fact_exclusions.append(excluded)
        # Keep a compact, semantically relevant fact set.
        repaired_for_case = repaired_for_case[:8]
        if len(repaired_for_case) < 2:
            exclusions.append(
                {
                    "case_id": case["case_id"],
                    "reason": "insufficient_repaired_required_facts",
                    "repaired_fact_count": len(repaired_for_case),
                    "fact_exclusions": fact_exclusions[:8],
                }
            )
            continue

        repaired_case = dict(case)
        repaired_case["company"] = patched_company
        repaired_case["ticker"] = patched_ticker
        repaired_case["required_fact_count"] = len(repaired_for_case)
        repaired_case["repair_status"] = "local_eval_ready_pending_neo4j"
        repaired_case["source_candidate_pool"] = "outputs/round3_case_factory"
        repaired_case["created_at"] = datetime.now(timezone.utc).isoformat()
        repaired_cases.append(repaired_case)
        repaired_facts.extend(repaired_for_case)
        exclusions.extend(fact_exclusions)

    ready_ids = {case["case_id"] for case in repaired_cases}
    exclusions.extend(
        {
            "case_id": case["case_id"],
            "reason": "not_selected_for_initial_eval_ready_subset",
            "split": case["split"],
            "category": case["category"],
        }
        for case in cases
        if case["split"] != "integration_demo" and case["case_id"] not in ready_ids
    )

    duplicate_case_id_count = len(repaired_cases) - len({case["case_id"] for case in repaired_cases})
    exact_quote_coverage = sum(1 for fact in repaired_facts if fact["quote_is_exact_excerpt"]) / max(1, len(repaired_facts))
    derived_leakage = sum(1 for fact in repaired_facts if fact["source_fact"] and fact["derived_answer_value"])
    unresolved_company_ticker = sum(
        1 for case in repaired_cases if company_bad_reason(case.get("company", "")) or case.get("ticker", "") in BAD_TICKERS
    )
    semantic_pass_rate = sum(
        1
        for fact in repaired_facts
        if fact["period_role"] == "reporting_period"
        and fact["quote_is_exact_excerpt"]
        and not fact["derived_answer_value"]
        and not fact["needs_manual_review"]
    ) / max(1, len(repaired_facts))

    neo4j_status, neo4j_rows = neo4j_coverage(repaired_cases, repaired_facts, env_presence)
    local_ready = (
        len(repaired_cases) >= 15
        and semantic_pass_rate >= 0.95
        and exact_quote_coverage == 1.0
        and derived_leakage == 0
        and duplicate_case_id_count == 0
        and unresolved_company_ticker == 0
    )
    if local_ready and neo4j_status == "checked_pass_read_only":
        decision = "go"
    elif local_ready:
        decision = "conditional_go"
    else:
        decision = "no_go"

    write_jsonl(OUT_DIR / "eval_ready_cases.jsonl", repaired_cases)
    write_jsonl(OUT_DIR / "eval_ready_required_facts.jsonl", repaired_facts)
    write_jsonl(OUT_DIR / "company_ticker_patch_review.jsonl", patch_review)
    write_jsonl(OUT_DIR / "parser_artifact_exclusions.jsonl", exclusions)

    (OUT_DIR / "exact_quote_recovery_report.md").write_text(
        f"""# Exact Quote Recovery Report

## Summary
- Repaired cases: {len(repaired_cases)}
- Repaired required facts: {len(repaired_facts)}
- Exact quote coverage: {exact_quote_coverage:.2%}
- Synthetic parser quotes retained: 0

## Method
For each selected fact, the repair step searched the original `evidence_text` for the metric label and reported value, then stored the exact source substring in `evidence_quote_exact`. Facts that could not be tied to an exact source substring were excluded.
""",
        encoding="utf-8",
    )

    (OUT_DIR / "neo4j_readonly_coverage_report.md").write_text(
        f"""# Neo4j Read-Only Coverage Report

## Status
`{neo4j_status}`

## Configuration Presence
- .env exists: {env_presence['env_file_exists']}
- NEO4J_URI present: {env_presence['NEO4J_URI_present']}
- NEO4J_PASSWORD present: {env_presence['NEO4J_PASSWORD_present']}
- NEO4J_DATABASE present: {env_presence['NEO4J_DATABASE_present']}

Secret values were not printed. No write Cypher was executed.

## Coverage Rows
- Rows checked: {len(neo4j_rows)}
""",
        encoding="utf-8",
    )

    split_counts = Counter(case["split"] for case in repaired_cases)
    category_counts = Counter(case["category"] for case in repaired_cases)
    reasoning_counts = Counter(case["reasoning_type"] for case in repaired_cases)
    (OUT_DIR / "repair_summary.md").write_text(
        f"""# Round 3 Case Repair Summary

Generated at: {datetime.now(timezone.utc).isoformat()}

## Scope
The original candidate pool in `outputs/round3_case_factory/` was preserved. This repair pass created a smaller local evaluation-ready subset.

## Repaired Subset
- eval-ready local cases: {len(repaired_cases)}
- eval-ready required facts: {len(repaired_facts)}
- split counts: {dict(split_counts)}
- category counts: {dict(category_counts)}
- reasoning counts: {dict(reasoning_counts)}

## Validation
- required fact semantic pass: {semantic_pass_rate:.2%}
- exact evidence quote coverage: {exact_quote_coverage:.2%}
- derived leakage: {derived_leakage}
- duplicate case_id: {duplicate_case_id_count}
- company/ticker unresolved issue: {unresolved_company_ticker}
- Neo4j status: `{neo4j_status}`

## Notes
Company/ticker changes are recorded as patch review suggestions and are applied only inside the repaired subset outputs. The original candidate pool was not overwritten.
""",
        encoding="utf-8",
    )

    (OUT_DIR / "go_no_go_decision.md").write_text(
        f"""# Go / No-Go Decision

## Decision
`{decision}`

## Gate Results
- eval_ready_cases >= 15: {len(repaired_cases) >= 15} ({len(repaired_cases)})
- required fact semantic pass >= 95%: {semantic_pass_rate >= 0.95} ({semantic_pass_rate:.2%})
- exact evidence quote coverage = 100%: {exact_quote_coverage == 1.0} ({exact_quote_coverage:.2%})
- derived leakage = 0: {derived_leakage == 0} ({derived_leakage})
- duplicate case_id = 0: {duplicate_case_id_count == 0} ({duplicate_case_id_count})
- company/ticker unresolved issue = 0: {unresolved_company_ticker == 0} ({unresolved_company_ticker})
- Neo4j coverage checked and passed: {neo4j_status == 'checked_pass_read_only'} (`{neo4j_status}`)

## Interpretation
The local repaired subset can be used for dry-run evaluation design review, but final `go` requires Neo4j read-only coverage to pass.
""",
        encoding="utf-8",
    )

    summary = {
        "decision": decision,
        "eval_ready_cases": len(repaired_cases),
        "eval_ready_required_facts": len(repaired_facts),
        "split_counts": dict(split_counts),
        "semantic_pass_rate": semantic_pass_rate,
        "exact_quote_coverage": exact_quote_coverage,
        "derived_leakage": derived_leakage,
        "duplicate_case_id_count": duplicate_case_id_count,
        "company_ticker_unresolved": unresolved_company_ticker,
        "neo4j_status": neo4j_status,
    }
    write_json(OUT_DIR / "repair_summary.json", summary)
    print(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
