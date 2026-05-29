"""Run Round 4 evaluation against the Neo4j LLM IE KG.

This is based on scripts/round3_dev_dryrun_v3_2_clean.py, with the graph
context replaced by read-only LLMObservation retrieval from Neo4j.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import os
import re
import socket
import sys
import time
import urllib.error
import urllib.request
from collections import defaultdict
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from seocho.eval.round3 import MethodResult


METHODS = ["vector_only_v4", "graph_neo4j_v4", "hybrid_neo4j_v4"]
MODEL = "gpt-4o-mini"
KG_BATCH = "kg-llm-ie-v1-20260528"
TRACK = "track_b_neo4j_llm_ie"

OUT_ROOT = ROOT / "outputs" / "round3_eval_runs"
STATE_PATH = ROOT / "outputs" / "round4_neo4j_eval" / "state.json"
TRACK_B = ROOT / "outputs" / "round3_dual_track_eval_prep" / "track_b_shadow_overlay"
CASES_PATH = TRACK_B / "shadow_overlay_eval_ready_cases.jsonl"
REQUIRED_FACTS_PATH = TRACK_B / "shadow_overlay_required_facts.jsonl"
PROMPTS = ROOT / "outputs" / "round3_dual_track_eval_prep" / "prompt_formatter_v3_2"
CLEAN = ROOT / "outputs" / "round3_eval_harness" / "formula_contract_v3_2_clean_dev"
DEV_BASELINE = ROOT / "outputs" / "round3_eval_harness" / "formula_contract_v3_2" / "dev_baseline_contracts"
ROUND3B = ROOT / "outputs" / "round3b_recovery"
LOCKED = OUT_ROOT / "locked_test_v3_2_track_b_20260528_145253"
ENV_FILES = (ROOT / ".env", ROOT.parent / ".env")

NUM_RE = re.compile(
    r"(?<![A-Za-z_])-?\(?\$?\d[\d,]*(?:\.\d+)?\)?\s*(?:%|percent|percentage|million|millions|billion|billions|thousand|thousands)?",
    re.I,
)
ID_CONTEXT_RE = re.compile(
    r"\b(?:round3|baseline|control|dev|test|fact|trace|case|source|evidence|prompt|sha|id)[-_A-Za-z0-9]*\b",
    re.I,
)
YEAR_RE = re.compile(r"\b20\d{2}\b")
PROVIDER_ERROR_TYPES = {
    "provider_rate_limit",
    "provider_unavailable",
    "provider_timeout",
    "provider_auth",
    "provider_bad_response",
    "provider_unknown",
}


class ProviderError(RuntimeError):
    def __init__(self, error_type: str, message: str) -> None:
        super().__init__(message)
        self.error_type = error_type


def ts() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S")


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def rel(path: Path) -> str:
    return path.resolve().relative_to(ROOT.resolve()).as_posix()


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


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(
        json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    tmp.replace(path)


def write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text.rstrip() + "\n", encoding="utf-8", newline="\n")


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")


def read_existing_jsonl(path: Path) -> list[dict[str, Any]]:
    return read_jsonl(path)


def sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


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
        raise ProviderError("provider_auth", "OPENAI_API_KEY missing from process environment; value not printed")
    return key


def neo4j_env() -> dict[str, str]:
    values = {
        "NEO4J_URI": env_from_files("NEO4J_URI"),
        "NEO4J_USERNAME": env_from_files("NEO4J_USERNAME") or env_from_files("NEO4J_USER"),
        "NEO4J_PASSWORD": env_from_files("NEO4J_PASSWORD"),
        "NEO4J_DATABASE": env_from_files("NEO4J_DATABASE") or "neo4j",
    }
    missing = [key for key in ("NEO4J_URI", "NEO4J_USERNAME", "NEO4J_PASSWORD") if not values[key]]
    if missing:
        raise RuntimeError(f"Missing Neo4j configuration: {', '.join(missing)}")
    return values


def create_driver() -> Any:
    from neo4j import GraphDatabase

    env = neo4j_env()
    return GraphDatabase.driver(
        env["NEO4J_URI"],
        auth=(env["NEO4J_USERNAME"], env["NEO4J_PASSWORD"]),
        connection_timeout=30,
        max_connection_lifetime=120,
    )


def load_cases() -> list[dict[str, Any]]:
    return read_jsonl(CASES_PATH)


def load_required_facts() -> dict[str, list[dict[str, Any]]]:
    facts: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in read_jsonl(REQUIRED_FACTS_PATH):
        facts[row["case_id"]].append(row)
    return facts


def index_contract_file(path: Path, field: str) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for row in read_jsonl(path):
        if row.get("case_id") and field in row:
            out[row["case_id"]] = row[field]
    return out


def load_contracts() -> tuple[dict[str, Any], dict[str, Any]]:
    visible: dict[str, Any] = {}
    scorer: dict[str, Any] = {}
    for path, field in [
        (CLEAN / "clean_dev_model_visible_formula_contracts.jsonl", "model_visible_formula_contract"),
        (DEV_BASELINE / "dev_baseline_model_visible_formula_contracts.jsonl", "model_visible_formula_contract"),
        (ROUND3B / "formula_contracts.jsonl", "model_visible_formula_contract"),
        (ROUND3B / "repaired_cases.jsonl", "model_visible_formula_contract"),
    ]:
        visible.update({k: v for k, v in index_contract_file(path, field).items() if k not in visible})
    for path, field in [
        (CLEAN / "clean_dev_scorer_only_target_slot_contracts.jsonl", "scorer_only_target_slot_contract"),
        (DEV_BASELINE / "dev_baseline_scorer_only_target_slot_contracts.jsonl", "scorer_only_target_slot_contract"),
        (ROUND3B / "scorer_contracts.jsonl", "scorer_only_target_slot_contract"),
    ]:
        scorer.update({k: v for k, v in index_contract_file(path, field).items() if k not in scorer})

    # Locked traces are read-only fallback for Track B test cases whose contracts
    # are absent from the spec-listed files.
    for row in read_jsonl(LOCKED / "locked_test_v3_2_traces.jsonl"):
        cid = row.get("case_id")
        if cid and cid not in visible and row.get("model_visible_formula_contract"):
            visible[cid] = row["model_visible_formula_contract"]
        if cid and cid not in scorer:
            scorer[cid] = {
                "formula_type": row.get("formula_type", ""),
                "target_slots": [],
                "scorer_note": "fallback_from_locked_trace_without_target_slots",
            }
    return visible, scorer


def fact_table(facts: list[dict[str, Any]]) -> str:
    header = "| source_fact_id | company | ticker | metric | year / period | value | unit | fact_role | evidence_quote_exact or evidence_ref |\n| --- | --- | --- | --- | --- | ---: | --- | --- | --- |"
    lines = [header]
    for fact in facts:
        period = fact.get("year") or fact.get("period_label") or ""
        quote = str(fact.get("evidence_quote_exact") or fact.get("evidence_ref") or "").replace("\n", " ")[:240]
        lines.append(
            f"| {fact.get('fact_id','')} | {fact.get('company','')} | {fact.get('ticker','')} | {fact.get('metric_canonical') or fact.get('metric','')} | {period} | {fact.get('value','')} | {fact.get('unit','')} | {fact.get('role') or fact.get('fact_role','')} | {quote} |"
        )
    return "\n".join(lines)


def build_prompt(track: str, method: str, case: dict[str, Any], facts: list[dict[str, Any]], formula_contract: dict[str, Any]) -> dict[str, str]:
    system = (PROMPTS / "prompt_v3_2_system.md").read_text(encoding="utf-8")
    answer_format = (PROMPTS / "answer_format_spec_v3_2.md").read_text(encoding="utf-8")
    rounding = (PROMPTS / "rounding_and_tolerance_rules_v3_2.md").read_text(encoding="utf-8")
    formula_text = json.dumps(formula_contract, ensure_ascii=False, indent=2, sort_keys=True)
    evidence = str(case.get("evidence_text", ""))
    table = fact_table(facts)
    if method == "vector_only_v4":
        context = f"TEXT_CONTEXT\n{evidence}"
    elif method == "graph_neo4j_v4":
        context = f"GRAPH_FACTS_TABLE\n{table}"
    elif method == "hybrid_neo4j_v4":
        context = f"TEXT_CONTEXT\n{evidence}\n\nGRAPH_FACTS_TABLE\n{table}"
    else:
        raise RuntimeError(f"unknown method: {method}")
    user = f"""track: {track}
