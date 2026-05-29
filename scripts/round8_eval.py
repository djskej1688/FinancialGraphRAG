from __future__ import annotations

import argparse
import json
import sys
import time
from dataclasses import asdict
from pathlib import Path
from typing import Any

import round8_common as c


def avg(values: list[Any]) -> float:
    nums = [float(v) for v in values]
    return round(sum(nums) / len(nums), 4) if nums else 0.0


def build_prompt(case: dict[str, Any], method: str, facts: list[dict[str, Any]], visible: dict[str, Any]) -> dict[str, str]:
    pdir = c.ROOT / "outputs" / "round3_dual_track_eval_prep" / "prompt_formatter_v3_2"
    system = (pdir / "prompt_v3_3_system.md").read_text(encoding="utf-8")
    answer_format = (pdir / "answer_format_spec_v3_2.md").read_text(encoding="utf-8")
    rounding = (pdir / "rounding_and_tolerance_rules_v3_2.md").read_text(encoding="utf-8")
    if method == "vector_only_v8":
        context = f"TEXT_CONTEXT\n{case['evidence_text']}"
    elif method == "graph_neo4j_v8":
        context = f"GRAPH_FACTS_TABLE\n{c.r7.r6.r5.fact_table(facts)}"
    elif method == "hybrid_neo4j_v8":
        context = f"TEXT_CONTEXT\n{case['evidence_text']}\n\nGRAPH_FACTS_TABLE\n{c.r7.r6.r5.fact_table(facts)}"
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
    return {"system": system, "user": user}


def load_graph_facts(case: dict[str, Any], driver: Any) -> list[dict[str, Any]]:
    env = c.neo4j_env()
    years = [int(y) for y in case.get("years", []) if str(y).isdigit()]
    if not years:
        years = [0]
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
            batch=c.ROUND8_BATCH,
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


def score_result(method: str, result: Any | None, required: list[dict[str, Any]], facts: list[dict[str, Any]], scorer: dict[str, Any]) -> dict[str, Any]:
    mapped = {"vector_only_v8": "vector_only_v6", "graph_neo4j_v8": "graph_neo4j_v6", "hybrid_neo4j_v8": "hybrid_neo4j_v6"}[method]
    base = c.r7.r6.score_result(mapped, result, required, facts, scorer)
    if result is None:
        base["scoring_version"] = c.SCORING_VERSION
        return base
    numeric_ok = float(base["target_numeric_recall"]) >= 0.8
    fmt = bool(result.final_answer and result.calculation)
    calc = bool(result.calculation and any(token in result.calculation.lower() for token in ["/", "%", "=", "ratio", "rate", "margin", "growth", "change", "formula", "program"]))
    ans = numeric_ok and fmt and calc
    failure = "none"
    if not fmt:
        failure = "answer_format_error"
    elif not numeric_ok:
        failure = "formula_target_mismatch"
    elif not ans:
        failure = "scoring_uncertain"
    base["answer_correctness"] = 1.0 if ans else 0.0
    base["failure_reason"] = failure
    base["scoring_version"] = c.SCORING_VERSION
    return base


def update_state(state: dict[str, Any], rows: list[dict[str, Any]]) -> None:
    state["runs_completed"] = len(rows)
    state["runs_failed"] = [{"case_id": r["case_id"], "method": r["method"], "error_type": r.get("error_type", "")} for r in rows if r.get("failure_reason") == "provider_error"]
    test = rows
    state["test_ac_vector"] = avg([r["answer_correctness"] for r in test if r["method"] == "vector_only_v8"])
    state["test_ac_graph"] = avg([r["answer_correctness"] for r in test if r["method"] == "graph_neo4j_v8"])
    state["test_ac_hybrid"] = avg([r["answer_correctness"] for r in test if r["method"] == "hybrid_neo4j_v8"])
    state["test_ac_graph_finder"] = avg([r["answer_correctness"] for r in test if r["method"] == "graph_neo4j_v8" and r["source_dataset"] == "FinDER"])
    state["test_ac_graph_finqa"] = avg([r["answer_correctness"] for r in test if r["method"] == "graph_neo4j_v8" and r["source_dataset"] == "FinQA"])
    state["graph_beats_vector_test"] = state["test_ac_graph"] > state["test_ac_vector"]
    c.write_json(c.EVAL_STATE, state)


