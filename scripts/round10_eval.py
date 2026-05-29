from __future__ import annotations

import argparse
import json
import time
from collections import Counter
from dataclasses import asdict
from pathlib import Path
from typing import Any

import round10_common as c
from scorer_v9 import score_trace


def avg(values: list[Any]) -> float:
    nums = [float(value) for value in values]
    return round(sum(nums) / len(nums), 4) if nums else 0.0


def prompt_dir() -> Path:
    return c.ROOT / "outputs" / "round3_dual_track_eval_prep" / "prompt_formatter_v3_2"


def build_prompt(case: dict[str, Any], method: str, facts: list[dict[str, Any]], visible: dict[str, Any]) -> dict[str, str]:
    pdir = prompt_dir()
    system = (pdir / "prompt_v3_4_system.md").read_text(encoding="utf-8")
    answer_format = (pdir / "answer_format_spec_v3_2.md").read_text(encoding="utf-8")
    rounding = (pdir / "rounding_and_tolerance_rules_v3_2.md").read_text(encoding="utf-8")
    if method == "vector_only_v10":
        context = f"TEXT_CONTEXT\n{case['evidence_text']}"
    elif method == "graph_neo4j_v10":
        context = f"GRAPH_FACTS_TABLE\n{c.r8.r7.r6.r5.fact_table(facts)}"
    elif method == "hybrid_neo4j_v10":
        context = f"TEXT_CONTEXT\n{case['evidence_text']}\n\nGRAPH_FACTS_TABLE\n{c.r8.r7.r6.r5.fact_table(facts)}"
    else:
        raise RuntimeError(f"unknown method {method}")
    return {
        "system": system,
        "user": f"""QUESTION
{case['question']}

{context}

FORMULA_CONTRACT
{json.dumps(visible, ensure_ascii=False, indent=2, sort_keys=True)}

ANSWER_FORMAT
{answer_format}

ROUNDING_AND_TOLERANCE_RULES
{rounding}

Use temperature=0 behavior. Do not use outside knowledge.
""",
    }


def load_graph_facts(case: dict[str, Any], driver: Any, batch_id: str) -> list[dict[str, Any]]:
    env = c.r8.neo4j_env()
    with driver.session(database=env["NEO4J_DATABASE"]) as session:
        records = session.run(
            """
MATCH (obs:LLMObservation)-[:LLM_MENTIONS_COMPANY]->(co:LLMCompany {ticker: $ticker}),
      (obs)-[:LLM_OBSERVES_METRIC]->(m:LLMFinancialMetric),
      (obs)-[:LLM_OBSERVED_IN_YEAR]->(yr:LLMFiscalYear)
WHERE obs.kg_batch = $batch
  AND obs.case_id = $case_id
RETURN obs.obs_id AS obs_id,
       obs.value AS value,
       obs.unit AS unit,
       obs.evidence_quote AS evidence_quote,
       m.canonical_name AS metric_canonical,
       m.display_name AS metric_display,
       yr.year AS year
ORDER BY m.canonical_name, yr.year
""",
            ticker=case["ticker"],
            batch=batch_id,
            case_id=case["case_id"],
        )
        return [
            {
                "fact_id": rec["obs_id"],
                "metric_canonical": rec["metric_canonical"],
                "metric_raw": rec["metric_display"],
                "value": rec["value"],
                "year": rec["year"],
                "unit": rec["unit"] or "",
                "company": case["ticker"],
                "ticker": case["ticker"],
                "evidence_quote_exact": rec["evidence_quote"] or "",
                "source_fact": True,
                "derived_answer_value": False,
            }
            for rec in records
        ]


def required_fact_recall(method: str, scorer: dict[str, Any], facts: list[dict[str, Any]]) -> float:
    if method == "vector_only_v10":
        return 1.0
    return c.r8.r7.r6.required_fact_recall(scorer.get("source_fact_numbers", []), facts)