case_id: {case['case_id']}
split: {case['split']}
method: {method}
question: {case['question']}

MODEL_VISIBLE_FORMULA_CONTRACT
{formula_text}

{context}

ANSWER_FORMAT
{answer_format}

ROUNDING_AND_TOLERANCE_RULES
{rounding}

Use temperature=0 behavior. Do not use outside knowledge. Do not mention hidden expected answers or scorer-only target slots.
"""
    return {"system": system, "user": user}


def load_neo4j_graph_facts(ticker: str, case_id: str, driver: Any) -> list[dict[str, Any]]:
    """Query LLMObservation nodes for a given ticker from the LLM IE KG."""
    database = neo4j_env()["NEO4J_DATABASE"]
    last_error: Exception | None = None
    for attempt in range(3):
        active_driver = driver if attempt == 0 else create_driver()
        try:
            with active_driver.session(database=database) as s:
                records = s.run(
                    """
MATCH (obs:LLMObservation)-[:LLM_MENTIONS_COMPANY]->(c:LLMCompany {ticker: $ticker}),
      (obs)-[:LLM_OBSERVES_METRIC]->(m:LLMFinancialMetric),
      (obs)-[:LLM_OBSERVED_IN_YEAR]->(yr:LLMFiscalYear)
WHERE obs.kg_batch = $batch
RETURN obs.obs_id AS obs_id,
       obs.value AS value,
       obs.unit AS unit,
       obs.evidence_quote AS evidence_quote,
       m.canonical_name AS metric_canonical,
       m.display_name AS metric_display,
       yr.year AS year
