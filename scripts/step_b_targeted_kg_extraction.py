"""Step B targeted KG extraction for formula-required facts.

This builds a new Neo4j LLMObservation batch without modifying the existing
kg-llm-ie-v1-20260528 observations. OpenAI expected values are never included
in extraction prompts; they are used only for post-hoc validation.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import socket
import sys
import time
import urllib.error
import urllib.request
from collections import defaultdict
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

from neo4j import GraphDatabase


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

MODEL = "gpt-4o-mini"
TODAY = date.today().strftime("%Y%m%d")
DEFAULT_BATCH_ID = f"kg-targeted-ie-v1-{TODAY}"

OUT_DIR = ROOT / "outputs" / "step_b_targeted_kg"
STATE_PATH = OUT_DIR / "state.json"
EXTRACTION_TRACE = OUT_DIR / "extraction_trace.jsonl"
VALIDATION_REPORT = OUT_DIR / "validation_report.jsonl"
KG_WRITE_LOG = OUT_DIR / "kg_write_log.jsonl"
FAILED_EXTRACTIONS = OUT_DIR / "failed_extractions.jsonl"
SUMMARY_PATH = OUT_DIR / "step_b_summary.md"

TRACK_B = ROOT / "outputs" / "round3_dual_track_eval_prep" / "track_b_shadow_overlay"
CASES_PATH = TRACK_B / "shadow_overlay_eval_ready_cases.jsonl"
ROUND5_STATE = ROOT / "outputs" / "round5_diagnostic_eval" / "state.json"
ROUND5_TRACES_DEFAULT = ROOT / "outputs" / "round3_eval_runs" / "round5_diagnostic_20260528_213524" / "round5_traces.jsonl"
CLEAN = ROOT / "outputs" / "round3_eval_harness" / "formula_contract_v3_2_clean_dev" / "clean_dev_scorer_only_target_slot_contracts.jsonl"
TEST = ROOT / "outputs" / "round3_eval_harness" / "formula_contract_v3_2_test_split" / "test_scorer_contracts.jsonl"
DEV_BASELINE = ROOT / "outputs" / "round3_eval_harness" / "formula_contract_v3_2" / "dev_baseline_contracts" / "dev_baseline_scorer_only_target_slot_contracts.jsonl"
ENV_FILES = (ROOT / ".env", ROOT.parent / ".env")

PROVIDER_ERROR_TYPES = {
    "provider_rate_limit",
    "provider_unavailable",
    "provider_timeout",
    "provider_auth",
    "provider_bad_response",
    "provider_unknown",
}

BEST_EFFORT_CASES = [
    {"case_id_prefix": "round3_dev_007", "ticker": "KR", "formula_type": "operating_margin"},
    {"case_id_prefix": "round3_dev_009", "ticker": "LND", "formula_type": "operating_margin"},
    {"case_id_prefix": "round3_dev_012", "ticker": "MSFT", "formula_type": "operating_margin"},
    {"case_id_prefix": "round3_dev_014", "ticker": "FOXA", "formula_type": "ambiguous_manual_review"},
    {"case_id_prefix": "round3_dev_018", "ticker": "BW", "formula_type": "operating_margin"},
    {"case_id_prefix": "round3_dev_020", "ticker": "CARR", "formula_type": "gross_margin"},
]

BEST_EFFORT_METRICS = {
    "operating_margin": ["operating_income", "revenue", "net_sales", "total_revenues", "total_net_revenues"],
    "gross_margin": [
        "revenue",
        "net_sales",
        "total_revenues",
        "cost_of_sales",
        "cost_of_goods_sold",
        "cost_of_products_sold",
        "gross_profit",
    ],
    "ambiguous_manual_review": [
        "revenue",
        "operating_income",
        "net_income",
        "restructuring_charges",
        "depreciation_and_amortization",
    ],
}


class ProviderError(RuntimeError):
    def __init__(self, error_type: str, message: str) -> None:
        super().__init__(message)
        self.error_type = error_type


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def rel(path: Path) -> str:
    return path.resolve().relative_to(ROOT.resolve()).as_posix()


def sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n")
    tmp.replace(path)


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")


def append_jsonl(path: Path, row: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")


def env_from_files(name: str) -> str:
    if os.environ.get(name):
        return os.environ[name]
    for path in ENV_FILES:
        if not path.exists():
            continue
        for raw in path.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            if key.strip() == name:
                return value.strip().strip("\"'")
    return ""


def openai_api_key() -> str:
    key = os.environ.get("OPENAI_API_KEY", "")
    if not key:
        raise ProviderError("provider_auth", "OPENAI_API_KEY missing from process environment; not loaded from .env")
    return key


def neo4j_env() -> dict[str, str]:
    values = {
        "NEO4J_URI": env_from_files("NEO4J_URI") or "bolt://localhost:7687",
        "NEO4J_USERNAME": env_from_files("NEO4J_USERNAME") or env_from_files("NEO4J_USER") or "neo4j",
        "NEO4J_PASSWORD": env_from_files("NEO4J_PASSWORD"),
        "NEO4J_DATABASE": env_from_files("NEO4J_DATABASE") or "neo4j",
    }
    if not values["NEO4J_PASSWORD"]:
        raise RuntimeError("Missing NEO4J_PASSWORD in environment or .env")
    return values


def create_driver() -> Any:
    env = neo4j_env()
    return GraphDatabase.driver(env["NEO4J_URI"], auth=(env["NEO4J_USERNAME"], env["NEO4J_PASSWORD"]))


def classify_http(code: int) -> str:
    if code == 401:
        return "provider_auth"
    if code == 429:
        return "provider_rate_limit"
    if code == 503 or 500 <= code < 600:
        return "provider_unavailable"
    if 400 <= code < 500:
        return "provider_bad_response"
    return "provider_unknown"


def sanitize_error(exc: BaseException) -> str:
    if isinstance(exc, urllib.error.HTTPError):
        return f"HTTP Error {exc.code}: {exc.reason}"
    return str(exc).replace("\n", " ")[:300]


def extract_json_object(text: str) -> dict[str, Any]:
    stripped = text.strip()
    start = stripped.find("{")
    end = stripped.rfind("}")
    if start >= 0 and end > start:
        stripped = stripped[start : end + 1]
    return json.loads(stripped)


def call_openai_json(prompt: str) -> tuple[dict[str, Any], dict[str, Any]]:
    payload = {
        "model": MODEL,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0,
        "response_format": {"type": "json_object"},
    }
    request = urllib.request.Request(
        "https://api.openai.com/v1/chat/completions",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {openai_api_key()}"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=180) as response:
            data = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        raise ProviderError(classify_http(exc.code), sanitize_error(exc)) from exc
    except (socket.timeout, TimeoutError) as exc:
        raise ProviderError("provider_timeout", sanitize_error(exc)) from exc
    except urllib.error.URLError as exc:
        raise ProviderError("provider_unknown", sanitize_error(exc)) from exc
    except json.JSONDecodeError as exc:
        raise ProviderError("provider_bad_response", "provider returned invalid JSON envelope") from exc
    try:
        return extract_json_object(data["choices"][0]["message"]["content"]), data.get("usage", {})
    except Exception as exc:  # noqa: BLE001
        raise ProviderError("provider_bad_response", "provider returned non-JSON model output") from exc


def load_cases() -> dict[str, dict[str, Any]]:
    return {row["case_id"]: row for row in read_jsonl(CASES_PATH)}


def load_contract_rows(path: Path) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for row in read_jsonl(path):
        case_id = row.get("case_id")
        contract = row.get("scorer_only_target_slot_contract") or row
        if case_id:
            out[case_id] = contract
    return out


def load_primary_contracts() -> dict[str, dict[str, Any]]:
    contracts: dict[str, dict[str, Any]] = {}
    for path in [CLEAN, TEST]:
        for case_id, contract in load_contract_rows(path).items():
            source_facts = contract.get("source_fact_numbers", [])
            if source_facts:
                contracts[case_id] = contract
    return contracts


def find_case_by_prefix(cases: dict[str, dict[str, Any]], prefix: str) -> tuple[str, dict[str, Any]]:
    matches = [(case_id, case) for case_id, case in cases.items() if case_id.startswith(prefix)]
    if len(matches) != 1:
        raise RuntimeError(f"Expected one case for prefix {prefix}, found {len(matches)}")
    return matches[0]


def dedupe_targets(source_facts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    targets: list[dict[str, Any]] = []
    seen: set[tuple[str, int | str]] = set()
    for fact in source_facts:
        metric = str(fact.get("metric") or fact.get("metric_canonical") or "").strip()
        year = fact.get("year")
        if not metric or year in (None, ""):
            continue
        key = (metric, year)
        if key in seen:
            continue
        seen.add(key)
        targets.append({"metric": metric, "year": int(year), "unit_hint": str(fact.get("unit") or "unknown")})
    return targets


def build_extraction_prompt(ticker: str, evidence_text: str, extraction_targets: list[dict[str, Any]]) -> str:
    targets_text = "\n".join(
        f"  - metric: {target['metric']}, year: {target['year']}, unit expected: {target['unit_hint']}"
        for target in extraction_targets
    )
    return f"""You are a precise financial data extractor. Extract the following specific metrics from the 10-K text for company {ticker}.

