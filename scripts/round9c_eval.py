from __future__ import annotations

import argparse
import json
import time
from collections import Counter
from dataclasses import asdict
from pathlib import Path
from typing import Any

import round9c_common as c
from scorer_v9 import score_trace


def avg(values: list[Any]) -> float:
    nums = [float(value) for value in values]
    return round(sum(nums) / len(nums), 4) if nums else 0.0


def prompt_dir() -> Path:
    return c.ROOT / "outputs" / "round3_dual_track_eval_prep" / "prompt_formatter_v3_2"


def get_system_prompt(method: str) -> str:
    pdir = prompt_dir()
    if method.startswith("hybrid"):
        return (pdir / "prompt_v3_3_kgfirst.md").read_text(encoding="utf-8")
    return (pdir / "prompt_v3_3_system.md").read_text(encoding="utf-8")


def prompt_version(method: str) -> str:
    return c.KGFIRST_PROMPT_VERSION if method.startswith("hybrid") else c.PROMPT_VERSION


def build_prompt(case: dict[str, Any], method: str, facts: list[dict[str, Any]], visible: dict[str, Any]) -> dict[str, str]:
    pdir = prompt_dir()
    answer_format = (pdir / "answer_format_spec_v3_2.md").read_text(encoding="utf-8")
    rounding = (pdir / "rounding_and_tolerance_rules_v3_2.md").read_text(encoding="utf-8")
    if method == "vector_only_v9":
        context = f"TEXT_CONTEXT\n{case['evidence_text']}"
    elif method == "graph_neo4j_v9":
        context = f"GRAPH_FACTS_TABLE\n{c.r8.r7.r6.r5.fact_table(facts)}"
    elif method == "hybrid_neo4j_v9":
        context = f"TEXT_CONTEXT\n{case['evidence_text']}\n\nGRAPH_FACTS_TABLE\n{c.r8.r7.r6.r5.fact_table(facts)}"
    else:
        raise RuntimeError(f"unknown method {method}")
    formula_text = json.dumps(visible, ensure_ascii=False, indent=2, sort_keys=True)
    user = f"""QUESTION
{case['question']}

{context}

FORMULA_CONTRACT
{formula_text}

ANSWER_FORMAT
{answer_format}

ROUNDING_AND_TOLERANCE_RULES
{rounding}

Use temperature=0 behavior. Do not use outside knowledge.
"""
    return {"system": get_system_prompt(method), "user": user}


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
    if method == "vector_only_v9":
        return 1.0
    return c.r8.r7.r6.required_fact_recall(scorer.get("source_fact_numbers", []), facts)


def update_state(state: dict[str, Any], rows: list[dict[str, Any]]) -> None:
    state["runs_completed"] = len(rows)
    state["runs_failed"] = [{"case_id": row["case_id"], "method": row["method"], "error_type": row.get("error_type", "")} for row in rows if row.get("failure_reason") == "provider_error"]
    state["test_ac_vector"] = avg([row["answer_correctness"] for row in rows if row["method"] == "vector_only_v9"])
    state["test_ac_graph"] = avg([row["answer_correctness"] for row in rows if row["method"] == "graph_neo4j_v9"])
    state["test_ac_hybrid"] = avg([row["answer_correctness"] for row in rows if row["method"] == "hybrid_neo4j_v9"])
    state["test_ac_graph_finder"] = avg([row["answer_correctness"] for row in rows if row["method"] == "graph_neo4j_v9" and row["source_dataset"] == "FinDER"])
    state["test_ac_graph_finqa"] = avg([row["answer_correctness"] for row in rows if row["method"] == "graph_neo4j_v9" and row["source_dataset"] == "FinQA"])
    state["graph_beats_vector_test"] = state["test_ac_graph"] > state["test_ac_vector"]
    finder_hybrid = avg([row["answer_correctness"] for row in rows if row["method"] == "hybrid_neo4j_v9" and row["source_dataset"] == "FinDER"])
    finder_graph = state["test_ac_graph_finder"]
    state["hybrid_beats_graph_finder"] = finder_hybrid > finder_graph
    c.write_json(c.EVAL_STATE, state)