ORDER BY yr.year, m.canonical_name
""",
                    ticker=ticker,
                    batch=KG_BATCH,
                )
                facts = []
                for rec in records:
                    facts.append(
                        {
                            "fact_id": rec["obs_id"],
                            "metric_canonical": rec["metric_canonical"],
                            "metric_raw": rec["metric_display"],
                            "value": rec["value"],
                            "year": rec["year"],
                            "unit": rec["unit"] or "",
                            "company": ticker,
                            "ticker": ticker,
                            "evidence_quote_exact": rec["evidence_quote"] or "",
                            "fact_role": "component",
                            "source_fact": True,
                            "derived_answer_value": False,
                        }
                    )
                return facts
        except Exception as exc:  # noqa: BLE001
            last_error = exc
            time.sleep(2 * (attempt + 1))
        finally:
            if attempt > 0:
                active_driver.close()
    raise RuntimeError(f"Neo4j read failed after retries for {ticker}/{case_id}: {last_error}") from last_error


def classify_http(code: int) -> str:
    if code == 429:
        return "provider_rate_limit"
    if code in {401, 403}:
        return "provider_auth"
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


def adapt_result(row: dict[str, Any]) -> MethodResult:
    steps = row.get("calculation_steps") or row.get("calculation") or ""
    calculation = "\n".join(
        json.dumps(step, ensure_ascii=False, sort_keys=True) if isinstance(step, dict) else str(step)
        for step in steps
    ) if isinstance(steps, list) else str(steps)
    cited = row.get("cited_source_facts_used") or row.get("source_facts_used") or row.get("source_fact_ids_used") or []
    fact_ids: list[str] = []
    citations: list[str] = []
    for item in cited:
        if isinstance(item, dict):
            fid = str(item.get("source_fact_id") or item.get("fact_id") or "")
            if fid:
                fact_ids.append(fid)
            citations.append(" ".join(str(item.get(k, "")) for k in ["source_fact_id", "metric", "year_or_period", "value"]).strip())
        else:
            fact_ids.append(str(item))
            citations.append(str(item))
    missing = row.get("uncertainty_or_missing_information") or row.get("missing_information") or []
    if isinstance(missing, str):
        missing = [missing] if missing else []
    return MethodResult(
        final_answer=str(row.get("final_answer") or row.get("answer") or ""),
        calculation=calculation,
        source_fact_ids_used=fact_ids,
        citations=citations,
        missing_information=[str(item) for item in missing],
    )


def call_openai(prompt: dict[str, str], model: str) -> tuple[MethodResult, dict[str, Any], dict[str, Any]]:
    key = openai_api_key()
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": prompt["system"]},
            {"role": "user", "content": prompt["user"]},
        ],
        "temperature": 0,
    }
    req = urllib.request.Request(
        "https://api.openai.com/v1/chat/completions",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {key}"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=180) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        raise ProviderError(classify_http(exc.code), sanitize_error(exc)) from exc
    except (socket.timeout, TimeoutError) as exc:
        raise ProviderError("provider_timeout", sanitize_error(exc)) from exc
    except urllib.error.URLError as exc:
        raise ProviderError("provider_unknown", sanitize_error(exc)) from exc
    except json.JSONDecodeError as exc:
        raise ProviderError("provider_bad_response", "provider returned invalid JSON envelope") from exc
    try:
        parsed = extract_json_object(data["choices"][0]["message"]["content"])
    except Exception as exc:
        raise ProviderError("provider_bad_response", "provider returned non-JSON model output") from exc
    return adapt_result(parsed), data.get("usage", {}), parsed


def parse_number(raw: str) -> dict[str, Any] | None:
    display = raw.strip()
    text = display.lower().strip()
    is_percent = "%" in text or "percent" in text or "percentage" in text
    negative = text.startswith("(") and text.endswith(")")
    text = text.strip("()")
    multiplier = 1.0
    if "billion" in text:
        multiplier = 1_000_000_000.0
    elif "million" in text:
        multiplier = 1_000_000.0
    elif "thousand" in text:
        multiplier = 1_000.0
    text = re.sub(r"[$,%]|percent|percentage|millions?|billions?|thousands?", "", text).replace(",", "").strip()
    if not text:
        return None
    try:
        value = float(text)
    except ValueError:
        return None
    if negative:
        value = -value
    return {
        "raw": display,
        "value": value,
        "scaled_value": value * multiplier,
        "is_percent": is_percent,
        "canonical_ratio": value / 100.0 if is_percent else value,
    }


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
        return math.isclose(expected_pct, actual_pct, abs_tol=0.1) or math.isclose(
            expected.get("canonical_ratio", expected["value"]),
            actual.get("canonical_ratio", actual["value"]),
            rel_tol=0.01,
            abs_tol=0.0015,
        )
    if unit in {"ratio, amount", "ratio", "amount"} and abs(expected["value"]) < 100 and abs(actual["value"]) < 100:
        return math.isclose(expected["value"], actual["value"], rel_tol=0.01, abs_tol=0.01)
    return math.isclose(expected["scaled_value"], actual["scaled_value"], rel_tol=0.005, abs_tol=0.01) or math.isclose(
        expected["value"], actual["value"], rel_tol=0.005, abs_tol=0.01
    )


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


def normalize_metric(value: Any) -> str:
    return re.sub(r"[^a-z0-9]+", "_", str(value or "").lower()).strip("_")


def metric_matches(required_metric: str, neo_metric: str) -> bool:
    req = normalize_metric(required_metric)
    got = normalize_metric(neo_metric)
    if not req or not got:
        return False
    if req == got or req in got or got in req:
        return True
    req_tokens = {token for token in req.split("_") if len(token) >= 4}
    got_tokens = {token for token in got.split("_") if len(token) >= 4}
    return bool(req_tokens) and len(req_tokens & got_tokens) >= min(2, len(req_tokens))


def fact_value_close(required: dict[str, Any], candidate: dict[str, Any]) -> bool:
    expected = parse_number(str(required.get("value", "")))
    actual = parse_number(str(candidate.get("value", "")))
    return bool(expected and actual and close(expected, actual, str(required.get("unit") or candidate.get("unit") or "")))


def required_fact_recall(required_facts: list[dict[str, Any]], neo4j_facts: list[dict[str, Any]]) -> float:
    if not required_facts:
        return 0.0
    matched = 0
    for req in required_facts:
        req_year = str(req.get("year") or "")
        req_ticker = str(req.get("ticker") or "").upper()
        req_metric = str(req.get("metric_canonical") or req.get("metric") or "")
        for fact in neo4j_facts:
            if req_ticker and str(fact.get("ticker") or "").upper() != req_ticker:
                continue
            if req_year and str(fact.get("year") or "") != req_year:
                continue
            if not metric_matches(req_metric, str(fact.get("metric_canonical") or "")):
                continue
            if fact_value_close(req, fact):
                matched += 1
                break
    return round(matched / len(required_facts), 4)


def score_result(
    trace_base: dict[str, Any],
    result: MethodResult | None,
    prompt: dict[str, str],
    required_facts: list[dict[str, Any]],
    neo4j_facts: list[dict[str, Any]],
    scorer_contract: dict[str, Any],
) -> dict[str, Any]:
    if result is None:
        return {
            "required_fact_recall": 0.0,
            "target_numeric_recall": 0.0,
            "numeric_correctness": 0.0,
            "answer_correctness": 0.0,
            "faithfulness": 0.0,
            "calculation_completeness": 0.0,
            "answer_format_compliance": 0.0,
            "failure_reason": "provider_error",
        }
    output = "\n".join([result.final_answer, result.calculation])
    actual = extract_numbers(output)
    slots = scorer_contract.get("target_slots", [])
    matched = []
    missing = []
    for slot in slots:
        expected = parse_number(str(slot["expected_value"]))
        if expected and any(close(expected, candidate, slot.get("unit", "")) for candidate in actual):
            matched.append(slot["target_slot_name"])
        else:
            missing.append(slot["target_slot_name"])
    target_recall = round(len(matched) / len(slots), 4) if slots else 0.0
    numeric_ok = target_recall >= 0.8 if slots else False
    if trace_base["method"] in {"graph_neo4j_v4", "hybrid_neo4j_v4"}:
        rfr = required_fact_recall(required_facts, neo4j_facts)
    else:
        text_recall = value_recall(required_facts, prompt["user"])
        answer_value = value_recall(required_facts, output)
        rfr = max(text_recall, answer_value)
    fmt = bool(result.final_answer and result.calculation)
    calc = bool(result.calculation and any(token in result.calculation.lower() for token in ["/", "%", "=", "ratio", "rate", "margin", "growth", "change", "formula"]))
    faith = rfr >= 0.8
    ans = numeric_ok and fmt and calc and faith
    failure = "none"
    if not fmt:
        failure = "answer_format_error"
    elif rfr < 0.5:
        failure = "required_fact_missing"
    elif slots and not numeric_ok:
        failure = "formula_target_mismatch"
    elif not slots:
        failure = "expected_answer_ambiguous"
    elif not ans:
        failure = "scoring_uncertain"
    return {
        "required_fact_recall": rfr,
        "target_numeric_recall": target_recall,
        "numeric_correctness": 1.0 if numeric_ok else 0.0,
        "answer_correctness": 1.0 if ans else 0.0,
        "faithfulness": 1.0 if faith else 0.0,
        "calculation_completeness": 1.0 if calc else 0.0,
        "answer_format_compliance": 1.0 if fmt else 0.0,
        "failure_reason": failure,
        "matched_target_slots": ";".join(matched),
        "missing_target_slots": ";".join(missing),
    }


def avg(values: list[Any]) -> float:
    nums = [float(v) for v in values]
    return round(sum(nums) / len(nums), 4) if nums else 0.0


def summarize_by_method(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        groups[row["method"]].append(row)
    out = []
    for method, items in sorted(groups.items()):
        out.append(
            {
                "method": method,
                "attempts": len(items),
                "provider_success": sum(1 for row in items if row.get("provider_success")),
                "avg_answer_correctness": avg([row["answer_correctness"] for row in items]),
                "avg_numeric_correctness": avg([row["numeric_correctness"] for row in items]),
                "avg_rfr": avg([row["required_fact_recall"] for row in items]),
            }
        )
    return out


def locked_summary_rows() -> list[dict[str, Any]]:
    rows = []
    path = LOCKED / "locked_test_v3_2_results.csv"
    if not path.exists():
        return rows
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        locked_rows = [dict(row) for row in csv.DictReader(handle)]
    wanted = {
        "vector_only_v3_2": "vector_only (locked)",
        "hybrid_vector_graph_v3_2": "hybrid_shadow (locked)",
    }
    for method, label in wanted.items():
        items = [row for row in locked_rows if row.get("method") == method]
        if not items:
            continue
        rows.append(
            {
                "method": label,
                "avg_answer_correctness": avg([row.get("answer_correctness") or 0 for row in items]),
                "avg_numeric_correctness": avg([row.get("numeric_correctness") or 0 for row in items]),
                "avg_rfr": avg([row.get("required_fact_recall_v3_2") or row.get("required_fact_recall") or 0 for row in items]),
                "notes": "from locked test",
            }
        )
    return rows


def write_summary(run_dir: Path, rows: list[dict[str, Any]]) -> None:
    round4 = summarize_by_method(rows)
    lines = [
        "# Round 4 LLM IE KG Evaluation",
        "",
        f"- Track: `{TRACK}`",
        f"- Model: `{MODEL}`",
        f"- Attempts: {len(rows)}",
        f"- Provider failures: {sum(1 for row in rows if row.get('failure_reason') == 'provider_error')}",
        f"- Neo4j writes performed: no",
        "",
        "| Method | avg_answer_correctness | avg_numeric_correctness | avg_rfr | notes |",
        "|---|---:|---:|---:|---|",
    ]
    for row in locked_summary_rows():
        lines.append(f"| {row['method']} | {row['avg_answer_correctness']} | {row['avg_numeric_correctness']} | {row['avg_rfr']} | {row['notes']} |")
    for row in round4:
        lines.append(f"| {row['method']} | {row['avg_answer_correctness']} | {row['avg_numeric_correctness']} | {row['avg_rfr']} | Round 4 |")
    write(run_dir / "round4_summary.md", "\n".join(lines))


def initial_state(run_dir: Path, cases_total: int) -> dict[str, Any]:
    return {
        "phase": "running",
        "cases_total": cases_total,
        "methods": METHODS,
        "runs_total": cases_total * len(METHODS),
        "runs_completed": 0,
        "runs_failed": [],
        "started_at": utc_now(),
        "completed_at": None,
        "run_dir": rel(run_dir) + "/",
        "codex_handoff_message": None,
    }


def update_state(state: dict[str, Any], rows: list[dict[str, Any]]) -> None:
    state["runs_completed"] = len(rows)
    state["runs_failed"] = [
        {
            "case_id": row["case_id"],
            "method": row["method"],
            "failure_reason": row.get("failure_reason"),
            "error_type": row.get("error_type", ""),
        }
        for row in rows
        if row.get("failure_reason") == "provider_error"
    ]
    write_json(STATE_PATH, state)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-dir", default="")
    parser.add_argument("--resume", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--limit", type=int, default=None)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    cases = load_cases()
    run_dir = Path(args.run_dir).resolve() if args.run_dir else OUT_ROOT / f"round4_llm_ie_kg_{ts()}"
    if args.resume and not args.run_dir and STATE_PATH.exists():
        existing = read_json(STATE_PATH)
        if existing.get("phase") == "running" and existing.get("run_dir"):
            run_dir = ROOT / str(existing["run_dir"]).rstrip("/")
    run_dir.mkdir(parents=True, exist_ok=True)

    required_by_case = load_required_facts()
    visible_contracts, scorer_contracts = load_contracts()
    missing_visible = [case["case_id"] for case in cases if case["case_id"] not in visible_contracts]
    missing_scorer = [case["case_id"] for case in cases if case["case_id"] not in scorer_contracts]
    if missing_visible or missing_scorer:
        raise RuntimeError(f"Missing contracts: visible={missing_visible} scorer={missing_scorer}")

    state = initial_state(run_dir, len(cases))
    if args.resume and STATE_PATH.exists():
        existing = read_json(STATE_PATH)
        if existing.get("run_dir") == rel(run_dir) + "/" and existing.get("phase") in {"running", "done"}:
            state.update(existing)
            state["phase"] = "running"
            state["completed_at"] = None
            state["codex_handoff_message"] = None
    write_json(STATE_PATH, state)

    traces = read_existing_jsonl(run_dir / "round4_traces.jsonl") if args.resume else []
    if args.resume:
        traces = [row for row in traces if row.get("failure_reason") != "provider_error"]
        write_jsonl(run_dir / "round4_traces.jsonl", traces)
    completed = {(row["case_id"], row["method"]) for row in traces}
    facts_cache = read_existing_jsonl(run_dir / "neo4j_facts_cache.jsonl") if args.resume else []
    facts_by_case = {row["case_id"]: row["facts"] for row in facts_cache}
    rows_run = 0

    driver = create_driver()
    try:
        for case in cases:
            case_id = case["case_id"]
            ticker = str(case["ticker"]).upper()
            if case_id not in facts_by_case:
                facts_by_case[case_id] = load_neo4j_graph_facts(ticker, case_id, driver)
                facts_cache.append(
                    {
                        "case_id": case_id,
                        "ticker": ticker,
                        "neo4j_facts_count": len(facts_by_case[case_id]),
                        "facts": facts_by_case[case_id],
                    }
                )
                write_jsonl(run_dir / "neo4j_facts_cache.jsonl", facts_cache)

            for method in METHODS:
                if (case_id, method) in completed:
                    continue
                if args.limit is not None and rows_run >= args.limit:
                    update_state(state, traces)
                    return

                attempt_idx = len(traces) + 1
                trace_id = f"local_trace_round4_{attempt_idx:04d}_{case_id}__{method}"
                prompt_facts = [] if method == "vector_only_v4" else facts_by_case[case_id]
                prompt = build_prompt(TRACK, method, case, prompt_facts, visible_contracts[case_id])
                scorer_contract = scorer_contracts[case_id]
                base = {
                    "case_id": case_id,
                    "ticker": ticker,
                    "split": case.get("split", ""),
                    "method": method,
                    "track": TRACK,
                    "neo4j_facts_count": len(facts_by_case[case_id]),
                    "formula_type": scorer_contract.get("formula_type") or visible_contracts[case_id].get("formula_type", ""),
                    "target_slot_count": len(scorer_contract.get("target_slots", [])),
                    "provider": "openai",
                    "model": MODEL,
                    "trace_id": trace_id,
                    "success": False,
                    "provider_success": False,
                    "error_type": "",
                    "error_message": "",
                }
                result = None
                raw = None
                usage = {}
                try:
                    result, usage, raw = call_openai(prompt, MODEL)
                    base.update({"success": True, "provider_success": True})
                except ProviderError as exc:
                    base.update({"error_type": exc.error_type, "error_message": str(exc)})
                except Exception as exc:  # noqa: BLE001
                    base.update({"error_type": "scoring_uncertain", "error_message": str(exc)[:300]})

                scores = score_result(
                    {**base},
                    result,
                    prompt,
                    required_by_case.get(case_id, []),
                    facts_by_case[case_id],
                    scorer_contract,
                )
                base.update(scores)
                if base.get("error_type") in PROVIDER_ERROR_TYPES:
                    base["failure_reason"] = "provider_error"
                row = {
                    **base,
                    "final_answer": result.final_answer if result else "",
                    "calculation": result.calculation if result else "",
                    "prompt_sha256": sha(prompt["system"] + "\n" + prompt["user"]),
                    "system_prompt": prompt["system"],
                    "user_prompt": prompt["user"],
                    "method_result": asdict(result) if result else None,
                    "raw_method_result_v4": raw,
                    "usage": usage,
                    "model_api_called": True,
                    "neo4j_write_performed": False,
                    "kg_patch_applied": False,
                }
                traces.append(row)
                completed.add((case_id, method))
                rows_run += 1
                write_jsonl(run_dir / "round4_traces.jsonl", traces)
                write_jsonl(run_dir / "failure_analysis.jsonl", [r for r in traces if r.get("failure_reason") != "none"])
                update_state(state, traces)
                print(
                    json.dumps(
                        {
                            "runs_completed": len(traces),
                            "runs_total": len(cases) * len(METHODS),
                            "case_id": case_id,
                            "method": method,
                            "failure_reason": row.get("failure_reason"),
                        },
                        ensure_ascii=False,
                    ),
                    flush=True,
                )
                time.sleep(0.25)
    finally:
        driver.close()

    write_summary(run_dir, traces)
    state["phase"] = "done"
    state["completed_at"] = utc_now()
    state["codex_handoff_message"] = "Round 4 complete. Run scripts/verify_round4_results.py"
    update_state(state, traces)
    print(json.dumps({"run_dir": rel(run_dir), "runs_completed": len(traces), "summary": rel(run_dir / "round4_summary.md")}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
