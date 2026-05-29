"""Run Round 5 diagnostic evaluation with post-hoc test formula contracts."""

from __future__ import annotations

import argparse
import csv
import importlib.util
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
ROUND4_PATH = ROOT / "scripts" / "round4_eval_llm_ie_kg.py"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

spec = importlib.util.spec_from_file_location("round4_eval_llm_ie_kg", ROUND4_PATH)
r4 = importlib.util.module_from_spec(spec)
assert spec and spec.loader
spec.loader.exec_module(r4)


METHODS = ["vector_only_v5", "graph_neo4j_v5", "hybrid_neo4j_v5"]
MODEL = "gpt-4o-mini"
TRACK = "track_b_neo4j_llm_ie"
KG_BATCH = "kg-llm-ie-v1-20260528"
DIAGNOSTIC_LABEL = "round5_diagnostic_post_hoc_formula_contracts"
TEST_FORMULA_SOURCE = "post_hoc"
CLAIM_BOUNDARY = "method_comparison_valid_absolute_scores_diagnostic"

OUT_ROOT = ROOT / "outputs" / "round3_eval_runs"
STATE_PATH = ROOT / "outputs" / "round5_diagnostic_eval" / "state.json"
TRACK_B = ROOT / "outputs" / "round3_dual_track_eval_prep" / "track_b_shadow_overlay"
CASES_PATH = TRACK_B / "shadow_overlay_eval_ready_cases.jsonl"
REQUIRED_FACTS_PATH = TRACK_B / "shadow_overlay_required_facts.jsonl"
PROMPTS = ROOT / "outputs" / "round3_eval_harness" / "prompts"
PROMPTS_FALLBACK = ROOT / "outputs" / "round3_dual_track_eval_prep" / "prompt_formatter_v3_2"
CLEAN = ROOT / "outputs" / "round3_eval_harness" / "formula_contract_v3_2_clean_dev"
DEV_BASELINE = ROOT / "outputs" / "round3_eval_harness" / "formula_contract_v3_2" / "dev_baseline_contracts"
TEST_CONTRACTS = ROOT / "outputs" / "round3_eval_harness" / "formula_contract_v3_2_test_split"
ROUND4_RUN = OUT_ROOT / "round4_llm_ie_kg_20260528_191900"


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def ts() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S")


def rel(path: Path) -> str:
    return path.resolve().relative_to(ROOT.resolve()).as_posix()


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


def write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text.rstrip() + "\n", encoding="utf-8", newline="\n")


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")


def load_cases() -> list[dict[str, Any]]:
    return read_jsonl(CASES_PATH)


def load_required_facts() -> dict[str, list[dict[str, Any]]]:
    out: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in read_jsonl(REQUIRED_FACTS_PATH):
        out[row["case_id"]].append(row)
    return out


def index_contract(path: Path, field: str) -> dict[str, Any]:
    return {row["case_id"]: row[field] for row in read_jsonl(path) if row.get("case_id") and field in row}


def prefer_contract(existing: dict[str, Any] | None, candidate: dict[str, Any]) -> dict[str, Any]:
    if existing is None:
        return candidate
    existing_slots = existing.get("target_slots", [])
    candidate_slots = candidate.get("target_slots", [])
    if not existing_slots and candidate_slots:
        return candidate
    return existing


def load_all_formula_contracts() -> tuple[dict[str, Any], dict[str, Any]]:
    scorer: dict[str, Any] = {}
    visible: dict[str, Any] = {}

    # The current repo's clean_dev files contain 9 rows, while dev_baseline
    # contains the full 15 dev/baseline contracts. Keep non-empty target slots
    # when the two dev sources overlap, then let test contracts take precedence.
    for path in [
        CLEAN / "clean_dev_scorer_only_target_slot_contracts.jsonl",
        DEV_BASELINE / "dev_baseline_scorer_only_target_slot_contracts.jsonl",
    ]:
        for case_id, contract in index_contract(path, "scorer_only_target_slot_contract").items():
            scorer[case_id] = prefer_contract(scorer.get(case_id), contract)
    scorer.update(index_contract(TEST_CONTRACTS / "test_scorer_contracts.jsonl", "scorer_only_target_slot_contract"))

    for path in [
        CLEAN / "clean_dev_model_visible_formula_contracts.jsonl",
        DEV_BASELINE / "dev_baseline_model_visible_formula_contracts.jsonl",
        TEST_CONTRACTS / "test_model_visible_contracts.jsonl",
    ]:
        visible.update(index_contract(path, "model_visible_formula_contract"))
    return scorer, visible


