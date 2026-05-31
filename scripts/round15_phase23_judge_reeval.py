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
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any

import numpy as np

import round10_common as c
from scorer_v9 import score_trace

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

SEOCHO = Path(__file__).resolve().parents[1]
R15 = SEOCHO / "outputs" / "round15_vector_rehab"
INDEX = R15 / "01_vector_index"
JUDGE_DIR = R15 / "03_judge_layer"
REEVAL = R15 / "04_reeval"
EVAL_POLICY = R15 / "EVALUATION_METRICS.md"
STATE = R15 / "state_phase23.json"
RUN_ROOT = SEOCHO / "outputs" / "round3_eval_runs"

R14_CASES = SEOCHO / "outputs" / "round14_cross_company" / "04_cross_company_queries" / "cross_company_cases.jsonl"
R14_TRACES = SEOCHO / "outputs" / "round3_eval_runs" / "round14_cross_company_20260530_133644" / "round14_traces.jsonl"

GEN_MODEL = "gpt-4o-mini"
JUDGE_OPENAI_MODEL = "gpt-4o"
EMBED_MODEL = "text-embedding-3-small"
CHUNK_TOPK_SINGLE = 12
CHUNK_TOPK_MULTI = 8
SOFT_BUDGET_USD = 5.0
HARD_BUDGET_USD = 10.0

NEW_VECTOR_METHODS = ["vector_single_chunk_v15", "vector_multi_by_company_chunk_v15"]
REUSED_METHODS = ["graph_structured_v14", "graph_guided_text_v14", "source_text_concat_v14"]
ALL_METHODS = NEW_VECTOR_METHODS + REUSED_METHODS

TOKEN_PRICE = {
    # Conservative current-ish estimates; used only as a run guard/ledger.
    "gpt-4o-mini": {"input": 0.00000015, "output": 0.00000060},
    "gpt-4o": {"input": 0.00000250, "output": 0.00001000},
    "claude": {"input": 0.00000300, "output": 0.00001500},
}


def now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def ts() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S")


def sha(text: str, n: int = 16) -> str:
    return hashlib.sha256(text.encode("utf-8", errors="ignore")).hexdigest()[:n]


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    if not path.exists():
        return rows
    with path.open("r", encoding="utf-8", errors="ignore") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return rows


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")


def append_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8", newline="\n") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")


def write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text.rstrip() + "\n", encoding="utf-8", newline="\n")


def update_state(patch: dict[str, Any]) -> None:
    state = {}
    if STATE.exists():
        try:
            state = json.loads(STATE.read_text(encoding="utf-8"))
        except Exception:
            state = {}
    state.update(patch)
    state["updated_at"] = now_iso()
    write_json(STATE, state)


def rel(path: Path) -> str:
    try:
        return str(path.relative_to(SEOCHO)).replace("\\", "/")
    except ValueError:
        return str(path)


def extract_numbers(text: str) -> list[float]:
    values: list[float] = []
    for match in re.finditer(r"\(?\$?\s*-?\d[\d,]*(?:\.\d+)?\)?\s*%?", str(text)):
        raw = match.group(0).strip()
        neg = raw.startswith("(") and raw.endswith(")")
        is_pct = raw.endswith("%")
        cleaned = raw.strip("()%$ ").replace(",", "")
        try:
            val = float(cleaned)
        except ValueError:
            continue
        if neg:
            val = -val
        values.append(val)
        if is_pct:
            values.append(val / 100.0)
    out = []
    seen = set()
    for val in values:
        key = round(val, 8)
        if math.isfinite(val) and key not in seen:
            seen.add(key)
            out.append(val)
    return out


def number_overlap(candidate_text: str, gold_numbers: list[float]) -> float:
    cand = extract_numbers(candidate_text)
    if not gold_numbers:
        return 0.0
    hits = 0
    for gold in gold_numbers:
        tol = max(0.5, abs(float(gold)) * 0.02)
        if any(abs(float(value) - float(gold)) <= tol for value in cand):
            hits += 1
    return round(hits / len(gold_numbers), 4)


def normalize_tokens(text: str) -> list[str]:
    return re.findall(r"[a-z0-9]+", str(text).lower().replace("_", " "))


def token_f1(candidate_text: str, gold_text: str) -> float:
    cand = Counter(normalize_tokens(candidate_text))
    gold = Counter(normalize_tokens(gold_text))
    if not cand or not gold:
        return 0.0
    overlap = sum((cand & gold).values())
    if overlap == 0:
        return 0.0
    precision = overlap / sum(cand.values())
    recall = overlap / sum(gold.values())
    return round((2 * precision * recall) / (precision + recall), 4)


def gold_for_case(case: dict[str, Any]) -> dict[str, Any]:
    slots = case.get("scorer_only_target_slot_contract", {}).get("target_slots", [])
    values = {str(slot.get("target_slot_name", "")): float(slot["expected_value"]) for slot in slots if slot.get("expected_value") is not None}
    numbers = [values[name] for name in ["company_a_value", "company_b_value", "difference"] if name in values]
    if not numbers:
        numbers = [float(v) for v in case.get("source_fact_numbers", []) if isinstance(v, (int, float))]
    gold_text = (
        f"{case.get('company_a')} {values.get('company_a_value', '')}; "
        f"{case.get('company_b')} {values.get('company_b_value', '')}; "
        f"winner {case.get('winner', '')}; difference {values.get('difference', '')}"
    )
    return {"numbers": numbers, "text": gold_text, "slot_values": values, "winner": case.get("winner", "")}


