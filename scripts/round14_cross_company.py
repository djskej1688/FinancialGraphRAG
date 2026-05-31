from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import random
import re
import socket
import time
import traceback
import urllib.error
import urllib.request
from collections import Counter, defaultdict
from datetime import date
from pathlib import Path
from typing import Any

import kg_rebuild_llm_ie as ie
import round10_common as c
from scorer_v9 import score_trace


ROUND = "round14"
MODEL = "gpt-4o-mini"
EMBEDDING_MODEL = "text-embedding-3-small"
SCORER_VERSION = "v9"
PROMPT_VERSION = "v3.4"
CLAIM_BOUNDARY = "cross_company_graph_advantage_round14"
KG_BATCH = f"kg-round14-multicompany-v1-{date.today().strftime('%Y%m%d')}"

ROOT = c.ROOT
MANIFEST = ROOT / "inputs" / "round14" / "manifest.json"
ALL_SLICES_CSV = ROOT / "inputs" / "round14" / "all_slices.csv"
ONTOLOGY_GUIDE = ROOT / "inputs" / "round14" / "ONTOLOGY_GUIDE.md"

OUT = ROOT / "outputs" / "round14_cross_company"
AUDIT_DIR = OUT / "00_input_audit"
METRICDICT_DIR = OUT / "01_metric_dictionary"
PILOT_DIR = OUT / "02_pilot_smoke"
EXTRACT_DIR = OUT / "03_extraction"
XQUERY_DIR = OUT / "04_cross_company_queries"
STATE_FILE = OUT / "state.json"
SUMMARY_FILE = OUT / "round14_summary.md"

AUDIT_FILE = AUDIT_DIR / "uploaded_input_audit.json"
METRIC_DICT_FILE = METRICDICT_DIR / "metric_dictionary_fibo_style.json"
SYNONYM_FILE = METRICDICT_DIR / "canonical_metric_synonyms.json"
HYGIENE_FILE = METRICDICT_DIR / "metric_dictionary_hygiene_report.md"
CQ_FILE = METRICDICT_DIR / "competency_questions.md"
PILOT_OBS_FILE = PILOT_DIR / "pilot_observations.jsonl"
SMOKE_FILE = PILOT_DIR / "smoke_gate_report.md"
OBS_FILE = EXTRACT_DIR / "observations.jsonl"
UNMAPPED_FILE = EXTRACT_DIR / "unmapped_observations.jsonl"
WRITE_LOG = EXTRACT_DIR / "neo4j_write_log.jsonl"
ROLLBACK_FILE = EXTRACT_DIR / "round14_kg_rollback.cypher"
KG_COVERAGE_FILE = EXTRACT_DIR / "kg_coverage_report.md"
EMBED_CACHE_FILE = EXTRACT_DIR / "embedding_cache.jsonl"
CROSS_CASES_FILE = XQUERY_DIR / "cross_company_cases.jsonl"
GT_FILE = XQUERY_DIR / "ground_truth_derivation.jsonl"
SYNTHESIS_FILE = XQUERY_DIR / "synthesis_report.md"

METHODS = [
    "vector_single_v14",
    "vector_multi_by_company_v14",
    "graph_structured_v14",
    "graph_guided_text_v14",
    "source_text_concat_v14",
]
PROVIDER_ERROR_TYPES = {
    "provider_auth",
    "provider_rate_limit",
    "provider_unavailable",
    "provider_timeout",
    "provider_bad_response",
    "provider_unknown",
}
CANONICAL_RE = re.compile(r"^[a-z][a-z0-9_]{3,}$")
BAD_CANONICAL = {"source_value", "program_operand", "operand", "value", "amount", "number"}
ALLOWED_UNITS = {"currency_millions", "currency_billions", "currency_per_share", "count", "percentage", "ratio"}
FINANCE_TOKEN_STOP = {
    "CAGR",
    "COGS",
    "EPS",
    "FY",
    "GAAP",
    "GPM",
    "ROI",
    "SEC",
    "SGA",
    "US",
    "YOY",
}


METRIC_DICTIONARY: list[dict[str, Any]] = [
    {"canonical_name": "total_revenue", "label": "total revenue", "definition": "income measure that represents all revenue recognized by an entity", "synonyms": ["revenue", "revenues", "sales", "net sales", "total revenues", "total revenue", "net revenue"], "abbreviations": []},
    {"canonical_name": "total_net_revenue", "label": "total net revenue", "definition": "income measure that represents total revenue net of directly offsetting revenue items", "synonyms": ["total net revenue", "total net revenues"], "abbreviations": []},
    {"canonical_name": "cost_of_sales", "label": "cost of sales", "definition": "expense measure that represents direct costs incurred to generate revenue", "synonyms": ["cost of revenue", "cost of revenues", "cost of goods sold", "cost of sales", "total cost of revenue"], "abbreviations": ["cogs"]},
    {"canonical_name": "gross_profit", "label": "gross profit", "definition": "profit measure that represents revenue less cost of sales", "synonyms": ["gross profit", "total gross profit", "gross income"], "abbreviations": []},
    {"canonical_name": "operating_income", "label": "operating income", "definition": "profit measure that represents income from continuing business operations before non-operating items", "synonyms": ["operating income", "income from operations", "operating earnings", "loss from operations", "operating income loss"], "abbreviations": []},
    {"canonical_name": "operating_expense", "label": "operating expense", "definition": "expense measure that represents costs incurred to run core operations", "synonyms": ["operating expenses", "total operating expenses"], "abbreviations": ["opex"]},
    {"canonical_name": "net_income", "label": "net income", "definition": "profit measure that represents earnings after expenses and taxes", "synonyms": ["net income", "net earnings", "net income loss", "net loss", "net earnings loss"], "abbreviations": []},
    {"canonical_name": "net_income_attributable_common", "label": "net income attributable to common shareholders", "definition": "profit measure that represents net income attributable to common shareholders", "synonyms": ["net income available to common shares", "net income attributable to common shareholders", "net income attributable to common stockholders"], "abbreviations": []},
    {"canonical_name": "research_and_development_expense", "label": "research and development expense", "definition": "expense measure that represents research and development costs", "synonyms": ["research and development", "research and development expense", "research and development expenses"], "abbreviations": ["r&d", "rd"]},
    {"canonical_name": "selling_general_admin_expense", "label": "selling general and administrative expense", "definition": "expense measure that represents selling, general, and administrative costs", "synonyms": ["selling general and administrative", "selling general and administrative expense", "general and administrative", "sales and marketing"], "abbreviations": ["sg&a", "sga"]},
    {"canonical_name": "interest_expense", "label": "interest expense", "definition": "expense measure that represents interest cost incurred on debt or financing obligations", "synonyms": ["interest expense", "interest income expense net", "interest expense net"], "abbreviations": []},
    {"canonical_name": "income_tax_expense", "label": "income tax expense", "definition": "tax expense measure that represents provision for income taxes", "synonyms": ["provision for income taxes", "income tax expense", "provision for taxes", "tax provision", "income taxes"], "abbreviations": []},
    {"canonical_name": "income_before_taxes", "label": "income before taxes", "definition": "profit measure that represents income before provision for income taxes", "synonyms": ["income before taxes", "earnings before taxes", "income before provision for income taxes", "income loss before income taxes"], "abbreviations": ["ebt"]},
    {"canonical_name": "total_assets", "label": "total assets", "definition": "balance sheet measure that represents all assets controlled by an entity", "synonyms": ["total assets"], "abbreviations": []},
    {"canonical_name": "total_liabilities", "label": "total liabilities", "definition": "balance sheet measure that represents all obligations of an entity", "synonyms": ["total liabilities"], "abbreviations": []},
    {"canonical_name": "stockholders_equity", "label": "stockholders equity", "definition": "equity measure that represents residual ownership interest attributable to shareholders", "synonyms": ["stockholders equity", "shareholders equity", "total equity", "total stockholders equity"], "abbreviations": []},
    {"canonical_name": "cash_and_equivalents", "label": "cash and equivalents", "definition": "asset measure that represents cash and highly liquid short-term investments", "synonyms": ["cash and cash equivalents", "cash equivalents", "cash"], "abbreviations": []},
    {"canonical_name": "total_debt", "label": "total debt", "definition": "liability measure that represents interest-bearing borrowings outstanding", "synonyms": ["total debt", "debt", "borrowings"], "abbreviations": []},
    {"canonical_name": "depreciation_amortization", "label": "depreciation and amortization", "definition": "expense measure that represents allocation of tangible and intangible asset costs", "synonyms": ["depreciation and amortization", "depreciation", "amortization"], "abbreviations": ["d&a"]},
    {"canonical_name": "ebit", "label": "ebit", "definition": "profit measure that represents earnings before interest and taxes", "synonyms": ["ebit", "earnings before interest and taxes"], "abbreviations": ["ebit"]},
    {"canonical_name": "ebitda", "label": "ebitda", "definition": "profit measure that represents earnings before interest, taxes, depreciation, and amortization", "synonyms": ["ebitda"], "abbreviations": ["ebitda"]},
    {"canonical_name": "net_earned_premiums", "label": "net earned premiums", "definition": "insurance revenue measure that represents premiums recognized as earned net of ceded amounts", "synonyms": ["net earned premiums", "premiums earned", "earned premiums"], "abbreviations": []},
    {"canonical_name": "total_employees", "label": "total employees", "definition": "workforce measure that represents the number of employees of an entity", "synonyms": ["employees", "total employees", "workforce", "headcount"], "abbreviations": []},
    {"canonical_name": "diluted_eps", "label": "diluted eps", "definition": "per-share measure that represents net income per diluted common share", "synonyms": ["diluted eps", "diluted earnings per share", "net income per share diluted", "earnings per share diluted"], "abbreviations": ["eps diluted"]},
    {"canonical_name": "basic_eps", "label": "basic eps", "definition": "per-share measure that represents net income per basic common share", "synonyms": ["basic eps", "basic earnings per share", "net income per share basic", "earnings per share basic"], "abbreviations": ["eps basic"]},
    {"canonical_name": "diluted_shares", "label": "diluted shares", "definition": "share count measure that represents weighted-average diluted shares outstanding", "synonyms": ["weighted average diluted shares", "weighted-average shares diluted", "diluted shares", "weighted-average shares used to compute diluted"], "abbreviations": []},
    {"canonical_name": "basic_shares", "label": "basic shares", "definition": "share count measure that represents weighted-average basic shares outstanding", "synonyms": ["weighted average basic shares", "weighted-average shares basic", "basic shares", "weighted-average shares used to compute basic"], "abbreviations": []},
    {"canonical_name": "dividends_paid", "label": "dividends paid", "definition": "cash flow measure that represents dividends paid to shareholders", "synonyms": ["dividends paid", "cash dividends", "dividends"], "abbreviations": []},
    {"canonical_name": "capital_expenditure", "label": "capital expenditure", "definition": "cash flow measure that represents cash used to acquire property and equipment", "synonyms": ["capital expenditures", "capital expenditure", "purchases of property and equipment"], "abbreviations": ["capex"]},
    {"canonical_name": "inventory", "label": "inventory", "definition": "asset measure that represents goods held for sale or production", "synonyms": ["inventory", "inventories"], "abbreviations": []},
    {"canonical_name": "accounts_receivable", "label": "accounts receivable", "definition": "asset measure that represents amounts due from customers", "synonyms": ["accounts receivable", "receivables", "trade receivables"], "abbreviations": []},
    {"canonical_name": "long_term_debt", "label": "long term debt", "definition": "liability measure that represents debt due beyond one year", "synonyms": ["long-term debt", "long term debt"], "abbreviations": []},
    {"canonical_name": "current_assets", "label": "current assets", "definition": "asset measure that represents assets expected to be converted to cash within one operating cycle", "synonyms": ["current assets", "total current assets"], "abbreviations": []},
    {"canonical_name": "current_liabilities", "label": "current liabilities", "definition": "liability measure that represents obligations due within one operating cycle", "synonyms": ["current liabilities", "total current liabilities"], "abbreviations": []},
    {"canonical_name": "retained_earnings", "label": "retained earnings", "definition": "equity measure that represents cumulative earnings retained by an entity", "synonyms": ["retained earnings", "accumulated deficit"], "abbreviations": []},
]