def write_summary(run_dir: Path, rows: list[dict[str, Any]], formula_other_pct: float) -> None:
    lines = ["# Round 09C Summary", "", "## Overall (all cases)", "", "| Method | avg_ac | avg_nc | avg_rfr | n_cases |", "|---|---:|---:|---:|---:|"]
    for method in c.METHODS:
        selected = [row for row in rows if row["method"] == method]
        lines.append(f"| {method} | {avg([row['answer_correctness'] for row in selected])} | {avg([row['numerical_closeness'] for row in selected])} | {avg([row['required_fact_recall'] for row in selected])} | {len(selected)} |")
    lines.extend(["", "## By Dataset", "", "| Dataset | Method | avg_ac | avg_nc | n_cases |", "|---|---|---:|---:|---:|"])
    for dataset in ["FinDER", "FinQA"]:
        for method in c.METHODS:
            selected = [row for row in rows if row["source_dataset"] == dataset and row["method"] == method]
            lines.append(f"| {dataset} | {method} | {avg([row['answer_correctness'] for row in selected])} | {avg([row['numerical_closeness'] for row in selected])} | {len(selected)} |")

    lines.extend(["", "## Formula Type Distribution", "", "| formula_type | count | graph_avg_ac |", "|---|---:|---:|"])
    case_ftypes = {row["case_id"]: row["formula_type"] for row in rows if row["method"] == "graph_neo4j_v9"}
    for formula_type, count in sorted(Counter(case_ftypes.values()).items()):
        selected = [row for row in rows if row["method"] == "graph_neo4j_v9" and row["formula_type"] == formula_type]
        lines.append(f"| {formula_type} | {count} | {avg([row['answer_correctness'] for row in selected])} |")

    r8_state = c.read_json(c.ROOT / "outputs" / "round8_eval" / "state.json")
    r9_state = c.read_json(c.EVAL_STATE)
    lines.extend(
        [
            "",
            "## R8 vs R9C Comparison (graph method)",
            "",
            "| Dataset | R8 graph ac | R9C graph ac | delta |",
            "|---|---:|---:|---:|",
            f"| FinDER | {r8_state.get('test_ac_graph_finder', 0.0)} | {r9_state.get('test_ac_graph_finder', 0.0)} | {round(r9_state.get('test_ac_graph_finder', 0.0) - r8_state.get('test_ac_graph_finder', 0.0), 4)} |",
            f"| FinQA | {r8_state.get('test_ac_graph_finqa', 0.0)} | {r9_state.get('test_ac_graph_finqa', 0.0)} | {round(r9_state.get('test_ac_graph_finqa', 0.0) - r8_state.get('test_ac_graph_finqa', 0.0), 4)} |",
            f"| Overall | {r8_state.get('test_ac_graph', 0.0)} | {r9_state.get('test_ac_graph', 0.0)} | {round(r9_state.get('test_ac_graph', 0.0) - r8_state.get('test_ac_graph', 0.0), 4)} |",
            "",
            "## Claim Limit",
            "",
            "Round 09C is a clean held-out benchmark with fixed pipeline:",
            "- New cases not seen in R8 (different tickers, different source records).",
            "- scorer_v9: FinQA tolerance 2%, vector unit normalization.",
            "- hybrid uses KG-first prompt (v3.3_kgfirst).",
            "- ticker filter applied: 0 suspect tickers expected.",
            f"- formula_type other ratio: {round(formula_other_pct * 100, 2)}% (target < 50%).",
        ]
    )
    c.write_text(run_dir / "round9c_summary.md", "\n".join(lines))


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
                facts = [] if method == "vector_only_v9" else facts_by_case[cid]
                prompt = build_prompt(case, method, facts, visible[cid])
                result = None
                raw = None
                usage: dict[str, Any] = {}
                base = {
                    "trace_id": f"local_trace_round9c_{len(traces)+1:04d}_{cid}__{method}",
                    "case_id": cid,
                    "ticker": case["ticker"],
                    "split": "round9c_test",
                    "source_dataset": case["source_dataset"],
                    "method": method,
                    "round": c.ROUND,
                    "kg_batch": "N/A" if method == "vector_only_v9" else batch_id,
                    "prompt_version": prompt_version(method),
                    "scoring_version": c.SCORING_VERSION,
                    "scorer_version": c.SCORING_VERSION,
                    "claim_boundary": c.CLAIM_BOUNDARY,
                    "formula_type": scorer[cid].get("formula_type", ""),
                    "neo4j_facts_count": 0 if method == "vector_only_v9" else len(facts_by_case[cid]),
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
                    "raw_method_result_v9": raw,
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
    parser.add_argument("--sanity-only", action="store_true")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    c.assert_round9b_ready()
    kg_state = c.read_json(c.KG_DIR / "state.json")
    if kg_state.get("phase") != "D_done":
        raise RuntimeError("Round9C KG state is not D_done")
    batch_id = kg_state["batch_id"]
    gen_state = c.read_json(c.GEN_STATE)
    scorer, _visible = c.load_contract_maps()
    cases = [case for case in c.load_all_round9c_cases() if case["case_id"] in scorer]
    if args.sanity_only:
        cases = [case for case in cases if case["source_dataset"] == "FinDER"][:3] + [case for case in cases if case["source_dataset"] == "FinQA"][:2]
    run_dir = Path(args.run_dir).resolve() if args.run_dir else c.OUT_ROOT / f"round9c_eval_{c.ts()}"
    run_dir.mkdir(parents=True, exist_ok=True)
    trace_path = run_dir / "round9c_traces.jsonl"
    facts_path = run_dir / "neo4j_facts_cache.jsonl"
    state = {
        "round": c.ROUND,
        "phase": "running",
        "kg_batch": batch_id,
        "prompt_version": c.KGFIRST_PROMPT_VERSION,
        "scoring_version": c.SCORING_VERSION,
        "claim_boundary": c.CLAIM_BOUNDARY,
        "cases_finder": len([case for case in cases if case["source_dataset"] == "FinDER"]),
        "cases_finqa": len([case for case in cases if case["source_dataset"] == "FinQA"]),
        "cases_total": len(cases),
        "formula_type_other_pct": gen_state.get("formula_type_other_pct", 0.0),
        "runs_total": len(cases) * 3,
        "runs_completed": 0,
        "runs_failed": [],
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
    write_summary(run_dir, rows, float(gen_state.get("formula_type_other_pct", 0.0)))
    print(json.dumps({"run_dir": c.rel(run_dir), "runs_completed": len(rows), "summary": c.rel(run_dir / "round9c_summary.md"), "graph_beats_vector_test": state["graph_beats_vector_test"]}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