def fact_to_target_slot(case_id: str, idx: int, fact: dict[str, Any]) -> dict[str, Any]:
    metric = str(fact.get("metric") or fact.get("metric_canonical") or "source_fact")
    year = fact.get("year")
    unit = str(fact.get("unit") or "")
    value = fact.get("value")
    return {
        "target_slot_name": f"source_{metric}_{year}_{idx:02d}",
        "expected_value": value,
        "unit": unit,
        "years": [year] if year not in (None, "") else [],
        "derived_or_source": "source",
        "required_for_answer": True,
        "acceptable_equivalent_forms": [str(value)],
        "source_case_id": case_id,
    }


def fill_source_target_slots(
    cases: list[dict[str, Any]],
    scorer_contracts: dict[str, Any],
    required_by_case: dict[str, list[dict[str, Any]]],
) -> list[str]:
    filled: list[str] = []
    for case in cases:
        cid = case["case_id"]
        contract = scorer_contracts[cid]
        if contract.get("target_slots"):
            continue
        facts = contract.get("source_fact_numbers") or required_by_case.get(cid, [])
        deduped: list[dict[str, Any]] = []
        seen: set[tuple[str, str, str]] = set()
        for fact in facts:
            key = (
                str(fact.get("metric") or fact.get("metric_canonical") or ""),
                str(fact.get("year") or ""),
                str(fact.get("value") or ""),
            )
            if key in seen:
                continue
            seen.add(key)
            deduped.append(fact)
        if not deduped:
            continue
        slots = [fact_to_target_slot(cid, idx, fact) for idx, fact in enumerate(deduped, start=1)]
        contract["target_slots"] = slots
        contract["final_target_numbers"] = slots
        contract["diagnostic_source_target_fallback"] = True
        contract["diagnostic_note"] = "No final target slot contract existed; source facts are used as diagnostic target slots."
        filled.append(cid)
    return filled


def prompt_dir() -> Path:
    return PROMPTS if (PROMPTS / "prompt_v3_2_system.md").exists() else PROMPTS_FALLBACK


def fact_table(facts: list[dict[str, Any]]) -> str:
    return r4.fact_table(facts)


def formula_section(model_visible_contract: dict[str, Any] | None) -> str:
    if not model_visible_contract:
        return ""
    steps = "\n".join("- " + str(step) for step in model_visible_contract.get("required_steps", []))
    return f"""
FORMULA_CONTRACT
formula_type: {model_visible_contract.get('formula_type', '')}
template: {model_visible_contract.get('target_formula_template', '')}
target_years: {model_visible_contract.get('target_years', [])}
required_steps:
{steps}
rounding: {model_visible_contract.get('rounding_instruction', 'use v3.2 rounding rules')}
"""


def build_prompt(case: dict[str, Any], method: str, facts: list[dict[str, Any]], model_visible_contract: dict[str, Any]) -> dict[str, str]:
    pdir = prompt_dir()
    system = (pdir / "prompt_v3_2_system.md").read_text(encoding="utf-8")
    answer_format = (pdir / "answer_format_spec_v3_2.md").read_text(encoding="utf-8")
    rounding = (pdir / "rounding_and_tolerance_rules_v3_2.md").read_text(encoding="utf-8")
    evidence = str(case.get("evidence_text", ""))
    if method == "vector_only_v5":
        context = f"TEXT_CONTEXT\n{evidence}"
    elif method == "graph_neo4j_v5":
        context = f"GRAPH_FACTS_TABLE\n{fact_table(facts)}"
    elif method == "hybrid_neo4j_v5":
        context = f"TEXT_CONTEXT\n{evidence}\n\nGRAPH_FACTS_TABLE\n{fact_table(facts)}"
    else:
        raise RuntimeError(f"unknown method: {method}")
    user = f"""QUESTION
{case['question']}

{context}
{formula_section(model_visible_contract)}

ANSWER_FORMAT
{answer_format}

ROUNDING_AND_TOLERANCE_RULES
{rounding}

Use temperature=0 behavior. Do not use outside knowledge. Do not mention hidden expected answers or scorer-only target slots.
"""
    return {"system": system, "user": user}


