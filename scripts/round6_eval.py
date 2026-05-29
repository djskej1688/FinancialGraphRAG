"""Run Round 06 evaluation against the Step B targeted KG batch."""

from __future__ import annotations

import argparse
import importlib.util
import json
import math
import sys
import time
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
ROUND5_PATH = ROOT / "scripts" / "round5_diagnostic_eval.py"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

spec = importlib.util.spec_from_file_location("round5_diagnostic_eval", ROUND5_PATH)
r5 = importlib.util.module_from_spec(spec)
assert spec and spec.loader
spec.loader.exec_module(r5)


METHODS = ["vector_only_v6", "graph_neo4j_v6", "hybrid_neo4j_v6"]
MODEL = "gpt-4o-mini"
ROUND = "round6"
TRACK = "track_b_neo4j_llm_ie"
KG_BATCH = "kg-targeted-ie-v1-20260528"
STEP_B_BATCH = KG_BATCH
TEST_FORMULA_SOURCE = "post_hoc"
CLAIM_BOUNDARY = "method_comparison_valid_absolute_scores_diagnostic"

OUT_ROOT = ROOT / "outputs" / "round3_eval_runs"
STATE_PATH = ROOT / "outputs" / "round6_eval" / "state.json"
STEP_B_STATE = ROOT / "outputs" / "step_b_targeted_kg" / "state.json"
ROUND5_RUN = OUT_ROOT / "round5_diagnostic_20260528_213524"

TEST_NOTES = {
    "round3_test_004": "employees metric ambiguous; Step B extracted total workforce 11311 vs expected female% 23",
    "round3_test_007": "values fixed",
    "round3_test_009": "royalty revenue mismatch remains; total revenue plus cost_of_sales may still support gross margin",
    "round3_test_011": "all 6 facts match",
    "round3_test_012": "all 10 facts match",
    "round3_test_013": "revenues=2681.4 fixed",
    "round3_test_014": "all 9 facts match",
    "round3_test_016": "all 6 facts match",
    "round3_test_017": "all 6 facts match",
    "round3_test_018": "all 7 facts match",
}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def ts() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S")


def rel(path: Path) -> str:
    return path.resolve().relative_to(ROOT.resolve()).as_posix()


def read_json(path: Path) -> Any:
    return r5.read_json(path)


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    return r5.read_jsonl(path)


def write_json(path: Path, data: Any) -> None:
    r5.write_json(path, data)


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    r5.write_jsonl(path, rows)


def write_text(path: Path, text: str) -> None:
    r5.write_text(path, text)


def avg(values: list[Any]) -> float:
    nums = [float(value) for value in values]
    return round(sum(nums) / len(nums), 4) if nums else 0.0


def build_prompt(case: dict[str, Any], method: str, facts: list[dict[str, Any]], model_visible_contract: dict[str, Any]) -> dict[str, str]:
    pdir = r5.prompt_dir()
    system = (pdir / "prompt_v3_2_system.md").read_text(encoding="utf-8")
    answer_format = (pdir / "answer_format_spec_v3_2.md").read_text(encoding="utf-8")
    rounding = (pdir / "rounding_and_tolerance_rules_v3_2.md").read_text(encoding="utf-8")
    evidence = str(case.get("evidence_text", ""))
    if method == "vector_only_v6":
        context = f"TEXT_CONTEXT\n{evidence}"
    elif method == "graph_neo4j_v6":
        context = f"GRAPH_FACTS_TABLE\n{r5.fact_table(facts)}"
    elif method == "hybrid_neo4j_v6":
        context = f"TEXT_CONTEXT\n{evidence}\n\nGRAPH_FACTS_TABLE\n{r5.fact_table(facts)}"
    else:
        raise RuntimeError(f"unknown method: {method}")
    user = f"""QUESTION
{case['question']}

{context}
{r5.formula_section(model_visible_contract)}

ANSWER_FORMAT
{answer_format}

ROUNDING_AND_TOLERANCE_RULES
{rounding}

Use temperature=0 behavior. Do not use outside knowledge. Do not mention hidden expected answers or scorer-only target slots.
"""
    return {"system": system, "user": user}