def select_judge() -> dict[str, Any]:
    if os.environ.get("ANTHROPIC_API_KEY"):
        return {
            "judge_vendor": "anthropic",
            "judge_model": os.environ.get("ANTHROPIC_MODEL", "claude-3-5-sonnet-latest"),
            "same_vendor_as_generator": False,
            "bias_disclosure": "Cross-vendor judge selected via ANTHROPIC_API_KEY.",
        }
    if not os.environ.get("OPENAI_API_KEY"):
        raise RuntimeError("OPENAI_API_KEY missing; judge fallback requires environment key")
    return {
        "judge_vendor": "openai",
        "judge_model": JUDGE_OPENAI_MODEL,
        "same_vendor_as_generator": True,
        "bias_disclosure": "No cross-vendor judge key found. Using OpenAI gpt-4o while generation uses gpt-4o-mini; same-vendor bias disclosed.",
    }


def write_phase2_files(judge_config: dict[str, Any]) -> None:
    write_text(
        EVAL_POLICY,
        """# Round 15 Evaluation Metrics

This research-track policy defines a uniform scoring layer for R15.

## Three-Layer Evaluation

1. `number_overlap`: diagnostic numeric recall against canonical gold numbers.
2. `token_f1`: diagnostic lexical overlap against canonical gold text.
3. `judge_score`: headline semantic correctness from one fixed judge prompt.

`judge_score` is the headline metric. `number_overlap` and `token_f1` are diagnostic only.

## Gold Answer Source

For R14 cross-company cases, canonical gold comes from each case's
`scorer_only_target_slot_contract`: `company_a_value`, `company_b_value`,
`difference`, and the case-level `winner`. These are derived target values,
not model-visible answers.

## Uniformity

All methods use the same metric functions and the same judge prompt. `scorer_v9`
is retained as a parallel numeric/formula diagnostic and is not silently replaced.

## Judge Bias Disclosure

Prefer cross-vendor judging. If unavailable, use `gpt-4o` while generation uses
`gpt-4o-mini`, and disclose same-vendor bias in metadata.

## Parse Failures

Judge parsing failures are recorded as `verdict=scorer_uncertain` and
`score=null`; no guessed score is allowed.
""",
    )
    write_text(
        JUDGE_DIR / "metric_functions.py",
        '''"""Deterministic R15 diagnostic metrics: number_overlap and token_f1."""\n\n'''
        + inspect_metric_source(),
    )
    write_text(
        JUDGE_DIR / "judge_prompt.txt",
        """You are an independent financial QA judge.
Return ONLY strict JSON with exactly:
{"verdict":"correct|partial|incorrect","score":1.0|0.5|0.0,"matched":["..."],"missing_or_wrong":["..."],"rationale":"..."}

Judge whether CANDIDATE_ANSWER correctly answers QUESTION relative to GOLD_ANSWER.
Accept equivalent wording and rounding if the required numeric comparison, winner, and difference are correct.
Do not reward unsupported extra facts.
""",
    )
    write_json(JUDGE_DIR / "judge_config.json", judge_config)


def inspect_metric_source() -> str:
    return r'''
import math
import re
from collections import Counter


def extract_numbers(text):
    values = []
    for match in re.finditer(r"\(?\$?\s*-?\d[\d,]*(?:\.\d+)?\)?\s*%?", str(text)):
        raw = match.group(0).strip()
        neg = raw.startswith("(") and raw.endswith(")")
        is_pct = raw.endswith("%")
        cleaned = raw.strip("()%$ ").replace(",", "")
        try:
            val = float(cleaned)
        except ValueError:
            continue
        if neg:
            val = -val
        values.append(val)
        if is_pct:
            values.append(val / 100.0)
    out = []
    seen = set()
    for val in values:
        key = round(val, 8)
        if math.isfinite(val) and key not in seen:
            seen.add(key)
            out.append(val)
    return out


def number_overlap(candidate_text, gold_numbers):
    cand = extract_numbers(candidate_text)
    if not gold_numbers:
        return 0.0
    hits = 0
    for gold in gold_numbers:
        tol = max(0.5, abs(float(gold)) * 0.02)
        if any(abs(float(value) - float(gold)) <= tol for value in cand):
            hits += 1
    return round(hits / len(gold_numbers), 4)


def normalize_tokens(text):
    return re.findall(r"[a-z0-9]+", str(text).lower().replace("_", " "))


def token_f1(candidate_text, gold_text):
    cand = Counter(normalize_tokens(candidate_text))
    gold = Counter(normalize_tokens(gold_text))
    if not cand or not gold:
        return 0.0
    overlap = sum((cand & gold).values())
    if overlap == 0:
        return 0.0
    precision = overlap / sum(cand.values())
    recall = overlap / sum(gold.values())
    return round((2 * precision * recall) / (precision + recall), 4)
'''


def load_index() -> tuple[list[dict[str, Any]], np.ndarray, dict[str, Any]]:
    manifest = json.loads((INDEX / "index_manifest.json").read_text(encoding="utf-8"))
    chunks = read_jsonl(INDEX / "chunks.jsonl")
    matrix = np.load(INDEX / "embeddings.npy")
    if len(chunks) != int(matrix.shape[0]):
        raise RuntimeError(f"chunk/index mismatch: chunks={len(chunks)} matrix={matrix.shape}")
    return chunks, matrix, manifest