def avg(values: list[Any]) -> float:
    nums = [float(v) for v in values if v is not None]
    return round(sum(nums) / len(nums), 4) if nums else 0.0


def utc_now() -> str:
    return c.utc_now()


def update_state(data: dict[str, Any]) -> None:
    base: dict[str, Any] = {}
    if STATE_FILE.exists():
        for attempt in range(12):
            try:
                base = c.read_json(STATE_FILE)
                break
            except PermissionError:
                if attempt == 11:
                    base = {}
                    break
                time.sleep(0.5 * (attempt + 1))
    base.update(data)
    base["updated_at"] = utc_now()
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(base, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    for attempt in range(12):
        tmp = STATE_FILE.with_suffix(STATE_FILE.suffix + f".{time.time_ns()}.tmp")
        try:
            tmp.write_text(payload, encoding="utf-8", newline="\n")
            tmp.replace(STATE_FILE)
            return
        except PermissionError:
            try:
                if tmp.exists():
                    tmp.unlink()
            except OSError:
                pass
            if attempt == 11:
                raise
            time.sleep(0.5 * (attempt + 1))


def read_jsonl_lenient(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    if not path.exists():
        return rows
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        if not line.strip():
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return rows


def safe_print_json(data: Any, indent: int | None = None) -> None:
    try:
        print(json.dumps(data, ensure_ascii=False, indent=indent), flush=True)
    except OSError:
        pass


def stable_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


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


def call_openai_json(messages: list[dict[str, str]], response_format: dict[str, Any], max_tokens: int = 3500) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    payload = {
        "model": MODEL,
        "messages": messages,
        "temperature": 0,
        "response_format": response_format,
        "max_tokens": max_tokens,
    }
    req = urllib.request.Request(
        "https://api.openai.com/v1/chat/completions",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {c.r8.openai_api_key()}"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=180) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        raise c.r8.ProviderError(classify_http(exc.code), sanitize_error(exc)) from exc
    except (socket.timeout, TimeoutError) as exc:
        raise c.r8.ProviderError("provider_timeout", sanitize_error(exc)) from exc
    except urllib.error.URLError as exc:
        raise c.r8.ProviderError("provider_unknown", sanitize_error(exc)) from exc
    except json.JSONDecodeError as exc:
        raise c.r8.ProviderError("provider_bad_response", "provider returned invalid JSON envelope") from exc
    try:
        return extract_json_object(data["choices"][0]["message"]["content"]), data.get("usage", {}), data
    except Exception as exc:
        raise c.r8.ProviderError("provider_bad_response", "provider returned non-JSON model output") from exc


def call_with_retries(messages: list[dict[str, str]], response_format: dict[str, Any], max_tokens: int = 3500) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    last: c.r8.ProviderError | None = None
    for attempt in range(1, 4):
        try:
            return call_openai_json(messages, response_format, max_tokens=max_tokens)
        except c.r8.ProviderError as exc:
            last = exc
            if exc.error_type in {"provider_auth", "provider_bad_response"}:
                break
            if attempt < 3:
                time.sleep(2**attempt)
    assert last is not None
    raise last


def call_embeddings(texts: list[str]) -> list[list[float]]:
    payload = {"model": EMBEDDING_MODEL, "input": texts}
    req = urllib.request.Request(
        "https://api.openai.com/v1/embeddings",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {c.r8.openai_api_key()}"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=180) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        raise c.r8.ProviderError(classify_http(exc.code), sanitize_error(exc)) from exc
    except (socket.timeout, TimeoutError) as exc:
        raise c.r8.ProviderError("provider_timeout", sanitize_error(exc)) from exc
    except urllib.error.URLError as exc:
        raise c.r8.ProviderError("provider_unknown", sanitize_error(exc)) from exc
    return [row["embedding"] for row in sorted(data["data"], key=lambda x: x["index"])]


def read_csv_rows() -> list[dict[str, Any]]:
    with ALL_SLICES_CSV.open("r", encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def used_hashes() -> set[str]:
    hashes: set[str] = set()
    for path in (ROOT / "outputs").glob("round*_case_selection/finder_candidates.jsonl"):
        for row in c.read_jsonl(path):
            h = str(row.get("case_id", "")).split("_")[-1]
            if len(h) == 8:
                hashes.add(h)
    return hashes


def audit_inputs() -> dict[str, Any]:
    manifest = c.read_json(MANIFEST)
    rows = read_csv_rows()
    csv_ids = [str(r["_id"]) for r in rows]
    manifest_ids = [cid for s in manifest["slices"].values() for cid in s["case_ids"]]
    used = used_hashes()
    profile = {}
    for sname, sdef in manifest["slices"].items():
        fresh = [cid for cid in sdef["case_ids"] if cid not in used]
        profile[sname] = {"total": len(sdef["case_ids"]), "fresh_heldout": len(fresh), "definition": sdef["definition"]}
    audit = {
        "csv_rows": len(rows),
        "unique_ids": len(set(csv_ids)),
        "duplicate_ids": len(csv_ids) - len(set(csv_ids)),
        "manifest_total": manifest["selected_unique_total"],
        "manifest_ids_missing_in_csv": len(set(manifest_ids) - set(csv_ids)),
        "held_out_excluded_hashes": len(used),
        "missing_evidence": sum(1 for r in rows if not str(r.get("references_joined", "")).strip()),
        "missing_answer": sum(1 for r in rows if not str(r.get("answer", "")).strip()),
        "slice_profile": profile,
    }
    audit["phase0_gate_pass"] = audit["duplicate_ids"] == 0 and audit["manifest_ids_missing_in_csv"] == 0 and profile["S1_FIN_COMP"]["fresh_heldout"] >= 100
    c.write_json(AUDIT_FILE, audit)
    update_state({"round": ROUND, "phase": "audit_done", "kg_batch": KG_BATCH, "audit": audit, "neo4j_write_performed": False})
    return audit


def normalize_metric(value: Any) -> str:
    return ie.normalize_metric_name(value)


def synonym_index() -> dict[str, str]:
    index: dict[str, str] = {}
    for metric in METRIC_DICTIONARY:
        canonical = metric["canonical_name"]
        index[normalize_metric(canonical)] = canonical
        index[normalize_metric(metric["label"])] = canonical
        for synonym in metric.get("synonyms", []):
            index[normalize_metric(synonym)] = canonical
        for abbr in metric.get("abbreviations", []):
            index[normalize_metric(abbr)] = canonical
    return index


def map_to_canonical(raw_metric: Any) -> str | None:
    raw = normalize_metric(raw_metric)
    if not raw or raw in BAD_CANONICAL or raw.startswith("source_value"):
        return None
    idx = synonym_index()
    if raw in idx:
        return idx[raw]
    if raw in {"total_net_revenue", "total_net_revenues"}:
        return "total_net_revenue"
    if "available_to_common" in raw or "available_common" in raw or "attributable_to_common" in raw:
        return "net_income_attributable_common"
    if "share" in raw and ("weighted" in raw or "average" in raw or "outstanding" in raw):
        return "diluted_shares" if "diluted" in raw else "basic_shares"
    if ("other" in raw or "product" in raw or "subscription" in raw or "segment" in raw) and ("revenue" in raw or "revenues" in raw or "sales" in raw):
        return None
    if "cost" in raw and ("revenue" in raw or "sales" in raw or "goods_sold" in raw):
        return "cost_of_sales"
    if "gross" in raw and "profit" in raw:
        return "gross_profit"
    if ("operating" in raw or "operations" in raw) and ("income" in raw or "loss" in raw or "earnings" in raw):
        return "operating_income"
    if "operating" in raw and "expense" in raw:
        return "operating_expense"
    if "net" in raw and ("income" in raw or "earnings" in raw or "loss" in raw):
        if "per_share" in raw or "earnings_per_share" in raw or raw.endswith("_eps"):
            return "diluted_eps" if "diluted" in raw else "basic_eps"
        return "net_income"
    if ("revenue" in raw or "revenues" in raw or raw == "sales" or raw.endswith("_sales")) and "cost" not in raw:
        return "total_revenue"
    if "diluted" in raw and ("eps" in raw or "per_share" in raw or "earnings_per_share" in raw):
        return "diluted_eps"
    if "basic" in raw and ("eps" in raw or "per_share" in raw or "earnings_per_share" in raw):
        return "basic_eps"
    if "diluted" in raw and "share" in raw:
        return "diluted_shares"
    if "basic" in raw and "share" in raw:
        return "basic_shares"
    if "tax" in raw:
        return "income_tax_expense"
    if "income_before" in raw or "earnings_before_tax" in raw:
        return "income_before_taxes"
    if "research" in raw and "development" in raw:
        return "research_and_development_expense"
    if ("selling" in raw or "general" in raw or "administrative" in raw or "marketing" in raw) and "expense" in raw:
        return "selling_general_admin_expense"
    if "employee" in raw or "workforce" in raw or "headcount" in raw:
        return "total_employees"
    if raw in {m["canonical_name"] for m in METRIC_DICTIONARY}:
        return raw
    return None


def write_metric_dictionary() -> None:
    names = [m["canonical_name"] for m in METRIC_DICTIONARY]
    labels = [m["label"] for m in METRIC_DICTIONARY]
    bad = [n for n in names if not CANONICAL_RE.match(n) or n in BAD_CANONICAL]
    duplicate_names = [k for k, v in Counter(names).items() if v > 1]
    duplicate_labels = [k for k, v in Counter(labels).items() if v > 1]
    missing = [m["canonical_name"] for m in METRIC_DICTIONARY if not m.get("label") or not m.get("definition")]
    passed = not bad and not duplicate_names and not duplicate_labels and not missing
    c.write_json(METRIC_DICT_FILE, METRIC_DICTIONARY)
    c.write_json(SYNONYM_FILE, synonym_index())
    c.write_text(
        HYGIENE_FILE,
        "\n".join(
            [
                "# Metric Dictionary Hygiene Report",
                "",
                f"- canonical metrics: {len(METRIC_DICTIONARY)}",
                f"- unique canonical_name: {len(set(names)) == len(names)}",
                f"- unique label: {len(set(labels)) == len(labels)}",
                f"- missing label/definition: {missing}",
                f"- invalid canonical names: {bad}",
                f"- duplicate canonical names: {duplicate_names}",
                f"- duplicate labels: {duplicate_labels}",
                f"- hygiene_gate_pass: {passed}",
            ]
        ),
    )
    c.write_text(
        CQ_FILE,
        "\n".join(
            [
                "# Competency Questions",
                "",
                "CQ1. Can the KG retrieve the same canonical metric for multiple companies in the same fiscal year?",
                "CQ2. Can the KG compare margin measures across companies using aligned numerator and denominator observations?",
                "CQ3. Can the KG identify which company improved faster across multiple fiscal years for a given metric?",
            ]
        ),
    )
    update_state({"phase": "metric_dictionary_done", "metric_dictionary_gate_pass": passed, "canonical_metrics": len(METRIC_DICTIONARY), "neo4j_write_performed": False})
    if not passed:
        raise RuntimeError("Metric dictionary hygiene gate failed")


def ticker_candidate(token: str) -> str:
    token = token.strip(" .,;:()[]{}").upper()
    if not re.fullmatch(r"[A-Z]{1,5}", token):
        return ""
    if token in FINANCE_TOKEN_STOP:
        return ""
    return token


def infer_ticker(row: dict[str, Any]) -> str:
    query = str(row.get("query", ""))
    evidence = str(row.get("references_joined", ""))
    for token in re.findall(r"\(([A-Z]{1,5})\)", query):
        candidate = ticker_candidate(token)
        if candidate:
            return candidate
    for pattern in [
        r",\s*([A-Z]{1,5})\.?\s*$",
        r"\bfor\s+([A-Z]{1,5})\b",
        r"\b([A-Z]{1,5})\s+FY\d{2,4}\b",
        r"\b([A-Z]{1,5})\s+\d{4}\b",
        r"^([A-Z]{1,5})\s+(?:EPS|revenue|sales|margin|income|operating|net)\b",
    ]:
        match = re.search(pattern, query)
        if match:
            candidate = ticker_candidate(match.group(1))
            if candidate:
                return candidate
    tokens = [ticker_candidate(t) for t in re.findall(r"\b[A-Z]{1,5}\b", query)]
    tokens = [t for t in tokens if t]
    unique = list(dict.fromkeys(tokens))
    if len(unique) == 1:
        return unique[0]
    company_hint = re.match(r"^([A-Z][A-Za-z&.\-]+)", query.strip())
    if company_hint:
        return ticker_candidate(company_hint.group(1)[:5]) or f"C{stable_hash(query)[:5].upper()}"
    return f"C{stable_hash(query)[:5].upper()}"


def row_to_case(row: dict[str, Any]) -> dict[str, Any]:
    evidence = str(row.get("references_joined", ""))
    ticker = infer_ticker(row)
    return {
        "case_id": str(row["_id"]),
        "source_id": str(row["_id"]),
        "ticker": ticker,
        "company": c.r8.infer_company(evidence, ticker),
        "question": str(row.get("query", "")),
        "answer": str(row.get("answer", "")),
        "evidence_text": evidence,
        "slice": str(row.get("slice", "")),
        "source_dataset": "FinDER",
        "category": str(row.get("category", "")),
        "type": str(row.get("type", "")),
        "n_refs": int(row.get("n_refs") or 0),
        "years": c.r8.years_in_text(evidence + "\n" + str(row.get("query", ""))),
    }


def load_round14_cases() -> dict[str, dict[str, Any]]:
    return {str(row["_id"]): row_to_case(row) for row in read_csv_rows()}


def fresh_ids_by_slice() -> dict[str, list[str]]:
    manifest = c.read_json(MANIFEST)
    used = used_hashes()
    return {name: [cid for cid in sdef["case_ids"] if cid not in used] for name, sdef in manifest["slices"].items()}


def pilot_ids() -> list[str]:
    fresh = fresh_ids_by_slice()
    rng = random.Random(42)
    s1 = list(fresh["S1_FIN_COMP"])
    s6 = list(fresh["S6_BASELINE_SINGLE"])
    rng.shuffle(s1)
    rng.shuffle(s6)
    return s1[:20] + s6[:5]


def extraction_system_prompt() -> str:
    canonical = ", ".join(m["canonical_name"] for m in METRIC_DICTIONARY)
    return (
        ie.SYSTEM_PROMPT
        + "\nUse the closest canonical metric when possible. Preferred canonical metrics are:\n"
        + canonical
        + "\nNever emit source_value, value, operand, program_operand, or generic metric names."
    )


def extraction_prompt(case: dict[str, Any]) -> str:
    return "\n".join(
        [
            f"case_id: {case['case_id']}",
            f"ticker: {case.get('ticker', '')}",
            f"company: {case.get('company', '')}",
            f"years: {case.get('years', [])}",
            "",
            "Extract observations from this evidence_text only:",
            str(case.get("evidence_text") or ""),
        ]
    )


def quote_verified(quote: str, evidence_text: str) -> bool:
    if quote in evidence_text:
        return True
    def norm(text: str) -> str:
        text = text.replace("$", " ")
        text = re.sub(r"\s+", " ", text)
        return text.strip().lower()
    return norm(quote) in norm(evidence_text)


def repair_quote(quote: str, evidence_text: str) -> str:
    if quote_verified(quote, evidence_text):
        return quote
    numbers = re.findall(r"\(?\$?\s*-?\d[\d,]*(?:\.\d+)?\)?", quote or "")
    metric_text = re.split(r"\(?\$?\s*-?\d", quote or "", maxsplit=1)[0]
    metric_tokens = [t.lower() for t in re.findall(r"[A-Za-z]{3,}", metric_text)[:3]]
    evidence_lines = [line.strip() for line in evidence_text.splitlines() if line.strip()]
    for number in numbers:
        number_compact = number.replace("$", "").replace(" ", "")
        for line in evidence_lines:
            line_compact = line.replace("$", "").replace(" ", "")
            lower = line.lower()
            if number_compact in line_compact and (not metric_tokens or any(token in lower for token in metric_tokens)):
                return line[:500]
    return quote


def extract_case(case: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    messages = [
        {"role": "system", "content": extraction_system_prompt()},
        {"role": "user", "content": extraction_prompt(case)},
    ]
    schema_format = {"type": "json_schema", "json_schema": {"name": "financial_observations", "strict": True, "schema": ie.RESPONSE_SCHEMA}}
    try:
        return call_with_retries(messages, schema_format, max_tokens=3500)
    except c.r8.ProviderError as exc:
        if "response_format" not in str(exc).lower() and "json_schema" not in str(exc).lower():
            raise
        fallback = [
            {"role": "system", "content": extraction_system_prompt() + "\nReturn only valid JSON with top-level observations array."},
            messages[1],
        ]
        return call_with_retries(fallback, {"type": "json_object"}, max_tokens=3500)


def canonicalize_observations(case: dict[str, Any], raw_obs: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    valid, skipped = ie.validate_observations(case, raw_obs)
    mapped: list[dict[str, Any]] = []
    unmapped: list[dict[str, Any]] = []
    seen: set[tuple[str, str, int, float]] = set()
    recovered: list[dict[str, Any]] = []
    for skipped_row in skipped:
        raw = skipped_row.get("raw", {})
        if skipped_row.get("reason") != "metric_artifact":
            continue
        canonical = map_to_canonical(raw.get("metric_canonical"))
        if canonical is None:
            continue
        try:
            year = int(raw.get("year"))
            value = float(raw.get("value"))
        except (TypeError, ValueError):
            continue
        unit = str(raw.get("unit") or "").strip()
        quote = repair_quote(str(raw.get("evidence_quote") or "").strip(), str(case.get("evidence_text") or ""))
        if year < 2000 or year > 2030 or not math.isfinite(value) or unit not in ALLOWED_UNITS or not quote:
            continue
        recovered.append(
            {
                "obs_id": ie.observation_id(str(case["case_id"]), str(case["ticker"]).strip().upper(), canonical, year),
                "ticker": str(case["ticker"]).strip().upper(),
                "metric_canonical": canonical,
                "metric_display": canonical.replace("_", " ").title(),
                "year": year,
                "value": value,
                "unit": unit,
                "evidence_quote": quote,
                "case_id": str(case["case_id"]),
                "evidence_quote_verified": quote_verified(quote, str(case.get("evidence_text") or "")),
                "recovered_from_skip": True,
            }
        )
    valid = valid + recovered
    for obs in valid:
        canonical = map_to_canonical(obs.get("metric_canonical"))
        quote_lower = str(obs.get("evidence_quote", "")).lower()
        display_lower = str(obs.get("metric_display", "")).lower()
        if canonical == "cost_of_sales" and "total" not in quote_lower and (
            "cost of products" in quote_lower
            or "cost of services" in quote_lower
            or "cost of product" in display_lower
            or "cost of service" in display_lower
        ):
            canonical = None
        if canonical is None or not CANONICAL_RE.match(canonical):
            unmapped.append({"case_id": case["case_id"], "ticker": case.get("ticker", ""), "raw_metric": obs.get("metric_canonical"), "reason": "unmapped", "observation": obs})
            continue
        obs = dict(obs)
        obs["raw_metric_canonical"] = obs["metric_canonical"]
        obs["metric_canonical"] = canonical
        obs["metric_display"] = next((m["label"] for m in METRIC_DICTIONARY if m["canonical_name"] == canonical), canonical.replace("_", " "))
        obs["company"] = case.get("company", "")
        obs["ticker"] = str(case.get("ticker", "")).upper()
        obs["slice"] = case.get("slice", "")
        obs["evidence_quote"] = repair_quote(str(obs.get("evidence_quote", "")), str(case.get("evidence_text") or ""))
        obs["evidence_quote_verified"] = quote_verified(str(obs.get("evidence_quote", "")), str(case.get("evidence_text") or ""))
        key = (obs["ticker"], canonical, int(obs["year"]), round(float(obs["value"]), 6))
        if key in seen:
            continue
        seen.add(key)
        mapped.append(obs)
    return mapped, skipped, unmapped


def load_extraction_rows(path: Path) -> list[dict[str, Any]]:
    return c.read_jsonl(path)


def append_unmapped(rows: list[dict[str, Any]]) -> None:
    for row in rows:
        c.append_jsonl(UNMAPPED_FILE, row)


def run_extraction(case_ids: list[str], out_path: Path, phase: str) -> list[dict[str, Any]]:
    cases = load_round14_cases()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    rows = load_extraction_rows(out_path)
    done = {row["case_id"] for row in rows}
    update_state({"phase": phase, "phase_total": len(case_ids), "phase_done": len(done), "neo4j_write_performed": False})
    for idx, cid in enumerate(case_ids, start=1):
        if cid in done:
            continue
        case = cases[cid]
        safe_print_json({"phase": phase, "progress": f"{idx}/{len(case_ids)}", "case_id": cid, "ticker": case.get("ticker", "")})
        try:
            parsed, usage, raw = extract_case(case)
            raw_obs = parsed.get("observations", [])
            if not isinstance(raw_obs, list):
                raise ValueError("observations is not a list")
            observations, skipped, unmapped = canonicalize_observations(case, raw_obs)
            row = {
                "case_id": cid,
                "ticker": case.get("ticker", ""),
                "company": case.get("company", ""),
                "slice": case.get("slice", ""),
                "success": True,
                "observations": observations,
                "raw_count": len(raw_obs),
                "mapped_count": len(observations),
                "skipped": skipped,
                "skipped_count": len(skipped),
                "unmapped_count": len(unmapped),
                "usage": usage,
                "raw_response": raw,
                "model": MODEL,
            }
            append_unmapped(unmapped)
        except Exception as exc:
            row = {
                "case_id": cid,
                "ticker": case.get("ticker", ""),
                "company": case.get("company", ""),
                "slice": case.get("slice", ""),
                "success": False,
                "observations": [],
                "raw_count": 0,
                "mapped_count": 0,
                "skipped": [],
                "skipped_count": 0,
                "unmapped_count": 0,
                "error": str(exc)[:500],
                "model": MODEL,
            }
        c.append_jsonl(out_path, row)
        rows.append(row)
        done.add(cid)
        update_state(
            {
                "phase": phase,
                "phase_done": len(rows),
                "observations_total": sum(len(r.get("observations", [])) for r in rows),
                "extraction_failed_cases": sum(1 for r in rows if not r.get("success")),
                "neo4j_write_performed": False,
            }
        )
        time.sleep(0.15)
    return rows


def value_is_year(value: Any) -> bool:
    try:
        v = float(value)
    except (TypeError, ValueError):
        return False
    return v.is_integer() and 2015 <= v <= 2030


def duplicate_consistency(observations: list[dict[str, Any]]) -> float:
    groups: dict[tuple[str, str, int], list[float]] = defaultdict(list)
    for obs in observations:
        groups[(str(obs.get("ticker", "")), str(obs["metric_canonical"]), int(obs["year"]))].append(float(obs["value"]))
    checked = 0
    ok = 0
    for values in groups.values():
        if len(values) < 2:
            continue
        checked += 1
        base = values[0]
        ok += int(all(abs(v - base) <= max(abs(base) * 0.01, 0.1) for v in values[1:]))
    return 1.0 if checked == 0 else round(ok / checked, 4)


def smoke_gate(rows: list[dict[str, Any]]) -> dict[str, Any]:
    observations = [obs for row in rows for obs in row.get("observations", [])]
    raw_count = sum(int(row.get("raw_count", 0)) for row in rows)
    mapped_count = len(observations)
    placeholder_count = sum(1 for obs in observations if str(obs.get("metric_canonical", "")).startswith("source_value"))
    mapped_ratio = round(mapped_count / raw_count, 4) if raw_count else 0.0
    avg_years = avg([len({obs.get("year") for obs in row.get("observations", [])}) for row in rows])
    value_as_year = sum(1 for obs in observations if value_is_year(obs.get("value")))
    quote_verified_ratio = round(sum(1 for obs in observations if obs.get("evidence_quote_verified")) / len(observations), 4) if observations else 0.0
    duplicate_ok = duplicate_consistency(observations)
    report = {
        "cases": len(rows),
        "observations": len(observations),
        "raw_count": raw_count,
        "placeholder_count": placeholder_count,
        "canonical_mapped_ratio": mapped_ratio,
        "avg_distinct_years_per_case": avg_years,
        "value_as_year_count": value_as_year,
        "evidence_quote_verified_ratio": quote_verified_ratio,
        "duplicate_consistency_ratio": duplicate_ok,
    }
    report["smoke_gate_pass"] = (
        placeholder_count == 0
        and mapped_ratio >= 0.70
        and avg_years >= 2.0
        and value_as_year <= 1
        and quote_verified_ratio >= 0.90
        and duplicate_ok >= 0.95
    )
    lines = [
        "# Round14 Pilot Smoke Gate",
        "",
        "| Gate | Value | Pass |",
        "|---|---:|---|",
        f"| placeholder_count == 0 | {placeholder_count} | {placeholder_count == 0} |",
        f"| canonical_mapped_ratio >= 0.70 | {mapped_ratio} | {mapped_ratio >= 0.70} |",
        f"| avg_distinct_years_per_case >= 2 | {avg_years} | {avg_years >= 2.0} |",
        f"| value_as_year_count <= 1 | {value_as_year} | {value_as_year <= 1} |",
        f"| evidence_quote_verified_ratio >= 0.90 | {quote_verified_ratio} | {quote_verified_ratio >= 0.90} |",
        f"| duplicate_consistency_ratio >= 0.95 | {duplicate_ok} | {duplicate_ok >= 0.95} |",
        "",
        f"**smoke_gate_pass:** {report['smoke_gate_pass']}",
    ]
    c.write_text(SMOKE_FILE, "\n".join(lines))
    update_state({"phase": "smoke_gate_passed" if report["smoke_gate_pass"] else "smoke_gate_failed", "smoke_gate": report, "neo4j_write_performed": False})
    if not report["smoke_gate_pass"]:
        raise RuntimeError(f"Smoke gate failed: {report}")
    return report


def create_driver() -> tuple[Any, str]:
    env = ie.effective_neo4j_env()
    return ie.create_neo4j_driver(env), env["NEO4J_DATABASE"]


def write_rollback_file() -> None:
    ROLLBACK_FILE.parent.mkdir(parents=True, exist_ok=True)
    ROLLBACK_FILE.write_text(f"MATCH (o:LLMObservation {{kg_batch:'{KG_BATCH}'}}) DETACH DELETE o;\n", encoding="utf-8", newline="\n")


def assert_batch_absent(driver: Any, database: str) -> None:
    with driver.session(database=database) as session:
        record = session.run("MATCH (o:LLMObservation {kg_batch:$batch}) RETURN count(o) AS n", batch=KG_BATCH).single()
    n = int(record["n"] if record else 0)
    if n:
        raise RuntimeError(f"KG batch collision: {KG_BATCH} already has {n} LLMObservation nodes")


def existing_batch_obs_ids(driver: Any, database: str) -> set[str]:
    with driver.session(database=database) as session:
        rows = session.run(
            "MATCH (o:LLMObservation {kg_batch:$batch}) RETURN o.obs_id AS obs_id",
            batch=KG_BATCH,
        )
        return {str(row["obs_id"]) for row in rows if row["obs_id"]}


def preflight_neo4j(allow_existing_batch_resume: bool = False) -> dict[str, Any]:
    write_rollback_file()
    driver, database = create_driver()
    existing_count = 0
    try:
        existing = existing_batch_obs_ids(driver, database)
        existing_count = len(existing)
        if existing_count and not allow_existing_batch_resume:
            raise RuntimeError(f"KG batch collision: {KG_BATCH} already has {existing_count} LLMObservation nodes")
    finally:
        driver.close()
    result = {
        "kg_batch": KG_BATCH,
        "batch_collision_checked": True,
        "existing_batch_observations": existing_count,
        "allow_existing_batch_resume": allow_existing_batch_resume,
        "rollback_file": str(ROLLBACK_FILE),
        "rollback_exists": ROLLBACK_FILE.exists(),
    }
    update_state({"phase": "preflight_ok", **result, "neo4j_write_performed": False})
    return result


def metric_definition(canonical: str) -> dict[str, Any]:
    return next((m for m in METRIC_DICTIONARY if m["canonical_name"] == canonical), {"canonical_name": canonical, "label": canonical.replace("_", " "), "definition": ""})


def obs_id(case_id: str, ticker: str, canonical: str, year: int, index: int) -> str:
    return f"{KG_BATCH}__{case_id}__{ticker}__{canonical}__{year}__{index:02d}"


def write_observation(session: Any, row: dict[str, Any], obs: dict[str, Any], index: int) -> str:
    canonical = str(obs["metric_canonical"])
    year = int(obs["year"])
    ticker = str(obs.get("ticker") or row.get("ticker") or "").upper()
    definition = metric_definition(canonical)
    oid = obs_id(str(row["case_id"]), ticker, canonical, year, index)
    session.run(
        """
MERGE (co:LLMCompany {ticker: $ticker, kg_batch: $batch})
  ON CREATE SET co.name = $company, co.created_at = $created_at
MERGE (yr:LLMFiscalYear {year: $year, kg_batch: $batch})
  ON CREATE SET yr.created_at = $created_at
MERGE (m:LLMFinancialMetric {canonical_name: $metric, kg_batch: $batch})
  ON CREATE SET m.display_name = $display, m.label = $label, m.definition = $definition, m.created_at = $created_at
MERGE (obs:LLMObservation {obs_id: $obs_id})
SET obs.value = $value,
    obs.unit = $unit,
    obs.evidence_quote = $quote,
    obs.kg_batch = $batch,
    obs.batch_id = $batch,
    obs.extraction_method = 'round14_canonical_ie',
    obs.validation_status = 'canonical_ie_validated',
    obs.case_id = $case_id,
    obs.source_dataset = 'FinDER',
    obs.ticker = $ticker,
    obs.metric_canonical = $metric,
    obs.year = $year,
    obs.slice = $slice,
    obs.created_at = coalesce(obs.created_at, $created_at)
MERGE (obs)-[:LLM_MENTIONS_COMPANY]->(co)
MERGE (obs)-[:LLM_OBSERVES_METRIC]->(m)
MERGE (obs)-[:LLM_OBSERVED_IN_YEAR]->(yr)
""",
        ticker=ticker,
        company=row.get("company") or ticker,
        year=year,
        metric=canonical,
        display=obs.get("metric_display") or canonical.replace("_", " "),
        label=definition.get("label", canonical.replace("_", " ")),
        definition=definition.get("definition", ""),
        obs_id=oid,
        value=float(obs["value"]),
        unit=str(obs.get("unit", "currency_millions")),
        quote=str(obs.get("evidence_quote", ""))[:500],
        batch=KG_BATCH,
        case_id=row["case_id"],
        slice=row.get("slice", ""),
        created_at=utc_now(),
    )
    return oid


def write_kg(rows: list[dict[str, Any]], allow_existing_batch_resume: bool = False) -> int:
    write_rollback_file()
    driver, database = create_driver()
    try:
        existing_ids = existing_batch_obs_ids(driver, database)
        if existing_ids and not allow_existing_batch_resume:
            raise RuntimeError(f"KG batch collision: {KG_BATCH} already has {len(existing_ids)} LLMObservation nodes")
        log_ids = {row["obs_id"] for row in read_jsonl_lenient(WRITE_LOG) if row.get("obs_id")}
        written_ids = set(existing_ids)
        update_state({"phase": "writing_neo4j", "neo4j_write_performed": True, "kg_batch": KG_BATCH, "rollback_file": str(ROLLBACK_FILE)})
        with driver.session(database=database) as session:
            for row in rows:
                for idx, obs in enumerate(row.get("observations", []), start=1):
                    oid = obs_id(row["case_id"], str(obs.get("ticker", row.get("ticker", ""))).upper(), obs["metric_canonical"], int(obs["year"]), idx)
                    if oid in written_ids:
                        continue
                    actual = write_observation(session, row, obs, idx)
                    written_ids.add(actual)
                    if actual not in log_ids:
                        c.append_jsonl(WRITE_LOG, {"obs_id": actual, "case_id": row["case_id"], "ticker": row.get("ticker", ""), "kg_batch": KG_BATCH, "created_at": utc_now()})
                        log_ids.add(actual)
                update_state({"phase": "writing_neo4j", "observations_written": len(written_ids), "neo4j_write_performed": True})
        return len(existing_batch_obs_ids(driver, database))
    finally:
        driver.close()


def observations_from_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for row in rows:
        for obs in row.get("observations", []):
            item = dict(obs)
            item["case_id"] = row["case_id"]
            item["company"] = row.get("company", "")
            item["slice"] = row.get("slice", "")
            out.append(item)
    return out


def write_coverage_report(rows: list[dict[str, Any]]) -> dict[str, Any]:
    obs = observations_from_rows(rows)
    companies = {o.get("ticker") for o in obs if o.get("ticker")}
    metrics = {o.get("metric_canonical") for o in obs if o.get("metric_canonical")}
    cells: dict[tuple[str, int], set[str]] = defaultdict(set)
    for o in obs:
        cells[(str(o["metric_canonical"]), int(o["year"]))].add(str(o.get("ticker", "")))
    comparable = {k: len(v) for k, v in cells.items() if len(v) >= 2}
    report = {
        "companies": len(companies),
        "canonical_metrics": len(metrics),
        "observations": len(obs),
        "comparable_metric_year_cells": len(comparable),
        "top_comparable_cells": sorted([{"metric": k[0], "year": k[1], "companies": v} for k, v in comparable.items()], key=lambda x: (-x["companies"], x["metric"], x["year"]))[:20],
    }
    lines = [
        "# Round14 KG Coverage Report",
        "",
        f"- companies: {report['companies']}",
        f"- canonical metrics: {report['canonical_metrics']}",
        f"- observations: {report['observations']}",
        f"- comparable metric-year cells: {report['comparable_metric_year_cells']}",
        "",
        "| metric | year | companies |",
        "|---|---:|---:|",
    ]
    for row in report["top_comparable_cells"]:
        lines.append(f"| {row['metric']} | {row['year']} | {row['companies']} |")
    c.write_text(KG_COVERAGE_FILE, "\n".join(lines))
    update_state({"phase": "kg_coverage_done", "kg_coverage": report})
    return report


def choose_pairs(items: list[dict[str, Any]], target: int, cap: int = 8) -> list[dict[str, Any]]:
    counts: Counter[str] = Counter()
    selected: list[dict[str, Any]] = []
    for item in sorted(items, key=lambda x: stable_hash(json.dumps(x, sort_keys=True))):
        a = item["company_a"]
        b = item["company_b"]
        if counts[a] >= cap or counts[b] >= cap:
            continue
        selected.append(item)
        counts[a] += 1
        counts[b] += 1
        if len(selected) >= target:
            break
    return selected


def visible_contract(case: dict[str, Any]) -> dict[str, Any]:
    return {
        "formula_type": case["formula_type"],
        "task": case["question"],
        "formula": case["formula"],
        "target_slots": [slot["target_slot_name"] for slot in case["target_slots"]],
        "rounding": "Report numeric values clearly. Include the compared values and the final difference or percentage-point difference.",
    }


def scorer_contract(case: dict[str, Any]) -> dict[str, Any]:
    return {
        "formula_type": case["formula_type"],
        "target_slots": case["target_slots"],
        "source_fact_numbers": case["source_fact_numbers"],
    }


def slot(name: str, value: float) -> dict[str, Any]:
    return {"target_slot_name": name, "expected_value": round(float(value), 4)}


def build_cross_company_cases(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    obs = observations_from_rows(rows)
    by_metric_year: dict[tuple[str, int], list[dict[str, Any]]] = defaultdict(list)
    by_company_metric_year: dict[tuple[str, str, int], dict[str, Any]] = {}
    for o in obs:
        if not o.get("ticker") or not o.get("metric_canonical") or value_is_year(o.get("value")):
            continue
        key = (str(o["ticker"]), str(o["metric_canonical"]), int(o["year"]))
        by_company_metric_year.setdefault(key, o)
        by_metric_year[(str(o["metric_canonical"]), int(o["year"]))].append(o)

    direct_metrics = {"total_revenue", "gross_profit", "operating_income", "net_income", "operating_expense", "cost_of_sales", "income_tax_expense"}
    l1_candidates: list[dict[str, Any]] = []
    for (metric, year), vals in by_metric_year.items():
        if metric not in direct_metrics:
            continue
        best_by_ticker = {str(v["ticker"]): v for v in vals}
        tickers = sorted(best_by_ticker)
        for i, a in enumerate(tickers):
            for b in tickers[i + 1 :]:
                va = best_by_ticker[a]
                vb = best_by_ticker[b]
                diff = abs(float(va["value"]) - float(vb["value"]))
                if diff <= 0:
                    continue
                winner = a if float(va["value"]) > float(vb["value"]) else b
                case_id = f"r14_l1_{stable_hash(metric + str(year) + a + b)}"
                l1_candidates.append(
                    {
                        "case_id": case_id,
                        "level": "L1_direct",
                        "formula_type": "cross_company_direct_metric",
                        "metric": metric,
                        "year": year,
                        "company_a": a,
                        "company_b": b,
                        "question": f"Compare {metric.replace('_', ' ')} for {a} and {b} in fiscal {year}. Which company is higher, and by how much?",
                        "formula": "difference = abs(company_a_value - company_b_value)",
                        "target_slots": [slot("company_a_value", va["value"]), slot("company_b_value", vb["value"]), slot("difference", diff)],
                        "winner": winner,
                        "source_fact_numbers": [float(va["value"]), float(vb["value"])],
                        "source_observations": [va, vb],
                    }
                )
    l1 = choose_pairs(l1_candidates, 40)

    margin_specs = [
        ("gross_margin", "gross_profit", "total_revenue"),
        ("operating_margin", "operating_income", "total_revenue"),
        ("net_margin", "net_income", "total_revenue"),
    ]
    l2_candidates: list[dict[str, Any]] = []
    for ratio_name, numerator, denominator in margin_specs:
        cells: dict[int, list[tuple[str, dict[str, Any], dict[str, Any], float]]] = defaultdict(list)
        for (ticker, metric, year), num_obs in by_company_metric_year.items():
            if metric != numerator:
                continue
            den_obs = by_company_metric_year.get((ticker, denominator, year))
            if not den_obs:
                continue
            den = float(den_obs["value"])
            if den == 0:
                continue
            cells[year].append((ticker, num_obs, den_obs, float(num_obs["value"]) / den * 100.0))
        for year, vals in cells.items():
            vals_by = {v[0]: v for v in vals}
            tickers = sorted(vals_by)
            for i, a in enumerate(tickers):
                for b in tickers[i + 1 :]:
                    ta, numa, dena, ra = vals_by[a]
                    tb, numb, denb, rb = vals_by[b]
                    diff = abs(ra - rb)
                    if diff <= 0:
                        continue
                    winner = a if ra > rb else b
                    l2_candidates.append(
                        {
                            "case_id": f"r14_l2_{stable_hash(ratio_name + str(year) + a + b)}",
                            "level": "L2_derived",
                            "formula_type": "cross_company_margin",
                            "metric": ratio_name,
                            "year": year,
                            "company_a": a,
                            "company_b": b,
                            "question": f"Compare {ratio_name.replace('_', ' ')} for {a} and {b} in fiscal {year}. Which company is higher, and by how many percentage points?",
                            "formula": f"{ratio_name} = {numerator} / {denominator} * 100; difference = abs(company_a_ratio - company_b_ratio)",
                            "target_slots": [slot("company_a_ratio", ra), slot("company_b_ratio", rb), slot("difference", diff)],
                            "winner": winner,
                            "source_fact_numbers": [float(numa["value"]), float(dena["value"]), float(numb["value"]), float(denb["value"])],
                            "source_observations": [numa, dena, numb, denb],
                        }
                    )
    l2 = choose_pairs(l2_candidates, 30)

    l3_candidates: list[dict[str, Any]] = []
    metric = "total_revenue"
    company_years: dict[str, dict[int, dict[str, Any]]] = defaultdict(dict)
    for (ticker, m, year), o in by_company_metric_year.items():
        if m == metric:
            company_years[ticker][year] = o
    tickers = sorted(company_years)
    for i, a in enumerate(tickers):
        years_a = sorted(company_years[a])
        for b in tickers[i + 1 :]:
            common_years = sorted(set(years_a) & set(company_years[b]))
            if len(common_years) < 2:
                continue
            y0, y1 = common_years[0], common_years[-1]
            if y0 == y1:
                continue
            a0, a1 = company_years[a][y0], company_years[a][y1]
            b0, b1 = company_years[b][y0], company_years[b][y1]
            if float(a0["value"]) == 0 or float(b0["value"]) == 0:
                continue
            ga = (float(a1["value"]) - float(a0["value"])) / abs(float(a0["value"])) * 100.0
            gb = (float(b1["value"]) - float(b0["value"])) / abs(float(b0["value"])) * 100.0
            diff = abs(ga - gb)
            l3_candidates.append(
                {
                    "case_id": f"r14_l3_{stable_hash(str(y0) + str(y1) + a + b)}",
                    "level": "L3_trend",
                    "formula_type": "cross_company_growth_trend",
                    "metric": "total_revenue_growth",
                    "year": y1,
                    "start_year": y0,
                    "end_year": y1,
                    "company_a": a,
                    "company_b": b,
                    "question": f"Compare total revenue growth for {a} and {b} from fiscal {y0} to fiscal {y1}. Which company grew faster, and by how many percentage points?",
                    "formula": "growth = (end_value - start_value) / abs(start_value) * 100; difference = abs(company_a_growth - company_b_growth)",
                    "target_slots": [slot("company_a_growth", ga), slot("company_b_growth", gb), slot("difference", diff)],
                    "winner": a if ga > gb else b,
                    "source_fact_numbers": [float(a0["value"]), float(a1["value"]), float(b0["value"]), float(b1["value"])],
                    "source_observations": [a0, a1, b0, b1],
                }
            )
    l3 = choose_pairs(l3_candidates, 10)
    cases = l1 + l2 + l3
    gt_rows = []
    for case in cases:
        case["model_visible_formula_contract"] = visible_contract(case)
        case["scorer_only_target_slot_contract"] = scorer_contract(case)
        gt_rows.append(
            {
                "case_id": case["case_id"],
                "level": case["level"],
                "company_a": case["company_a"],
                "company_b": case["company_b"],
                "metric": case["metric"],
                "source_observations": [
                    {
                        "case_id": o.get("case_id"),
                        "ticker": o.get("ticker"),
                        "metric_canonical": o.get("metric_canonical"),
                        "year": o.get("year"),
                        "value": o.get("value"),
                        "evidence_quote_verified": o.get("evidence_quote_verified"),
                    }
                    for o in case["source_observations"]
                ],
            }
        )
    c.write_jsonl(CROSS_CASES_FILE, cases)
    c.write_jsonl(GT_FILE, gt_rows)
    by_level = Counter(case["level"] for case in cases)
    by_company = Counter([case["company_a"] for case in cases] + [case["company_b"] for case in cases])
    c.write_text(
        SYNTHESIS_FILE,
        "\n".join(
            [
                "# Round14 Cross-Company Query Synthesis",
                "",
                f"- total cases: {len(cases)}",
                f"- L1 direct: {by_level.get('L1_direct', 0)}",
                f"- L2 derived: {by_level.get('L2_derived', 0)}",
                f"- L3 trend: {by_level.get('L3_trend', 0)}",
                f"- companies represented: {len(by_company)}",
                f"- max appearances per company: {max(by_company.values()) if by_company else 0}",
            ]
        ),
    )
    update_state({"phase": "cross_company_cases_done", "cross_company_cases": len(cases), "cases_by_level": dict(by_level)})
    return cases


def load_passage_corpus(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    cases = load_round14_cases()
    out = []
    for row in rows:
        case = cases.get(row["case_id"], {})
        text = str(case.get("evidence_text", ""))
        if not text.strip():
            continue
        out.append(
            {
                "case_id": row["case_id"],
                "ticker": row.get("ticker", ""),
                "company": row.get("company", ""),
                "text": text[:7000],
            }
        )
    return out


def load_embedding_cache() -> dict[str, list[float]]:
    return {row["key"]: row["embedding"] for row in c.read_jsonl(EMBED_CACHE_FILE)}


def save_embedding(key: str, embedding: list[float]) -> None:
    c.append_jsonl(EMBED_CACHE_FILE, {"key": key, "embedding": embedding})


def get_embeddings(keys_texts: list[tuple[str, str]]) -> dict[str, list[float]]:
    cache = load_embedding_cache()
    missing = [(k, t) for k, t in keys_texts if k not in cache]
    for start in range(0, len(missing), 64):
        chunk = missing[start : start + 64]
        if not chunk:
            continue
        vectors = call_embeddings([text[:7000] for _, text in chunk])
        for (key, _), vec in zip(chunk, vectors):
            cache[key] = vec
            save_embedding(key, vec)
        update_state({"phase": "embedding", "embedding_cached": len(cache), "embedding_missing_remaining": max(0, len(missing) - start - len(chunk))})
    return {k: cache[k] for k, _ in keys_texts}


def cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    return dot / (na * nb) if na and nb else 0.0


def retrieve(passages: list[dict[str, Any]], query: str, top_k: int) -> list[dict[str, Any]]:
    keys = [(f"passage::{p['case_id']}", p["text"]) for p in passages] + [(f"query::{stable_hash(query)}", query)]
    embeddings = get_embeddings(keys)
    qv = embeddings[f"query::{stable_hash(query)}"]
    scored = []
    for p in passages:
        score = cosine(qv, embeddings[f"passage::{p['case_id']}"])
        scored.append((score, p))
    return [p for _, p in sorted(scored, key=lambda x: x[0], reverse=True)[:top_k]]


def fact_table(facts: list[dict[str, Any]]) -> str:
    lines = ["ticker | metric | year | value | unit | evidence"]
    for f in facts:
        lines.append(f"{f.get('ticker','')} | {f.get('metric_canonical','')} | {f.get('year','')} | {f.get('value','')} | {f.get('unit','')} | {str(f.get('evidence_quote',''))[:180]}")
    return "\n".join(lines)


def context_for_method(case: dict[str, Any], method: str, passages: list[dict[str, Any]]) -> tuple[str, list[dict[str, Any]], list[dict[str, Any]]]:
    source_obs = case["source_observations"]
    if method == "graph_structured_v14":
        return "GRAPH_FACTS_TABLE\n" + fact_table(source_obs), [], source_obs
    if method == "graph_guided_text_v14":
        unique = {o["case_id"]: o for o in source_obs}
        texts = []
        for cid, obs in unique.items():
            p = next((p for p in passages if p["case_id"] == cid), None)
            if p:
                texts.append(f"PASSAGE {cid} {p['ticker']}\n{p['text']}")
        return "GRAPH_FACTS_TABLE\n" + fact_table(source_obs) + "\n\nTEXT_CONTEXT\n" + "\n\n".join(texts), [p for p in passages if p["case_id"] in unique], source_obs
    if method == "source_text_concat_v14":
        ids = {o["case_id"] for o in source_obs}
        selected = [p for p in passages if p["case_id"] in ids]
        return "TEXT_CONTEXT\n" + "\n\n".join(f"PASSAGE {p['case_id']} {p['ticker']}\n{p['text']}" for p in selected), selected, source_obs
    if method == "vector_single_v14":
        selected = retrieve(passages, case["question"], 6)
        return "TEXT_CONTEXT\n" + "\n\n".join(f"PASSAGE {p['case_id']} {p['ticker']}\n{p['text']}" for p in selected), selected, []
    if method == "vector_multi_by_company_v14":
        qa = f"{case['company_a']} {case['metric']} fiscal {case.get('year', '')} {case.get('start_year', '')} {case.get('end_year', '')}"
        qb = f"{case['company_b']} {case['metric']} fiscal {case.get('year', '')} {case.get('start_year', '')} {case.get('end_year', '')}"
        selected_by_id = {p["case_id"]: p for p in retrieve(passages, qa, 4) + retrieve(passages, qb, 4)}
        selected = list(selected_by_id.values())
        return "TEXT_CONTEXT\n" + "\n\n".join(f"PASSAGE {p['case_id']} {p['ticker']}\n{p['text']}" for p in selected), selected, []
    raise RuntimeError(f"unknown method {method}")


def found_metrics(case: dict[str, Any], selected_passages: list[dict[str, Any]], facts: list[dict[str, Any]]) -> tuple[bool, bool, float, float]:
    if facts:
        tickers = {str(f.get("ticker", "")) for f in facts}
        a = case["company_a"] in tickers
        b = case["company_b"] in tickers
        return a, b, 1.0 if a else 0.0, 1.0 if b else 0.0
    selected_ids = {p["case_id"] for p in selected_passages}
    obs_a = [o for o in case["source_observations"] if o.get("ticker") == case["company_a"]]
    obs_b = [o for o in case["source_observations"] if o.get("ticker") == case["company_b"]]
    a_hits = sum(1 for o in obs_a if o.get("case_id") in selected_ids)
    b_hits = sum(1 for o in obs_b if o.get("case_id") in selected_ids)
    ra = round(a_hits / len(obs_a), 4) if obs_a else 0.0
    rb = round(b_hits / len(obs_b), 4) if obs_b else 0.0
    return ra == 1.0, rb == 1.0, ra, rb


def answer_fields(result: dict[str, Any] | None) -> tuple[str, str]:
    if result is None:
        return "", ""
    final_answer = str(result.get("final_answer", ""))
    calculation = result.get("calculation", "")
    if calculation:
        return final_answer, str(calculation)
    steps = result.get("calculation_steps", [])
    if isinstance(steps, list):
        calculation = "; ".join(json.dumps(step, ensure_ascii=False) if isinstance(step, dict) else str(step) for step in steps)
    return final_answer, str(calculation)


def prompt_for_case(case: dict[str, Any], context: str) -> dict[str, str]:
    pdir = ROOT / "outputs" / "round3_dual_track_eval_prep" / "prompt_formatter_v3_2"
    system = (pdir / "prompt_v3_4_system.md").read_text(encoding="utf-8")
    answer_format = (pdir / "answer_format_spec_v3_2.md").read_text(encoding="utf-8")
    rounding = (pdir / "rounding_and_tolerance_rules_v3_2.md").read_text(encoding="utf-8")
    return {
        "system": system,
        "user": f"""QUESTION
{case['question']}

{context}

FORMULA_CONTRACT
{json.dumps(case['model_visible_formula_contract'], ensure_ascii=False, indent=2, sort_keys=True)}

ANSWER_FORMAT
{answer_format}

ROUNDING_AND_TOLERANCE_RULES
{rounding}

Use temperature=0 behavior. Do not use outside knowledge.
""",
    }


def build_trace(case: dict[str, Any], method: str, context: str, selected_passages: list[dict[str, Any]], facts: list[dict[str, Any]], result: dict[str, Any] | None, usage: dict[str, Any], raw: dict[str, Any] | None, error_type: str = "", error_message: str = "") -> dict[str, Any]:
    final_answer, calculation = answer_fields(result)
    a_found, b_found, rfr_a, rfr_b = found_metrics(case, selected_passages, facts)
    required_fact_recall = round((rfr_a + rfr_b) / 2.0, 4)
    base = {
        "trace_id": f"local_trace_round14_{case['case_id']}__{method}",
        "case_id": case["case_id"],
        "split": "round14_cross_company",
        "source_dataset": "FinDER",
        "method": method,
        "round": ROUND,
        "kg_batch": KG_BATCH if method.startswith("graph") else "N/A",
        "prompt_version": PROMPT_VERSION,
        "scoring_version": SCORER_VERSION,
        "scorer_version": SCORER_VERSION,
        "claim_boundary": CLAIM_BOUNDARY,
        "formula_type": case["formula_type"],
        "level": case["level"],
        "metric": case["metric"],
        "company_a": case["company_a"],
        "company_b": case["company_b"],
        "target_slot_count": len(case["target_slots"]),
        "provider": "openai",
        "model": MODEL,
        "success": result is not None,
        "provider_success": result is not None,
        "error_type": error_type,
        "error_message": error_message,
        "required_fact_recall": required_fact_recall,
        "company_a_found": a_found,
        "company_b_found": b_found,
        "both_companies_found": a_found and b_found,
        "rfr_company_a": rfr_a,
        "rfr_company_b": rfr_b,
        "context_passages": len(selected_passages),
        "retrieved_chunks": [p["case_id"] for p in selected_passages],
        "neo4j_facts_count": len(facts),
        "prompt_tokens": usage.get("prompt_tokens"),
        "completion_tokens": usage.get("completion_tokens"),
        "total_tokens": usage.get("total_tokens"),
        "final_answer": final_answer,
        "calculation": calculation,
        "method_result": {"final_answer": final_answer, "calculation": calculation} if result is not None else None,
        "raw_method_result_v14": raw,
        "usage": usage,
        "model_api_called": True,
        "neo4j_write_performed": False,
        "prompt_sha256": c.sha(context + "\n" + case["question"]),
    }
    row = score_trace(base, case["scorer_only_target_slot_contract"], method)
    if error_type in PROVIDER_ERROR_TYPES:
        row["failure_reason"] = "provider_error"
        row["answer_correctness"] = 0.0
    return row


def run_eval(cases: list[dict[str, Any]], rows: list[dict[str, Any]], run_dir: Path, resume: bool) -> list[dict[str, Any]]:
    trace_path = run_dir / "round14_traces.jsonl"
    traces = c.read_jsonl(trace_path) if resume else []
    traces = [row for row in traces if row.get("failure_reason") != "provider_error"]
    completed = {(row["case_id"], row["method"]) for row in traces}
    passages = load_passage_corpus(rows)
    update_state({"phase": "evaluating", "eval_total": len(cases) * len(METHODS), "eval_done": len(traces), "run_dir": str(run_dir), "trace_file": str(trace_path)})
    for case in cases:
        for method in METHODS:
            if (case["case_id"], method) in completed:
                continue
            context, selected_passages, facts = context_for_method(case, method, passages)
            prompt = prompt_for_case(case, context)
            result = None
            usage: dict[str, Any] = {}
            raw = None
            error_type = ""
            error_message = ""
            safe_print_json({"phase": "eval", "case_id": case["case_id"], "method": method, "done": len(traces)})
            try:
                result, usage, raw = call_with_retries(
                    [{"role": "system", "content": prompt["system"]}, {"role": "user", "content": prompt["user"]}],
                    {"type": "json_object"},
                    max_tokens=1800,
                )
            except c.r8.ProviderError as exc:
                error_type = exc.error_type
                error_message = str(exc)
            except Exception as exc:
                error_type = "provider_unknown"
                error_message = str(exc)[:300]
            trace = build_trace(case, method, context, selected_passages, facts, result, usage, raw, error_type, error_message)
            traces.append(trace)
            completed.add((case["case_id"], method))
            c.write_jsonl(trace_path, traces)
            update_state({"phase": "evaluating", "eval_done": len(traces), "runs_failed": [{"case_id": r["case_id"], "method": r["method"], "error_type": r.get("error_type", "")} for r in traces if r.get("failure_reason") == "provider_error"]})
            time.sleep(0.15)
    return traces


def analyze(traces: list[dict[str, Any]]) -> dict[str, Any]:
    by_method: dict[str, list[dict[str, Any]]] = {m: [r for r in traces if r["method"] == m] for m in METHODS}
    method_summary = {
        m: {
            "n": len(rows),
            "ac": avg([r.get("answer_correctness", 0.0) for r in rows]),
            "nc": avg([r.get("numerical_closeness", 0.0) for r in rows]),
            "both_companies_found": avg([1.0 if r.get("both_companies_found") else 0.0 for r in rows]),
            "required_fact_recall": avg([r.get("required_fact_recall", 0.0) for r in rows]),
            "tokens": avg([r.get("total_tokens") for r in rows if r.get("total_tokens") is not None]),
        }
        for m, rows in by_method.items()
    }
    by_level: dict[str, dict[str, Any]] = {}
    for level in sorted({r.get("level", "") for r in traces}):
        by_level[level] = {}
        for method in METHODS:
            rows = [r for r in traces if r.get("level") == level and r["method"] == method]
            by_level[level][method] = {"n": len(rows), "ac": avg([r.get("answer_correctness", 0.0) for r in rows]), "nc": avg([r.get("numerical_closeness", 0.0) for r in rows])}
    h1 = method_summary["graph_structured_v14"]["ac"] > method_summary["vector_single_v14"]["ac"]
    h2 = method_summary["graph_structured_v14"]["ac"] >= method_summary["vector_multi_by_company_v14"]["ac"] - 0.0001
    h3 = method_summary["graph_guided_text_v14"]["ac"] >= method_summary["graph_structured_v14"]["ac"] - 0.0001
    h4 = method_summary["graph_structured_v14"]["both_companies_found"] > method_summary["vector_single_v14"]["both_companies_found"]
    vector_single_failures = [r for r in by_method["vector_single_v14"] if float(r.get("answer_correctness", 0.0)) == 0.0]
    h5_rate = avg([1.0 if not r.get("both_companies_found") else 0.0 for r in vector_single_failures])
    return {
        "method_summary": method_summary,
        "by_level": by_level,
        "h1_graph_structured_gt_vector_single": h1,
        "h2_graph_structured_ge_vector_multi": h2,
        "h3_graph_guided_ge_graph_structured": h3,
        "h4_graph_coverage_gt_vector_single": h4,
        "h5_vector_single_failure_missing_company_rate": h5_rate,
    }


def write_summary(traces: list[dict[str, Any]], cases: list[dict[str, Any]], observations_written: int, run_dir: Path, smoke: dict[str, Any], coverage: dict[str, Any]) -> None:
    analysis = analyze(traces)
    lines = [
        "# Round 14 Summary: Multi-Company Semantic KG Cross-Company Evaluation",
        "",
        f"**KG batch:** `{KG_BATCH}`",
        f"**Claim boundary:** `{CLAIM_BOUNDARY}`",
        f"**Synthetic cross-company cases:** {len(cases)}",
        f"**Observations written:** {observations_written}",
        "",
        "## Method Results",
        "",
        "| Method | n | AC | NC | both companies found | RFR | avg tokens |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    for method in METHODS:
        row = analysis["method_summary"][method]
        lines.append(f"| {method} | {row['n']} | {row['ac']:.4f} | {row['nc']:.4f} | {row['both_companies_found']:.4f} | {row['required_fact_recall']:.4f} | {row['tokens']:.1f} |")
    lines.extend(["", "## By Level", "", "| Level | Method | n | AC | NC |", "|---|---|---:|---:|---:|"])
    for level, data in analysis["by_level"].items():
        for method in METHODS:
            row = data[method]
            lines.append(f"| {level} | {method} | {row['n']} | {row['ac']:.4f} | {row['nc']:.4f} |")
    lines.extend(
        [
            "",
            "## Hypotheses",
            "",
            "| H | Result |",
            "|---|---|",
            f"| H1 graph_structured > vector_single | {analysis['h1_graph_structured_gt_vector_single']} |",
            f"| H2 graph_structured >= vector_multi_by_company | {analysis['h2_graph_structured_ge_vector_multi']} |",
            f"| H3 graph_guided_text >= graph_structured | {analysis['h3_graph_guided_ge_graph_structured']} |",
            f"| H4 graph both-company coverage > vector_single | {analysis['h4_graph_coverage_gt_vector_single']} |",
            f"| H5 vector_single failure missing-company rate | {analysis['h5_vector_single_failure_missing_company_rate']:.4f} |",
            "",
            "## Smoke Gate",
            "",
            f"`{json.dumps(smoke, ensure_ascii=False, sort_keys=True)}`",
            "",
            "## KG Coverage",
            "",
            f"`{json.dumps(coverage, ensure_ascii=False, sort_keys=True)}`",
        ]
    )
    c.write_text(SUMMARY_FILE, "\n".join(lines))
    state = {
        "round": ROUND,
        "phase": "done",
        "kg_batch": KG_BATCH,
        "claim_boundary": CLAIM_BOUNDARY,
        "model": MODEL,
        "embedding_model": EMBEDDING_MODEL,
        "methods": METHODS,
        "cross_company_cases": len(cases),
        "observations_written": observations_written,
        "smoke_gate": smoke,
        "kg_coverage": coverage,
        **analysis,
        "neo4j_write_performed": True,
        "kg_rollback_file": str(ROLLBACK_FILE),
        "run_dir": str(run_dir),
        "trace_file": str(run_dir / "round14_traces.jsonl"),
        "completed_at": utc_now(),
    }
    c.write_json(STATE_FILE, state)


def integrity_checks(traces: list[dict[str, Any]], cases: list[dict[str, Any]], observations_written: int) -> None:
    if any(t.get("neo4j_write_performed") for t in traces):
        raise RuntimeError("eval trace contains neo4j_write_performed=True")
    if not ROLLBACK_FILE.exists():
        raise RuntimeError("rollback file missing")
    if not WRITE_LOG.exists():
        raise RuntimeError("write log missing")
    write_log_count = len(read_jsonl_lenient(WRITE_LOG))
    if observations_written != write_log_count:
        update_state({"write_log_count_warning": {"observations_written": observations_written, "valid_write_log_rows": write_log_count}})
    pairs = [(t["case_id"], t["method"]) for t in traces]
    if len(pairs) != len(set(pairs)):
        raise RuntimeError("duplicate case_id/method trace")
    if len(traces) != len(cases) * len(METHODS):
        raise RuntimeError(f"expected {len(cases) * len(METHODS)} traces, got {len(traces)}")
    for case in cases:
        companies = {o.get("ticker") for o in case["source_observations"]}
        if case["company_a"] not in companies or case["company_b"] not in companies:
            raise RuntimeError(f"ground truth missing company source observation for {case['case_id']}")
    driver, database = create_driver()
    try:
        with driver.session(database=database) as session:
            rec = session.run(
                """
MATCH (obs:LLMObservation {kg_batch:$batch})-[:LLM_OBSERVES_METRIC]->(m:LLMFinancialMetric)
RETURN count(obs) AS observations,
       sum(CASE WHEN m.canonical_name STARTS WITH 'source_value' THEN 1 ELSE 0 END) AS placeholders,
       sum(CASE WHEN m.canonical_name =~ '^[a-z][a-z0-9_]{3,}$' THEN 0 ELSE 1 END) AS bad_names
""",
                batch=KG_BATCH,
            ).single()
        if int(rec["observations"]) != observations_written:
            raise RuntimeError(f"Neo4j observation count mismatch: {rec['observations']} vs {observations_written}")
        if int(rec["placeholders"]) != 0:
            raise RuntimeError("placeholder metrics found in Neo4j")
        if int(rec["bad_names"]) != 0:
            raise RuntimeError("bad canonical names found in Neo4j")
    finally:
        driver.close()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--phase", choices=["audit", "preflight", "pilot", "full"], default="full")
    parser.add_argument("--run-dir", default="")
    parser.add_argument("--resume", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--allow-existing-batch-resume", action="store_true")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    OUT.mkdir(parents=True, exist_ok=True)
    audit = audit_inputs()
    safe_print_json({"phase": "audit", "audit": audit})
    if not audit["phase0_gate_pass"]:
        raise RuntimeError(f"Phase 0 gate failed: {audit}")
    if args.phase == "audit":
        return
    write_metric_dictionary()
    preflight = preflight_neo4j(args.allow_existing_batch_resume)
    safe_print_json({"phase": "preflight", **preflight})
    if args.phase == "preflight":
        return
    if not args.resume:
        for path in [PILOT_OBS_FILE, SMOKE_FILE, OBS_FILE, UNMAPPED_FILE, WRITE_LOG, KG_COVERAGE_FILE, CROSS_CASES_FILE, GT_FILE, SYNTHESIS_FILE, EMBED_CACHE_FILE]:
            if path.exists():
                path.unlink()
    pilot_rows = run_extraction(pilot_ids(), PILOT_OBS_FILE, "pilot_extraction")
    smoke = smoke_gate(pilot_rows)
    if args.phase == "pilot":
        return
    fresh = fresh_ids_by_slice()
    full_ids = fresh["S1_FIN_COMP"] + fresh["S6_BASELINE_SINGLE"]
    rows = run_extraction(full_ids, OBS_FILE, "full_extraction")
    observations_written = write_kg(rows, args.allow_existing_batch_resume)
    coverage = write_coverage_report(rows)
    cases = build_cross_company_cases(rows)
    run_dir = Path(args.run_dir).resolve() if args.run_dir else c.OUT_ROOT / f"round14_cross_company_{c.ts()}"
    run_dir.mkdir(parents=True, exist_ok=True)
    traces = run_eval(cases, rows, run_dir, args.resume)
    write_summary(traces, cases, observations_written, run_dir, smoke, coverage)
    integrity_checks(traces, cases, observations_written)
    safe_print_json(c.read_json(STATE_FILE), indent=2)


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        OUT.mkdir(parents=True, exist_ok=True)
        (OUT / "last_error.txt").write_text(traceback.format_exc(), encoding="utf-8", newline="\n")
        try:
            prior = c.read_json(STATE_FILE) if STATE_FILE.exists() else {}
            update_state({"phase": "failed", "last_error": str(exc)[:500], "neo4j_write_performed": bool(prior.get("neo4j_write_performed", False))})
        except Exception:
            pass
        raise