def create_driver() -> Any:
    return r4.create_driver()


def load_neo4j_graph_facts_filtered(
    ticker: str,
    case_id: str,
    years: list[int],
    metric_tags: list[str],
    driver: Any,
) -> list[dict[str, Any]]:
    database = r4.neo4j_env()["NEO4J_DATABASE"]
    years = [int(year) for year in years if str(year).isdigit()]
    last_error: Exception | None = None
    for attempt in range(3):
        active_driver = driver if attempt == 0 else create_driver()
        try:
            with active_driver.session(database=database) as session:
                records = session.run(
                    """
MATCH (obs:LLMObservation)-[:LLM_MENTIONS_COMPANY]->(c:LLMCompany {ticker: $ticker}),
      (obs)-[:LLM_OBSERVES_METRIC]->(m:LLMFinancialMetric),
      (obs)-[:LLM_OBSERVED_IN_YEAR]->(yr:LLMFiscalYear)
WHERE obs.kg_batch = $batch
  AND yr.year IN $years
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
                    years=years,
                )
                all_facts = [
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
                    for rec in records
                ]
                if len(all_facts) > 20 and metric_tags:
                    tag_keywords: set[str] = set()
                    for tag in metric_tags:
                        tag_keywords.update(str(tag).lower().replace("_", " ").split())
                    tag_keywords = {kw for kw in tag_keywords if len(kw) >= 4}
                    filtered = [
                        fact
                        for fact in all_facts
                        if any(kw in str(fact["metric_canonical"]).lower() for kw in tag_keywords)
                    ]
                    if len(filtered) >= 3:
                        return filtered
                return all_facts
        except Exception as exc:  # noqa: BLE001
            last_error = exc
            time.sleep(2 * (attempt + 1))
        finally:
            if attempt > 0:
                active_driver.close()
    raise RuntimeError(f"Neo4j read failed after retries for {ticker}/{case_id}: {last_error}") from last_error


def call_openai(prompt: dict[str, str]) -> tuple[Any, dict[str, Any], dict[str, Any]]:
    key = r4.openai_api_key()
    payload = {
        "model": MODEL,
        "messages": [
            {"role": "system", "content": prompt["system"] + "\nReturn only a valid JSON object."},
            {"role": "user", "content": prompt["user"]},
        ],
        "temperature": 0,
        "response_format": {"type": "json_object"},
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
        raise r4.ProviderError(r4.classify_http(exc.code), r4.sanitize_error(exc)) from exc
    except (socket.timeout, TimeoutError) as exc:
        raise r4.ProviderError("provider_timeout", r4.sanitize_error(exc)) from exc
    except urllib.error.URLError as exc:
        raise r4.ProviderError("provider_unknown", r4.sanitize_error(exc)) from exc
    except json.JSONDecodeError as exc:
        raise r4.ProviderError("provider_bad_response", "provider returned invalid JSON envelope") from exc
    try:
        parsed = r4.extract_json_object(data["choices"][0]["message"]["content"])
    except Exception as exc:  # noqa: BLE001
        raise r4.ProviderError("provider_bad_response", "provider returned non-JSON model output") from exc
    return r4.adapt_result(parsed), data.get("usage", {}), parsed


def required_fact_recall(required_facts: list[dict[str, Any]], neo4j_facts: list[dict[str, Any]]) -> float:
    if not required_facts:
        return 0.0
    matched = 0
    for req in required_facts:
        req_ticker = str(req.get("ticker") or "").upper()
        req_metric = str(req.get("metric_canonical") or req.get("metric") or "")
        req_year = str(req.get("year") or "")
        for fact in neo4j_facts:
            if req_ticker and str(fact.get("ticker") or "").upper() != req_ticker:
                continue
            if req_year and str(fact.get("year") or "") != req_year:
                continue
            if not r4.metric_matches(req_metric, str(fact.get("metric_canonical") or "")):
                continue
            expected = r4.parse_number(str(req.get("value", "")))
            actual = r4.parse_number(str(fact.get("value", "")))
            if expected and actual and (
                math.isclose(expected["value"], actual["value"], rel_tol=0.01, abs_tol=0.01)
                or math.isclose(expected["scaled_value"], actual["scaled_value"], rel_tol=0.01, abs_tol=0.01)
            ):
                matched += 1
                break
    return round(matched / len(required_facts), 4)


def slot_numerical_closeness(expected: dict[str, Any] | None, actual: list[dict[str, Any]]) -> float:
    if not expected or not actual:
        return 0.0
    expected_values = [float(expected["value"]), float(expected["scaled_value"])]
    best = 0.0
    for candidate in actual:
        candidate_values = [float(candidate["value"]), float(candidate["scaled_value"])]
        for exp_value in expected_values:
            denom = max(abs(exp_value), 1.0)
            for got_value in candidate_values:
                relative_error = abs(got_value - exp_value) / denom
                best = max(best, max(0.0, 1.0 - relative_error))
    return best


def score_result(
    method: str,
    result: Any | None,
    required_facts: list[dict[str, Any]],
    neo4j_facts: list[dict[str, Any]],
    scorer_contract: dict[str, Any],
) -> dict[str, Any]:
    if result is None:
        return {
            "required_fact_recall": 0.0,
            "target_numeric_recall": 0.0,
            "numerical_closeness": 0.0,
            "numeric_correctness": 0.0,
            "answer_correctness": 0.0,
            "faithfulness": 0.0,
            "calculation_completeness": 0.0,
            "answer_format_compliance": 0.0,
            "failure_reason": "provider_error",
            "matched_target_slots": "",
            "missing_target_slots": "",
        }
    output = "\n".join([result.final_answer, result.calculation])
    actual = r4.extract_numbers(output)
    slots = scorer_contract.get("target_slots", [])
    matched: list[str] = []
    missing: list[str] = []
    closeness_scores: list[float] = []
    for slot in slots:
        expected = r4.parse_number(str(slot["expected_value"]))
        closeness_scores.append(slot_numerical_closeness(expected, actual))
        if expected and any(r4.close(expected, candidate, slot.get("unit", "")) for candidate in actual):
            matched.append(slot["target_slot_name"])
        else:
            missing.append(slot["target_slot_name"])
    target_recall = round(len(matched) / len(slots), 4) if slots else 0.0
    numerical_closeness = round(sum(closeness_scores) / len(closeness_scores), 4) if closeness_scores else 0.0
    numeric_ok = target_recall >= 0.8 if slots else False
    rfr = 1.0 if method == "vector_only_v5" else required_fact_recall(required_facts, neo4j_facts)
    fmt = bool(result.final_answer and result.calculation)
    calc = bool(result.calculation and any(token in result.calculation.lower() for token in ["/", "%", "=", "ratio", "rate", "margin", "growth", "change", "formula"]))
    faith = rfr >= 0.8
    ans = numeric_ok and fmt and calc and faith
    failure = "none"
    if not fmt:
        failure = "answer_format_error"
    elif rfr < 0.5:
        failure = "required_fact_missing"
    elif not numeric_ok:
        failure = "formula_target_mismatch"
    elif not ans:
        failure = "scoring_uncertain"
    return {
        "required_fact_recall": rfr,
        "target_numeric_recall": target_recall,
        "numerical_closeness": numerical_closeness,
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


def summarize(rows: list[dict[str, Any]], split_filter: set[str] | None = None) -> list[dict[str, Any]]:
    selected = [row for row in rows if split_filter is None or row["split"] in split_filter]
    out = []
    for method in METHODS:
        items = [row for row in selected if row["method"] == method]
        out.append(
            {
                "method": method,
                "avg_ac": avg([row["answer_correctness"] for row in items]),
                "avg_nc": avg([row["numeric_correctness"] for row in items]),
                "avg_rfr": avg([row["required_fact_recall"] for row in items]),
                "avg_facts": avg([row["neo4j_facts_count"] for row in items]),
            }
        )
    return out


def round4_dev_baseline_summary() -> dict[str, float]:
    path = ROUND4_RUN / "round4_traces.jsonl"
    if not path.exists():
        return {}
    rows = [row for row in read_jsonl(path) if row.get("split") in {"round3_dev", "baseline_control"}]
    mapping = {"vector_only_v4": "vector_only", "graph_neo4j_v4": "graph_neo4j", "hybrid_neo4j_v4": "hybrid_neo4j"}
    return {label: avg([row["answer_correctness"] for row in rows if row["method"] == method]) for method, label in mapping.items()}


def write_summary(run_dir: Path, rows: list[dict[str, Any]], facts_cache: list[dict[str, Any]], source_target_fallback_count: int = 0) -> None:
    overall = summarize(rows)
    dev = summarize(rows, {"round3_dev", "baseline_control"})
    test = summarize(rows, {"round3_test"})
    r4_dev = round4_dev_baseline_summary()
    r5_dev = {row["method"].replace("_v5", ""): row["avg_ac"] for row in dev}
    counts = [int(row["neo4j_facts_count"]) for row in facts_cache]
    r4_counts = []
    r4_cache = ROUND4_RUN / "neo4j_facts_cache.jsonl"
    if r4_cache.exists():
        r4_counts = [int(row.get("neo4j_facts_count", 0)) for row in read_jsonl(r4_cache)]
    lines = [
        "# Round 5 Diagnostic Evaluation",
        "",
        "**Diagnostic label:** post-hoc formula contracts for test split",
        "**Claim boundary:** method comparison is valid; absolute scores are diagnostic",
        f"**Source-target fallback cases:** {source_target_fallback_count}",
        "",
        "## Overall (25 cases)",
        "",
        "| Method | avg_ac | avg_nc | avg_rfr | avg_facts |",
        "|---|---:|---:|---:|---:|",
    ]
    lines.extend(f"| {row['method']} | {row['avg_ac']} | {row['avg_nc']} | {row['avg_rfr']} | {row['avg_facts']} |" for row in overall)
    lines.extend(["", "## Dev/Baseline only (15 cases) - formula/source-target diagnostic contracts", "", "| Method | avg_ac | avg_nc | avg_rfr |", "|---|---:|---:|---:|"])
    lines.extend(f"| {row['method']} | {row['avg_ac']} | {row['avg_nc']} | {row['avg_rfr']} |" for row in dev)
    lines.extend(["", "## Test split only (10 cases) - FIRST VALID SCORING", "", "| Method | avg_ac | avg_nc | avg_rfr |", "|---|---:|---:|---:|"])
    lines.extend(f"| {row['method']} | {row['avg_ac']} | {row['avg_nc']} | {row['avg_rfr']} |" for row in test)
    lines.extend(["", "## Comparison: Round 4 vs Round 5 (dev+baseline 15 cases only)", "", "| Method | Round 4 avg_ac | Round 5 avg_ac | delta |", "|---|---:|---:|---:|"])
    for method in ["vector_only", "graph_neo4j", "hybrid_neo4j"]:
        before = r4_dev.get(method, 0.0)
        after = r5_dev.get(method, 0.0)
        lines.append(f"| {method} | {before} | {after} | {round(after - before, 4)} |")
    lines.extend(["", "## Neo4j Facts Count (Round 5 filtered)", "", "| Stat | Round 4 | Round 5 |", "|---|---:|---:|"])
    lines.append(f"| Average | {avg(r4_counts) if r4_counts else 57.4} | {avg(counts)} |")
    lines.append(f"| Min | {min(r4_counts) if r4_counts else 20} | {min(counts) if counts else 0} |")
    lines.append(f"| Max | {max(r4_counts) if r4_counts else 108} | {max(counts) if counts else 0} |")
    write_text(run_dir / "round5_summary.md", "\n".join(lines))


def initial_state(run_dir: Path, contracts_loaded: int, source_target_fallback_count: int) -> dict[str, Any]:
    return {
        "phase": "running",
        "diagnostic_label": DIAGNOSTIC_LABEL,
        "cases_total": 25,
        "methods": METHODS,
        "runs_total": 75,
        "runs_completed": 0,
        "runs_failed": [],
        "formula_contracts_loaded": contracts_loaded,
        "source_target_fallback_count": source_target_fallback_count,
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
    scorer_contracts, visible_contracts = load_all_formula_contracts()
    required_by_case = load_required_facts()
    source_target_fallback_cases = fill_source_target_slots(cases, scorer_contracts, required_by_case)
    case_ids = [case["case_id"] for case in cases]
    missing_scorer = [cid for cid in case_ids if cid not in scorer_contracts]
    missing_visible = [cid for cid in case_ids if cid not in visible_contracts]
    if len(scorer_contracts) < 25 or len(visible_contracts) < 25 or missing_scorer or missing_visible:
        raise RuntimeError(f"Formula contract coverage failed: scorer={len(scorer_contracts)} visible={len(visible_contracts)} missing_scorer={missing_scorer} missing_visible={missing_visible}")
    zero_slots = [cid for cid in case_ids if len(scorer_contracts[cid].get("target_slots", [])) == 0]
    if zero_slots:
        raise RuntimeError(f"target_slot_count is zero for cases: {zero_slots}")

    run_dir = Path(args.run_dir).resolve() if args.run_dir else OUT_ROOT / f"round5_diagnostic_{ts()}"
    if args.resume and not args.run_dir and STATE_PATH.exists():
        existing = read_json(STATE_PATH)
        if existing.get("phase") == "running" and existing.get("run_dir"):
            run_dir = ROOT / str(existing["run_dir"]).rstrip("/")
    run_dir.mkdir(parents=True, exist_ok=True)

    state = initial_state(run_dir, 25, len(source_target_fallback_cases))
    if args.resume and STATE_PATH.exists():
        existing = read_json(STATE_PATH)
        if existing.get("run_dir") == rel(run_dir) + "/" and existing.get("phase") in {"running", "done"}:
            state.update(existing)
            state["phase"] = "running"
            state["completed_at"] = None
            state["codex_handoff_message"] = None
            state["source_target_fallback_count"] = len(source_target_fallback_cases)
    write_json(STATE_PATH, state)

    traces = read_jsonl(run_dir / "round5_traces.jsonl") if args.resume else []
    if args.resume:
        traces = [row for row in traces if row.get("failure_reason") != "provider_error"]
        write_jsonl(run_dir / "round5_traces.jsonl", traces)
    completed = {(row["case_id"], row["method"]) for row in traces}
    facts_cache = read_jsonl(run_dir / "neo4j_facts_cache.jsonl") if args.resume else []
    facts_by_case = {row["case_id"]: row["facts"] for row in facts_cache}
    rows_run = 0

    driver = create_driver()
    try:
        for case in cases:
            cid = case["case_id"]
            ticker = str(case["ticker"]).upper()
            if cid not in facts_by_case:
                facts_by_case[cid] = load_neo4j_graph_facts_filtered(
                    ticker=ticker,
                    case_id=cid,
                    years=case.get("years", []),
                    metric_tags=case.get("metric_tags", []),
                    driver=driver,
                )
                facts_cache.append({"case_id": cid, "ticker": ticker, "neo4j_facts_count": len(facts_by_case[cid]), "facts": facts_by_case[cid]})
                write_jsonl(run_dir / "neo4j_facts_cache.jsonl", facts_cache)

            for method in METHODS:
                if (cid, method) in completed:
                    continue
                if args.limit is not None and rows_run >= args.limit:
                    update_state(state, traces)
                    return
                attempt_idx = len(traces) + 1
                prompt_facts = [] if method == "vector_only_v5" else facts_by_case[cid]
                prompt = build_prompt(case, method, prompt_facts, visible_contracts[cid])
                base = {
                    "case_id": cid,
                    "ticker": ticker,
                    "split": case.get("split", ""),
                    "method": method,
                    "track": TRACK,
                    "neo4j_facts_count": len(facts_by_case[cid]),
                    "formula_type": scorer_contracts[cid].get("formula_type") or visible_contracts[cid].get("formula_type", ""),
                    "target_slot_count": len(scorer_contracts[cid].get("target_slots", [])),
                    "provider": "openai",
                    "model": MODEL,
                    "trace_id": f"local_trace_round5_{attempt_idx:04d}_{cid}__{method}",
                    "success": False,
                    "provider_success": False,
                    "error_type": "",
                    "error_message": "",
                }
                result = None
                raw = None
                usage = {}
                try:
                    result, usage, raw = call_openai(prompt)
                    base.update({"success": True, "provider_success": True})
                except r4.ProviderError as exc:
                    base.update({"error_type": exc.error_type, "error_message": str(exc)})
                except Exception as exc:  # noqa: BLE001
                    base.update({"error_type": "scoring_uncertain", "error_message": str(exc)[:300]})
                scores = score_result(method, result, required_by_case.get(cid, []), facts_by_case[cid], scorer_contracts[cid])
                base.update(scores)
                if base.get("error_type") in r4.PROVIDER_ERROR_TYPES:
                    base["failure_reason"] = "provider_error"
                row = {
                    **base,
                    "final_answer": result.final_answer if result else "",
                    "calculation": result.calculation if result else "",
                    "diagnostic_label": DIAGNOSTIC_LABEL,
                    "diagnostic_source_target_fallback": bool(scorer_contracts[cid].get("diagnostic_source_target_fallback")),
                    "test_split_formula_source": TEST_FORMULA_SOURCE if case.get("split") == "round3_test" else "pre_built",
                    "claim_boundary": CLAIM_BOUNDARY,
                    "prompt_sha256": r4.sha(prompt["system"] + "\n" + prompt["user"]),
                    "system_prompt": prompt["system"],
                    "user_prompt": prompt["user"],
                    "method_result": asdict(result) if result else None,
                    "raw_method_result_v5": raw,
                    "usage": usage,
                    "model_api_called": True,
                    "neo4j_write_performed": False,
                    "kg_patch_applied": False,
                }
                traces.append(row)
                completed.add((cid, method))
                rows_run += 1
                write_jsonl(run_dir / "round5_traces.jsonl", traces)
                write_jsonl(run_dir / "failure_analysis.jsonl", [r for r in traces if r.get("failure_reason") != "none"])
                update_state(state, traces)
                print(json.dumps({"runs_completed": len(traces), "runs_total": 75, "case_id": cid, "method": method, "failure_reason": row.get("failure_reason")}, ensure_ascii=False), flush=True)
                time.sleep(0.25)
    finally:
        driver.close()

    write_summary(run_dir, traces, facts_cache, len(source_target_fallback_cases))
    counts = [row["neo4j_facts_count"] for row in facts_cache]
    state["phase"] = "done"
    state["completed_at"] = utc_now()
    state["neo4j_avg_facts_filtered"] = avg(counts)
    state["codex_handoff_message"] = "Round 5 diagnostic complete. Check round5_summary.md for results."
    update_state(state, traces)
    print(json.dumps({"run_dir": rel(run_dir), "runs_completed": len(traces), "summary": rel(run_dir / "round5_summary.md"), "neo4j_avg_facts_filtered": state["neo4j_avg_facts_filtered"]}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