def embed_texts(texts: list[str]) -> list[list[float]]:
    cache_path = INDEX / "phase23_query_embedding_cache.jsonl"
    cache: dict[str, list[float]] = {}
    if cache_path.exists():
        for row in read_jsonl(cache_path):
            if row.get("key") and isinstance(row.get("embedding"), list):
                cache[str(row["key"])] = row["embedding"]
    missing = [(sha(text, 32), text) for text in texts if sha(text, 32) not in cache]
    if missing and not os.environ.get("OPENAI_API_KEY"):
        raise RuntimeError("OPENAI_API_KEY missing for query embeddings")
    for start in range(0, len(missing), 64):
        batch = missing[start:start + 64]
        vectors = call_openai_embeddings([text for _, text in batch])
        rows = []
        for (key, _), vec in zip(batch, vectors):
            cache[key] = vec
            rows.append({"key": key, "embedding": vec, "embed_model": EMBED_MODEL})
        append_jsonl(cache_path, rows)
    return [cache[sha(text, 32)] for text in texts]


def call_openai_embeddings(texts: list[str]) -> list[list[float]]:
    payload = json.dumps({"model": EMBED_MODEL, "input": texts}, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        "https://api.openai.com/v1/embeddings",
        data=payload,
        headers={"Authorization": f"Bearer {os.environ['OPENAI_API_KEY']}", "Content-Type": "application/json"},
        method="POST",
    )
    for attempt in range(1, 5):
        try:
            with urllib.request.urlopen(req, timeout=120) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            return [row["embedding"] for row in sorted(data["data"], key=lambda item: item["index"])]
        except Exception:
            if attempt == 4:
                raise
            time.sleep(2 * attempt)
    raise RuntimeError("embedding failure")


def retrieve(query: str, chunks: list[dict[str, Any]], matrix: np.ndarray, top_k: int) -> list[dict[str, Any]]:
    qv = np.array(embed_texts([query])[0], dtype=np.float32)
    qv = qv / (np.linalg.norm(qv) or 1.0)
    scores = matrix @ qv
    order = np.argsort(-scores)[:top_k]
    out = []
    for rank, idx in enumerate(order, start=1):
        chunk = chunks[int(idx)]
        out.append(
            {
                "rank": rank,
                "chunk_id": chunk["chunk_id"],
                "source_case_id": chunk["source_case_id"],
                "score": float(scores[int(idx)]),
                "chunk_text": chunk["chunk_text"],
                "ticker": chunk.get("ticker", ""),
                "company": chunk.get("company", ""),
            }
        )
    return out


def context_for_vector(case: dict[str, Any], method: str, chunks: list[dict[str, Any]], matrix: np.ndarray) -> tuple[str, list[dict[str, Any]]]:
    if method == "vector_single_chunk_v15":
        selected = retrieve(case["question"], chunks, matrix, CHUNK_TOPK_SINGLE)
    elif method == "vector_multi_by_company_chunk_v15":
        year = case.get("year") or ""
        qa = f"{case['company_a']} {case['metric']} fiscal {year}"
        qb = f"{case['company_b']} {case['metric']} fiscal {year}"
        by_id = {}
        for row in retrieve(qa, chunks, matrix, CHUNK_TOPK_MULTI) + retrieve(qb, chunks, matrix, CHUNK_TOPK_MULTI):
            by_id.setdefault(row["chunk_id"], row)
        selected = list(by_id.values())
    else:
        raise RuntimeError(f"unknown vector method {method}")
    context = "TEXT_CONTEXT\n" + "\n\n".join(
        f"PASSAGE {row['chunk_id']} source_case_id={row['source_case_id']} ticker={row['ticker']} score={row['score']:.4f}\n{row['chunk_text']}"
        for row in selected
    )
    return context, selected


def prompt_for_case(case: dict[str, Any], context: str) -> dict[str, str]:
    pdir = SEOCHO / "outputs" / "round3_dual_track_eval_prep" / "prompt_formatter_v3_2"
    system = (pdir / "prompt_v3_4_system.md").read_text(encoding="utf-8")
    answer_format = (pdir / "answer_format_spec_v3_2.md").read_text(encoding="utf-8")
    rounding = (pdir / "rounding_and_tolerance_rules_v3_2.md").read_text(encoding="utf-8")
    user = f"""QUESTION
{case['question']}

{context}

FORMULA_CONTRACT
{json.dumps(case['model_visible_formula_contract'], ensure_ascii=False, indent=2, sort_keys=True)}

ANSWER_FORMAT
{answer_format}

ROUNDING_AND_TOLERANCE_RULES
{rounding}

Use temperature=0 behavior. Do not use outside knowledge.
"""
    return {"system": system, "user": user}


def call_openai_json(model: str, messages: list[dict[str, str]], max_tokens: int = 1800) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    payload = {"model": model, "messages": messages, "temperature": 0, "max_tokens": max_tokens, "response_format": {"type": "json_object"}}
    req = urllib.request.Request(
        "https://api.openai.com/v1/chat/completions",
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {os.environ['OPENAI_API_KEY']}"},
        method="POST",
    )
    for attempt in range(1, 5):
        try:
            with urllib.request.urlopen(req, timeout=180) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            content = data["choices"][0]["message"]["content"]
            return json.loads(content), data.get("usage", {}), data
        except (urllib.error.HTTPError, urllib.error.URLError, socket.timeout, TimeoutError, json.JSONDecodeError):
            if attempt == 4:
                raise
            time.sleep(2 * attempt)
    raise RuntimeError("OpenAI JSON call failed")


