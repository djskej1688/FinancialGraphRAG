"""Rebuild a typed LLM-extracted financial KG in Neo4j.

This script reads the 25 Track B shadow-overlay evaluation cases, extracts
structured financial observations from each evidence text with OpenAI, and
writes only new LLM-prefixed labels/relationships to Neo4j.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[1]
CASES_PATH = (
    REPO_ROOT
    / "outputs"
    / "round3_dual_track_eval_prep"
    / "track_b_shadow_overlay"
    / "shadow_overlay_eval_ready_cases.jsonl"
)
OUT_DIR = REPO_ROOT / "outputs" / "kg_rebuild_llm_ie"
RESULTS_DIR = OUT_DIR / "extraction_results"
STATE_PATH = OUT_DIR / "state.json"
EXTRACTION_ERRORS_PATH = OUT_DIR / "extraction_errors.jsonl"
NEO4J_LOG_PATH = OUT_DIR / "neo4j_write_log.jsonl"
ENV_FILES = (REPO_ROOT / ".env", REPO_ROOT.parent / ".env")

KG_BATCH = "kg-llm-ie-v1-20260528"
MODEL = "gpt-4o-mini"
ALLOWED_UNITS = {
    "currency_millions",
    "currency_billions",
    "count",
    "percentage",
    "ratio",
    "currency_per_share",
}
BAD_METRIC_FRAGMENTS = (
    "at_the_",
    "during_the_",
    "_of_",
    "march_",
    "june_",
    "december_",
    "generation_x",
    "birth_year",
)
HEADER_METRIC_FRAGMENTS = (
    "consolidated_",
    "statement",
    "for_the_year",
    "years_ended",
    "dollars_in",
    "in_millions",
    "per_share_data",
    "shares_used_in",
)
NUMBER_RE = re.compile(r"\(?\$?\s*-?\d[\d,]*(?:\.\d+)?\)?")

SYSTEM_PROMPT = """You are a financial information extraction expert. Given a financial text excerpt from a 10-K SEC filing, extract structured financial observations.

For each numeric value mentioned, extract:
- The exact metric it represents (use standard financial terminology)
- The fiscal year it applies to
- The numeric value
- The unit (currency_millions, count, percentage, ratio)
- The exact quote from the text containing this value

