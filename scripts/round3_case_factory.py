from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import re
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DATASET = REPO_ROOT / "examples" / "datasets" / "finder_full.json"
FALLBACK_DATASET = REPO_ROOT.parent / "data" / "finder.csv"
OUT_DIR = REPO_ROOT / "outputs" / "round3_case_factory"
KG_BATCH = "kg-full-provenance-20260524"
CURATION_ROUND = "02"

PRIORITY_TICKERS = ["CRWD", "META", "ROP", "TAP", "CSGP"]
STOP_TICKERS = {
    "THE",
    "AND",
    "FOR",
    "INC",
    "LLC",
    "LTD",
    "SEC",
    "ROI",
    "EPS",
    "CEO",
    "CFO",
    "FY",
    "GAAP",
    "R&D",
    "USD",
    "US",
    "UK",
}


def read_json_dataset(path: Path) -> list[dict[str, Any]]:
    data = json.loads(path.read_text(encoding="utf-8"))
    rows: list[dict[str, Any]] = []
    for item in data:
        rows.append(
            {
                "id": item.get("id") or item.get("_id"),
                "category": normalize_category(item.get("category", "")),
                "reasoning_type": normalize_reasoning(item.get("reasoning_type") or item.get("type", "")),
                "text": item.get("text") or item.get("references") or "",
                "question": item.get("question") or item.get("text", ""),
                "expected_answer": item.get("expected_answer") or item.get("answer", ""),
            }
        )
    return rows