METRICS TO EXTRACT:
{targets_text}

RULES:
- Extract ONLY the metrics listed above for ONLY the years listed.
- Use the exact numeric value as it appears in the financial statements; do not round.
- If a requested metric name is a canonical label, map it to the matching line item in the text, but output the requested metric name exactly.
- If a metric appears in multiple scopes, choose the line item that best matches the requested canonical metric and the question context.
- Provide a short evidence_quote copied from the text, max 120 characters.
- If a metric is genuinely not found in the text, include it with value null.
- Return JSON only. No explanation outside JSON.

OUTPUT FORMAT:
{{
  "extracted_facts": [
    {{
      "metric": "<metric_name_exactly_as_requested>",
      "year": 2023,
      "value": 123.45,
      "unit": "<unit_string>",
      "evidence_quote": "<short verbatim quote>"
    }}
  ]
}}

10-K TEXT:
{evidence_text}
"""


def normalize_extracted_facts(raw_facts: list[Any], targets: list[dict[str, Any]]) -> list[dict[str, Any]]:
    allowed = {(target["metric"], int(target["year"])): target for target in targets}
    out_by_key: dict[tuple[str, int], dict[str, Any]] = {}
    for item in raw_facts:
        if not isinstance(item, dict):
            continue
        metric = str(item.get("metric") or "").strip()
        try:
            year = int(item.get("year"))
        except (TypeError, ValueError):
            continue
        if (metric, year) not in allowed:
            continue
        value = item.get("value")
        if value is not None:
            try:
                value = float(value)
            except (TypeError, ValueError):
                value = None
        out_by_key[(metric, year)] = {
            "metric": metric,
            "year": year,
            "value": value,
            "unit": str(item.get("unit") or allowed[(metric, year)].get("unit_hint") or ""),
            "unit_hint": allowed[(metric, year)].get("unit_hint", ""),
            "evidence_quote": str(item.get("evidence_quote") or "")[:300],
        }
    for target in targets:
        key = (target["metric"], int(target["year"]))
        if key not in out_by_key:
            out_by_key[key] = {
                "metric": target["metric"],
                "year": int(target["year"]),
                "value": None,
                "unit": target.get("unit_hint", ""),
                "unit_hint": target.get("unit_hint", ""),
                "evidence_quote": "",
            }
    return [out_by_key[(target["metric"], int(target["year"]))] for target in targets]


def extract_facts_for_case(case: dict[str, Any], source_facts: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], dict[str, Any], str]:
    targets = dedupe_targets(source_facts)
    prompt = build_extraction_prompt(str(case["ticker"]).upper(), str(case.get("evidence_text", "")), targets)
    last_error: ProviderError | None = None
    for attempt in range(2):
        try:
            raw, usage = call_openai_json(prompt)
            return normalize_extracted_facts(raw.get("extracted_facts", []), targets), usage, sha(prompt)
        except ProviderError as exc:
            last_error = exc
            if attempt == 0 and exc.error_type in {"provider_bad_response", "provider_rate_limit", "provider_unavailable", "provider_timeout"}:
                time.sleep(2)
                continue
            raise
    assert last_error is not None
    raise last_error


def expected_fact_map(source_facts: list[dict[str, Any]]) -> dict[tuple[str, int], dict[str, Any]]:
    out: dict[tuple[str, int], dict[str, Any]] = {}
    for fact in source_facts:
        metric = str(fact.get("metric") or fact.get("metric_canonical") or "")
        try:
            year = int(fact.get("year"))
        except (TypeError, ValueError):
            continue
        out.setdefault((metric, year), fact)
    return out


def validate_extracted_fact(extracted: dict[str, Any], source_facts: list[dict[str, Any]]) -> dict[str, Any]:
    metric = str(extracted.get("metric") or "")
    try:
        year = int(extracted.get("year"))
    except (TypeError, ValueError):
        year = 0
    matching = expected_fact_map(source_facts).get((metric, year))
    if not matching:
        return {"status": "no_contract_fact", "match": None}
    expected = matching.get("value")
    value = extracted.get("value")
    if value is None:
        return {
            "status": "extraction_failed",
            "match": False,
            "expected": expected,
            "extracted": None,
            "source_fact_id": matching.get("fact_id", ""),
        }
    try:
        expected_float = float(expected)
        extracted_float = float(value)
    except (TypeError, ValueError):
        return {
            "status": "mismatch",
            "match": False,
            "expected": expected,
            "extracted": value,
            "source_fact_id": matching.get("fact_id", ""),
        }
    if abs(expected_float) > 1.0:
        delta_pct = abs(extracted_float - expected_float) / abs(expected_float) * 100
        match = delta_pct <= 1.0
        tolerance_pct = round(delta_pct, 4)
    else:
        delta_abs = abs(extracted_float - expected_float)
        match = delta_abs <= 0.05
        tolerance_pct = round(delta_abs, 4)
    return {
        "status": "matched" if match else "mismatch",
        "match": match,
        "expected": expected_float,
        "extracted": extracted_float,
        "tolerance_pct": tolerance_pct,
        "source_fact_id": matching.get("fact_id", ""),
    }


def source_fact_id_for(fact: dict[str, Any], source_facts: list[dict[str, Any]]) -> str:
    match = expected_fact_map(source_facts).get((str(fact.get("metric") or ""), int(fact.get("year") or 0)))
    return str(match.get("fact_id") or "") if match else ""


def write_facts_to_neo4j(
    driver: Any,
    case_id: str,
    ticker: str,
    company_name: str,
    extracted_facts: list[dict[str, Any]],
    batch_id: str,
    validation_results: dict[str, dict[str, Any]],
    source_facts: list[dict[str, Any]],
    validation_status_default: str,
) -> list[dict[str, Any]]:
    database = neo4j_env()["NEO4J_DATABASE"]
    rows: list[dict[str, Any]] = []
    with driver.session(database=database) as session:
        for fact in extracted_facts:
            metric = str(fact.get("metric") or "").strip()
            year = fact.get("year")
            value = fact.get("value")
            if not metric or year in (None, "") or value is None:
                continue
            try:
                year_int = int(year)
                value_float = float(value)
            except (TypeError, ValueError):
                continue
            key = f"{metric}_{year_int}"
            validation = validation_results.get(key, {})
            val_status = validation.get("status", validation_status_default)
            source_fact_id = validation.get("source_fact_id") or source_fact_id_for(fact, source_facts)
            obs_id = f"{batch_id}__{case_id}__{metric}__{year_int}"
            session.run(
                """