def call_anthropic_judge(messages: list[dict[str, str]], model: str) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    system = messages[0]["content"]
    user = messages[1]["content"]
    payload = {"model": model, "max_tokens": 500, "temperature": 0, "system": system, "messages": [{"role": "user", "content": user}]}
    req = urllib.request.Request(
        "https://api.anthropic.com/v1/messages",
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "x-api-key": os.environ["ANTHROPIC_API_KEY"],
            "anthropic-version": "2023-06-01",
        },
        method="POST",
    )
    for attempt in range(1, 5):
        try:
            with urllib.request.urlopen(req, timeout=180) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            text = "".join(block.get("text", "") for block in data.get("content", []) if block.get("type") == "text")
            return json.loads(text), data.get("usage", {}), data
        except Exception:
            if attempt == 4:
                raise
            time.sleep(2 * attempt)
    raise RuntimeError("Anthropic judge failed")


def answer_fields(result: dict[str, Any] | None) -> tuple[str, str]:
    if not result:
        return "", ""
    final_answer = str(result.get("final_answer", ""))
    calculation = result.get("calculation", "")
    if calculation:
        return final_answer, str(calculation)
    steps = result.get("calculation_steps", [])
    if isinstance(steps, list):
        calculation = "; ".join(json.dumps(step, ensure_ascii=False) if isinstance(step, dict) else str(step) for step in steps)
    return final_answer, str(calculation)


def expected_context_checks(case: dict[str, Any], selected: list[dict[str, Any]]) -> dict[str, Any]:
    blob = "\n".join(row["chunk_text"] for row in selected).lower()
    obs = case.get("source_observations", [])
    ids = {row["source_case_id"] for row in selected}
    tickers = {row.get("ticker", "") for row in selected}
    a_ids = {o.get("case_id") for o in obs if o.get("ticker") == case.get("company_a")}
    b_ids = {o.get("case_id") for o in obs if o.get("ticker") == case.get("company_b")}
    a_found = bool(a_ids & ids) or case.get("company_a") in tickers
    b_found = bool(b_ids & ids) or case.get("company_b") in tickers
    numbers = [str(x).rstrip("0").rstrip(".") for x in case.get("source_fact_numbers", [])]
    contains_number = any(num and num in blob for num in numbers)
    metric_text = str(case.get("metric", "")).replace("_", " ").lower()
    contains_metric = metric_text in blob or str(case.get("metric", "")).lower() in blob
    contains_company = any(str(co).lower() in blob for co in [case.get("company_a"), case.get("company_b")] if co)
    return {
        "retrieved_chunk_ids": [row["chunk_id"] for row in selected],
        "retrieved_scores": [round(float(row["score"]), 6) for row in selected],
        "retrieved_source_case_ids": [row["source_case_id"] for row in selected],
        "companies_in_context": sorted(t for t in tickers if t),
        "company_a_found": a_found,
        "company_b_found": b_found,
        "both_companies_found": a_found and b_found,
        "rfr_company_a": 1.0 if a_found else 0.0,
        "rfr_company_b": 1.0 if b_found else 0.0,
        "required_fact_recall": round(((1.0 if a_found else 0.0) + (1.0 if b_found else 0.0)) / 2.0, 4),
        "contains_expected_number": contains_number,
        "contains_expected_metric": contains_metric,
        "contains_expected_company": contains_company,
        "contains_match": bool(contains_number and (contains_metric or contains_company)),
    }


def build_vector_trace(case: dict[str, Any], method: str, result: dict[str, Any] | None, usage: dict[str, Any], raw: dict[str, Any] | None, context: str, selected: list[dict[str, Any]], error_type: str = "", error_message: str = "") -> dict[str, Any]:
    final_answer, calculation = answer_fields(result)
    checks = expected_context_checks(case, selected)
    base = {
        "trace_id": f"local_trace_round15_{case['case_id']}__{method}",
        "case_id": case["case_id"],
        "split": "round15_cross_company_reeval",
        "source_dataset": "FinDER",
        "method": method,
        "round": "round15",
        "kg_batch": "N/A",
        "prompt_version": "v3.4",
        "scoring_version": "v9",
        "scorer_version": "v9",
        "claim_boundary": "round15_fair_chunk_vector_vs_round14_graph",
        "formula_type": case["formula_type"],
        "level": case["level"],
        "metric": case["metric"],
        "company_a": case["company_a"],
        "company_b": case["company_b"],
        "target_slot_count": len(case["target_slots"]),
        "provider": "openai",
        "model": GEN_MODEL,
        "success": result is not None,
        "provider_success": result is not None,
        "error_type": error_type,
        "error_message": error_message,
        "context_passages": len(selected),
        "context_chars": len(context),
        "neo4j_facts_count": 0,
        "prompt_tokens": usage.get("prompt_tokens"),
        "completion_tokens": usage.get("completion_tokens"),
        "total_tokens": usage.get("total_tokens"),
        "final_answer": final_answer,
        "calculation": calculation,
        "method_result": {"final_answer": final_answer, "calculation": calculation} if result is not None else None,
        "raw_method_result_v15": raw,
        "usage": usage,
        "model_api_called": True,
        "neo4j_write_performed": False,
        "prompt_sha256": sha(context + "\n" + case["question"], 64),
        "answer_correctness": 0.0,
        **checks,
    }
    row = score_trace(base, case["scorer_only_target_slot_contract"], method)
    row.update(checks)
    row["failure_type"] = classify_failure_type(row)
    return row


