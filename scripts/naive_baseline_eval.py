from __future__ import annotations

import argparse
import json
import random
import socket
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

import round10_common as c
from scorer_v9 import score_trace


ROUND = "naive_baseline"
CLAIM_BOUNDARY = "portfolio_naive_baseline_comparison"
PROMPT_VERSION = "naive_v1"
SCORER_VERSION = "v9"
OUT_DIR = c.ROOT / "outputs" / "naive_baseline"
CASE_SAMPLE = OUT_DIR / "case_sample.jsonl"
MINI_TRACES = OUT_DIR / "naive_gpt4omini_traces.jsonl"
FOUR_O_TRACES = OUT_DIR / "naive_gpt4o_traces.jsonl"
SUMMARY = OUT_DIR / "comparison_summary.md"
STATE = OUT_DIR / "state.json"
R10_RUN_DIR = c.ROOT / "outputs" / "round3_eval_runs" / "round10_eval_20260529_170409"
R10_TRACES = R10_RUN_DIR / "round10_traces.jsonl"

METHOD_CONFIG = {
    "naive_gpt4omini": {"model": "gpt-4o-mini", "trace_path": MINI_TRACES},
    "naive_gpt4o": {"model": "gpt-4o", "trace_path": FOUR_O_TRACES},
}

PROVIDER_ERROR_TYPES = {
    "provider_auth",
    "provider_rate_limit",
    "provider_unavailable",
    "provider_timeout",
    "provider_bad_response",
    "provider_unknown",
}


def avg(values: list[Any]) -> float:
    nums = [float(value) for value in values]
    return round(sum(nums) / len(nums), 4) if nums else 0.0


def load_sample() -> list[dict[str, Any]]:
    if CASE_SAMPLE.exists():
        return c.read_jsonl(CASE_SAMPLE)
    rng = random.Random(42)
    selected = (
        rng.sample(c.read_jsonl(c.FINDER_CANDIDATES), 26)
        + rng.sample(c.read_jsonl(c.FINQA_CANDIDATES), 11)
        + rng.sample(c.read_jsonl(c.TATQA_CANDIDATES), 13)
    )
    c.write_jsonl(CASE_SAMPLE, selected)
    return selected


def build_prompt(case: dict[str, Any]) -> dict[str, str]:
    return {
        "system": """You are a financial analyst assistant.
Given a financial document and a question, compute the answer and return it in JSON format.
Use only the information provided in the document. Do not use outside knowledge.

Return exactly this JSON structure:
{
  "final_answer": "<numeric result with unit, e.g. '42.3%' or '$1.2 billion'>",
  "calculation": "<brief explanation of how you calculated it>"
}
""",
        "user": f"""FINANCIAL DOCUMENT:
{case.get('evidence_text', '')}

QUESTION:
{case.get('question', '')}
""",
    }


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


def call_openai_json(prompt: dict[str, str], model: str) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": prompt["system"]},
            {"role": "user", "content": prompt["user"]},
        ],
        "temperature": 0,
        "response_format": {"type": "json_object"},
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
        result = extract_json_object(data["choices"][0]["message"]["content"])
    except Exception as exc:  # noqa: BLE001
        raise c.r8.ProviderError("provider_bad_response", "provider returned non-JSON model output") from exc
    return result, data.get("usage", {}), data


def load_graph_subset(sample: list[dict[str, Any]]) -> list[dict[str, Any]]:
    sample_ids = {case["case_id"] for case in sample}
    rows = [
        row
        for row in c.read_jsonl(R10_TRACES)
        if row.get("case_id") in sample_ids and row.get("method") == "graph_neo4j_v10"
    ]
    missing = sorted(sample_ids - {row["case_id"] for row in rows})
    if missing:
        raise RuntimeError(f"Missing R10 graph rows for sample cases: {missing[:10]}")
    provider_rows = [row for row in rows if row.get("failure_reason") == "provider_error"]
    if provider_rows:
        ids = [row["case_id"] for row in provider_rows]
        raise RuntimeError(f"R10 graph subset contains provider errors: {ids}")
    return rows


def trace_rows(path: Path) -> list[dict[str, Any]]:
    rows = c.read_jsonl(path)
    return [row for row in rows if row.get("failure_reason") != "provider_error"]