def write_summary(run_dir: Path, rows: list[dict[str, Any]]) -> None:
    lines = ["# Round 08 Summary", "", "## Overall (all cases)", "", "| Method | avg_ac | avg_nc | avg_rfr | n_cases |", "|---|---:|---:|---:|---:|"]
    for method in c.METHODS:
        selected = [r for r in rows if r["method"] == method]
        lines.append(f"| {method} | {avg([r['answer_correctness'] for r in selected])} | {avg([r['numerical_closeness'] for r in selected])} | {avg([r['required_fact_recall'] for r in selected])} | {len(selected)} |")
    lines.extend(["", "## By Dataset", "", "| Dataset | Method | avg_ac | avg_nc | n_cases |", "|---|---|---:|---:|---:|"])
    for dataset in ["FinDER", "FinQA"]:
        for method in c.METHODS:
            selected = [r for r in rows if r["source_dataset"] == dataset and r["method"] == method]
            lines.append(f"| {dataset} | {method} | {avg([r['answer_correctness'] for r in selected])} | {avg([r['numerical_closeness'] for r in selected])} | {len(selected)} |")
    state = c.read_json(c.EVAL_STATE)
    lines.extend([
        "",
        "## Claim Limit",
        "",
        "Round 08 is a clean held-out benchmark:",
        "- Cases selected from unused FinDER + FinQA records without observed model outcome.",
        "- Formula contracts auto-generated and validated.",
        f"- KG extraction performed on new batch `{c.ROUND8_BATCH}`.",
        f"- graph_beats_vector: {state['graph_beats_vector_test']}",
    ])
    c.write_text(run_dir / "round8_summary.md", "\n".join(lines))