def update_state(state: dict[str, Any], rows: list[dict[str, Any]]) -> None:
    state["runs_completed"] = len(rows)
    state["runs_failed"] = [{"case_id": row["case_id"], "method": row["method"], "error_type": row.get("error_type", "")} for row in rows if row.get("failure_reason") == "provider_error"]
    state["test_ac_vector"] = avg([row["answer_correctness"] for row in rows if row["method"] == "vector_only_v10"])
    state["test_ac_graph"] = avg([row["answer_correctness"] for row in rows if row["method"] == "graph_neo4j_v10"])
    state["test_ac_hybrid"] = avg([row["answer_correctness"] for row in rows if row["method"] == "hybrid_neo4j_v10"])
    for dataset, key in [("FinDER", "finder"), ("FinQA", "finqa"), ("TAT-QA", "tatqa")]:
        state[f"test_ac_graph_{key}"] = avg([row["answer_correctness"] for row in rows if row["method"] == "graph_neo4j_v10" and row["source_dataset"] == dataset])
    state["graph_beats_vector_test"] = state["test_ac_graph"] > state["test_ac_vector"]
    finder_hybrid = avg([row["answer_correctness"] for row in rows if row["method"] == "hybrid_neo4j_v10" and row["source_dataset"] == "FinDER"])
    state["hybrid_beats_graph_finder"] = finder_hybrid > state.get("test_ac_graph_finder", 0.0)
    state["finqa_vector_beats_graph"] = avg([row["answer_correctness"] for row in rows if row["method"] == "vector_only_v10" and row["source_dataset"] == "FinQA"]) > state.get("test_ac_graph_finqa", 0.0)
    c.write_json(c.EVAL_STATE, state)


def write_summary(run_dir: Path, rows: list[dict[str, Any]]) -> None:
    state = c.read_json(c.EVAL_STATE)
    lines = [f"# Round 10 Summary", "", f"## Overall ({state['cases_total_actual']} cases)", "", "| Method | avg_ac | avg_nc | n_cases |", "|---|---:|---:|---:|"]
    for method in c.METHODS:
        selected = [row for row in rows if row["method"] == method]
        lines.append(f"| {method} | {avg([row['answer_correctness'] for row in selected])} | {avg([row['numerical_closeness'] for row in selected])} | {len(selected)} |")
    lines.extend(["", "## By Dataset", "", "| Dataset | Method | avg_ac | avg_nc | n_cases |", "|---|---|---:|---:|---:|"])
    for dataset in ["FinDER", "FinQA", "TAT-QA"]:
        for method in c.METHODS:
            selected = [row for row in rows if row["source_dataset"] == dataset and row["method"] == method]
            if selected:
                lines.append(f"| {dataset} | {method} | {avg([row['answer_correctness'] for row in selected])} | {avg([row['numerical_closeness'] for row in selected])} | {len(selected)} |")
    lines.extend(["", "## formula_type Distribution", "", "| formula_type | count | avg_ac (graph) |", "|---|---:|---:|"])
    for formula_type, count in sorted(Counter(row["formula_type"] for row in rows if row["method"] == "graph_neo4j_v10").items()):
        selected = [row for row in rows if row["method"] == "graph_neo4j_v10" and row["formula_type"] == formula_type]
        lines.append(f"| {formula_type} | {count} | {avg([row['answer_correctness'] for row in selected])} |")
    r8_state = c.read_json(c.ROOT / "outputs" / "round8_eval" / "state.json")
    r9_state = c.read_json(c.R9C_STATE)
    lines.extend(
        [
            "",
            "## R8 vs R9C vs R10 Graph AC Trend",
            "",
            "| Dataset | R8 | R9C | R10 |",
            "|---|---:|---:|---:|",
            f"| FinDER | {r8_state.get('test_ac_graph_finder', 0.0)} | {r9_state.get('test_ac_graph_finder', 0.0)} | {state.get('test_ac_graph_finder', 0.0)} |",
            f"| FinQA | {r8_state.get('test_ac_graph_finqa', 0.0)} | {r9_state.get('test_ac_graph_finqa', 0.0)} | {state.get('test_ac_graph_finqa', 0.0)} |",
            f"| Overall | {r8_state.get('test_ac_graph', 0.0)} | {r9_state.get('test_ac_graph', 0.0)} | {state.get('test_ac_graph', 0.0)} |",
            "",
            "## TAT-QA: Company-Ticker Extraction Coverage",
            f"- GPT extraction rate: {state.get('tatqa_ticker_extraction_rate', 0.0)}",
            f"- Cases used: {state.get('cases_tatqa', 0)}",
            "",
            "## Claim Limit",
            "",
            "Round 10 is a clean held-out benchmark across available FinDER, FinQA, and TAT-QA cases.",
            "- Do not treat TAT-QA ticker-mapped cases as representative of all TAT-QA.",
            "- Prior Round 8/Round 9C source records and tickers were excluded.",
            "- Prompt v3.4 includes the YoY calculation rule.",
        ]
    )
    c.write_text(run_dir / "round10_summary.md", "\n".join(lines))