def update_state(sample: list[dict[str, Any]], graph_rows: list[dict[str, Any]], rows_by_method: dict[str, list[dict[str, Any]]], phase: str) -> None:
    mini_rows = rows_by_method.get("naive_gpt4omini", [])
    four_o_rows = rows_by_method.get("naive_gpt4o", [])
    graph_ac = avg([row["answer_correctness"] for row in graph_rows])
    mini_ac = avg([row["answer_correctness"] for row in mini_rows])
    four_o_ac = avg([row["answer_correctness"] for row in four_o_rows])
    state = {
        "phase": phase,
        "round": ROUND,
        "claim_boundary": CLAIM_BOUNDARY,
        "n_cases": len(sample),
        "cases_finder": len([case for case in sample if case.get("source_dataset") == "FinDER"]),
        "cases_finqa": len([case for case in sample if case.get("source_dataset") == "FinQA"]),
        "cases_tatqa": len([case for case in sample if case.get("source_dataset") == "TAT-QA"]),
        "model_naive_mini": METHOD_CONFIG["naive_gpt4omini"]["model"],
        "model_naive_4o": METHOD_CONFIG["naive_gpt4o"]["model"],
        "prompt_version": PROMPT_VERSION,
        "scorer_version": SCORER_VERSION,
        "r10_graph_ac_on_subset": graph_ac,
        "r10_graph_nc_on_subset": avg([row["numerical_closeness"] for row in graph_rows]),
        "naive_mini_ac": mini_ac,
        "naive_mini_nc": avg([row["numerical_closeness"] for row in mini_rows]),
        "naive_4o_ac": four_o_ac,
        "naive_4o_nc": avg([row["numerical_closeness"] for row in four_o_rows]),
        "graph_beats_naive_mini": graph_ac > mini_ac if mini_rows else None,
        "graph_beats_naive_4o": graph_ac > four_o_ac if four_o_rows else None,
        "model_calls": len(mini_rows) + len(four_o_rows),
        "runs_total": len(sample) * 2,
        "runs_completed": len(mini_rows) + len(four_o_rows),
        "runs_by_method": {method: len(rows) for method, rows in rows_by_method.items()},
        "updated_at": c.utc_now(),
    }
    if phase == "done":
        state["completed_at"] = c.utc_now()
    c.write_json(STATE, state)


def score_result(
    case: dict[str, Any],
    scorer_contract: dict[str, Any],
    method: str,
    model: str,
    prompt: dict[str, str],
    result: dict[str, Any] | None,
    usage: dict[str, Any],
    raw: dict[str, Any] | None,
    error_type: str = "",
    error_message: str = "",
) -> dict[str, Any]:
    final_answer = "" if result is None else str(result.get("final_answer", ""))
    calculation = "" if result is None else str(result.get("calculation", ""))
    base = {
        "trace_id": f"local_trace_naive_{case['case_id']}__{method}",
        "case_id": case["case_id"],
        "ticker": case.get("ticker", ""),
        "split": "round10_sample",
        "source_dataset": case.get("source_dataset", ""),
        "method": method,
        "round": ROUND,
        "kg_batch": "N/A",
        "prompt_version": PROMPT_VERSION,
        "scoring_version": SCORER_VERSION,
        "scorer_version": SCORER_VERSION,
        "claim_boundary": CLAIM_BOUNDARY,
        "formula_type": scorer_contract.get("formula_type", ""),
        "neo4j_facts_count": 0,
        "target_slot_count": len(scorer_contract.get("target_slots", [])),
        "provider": "openai",
        "model": model,
        "success": result is not None,
        "provider_success": result is not None,
        "error_type": error_type,
        "error_message": error_message,
        "required_fact_recall": 1.0,
        "final_answer": final_answer,
        "calculation": calculation,
        "prompt_sha256": c.sha(prompt["system"] + "\n" + prompt["user"]),
        "method_result": {"final_answer": final_answer, "calculation": calculation} if result is not None else None,
        "raw_method_result_naive": raw,
        "usage": usage,
        "model_api_called": True,
        "neo4j_write_performed": False,
    }
    row = score_trace(base, scorer_contract, method)
    if error_type in PROVIDER_ERROR_TYPES:
        row["failure_reason"] = "provider_error"
        row["answer_correctness"] = 0.0
    return row


def run_method(
    method: str,
    sample: list[dict[str, Any]],
    scorer: dict[str, Any],
    graph_rows: list[dict[str, Any]],
    rows_by_method: dict[str, list[dict[str, Any]]],
    resume: bool,
) -> list[dict[str, Any]]:
    model = METHOD_CONFIG[method]["model"]
    trace_path = METHOD_CONFIG[method]["trace_path"]
    rows = trace_rows(trace_path) if resume else []
    completed = {row["case_id"] for row in rows}
    for case in sample:
        cid = case["case_id"]
        if cid in completed:
            continue
        prompt = build_prompt(case)
        result: dict[str, Any] | None = None
        raw: dict[str, Any] | None = None
        usage: dict[str, Any] = {}
        error_type = ""
        error_message = ""
        try:
            result, usage, raw = call_openai_json(prompt, model)
        except c.r8.ProviderError as exc:
            error_type = exc.error_type
            error_message = str(exc)
        except Exception as exc:  # noqa: BLE001
            error_type = "provider_unknown"
            error_message = str(exc)[:300]
        row = score_result(case, scorer[cid], method, model, prompt, result, usage, raw, error_type, error_message)
        rows.append(row)
        completed.add(cid)
        c.write_jsonl(trace_path, rows)
        rows_by_method[method] = rows
        update_state(sample, graph_rows, rows_by_method, "running")
        print(
            json.dumps(
                {
                    "method": method,
                    "model": model,
                    "runs_completed": sum(len(v) for v in rows_by_method.values()),
                    "case_id": cid,
                    "failure_reason": row.get("failure_reason"),
                    "answer_correctness": row.get("answer_correctness"),
                },
                ensure_ascii=False,
            ),
            flush=True,
        )
        time.sleep(0.2)
    return rows


