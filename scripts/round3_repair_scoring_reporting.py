"""Repair Round 3 ready-partial scoring/reporting from existing raw outputs only."""

from __future__ import annotations

import csv
import json
import math
import re
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[1]
RUN_DIR = REPO_ROOT / "outputs" / "round3_eval_runs" / "ready_partial_real_20260527_093341"
CASES_PATH = REPO_ROOT / "outputs" / "round3_case_factory_repaired" / "eval_ready_cases.jsonl"
FACTS_PATH = REPO_ROOT / "outputs" / "round3_case_factory_repaired" / "eval_ready_required_facts.jsonl"
METHODS = ("vector_only", "graph_facts_only", "hybrid_vector_graph", "gold_context")
NUM_RE = re.compile(r"\(?-?\$?\d[\d,]*(?:\.\d+)?\)?\s*(?:%|percent|percentage|million|millions|billion|billions|thousand|thousands)?", re.I)
YEAR_RE = re.compile(r"\b(?:19|20)\d{2}\b")


@dataclass(frozen=True)
class NumberToken:
    raw: str
    value: float
    is_percent: bool
    is_year: bool
    scale_hint: str

    @property
    def ratio(self) -> float:
        return self.value / 100.0 if self.is_percent else self.value


def now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def rel(path: Path) -> str:
    try:
        return path.resolve().relative_to(REPO_ROOT.resolve()).as_posix()
    except ValueError:
        return str(path)


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            rows.append(json.loads(line))
    return rows


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")


def write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text.rstrip() + "\n", encoding="utf-8", newline="\n")