Rules:
- Only extract metrics with actual numeric values
- metric_canonical must be a real financial metric name in snake_case (e.g., net_revenue, total_employees, gross_profit_margin)
- Do NOT create metric names from sentence fragments like "at_the_end_of", "during_the_first_quarter_of", etc.
- year must be an integer (e.g., 2023); if the text says "fiscal year ended June 28, 2024", use 2024
- If the same metric appears across multiple years in a table, extract one observation per year
- evidence_quote must be a substring that actually appears in the input text
- Maximum 20 observations per case
"""

RESPONSE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "observations": {
            "type": "array",
            "maxItems": 20,
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": [
                    "metric_canonical",
                    "metric_display",
                    "year",
                    "value",
                    "unit",
                    "evidence_quote",
                ],
                "properties": {
                    "metric_canonical": {"type": "string"},
                    "metric_display": {"type": "string"},
                    "year": {"type": "integer"},
                    "value": {"type": "number"},
                    "unit": {
                        "type": "string",
                        "enum": sorted(ALLOWED_UNITS),
                    },
                    "evidence_quote": {"type": "string"},
                },
            },
        }
    },
    "required": ["observations"],
}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def write_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    tmp_path.write_text(
        json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    tmp_path.replace(path)


def append_jsonl(path: Path, row: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8", newline="\n") as f:
        f.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")


def read_neo4j_env_files() -> dict[str, str]:
    values: dict[str, str] = {}
    for path in ENV_FILES:
        if not path.exists():
            continue
        for raw in path.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            key = key.strip()
            if key.startswith("NEO4J_"):
                values[key] = value.strip().strip("\"'")
    return values


def effective_neo4j_env() -> dict[str, str]:
    file_values = read_neo4j_env_files()
    values = {
        "NEO4J_URI": os.environ.get("NEO4J_URI") or file_values.get("NEO4J_URI", ""),
        "NEO4J_USERNAME": os.environ.get("NEO4J_USERNAME")
        or os.environ.get("NEO4J_USER")
        or file_values.get("NEO4J_USERNAME")
        or file_values.get("NEO4J_USER", ""),
        "NEO4J_PASSWORD": os.environ.get("NEO4J_PASSWORD")
        or file_values.get("NEO4J_PASSWORD", ""),
        "NEO4J_DATABASE": os.environ.get("NEO4J_DATABASE")
        or file_values.get("NEO4J_DATABASE")
        or "neo4j",
    }
    return values


def require_openai_api_key() -> str:
    key = os.environ.get("OPENAI_API_KEY", "")
    if not key:
        raise RuntimeError(
            "OPENAI_API_KEY is missing from the process environment. "
            "It is intentionally not loaded from .env."
        )
    return key


def blank_state(cases_total: int) -> dict[str, Any]:
    return {
        "phase": "extracting",
        "kg_batch": KG_BATCH,
        "cases_total": cases_total,
        "cases_processed": 0,
        "cases_succeeded": [],
        "cases_failed": [],
        "nodes_created": {
            "companies": 0,
            "metrics": 0,
            "observations": 0,
            "years": 0,
            "dataset_cases": 0,
        },
        "relationships_created": 0,
        "openai_calls": 0,
        "openai_tokens_used": 0,
        "started_at": utc_now(),
        "completed_at": None,
        "last_error": None,
    }


def load_state(cases_total: int, resume: bool) -> dict[str, Any]:
    if resume and STATE_PATH.exists():
        state = json.loads(STATE_PATH.read_text(encoding="utf-8"))
        state.setdefault("kg_batch", KG_BATCH)
        state.setdefault("cases_total", cases_total)
        state.setdefault("cases_processed", 0)
        state.setdefault("cases_succeeded", [])
        state.setdefault("cases_failed", [])
        state.setdefault("nodes_created", blank_state(cases_total)["nodes_created"])
        state.setdefault("relationships_created", 0)
        state.setdefault("openai_calls", 0)
        state.setdefault("openai_tokens_used", 0)
        if not state.get("started_at"):
            state["started_at"] = utc_now()
        state.setdefault("completed_at", None)
        state.setdefault("last_error", None)
        if state.get("phase") in {"done", "partial", "failed"}:
            state["phase"] = "extracting"
            state["completed_at"] = None
            state["codex_handoff_message"] = None
        return state
    return blank_state(cases_total)


def normalize_metric_name(value: Any) -> str:
    text = str(value or "").strip().lower()
    text = text.replace("&", " and ")
    text = re.sub(r"[^a-z0-9]+", "_", text)
    text = re.sub(r"_+", "_", text).strip("_")
    return text


def display_metric_name(metric_canonical: str, metric_display: Any) -> str:
    display = str(metric_display or "").strip()
    if display:
        return display
    return metric_canonical.replace("_", " ").title()


def split_from_case(case: dict[str, Any]) -> str:
    split = str(case.get("split") or "").strip()
    if split:
        return split
    parts = str(case.get("case_id", "")).split("_")
    return "_".join(parts[:2]) if len(parts) >= 2 else ""


def observation_id(case_id: str, ticker: str, canonical_name: str, year: int) -> str:
    return f"{case_id}___{ticker}_{canonical_name}_{year}"


def validate_observations(
    case: dict[str, Any], raw_observations: list[dict[str, Any]]
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    valid: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    seen_obs_ids: set[str] = set()
    case_id = str(case["case_id"])
    ticker = str(case["ticker"]).strip().upper()

    for index, raw in enumerate(raw_observations[:20]):
        reason = ""
        canonical_name = normalize_metric_name(raw.get("metric_canonical"))
        try:
            year = int(raw.get("year"))
        except (TypeError, ValueError):
            year = -1
        try:
            value = float(raw.get("value"))
        except (TypeError, ValueError):
            value = math.nan
        unit = str(raw.get("unit") or "").strip()
        quote = str(raw.get("evidence_quote") or "").strip()

        if any(fragment in canonical_name for fragment in BAD_METRIC_FRAGMENTS):
            reason = "metric_artifact"
        elif len(canonical_name) < 4:
            reason = "metric_too_short"
        elif year < 2000 or year > 2030:
            reason = "year_out_of_range"
        elif value is None or not math.isfinite(value):
            reason = "invalid_value"
        elif unit not in ALLOWED_UNITS:
            reason = "invalid_unit"
        elif not quote:
            reason = "empty_evidence_quote"

        obs_id = observation_id(case_id, ticker, canonical_name, year)
        if not reason and obs_id in seen_obs_ids:
            reason = "duplicate_observation_id"

        if reason:
            skipped.append({"index": index, "reason": reason, "raw": raw})
            continue

        seen_obs_ids.add(obs_id)
        valid.append(
            {
                "obs_id": obs_id,
                "ticker": ticker,
                "metric_canonical": canonical_name,
                "metric_display": display_metric_name(
                    canonical_name, raw.get("metric_display")
                ),
                "year": year,
                "value": value,
                "unit": unit,
                "evidence_quote": quote,
                "case_id": case_id,
                "evidence_quote_verified": quote in str(case.get("evidence_text") or ""),
            }
        )

    return valid, skipped


def parse_number_token(token: str) -> float | None:
    cleaned = token.strip()
    if not cleaned or not re.search(r"\d", cleaned):
        return None
    negative = "(" in cleaned or cleaned.startswith("-")
    cleaned = cleaned.replace("$", "").replace(",", "")
    cleaned = cleaned.replace("(", "").replace(")", "").strip()
    try:
        value = float(cleaned)
    except ValueError:
        return None
    return -value if negative else value


def line_numbers(line: str) -> list[float]:
    values: list[float] = []
    for match in NUMBER_RE.findall(line):
        value = parse_number_token(match)
        if value is not None:
            values.append(value)
    return values


def metric_text_before_numbers(line: str) -> str:
    match = NUMBER_RE.search(line)
    text = line[: match.start()] if match else line
    return text.strip(" \t:-")


def is_probable_metric(canonical_name: str) -> bool:
    if len(canonical_name) < 4:
        return False
    if any(fragment in canonical_name for fragment in BAD_METRIC_FRAGMENTS):
        return False
    if any(fragment in canonical_name for fragment in HEADER_METRIC_FRAGMENTS):
        return False
    return bool(re.search(r"[a-z]", canonical_name))


def infer_unit(metric_canonical: str, evidence_text: str) -> str:
    if "per_share" in metric_canonical or metric_canonical.endswith("_eps"):
        return "currency_per_share"
    if "margin" in metric_canonical or "rate" in metric_canonical or "percentage" in metric_canonical:
        return "percentage"
    if "share" in metric_canonical and "per_share" not in metric_canonical:
        return "count"
    lower = evidence_text.lower()
    if "in billions" in lower or "(in billions" in lower:
        return "currency_billions"
    return "currency_millions"


def year_order_for_case(case: dict[str, Any]) -> list[int]:
    years = []
    for year in case.get("years") or []:
        try:
            years.append(int(year))
        except (TypeError, ValueError):
            continue
    if not years:
        years = [int(y) for y in re.findall(r"\b20\d{2}\b", str(case.get("evidence_text") or ""))]
    return sorted(set(years), reverse=True)[:4]


def table_fallback_observations(
    case: dict[str, Any], existing_observations: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    evidence_text = str(case.get("evidence_text") or "")
    year_order = year_order_for_case(case)
    if not evidence_text or not year_order:
        return []

    existing_ids = {obs["obs_id"] for obs in existing_observations}
    ticker = str(case["ticker"]).strip().upper()
    case_id = str(case["case_id"])
    raw_lines = evidence_text.splitlines()
    stripped_lines = [line.strip() for line in raw_lines]
    additions: list[dict[str, Any]] = []

    for index, line in enumerate(stripped_lines):
        if not line or not re.search(r"[A-Za-z]", line):
            continue

        metric_text = metric_text_before_numbers(line)
        canonical_name = normalize_metric_name(metric_text)
        if not is_probable_metric(canonical_name):
            continue

        values = line_numbers(line)
        quote_lines = [line]
        if len(values) < len(year_order):
            lookahead = index + 1
            while lookahead < len(stripped_lines):
                next_line = stripped_lines[lookahead]
                if next_line and re.search(r"[A-Za-z]", next_line):
                    break
                quote_lines.append(next_line)
                values.extend(line_numbers(next_line))
                if len(values) >= len(year_order):
                    break
                lookahead += 1

        if len(values) < len(year_order):
            continue

        unit = infer_unit(canonical_name, evidence_text)
        display_name = display_metric_name(canonical_name, metric_text)
        quote = "\n".join(q for q in quote_lines if q).strip() or line
        for year, value in zip(year_order, values[: len(year_order)]):
            obs_id = observation_id(case_id, ticker, canonical_name, year)
            if obs_id in existing_ids:
                continue
            existing_ids.add(obs_id)
            additions.append(
                {
                    "obs_id": obs_id,
                    "ticker": ticker,
                    "metric_canonical": canonical_name,
                    "metric_display": display_name,
                    "year": year,
                    "value": float(value),
                    "unit": unit,
                    "evidence_quote": quote,
                    "case_id": case_id,
                    "evidence_quote_verified": quote in evidence_text,
                    "extraction_method": "table_fallback",
                }
            )

    return additions


def extraction_prompt(case: dict[str, Any]) -> str:
    return "\n".join(
        [
            f"case_id: {case.get('case_id')}",
            f"ticker: {case.get('ticker')}",
            f"company: {case.get('company')}",
            f"years: {case.get('years')}",
            "",
            "Extract observations from this evidence_text only:",
            str(case.get("evidence_text") or ""),
        ]
    )


def extract_case(client: Any, case: dict[str, Any]) -> tuple[dict[str, Any], int]:
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": extraction_prompt(case)},
    ]
    try:
        response = client.chat.completions.create(
            model=MODEL,
            messages=messages,
            response_format={
                "type": "json_schema",
                "json_schema": {
                    "name": "financial_observations",
                    "strict": True,
                    "schema": RESPONSE_SCHEMA,
                },
            },
            temperature=0,
            max_tokens=3500,
        )
    except Exception as exc:  # noqa: BLE001
        message = str(exc).lower()
        schema_error = "response_format" in message or "json_schema" in message
        if not schema_error:
            raise
        fallback_messages = [
            {
                "role": "system",
                "content": SYSTEM_PROMPT
                + "\nReturn only valid JSON with a top-level observations array.",
            },
            messages[1],
        ]
        response = client.chat.completions.create(
            model=MODEL,
            messages=fallback_messages,
            response_format={"type": "json_object"},
            temperature=0,
            max_tokens=3500,
        )
    content = response.choices[0].message.content or ""
    tokens = int(getattr(getattr(response, "usage", None), "total_tokens", 0) or 0)
    try:
        parsed = json.loads(content)
    except json.JSONDecodeError as exc:
        append_jsonl(
            EXTRACTION_ERRORS_PATH,
            {
                "case_id": case.get("case_id"),
                "error_type": "json_parse_error",
                "error": str(exc),
                "raw_response": content,
                "created_at": utc_now(),
            },
        )
        raise
    return parsed, tokens


def create_neo4j_driver(env: dict[str, str]) -> Any:
    missing = [key for key in ("NEO4J_URI", "NEO4J_USERNAME", "NEO4J_PASSWORD") if not env.get(key)]
    if missing:
        raise RuntimeError(f"Missing Neo4j configuration: {', '.join(missing)}")
    from neo4j import GraphDatabase

    return GraphDatabase.driver(
        env["NEO4J_URI"], auth=(env["NEO4J_USERNAME"], env["NEO4J_PASSWORD"])
    )


def merge_case_company(session: Any, case: dict[str, Any], created_at: str) -> dict[str, int]:
    cypher = """