def dataset_avgs(rows: list[dict[str, Any]]) -> dict[str, float]:
    return {
        dataset: avg([row["answer_correctness"] for row in rows if row.get("source_dataset") == dataset])
        for dataset in ["FinDER", "FinQA", "TAT-QA"]
    }


def write_summary(sample: list[dict[str, Any]], graph_rows: list[dict[str, Any]], rows_by_method: dict[str, list[dict[str, Any]]]) -> None:
    mini_rows = rows_by_method["naive_gpt4omini"]
    four_o_rows = rows_by_method["naive_gpt4o"]
    graph_ac = avg([row["answer_correctness"] for row in graph_rows])
    mini_ac = avg([row["answer_correctness"] for row in mini_rows])
    four_o_ac = avg([row["answer_correctness"] for row in four_o_rows])
    graph_by_ds = dataset_avgs(graph_rows)
    mini_by_ds = dataset_avgs(mini_rows)
    four_o_by_ds = dataset_avgs(four_o_rows)
    counts = {dataset: len([case for case in sample if case.get("source_dataset") == dataset]) for dataset in ["FinDER", "FinQA", "TAT-QA"]}
    lines = [
        "# Naive Baseline Comparison",
        "",
        "## Overall on 50-Case Subset",
        "",
        "| Method | avg_ac | avg_nc | model | prompt |",
        "|---|---:|---:|---|---|",
        f"| graph_neo4j_v10 (R10) | {graph_ac:.4f} | {avg([row['numerical_closeness'] for row in graph_rows]):.4f} | gpt-4o-mini | structured v3.4 + KG |",
        f"| naive_gpt4omini | {mini_ac:.4f} | {avg([row['numerical_closeness'] for row in mini_rows]):.4f} | gpt-4o-mini | naive |",
        f"| naive_gpt4o | {four_o_ac:.4f} | {avg([row['numerical_closeness'] for row in four_o_rows]):.4f} | gpt-4o | naive |",
        "",
        "## By Dataset",
        "",
        "| Dataset | graph_v10 | naive_mini | naive_4o |",
        "|---|---:|---:|---:|",
    ]
    for dataset in ["FinDER", "FinQA", "TAT-QA"]:
        lines.append(f"| {dataset} ({counts[dataset]}) | {graph_by_ds[dataset]:.4f} | {mini_by_ds[dataset]:.4f} | {four_o_by_ds[dataset]:.4f} |")
    lines.extend(
        [
            "",
            "## Diagnostic Questions",
            "",
            f"1. graph_neo4j > naive_gpt4omini? {graph_ac > mini_ac} ({graph_ac - mini_ac:+.4f})",
            f"2. naive_gpt4o > graph_neo4j? {four_o_ac > graph_ac} ({four_o_ac - graph_ac:+.4f})",
            f"3. naive_gpt4o > naive_gpt4omini? {four_o_ac > mini_ac} ({four_o_ac - mini_ac:+.4f})",
        ]
    )
    c.write_text(SUMMARY, "\n".join(lines))


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--resume", action=argparse.BooleanOptionalAction, default=True)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    r10_state = c.read_json(c.EVAL_STATE)
    if r10_state.get("phase") != "done":
        raise RuntimeError("Round10 eval state is not done")
    scorer, _visible = c.load_contract_maps()
    sample = [case for case in load_sample() if case["case_id"] in scorer]
    if len(sample) != 50:
        raise RuntimeError(f"Expected 50 sampled cases with scorer contracts, got {len(sample)}")
    graph_rows = load_graph_subset(sample)
    rows_by_method = {
        "naive_gpt4omini": trace_rows(MINI_TRACES) if args.resume else [],
        "naive_gpt4o": trace_rows(FOUR_O_TRACES) if args.resume else [],
    }
    update_state(sample, graph_rows, rows_by_method, "running")
    for method in ["naive_gpt4omini", "naive_gpt4o"]:
        rows_by_method[method] = run_method(method, sample, scorer, graph_rows, rows_by_method, args.resume)
    update_state(sample, graph_rows, rows_by_method, "done")
    write_summary(sample, graph_rows, rows_by_method)
    state = c.read_json(STATE)
    print(json.dumps({"state": c.rel(STATE), "summary": c.rel(SUMMARY), **state}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