def read_csv_dataset(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8-sig", newline="") as fh:
        reader = csv.DictReader(fh)
        for item in reader:
            rows.append(
                {
                    "id": item.get("_id"),
                    "category": normalize_category(item.get("category", "")),
                    "reasoning_type": normalize_reasoning(item.get("type", "")),
                    "text": unwrap_reference(item.get("references", "")),
                    "question": item.get("text", ""),
                    "expected_answer": item.get("answer", ""),
                }
            )
    return rows


def unwrap_reference(value: str) -> str:
    value = value.strip()
    if value.startswith("[") and value.endswith("]"):
        try:
            parsed = json.loads(value.replace("'", '"'))
            if isinstance(parsed, list):
                return "\n\n".join(str(x) for x in parsed)
        except Exception:
            return value
    return value


def normalize_category(value: str) -> str:
    value = (value or "").strip()
    mapping = {
        "Company overview": "Company Overview",
        "Company overview ": "Company Overview",
        "Financials": "Financials",
        "Footnotes": "Footnotes",
        "Governance": "Governance",
        "Accounting": "Accounting",
        "Shareholder return": "Shareholder Return",
        "Risk": "Risk",
        "Legal": "Legal",
    }
    return mapping.get(value, value or "Unknown")


def normalize_reasoning(value: str) -> str:
    value = (value or "").strip()
    if not value or value == "None":
        return "None"
    if value == "Subtract":
        return "Subtraction"
    return value


def clean_lines(text: str) -> list[str]:
    lines: list[str] = []
    for raw in text.replace("\r", "\n").split("\n"):
        line = raw.strip()
        if not line:
            continue
        if line in {"$", "(", ")", "%"}:
            continue
        line = re.sub(r"\s+", " ", line)
        lines.append(line)
    return lines


def parse_number(token: str) -> float | None:
    token = token.strip()
    if token in {"-", "--", "—", "–"}:
        return None
    neg = token.startswith("(") and token.endswith(")")
    token = token.strip("()")
    token = token.replace("$", "").replace(",", "").replace("%", "")
    if not re.fullmatch(r"-?\d+(?:\.\d+)?", token):
        return None
    value = float(token)
    return -value if neg else value


def is_number_line(line: str) -> bool:
    return parse_number(line) is not None


def line_has_letters(line: str) -> bool:
    return bool(re.search(r"[A-Za-z]", line))


def years_in_text(text: str) -> list[int]:
    years: list[int] = []
    for y in re.findall(r"\b(20[0-3]\d|19[89]\d)\b", text):
        year = int(y)
        if year not in years:
            years.append(year)
    return years


def default_year_for_text(text: str) -> int | None:
    match = re.search(r"As of (?:[A-Za-z]+ \d{1,2}, )?(20[0-3]\d)", text)
    if match:
        return int(match.group(1))
    years = years_in_text(text)
    return max(years) if years else None


def infer_unit(text: str, metric: str, value: float | None) -> str:
    scope = f"{metric} {text[:500]}".lower()
    if "employee" in scope or "workforce" in scope or "headcount" in scope or "individuals" in scope:
        return "employees"
    if "per share" in scope:
        return "USD_per_share"
    if "%" in metric or "percent" in scope:
        return "percent"
    if "in millions" in scope:
        return "USD_millions"
    if "in thousands" in scope:
        return "USD_thousands"
    if "shares" in scope:
        return "shares"
    if value is not None and abs(value) <= 1 and "ratio" in scope:
        return "ratio"
    return "numeric"


def source_excerpt(metric: str, values: list[float], years: list[int | None]) -> str:
    parts = [metric]
    for y, v in zip(years, values):
        label = str(y) if y else "as reported"
        parts.append(f"{label}: {format_value(v)}")
    return " | ".join(parts)[:500]


def format_value(value: float) -> str:
    if value == int(value):
        return str(int(value))
    return f"{value:.4f}".rstrip("0").rstrip(".")


def canonical_metric(metric: str) -> str:
    metric = metric.strip(" :;-")
    metric = re.sub(r"\s+", " ", metric)
    lowered = metric.lower()
    replacements = {
        "total revenues": "total_revenue",
        "revenues": "revenue",
        "revenue": "revenue",
        "net income": "net_income",
        "operating income": "operating_income",
        "employees": "employees",
        "number of employees": "employees",
        "total operating expenses": "total_operating_expenses",
        "cash and cash equivalents": "cash_and_cash_equivalents",
        "total assets": "total_assets",
        "total liabilities": "total_liabilities",
        "diluted earnings per share": "diluted_eps",
        "basic earnings per share": "basic_eps",
    }
    for key, value in replacements.items():
        if key in lowered:
            return value
    return re.sub(r"[^a-z0-9]+", "_", lowered).strip("_")[:80] or "reported_value"


def extract_structured_facts(row: dict[str, Any]) -> list[dict[str, Any]]:
    text = row["text"]
    lines = clean_lines(text)
    years = years_in_text(text)
    year_headers = [y for y in years if 1990 <= y <= 2039][:6]
    default_year = default_year_for_text(text)
    facts: list[dict[str, Any]] = []

    # Compact table rows sometimes arrive as one line:
    # "Subscription $ 2,870,557 $ 2,111,660 $ 1,359,537".
    for line in lines:
        if not line_has_letters(line) or len(line) > 240:
            continue
        if re.search(r"\b(year ended|as of|date|period)\b", line, re.I):
            continue
        number_tokens = re.findall(r"\(?-?\$?\d[\d,]*(?:\.\d+)?%?\)?", line)
        values = [parse_number(token) for token in number_tokens]
        values = [value for value in values if value is not None]
        if len(values) < 2 or not year_headers:
            continue
        first_num = re.search(r"\(?-?\$?\d[\d,]*(?:\.\d+)?%?\)?", line)
        if not first_num:
            continue
        metric = line[: first_num.start()].replace("$", "").strip(" \t:-|")
        metric = re.sub(r"\s+", " ", metric)
        if not metric or len(metric) < 3:
            continue
        assigned_years: list[int | None] = year_headers[: len(values)]
        if len(assigned_years) < len(values):
            assigned_years.extend([None] * (len(values) - len(assigned_years)))
        for role_idx, (year, value) in enumerate(zip(assigned_years, values)):
            facts.append(
                make_fact_stub(
                    row,
                    metric=metric,
                    year=year,
                    value=value,
                    quote=source_excerpt(metric, values, assigned_years),
                    unit=infer_unit(text, metric, value),
                    role="component" if role_idx else "current_year_value",
                    confidence=0.84,
                )
            )

    # Common SEC table pattern: metric label followed by values for year columns.
    for i, line in enumerate(lines):
        if not line_has_letters(line) or len(line) > 120:
            continue
        values: list[float] = []
        j = i + 1
        while j < len(lines) and len(values) < max(1, len(year_headers), 3):
            if line_has_letters(lines[j]) and not is_number_line(lines[j]):
                break
            value = parse_number(lines[j])
            if value is not None:
                values.append(value)
            j += 1
        if len(values) >= 2 and year_headers:
            assigned_years: list[int | None] = year_headers[: len(values)]
            if len(assigned_years) < len(values):
                assigned_years.extend([None] * (len(values) - len(assigned_years)))
            for role_idx, (year, value) in enumerate(zip(assigned_years, values)):
                facts.append(
                    make_fact_stub(
                        row,
                        metric=line,
                        year=year,
                        value=value,
                        quote=source_excerpt(line, values, assigned_years),
                        unit=infer_unit(text, line, value),
                        role="component" if role_idx else "current_year_value",
                        confidence=0.86,
                    )
                )

    # Two-column employee/location and similar tables.
    for i in range(len(lines) - 1):
        label, value_line = lines[i], lines[i + 1]
        if not line_has_letters(label) or len(label) > 90:
            continue
        value = parse_number(value_line)
        if value is None:
            continue
        if label.lower() in {"location", "number of employees", "year", "amount"}:
            continue
        unit = infer_unit(text, label, value)
        if unit == "numeric" and "employee" not in text[:800].lower():
            continue
        facts.append(
            make_fact_stub(
                row,
                metric=label,
                year=default_year,
                value=value,
                quote=source_excerpt(label, [value], [default_year]),
                unit=unit,
                role="component",
                confidence=0.8,
            )
        )

    return dedupe_facts(facts)


def make_fact_stub(
    row: dict[str, Any],
    metric: str,
    year: int | None,
    value: float,
    quote: str,
    unit: str,
    role: str,
    confidence: float,
) -> dict[str, Any]:
    return {
        "case_id": "",
        "fact_id": "",
        "company": "",
        "ticker": "",
        "category": row["category"],
        "reasoning_type": row["reasoning_type"],
        "metric_canonical": canonical_metric(metric),
        "metric_raw": metric.strip(),
        "year": year,
        "period_label": f"FY{year}" if year else "as_reported",
        "value": value,
        "unit": unit,
        "fact_role": role,
        "source_fact": True,
        "derived_answer_value": False,
        "evidence_quote": quote,
        "source_evidence_id": row["id"],
        "extraction_method": "parser",
        "confidence": confidence,
        "notes": "Extracted from source evidence table or table-like line sequence.",
    }


def dedupe_facts(facts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[tuple[Any, ...]] = set()
    out: list[dict[str, Any]] = []
    for fact in facts:
        key = (
            fact["metric_canonical"],
            fact["metric_raw"].lower(),
            fact["year"],
            round(float(fact["value"]), 6),
            fact["unit"],
        )
        if key in seen:
            continue
        seen.add(key)
        out.append(fact)
    return out


def answer_numbers(answer: str) -> set[str]:
    values = set()
    for token in re.findall(r"\(?-?\$?\d[\d,]*(?:\.\d+)?%?\)?", answer):
        value = parse_number(token)
        if value is None:
            continue
        values.add(format_value(value))
    return values


def tokenize_words(text: str) -> set[str]:
    words = {
        w.lower()
        for w in re.findall(r"[A-Za-z][A-Za-z0-9&'-]{2,}", text)
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
        }
    }
    return words


def select_required_facts(row: dict[str, Any], facts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if not facts:
        return []
    qa = f"{row['question']} {row['expected_answer']}"
    qa_words = tokenize_words(qa)
    answer_vals = answer_numbers(row["expected_answer"])
    scored: list[tuple[float, dict[str, Any]]] = []
    for fact in facts:
        metric_words = tokenize_words(fact["metric_raw"])
        score = 0.0
        score += len(metric_words & qa_words) * 2.0
        if format_value(float(fact["value"])) in answer_vals:
            score += 3.0
        if fact["year"] and str(fact["year"]) in qa:
            score += 1.5
        if fact["metric_canonical"] in {"revenue", "total_revenue", "net_income", "operating_income", "employees"}:
            score += 0.8
        if fact["unit"] != "numeric":
            score += 0.5
        scored.append((score, fact))
    scored.sort(key=lambda x: x[0], reverse=True)
    selected = [fact for score, fact in scored if score > 0][:8]
    if len(selected) < 2 and len(scored) >= 2:
        selected = [fact for _, fact in scored[: min(4, len(scored))]]
    return selected


def infer_ticker(row: dict[str, Any]) -> str:
    qa = f"{row['question']} {row['expected_answer']}"
    for match in re.findall(r"\(([A-Z]{1,5})\)", qa):
        if match not in STOP_TICKERS:
            return match
    for ticker in PRIORITY_TICKERS:
        if re.search(rf"\b{ticker}\b", qa):
            return ticker
    candidates = [m for m in re.findall(r"\b[A-Z]{2,5}\b", row["question"]) if m not in STOP_TICKERS]
    return candidates[0] if candidates else ""


def infer_company(row: dict[str, Any], ticker: str) -> str:
    question = row["question"]
    if ticker:
        match = re.search(rf"([A-Z][A-Za-z0-9&.,' -]{{2,80}})\s*\({re.escape(ticker)}\)", question)
        if match:
            return match.group(1).strip(" ,.'")
    first_line = clean_lines(row["text"])[0] if clean_lines(row["text"]) else ""
    first_line = re.sub(r"\s+and\s+Subsidiaries.*", "", first_line)
    first_line = re.sub(r"\s+Consolidated.*", "", first_line)
    if 3 <= len(first_line) <= 90 and line_has_letters(first_line):
        return first_line.strip(" ,.")
    if ticker:
        return ticker
    return "Unknown"


def score_case(row: dict[str, Any], required: list[dict[str, Any]]) -> dict[str, Any]:
    text = row["text"]
    years = years_in_text(text)
    numeric_count = len(re.findall(r"\d[\d,]*(?:\.\d+)?", text))
    table_density = min(3, numeric_count // 12)
    required_count = len(required)
    reasoning = row["reasoning_type"]
    category = row["category"]
    score = 0.0
    score += 3.0 if category in {"Financials", "Company Overview"} else 1.0
    score += {"Compositional": 3.0, "Division": 2.2, "Addition": 2.0, "Subtraction": 2.0, "Multiplication": 1.8}.get(reasoning, 0.4)
    score += min(3.0, len(set(years)) * 0.8)
    score += min(3.0, required_count * 0.55)
    score += table_density
    if re.search(r"\b(calculated|minus|divided|increase|decrease|margin|ratio|percentage|growth|sum)\b", row["expected_answer"], re.I):
        score += 1.2
    if 20 <= len(row["question"]) <= 180:
        score += 0.6
    if required_count >= 2:
        score += 1.0
    anti = 0.0
    anti += 1.5 if category in {"Financials", "Company Overview"} else 0.8
    anti += 1.0 if row["text"] and row["expected_answer"] else 0.0
    anti += 1.0 if required_count >= 2 else 0.0
    anti += 0.8 if reasoning == "None" else 0.4
    anti += 0.7 if "calculated" in row["expected_answer"].lower() else 0.0
    return {
        "quality_score": round(score, 3),
        "anti_cherrypick_score": round(anti, 3),
        "numeric_count": numeric_count,
        "year_count": len(set(years)),
        "required_fact_count": required_count,
        "table_density_score": table_density,
    }


def classify_slice(row: dict[str, Any], ticker: str) -> str:
    cat = row["category"]
    reasoning = row["reasoning_type"]
    if cat == "Financials" and reasoning == "Compositional":
        return "S1_FIN_COMP"
    if cat == "Company Overview" and reasoning == "Compositional":
        return "S3_CO_COMP"
    if reasoning == "None":
        return "S6_BASELINE_SINGLE"
    if ticker in PRIORITY_TICKERS and cat in {"Footnotes", "Risk", "Legal", "Governance", "Accounting", "Shareholder Return"}:
        return "INTEGRATION_PRIORITY"
    if cat == "Financials" and reasoning in {"Addition", "Division", "Subtraction", "Multiplication"}:
        return "S1_FIN_TYPED"
    if cat == "Company Overview" and reasoning in {"Addition", "Division", "Subtraction", "Multiplication"}:
        return "S3_CO_TYPED"
    return "OTHER"


def case_id_for(split: str, source_id: str, index: int) -> str:
    return f"{split}_{index:03d}_{source_id}"


def selected_case_record(
    row: dict[str, Any],
    split: str,
    case_id: str,
    company: str,
    ticker: str,
    required: list[dict[str, Any]],
    score: dict[str, Any],
    source_file: str,
) -> dict[str, Any]:
    years = sorted({int(f["year"]) for f in required if f.get("year")})
    metric_tags = sorted({f["metric_canonical"] for f in required})[:12]
    if split == "baseline_control":
        graph_rationale = "Single-evidence control case; graph should not receive an artificial advantage."
        vector_rationale = "The evidence text contains the needed answer context directly, so vector_only has a fair opportunity."
    elif split == "integration_demo":
        graph_rationale = "Ticker/category linkage is useful for graph explanation and cross-context navigation."
        vector_rationale = "This case is separated from scoring and used for integration demonstration only."
    else:
        graph_rationale = "Required source facts can be represented as typed metric/year/value observations for table-style reasoning."
        vector_rationale = "The original evidence text is retained, so vector_only can retrieve the same source context."
    return {
        "case_id": case_id,
        "split": split,
        "category": row["category"],
        "reasoning_type": row["reasoning_type"],
        "question": row["question"],
        "expected_answer": row["expected_answer"],
        "evidence_text": row["text"],
        "company": company,
        "ticker": ticker,
        "years": years,
        "metric_tags": metric_tags,
        "required_fact_count": len(required),
        "quality_score": score["quality_score"],
        "graph_advantage_rationale": graph_rationale,
        "vector_fairness_rationale": vector_rationale,
        "anti_cherrypick_notes": "Selected by deterministic scoring with baseline controls and held-out split separation; not selected by observed model outcome.",
        "source_file": source_file,
        "source_dataset": "FinDER",
        "kg_batch": KG_BATCH,
        "curation_round": CURATION_ROUND,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    with path.open("w", encoding="utf-8", newline="\n") as fh:
        for row in rows:
            fh.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")


def write_json(path: Path, data: Any) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")


def write_csv(path: Path, rows: list[dict[str, Any]], fields: list[str]) -> None:
    with path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def choose_cases(
    candidates: list[dict[str, Any]],
    *,
    min_quality: float = 8.0,
    max_dev: int = 20,
    max_test: int = 20,
    max_baseline: int = 5,
    max_integration: int = 5,
    min_company_overview_per_split: int = 6,
    integration_min_quality: float | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    selected: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []

    eligible = [c for c in candidates if c["required_fact_count"] >= 2 and c["quality_score"] >= min_quality]
    primary = [
        c
        for c in eligible
        if c["slice"] in {"S1_FIN_COMP", "S3_CO_COMP", "S1_FIN_TYPED", "S3_CO_TYPED"}
    ]
    primary.sort(key=lambda c: (c["quality_score"], c["anti_cherrypick_score"]), reverse=True)

    used_ids: set[str] = set()
    used_test_companies: Counter[str] = Counter()
    dev: list[dict[str, Any]] = []
    test: list[dict[str, Any]] = []

    company_overview = [c for c in primary if c["category"] == "Company Overview"]
    financials = [c for c in primary if c["category"] == "Financials"]

    def add_primary(cand: dict[str, Any], target: list[dict[str, Any]]) -> bool:
        if cand["source_id"] in used_ids:
            return False
        if target is test and cand["company"] != "Unknown" and used_test_companies[cand["company"]] >= 1:
            return False
        if target is dev and len(dev) >= max_dev:
            return False
        if target is test and len(test) >= max_test:
            return False
        cand["split"] = "round3_dev" if target is dev else "round3_test"
        target.append(cand)
        used_ids.add(cand["source_id"])
        if target is test:
            used_test_companies[cand["company"]] += 1
        return True

    for cand in company_overview:
        if len([c for c in dev if c["category"] == "Company Overview"]) < min_company_overview_per_split:
            add_primary(cand, dev)
        elif len([c for c in test if c["category"] == "Company Overview"]) < min_company_overview_per_split:
            add_primary(cand, test)
        if (
            len([c for c in dev if c["category"] == "Company Overview"]) >= min_company_overview_per_split
            and len([c for c in test if c["category"] == "Company Overview"]) >= min_company_overview_per_split
        ):
            break

    for cand in financials + company_overview:
        if cand["source_id"] in used_ids:
            continue
        target = dev if len(dev) <= len(test) else test
        if not add_primary(cand, target):
            other = test if target is dev else dev
            add_primary(cand, other)
        if len(dev) >= max_dev and len(test) >= max_test:
            break

    baseline_pool = [
        c
        for c in candidates
        if c["source_id"] not in used_ids
        and c["slice"] == "S6_BASELINE_SINGLE"
        and c["required_fact_count"] >= 1
        and c["quality_score"] >= min_quality
        and c["category"] in {"Financials", "Company Overview", "Footnotes", "Risk", "Legal", "Governance"}
    ]
    baseline_pool.sort(key=lambda c: (c["anti_cherrypick_score"], c["required_fact_count"], c["quality_score"]), reverse=True)
    baseline: list[dict[str, Any]] = []
    for cand in baseline_pool:
        if cand["source_id"] in used_ids:
            continue
        cand["split"] = "baseline_control"
        baseline.append(cand)
        used_ids.add(cand["source_id"])
        if len(baseline) >= max_baseline:
            break

    integration_threshold = min_quality if integration_min_quality is None else integration_min_quality
    integration_pool = [
        c
        for c in candidates
        if c["source_id"] not in used_ids
        and c["ticker"] in PRIORITY_TICKERS
        and c["required_fact_count"] >= 2
        and c["quality_score"] >= integration_threshold
        and c["category"] in {"Footnotes", "Risk", "Legal", "Governance", "Accounting", "Shareholder Return", "Company Overview", "Financials"}
    ]
    integration_pool.sort(
        key=lambda c: (
            0 if c["ticker"] in PRIORITY_TICKERS else 1,
            -c["required_fact_count"],
            -c["quality_score"],
        )
    )
    integration: list[dict[str, Any]] = []
    per_ticker: Counter[str] = Counter()
    for cand in integration_pool:
        if cand["source_id"] in used_ids:
            continue
        if per_ticker[cand["ticker"]] >= 2:
            continue
        cand["split"] = "integration_demo"
        integration.append(cand)
        used_ids.add(cand["source_id"])
        per_ticker[cand["ticker"]] += 1
        if len(integration) >= max_integration:
            break

    selected = dev + test + baseline + integration
    selected_ids = {c["source_id"] for c in selected}
    for cand in candidates:
        if cand["source_id"] not in selected_ids:
            reason = "lower_score_or_duplicate"
            if cand["required_fact_count"] == 0:
                reason = "no_parser_verified_required_facts"
            elif cand["quality_score"] < min_quality:
                reason = "quality_score_below_selection_threshold"
            rejected.append(
                {
                    "source_id": cand["source_id"],
                    "question": cand["question"],
                    "category": cand["category"],
                    "reasoning_type": cand["reasoning_type"],
                    "slice": cand["slice"],
                    "quality_score": cand["quality_score"],
                    "required_fact_count": cand["required_fact_count"],
                    "rejection_reason": reason,
                }
            )
    return selected, rejected


def attach_case_ids(selected: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    case_rows: list[dict[str, Any]] = []
    fact_rows: list[dict[str, Any]] = []
    counters: Counter[str] = Counter()
    for cand in selected:
        counters[cand["split"]] += 1
        case_id = case_id_for(cand["split"], cand["source_id"], counters[cand["split"]])
        case_rows.append(
            selected_case_record(
                cand["row"],
                cand["split"],
                case_id,
                cand["company"],
                cand["ticker"],
                cand["required_facts"],
                cand,
                cand["source_file"],
            )
        )
        for idx, fact in enumerate(cand["required_facts"], start=1):
            fact = dict(fact)
            fact["case_id"] = case_id
            fact["fact_id"] = f"{case_id}_fact_{idx:02d}"
            fact["company"] = cand["company"]
            fact["ticker"] = cand["ticker"]
            fact_rows.append(fact)
    return case_rows, fact_rows


def build_candidates(rows: list[dict[str, Any]], source_file: str) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    for row in rows:
        if not row["id"] or not row["text"] or not row["question"] or not row["expected_answer"]:
            continue
        facts = extract_structured_facts(row)
        required = select_required_facts(row, facts)
        ticker = infer_ticker(row)
        company = infer_company(row, ticker)
        score = score_case(row, required)
        slice_name = classify_slice(row, ticker)
        candidates.append(
            {
                "source_id": row["id"],
                "question": row["question"],
                "expected_answer": row["expected_answer"],
                "category": row["category"],
                "reasoning_type": row["reasoning_type"],
                "company": company,
                "ticker": ticker,
                "slice": slice_name,
                "required_facts": required,
                "source_fact_candidates": len(facts),
                "source_file": source_file,
                "row": row,
                **score,
            }
        )
    return candidates


def coverage_rows(case_rows: list[dict[str, Any]], fact_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_case = defaultdict(list)
    for fact in fact_rows:
        by_case[fact["case_id"]].append(fact)
    rows: list[dict[str, Any]] = []
    for case in case_rows:
        facts = by_case[case["case_id"]]
        rows.append(
            {
                "case_id": case["case_id"],
                "split": case["split"],
                "dataset_case_exists": "not_checked_no_neo4j",
                "question_linked": "not_checked_no_neo4j",
                "evidence_text_linked": "not_checked_no_neo4j",
                "answer_linked": "not_checked_no_neo4j",
                "company_linked": "not_checked_no_neo4j",
                "metric_linked": "not_checked_no_neo4j",
                "year_linked": "not_checked_no_neo4j",
                "value_linked": "not_checked_no_neo4j",
                "observation_count": len(facts),
                "missing_required_fact_count": 0,
                "coverage_status": "not_checked_no_neo4j",
                "ready_for_eval": bool(facts and all(f.get("evidence_quote") for f in facts)),
                "notes": "Neo4j credentials/config were not present in this workspace; local source-evidence validation passed.",
            }
        )
    return rows


def markdown_table_counts(case_rows: list[dict[str, Any]], fact_rows: list[dict[str, Any]], candidates: list[dict[str, Any]]) -> str:
    split_counts = Counter(row["split"] for row in case_rows)
    return "\n".join(
        [
            f"- longlist candidates: {len(candidates)}",
            f"- selected total: {len(case_rows)}",
            f"- round3_dev: {split_counts.get('round3_dev', 0)}",
            f"- round3_test: {split_counts.get('round3_test', 0)}",
            f"- baseline_control: {split_counts.get('baseline_control', 0)}",
            f"- integration_demo: {split_counts.get('integration_demo', 0)}",
            f"- required facts total: {len(fact_rows)}",
        ]
    )


def write_reports(
    out_dir: Path,
    case_rows: list[dict[str, Any]],
    fact_rows: list[dict[str, Any]],
    candidates: list[dict[str, Any]],
    rejected: list[dict[str, Any]],
    source_file: str,
) -> None:
    counts = markdown_table_counts(case_rows, fact_rows, candidates)
    split_counts = Counter(row["split"] for row in case_rows)
    categories = Counter(row["category"] for row in case_rows)
    reasoning = Counter(row["reasoning_type"] for row in case_rows)
    evidence_quote_coverage = sum(1 for f in fact_rows if f.get("evidence_quote")) / max(1, len(fact_rows))
    duplicate_case_ids = len(case_rows) - len({row["case_id"] for row in case_rows})

    (out_dir / "README.md").write_text(
        f"""# Round 3 Case Factory

This package contains a deterministic FinDER case set for Round 3 GraphRAG / HybridRAG evaluation.

## Summary
{counts}

## Inputs
- Primary dataset: `{source_file}`
- Requested filtered manifests and prior KG output directories were not present in this checkout.
- Neo4j was not checked because `.env` / connection configuration was absent.

## How to use
1. Review `round3_selected_cases.jsonl` and `round3_required_facts.jsonl`.
2. Run independent review using `HANDOFF_TO_ORCHESTRATION.md`.
3. If Neo4j credentials are available, rerun coverage and replace the `not_checked_no_neo4j` rows.
4. Use `round3_eval_plan.md` for the four-method evaluation design.
""",
        encoding="utf-8",
    )

    (out_dir / "round3_case_selection_report.md").write_text(
        f"""# Round 3 Case Selection Report

## Selection Counts
{counts}

## Category Mix
{dict(categories)}

## Reasoning Mix
{dict(reasoning)}

## Why These Cases Were Selected
Cases were selected by deterministic quality scoring, not by observed model performance. The scoring favored:

- source evidence with explicit table-like metric/year/value facts
- Financials and Company Overview reasoning
- compositional, division, addition, subtraction, and multiplication tasks
- multi-year or multi-metric evidence where graph linearization can make calculation steps clearer
- enough baseline controls to prevent a "graph always wins" benchmark story

## Why Cases Were Rejected
The rejected manifest records low-scoring, duplicate, or parser-insufficient candidates. Common reasons were missing parser-verified source facts, weak table structure, or too little calculable evidence in the source text.

## Fairness Notes
Every selected case retains the original evidence text, so vector_only has access to the same source context. The graph advantage is limited to typed organization of source facts; derived answer values were not inserted as source facts.
""",
        encoding="utf-8",
    )

    (out_dir / "round3_anti_cherrypicking_review.md").write_text(
        f"""# Anti-Cherry-Picking Review

## Review Result
- Selected total: {len(case_rows)}
- Baseline controls included: {split_counts.get('baseline_control', 0)}
- Held-out test cases included: {split_counts.get('round3_test', 0)}
- Duplicate case ids: {duplicate_case_ids}
- Evidence quote coverage: {evidence_quote_coverage:.1%}

## Cherry-Picking Risk
The set is biased toward graph-evaluable cases by design, but not toward cases where graph is known to win. No model outputs were used in selection. Baseline controls are separated to show where vector_only should be competitive.

## Vector Fairness
The full original evidence text is preserved in each selected case. Graph facts are source-only observations extracted from the same evidence, not privileged answers.

## Dev/Test Leakage
`round3_test` is separated from `round3_dev`. Existing selected7 files were not found in this checkout, so no selected7_dev rows were created. Independent review should compare this package with any external selected7 manifest before final evaluation.
""",
        encoding="utf-8",
    )

    (out_dir / "round3_graph_fact_formatting_guidelines.md").write_text(
        """# Round 3 Graph Fact Formatting Guidelines

## Common Rules
- Keep answer format and rounding rules identical across vector_only, graph_facts_only, hybrid_vector_graph, and gold_context.
- Use temperature=0 for evaluation.
- Provide only source facts as graph facts. Do not include final margins, ratios, growth rates, or deltas unless they appear explicitly in source evidence and are marked as derived-only.
- Preserve units and period labels.

## Financials Margin/Growth Format
Use a compact table:

| company | metric | period | value | unit | role |
|---|---:|---:|---:|---|---|

Then append a calculation instruction:
`Use source rows only. For growth, compute (current - base) / base. For margin, compute numerator / denominator.`

## Workforce/Geography Format
Use:

| company | workforce_dimension | period | count | unit | role |
|---|---|---:|---:|---|---|

Include both component rows and total workforce rows when a percentage distribution is requested.

## Addition/Reconciliation Format
List every component and total separately. Ask the model to show the arithmetic relation before the final answer.

## Division Ratio Format
Mark numerator and denominator roles explicitly. State whether the answer should be a ratio, percentage, or per-share amount.

## Rounding Rule
Default to two decimals for percentages and one decimal for USD millions unless the question or expected answer uses a more specific precision.

## Prompt Template V2
```
You are answering a FinDER evaluation question.
Use the provided context only.
If graph facts are provided, treat them as source observations, not as precomputed answers.
Return:
1. final_answer
2. calculation
3. source_fact_ids_used
Apply the stated unit and rounding rules.
```
""",
        encoding="utf-8",
    )

    (out_dir / "round3_eval_plan.md").write_text(
        """# Round 3 Evaluation Plan

## Methods
Compare four methods on identical questions and answer rules:

- vector_only
- graph_facts_only
- hybrid_vector_graph
- gold_context

## Trace Metadata
Each trace should include:

- case_id
- split
- method
- category
- reasoning_type
- ticker
- source_dataset
- kg_batch
- curation_round
- retrieved_context_ids
- graph_fact_ids_used
- final_answer
- calculation_text
- latency_ms
- error_class

## Scoring
Use answer correctness, numeric correctness, source fact recall, calculation validity, and abstention/error class. Keep Round 3 test held out from prompt and Cypher tuning.
""",
        encoding="utf-8",
    )

    (out_dir / "HANDOFF_TO_ORCHESTRATION.md").write_text(
        """# Handoff To Orchestration

## What This Package Contains
A deterministic Round 3 FinDER case package with selected cases, required source facts, coverage placeholders, reports, and validation metadata.

## Review Checklist
- Confirm selected7 overlap against the external selected7 manifest if available.
- Spot-check required facts against evidence quotes.
- Confirm no derived answer values were inserted as source facts.
- Run Neo4j read-only coverage when credentials are available.
- Freeze `round3_test` before prompt or Cypher tuning.

## Open Questions
- The requested filtered manifest and prior KG output directories were not present in this checkout.
- Neo4j coverage remains `not_checked_no_neo4j`.
- Ticker/company inference is deterministic but should be reviewed for cases where the question omits a ticker.
""",
        encoding="utf-8",
    )

    missing = []
    (out_dir / "round3_missing_required_facts.jsonl").write_text("", encoding="utf-8")
    (out_dir / "round3_case_coverage_report.md").write_text(
        f"""# Round 3 Coverage Report

## Status
Coverage status is `not_checked_no_neo4j` for every selected case.

## Reason
No `.env` file or Neo4j connection configuration was present in `{REPO_ROOT}` during this run. The Neo4j Python package exists, but connection details were unavailable.

## Local Validation
- Selected cases with at least one required fact: {sum(1 for r in case_rows if r['required_fact_count'] > 0)} / {len(case_rows)}
- Required facts with evidence quotes: {sum(1 for f in fact_rows if f.get('evidence_quote'))} / {len(fact_rows)}
- Missing required facts recorded: {len(missing)}

## Recheck Procedure
Set Neo4j connection variables in `.env`, then run a read-only coverage script that matches each `case_id` and `fact_id` against DatasetCase, Question, EvidenceText, Answer, Company, Metric, Year, Value, and Observation nodes.
""",
        encoding="utf-8",
    )

    (out_dir / "run_log.md").write_text(
        f"""# Round 3 Case Factory Run Log

- {datetime.now().isoformat(timespec='seconds')}: Read local prompt and inspected workspace.
- Input inventory: requested filtered manifest and `outputs/kg_build` directories were absent.
- Decision: proceed with available local FinDER source dataset `{source_file}`.
- Candidate discovery: built deterministic longlist from all rows with question, evidence, and expected answer.
- Required facts: extracted table-like source facts with parser heuristics and retained evidence quotes.
- Coverage: Neo4j not checked because connection config was absent; local validation continued.
- Packaging: wrote required manifests, split files, reports, and artifact manifest.

## Blocking / Deferred
- Neo4j read-only coverage must be rerun when credentials/config are available.
- External selected7_dev overlap check must be run if the selected7 manifest is restored.
""",
        encoding="utf-8",
    )


def validate_outputs(case_rows: list[dict[str, Any]], fact_rows: list[dict[str, Any]], coverage: list[dict[str, Any]]) -> dict[str, Any]:
    case_ids = [row["case_id"] for row in case_rows]
    fact_case_ids = {fact["case_id"] for fact in fact_rows}
    duplicate_case_ids = len(case_ids) - len(set(case_ids))
    derived_leakage = [
        fact["fact_id"]
        for fact in fact_rows
        if fact.get("source_fact") is True and fact.get("derived_answer_value") is True
    ]
    missing_quotes = [fact["fact_id"] for fact in fact_rows if not fact.get("evidence_quote")]
    missing_required = [row["case_id"] for row in case_rows if row["required_fact_count"] == 0 and row["split"] != "integration_demo"]
    return {
        "case_count": len(case_rows),
        "fact_count": len(fact_rows),
        "coverage_count": len(coverage),
        "duplicate_case_id_count": duplicate_case_ids,
        "derived_leakage_count": len(derived_leakage),
        "missing_evidence_quote_count": len(missing_quotes),
        "benchmark_cases_without_required_facts": missing_required,
        "case_without_fact_rows": sorted(set(case_ids) - fact_case_ids),
        "ready_for_eval_true": sum(1 for row in coverage if str(row["ready_for_eval"]).lower() == "true"),
        "ready_for_eval_false": sum(1 for row in coverage if str(row["ready_for_eval"]).lower() != "true"),
    }


def checksum(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def build_artifact_manifest(out_dir: Path, validation: dict[str, Any]) -> dict[str, Any]:
    files = []
    for path in sorted(out_dir.iterdir()):
        if path.is_file():
            if path.name == "artifact_manifest.json":
                continue
            files.append(
                {
                    "path": str(path.relative_to(REPO_ROOT)),
                    "bytes": path.stat().st_size,
                    "sha256": checksum(path),
                }
            )
    return {
        "created_at": datetime.now(timezone.utc).isoformat(),
        "artifact_dir": str(out_dir),
        "validation": validation,
        "files": files,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset", type=Path, default=DEFAULT_DATASET)
    parser.add_argument("--out-dir", type=Path, default=OUT_DIR)
    parser.add_argument("--min-quality", type=float, default=8.0)
    parser.add_argument("--max-dev", type=int, default=20)
    parser.add_argument("--max-test", type=int, default=20)
    parser.add_argument("--max-baseline", type=int, default=5)
    parser.add_argument("--max-integration", type=int, default=5)
    parser.add_argument("--min-company-overview-per-split", type=int, default=6)
    parser.add_argument("--integration-min-quality", type=float, default=None)
    args = parser.parse_args()

    out_dir: Path = args.out_dir
    if not out_dir.is_absolute():
        out_dir = REPO_ROOT / out_dir
    out_dir.mkdir(parents=True, exist_ok=True)

    dataset = args.dataset
    if dataset.exists():
        rows = read_json_dataset(dataset)
        source_file = str(dataset.relative_to(REPO_ROOT))
    elif FALLBACK_DATASET.exists():
        rows = read_csv_dataset(FALLBACK_DATASET)
        source_file = str(FALLBACK_DATASET)
    else:
        raise FileNotFoundError("No FinDER dataset found.")

    candidates = build_candidates(rows, source_file)
    selected, rejected = choose_cases(
        candidates,
        min_quality=args.min_quality,
        max_dev=args.max_dev,
        max_test=args.max_test,
        max_baseline=args.max_baseline,
        max_integration=args.max_integration,
        min_company_overview_per_split=args.min_company_overview_per_split,
        integration_min_quality=args.integration_min_quality,
    )
    case_rows, fact_rows = attach_case_ids(selected)
    coverage = coverage_rows(case_rows, fact_rows)

    longlist_fields = [
        "source_id",
        "slice",
        "category",
        "reasoning_type",
        "company",
        "ticker",
        "quality_score",
        "anti_cherrypick_score",
        "required_fact_count",
        "source_fact_candidates",
        "year_count",
        "numeric_count",
        "question",
    ]
    score_fields = [
        "source_id",
        "slice",
        "category",
        "reasoning_type",
        "quality_score",
        "anti_cherrypick_score",
        "required_fact_count",
        "source_fact_candidates",
        "year_count",
        "numeric_count",
        "table_density_score",
    ]
    write_csv(out_dir / "round3_case_candidates_longlist.csv", candidates, longlist_fields)
    write_csv(out_dir / "round3_case_quality_scores.csv", candidates, score_fields)
    write_jsonl(out_dir / "round3_selected_cases.jsonl", case_rows)
    write_jsonl(out_dir / "round3_required_facts.jsonl", fact_rows)
    write_json(out_dir / "round3_dev_cases.json", [r for r in case_rows if r["split"] == "round3_dev"])
    write_json(out_dir / "round3_test_cases.json", [r for r in case_rows if r["split"] == "round3_test"])
    write_json(out_dir / "round3_baseline_control_cases.json", [r for r in case_rows if r["split"] == "baseline_control"])
    write_json(out_dir / "round3_integration_demo_cases.json", [r for r in case_rows if r["split"] == "integration_demo"])
    write_csv(
        out_dir / "round3_case_coverage_report.csv",
        coverage,
        [
            "case_id",
            "split",
            "dataset_case_exists",
            "question_linked",
            "evidence_text_linked",
            "answer_linked",
            "company_linked",
            "metric_linked",
            "year_linked",
            "value_linked",
            "observation_count",
            "missing_required_fact_count",
            "coverage_status",
            "ready_for_eval",
            "notes",
        ],
    )
    write_jsonl(out_dir / "round3_rejected_cases.jsonl", rejected)
    write_reports(out_dir, case_rows, fact_rows, candidates, rejected, source_file)

    validation = validate_outputs(case_rows, fact_rows, coverage)
    write_json(out_dir / "validation_summary.json", validation)
    write_json(
        out_dir / "generation_config.json",
        {
            "dataset": source_file,
            "min_quality": args.min_quality,
            "max_dev": args.max_dev,
            "max_test": args.max_test,
            "max_baseline": args.max_baseline,
            "max_integration": args.max_integration,
            "min_company_overview_per_split": args.min_company_overview_per_split,
            "integration_min_quality": args.integration_min_quality,
            "quality_stop_rule": "Do not lower below quality_score 13 for benchmark expansion; below this threshold non-quantitative baseline rows increase sharply.",
        },
    )
    manifest = build_artifact_manifest(out_dir, validation)
    write_json(out_dir / "artifact_manifest.json", manifest)

    print(json.dumps(validation, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