MERGE (c:LLMCompany {ticker: $ticker})
ON CREATE SET c.name = $name, c.kg_batch = $kg_batch, c.created_at = $created_at
MERGE (dc:LLMDatasetCase {case_id: $case_id})
ON CREATE SET dc.ticker = $ticker, dc.split = $split, dc.kg_batch = $kg_batch, dc.created_at = $created_at
"""
    result = session.run(
        cypher,
        {
            "ticker": str(case["ticker"]).strip().upper(),
            "name": str(case.get("company") or "").strip(),
            "case_id": str(case["case_id"]),
            "split": split_from_case(case),
            "kg_batch": KG_BATCH,
            "created_at": created_at,
        },
    )
    counters = result.consume().counters
    return {
        "nodes_created": int(counters.nodes_created),
        "relationships_created": int(counters.relationships_created),
    }


def merge_observation(session: Any, case: dict[str, Any], obs: dict[str, Any], created_at: str) -> dict[str, int]:
    cypher = """
MERGE (c:LLMCompany {ticker: $ticker})
ON CREATE SET c.name = $name, c.kg_batch = $kg_batch, c.created_at = $created_at

MERGE (m:LLMFinancialMetric {canonical_name: $canonical_name})
ON CREATE SET m.display_name = $display_name, m.unit = $unit, m.kg_batch = $kg_batch, m.created_at = $created_at