def classify_failure_type(row: dict[str, Any]) -> str:
    if row.get("answer_correctness") == 1.0:
        return "none"
    if not row.get("contains_match") or not row.get("both_companies_found"):
        return "retrieval_miss"
    if row.get("failure_reason") == "answer_format_error":
        return "answer_format_error"
    if row.get("final_answer") and row.get("contains_match"):
        return "retrieved_but_reasoning_error"
    if row.get("final_answer") and not row.get("contains_expected_number"):
        return "possible_hallucination"
    return "scorer_uncertain"


def generate_vectors(cases: list[dict[str, Any]], run_dir: Path, smoke: bool = False) -> list[dict[str, Any]]:
    chunks, matrix, manifest = load_index()
    trace_path = run_dir / "round15_reeval_traces.jsonl"
    rows = read_jsonl(trace_path)
    completed = {(row.get("case_id"), row.get("method")) for row in rows if row.get("method") in NEW_VECTOR_METHODS and not row.get("error_type")}
    target_cases = cases[:3] if smoke else cases
    total = len(target_cases) * len(NEW_VECTOR_METHODS)
    for case in target_cases:
        for method in NEW_VECTOR_METHODS:
            if (case["case_id"], method) in completed:
                continue
            context, selected = context_for_vector(case, method, chunks, matrix)
            prompt = prompt_for_case(case, context)
            result = None
            usage: dict[str, Any] = {}
            raw = None
            error_type = ""
            error_message = ""
            print(json.dumps({"phase": "vector_generation", "case_id": case["case_id"], "method": method, "done": len(rows), "total": total}, ensure_ascii=False), flush=True)
            try:
                result, usage, raw = call_openai_json(
                    GEN_MODEL,
                    [{"role": "system", "content": prompt["system"]}, {"role": "user", "content": prompt["user"]}],
                    max_tokens=1800,
                )
            except Exception as exc:
                error_type = "provider_error"
                error_message = str(exc)[:300]
            row = build_vector_trace(case, method, result, usage, raw, context, selected, error_type, error_message)
            rows.append(row)
            write_jsonl(trace_path, rows)
            guard_budget(rows, [])
    return [row for row in rows if row.get("method") in NEW_VECTOR_METHODS and row.get("case_id") in {case["case_id"] for case in target_cases}]


def reused_trace_rows(cases: list[dict[str, Any]]) -> list[dict[str, Any]]:
    ids = {case["case_id"] for case in cases}
    out = []
    for row in read_jsonl(R14_TRACES):
        if row.get("case_id") in ids and row.get("method") in REUSED_METHODS:
            copy = dict(row)
            copy["round15_reused_from_r14"] = True
            copy["model_api_called_round15"] = False
            out.append(copy)
    return out


def candidate_text(row: dict[str, Any]) -> str:
    return "\n".join([str(row.get("final_answer", "")), str(row.get("calculation", ""))]).strip()


def judge_messages(case: dict[str, Any], gold: dict[str, Any], row: dict[str, Any]) -> list[dict[str, str]]:
    system = (JUDGE_DIR / "judge_prompt.txt").read_text(encoding="utf-8")
    user = f"""QUESTION:
{case['question']}

GOLD_ANSWER:
{gold['text']}

CANDIDATE_METHOD:
{row.get('method')}

CANDIDATE_ANSWER:
{candidate_text(row)}
"""
    return [{"role": "system", "content": system}, {"role": "user", "content": user}]


def call_judge(judge_config: dict[str, Any], messages: list[dict[str, str]]) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    if judge_config["judge_vendor"] == "anthropic":
        return call_anthropic_judge(messages, judge_config["judge_model"])
    return call_openai_json(judge_config["judge_model"], messages, max_tokens=500)