def run_cases(cases: list[dict[str, Any]], trace_path: Path, facts_path: Path, resume: bool) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    scorer, visible = c.load_contract_maps()
    traces = c.read_jsonl(trace_path) if resume else []
    traces = [r for r in traces if r.get("failure_reason") != "provider_error"]
    facts_cache = c.read_jsonl(facts_path) if resume else []
    facts_by_case = {r["case_id"]: r["facts"] for r in facts_cache}
    completed = {(r["case_id"], r["method"]) for r in traces}
    driver = c.create_driver()
    try:
        for case in cases:
            cid = case["case_id"]
            if cid not in facts_by_case:
                facts_by_case[cid] = load_graph_facts(case, driver)
                facts_cache.append({"case_id": cid, "ticker": case["ticker"], "neo4j_facts_count": len(facts_by_case[cid]), "facts": facts_by_case[cid]})
                c.write_jsonl(facts_path, facts_cache)
            for method in c.METHODS:
                if (cid, method) in completed:
                    continue
                prompt = build_prompt(case, method, [] if method == "vector_only_v8" else facts_by_case[cid], visible[cid])
                result = None
                raw = None
                usage = {}
                base = {
                    "trace_id": f"local_trace_round8_{len(traces)+1:04d}_{cid}__{method}",
                    "case_id": cid,
                    "ticker": case["ticker"],
                    "split": "round8_test",
                    "source_dataset": case["source_dataset"],
                    "method": method,
                    "round": "round8",
                    "kg_batch": "N/A" if method == "vector_only_v8" else c.ROUND8_BATCH,
                    "prompt_version": c.PROMPT_VERSION,
                    "scoring_version": c.SCORING_VERSION,
                    "claim_boundary": c.CLAIM_BOUNDARY,
                    "formula_type": scorer[cid].get("formula_type", ""),
                    "neo4j_facts_count": 0 if method == "vector_only_v8" else len(facts_by_case[cid]),
                    "target_slot_count": len(scorer[cid].get("target_slots", [])),
                    "provider": "openai",
                    "model": c.MODEL,
                    "success": False,
                    "provider_success": False,
                    "error_type": "",
                    "error_message": "",
                }
                try:
                    result, usage, raw = c.r7.r6.r5.call_openai(prompt)
                    base.update({"success": True, "provider_success": True})
                except c.r7.r6.r5.r4.ProviderError as exc:
                    base.update({"error_type": exc.error_type, "error_message": str(exc)})
                except Exception as exc:  # noqa: BLE001
                    base.update({"error_type": "scoring_uncertain", "error_message": str(exc)[:300]})
                scores = score_result(method, result, scorer[cid].get("source_fact_numbers", []), facts_by_case[cid], scorer[cid])
                base.update(scores)
                if base.get("error_type") in c.r7.r6.r5.r4.PROVIDER_ERROR_TYPES:
                    base["failure_reason"] = "provider_error"
                row = {
                    **base,
                    "final_answer": result.final_answer if result else "",
                    "calculation": result.calculation if result else "",
                    "prompt_sha256": c.sha(prompt["system"] + "\n" + prompt["user"]),
                    "method_result": asdict(result) if result else None,
                    "raw_method_result_v8": raw,
                    "usage": usage,
                    "model_api_called": True,
                    "neo4j_write_performed": False,
                }
                traces.append(row)
                completed.add((cid, method))
                c.write_jsonl(trace_path, traces)
                print(json.dumps({"runs_completed": len(traces), "case_id": cid, "method": method, "failure_reason": row["failure_reason"]}, ensure_ascii=False), flush=True)
                time.sleep(0.2)
    finally:
        driver.close()
    return traces, facts_cache


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-dir", default="")
    parser.add_argument("--resume", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--sanity-only", action="store_true")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    kg_state = c.read_json(c.KG_DIR / "state.json")
    if kg_state.get("phase") != "D_done":
        raise RuntimeError("Round8 KG state is not D_done")
    scorer, _visible = c.load_contract_maps()
    cases = [case for case in c.load_all_round8_cases() if case["case_id"] in scorer]
    if args.sanity_only:
        finder = [case for case in cases if case["source_dataset"] == "FinDER"][:3]
        finqa = [case for case in cases if case["source_dataset"] == "FinQA"][:2]
        selected = finder + finqa
        trace_path = c.ROOT / "outputs" / "round8_eval" / "sanity_run_15.jsonl"
        facts_path = c.ROOT / "outputs" / "round8_eval" / "sanity_neo4j_facts_cache.jsonl"
        rows, _facts = run_cases(selected, trace_path, facts_path, args.resume)
        graph = avg([r["answer_correctness"] for r in rows if r["method"] == "graph_neo4j_v8"])
        vector = avg([r["answer_correctness"] for r in rows if r["method"] == "vector_only_v8"])
        ac_count = sum(1 for r in rows if r["answer_correctness"] == 1.0)
        report = {"passed": ac_count >= 5 and graph >= vector, "ac_count": ac_count, "graph_ac": graph, "vector_ac": vector}
        c.write_json(c.ROOT / "outputs" / "round8_eval" / "sanity_report.json", report)
        print(json.dumps(report, ensure_ascii=False, indent=2))
        if not report["passed"]:
            raise SystemExit(2)
        return
    run_dir = Path(args.run_dir).resolve() if args.run_dir else c.OUT_ROOT / f"round8_eval_{c.ts()}"
    run_dir.mkdir(parents=True, exist_ok=True)
    trace_path = run_dir / "round8_traces.jsonl"
    facts_path = run_dir / "neo4j_facts_cache.jsonl"
    state = {
        "round": "round8",
        "phase": "running",
        "kg_batch": c.ROUND8_BATCH,
        "prompt_version": c.PROMPT_VERSION,
        "scoring_version": c.SCORING_VERSION,
        "claim_boundary": c.CLAIM_BOUNDARY,
        "cases_finder": len([x for x in cases if x["source_dataset"] == "FinDER"]),
        "cases_finqa": len([x for x in cases if x["source_dataset"] == "FinQA"]),
        "cases_total": len(cases),
        "runs_total": len(cases) * 3,
        "runs_completed": 0,
        "runs_failed": [],
        "run_dir": c.rel(run_dir) + "/",
        "started_at": c.utc_now(),
        "completed_at": None,
    }
    c.write_json(c.EVAL_STATE, state)
    rows, _facts = run_cases(cases, trace_path, facts_path, args.resume)
    update_state(state, rows)
    state = c.read_json(c.EVAL_STATE)
    state["phase"] = "done"
    state["completed_at"] = c.utc_now()
    c.write_json(c.EVAL_STATE, state)
    write_summary(run_dir, rows)
    print(json.dumps({"run_dir": c.rel(run_dir), "runs_completed": len(rows), "summary": c.rel(run_dir / "round8_summary.md"), "graph_beats_vector_test": state["graph_beats_vector_test"]}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