MERGE (yr:LLMFiscalYear {year: $year})
ON CREATE SET yr.kg_batch = $kg_batch, yr.created_at = $created_at

MERGE (obs:LLMObservation {obs_id: $obs_id})
ON CREATE SET obs.ticker = $ticker, obs.metric_canonical = $canonical_name,
              obs.year = $year, obs.value = $value, obs.unit = $unit,
              obs.evidence_quote = $evidence_quote, obs.case_id = $case_id,
              obs.kg_batch = $kg_batch, obs.created_at = $created_at

MERGE (dc:LLMDatasetCase {case_id: $case_id})
ON CREATE SET dc.ticker = $ticker, dc.split = $split, dc.kg_batch = $kg_batch, dc.created_at = $created_at

MERGE (obs)-[:LLM_MENTIONS_COMPANY]->(c)
MERGE (obs)-[:LLM_OBSERVES_METRIC]->(m)
MERGE (obs)-[:LLM_OBSERVED_IN_YEAR]->(yr)
MERGE (dc)-[:LLM_HAS_OBSERVATION]->(obs)
"""
    params = {
        "ticker": str(case["ticker"]).strip().upper(),
        "name": str(case.get("company") or "").strip(),
        "canonical_name": obs["metric_canonical"],
        "display_name": obs["metric_display"],
        "unit": obs["unit"],
        "year": obs["year"],
        "obs_id": obs["obs_id"],
        "value": obs["value"],
        "evidence_quote": obs["evidence_quote"],
        "case_id": str(case["case_id"]),
        "split": split_from_case(case),
        "kg_batch": KG_BATCH,
        "created_at": created_at,
    }
    result = session.run(cypher, params)
    counters = result.consume().counters
    return {
        "nodes_created": int(counters.nodes_created),
        "relationships_created": int(counters.relationships_created),
    }


def log_merge_rows(case: dict[str, Any], observations: list[dict[str, Any]], status: str) -> None:
    base = {
        "case_id": case.get("case_id"),
        "ticker": str(case.get("ticker") or "").strip().upper(),
        "kg_batch": KG_BATCH,
        "status": status,
        "created_at": utc_now(),
    }
    append_jsonl(NEO4J_LOG_PATH, {**base, "merge": "LLMCompany", "key": base["ticker"]})
    append_jsonl(
        NEO4J_LOG_PATH,
        {**base, "merge": "LLMDatasetCase", "key": str(case.get("case_id") or "")},
    )
    for obs in observations:
        rows = [
            ("LLMFinancialMetric", obs["metric_canonical"]),
            ("LLMFiscalYear", obs["year"]),
            ("LLMObservation", obs["obs_id"]),
            ("LLM_MENTIONS_COMPANY", obs["obs_id"]),
            ("LLM_OBSERVES_METRIC", obs["obs_id"]),
            ("LLM_OBSERVED_IN_YEAR", obs["obs_id"]),
            ("LLM_HAS_OBSERVATION", obs["obs_id"]),
        ]
        for merge_name, key in rows:
            append_jsonl(
                NEO4J_LOG_PATH,
                {
                    **base,
                    "merge": merge_name,
                    "key": str(key),
                    "obs_id": obs["obs_id"],
                },
            )


def add_node_counts(state: dict[str, Any], before: dict[str, int], after: dict[str, int]) -> None:
    delta = {
        "companies": after.get("companies", 0) - before.get("companies", 0),
        "metrics": after.get("metrics", 0) - before.get("metrics", 0),
        "observations": after.get("observations", 0) - before.get("observations", 0),
        "years": after.get("years", 0) - before.get("years", 0),
        "dataset_cases": after.get("dataset_cases", 0) - before.get("dataset_cases", 0),
    }
    for key, value in delta.items():
        if value > 0:
            state["nodes_created"][key] = int(state["nodes_created"].get(key, 0)) + value


def count_batch_nodes(session: Any) -> dict[str, int]:
    labels = {
        "companies": "LLMCompany",
        "metrics": "LLMFinancialMetric",
        "observations": "LLMObservation",
        "years": "LLMFiscalYear",
        "dataset_cases": "LLMDatasetCase",
    }
    counts: dict[str, int] = {}
    for key, label in labels.items():
        record = session.run(
            f"MATCH (n:{label}) WHERE n.kg_batch = $kg_batch RETURN count(n) AS count",
            {"kg_batch": KG_BATCH},
        ).single()
        counts[key] = int(record["count"] if record else 0)
    return counts


def count_batch_relationships(session: Any) -> int:
    cypher = """
