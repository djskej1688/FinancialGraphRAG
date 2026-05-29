from __future__ import annotations

import csv
import importlib.util
import json
import os
import re
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[1]
INPUT_DIR = REPO_ROOT / "outputs" / "round3_case_factory"
REVIEW_DIR = REPO_ROOT / "outputs" / "round3_case_factory_review"
FACTORY_SCRIPT = REPO_ROOT / "scripts" / "round3_case_factory.py"

BAD_TICKERS = {"", "NA", "N/A", "LT", "FY", "US", "UK", "SEC", "EPS", "USD", "GAAP"}
BAD_COMPANY_EXACT = {
    "",
    "unknown",
    "employees",
    "employee",
    "na",
    "n/a",
    "long-term debt",
    "values",
    "principles",
    "governance",
    "values/principles/governance",
}
QUESTION_PHRASE_MARKERS = {
    "impact",
    "impacts",
    "comparison",
    "ratio",
    "ratios",
    "growth",
    "margin",
    "distribution",
    "breakdown",
    "strategy",
    "strategies",
    "analysis",
    "analyzing",
    "trends",
    "effects",
    "effect",
    "revenue",
    "headcount",
    "workforce",
    "employee",
    "employees",
    "cost",
    "costs",
    "sales force",
    "succession",
    "union",
    "retirement",
    "maturity",
    "litigation",
    "capital allocation",
}
HEADER_METRIC_PHRASES = {
    "birth years between",
    "in millions except",
    "in millions, except",
    "at_the_end_of",
    "at the end of",
    "year ended",
    "years ended",
    "number of employees",
    "consolidated statements",
    "statement of operations",
    "table of contents",
}
SECTION_COMPANY_TERMS = {
    "long-term debt",
    "values",
    "principles",
    "governance",
    "employees",
    "risk factors",
    "results of operations",
}


def load_factory_module() -> Any:
    spec = importlib.util.spec_from_file_location("round3_case_factory", FACTORY_SCRIPT)
    if spec is None or spec.loader is None:
        raise RuntimeError("Cannot load round3_case_factory.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    with path.open("w", encoding="utf-8", newline="\n") as fh:
        for row in rows:
            fh.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")


def write_json(path: Path, data: Any) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")


def words(text: str) -> set[str]:
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
        }
    }


def normalized_number_strings(text: str) -> set[str]:
    out: set[str] = set()
    for token in re.findall(r"\(?-?\$?\d[\d,]*(?:\.\d+)?%?\)?", text or ""):
        clean = token.strip().strip("()").replace("$", "").replace(",", "").replace("%", "")
        try:
            value = float(clean)
        except ValueError:
            continue
        if token.strip().startswith("(") and token.strip().endswith(")"):
            value = -value
        out.add(str(int(value)) if value == int(value) else f"{value:.4f}".rstrip("0").rstrip("."))
    return out


def suggested_company_ticker(case: dict[str, Any]) -> dict[str, str]:
    question = case.get("question", "")
    evidence = case.get("evidence_text", "")
    suggestion: dict[str, str] = {}
    ticker_match = re.search(r"\(([A-Z]{1,5})\)", question)
    if ticker_match and ticker_match.group(1) not in BAD_TICKERS:
        suggestion["ticker"] = ticker_match.group(1)
    elif case.get("ticker") and case["ticker"] not in BAD_TICKERS:
        suggestion["ticker"] = case["ticker"]
    trailing = re.search(r",\s*([A-Z]{1,5})\.?$", question)
    if trailing and trailing.group(1) not in BAD_TICKERS:
        suggestion["ticker"] = trailing.group(1)

    if "ticker" in suggestion:
        company_match = re.search(rf"([A-Z][A-Za-z0-9&.,' -]{{2,80}})\s*\({suggestion['ticker']}\)", question)
        if company_match:
            suggestion["company"] = company_match.group(1).strip(" ,.'")
    first_line = next((line.strip() for line in evidence.splitlines() if line.strip()), "")
    if first_line and len(first_line) <= 100 and re.search(r"[A-Za-z]", first_line):
        first_line = re.sub(r"\s+and\s+Subsidiaries.*", "", first_line)
        first_line = re.sub(r"\s+Consolidated.*", "", first_line)
        if not any(term in first_line.lower() for term in SECTION_COMPANY_TERMS):
            suggestion.setdefault("company", first_line.strip(" ,."))
    return suggestion


