from __future__ import annotations

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
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

from neo4j import GraphDatabase


ROOT = Path(__file__).resolve().parents[1]
ROUND7_PATH = ROOT / "scripts" / "round7_eval.py"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import importlib.util

spec = importlib.util.spec_from_file_location("round7_eval", ROUND7_PATH)
r7 = importlib.util.module_from_spec(spec)
assert spec and spec.loader
spec.loader.exec_module(r7)

TODAY = date.today().strftime("%Y%m%d")
ROUND8_BATCH = f"kg-round8-v1-{TODAY}"
CLAIM_BOUNDARY = "clean_held_out_round8_finder_finqa_pilot"
MODEL = "gpt-4o-mini"
METHODS = ["vector_only_v8", "graph_neo4j_v8", "hybrid_neo4j_v8"]
PROMPT_VERSION = "v3.3"
SCORING_VERSION = "v7_no_faith_gate"

CASE_DIR = ROOT / "outputs" / "round8_case_selection"
CONTRACT_DIR = ROOT / "outputs" / "round8_formula_contracts"
KG_DIR = ROOT / "outputs" / "round8_step_b_kg"
EVAL_STATE = ROOT / "outputs" / "round8_eval" / "state.json"
OUT_ROOT = ROOT / "outputs" / "round3_eval_runs"

FINDER_FULL = ROOT / "examples" / "datasets" / "finder_full.json"
FINQA_TRAIN = ROOT.parent / "data" / "github" / "FinQA" / "FinQA-main" / "dataset" / "train.json"
FINDER_CANDIDATES = CASE_DIR / "finder_candidates.jsonl"
FINQA_CANDIDATES = CASE_DIR / "finqa_candidates.jsonl"
SELECTION_STATE = CASE_DIR / "selection_state.json"
SCORER_CONTRACTS = CONTRACT_DIR / "round8_scorer_contracts.jsonl"
VISIBLE_CONTRACTS = CONTRACT_DIR / "round8_model_visible_contracts.jsonl"
GEN_STATE = CONTRACT_DIR / "generation_state.json"
GEN_TRACE = CONTRACT_DIR / "generation_trace.jsonl"
VALIDATION_REPORT = CONTRACT_DIR / "validation_report.jsonl"

EXCLUDED_TICKERS = {
    "AMGN", "APD", "BXP", "GM", "LOW", "MPC", "MU", "NXPI", "VRSK", "XEL",
    "BAC", "BW", "CARR", "CMCSA", "FOXA", "HCA", "KR", "LND", "MCO", "MDLZ", "MSFT", "MTB",
}
STOP_TICKERS = r7.r6.r5.r4.STOP_TICKERS if hasattr(r7.r6.r5.r4, "STOP_TICKERS") else {
    "THE", "AND", "FOR", "INC", "LLC", "LTD", "SEC", "ROI", "EPS", "CEO", "CFO", "FY", "GAAP", "USD", "US", "UK"
}
REASONING_ALLOWED = {"Calculation", "Compositional", "Subtract", "Subtraction"}


class ProviderError(RuntimeError):
    def __init__(self, error_type: str, message: str) -> None:
        super().__init__(message)
        self.error_type = error_type


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def ts() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S")


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


def write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text.rstrip() + "\n", encoding="utf-8", newline="\n")


NUM_RE = re.compile(r"\(?\$?\s*-?\d[\d,]*(?:\.\d+)?\)?\s*%?")


def parse_number(raw: Any) -> float | None:
    text = str(raw or "").strip()
    if not text:
        return None
    m = NUM_RE.search(text)
    if not m:
        return None
    token = m.group(0).strip()
    neg = token.startswith("(") and token.endswith(")")
    token = token.strip("()").replace("$", "").replace(",", "").replace("%", "").strip()
    try:
        value = float(token)
    except ValueError:
        return None
    return -value if neg else value


def all_numbers(text: str) -> list[float]:
    out = []
    for m in NUM_RE.finditer(text or ""):
        value = parse_number(m.group(0))
        if value is not None:
            out.append(value)
    return out


def years_in_text(text: str) -> list[int]:
    years = []
    for year in re.findall(r"\b(19[89]\d|20[0-3]\d)\b", text or ""):
        y = int(year)
        if y not in years:
            years.append(y)
    return years


def normalize_reasoning(value: str) -> str:
    return "Subtraction" if value == "Subtract" else (value or "").strip()


def infer_ticker_from_text(*parts: str) -> str:
    text = " ".join(parts)
    for match in re.findall(r"\(([A-Z]{1,5})\)", text):
        if match not in STOP_TICKERS:
            return match
    for match in re.findall(r"\b[A-Z]{2,5}\b", text):
        if match not in STOP_TICKERS and match not in {"NYSE", "NASDAQ"}:
            return match
    return ""