def load_neo4j_graph_facts_filtered(ticker: str, case_id: str, years: list[int], driver: Any) -> list[dict[str, Any]]:
    database = r5.r4.neo4j_env()["NEO4J_DATABASE"]
    years = [int(year) for year in years if str(year).isdigit()]
    last_error: Exception | None = None
    for attempt in range(3):
        active_driver = driver if attempt == 0 else r5.create_driver()
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
       obs.validation_status AS validation_status,
       obs.source_fact_id AS source_fact_id,
       m.canonical_name AS metric_canonical,
       m.display_name AS metric_display,
       yr.year AS year
ORDER BY yr.year, m.canonical_name
""",
                    ticker=ticker,
                    batch=KG_BATCH,
                    years=years,
                )
                return [
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
                        "validation_status": rec["validation_status"] or "",
                        "source_fact_id": rec["source_fact_id"] or "",
                        "fact_role": "component",
                        "source_fact": True,
                        "derived_answer_value": False,
                    }
                    for rec in records
                ]
        except Exception as exc:  # noqa: BLE001
            last_error = exc
            time.sleep(2 * (attempt + 1))
        finally:
            if attempt > 0:
                active_driver.close()
    raise RuntimeError(f"Neo4j read failed after retries for {ticker}/{case_id}: {last_error}") from last_error


def required_fact_recall(required_facts: list[dict[str, Any]], neo4j_facts: list[dict[str, Any]]) -> float:
    return r5.required_fact_recall(required_facts, neo4j_facts)


def required_facts_for_case(case_id: str, scorer_contract: dict[str, Any], fallback_required: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return scorer_contract.get("source_fact_numbers") or fallback_required


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
    actual = r5.r4.extract_numbers(output)
    slots = scorer_contract.get("target_slots", [])
    matched: list[str] = []
    missing: list[str] = []
    closeness_scores: list[float] = []
    for slot in slots:
        expected = r5.r4.parse_number(str(slot["expected_value"]))
        closeness_scores.append(r5.slot_numerical_closeness(expected, actual))
        if expected and any(r5.r4.close(expected, candidate, slot.get("unit", "")) for candidate in actual):
            matched.append(slot["target_slot_name"])
        else:
            missing.append(slot["target_slot_name"])
    target_recall = round(len(matched) / len(slots), 4) if slots else 0.0
    numerical_closeness = round(sum(closeness_scores) / len(closeness_scores), 4) if closeness_scores else 0.0
    numeric_ok = target_recall >= 0.8 if slots else False
    rfr = 1.0 if method == "vector_only_v6" else required_fact_recall(required_facts, neo4j_facts)
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


def summarize(rows: list[dict[str, Any]], split_filter: set[str] | None = None, clean_only: bool = False) -> list[dict[str, Any]]:
    selected = [row for row in rows if split_filter is None or row["split"] in split_filter]
    if clean_only:
        selected = [row for row in selected if not row.get("diagnostic_source_target_fallback")]
    out: list[dict[str, Any]] = []
    for method in METHODS:
        items = [row for row in selected if row["method"] == method]
        out.append(
            {
                "method": method,
                "avg_ac": avg([row["answer_correctness"] for row in items]),
                "avg_nc": avg([row["numerical_closeness"] for row in items]),
                "avg_rfr": avg([row["required_fact_recall"] for row in items]),
                "avg_facts": avg([row["neo4j_facts_count"] for row in items]),
            }
        )
    return out


def r5_rows() -> list[dict[str, Any]]:
    return read_jsonl(ROUND5_RUN / "round5_traces.jsonl")


def r5_test_summary() -> dict[str, dict[str, float]]:
    rows = [row for row in r5_rows() if row.get("split") == "round3_test"]
    out: dict[str, dict[str, float]] = {}
    for base, method in [("vector_only", "vector_only_v5"), ("graph_neo4j", "graph_neo4j_v5"), ("hybrid_neo4j", "hybrid_neo4j_v5")]:
        items = [row for row in rows if row.get("method") == method]
        out[base] = {
            "avg_ac": avg([row["answer_correctness"] for row in items]),
            "avg_nc": avg([row["numerical_closeness"] for row in items]),
        }
    return out


def test_case_breakdown(rows: list[dict[str, Any]]) -> list[str]:
    r5_graph = {row["case_id"]: row for row in r5_rows() if row.get("split") == "round3_test" and row.get("method") == "graph_neo4j_v5"}
    r6_graph = {row["case_id"]: row for row in rows if row.get("split") == "round3_test" and row.get("method") == "graph_neo4j_v6"}
    lines = ["| case_id | ticker | formula_type | R5_graph_rfr | R6_graph_rfr | R5_graph_ac | R6_graph_ac | delta | notes |", "|---|---|---|---:|---:|---:|---:|---:|---|"]
    for cid in sorted(r6_graph):
        before = r5_graph.get(cid, {})
        after = r6_graph[cid]
        prefix = "_".join(cid.split("_")[:3])
        note = TEST_NOTES.get(prefix, "")
        delta = round(float(after.get("answer_correctness", 0.0)) - float(before.get("answer_correctness", 0.0)), 4)
        lines.append(
            f"| {cid} | {after['ticker']} | {after['formula_type']} | {before.get('required_fact_recall', 0.0)} | "
            f"{after.get('required_fact_recall', 0.0)} | {before.get('answer_correctness', 0.0)} | "
            f"{after.get('answer_correctness', 0.0)} | {delta} | {note} |"
        )
    return lines


def write_summary(run_dir: Path, rows: list[dict[str, Any]], facts_cache: list[dict[str, Any]]) -> None:
    overall = summarize(rows)
    dev = summarize(rows, {"round3_dev", "baseline_control"})
    clean_dev = summarize(rows, {"round3_dev", "baseline_control"}, clean_only=True)
    test = summarize(rows, {"round3_test"})
    r5_test = r5_test_summary()
    r6_test = {row["method"].replace("_v6", ""): row for row in test}
    test_ac_vector = r6_test["vector_only"]["avg_ac"]
    test_ac_graph = r6_test["graph_neo4j"]["avg_ac"]
    diagnostic = "YES" if test_ac_graph > test_ac_vector else ("SAME" if math.isclose(test_ac_graph, test_ac_vector) else "NO")
    counts = [int(row["neo4j_facts_count"]) for row in facts_cache]
    lines = [
        "# Round 06 Evaluation",
        "",
        f"**KG batch:** {KG_BATCH}",
        "**avg_nc means numerical_closeness; numeric_correctness remains binary in traces.**",
        "",
        "## Overall (25 cases)",
        "",
        "| Method | avg_ac | avg_nc | avg_rfr | avg_facts |",
        "|---|---:|---:|---:|---:|",
    ]
    lines.extend(f"| {row['method']} | {row['avg_ac']} | {row['avg_nc']} | {row['avg_rfr']} | {row['avg_facts']} |" for row in overall)
    lines.extend(["", "## Dev/Baseline (15 cases, diagnostic fallback included)", "", "| Method | avg_ac | avg_nc | avg_rfr |", "|---|---:|---:|---:|"])
    lines.extend(f"| {row['method']} | {row['avg_ac']} | {row['avg_nc']} | {row['avg_rfr']} |" for row in dev)
    lines.extend(["", "## Clean Dev/Baseline (9 cases, fallback excluded)", "", "| Method | avg_ac | avg_nc | avg_rfr |", "|---|---:|---:|---:|"])
    lines.extend(f"| {row['method']} | {row['avg_ac']} | {row['avg_nc']} | {row['avg_rfr']} |" for row in clean_dev)
    lines.extend(["", "## Test split (10 cases) - primary comparison", "", "| Method | avg_ac | avg_nc | avg_rfr |", "|---|---:|---:|---:|"])
    lines.extend(f"| {row['method']} | {row['avg_ac']} | {row['avg_nc']} | {row['avg_rfr']} |" for row in test)
    lines.extend(["", "## Round 5 to Round 6 delta (test split)", "", "| Method | R5 avg_ac | R6 avg_ac | delta_ac | R5 avg_nc | R6 avg_nc | delta_nc |", "|---|---:|---:|---:|---:|---:|---:|"])
    for method in ["vector_only", "graph_neo4j", "hybrid_neo4j"]:
        before = r5_test[method]
        after = r6_test[method]
        lines.append(
            f"| {method} | {before['avg_ac']} | {after['avg_ac']} | {round(after['avg_ac'] - before['avg_ac'], 4)} | "
            f"{before['avg_nc']} | {after['avg_nc']} | {round(after['avg_nc'] - before['avg_nc'], 4)} |"
        )
    lines.extend(["", "## Per-case test split breakdown", ""])
    lines.extend(test_case_breakdown(rows))
    lines.extend(
        [
            "",
            "## Neo4j Facts Count",
            "",
            f"- Average facts per case: {avg(counts)}",
            f"- Min facts per case: {min(counts) if counts else 0}",
            f"- Max facts per case: {max(counts) if counts else 0}",
            "",
            "## Key diagnostic question",
            "",
            f"Does graph_neo4j_v6 test avg_ac exceed vector_only_v6 test avg_ac? **{diagnostic}**",
            "",
            "## Notes",
            "",
            "- XEL is expected to remain difficult: `employees` in the contract means female workforce percentage 23, but the targeted KG contains total workforce 11311 under the ambiguous metric name.",
            "- AMGN still has royalty revenue mismatches, but gross margin may remain computable from total revenue plus cost_of_sales.",
            "- KR, LND, MSFT, FOXA, BW, and CARR are diagnostic fallback cases and are excluded from the clean dev/baseline section.",
        ]
    )
    write_text(run_dir / "round6_summary.md", "\n".join(lines))


def initial_state(run_dir: Path, step_b_valid: bool) -> dict[str, Any]:
    return {
        "phase": "running",
        "round": ROUND,
        "kg_batch": KG_BATCH,
        "step_b_batch_validated": step_b_valid,
        "cases_total": 25,
        "runs_total": 75,
        "runs_completed": 0,
        "runs_failed": [],
        "methods": METHODS,
        "run_dir": rel(run_dir) + "/",
        "test_ac_vector": 0.0,
        "test_ac_graph": 0.0,
        "test_ac_hybrid": 0.0,
        "graph_beats_vector_test": False,
        "started_at": utc_now(),
        "completed_at": None,
        "codex_handoff_message": None,
    }


def update_state(state: dict[str, Any], rows: list[dict[str, Any]]) -> None:
    state["runs_completed"] = len(rows)
    state["runs_failed"] = [
        {"case_id": row["case_id"], "method": row["method"], "failure_reason": row.get("failure_reason"), "error_type": row.get("error_type", "")}
        for row in rows
        if row.get("failure_reason") == "provider_error"
    ]
    test_rows = [row for row in rows if row.get("split") == "round3_test"]
    for method, field in [("vector_only_v6", "test_ac_vector"), ("graph_neo4j_v6", "test_ac_graph"), ("hybrid_neo4j_v6", "test_ac_hybrid")]:
        state[field] = avg([row["answer_correctness"] for row in test_rows if row["method"] == method])
    state["graph_beats_vector_test"] = state["test_ac_graph"] > state["test_ac_vector"]
    write_json(STATE_PATH, state)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-dir", default="")
    parser.add_argument("--resume", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--limit", type=int, default=None)
    return parser.parse_args()


def verify_step_b(driver: Any) -> bool:
    if not STEP_B_STATE.exists():
        raise RuntimeError("Step B state missing")
    state = read_json(STEP_B_STATE)
    if state.get("phase") != "done" or state.get("batch_id") != KG_BATCH:
        raise RuntimeError(f"Step B precondition failed: phase={state.get('phase')} batch={state.get('batch_id')}")
    database = r5.r4.neo4j_env()["NEO4J_DATABASE"]
    with driver.session(database=database) as session:
        count = session.run("MATCH (obs:LLMObservation {kg_batch: $batch}) RETURN count(obs) AS n", batch=KG_BATCH).single()["n"]
    if int(count) <= 0:
        raise RuntimeError(f"No Neo4j observations found for {KG_BATCH}")
    return int(count) == int(state.get("neo4j_observations_in_batch", count))


def main() -> None:
    args = parse_args()
    cases = r5.load_cases()
    scorer_contracts, visible_contracts = r5.load_all_formula_contracts()
    required_by_case = r5.load_required_facts()
    source_target_fallback_cases = set(r5.fill_source_target_slots(cases, scorer_contracts, required_by_case))
    case_ids = [case["case_id"] for case in cases]
    missing = [cid for cid in case_ids if cid not in scorer_contracts or cid not in visible_contracts]
    zero_slots = [cid for cid in case_ids if len(scorer_contracts[cid].get("target_slots", [])) == 0]
    if missing or zero_slots:
        raise RuntimeError(f"Contract coverage failed: missing={missing} zero_slots={zero_slots}")

    run_dir = Path(args.run_dir).resolve() if args.run_dir else OUT_ROOT / f"round6_eval_{ts()}"
    if args.resume and not args.run_dir and STATE_PATH.exists():
        existing = read_json(STATE_PATH)
        if existing.get("phase") == "running" and existing.get("run_dir"):
            run_dir = ROOT / str(existing["run_dir"]).rstrip("/")
    run_dir.mkdir(parents=True, exist_ok=True)

    driver = r5.create_driver()
    try:
        step_b_valid = verify_step_b(driver)
        state = initial_state(run_dir, step_b_valid)
        if args.resume and STATE_PATH.exists():
            existing = read_json(STATE_PATH)
            if existing.get("run_dir") == rel(run_dir) + "/" and existing.get("phase") in {"running", "done"}:
                state.update(existing)
                state["phase"] = "running"
                state["completed_at"] = None
                state["codex_handoff_message"] = None
        write_json(STATE_PATH, state)

        traces = read_jsonl(run_dir / "round6_traces.jsonl") if args.resume else []
        if args.resume:
            traces = [row for row in traces if row.get("failure_reason") != "provider_error"]
            write_jsonl(run_dir / "round6_traces.jsonl", traces)
        completed = {(row["case_id"], row["method"]) for row in traces}
        facts_cache = read_jsonl(run_dir / "neo4j_facts_cache.jsonl") if args.resume else []
        facts_by_case = {row["case_id"]: row["facts"] for row in facts_cache}
        rows_run = 0

        for case in cases:
            cid = case["case_id"]
            ticker = str(case["ticker"]).upper()
            if cid not in facts_by_case:
                facts_by_case[cid] = load_neo4j_graph_facts_filtered(ticker, cid, case.get("years", []), driver)
                facts_cache.append({"case_id": cid, "ticker": ticker, "neo4j_facts_count": len(facts_by_case[cid]), "facts": facts_by_case[cid]})
                write_jsonl(run_dir / "neo4j_facts_cache.jsonl", facts_cache)

            for method in METHODS:
                if (cid, method) in completed:
                    continue
                if args.limit is not None and rows_run >= args.limit:
                    update_state(state, traces)
                    return
                attempt_idx = len(traces) + 1
                prompt_facts = [] if method == "vector_only_v6" else facts_by_case[cid]
                prompt = build_prompt(case, method, prompt_facts, visible_contracts[cid])
                result = None
                raw = None
                usage: dict[str, Any] = {}
                base = {
                    "trace_id": f"local_trace_round6_{attempt_idx:04d}_{cid}__{method}",
                    "case_id": cid,
                    "ticker": ticker,
                    "split": case.get("split", ""),
                    "method": method,
                    "round": ROUND,
                    "track": TRACK,
                    "kg_batch": "N/A" if method == "vector_only_v6" else KG_BATCH,
                    "step_b_batch": STEP_B_BATCH,
                    "formula_type": scorer_contracts[cid].get("formula_type") or visible_contracts[cid].get("formula_type", ""),
                    "diagnostic_source_target_fallback": cid in source_target_fallback_cases,
                    "test_split_formula_source": TEST_FORMULA_SOURCE if case.get("split") == "round3_test" else "pre_built",
                    "neo4j_facts_count": 0 if method == "vector_only_v6" else len(facts_by_case[cid]),
                    "provider": "openai",
                    "model": MODEL,
                    "target_slot_count": len(scorer_contracts[cid].get("target_slots", [])),
                    "success": False,
                    "provider_success": False,
                    "error_type": "",
                    "error_message": "",
                }
                try:
                    result, usage, raw = r5.call_openai(prompt)
                    base.update({"success": True, "provider_success": True})
                except r5.r4.ProviderError as exc:
                    base.update({"error_type": exc.error_type, "error_message": str(exc)})
                except Exception as exc:  # noqa: BLE001
                    base.update({"error_type": "scoring_uncertain", "error_message": str(exc)[:300]})
                required_facts = required_facts_for_case(cid, scorer_contracts[cid], required_by_case.get(cid, []))
                scores = score_result(method, result, required_facts, facts_by_case[cid], scorer_contracts[cid])
                base.update(scores)
                if base.get("error_type") in r5.r4.PROVIDER_ERROR_TYPES:
                    base["failure_reason"] = "provider_error"
                row = {
                    **base,
                    "claim_boundary": CLAIM_BOUNDARY,
                    "final_answer": result.final_answer if result else "",
                    "calculation": result.calculation if result else "",
                    "prompt_sha256": r5.r4.sha(prompt["system"] + "\n" + prompt["user"]),
                    "system_prompt": prompt["system"],
                    "user_prompt": prompt["user"],
                    "method_result": asdict(result) if result else None,
                    "raw_method_result_v6": raw,
                    "usage": usage,
                    "model_api_called": True,
                    "neo4j_write_performed": False,
                    "kg_patch_applied": False,
                }
                traces.append(row)
                completed.add((cid, method))
                rows_run += 1
                write_jsonl(run_dir / "round6_traces.jsonl", traces)
                write_jsonl(run_dir / "failure_analysis.jsonl", [r for r in traces if r.get("failure_reason") != "none"])
                update_state(state, traces)
                print(json.dumps({"runs_completed": len(traces), "runs_total": 75, "case_id": cid, "method": method, "failure_reason": row.get("failure_reason")}, ensure_ascii=False), flush=True)
                time.sleep(0.25)
    finally:
        driver.close()

    write_summary(run_dir, traces, facts_cache)
    state["phase"] = "done"
    state["completed_at"] = utc_now()
    state["codex_handoff_message"] = "Round 6 complete. Check round6_summary.md. Next: Step A if graph > vector; otherwise diagnose remaining failures."
    update_state(state, traces)
    print(json.dumps({"run_dir": rel(run_dir), "runs_completed": len(traces), "summary": rel(run_dir / "round6_summary.md"), "graph_beats_vector_test": state["graph_beats_vector_test"]}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