def company_ticker_issues(cases: list[dict[str, Any]]) -> list[dict[str, Any]]:
    issues: list[dict[str, Any]] = []
    for case in cases:
        company = (case.get("company") or "").strip()
        ticker = (case.get("ticker") or "").strip()
        lower_company = company.lower()
        reason_codes: list[str] = []
        if lower_company in BAD_COMPANY_EXACT:
            reason_codes.append("bad_company_exact_or_unknown")
        if any(term in lower_company for term in SECTION_COMPANY_TERMS):
            reason_codes.append("company_looks_like_section_heading")
        if len(company) > 45 or any(marker in lower_company for marker in QUESTION_PHRASE_MARKERS):
            reason_codes.append("company_looks_like_question_phrase")
        if company and case.get("question", "").lower().startswith(lower_company[: min(25, len(lower_company))]):
            reason_codes.append("company_prefix_matches_question_phrase")
        if ticker.upper() in BAD_TICKERS:
            reason_codes.append("bad_or_missing_ticker")
        if reason_codes:
            suggestion = suggested_company_ticker(case)
            issues.append(
                {
                    "case_id": case["case_id"],
                    "split": case["split"],
                    "company": company,
                    "ticker": ticker,
                    "reason_codes": sorted(set(reason_codes)),
                    "question": case["question"],
                    "suggested_company": suggestion.get("company", ""),
                    "suggested_ticker": suggestion.get("ticker", ""),
                    "action": "review_only_no_patch_applied",
                }
            )
    return issues