MERGE (c:LLMCompany {ticker: $ticker})
  ON CREATE SET c.name = $company_name
MERGE (yr:LLMFiscalYear {year: $year})
MERGE (m:LLMFinancialMetric {canonical_name: $canonical_name})
  ON CREATE SET m.display_name = $display_name
MERGE (obs:LLMObservation {obs_id: $obs_id})
SET obs.value = $value,
    obs.unit = $unit,
    obs.evidence_quote = $evidence_quote,
    obs.kg_batch = $batch_id,
    obs.extraction_method = 'targeted_gpt4o_mini',
    obs.validation_status = $validation_status,
    obs.source_fact_id = $source_fact_id,
    obs.case_id = $case_id
MERGE (obs)-[:LLM_MENTIONS_COMPANY]->(c)
MERGE (obs)-[:LLM_OBSERVES_METRIC]->(m)
MERGE (obs)-[:LLM_OBSERVED_IN_YEAR]->(yr)
""",
                ticker=ticker,
                company_name=company_name,
                year=year_int,
                canonical_name=metric,
                display_name=metric,
                obs_id=obs_id,
                value=value_float,
                unit=str(fact.get("unit") or fact.get("unit_hint") or ""),
                evidence_quote=str(fact.get("evidence_quote") or ""),
                batch_id=batch_id,
                validation_status=val_status,
                source_fact_id=source_fact_id,
                case_id=case_id,
            )
            rows.append(
                {
                    "case_id": case_id,
                    "ticker": ticker,
                    "obs_id": obs_id,
                    "metric": metric,
                    "year": year_int,
                    "value": value_float,
                    "validation_status": val_status,
                    "batch_id": batch_id,
                }
            )
    return rows


def initial_state(batch_id: str, resume: bool) -> dict[str, Any]:
    if resume and STATE_PATH.exists():
        existing = read_json(STATE_PATH)
        if existing.get("batch_id") == batch_id and existing.get("phase") != "done":
            return existing
    return {
        "phase": "running",
        "batch_id": batch_id,
        "cases_total": 25,
        "cases_processed_primary": 0,
        "cases_processed_best_effort": 0,
        "facts_extracted": 0,
        "facts_validated_ok": 0,
        "facts_validation_failed": 0,
        "facts_written_to_neo4j": 0,
        "primary_match_rate": 0.0,
        "test_match_rate": 0.0,
        "started_at": utc_now(),
        "completed_at": None,
        "warning": None,
        "codex_handoff_message": None,
        "output_dir": rel(OUT_DIR) + "/",
    }


def update_state_from_logs(state: dict[str, Any]) -> None:
    traces = read_jsonl(EXTRACTION_TRACE)
    validation = read_jsonl(VALIDATION_REPORT)
    writes = read_jsonl(KG_WRITE_LOG)
    state["cases_processed_primary"] = len({row["case_id"] for row in traces if row.get("mode") == "primary"})
    state["cases_processed_best_effort"] = len({row["case_id"] for row in traces if row.get("mode") == "best_effort"})
    state["facts_extracted"] = sum(int(row.get("n_extracted", 0)) for row in traces)
    state["facts_validated_ok"] = sum(1 for row in validation if row.get("match") is True)
    state["facts_validation_failed"] = sum(1 for row in validation if row.get("match") is False)
    state["facts_written_to_neo4j"] = sum(int(row.get("n_written", 0)) for row in writes)
    primary = [row for row in traces if row.get("mode") == "primary"]
    test = [row for row in primary if row.get("split") == "round3_test"]
    state["primary_match_rate"] = round(sum(float(row.get("match_rate", 0.0)) for row in primary) / len(primary), 4) if primary else 0.0
    state["test_match_rate"] = round(sum(float(row.get("match_rate", 0.0)) for row in test) / len(test), 4) if test else 0.0
    if state["test_match_rate"] < 0.5:
        state["warning"] = "test_match_rate_below_50_percent"
    else:
        state["warning"] = None
    write_json(STATE_PATH, state)


def case_completed(case_id: str, mode: str) -> bool:
    return any(row.get("case_id") == case_id and row.get("mode") == mode for row in read_jsonl(EXTRACTION_TRACE))


def validate_round5_done() -> None:
    if not ROUND5_STATE.exists():
        raise RuntimeError("Round 5 state.json missing")
    state = read_json(ROUND5_STATE)
    if state.get("phase") != "done":
        raise RuntimeError(f"Round 5 precondition failed: phase={state.get('phase')}")


def process_primary_case(
    driver: Any,
    state: dict[str, Any],
    batch_id: str,
    case_id: str,
    case: dict[str, Any],
    contract: dict[str, Any],
) -> None:
    source_facts = contract.get("source_fact_numbers", [])
    formula_type = str(contract.get("formula_type") or "unknown")
    extracted, usage, prompt_hash = extract_facts_for_case(case, source_facts)
    validation_results: dict[str, dict[str, Any]] = {}
    validation_rows: list[dict[str, Any]] = []
    ok = 0
    fail = 0
    for fact in extracted:
        result = validate_extracted_fact(fact, source_facts)
        key = f"{fact.get('metric')}_{fact.get('year')}"
        validation_results[key] = result
        if result.get("match") is True:
            ok += 1
        elif result.get("match") is False:
            fail += 1
        validation_rows.append(
            {
                "case_id": case_id,
                "ticker": case["ticker"],
                "split": case.get("split", ""),
                "formula_type": formula_type,
                "metric": fact.get("metric"),
                "year": fact.get("year"),
                "extracted_value": fact.get("value"),
                "unit": fact.get("unit"),
                "evidence_quote": fact.get("evidence_quote"),
                **result,
            }
        )
    for row in validation_rows:
        append_jsonl(VALIDATION_REPORT, row)
        if row.get("match") is False:
            append_jsonl(FAILED_EXTRACTIONS, row)
    write_rows = write_facts_to_neo4j(
        driver=driver,
        case_id=case_id,
        ticker=str(case["ticker"]).upper(),
        company_name=str(case.get("company") or ""),
        extracted_facts=extracted,
        batch_id=batch_id,
        validation_results=validation_results,
        source_facts=source_facts,
        validation_status_default="no_contract",
    )
    match_rate = round(ok / len(dedupe_targets(source_facts)), 4) if source_facts else 0.0
    append_jsonl(
        EXTRACTION_TRACE,
        {
            "case_id": case_id,
            "ticker": case["ticker"],
            "split": case.get("split", ""),
            "mode": "primary",
            "formula_type": formula_type,
            "n_raw_source_facts": len(source_facts),
            "n_target_facts": len(dedupe_targets(source_facts)),
            "n_extracted": sum(1 for fact in extracted if fact.get("value") is not None),
            "n_validated_ok": ok,
            "n_validated_fail": fail,
            "match_rate": match_rate,
            "prompt_sha256": prompt_hash,
            "openai_usage": usage,
        },
    )
    append_jsonl(KG_WRITE_LOG, {"case_id": case_id, "ticker": case["ticker"], "mode": "primary", "n_written": len(write_rows), "batch_id": batch_id})
    update_state_from_logs(state)


def best_effort_targets(formula_type: str, years: list[Any]) -> list[dict[str, Any]]:
    metrics = BEST_EFFORT_METRICS.get(formula_type, [])
    targets: list[dict[str, Any]] = []
    for metric in metrics:
        for year in years:
            try:
                targets.append({"metric": metric, "year": int(year), "unit_hint": "unknown"})
            except (TypeError, ValueError):
                continue
    return targets


def extract_best_effort(case: dict[str, Any], formula_type: str) -> tuple[list[dict[str, Any]], dict[str, Any], str]:
    targets = best_effort_targets(formula_type, case.get("years", []))
    prompt = build_extraction_prompt(str(case["ticker"]).upper(), str(case.get("evidence_text", "")), targets)
    raw, usage = call_openai_json(prompt)
    return normalize_extracted_facts(raw.get("extracted_facts", []), targets), usage, sha(prompt)


def process_best_effort_case(
    driver: Any,
    state: dict[str, Any],
    batch_id: str,
    case_id: str,
    case: dict[str, Any],
    formula_type: str,
) -> None:
    extracted, usage, prompt_hash = extract_best_effort(case, formula_type)
    write_rows = write_facts_to_neo4j(
        driver=driver,
        case_id=case_id,
        ticker=str(case["ticker"]).upper(),
        company_name=str(case.get("company") or ""),
        extracted_facts=extracted,
        batch_id=batch_id,
        validation_results={f"{fact.get('metric')}_{fact.get('year')}": {"status": "no_contract"} for fact in extracted},
        source_facts=[],
        validation_status_default="no_contract",
    )
    append_jsonl(
        EXTRACTION_TRACE,
        {
            "case_id": case_id,
            "ticker": case["ticker"],
            "split": case.get("split", ""),
            "mode": "best_effort",
            "formula_type": formula_type,
            "n_target_facts": len(best_effort_targets(formula_type, case.get("years", []))),
            "n_extracted": sum(1 for fact in extracted if fact.get("value") is not None),
            "n_validated_ok": 0,
            "n_validated_fail": 0,
            "match_rate": None,
            "prompt_sha256": prompt_hash,
            "openai_usage": usage,
        },
    )
    append_jsonl(KG_WRITE_LOG, {"case_id": case_id, "ticker": case["ticker"], "mode": "best_effort", "n_written": len(write_rows), "batch_id": batch_id})
    update_state_from_logs(state)


def verify_batch_count(driver: Any, batch_id: str) -> int:
    database = neo4j_env()["NEO4J_DATABASE"]
    with driver.session(database=database) as session:
        record = session.run(
            "MATCH (obs:LLMObservation {kg_batch: $batch_id}) RETURN count(obs) AS n",
            batch_id=batch_id,
        ).single()
    return int(record["n"]) if record else 0


def round5_graph_rfr_by_case() -> dict[str, float]:
    state = read_json(ROUND5_STATE) if ROUND5_STATE.exists() else {}
    trace_path = ROOT / str(state.get("run_dir", "")).rstrip("/") / "round5_traces.jsonl" if state.get("run_dir") else ROUND5_TRACES_DEFAULT
    rows = read_jsonl(trace_path)
    return {
        row["case_id"]: float(row.get("required_fact_recall", 0.0))
        for row in rows
        if row.get("method") == "graph_neo4j_v5"
    }


def write_summary(batch_id: str, kg_count: int) -> None:
    traces = read_jsonl(EXTRACTION_TRACE)
    validation = read_jsonl(VALIDATION_REPORT)
    writes = read_jsonl(KG_WRITE_LOG)
    primary = [row for row in traces if row.get("mode") == "primary"]
    failures = [row for row in validation if row.get("match") is False]
    by_split: dict[str, list[float]] = defaultdict(list)
    for row in primary:
        by_split[row.get("split", "")].append(float(row.get("match_rate", 0.0)))
    total_written = sum(int(row.get("n_written", 0)) for row in writes)
    validated_ok_written = sum(1 for row in validation if row.get("match") is True)
    best_effort_written = sum(int(row.get("n_written", 0)) for row in writes if row.get("mode") == "best_effort")
    r5_rfr = round5_graph_rfr_by_case()

    lines = [
        "# Step B: KG Targeted Extraction Summary",
        "",
        f"**Batch ID:** {batch_id}",
        f"**Run date:** {TODAY}",
        f"**Cases processed (primary):** {len(primary)}",
        f"**Cases processed (best-effort):** {len([row for row in traces if row.get('mode') == 'best_effort'])}",
        f"**Primary raw source facts:** {sum(int(row.get('n_raw_source_facts', row.get('n_target_facts', 0))) for row in primary)}",
        f"**Primary unique metric-year targets:** {sum(int(row.get('n_target_facts', 0)) for row in primary)}",
        f"**Neo4j observations in batch:** {kg_count}",
        "",
        "## Validation Results (19 primary cases)",
        "",
        "| Case | Ticker | Formula Type | Target Facts | Extracted | Validated OK | Failed | Match Rate |",
        "|---|---|---|---:|---:|---:|---:|---:|",
    ]
    for row in primary:
        lines.append(
            f"| {row['case_id']} | {row['ticker']} | {row['formula_type']} | {row['n_target_facts']} | "
            f"{row['n_extracted']} | {row['n_validated_ok']} | {row['n_validated_fail']} | {row['match_rate']} |"
        )
    lines.extend(["", "## Match Rate Summary", "", "| Split | n_cases | avg_match_rate |", "|---|---:|---:|"])
    for split, values in sorted(by_split.items()):
        avg_match = round(sum(values) / len(values), 4) if values else 0.0
        lines.append(f"| {split} | {len(values)} | {avg_match} |")
    all_values = [float(row.get("match_rate", 0.0)) for row in primary]
    lines.append(f"| **total** | {len(all_values)} | {round(sum(all_values) / len(all_values), 4) if all_values else 0.0} |")
    lines.extend(["", "## Validation Failures Detail", ""])
    if failures:
        for row in failures:
            lines.append(
                f"- {row['case_id']} {row['metric']} {row['year']}: expected={row.get('expected')}, "
                f"extracted={row.get('extracted')}, delta={row.get('tolerance_pct')}"
            )
    else:
        lines.append("- None")
    lines.extend(
        [
            "",
            "## KG Write Summary",
            "",
            f"- Total nodes written: {total_written}",
            f"- With validation_ok: {validated_ok_written}",
            f"- Best-effort (no contract): {best_effort_written}",
            "",
            "## Comparison: Round 5 rfr vs Expected Round 6 rfr",
            "",
            "| Case | Ticker | R5 rfr (graph) | Expected R6 rfr | Notes |",
            "|---|---|---:|---:|---|",
        ]
    )
    for row in primary:
        expected = "~1.0" if float(row.get("match_rate", 0.0)) >= 0.8 else f"~{row.get('match_rate', 0.0)}"
        lines.append(f"| {row['case_id']} | {row['ticker']} | {r5_rfr.get(row['case_id'], 0.0)} | {expected} | targeted batch |")
    lines.extend(
        [
            "",
            "## Codex Handoff",
            "",
            f"Next step: Round 06 eval with `KG_BATCH = \"{batch_id}\"`.",
        ]
    )
    SUMMARY_PATH.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8", newline="\n")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--batch-id", default=DEFAULT_BATCH_ID)
    parser.add_argument("--resume", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--limit", type=int, default=None)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    validate_round5_done()
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    state = initial_state(args.batch_id, args.resume)
    write_json(STATE_PATH, state)

    cases = load_cases()
    contracts = load_primary_contracts()
    if len(cases) != 25:
        raise RuntimeError(f"Expected 25 cases, found {len(cases)}")
    if len(contracts) != 19:
        raise RuntimeError(f"Expected 19 primary contracts, found {len(contracts)}")

    processed_this_run = 0
    driver = create_driver()
    try:
        for case_id, contract in contracts.items():
            if case_id not in cases:
                raise RuntimeError(f"Primary contract case missing from cases: {case_id}")
            if case_completed(case_id, "primary"):
                continue
            if args.limit is not None and processed_this_run >= args.limit:
                return
            try:
                process_primary_case(driver, state, args.batch_id, case_id, cases[case_id], contract)
            except Exception as exc:  # noqa: BLE001
                row = {"case_id": case_id, "mode": "primary", "error_type": getattr(exc, "error_type", "error"), "error_message": sanitize_error(exc)}
                append_jsonl(FAILED_EXTRACTIONS, row)
                update_state_from_logs(state)
                raise
            processed_this_run += 1
            print(json.dumps({"case_id": case_id, "mode": "primary", "processed": processed_this_run}, ensure_ascii=False), flush=True)
            time.sleep(0.1)

        for info in BEST_EFFORT_CASES:
            case_id, case = find_case_by_prefix(cases, info["case_id_prefix"])
            if case_completed(case_id, "best_effort"):
                continue
            if args.limit is not None and processed_this_run >= args.limit:
                return
            process_best_effort_case(driver, state, args.batch_id, case_id, case, info["formula_type"])
            processed_this_run += 1
            print(json.dumps({"case_id": case_id, "mode": "best_effort", "processed": processed_this_run}, ensure_ascii=False), flush=True)
            time.sleep(0.1)

        kg_count = verify_batch_count(driver, args.batch_id)
    finally:
        driver.close()

    update_state_from_logs(state)
    state = read_json(STATE_PATH)
    state["phase"] = "done"
    state["completed_at"] = utc_now()
    state["neo4j_observations_in_batch"] = kg_count
    state["codex_handoff_message"] = f"Step B complete. Next: Round 06 eval with KG_BATCH = {args.batch_id}."
    write_json(STATE_PATH, state)
    write_summary(args.batch_id, kg_count)
    print(json.dumps({"batch_id": args.batch_id, "state": rel(STATE_PATH), "summary": rel(SUMMARY_PATH), "neo4j_observations_in_batch": kg_count}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