def parse_judge_result(raw: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(raw, dict):
        return {"judge_verdict": "scorer_uncertain", "judge_score": None, "judge_matched": [], "judge_missing_or_wrong": ["parse_failed"], "judge_rationale": ""}
    verdict = raw.get("verdict")
    score = raw.get("score")
    if verdict not in {"correct", "partial", "incorrect"} or score not in {0, 0.0, 0.5, 1, 1.0}:
        return {"judge_verdict": "scorer_uncertain", "judge_score": None, "judge_matched": [], "judge_missing_or_wrong": ["invalid_schema"], "judge_rationale": str(raw)[:300]}
    return {
        "judge_verdict": verdict,
        "judge_score": float(score),
        "judge_matched": raw.get("matched", []) if isinstance(raw.get("matched", []), list) else [],
        "judge_missing_or_wrong": raw.get("missing_or_wrong", []) if isinstance(raw.get("missing_or_wrong", []), list) else [],
        "judge_rationale": str(raw.get("rationale", ""))[:500],
    }


def score_all_with_judge(cases: list[dict[str, Any]], candidate_rows: list[dict[str, Any]], judge_config: dict[str, Any], out_path: Path, smoke: bool = False) -> list[dict[str, Any]]:
    by_case = {case["case_id"]: case for case in cases}
    existing = read_jsonl(out_path)
    done = {(row.get("case_id"), row.get("method")) for row in existing if row.get("judge_verdict")}
    rows_by_key = {(row.get("case_id"), row.get("method")): row for row in existing}
    target_rows = [row for row in candidate_rows if row.get("case_id") in by_case]
    for row in target_rows:
        key = (row.get("case_id"), row.get("method"))
        if key in done:
            continue
        case = by_case[str(row["case_id"])]
        gold = gold_for_case(case)
        cand = candidate_text(row)
        no = number_overlap(cand, gold["numbers"])
        tf1 = token_f1(cand, gold["text"])
        judge_raw = None
        judge_usage: dict[str, Any] = {}
        judge_error = ""
        print(json.dumps({"phase": "judge", "case_id": row.get("case_id"), "method": row.get("method"), "done": len(rows_by_key), "total": len(target_rows)}, ensure_ascii=False), flush=True)
        try:
            judge_raw, judge_usage, _raw_envelope = call_judge(judge_config, judge_messages(case, gold, row))
            parsed = parse_judge_result(judge_raw)
        except Exception as exc:
            judge_error = str(exc)[:300]
            parsed = {"judge_verdict": "scorer_uncertain", "judge_score": None, "judge_matched": [], "judge_missing_or_wrong": ["judge_call_failed"], "judge_rationale": ""}
        enriched = {
            **row,
            "number_overlap": no,
            "token_f1": tf1,
            **parsed,
            "judge_raw": judge_raw,
            "judge_usage": judge_usage,
            "judge_error": judge_error,
            "judge_vendor": judge_config["judge_vendor"],
            "judge_model": judge_config["judge_model"],
            "judge_same_vendor_as_generator": judge_config["same_vendor_as_generator"],
            "judge_bias_disclosure": judge_config["bias_disclosure"],
            "gold_text": gold["text"],
            "gold_numbers": gold["numbers"],
        }
        rows_by_key[key] = enriched
        write_jsonl(out_path, list(rows_by_key.values()))
        guard_budget(list(rows_by_key.values()), [enriched])
        if smoke and len(rows_by_key) >= len(target_rows):
            break
    return list(rows_by_key.values())


def usage_cost(row: dict[str, Any]) -> float:
    usage = row.get("usage") or {}
    model = str(row.get("model") or GEN_MODEL)
    price = TOKEN_PRICE.get(model, TOKEN_PRICE["gpt-4o-mini"])
    cost = float(usage.get("prompt_tokens") or 0) * price["input"] + float(usage.get("completion_tokens") or 0) * price["output"]
    ju = row.get("judge_usage") or {}
    jmodel = str(row.get("judge_model") or "")
    if jmodel:
        key = "claude" if row.get("judge_vendor") == "anthropic" else jmodel
        jprice = TOKEN_PRICE.get(key, TOKEN_PRICE.get("gpt-4o", TOKEN_PRICE["gpt-4o-mini"]))
        cost += float(ju.get("input_tokens") or ju.get("prompt_tokens") or 0) * jprice["input"]
        cost += float(ju.get("output_tokens") or ju.get("completion_tokens") or 0) * jprice["output"]
    return cost


def guard_budget(rows: list[dict[str, Any]], recent: list[dict[str, Any]]) -> None:
    total = sum(usage_cost(row) for row in rows)
    update_state({"estimated_total_cost_usd": round(total, 4)})
    if total > HARD_BUDGET_USD:
        raise SystemExit(f"Hard budget exceeded: ${total:.2f}")


def summarize(rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    by_method: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        by_method[str(row.get("method"))].append(row)
    out: dict[str, dict[str, Any]] = {}
    for method, vals in sorted(by_method.items()):
        judge_vals = [float(row["judge_score"]) for row in vals if isinstance(row.get("judge_score"), (int, float))]
        out[method] = {
            "n": len(vals),
            "scorer_v9_AC": avg(row.get("answer_correctness") for row in vals),
            "number_overlap": avg(row.get("number_overlap") for row in vals),
            "token_f1": avg(row.get("token_f1") for row in vals),
            "judge_score": round(sum(judge_vals) / len(judge_vals), 4) if judge_vals else None,
            "both_companies_found": avg(row.get("both_companies_found") for row in vals),
            "mean_prompt_tokens": avg(row.get("prompt_tokens") for row in vals),
            "mean_context_chars": avg(row.get("context_chars") for row in vals),
        }
    return out


def avg(values: Any) -> float:
    nums = [float(v) for v in values if isinstance(v, (int, float))]
    return round(sum(nums) / len(nums), 4) if nums else 0.0


def write_csv(path: Path, rows: list[dict[str, Any]], fields: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


def write_outputs(scored_rows: list[dict[str, Any]], judge_config: dict[str, Any]) -> dict[str, Any]:
    summary = summarize(scored_rows)
    score_rows = [{"method": method, **stats} for method, stats in summary.items()]
    write_csv(
        REEVAL / "method_scores.csv",
        score_rows,
        ["method", "n", "scorer_v9_AC", "number_overlap", "token_f1", "judge_score", "both_companies_found", "mean_prompt_tokens", "mean_context_chars"],
    )
    diag_rows = [
        {
            "case_id": row.get("case_id"),
            "method": row.get("method"),
            "failure_type": row.get("failure_type", row.get("failure_reason", "")),
            "answer_correctness": row.get("answer_correctness"),
            "judge_score": row.get("judge_score"),
            "both_companies_found": row.get("both_companies_found"),
            "contains_match": row.get("contains_match"),
        }
        for row in scored_rows
    ]
    write_csv(
        REEVAL / "hallucination_diagnostics.csv",
        diag_rows,
        ["case_id", "method", "failure_type", "answer_correctness", "judge_score", "both_companies_found", "contains_match"],
    )
    write_text(REEVAL / "fair_vs_original_vector.md", render_fair_vs_original(summary))
    write_text(REEVAL / "graph_survival_verdict.md", render_verdict(summary, judge_config))
    return summary


def render_fair_vs_original(summary: dict[str, dict[str, Any]]) -> str:
    s15 = summary.get("vector_single_chunk_v15", {})
    m15 = summary.get("vector_multi_by_company_chunk_v15", {})
    lines = [
        "# Round 15 - Fair Chunk Vector vs Original R14 Vector",
        "",
        "| Comparison | R14 doc-level AC | R15 chunk AC | R14 both_found | R15 both_found |",
        "|---|---:|---:|---:|---:|",
        f"| single | 0.0625 | {s15.get('scorer_v9_AC', 0):.4f} | 0.1250 | {s15.get('both_companies_found', 0):.4f} |",
        f"| multi | 0.0875 | {m15.get('scorer_v9_AC', 0):.4f} | 0.2250 | {m15.get('both_companies_found', 0):.4f} |",
        "",
        "R15 uses chunk-level `numpy_ondisk` retrieval with chunk_id/source_case_id/score provenance.",
    ]
    return "\n".join(lines)


def render_verdict(summary: dict[str, dict[str, Any]], judge_config: dict[str, Any]) -> str:
    graph = summary.get("graph_structured_v14", {})
    fair = summary.get("vector_multi_by_company_chunk_v15", {})
    single = summary.get("vector_single_chunk_v15", {})
    graph_ac = float(graph.get("scorer_v9_AC") or 0)
    fair_ac = float(fair.get("scorer_v9_AC") or 0)
    graph_j = graph.get("judge_score")
    fair_j = fair.get("judge_score")
    ac_beats = graph_ac > fair_ac
    judge_beats = (graph_j is not None and fair_j is not None and float(graph_j) > float(fair_j))
    if ac_beats and judge_beats:
        interp = "Graph survives the fair chunk-vector rehabilitation under both scorer_v9 AC and judge_score."
    elif ac_beats:
        interp = "Graph survives under scorer_v9 AC, but judge_score narrows or reverses the margin."
    else:
        interp = "Fair chunk vector closes or reverses the R14 graph margin; publish as retriever-baseline correction."
    lines = [
        "# Round 15 - Graph Survival Verdict",
        "",
        f"**judge_vendor:** `{judge_config['judge_vendor']}`  ",
        f"**judge_model:** `{judge_config['judge_model']}`  ",
        f"**same_vendor_as_generator:** `{judge_config['same_vendor_as_generator']}`  ",
        f"**bias disclosure:** {judge_config['bias_disclosure']}",
        "",
        "## Headline",
        "",
        interp,
        "",
        "| Method | scorer_v9_AC | judge_score | number_overlap | token_f1 | both_found | mean_prompt_tokens |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    for method in ["vector_single_chunk_v15", "vector_multi_by_company_chunk_v15", "graph_structured_v14", "graph_guided_text_v14", "source_text_concat_v14"]:
        row = summary.get(method, {})
        j = row.get("judge_score")
        judge_text = f"{float(j):.4f}" if isinstance(j, (int, float)) else "n/a"
        lines.append(
            f"| {method} | {float(row.get('scorer_v9_AC') or 0):.4f} | "
            f"{judge_text} | "
            f"{float(row.get('number_overlap') or 0):.4f} | {float(row.get('token_f1') or 0):.4f} | "
            f"{float(row.get('both_companies_found') or 0):.4f} | {float(row.get('mean_prompt_tokens') or 0):.1f} |"
        )
    lines += [
        "",
        "## Margin",
        "",
        f"- R14 graph_structured vs original vector_multi AC margin: `{0.8250 - 0.0875:.4f}`",
        f"- R15 graph_structured vs fair vector_multi AC margin: `{graph_ac - fair_ac:.4f}`",
        f"- R15 graph_structured beats fair vector by AC: `{ac_beats}`",
        f"- R15 graph_structured beats fair vector by judge_score: `{judge_beats}`",
        f"- fair vector single both_found: `{single.get('both_companies_found', 0)}`",
        f"- fair vector multi both_found: `{fair.get('both_companies_found', 0)}`",
        "",
        "## Publish Interpretation Matrix",
        "",
        "- If graph wins: multi-company graph advantage is robust to a fair chunk retriever.",
        "- If margin shrinks but graph still wins: R14 vector baseline was weak, but graph advantage remains after correction.",
        "- If fair vector ties or wins: R14 graph advantage was mostly a weak-retriever artifact; retract broad graph-win framing.",
    ]
    return "\n".join(lines)


def load_cases() -> list[dict[str, Any]]:
    return read_jsonl(R14_CASES)


def phase23(smoke_only: bool = False, full_only: bool = False) -> None:
    if not os.environ.get("OPENAI_API_KEY"):
        raise RuntimeError("OPENAI_API_KEY must be set in environment")
    cases = load_cases()
    judge_config = select_judge()
    judge_config.update({
        "generator_model": GEN_MODEL,
        "chunk_topk_single": CHUNK_TOPK_SINGLE,
        "chunk_topk_multi": CHUNK_TOPK_MULTI,
        "soft_budget_usd": SOFT_BUDGET_USD,
        "hard_budget_usd": HARD_BUDGET_USD,
        "created_at": now_iso(),
    })
    JUDGE_DIR.mkdir(parents=True, exist_ok=True)
    REEVAL.mkdir(parents=True, exist_ok=True)
    write_phase2_files(judge_config)
    print(json.dumps({"judge_vendor": judge_config["judge_vendor"], "judge_model": judge_config["judge_model"], "same_vendor_as_generator": judge_config["same_vendor_as_generator"], "chunk_topk_single": CHUNK_TOPK_SINGLE, "chunk_topk_multi": CHUNK_TOPK_MULTI}, ensure_ascii=False, indent=2), flush=True)
    update_state({"phase": "phase2_done", "judge_config": judge_config, "neo4j_write_performed": False, "existing_outputs_overwritten": False})

    run_dir = RUN_ROOT / f"round15_reeval_{ts()}"
    if full_only:
        existing = sorted(RUN_ROOT.glob("round15_reeval_*/round15_reeval_traces.jsonl"))
        run_dir = existing[-1].parent if existing else run_dir
    run_dir.mkdir(parents=True, exist_ok=True)
    update_state({"run_dir": str(run_dir), "trace_file": str(run_dir / "round15_reeval_traces.jsonl")})

    if not full_only:
        smoke_cases = cases[:3]
        smoke_vector_rows = generate_vectors(smoke_cases, run_dir, smoke=True)
        smoke_candidates = smoke_vector_rows + reused_trace_rows(smoke_cases)
        smoke_scored = score_all_with_judge(smoke_cases, smoke_candidates, judge_config, REEVAL / "judge_smoke_traces.jsonl", smoke=True)
        smoke_ok = len(smoke_scored) == len(smoke_cases) * len(ALL_METHODS) and all(row.get("judge_verdict") != "scorer_uncertain" for row in smoke_scored)
        write_json(JUDGE_DIR / "judge_smoke_report.json", {"passed": smoke_ok, "rows": len(smoke_scored), "expected": len(smoke_cases) * len(ALL_METHODS)})
        update_state({"phase": "judge_smoke_done", "judge_smoke_passed": smoke_ok})
        print(json.dumps({"judge_smoke_passed": smoke_ok, "rows": len(smoke_scored)}, ensure_ascii=False), flush=True)
        if not smoke_ok or smoke_only:
            return

    vector_rows = generate_vectors(cases, run_dir, smoke=False)
    candidates = vector_rows + reused_trace_rows(cases)
    scored = score_all_with_judge(cases, candidates, judge_config, REEVAL / "reeval_traces.jsonl", smoke=False)
    write_jsonl(REEVAL / "reeval_traces.jsonl", scored)
    write_jsonl(run_dir / "round15_reeval_traces.jsonl", [row for row in scored if row.get("method") in NEW_VECTOR_METHODS])
    summary = write_outputs(scored, judge_config)
    final = {
        "phase": "done",
        "judge_vendor": judge_config["judge_vendor"],
        "judge_model": judge_config["judge_model"],
        "same_vendor_as_generator": judge_config["same_vendor_as_generator"],
        "methods": summary,
        "estimated_total_cost_usd": round(sum(usage_cost(row) for row in scored), 4),
        "neo4j_write_performed": False,
        "existing_outputs_overwritten": False,
        "generated_files": [
            rel(EVAL_POLICY),
            rel(JUDGE_DIR / "judge_config.json"),
            rel(REEVAL / "reeval_traces.jsonl"),
            rel(REEVAL / "method_scores.csv"),
            rel(REEVAL / "fair_vs_original_vector.md"),
            rel(REEVAL / "graph_survival_verdict.md"),
            rel(REEVAL / "hallucination_diagnostics.csv"),
            rel(run_dir / "round15_reeval_traces.jsonl"),
        ],
    }
    update_state(final)
    print_final(final)


def print_final(state: dict[str, Any]) -> None:
    methods = state["methods"]
    graph = methods.get("graph_structured_v14", {})
    single = methods.get("vector_single_chunk_v15", {})
    multi = methods.get("vector_multi_by_company_chunk_v15", {})
    out = {
        "judge_vendor": state["judge_vendor"],
        "same_vendor_as_generator": state["same_vendor_as_generator"],
        "fair_vector_single_AC_chunk_v15": single.get("scorer_v9_AC"),
        "R14_single_doc_level_AC": 0.0625,
        "fair_vector_multi_AC_chunk_v15": multi.get("scorer_v9_AC"),
        "R14_multi_doc_level_AC": 0.0875,
        "graph_structured_judge_score": graph.get("judge_score"),
        "graph_beats_fair_vector_AC": float(graph.get("scorer_v9_AC") or 0) > float(multi.get("scorer_v9_AC") or 0),
        "graph_beats_fair_vector_judge": graph.get("judge_score") is not None and multi.get("judge_score") is not None and float(graph["judge_score"]) > float(multi["judge_score"]),
        "both_companies_found_fair_single": single.get("both_companies_found"),
        "both_companies_found_fair_multi": multi.get("both_companies_found"),
        "neo4j_write_performed": False,
        "existing_outputs_overwritten": False,
        "total_cost": state["estimated_total_cost_usd"],
    }
    print(json.dumps(out, ensure_ascii=False, indent=2, sort_keys=True), flush=True)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--smoke-only", action="store_true")
    parser.add_argument("--full-only", action="store_true")
    args = parser.parse_args()
    phase23(smoke_only=args.smoke_only, full_only=args.full_only)


if __name__ == "__main__":
    main()