def fact_issues(cases_by_id: dict[str, dict[str, Any]], facts: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    semantic: list[dict[str, Any]] = []
    artifacts: list[dict[str, Any]] = []
    for fact in facts:
        case = cases_by_id[fact["case_id"]]
        metric = (fact.get("metric_raw") or "").strip()
        metric_canonical = (fact.get("metric_canonical") or "").strip()
        metric_lower = f"{metric} {metric_canonical}".lower()
        evidence = case.get("evidence_text", "")
        expected = case.get("expected_answer", "")
        question = case.get("question", "")
        quote = fact.get("evidence_quote", "")
        year = fact.get("year")
        value = fact.get("value")
        unit = fact.get("unit", "")
        issue_codes: list[str] = []
        artifact_codes: list[str] = []

        if not quote:
            issue_codes.append("missing_evidence_quote")
        if quote and quote not in evidence:
            issue_codes.append("evidence_quote_is_synthetic_not_exact_excerpt")

        qa_words = words(question + " " + expected)
        metric_words = words(metric)
        answer_numbers = normalized_number_strings(expected)
        fact_value = ""
        try:
            fval = float(value)
            fact_value = str(int(fval)) if fval == int(fval) else f"{fval:.4f}".rstrip("0").rstrip(".")
        except Exception:
            pass
        relevance = len(qa_words & metric_words)
        if str(year or "") and str(year) in (question + " " + expected):
            relevance += 1
        if fact_value and fact_value in answer_numbers:
            relevance += 2
        if relevance == 0:
            issue_codes.append("fact_not_clearly_needed_for_expected_answer")

        if any(phrase in metric_lower for phrase in HEADER_METRIC_PHRASES):
            issue_codes.append("metric_looks_like_header_or_section_phrase")
            artifact_codes.append("metric_header_phrase")
        if metric_lower in {"revenue", "revenues", "total revenues", "earned premiums"} and unit == "USD_per_share":
            issue_codes.append("unit_conflicts_with_metric")
            artifact_codes.append("usd_per_share_revenue_conflict")
        if unit == "USD_per_share" and any(term in metric_lower for term in ["revenue", "premium", "sales"]):
            issue_codes.append("unit_conflicts_with_metric")
            artifact_codes.append("usd_per_share_revenue_conflict")
        if year and int(year) < 2000:
            issue_codes.append("year_is_before_2000_reporting_period")
            artifact_codes.append("pre_2000_reporting_period")
        if year and re.search(rf"\b(?:born|birth|birth years?|ages?|maturity|expires?|expiration)\b[^.\n]{{0,80}}\b{year}\b", evidence, re.I):
            issue_codes.append("year_looks_like_birth_maturity_or_expiration_date")
            artifact_codes.append("non_reporting_year_used_as_period")
        if isinstance(value, (int, float)) and float(value) < 0 and re.search(r"\d+(?:\.\d+)?\s*%\s*(?:-|to|through)\s*\d+(?:\.\d+)?\s*%", evidence, re.I):
            issue_codes.append("negative_value_may_be_percentage_range_artifact")
            artifact_codes.append("negative_percentage_range_artifact")
        if fact.get("derived_answer_value") is True and fact.get("source_fact") is True:
            issue_codes.append("derived_answer_value_marked_as_source_fact")
        if fact.get("fact_role") not in {
            "numerator",
            "denominator",
            "base_year_value",
            "current_year_value",
            "component",
            "total",
            "explanatory_context",
        }:
            issue_codes.append("unclear_fact_role")
        if unit == "numeric":
            issue_codes.append("unit_too_generic_for_eval_ready_fact")

        if issue_codes:
            semantic.append(
                {
                    "case_id": fact["case_id"],
                    "fact_id": fact["fact_id"],
                    "split": case["split"],
                    "metric_raw": metric,
                    "metric_canonical": metric_canonical,
                    "year": year,
                    "value": value,
                    "unit": unit,
                    "fact_role": fact.get("fact_role", ""),
                    "issue_codes": sorted(set(issue_codes)),
                    "question": question,
                    "expected_answer": expected,
                    "evidence_quote": quote,
                    "action": "review_or_exclude_before_eval",
                }
            )
        if artifact_codes:
            artifacts.append(
                {
                    "case_id": fact["case_id"],
                    "fact_id": fact["fact_id"],
                    "split": case["split"],
                    "metric_raw": metric,
                    "year": year,
                    "value": value,
                    "unit": unit,
                    "artifact_codes": sorted(set(artifact_codes)),
                    "action": "parser_artifact_review",
                }
            )
    return semantic, artifacts


def neo4j_coverage(cases: list[dict[str, Any]], facts: list[dict[str, Any]]) -> tuple[str, list[dict[str, Any]]]:
    uri = os.getenv("NEO4J_URI", "").strip()
    password = os.getenv("NEO4J_PASSWORD", "").strip()
    user = os.getenv("NEO4J_USER", "neo4j").strip()
    if not uri or not password:
        return "not_checked_no_neo4j_config", []
    try:
        from neo4j import GraphDatabase
    except Exception as exc:
        return f"not_checked_neo4j_driver_unavailable:{exc}", []

    rows: list[dict[str, Any]] = []
    try:
        driver = GraphDatabase.driver(uri, auth=(user, password), connection_timeout=10)
        with driver.session() as session:
            session.run("RETURN 1 AS ok").single()
            for case in cases:
                result = session.run(
                    """
                    MATCH (n)
                    WHERE any(k IN keys(n) WHERE toString(n[k]) = $case_id OR toString(n[k]) = $source_id)
                    RETURN count(n) AS matching_nodes, collect(distinct labels(n))[0..10] AS labels
                    """,
                    case_id=case["case_id"],
                    source_id=case.get("source_evidence_id", ""),
                ).single()
                rows.append(
                    {
                        "case_id": case["case_id"],
                        "matching_nodes": result["matching_nodes"] if result else 0,
                        "labels": result["labels"] if result else [],
                    }
                )
        driver.close()
        return "checked_read_only_generic_property_match", rows
    except Exception as exc:
        return f"not_checked_connection_failed:{exc}", rows


def revised_integration_demo(factory: Any) -> list[dict[str, Any]]:
    rows = factory.read_json_dataset(factory.DEFAULT_DATASET)
    candidates = factory.build_candidates(rows, str(factory.DEFAULT_DATASET.relative_to(factory.REPO_ROOT)))
    by_ticker: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for cand in candidates:
        raw = cand["row"]["question"] + " " + cand["row"]["expected_answer"]
        if "CRWD" in raw or "CrowdStrike" in raw:
            cand["ticker"] = "CRWD"
            by_ticker["CRWD"].append(cand)
        elif "META" in raw or "Meta Platforms" in raw:
            cand["ticker"] = "META"
            by_ticker[cand["ticker"]].append(cand)

    best_ticker = ""
    best_score = -1
    for ticker, items in by_ticker.items():
        cats = {item["category"] for item in items}
        desired = {"Financials", "Company Overview", "Footnotes", "Risk", "Governance"}
        mixed_score = len(cats & desired) * 100 + len(cats) * 10 + len(items)
        if {"Financials", "Company Overview"} <= cats and mixed_score > best_score:
            best_ticker = ticker
            best_score = mixed_score
    if not best_ticker:
        best_ticker = "CRWD" if by_ticker.get("CRWD") else "META"

    items = sorted(by_ticker.get(best_ticker, []), key=lambda c: (-c["required_fact_count"], -c["quality_score"]))
    preferred_order = ["Financials", "Company Overview", "Footnotes", "Risk", "Governance", "Shareholder Return", "Legal", "Accounting"]
    selected: list[dict[str, Any]] = []
    used: set[str] = set()
    for cat in preferred_order:
        cat_items = [c for c in items if c["category"] == cat and c["source_id"] not in used]
        if cat_items:
            chosen = max(cat_items, key=lambda c: (c["required_fact_count"], c["quality_score"]))
            selected.append(chosen)
            used.add(chosen["source_id"])
        if len(selected) >= 8:
            break

    revised: list[dict[str, Any]] = []
    for idx, cand in enumerate(selected, start=1):
        row = cand["row"]
        revised.append(
            {
                "demo_id": f"integration_demo_revised_{best_ticker}_{idx:02d}_{cand['source_id']}",
                "source_id": cand["source_id"],
                "ticker": cand["ticker"],
                "company": cand["company"],
                "category": cand["category"],
                "reasoning_type": cand["reasoning_type"],
                "question": row["question"],
                "expected_answer": row["expected_answer"],
                "evidence_text": row["text"],
                "required_fact_count": cand["required_fact_count"],
                "quality_score": cand["quality_score"],
                "benchmark_usage": "do_not_score_with_round3_benchmark",
                "integration_rationale": "Revised demo groups one ticker across multiple categories for graph navigation/storytelling; it is deliberately separated from benchmark scoring.",
            }
        )
    return revised


def main() -> int:
    REVIEW_DIR.mkdir(parents=True, exist_ok=True)
    cases = read_jsonl(INPUT_DIR / "round3_selected_cases.jsonl")
    facts = read_jsonl(INPUT_DIR / "round3_required_facts.jsonl")
    split_files = [
        "round3_dev_cases.json",
        "round3_test_cases.json",
        "round3_baseline_control_cases.json",
        "round3_integration_demo_cases.json",
    ]
    for split_file in split_files:
        json.loads((INPUT_DIR / split_file).read_text(encoding="utf-8"))

    cases_by_id = {case["case_id"]: case for case in cases}
    company_issues = company_ticker_issues(cases)
    semantic_issues, artifacts = fact_issues(cases_by_id, facts)

    case_issue_ids = {issue["case_id"] for issue in company_issues}
    fact_issue_by_case: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for issue in semantic_issues:
        fact_issue_by_case[issue["case_id"]].append(issue)
        case_issue_ids.add(issue["case_id"])

    eval_ready_cases = [case for case in cases if case["case_id"] not in case_issue_ids and case["split"] != "integration_demo"]
    ready_ids = {case["case_id"] for case in eval_ready_cases}
    eval_ready_facts = [fact for fact in facts if fact["case_id"] in ready_ids]
    fix_or_exclude = []
    for case in cases:
        if case["case_id"] in case_issue_ids:
            fix_or_exclude.append(
                {
                    "case_id": case["case_id"],
                    "split": case["split"],
                    "question": case["question"],
                    "company": case.get("company", ""),
                    "ticker": case.get("ticker", ""),
                    "company_ticker_issue_count": sum(1 for issue in company_issues if issue["case_id"] == case["case_id"]),
                    "required_fact_issue_count": len(fact_issue_by_case.get(case["case_id"], [])),
                    "recommended_action": "fix_company_ticker_or_required_facts_then_revalidate",
                }
            )

    neo4j_status, neo4j_rows = neo4j_coverage(cases, facts)
    factory = load_factory_module()
    integration_revised = revised_integration_demo(factory)

    write_jsonl(REVIEW_DIR / "company_ticker_issues.jsonl", company_issues)
    write_jsonl(REVIEW_DIR / "required_fact_semantic_issues.jsonl", semantic_issues)
    write_jsonl(REVIEW_DIR / "suspicious_parser_artifacts.jsonl", artifacts)
    write_jsonl(REVIEW_DIR / "round3_eval_ready_cases.jsonl", eval_ready_cases)
    write_jsonl(REVIEW_DIR / "round3_eval_ready_required_facts.jsonl", eval_ready_facts)
    write_jsonl(REVIEW_DIR / "round3_cases_to_fix_or_exclude.jsonl", fix_or_exclude)
    write_json(REVIEW_DIR / "integration_demo_revised.json", integration_revised)

    fact_pass = len(facts) - len({issue["fact_id"] for issue in semantic_issues})
    fact_pass_rate = fact_pass / max(1, len(facts))
    ready_split_counts = Counter(case["split"] for case in eval_ready_cases)
    company_issue_counts = Counter(code for issue in company_issues for code in issue["reason_codes"])
    semantic_issue_counts = Counter(code for issue in semantic_issues for code in issue["issue_codes"])
    artifact_counts = Counter(code for issue in artifacts for code in issue["artifact_codes"])
    integration_categories = Counter(item["category"] for item in integration_revised)

    if neo4j_status.startswith("checked") and len(eval_ready_cases) >= 25 and ready_split_counts.get("round3_test", 0) >= 10 and fact_pass_rate >= 0.95:
        decision = "go"
    elif len(eval_ready_cases) >= 10 and ready_split_counts.get("round3_dev", 0) >= 10 and fact_pass_rate >= 0.80:
        decision = "conditional_go"
    else:
        decision = "no_go"
    if not neo4j_status.startswith("checked"):
        decision = "no_go"

    (REVIEW_DIR / "neo4j_coverage_report.md").write_text(
        f"""# Neo4j Coverage Report

## Status
`{neo4j_status}`

## Result
Neo4j read-only coverage was not counted as passed unless the status starts with `checked`.

## Notes
- `.env` present: {Path(REPO_ROOT / '.env').exists()}
- NEO4J_URI configured in environment: {bool(os.getenv('NEO4J_URI'))}
- NEO4J_PASSWORD configured in environment: {bool(os.getenv('NEO4J_PASSWORD'))}
- Coverage rows returned: {len(neo4j_rows)}

No write Cypher was executed by this preflight.
""",
        encoding="utf-8",
    )

    report = f"""# Round 3 Preflight Validation Report

Generated at: {datetime.now(timezone.utc).isoformat()}

## Candidate Pool
- Candidate cases checked: {len(cases)}
- Required facts checked: {len(facts)}
- Splits: {dict(Counter(case['split'] for case in cases))}

## Evaluation-Ready Local Subset
- Local eval-ready cases: {len(eval_ready_cases)}
- Local eval-ready split counts: {dict(ready_split_counts)}
- Local eval-ready required facts: {len(eval_ready_facts)}

This is a local semantic/provenance subset only. It is not a final `go` because Neo4j coverage is `{neo4j_status}`.

## Company/Ticker Sanity
- Issue rows: {len(company_issues)}
- Issue counts: {dict(company_issue_counts)}

## Required Facts Semantic Check
- Semantic issue rows: {len(semantic_issues)}
- Required fact semantic pass rate: {fact_pass_rate:.2%}
- Issue counts: {dict(semantic_issue_counts)}

## Suspicious Parser Artifacts
- Artifact rows: {len(artifacts)}
- Artifact counts: {dict(artifact_counts)}

## Revised Integration Demo
- Revised demo rows: {len(integration_revised)}
- Category mix: {dict(integration_categories)}
- Tickers: {dict(Counter(item['ticker'] for item in integration_revised))}

## Interpretation
The candidate pool should be preserved, but many cases need review before scoring. The largest risks are company names inferred from question phrases, synthetic rather than exact evidence quote strings, and parser artifacts where non-reporting years are treated as fiscal periods.
"""
    (REVIEW_DIR / "preflight_validation_report.md").write_text(report, encoding="utf-8")

    (REVIEW_DIR / "go_no_go_decision.md").write_text(
        f"""# Go / No-Go Decision

## Decision
`{decision}`

## Gate Results
- evaluation-ready cases >= 25: {len(eval_ready_cases) >= 25} ({len(eval_ready_cases)})
- test cases >= 10: {ready_split_counts.get('round3_test', 0) >= 10} ({ready_split_counts.get('round3_test', 0)})
- required fact semantic pass >= 95%: {fact_pass_rate >= 0.95} ({fact_pass_rate:.2%})
- Neo4j coverage pass: {neo4j_status.startswith('checked')} (`{neo4j_status}`)

## Recommendation
Do not run the full Round 3 scoring evaluation from the original 50-case candidate pool yet. First fix or exclude rows listed in `round3_cases_to_fix_or_exclude.jsonl`, replace synthetic evidence quotes with exact excerpts where needed, and rerun Neo4j read-only coverage.
""",
        encoding="utf-8",
    )

    summary = {
        "decision": decision,
        "candidate_cases": len(cases),
        "required_facts": len(facts),
        "eval_ready_cases": len(eval_ready_cases),
        "eval_ready_split_counts": dict(ready_split_counts),
        "company_ticker_issues": len(company_issues),
        "semantic_fact_issues": len(semantic_issues),
        "suspicious_parser_artifacts": len(artifacts),
        "fact_semantic_pass_rate": fact_pass_rate,
        "neo4j_status": neo4j_status,
        "integration_demo_revised_count": len(integration_revised),
    }
    write_json(REVIEW_DIR / "preflight_summary.json", summary)
    print(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