def run_cases(cases: list[dict[str, Any]], trace_path: Path, facts_path: Path, batch_id: str, resume: bool) -> list[dict[str, Any]]:
    scorer, visible = c.load_contract_maps()
    traces = c.read_jsonl(trace_path) if resume else []
    traces = [row for row in traces if row.get("failure_reason") != "provider_error"]
    facts_cache = c.read_jsonl(facts_path) if resume else []
    facts_by_case = {row["case_id"]: row["facts"] for row in facts_cache}
    completed = {(row["case_id"], row["method"]) for row in traces}
    driver = c.r8.create_driver()
    try:
        for case in cases:
            cid = case["case_id"]
            if cid not in facts_by_case:
                last_error: BaseException | None = None
                for attempt in range(1, 4):
                    try:
                        facts_by_case[cid] = load_graph_facts(case, driver, batch_id)
                        last_error = None
                        break
                    except Exception as exc:  # noqa: BLE001
                        last_error = exc
                        try:
                            driver.close()
                        except Exception:
                            pass
                        time.sleep(2 * attempt)
                        driver = c.r8.create_driver()
                if last_error is not None:
                    raise RuntimeError(f"Neo4j fact load failed after retries for {cid}: {last_error}") from last_error
                facts_cache.append({"case_id": cid, "ticker": case["ticker"], "neo4j_facts_count": len(facts_by_case[cid]), "facts": facts_by_case[cid]})
                c.write_jsonl(facts_path, facts_cache)
            for method in c.METHODS:
                if (cid, method) in completed:
                    continue
                facts = [] if method == "vector_only_v10" else facts_by_case[cid]
                prompt = build_prompt(case, method, facts, visible[cid])
                result = None
                raw = None
                usage: dict[str, Any] = {}
                base = {
                    "trace_id": f"local_trace_round10_{len(traces)+1:04d}_{cid}__{method}",
                    "case_id": cid,
                    "ticker": case["ticker"],
                    "split": "round10_test",
                    "source_dataset": case["source_dataset"],
                    "method": method,
                    "round": c.ROUND,
                    "kg_batch": "N/A" if method == "vector_only_v10" else batch_id,
                    "prompt_version": c.PROMPT_VERSION,
                    "scoring_version": c.SCORING_VERSION,
                    "scorer_version": c.SCORING_VERSION,
                    "claim_boundary": c.CLAIM_BOUNDARY,
                    "formula_type": scorer[cid].get("formula_type", ""),
                    "neo4j_facts_count": 0 if method == "vector_only_v10" else len(facts_by_case[cid]),
                    "target_slot_count": len(scorer[cid].get("target_slots", [])),
                    "provider": "openai",
                    "model": c.MODEL,
                    "success": False,
                    "provider_success": False,
                    "error_type": "",
                    "error_message": "",
                    "required_fact_recall": required_fact_recall(method, scorer[cid], facts_by_case[cid]),
                }
                try:
                    result, usage, raw = c.r8.r7.r6.r5.call_openai(prompt)
                    base.update({"success": True, "provider_success": True})
                except c.r8.r7.r6.r5.r4.ProviderError as exc:
                    base.update({"error_type": exc.error_type, "error_message": str(exc)})
                except Exception as exc:  # noqa: BLE001
                    base.update({"error_type": "scoring_uncertain", "error_message": str(exc)[:300]})
                row_pre_score = {
                    **base,
                    "final_answer": result.final_answer if result else "",
                    "calculation": result.calculation if result else "",
                    "prompt_sha256": c.sha(prompt["system"] + "\n" + prompt["user"]),
                    "method_result": asdict(result) if result else None,
                    "raw_method_result_v10": raw,
                    "usage": usage,
                    "model_api_called": True,
                    "neo4j_write_performed": False,
                }
                row = score_trace(row_pre_score, scorer[cid], method)
                if row.get("error_type") in c.r8.r7.r6.r5.r4.PROVIDER_ERROR_TYPES:
                    row["failure_reason"] = "provider_error"
                    row["answer_correctness"] = 0.0
                traces.append(row)
                completed.add((cid, method))
                c.write_jsonl(trace_path, traces)
                state = c.read_json(c.EVAL_STATE)
                update_state(state, traces)
                print(json.dumps({"runs_completed": len(traces), "case_id": cid, "method": method, "failure_reason": row["failure_reason"]}, ensure_ascii=False), flush=True)
                time.sleep(0.2)
    finally:
        driver.close()
    return traces


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-dir", default="")
    parser.add_argument("--resume", action=argparse.BooleanOptionalAction, default=True)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    c.assert_round9c_done()
    kg_state = c.read_json(c.KG_DIR / "state.json")
    if kg_state.get("phase") != "E_done":
        raise RuntimeError("Round10 KG state is not E_done")
    batch_id = kg_state["batch_id"]
    gen_state = c.read_json(c.GEN_STATE)
    selection_state = c.read_json(c.SELECTION_STATE)
    scorer, _visible = c.load_contract_maps()
    cases = [case for case in c.load_all_round10_cases() if case["case_id"] in scorer]
    if len(cases) < 200:
        raise RuntimeError(f"Round10 cases below 200: {len(cases)}")
    run_dir = Path(args.run_dir).resolve() if args.run_dir else c.OUT_ROOT / f"round10_eval_{c.ts()}"
    run_dir.mkdir(parents=True, exist_ok=True)
    trace_path = run_dir / "round10_traces.jsonl"
    facts_path = run_dir / "neo4j_facts_cache.jsonl"
    state = {
        "round": c.ROUND,
        "phase": "running",
        "kg_batch": batch_id,
        "prompt_version": c.PROMPT_VERSION,
        "scoring_version": c.SCORING_VERSION,
        "claim_boundary": c.CLAIM_BOUNDARY,
        "cases_target": 300,
        "cases_total_actual": len(cases),
        "cases_finder": len([case for case in cases if case["source_dataset"] == "FinDER"]),
        "cases_finqa": len([case for case in cases if case["source_dataset"] == "FinQA"]),
        "cases_tatqa": len([case for case in cases if case["source_dataset"] == "TAT-QA"]),
        "tatqa_ticker_extraction_rate": selection_state.get("tatqa_ticker_extraction_rate", 0.0),
        "formula_type_other_pct_finder": gen_state.get("formula_type_other_pct_finder", 0.0),
        "runs_total": len(cases) * 3,
        "runs_completed": 0,
        "runs_failed": [],
        "yoy_fix_applied": True,
        "eps_dilution_excluded": True,
        "run_dir": c.rel(run_dir) + "/",
        "started_at": c.utc_now(),
        "completed_at": None,
    }
    c.write_json(c.EVAL_STATE, state)
    rows = run_cases(cases, trace_path, facts_path, batch_id, args.resume)
    state = c.read_json(c.EVAL_STATE)
    update_state(state, rows)
    state = c.read_json(c.EVAL_STATE)
    state["phase"] = "done"
    state["completed_at"] = c.utc_now()
    c.write_json(c.EVAL_STATE, state)
    write_summary(run_dir, rows)
    print(json.dumps({"run_dir": c.rel(run_dir), "runs_completed": len(rows), "summary": c.rel(run_dir / "round10_summary.md"), "graph_beats_vector_test": state["graph_beats_vector_test"]}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