def infer_company(evidence_text: str, ticker: str) -> str:
    first = next((line.strip() for line in evidence_text.replace("\r", "\n").split("\n") if line.strip()), "")
    first = re.sub(r"\s+and\s+Subsidiaries.*", "", first)
    first = re.sub(r"\s+Consolidated.*", "", first)
    if 2 <= len(first) <= 100 and re.search(r"[A-Za-z]", first):
        return first.strip(" ,.")
    return ticker or "Unknown"


def quality_score(evidence: str, question: str, expected: str, reasoning: str) -> float:
    nums = len(all_numbers(evidence))
    years = len(set(years_in_text(evidence + " " + question)))
    score = 0.0
    score += {"Calculation": 3.0, "Compositional": 2.8, "Subtraction": 2.4}.get(normalize_reasoning(reasoning), 1.0)
    score += min(4.0, nums / 8)
    score += min(3.0, years * 0.8)
    score += min(2.0, len(evidence) / 1200)
    if re.search(r"\b(margin|ratio|percent|percentage|growth|change|increase|decrease|divided|per share)\b", question + " " + expected, re.I):
        score += 2.0
    if parse_number(expected) is not None:
        score += 2.0
    return round(score, 4)


def openai_api_key() -> str:
    key = os.environ.get("OPENAI_API_KEY", "")
    if not key:
        raise ProviderError("provider_auth", "OPENAI_API_KEY missing from process environment")
    return key


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


def call_openai_json(prompt: str, temperature: float = 0.0) -> tuple[dict[str, Any], dict[str, Any]]:
    payload = {
        "model": MODEL,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": temperature,
        "response_format": {"type": "json_object"},
    }
    req = urllib.request.Request(
        "https://api.openai.com/v1/chat/completions",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {openai_api_key()}"},
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
        return extract_json_object(data["choices"][0]["message"]["content"]), data.get("usage", {})
    except Exception as exc:  # noqa: BLE001
        raise ProviderError("provider_bad_response", "provider returned non-JSON model output") from exc


def env_from_files(name: str) -> str:
    if os.environ.get(name):
        return os.environ[name]
    for path in (ROOT / ".env", ROOT.parent / ".env"):
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


def neo4j_env() -> dict[str, str]:
    values = {
        "NEO4J_URI": env_from_files("NEO4J_URI") or "bolt://localhost:7687",
        "NEO4J_USERNAME": env_from_files("NEO4J_USERNAME") or env_from_files("NEO4J_USER") or "neo4j",
        "NEO4J_PASSWORD": env_from_files("NEO4J_PASSWORD"),
        "NEO4J_DATABASE": env_from_files("NEO4J_DATABASE") or "neo4j",
    }
    if not values["NEO4J_PASSWORD"]:
        raise RuntimeError("Missing NEO4J_PASSWORD")
    return values


def create_driver() -> Any:
    env = neo4j_env()
    return GraphDatabase.driver(env["NEO4J_URI"], auth=(env["NEO4J_USERNAME"], env["NEO4J_PASSWORD"]))


def linearize_finqa_table(table: list[list[Any]]) -> str:
    return "\n".join(" | ".join(str(cell) for cell in row) for row in table or [])


def finqa_evidence(row: dict[str, Any]) -> str:
    return "\n".join(
        part for part in [
            linearize_finqa_table(row.get("table_ori") or row.get("table") or []),
            " ".join(row.get("pre_text") or []),
            " ".join(row.get("post_text") or []),
        ] if part.strip()
    )


def program_numbers(program: str) -> list[float]:
    nums = []
    for token in re.findall(r"const_\d+(?:\.\d+)?|-?\d+(?:\.\d+)?%?", program or ""):
        if token.startswith("const_"):
            nums.append(float(token.replace("const_", "")))
        elif token.endswith("%"):
            nums.append(float(token[:-1]))
        else:
            nums.append(float(token))
    return nums


def infer_unit_from_answer(answer: str) -> str:
    if "%" in str(answer):
        return "percentage"
    return "amount"


def target_tolerance(value: float, unit: str) -> float:
    if unit == "percentage":
        return 0.1
    return max(0.05, abs(value) * 0.005)


def load_all_round8_cases() -> list[dict[str, Any]]:
    return read_jsonl(FINDER_CANDIDATES) + read_jsonl(FINQA_CANDIDATES)


def load_contract_maps() -> tuple[dict[str, Any], dict[str, Any]]:
    scorer = {row["case_id"]: row["scorer_only_target_slot_contract"] for row in read_jsonl(SCORER_CONTRACTS)}
    visible = {row["case_id"]: row["model_visible_formula_contract"] for row in read_jsonl(VISIBLE_CONTRACTS)}
    return scorer, visible