MATCH (obs:LLMObservation)
WHERE obs.kg_batch = $kg_batch
OPTIONAL MATCH (obs)-[r1:LLM_MENTIONS_COMPANY]->(:LLMCompany)
OPTIONAL MATCH (obs)-[r2:LLM_OBSERVES_METRIC]->(:LLMFinancialMetric)
OPTIONAL MATCH (obs)-[r3:LLM_OBSERVED_IN_YEAR]->(:LLMFiscalYear)
OPTIONAL MATCH (:LLMDatasetCase)-[r4:LLM_HAS_OBSERVATION]->(obs)
RETURN count(r1) + count(r2) + count(r3) + count(r4) AS count
"""
    record = session.run(cypher, {"kg_batch": KG_BATCH}).single()
    return int(record["count"] if record else 0)


def write_case_result(
    case: dict[str, Any],
    raw_output: dict[str, Any],
    valid_observations: list[dict[str, Any]],
    skipped_observations: list[dict[str, Any]],
    tokens_used: int,
) -> None:
    write_json(
        RESULTS_DIR / f"{case['case_id']}.json",
        {
            "case_id": case["case_id"],
            "ticker": case["ticker"],
            "company": case.get("company"),
            "kg_batch": KG_BATCH,
            "model": MODEL,
            "tokens_used": tokens_used,
            "raw_output": raw_output,
            "valid_observations": valid_observations,
            "skipped_observations": skipped_observations,
            "created_at": utc_now(),
        },
    )


def mark_success(state: dict[str, Any], case_id: str) -> None:
    if case_id not in state["cases_succeeded"]:
        state["cases_succeeded"].append(case_id)
    if case_id in state["cases_failed"]:
        state["cases_failed"].remove(case_id)
    state["cases_processed"] = len(set(state["cases_succeeded"]) | set(state["cases_failed"]))
    state["last_error"] = None


def mark_failure(state: dict[str, Any], case_id: str, error: str) -> None:
    if case_id not in state["cases_failed"]:
        state["cases_failed"].append(case_id)
    state["cases_processed"] = len(set(state["cases_succeeded"]) | set(state["cases_failed"]))
    state["last_error"] = {"case_id": case_id, "error": error, "created_at": utc_now()}


def finalize_state(state: dict[str, Any]) -> None:
    succeeded = len(set(state.get("cases_succeeded", [])))
    if succeeded >= 20:
        state["phase"] = "done"
    elif succeeded >= 10:
        state["phase"] = "partial"
    else:
        state["phase"] = "failed"
    state["completed_at"] = utc_now()
    observations = int(state.get("nodes_created", {}).get("observations", 0))
    state["codex_handoff_message"] = (
        f"KG rebuild complete. {state.get('cases_processed', 0)} cases processed, "
        f"{observations} observations extracted. Run scripts/verify_llm_ie_kg.py to verify."
    )


def write_observations_to_neo4j(
    driver: Any,
    database: str,
    case: dict[str, Any],
    observations: list[dict[str, Any]],
) -> None:
    created_at = utc_now()
    last_error: Exception | None = None

    for attempt in range(2):
        try:
            with driver.session(database=database) as session:
                merge_case_company(session, case, created_at)
                for obs in observations:
                    merge_observation(session, case, obs, created_at)
            log_merge_rows(case, observations, "ok")
            return
        except Exception as exc:  # noqa: BLE001
            last_error = exc
            if attempt == 0:
                time.sleep(2)
                continue

    log_merge_rows(case, observations, "failed")
    raise RuntimeError(f"Neo4j write failed after retry: {last_error}") from last_error


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--cases-path",
        type=Path,
        default=CASES_PATH,
        help="JSONL case file to process.",
    )
    parser.add_argument(
        "--resume",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Resume from outputs/kg_rebuild_llm_ie/state.json when present.",
    )
    parser.add_argument(
        "--retry-failed",
        action="store_true",
        help="Process cases listed in cases_failed again.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Optional maximum number of cases to process this run.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    cases_path = args.cases_path if args.cases_path.is_absolute() else REPO_ROOT / args.cases_path
    cases = read_jsonl(cases_path)
    state = load_state(len(cases), args.resume)
    state["phase"] = "extracting"
    write_json(STATE_PATH, state)

    try:
        from openai import OpenAI
    except ImportError as exc:
        raise RuntimeError("Missing required package: openai") from exc

    openai_key = require_openai_api_key()
    client = OpenAI(api_key=openai_key)
    neo4j_env = effective_neo4j_env()
    driver = create_neo4j_driver(neo4j_env)

    processed_this_run = 0
    succeeded = set(state.get("cases_succeeded", []))
    failed = set(state.get("cases_failed", []))

    try:
        for case in cases:
            case_id = str(case["case_id"])
            if case_id in succeeded:
                continue
            if case_id in failed and not args.retry_failed:
                continue
            if args.limit is not None and processed_this_run >= args.limit:
                break

            try:
                raw_output, tokens_used = extract_case(client, case)
                state["openai_calls"] = int(state.get("openai_calls", 0)) + 1
                state["openai_tokens_used"] = int(state.get("openai_tokens_used", 0)) + tokens_used

                raw_observations = raw_output.get("observations", [])
                if not isinstance(raw_observations, list):
                    raise ValueError("OpenAI response observations field is not a list")
                valid_observations, skipped_observations = validate_observations(
                    case, raw_observations
                )
                fallback_observations = table_fallback_observations(
                    case, valid_observations
                )
                valid_observations.extend(fallback_observations)
                write_case_result(
                    case,
                    raw_output,
                    valid_observations,
                    skipped_observations,
                    tokens_used,
                )

                with driver.session(database=neo4j_env["NEO4J_DATABASE"]) as session:
                    before_counts = count_batch_nodes(session)
                    before_rels = count_batch_relationships(session)

                write_observations_to_neo4j(
                    driver,
                    neo4j_env["NEO4J_DATABASE"],
                    case,
                    valid_observations,
                )

                with driver.session(database=neo4j_env["NEO4J_DATABASE"]) as session:
                    after_counts = count_batch_nodes(session)
                    after_rels = count_batch_relationships(session)

                add_node_counts(state, before_counts, after_counts)
                rel_delta = after_rels - before_rels
                if rel_delta > 0:
                    state["relationships_created"] = (
                        int(state.get("relationships_created", 0)) + rel_delta
                    )
                mark_success(state, case_id)
                succeeded.add(case_id)
                failed.discard(case_id)

            except Exception as exc:  # noqa: BLE001
                append_jsonl(
                    EXTRACTION_ERRORS_PATH,
                    {
                        "case_id": case_id,
                        "error_type": type(exc).__name__,
                        "error": str(exc),
                        "created_at": utc_now(),
                    },
                )
                mark_failure(state, case_id, str(exc))
                failed.add(case_id)

            processed_this_run += 1
            write_json(STATE_PATH, state)
            print(
                f"[{utc_now()}] processed {state['cases_processed']}/{state['cases_total']} "
                f"case_id={case_id} succeeded={len(state['cases_succeeded'])} "
                f"failed={len(state['cases_failed'])}",
                flush=True,
            )

    finally:
        driver.close()

    all_done = state["cases_processed"] >= state["cases_total"]
    no_more_selected = args.limit is None or processed_this_run < args.limit
    if all_done or no_more_selected:
        driver = create_neo4j_driver(neo4j_env)
        try:
            with driver.session(database=neo4j_env["NEO4J_DATABASE"]) as session:
                state["nodes_created"] = count_batch_nodes(session)
                state["relationships_created"] = count_batch_relationships(session)
        finally:
            driver.close()
        finalize_state(state)
        write_json(STATE_PATH, state)
    return 0 if state["phase"] in {"extracting", "done", "partial"} else 1


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        print("Interrupted.", file=sys.stderr)
        raise SystemExit(130)