def write_csv(path: Path, fieldnames: list[str], rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def normalize_number(raw: Any, *, unit: str = "") -> float | None:
    text = str(raw).strip().lower()
    if not text:
        return None
    negative = text.startswith("(") and text.endswith(")")
    text = text.strip("()").replace("$", "").replace(",", "")
    multiplier = 1.0
    if "billion" in text or "billion" in unit.lower():
        multiplier = 1_000_000_000.0
    elif "million" in text or "million" in unit.lower():
        multiplier = 1_000_000.0
    elif "thousand" in text or "thousand" in unit.lower():
        multiplier = 1_000.0
    text = re.sub(r"(percent|percentage|%|millions?|billions?|thousands?)", "", text).strip()
    try:
        value = float(text) * multiplier
    except ValueError:
        return None
    return -value if negative else value


def extract_numbers(text: str) -> list[NumberToken]:
    tokens: list[NumberToken] = []
    for match in NUM_RE.finditer(text or ""):
        raw = match.group(0)
        value = normalize_number(raw)
        if value is None:
            continue
        lowered = raw.lower()
        tokens.append(
            NumberToken(
                raw=raw,
                value=value,
                is_percent=("%" in lowered or "percent" in lowered or "percentage" in lowered),
                is_year=bool(YEAR_RE.fullmatch(raw.strip())),
                scale_hint=("million" if "million" in lowered else "billion" if "billion" in lowered else ""),
            )
        )
    return tokens


def numbers_close(expected: float, actual: float, *, unit: str = "", expected_percent: bool = False, actual_percent: bool = False) -> bool:
    if expected_percent or actual_percent:
        expected_pct = expected if expected_percent else expected * 100.0
        actual_pct = actual if actual_percent else actual * 100.0
        return math.isclose(expected_pct, actual_pct, abs_tol=0.25) or math.isclose(expected_pct / 100.0, actual_pct / 100.0, abs_tol=0.0025)
    candidates = [expected]
    if "million" in unit.lower():
        candidates.extend([expected / 1_000_000.0, expected * 1_000_000.0])
    if "billion" in unit.lower():
        candidates.extend([expected / 1_000_000_000.0, expected * 1_000_000_000.0])
    return any(math.isclose(candidate, actual, rel_tol=0.005, abs_tol=0.02) for candidate in candidates)


def normalized_words(text: str) -> set[str]:
    return {token for token in re.sub(r"[^a-z0-9]+", " ", str(text).lower()).split() if len(token) > 1}


def metric_match(metric: str, text: str) -> bool:
    metric_tokens = normalized_words(metric.replace("_", " "))
    if not metric_tokens:
        return True
    text_tokens = normalized_words(text)
    return len(metric_tokens & text_tokens) / max(1, len(metric_tokens)) >= 0.45


def value_appears(fact: dict[str, Any], text: str, nums: list[NumberToken]) -> bool:
    raw_value = fact.get("value")
    unit = str(fact.get("unit", ""))
    fact_value = normalize_number(raw_value, unit=unit)
    if fact_value is None:
        return str(raw_value).lower() in text.lower()
    fact_percent = "%" in str(raw_value) or "percent" in unit.lower()
    return any(numbers_close(fact_value, token.value, unit=unit, expected_percent=fact_percent, actual_percent=token.is_percent) for token in nums if not token.is_year)


def fact_answer_match(fact: dict[str, Any], text: str, nums: list[NumberToken]) -> tuple[bool, list[str]]:
    evidence = []
    if value_appears(fact, text, nums):
        evidence.append("value_unit_match")
    year = str(fact.get("year", ""))
    if not year or year in text:
        evidence.append("year_match")
    metric = str(fact.get("metric_canonical") or fact.get("metric_raw") or fact.get("metric") or "")
    if metric_match(metric, text):
        evidence.append("metric_match")
    entity = str(fact.get("ticker") or fact.get("company") or fact.get("entity") or "")
    if not entity or entity.lower() in text.lower() or metric_match(entity, text):
        evidence.append("entity_match")
    matched = "value_unit_match" in evidence and "year_match" in evidence and ("metric_match" in evidence or "entity_match" in evidence)
    return matched, evidence


def percent_by_year(text: str) -> dict[str, list[float]]:
    result: dict[str, list[float]] = defaultdict(list)
    for year in YEAR_RE.findall(text or ""):
        start = max(0, text.find(year) - 90)
        end = min(len(text), text.find(year) + 180)
        window = text[start:end]
        for token in extract_numbers(window):
            if token.is_percent and not token.is_year:
                result[year].append(token.value)
    return result


def consistency_warnings(final_answer: str, calculation: str, question: str, facts: list[dict[str, Any]]) -> list[str]:
    warnings: list[str] = []
    final_pct = percent_by_year(final_answer)
    calc_pct = percent_by_year(calculation)
    for year, final_values in final_pct.items():
        if year not in calc_pct:
            continue
        for value in final_values:
            if calc_pct[year] and min(abs(value - candidate) for candidate in calc_pct[year]) > 0.35:
                warnings.append("final_answer_calculation_mismatch")
                break
    text = f"{question}\n{final_answer}\n{calculation}".lower()
    if "inventory turnover" in text or ("inventory" in text and "turnover" in text):
        warnings.append("ambiguous_formula")
        years = sorted({int(fact["year"]) for fact in facts if str(fact.get("year", "")).isdigit()})
        if years and (min(years) - 1) not in years:
            warnings.append("missing_prior_period")
    return sorted(set(warnings))


def load_data() -> tuple[dict[str, dict[str, Any]], dict[str, list[dict[str, Any]]]]:
    cases = {row["case_id"]: row for row in load_jsonl(CASES_PATH)}
    facts: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in load_jsonl(FACTS_PATH):
        facts[str(row.get("case_id", ""))].append(row)
    return cases, facts


def repair() -> dict[str, Any]:
    cases, facts_by_case = load_data()
    original_rows = load_jsonl(RUN_DIR / "case_results.jsonl")
    revised_rows: list[dict[str, Any]] = []
    diagnostics: list[dict[str, Any]] = []
    ambiguous_cases: set[str] = set()
    unit_adjusted_count = 0

    for row in original_rows:
        case_id = str(row["case_id"])
        method = str(row["method"])
        case = cases.get(case_id, {})
        facts = facts_by_case.get(case_id, [])
        raw_output_path = RUN_DIR / "raw_outputs" / f"{case_id}__{method}.json"
        payload = json.loads(raw_output_path.read_text(encoding="utf-8"))
        result = payload.get("method_result", {})
        final_answer = str(result.get("final_answer", ""))
        calculation = str(result.get("calculation", ""))
        answer_text = f"{final_answer}\n{calculation}"
        nums = extract_numbers(answer_text)
        source_ids = {str(item) for item in result.get("source_fact_ids_used", [])}
        fact_ids = {str(fact.get("fact_id", "")) for fact in facts}
        source_matches = sorted(fid for fid in fact_ids if fid and fid in source_ids)
        answer_matches = []
        per_fact = []
        for fact in facts:
            matched, evidence = fact_answer_match(fact, answer_text, nums)
            if matched:
                answer_matches.append(str(fact.get("fact_id", "")))
            per_fact.append(
                {
                    "fact_id": fact.get("fact_id", ""),
                    "answer_value_matched": matched,
                    "evidence": evidence,
                    "unit": fact.get("unit", ""),
                    "value": fact.get("value"),
                    "year": fact.get("year"),
                    "metric": fact.get("metric_canonical") or fact.get("metric_raw") or "",
                }
            )
            if matched and "million" in str(fact.get("unit", "")).lower():
                unit_adjusted_count += 1
        total = len(facts)
        source_recall = round(len(source_matches) / max(1, total), 4)
        answer_value_recall = round(len(answer_matches) / max(1, total), 4)
        legacy = float(row.get("required_fact_recall", 0.0) or 0.0)
        expected_answer = str(case.get("expected_answer", ""))
        expected_percents = [token for token in extract_numbers(expected_answer) if token.is_percent and not token.is_year]
        actual_percents = [token for token in nums if token.is_percent and not token.is_year]
        numeric_ok = bool(row.get("numeric_correctness"))
        if expected_percents:
            numeric_ok = all(any(numbers_close(exp.value, act.value, expected_percent=True, actual_percent=True) for act in actual_percents) for exp in expected_percents)
        warnings = consistency_warnings(final_answer, calculation, str(case.get("question", "")), facts)
        if method == "gold_context" and not source_matches:
            warnings.append("gold_context_fact_id_unavailable")
        if any("million" in str(f.get("unit", "")).lower() for f in facts):
            warnings.append("scorer_unit_scaling_adjusted")
        warnings = sorted(set(warnings))
        if "ambiguous_formula" in warnings or "missing_prior_period" in warnings:
            ambiguous_cases.add(case_id)
        answer_correctness = bool(numeric_ok and answer_value_recall >= 0.75 and "final_answer_calculation_mismatch" not in warnings)
        revised = dict(row)
        revised.update(
            {
                "legacy_required_fact_recall": legacy,
                "source_fact_id_recall": source_recall,
                "answer_value_fact_recall": answer_value_recall,
                "required_fact_recall": answer_value_recall,
                "numeric_correctness": 1.0 if numeric_ok else 0.0,
                "answer_correctness": 1.0 if answer_correctness else 0.0,
                "warning_categories": warnings,
            }
        )
        revised_rows.append(revised)
        diagnostics.append(
            {
                "case_id": case_id,
                "method": method,
                "legacy_required_fact_recall": legacy,
                "source_fact_id_recall": source_recall,
                "source_fact_id_matches": source_matches,
                "answer_value_fact_recall": answer_value_recall,
                "answer_value_fact_matches": answer_matches,
                "total_required_facts": total,
                "numeric_correctness_revised": numeric_ok,
                "answer_correctness_revised": answer_correctness,
                "warning_categories": warnings,
                "per_fact": per_fact,
            }
        )

    summary_rows = []
    for method in METHODS:
        rows = [row for row in revised_rows if row["method"] == method]
        scored = [row for row in rows if row.get("success")]
        summary_rows.append(
            {
                "method": method,
                "attempt_count": len(rows),
                "provider_success_count": sum(1 for row in rows if row.get("provider_success")),
                "provider_error_count": sum(1 for row in rows if str(row.get("error_type")) in {"provider_rate_limit", "provider_unavailable", "provider_timeout", "provider_auth", "provider_bad_response", "provider_unknown"}),
                "scored_count": len(scored),
                "avg_legacy_required_fact_recall_scored_only": round(sum(float(row["legacy_required_fact_recall"]) for row in scored) / max(1, len(scored)), 4),
                "avg_source_fact_id_recall_scored_only": round(sum(float(row["source_fact_id_recall"]) for row in scored) / max(1, len(scored)), 4),
                "avg_answer_value_fact_recall_scored_only": round(sum(float(row["answer_value_fact_recall"]) for row in scored) / max(1, len(scored)), 4),
                "avg_numeric_correctness_scored_only": round(sum(float(row["numeric_correctness"]) for row in scored) / max(1, len(scored)), 4),
                "avg_answer_correctness_scored_only": round(sum(float(row["answer_correctness"]) for row in scored) / max(1, len(scored)), 4),
                "final_answer_calculation_mismatch_count": sum(1 for row in rows if "final_answer_calculation_mismatch" in row["warning_categories"]),
                "ambiguous_formula_count": sum(1 for row in rows if "ambiguous_formula" in row["warning_categories"]),
                "missing_prior_period_count": sum(1 for row in rows if "missing_prior_period" in row["warning_categories"]),
            }
        )

    write_jsonl(RUN_DIR / "revised_case_results.jsonl", revised_rows)
    write_csv(
        RUN_DIR / "revised_method_summary.csv",
        [
            "method",
            "attempt_count",
            "provider_success_count",
            "provider_error_count",
            "scored_count",
            "avg_legacy_required_fact_recall_scored_only",
            "avg_source_fact_id_recall_scored_only",
            "avg_answer_value_fact_recall_scored_only",
            "avg_numeric_correctness_scored_only",
            "avg_answer_correctness_scored_only",
            "final_answer_calculation_mismatch_count",
            "ambiguous_formula_count",
            "missing_prior_period_count",
        ],
        summary_rows,
    )
    write_jsonl(RUN_DIR / "revised_scorer_diagnostics.jsonl", diagnostics)
    md = [
        "# Revised Scorer Diagnostics",
        "",
        f"Generated: {now()}",
        "",
        "| Case | Method | Legacy Recall | Source ID Recall | Answer Value Recall | Numeric OK | Warnings |",
        "|---|---|---:|---:|---:|---|---|",
    ]
    for item in diagnostics:
        md.append(
            f"| `{item['case_id']}` | `{item['method']}` | {item['legacy_required_fact_recall']} | "
            f"{item['source_fact_id_recall']} | {item['answer_value_fact_recall']} | "
            f"`{str(item['numeric_correctness_revised']).lower()}` | `{','.join(item['warning_categories'])}` |"
        )
    write_text(RUN_DIR / "revised_scorer_diagnostics.md", "\n".join(md))

    report = [
        "# Revised Round 3 Ready Partial Eval Report",
        "",
        f"Generated: {now()}",
        "",
        "## Scope",
        "",
        "- Source run: `outputs/round3_eval_runs/ready_partial_real_20260527_093341`",
        "- Recomputed from existing raw outputs only: true",
        "- Model API called: false",
        "- Neo4j write performed: false",
        "- KG patch applied: false",
        "- Full eval executed: false",
        "",
        "## Method Summary",
        "",
        "| Method | Attempts | Provider Success | Legacy Recall | Source ID Recall | Answer Value Recall | Numeric OK | Answer OK | Mismatch | Ambiguous |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for row in summary_rows:
        report.append(
            f"| `{row['method']}` | {row['attempt_count']} | {row['provider_success_count']} | "
            f"{row['avg_legacy_required_fact_recall_scored_only']} | {row['avg_source_fact_id_recall_scored_only']} | "
            f"{row['avg_answer_value_fact_recall_scored_only']} | {row['avg_numeric_correctness_scored_only']} | "
            f"{row['avg_answer_correctness_scored_only']} | {row['final_answer_calculation_mismatch_count']} | {row['ambiguous_formula_count']} |"
        )
    report.extend(
        [
            "",
            "## Interpretation",
            "",
            "The revised report separates source fact ID recall from answer value fact recall. Source fact IDs are diagnostic only because graph/hybrid methods can see IDs while vector/gold methods generally cannot.",
            "Unit scaling for USD_millions and percentage/ratio comparisons is handled in the revised scorer.",
            "Rows with formula ambiguity or final-answer/calculation contradictions should not be used for final method-comparison claims without human review.",
        ]
    )
    write_text(RUN_DIR / "revised_report.md", "\n".join(report))
    write_text(
        RUN_DIR / "scoring_repair_notes.md",
        f"""# Scoring Repair Notes

Generated: {now()}

- Recomputed from existing raw outputs only: true
- Raw inputs modified: false
- Raw outputs modified: false
- Unit-aware numeric normalization implemented for USD_millions and percentages.
- Fact recall split into source_fact_id_recall and answer_value_fact_recall.
- Legacy required_fact_recall preserved as legacy_required_fact_recall.
- Added final_answer_calculation_mismatch, ambiguous_formula, missing_prior_period, gold_context_fact_id_unavailable, and scorer_unit_scaling_adjusted warnings.
- Ambiguous cases flagged: {len(ambiguous_cases)}
- Unit-scaling adjusted fact matches observed: {unit_adjusted_count}
""",
    )
    return {
        "ambiguous_cases": sorted(ambiguous_cases),
        "unit_adjusted_count": unit_adjusted_count,
        "summary": summary_rows,
    }


if __name__ == "__main__":
    result = repair()
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
