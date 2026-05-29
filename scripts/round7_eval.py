"""Round 07 targeted diagnostic rerun."""

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
ROUND6_PATH = ROOT / "scripts" / "round6_eval.py"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

spec = importlib.util.spec_from_file_location("round6_eval", ROUND6_PATH)
r6 = importlib.util.module_from_spec(spec)
assert spec and spec.loader
spec.loader.exec_module(r6)

METHODS = ["vector_only_v7", "graph_neo4j_v7", "hybrid_neo4j_v7"]
MODEL = "gpt-4o-mini"
ROUND = "round7"
KG_BATCH = "kg-targeted-ie-v1-20260528"
PROMPT_VERSION = "v3.3"
SCORING_VERSION = "v7_no_faith_gate"
CLAIM_BOUNDARY = "targeted_diagnostic_rerun_r06_failure_cases"
STATE_PATH = ROOT / "outputs" / "round7_eval" / "state.json"
OUT_ROOT = ROOT / "outputs" / "round3_eval_runs"
ROUND6_RUN = OUT_ROOT / "round6_eval_20260528_233753"
R6_RESCORED = ROOT / "outputs" / "round6_eval" / "r6_rescored_v7.jsonl"
PROMPT_V33 = ROOT / "outputs" / "round3_dual_track_eval_prep" / "prompt_formatter_v3_2" / "prompt_v3_3_system.md"

SANITY_CASE_PREFIXES = {
    "round3_test_009",  # AMGN
    "round3_test_012",  # GM
    "round3_test_014",  # MU
    "round3_test_018",  # BXP
    "round3_test_004",  # XEL
}

RESOLVED_BY = {
    "round3_test_009": "scorer_fix",
    "round3_test_012": "prompt_v3.3",
    "round3_test_014": "prompt_v3.3",
    "round3_test_018": "prompt_v3.3",
    "round3_test_004": "kg_patch",
}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def ts() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S")


def rel(path: Path) -> str:
    return path.resolve().relative_to(ROOT.resolve()).as_posix()


def avg(values: list[Any]) -> float:
    nums = [float(value) for value in values]
    return round(sum(nums) / len(nums), 4) if nums else 0.0


def read_json(path: Path) -> Any:
    return r6.read_json(path)


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    return r6.read_jsonl(path)


def write_json(path: Path, data: Any) -> None:
    r6.write_json(path, data)


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    r6.write_jsonl(path, rows)


def write_text(path: Path, text: str) -> None:
    r6.write_text(path, text)


def patch_xel_contracts(scorer: dict[str, Any], visible: dict[str, Any]) -> None:
    cid = "round3_test_004_b035aeed"
    if cid not in scorer:
        return
    scorer[cid]["source_fact_numbers"] = [
        {
            "fact_id": "round3_test_004_b035aeed_fact_01",
            "metric": "female_employee_pct",
            "unit": "%",
            "value": 23.0,
            "year": 2023,
        },
        {
            "fact_id": "round3_test_004_b035aeed_fact_02",
            "metric": "female_management_pct",
            "unit": "%",
            "value": 26.0,
            "year": 2023,
        },
    ]
    if cid in visible:
        visible[cid]["denominator_metric_role"] = "female_employee_pct"
        visible[cid]["numerator_metric_role"] = "female_management_pct"
        visible[cid]["required_steps"] = [
            "identify female_management_pct",
            "identify female_employee_pct",
            "compute female_management_pct / female_employee_pct",
        ]


def build_prompt(case: dict[str, Any], method: str, facts: list[dict[str, Any]], model_visible_contract: dict[str, Any]) -> dict[str, str]:
    pdir = r6.r5.prompt_dir()
    system = PROMPT_V33.read_text(encoding="utf-8")
    answer_format = (pdir / "answer_format_spec_v3_2.md").read_text(encoding="utf-8")
    rounding = (pdir / "rounding_and_tolerance_rules_v3_2.md").read_text(encoding="utf-8")
    evidence = str(case.get("evidence_text", ""))
    if method == "vector_only_v7":
        context = f"TEXT_CONTEXT\n{evidence}"
    elif method == "graph_neo4j_v7":
        context = f"GRAPH_FACTS_TABLE\n{r6.r5.fact_table(facts)}"
    elif method == "hybrid_neo4j_v7":
        context = f"TEXT_CONTEXT\n{evidence}\n\nGRAPH_FACTS_TABLE\n{r6.r5.fact_table(facts)}"
    else:
        raise RuntimeError(f"unknown method: {method}")
    user = f"""QUESTION
{case['question']}

{context}
{r6.r5.formula_section(model_visible_contract)}

ANSWER_FORMAT
{answer_format}

ROUNDING_AND_TOLERANCE_RULES
{rounding}

Use temperature=0 behavior. Do not use outside knowledge. Do not mention hidden expected answers or scorer-only target slots.
"""
    return {"system": system, "user": user}


def load_neo4j_graph_facts_filtered(ticker: str, case_id: str, years: list[int], driver: Any) -> list[dict[str, Any]]:
    database = r6.r5.r4.neo4j_env()["NEO4J_DATABASE"]
    years = [int(year) for year in years if str(year).isdigit()]
    last_error: Exception | None = None
    for attempt in range(3):
        active_driver = driver if attempt == 0 else r6.r5.create_driver()
        try:
            with active_driver.session(database=database) as session:
                records = session.run(
                    """
MATCH (obs:LLMObservation)-[:LLM_MENTIONS_COMPANY]->(c:LLMCompany {ticker: $ticker}),
      (obs)-[:LLM_OBSERVES_METRIC]->(m:LLMFinancialMetric),
      (obs)-[:LLM_OBSERVED_IN_YEAR]->(yr:LLMFiscalYear)
WHERE obs.kg_batch = $batch
  AND yr.year IN $years
  AND coalesce(obs.validation_status, '') <> 'deprecated_r7_patch'
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


def score_result(method: str, result: Any | None, required_facts: list[dict[str, Any]], neo4j_facts: list[dict[str, Any]], scorer_contract: dict[str, Any]) -> dict[str, Any]:
    mapped = method.replace("_v7", "_v6")
    base = r6.score_result(mapped, result, required_facts, neo4j_facts, scorer_contract)
    if result is None:
        base["scoring_version"] = SCORING_VERSION
        return base
    numeric_ok = float(base["target_numeric_recall"]) >= 0.8
    fmt = bool(result.final_answer and result.calculation)
    calc = bool(result.calculation and any(token in result.calculation.lower() for token in ["/", "%", "=", "ratio", "rate", "margin", "growth", "change", "formula"]))
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
    base["scoring_version"] = SCORING_VERSION
    return base


def required_facts_for_case(case_id: str, scorer_contract: dict[str, Any], fallback_required: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return scorer_contract.get("source_fact_numbers") or fallback_required


def load_eval_context() -> tuple[list[dict[str, Any]], dict[str, Any], dict[str, Any], dict[str, list[dict[str, Any]]], set[str]]:
    cases = r6.r5.load_cases()
    scorer, visible = r6.r5.load_all_formula_contracts()
    required = r6.r5.load_required_facts()
    fallback_cases = set(r6.r5.fill_source_target_slots(cases, scorer, required))
    patch_xel_contracts(scorer, visible)
    return cases, scorer, visible, required, fallback_cases


def run_eval(
    selected_cases: list[dict[str, Any]],
    trace_path: Path,
    facts_cache_path: Path,
    driver: Any,
    scorer: dict[str, Any],
    visible: dict[str, Any],
    required: dict[str, list[dict[str, Any]]],
    fallback_cases: set[str],
    resume: bool,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    traces = read_jsonl(trace_path) if resume else []
    traces = [row for row in traces if row.get("failure_reason") != "provider_error"]
    facts_cache = read_jsonl(facts_cache_path) if resume else []
    facts_by_case = {row["case_id"]: row["facts"] for row in facts_cache}
    completed = {(row["case_id"], row["method"]) for row in traces}
    for case in selected_cases:
        cid = case["case_id"]
        ticker = str(case["ticker"]).upper()
        if cid not in facts_by_case:
            facts_by_case[cid] = load_neo4j_graph_facts_filtered(ticker, cid, case.get("years", []), driver)
            facts_cache.append({"case_id": cid, "ticker": ticker, "neo4j_facts_count": len(facts_by_case[cid]), "facts": facts_by_case[cid]})
            write_jsonl(facts_cache_path, facts_cache)
        for method in METHODS:
            if (cid, method) in completed:
                continue
            attempt_idx = len(traces) + 1
            prompt_facts = [] if method == "vector_only_v7" else facts_by_case[cid]
            prompt = build_prompt(case, method, prompt_facts, visible[cid])
            result = None
            raw = None
            usage: dict[str, Any] = {}
            base = {
                "trace_id": f"local_trace_round7_{attempt_idx:04d}_{cid}__{method}",
                "case_id": cid,
                "ticker": ticker,
                "split": case.get("split", ""),
                "method": method,
                "round": ROUND,
                "prompt_version": PROMPT_VERSION,
                "scoring_version": SCORING_VERSION,
                "kg_batch": "N/A" if method == "vector_only_v7" else KG_BATCH,
                "step_b_batch": KG_BATCH,
                "formula_type": scorer[cid].get("formula_type") or visible[cid].get("formula_type", ""),
                "diagnostic_source_target_fallback": cid in fallback_cases,
                "test_split_formula_source": "post_hoc" if case.get("split") == "round3_test" else "pre_built",
                "claim_boundary": CLAIM_BOUNDARY,
                "neo4j_facts_count": 0 if method == "vector_only_v7" else len(facts_by_case[cid]),
                "xel_kg_patched": True,
                "provider": "openai",
                "model": MODEL,
                "target_slot_count": len(scorer[cid].get("target_slots", [])),
                "success": False,
                "provider_success": False,
                "error_type": "",
                "error_message": "",
            }
            try:
                result, usage, raw = r6.r5.call_openai(prompt)
                base.update({"success": True, "provider_success": True})
            except r6.r5.r4.ProviderError as exc:
                base.update({"error_type": exc.error_type, "error_message": str(exc)})
            except Exception as exc:  # noqa: BLE001
                base.update({"error_type": "scoring_uncertain", "error_message": str(exc)[:300]})
            req = required_facts_for_case(cid, scorer[cid], required.get(cid, []))
            scores = score_result(method, result, req, facts_by_case[cid], scorer[cid])
            base.update(scores)
            if base.get("error_type") in r6.r5.r4.PROVIDER_ERROR_TYPES:
                base["failure_reason"] = "provider_error"
            row = {
                **base,
                "final_answer": result.final_answer if result else "",
                "calculation": result.calculation if result else "",
                "prompt_sha256": r6.r5.r4.sha(prompt["system"] + "\n" + prompt["user"]),
                "system_prompt": prompt["system"],
                "user_prompt": prompt["user"],
                "method_result": asdict(result) if result else None,
                "raw_method_result_v7": raw,
                "usage": usage,
                "model_api_called": True,
                "neo4j_write_performed": False,
                "kg_patch_applied": False,
            }
            traces.append(row)
            completed.add((cid, method))
            write_jsonl(trace_path, traces)
            print(json.dumps({"trace_rows": len(traces), "case_id": cid, "method": method, "failure_reason": row["failure_reason"]}, ensure_ascii=False), flush=True)
            time.sleep(0.25)
    return traces, facts_cache


def summary_by_method(rows: list[dict[str, Any]], split: str = "round3_test") -> dict[str, dict[str, float]]:
    out: dict[str, dict[str, float]] = {}
    for method in METHODS:
        selected = [row for row in rows if row.get("split") == split and row.get("method") == method]
        out[method.replace("_v7", "")] = {
            "ac": avg([row["answer_correctness"] for row in selected]),
            "nc": avg([row["numerical_closeness"] for row in selected]),
            "rfr": avg([row["required_fact_recall"] for row in selected]),
        }
    return out


def r6_test(rows_path: Path) -> dict[str, dict[str, float]]:
    rows = read_jsonl(rows_path)
    mapping = {"vector_only": "vector_only_v6", "graph_neo4j": "graph_neo4j_v6", "hybrid_neo4j": "hybrid_neo4j_v6"}
    return {
        label: {
            "ac": avg([row["answer_correctness"] for row in rows if row.get("split") == "round3_test" and row.get("method") == method]),
            "nc": avg([row["numerical_closeness"] for row in rows if row.get("split") == "round3_test" and row.get("method") == method]),
        }
        for label, method in mapping.items()
    }


def sanity_report(rows: list[dict[str, Any]]) -> dict[str, Any]:
    by = {(row["case_id"], row["method"]): row for row in rows}
    def find(prefix: str, method: str) -> dict[str, Any]:
        cid = next(row["case_id"] for row in rows if row["case_id"].startswith(prefix))
        return by[(cid, method)]

    checks = {
        "amgn_graph_none": find("round3_test_009", "graph_neo4j_v7").get("failure_reason") == "none",
        "mu_graph_all_years": all(slot in find("round3_test_014", "graph_neo4j_v7").get("matched_target_slots", "") for slot in ["net_margin_2022", "net_margin_2023", "net_margin_2024"]),
        "gm_graph_2021": "tpo_gross_margin_2021" in find("round3_test_012", "graph_neo4j_v7").get("matched_target_slots", ""),
        "bxp_not_old_10_4": "10.4" not in find("round3_test_018", "graph_neo4j_v7").get("final_answer", ""),
        "xel_graph_two_facts": find("round3_test_004", "graph_neo4j_v7").get("neo4j_facts_count") == 2,
    }
    return {"passed": all(checks.values()), "checks": checks}


def write_summary(run_dir: Path, rows: list[dict[str, Any]], sanity: dict[str, Any]) -> None:
    r6_original = r6_test(ROUND6_RUN / "round6_traces.jsonl")
    r6_rescored = r6_test(R6_RESCORED)
    r7 = summary_by_method(rows)
    lines = [
        "# Round 07 Targeted Diagnostic Rerun",
        "",
        f"**Claim boundary:** {CLAIM_BOUNDARY}",
        f"**KG batch:** {KG_BATCH}",
        f"**Prompt version:** {PROMPT_VERSION}",
        f"**Scoring version:** {SCORING_VERSION}",
        f"**Sanity passed:** {sanity.get('passed')}",
        "",
        "## R6 to R7 delta (test split)",
        "",
        "| Method | R6_original | R6_rescored | R7 | delta_scorer | delta_combined |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    for method in ["vector_only", "graph_neo4j", "hybrid_neo4j"]:
        lines.append(
            f"| {method} | {r6_original[method]['ac']} | {r6_rescored[method]['ac']} | {r7[method]['ac']} | "
            f"{round(r6_rescored[method]['ac'] - r6_original[method]['ac'], 4)} | {round(r7[method]['ac'] - r6_rescored[method]['ac'], 4)} |"
        )
    lines.extend(["", "## Per-case test breakdown (5 targeted failures)", "", "| ticker | formula_type | R6_graph_ac | R7_graph_ac | delta | resolved_by | failure_reason |", "|---|---|---:|---:|---:|---|---|"])
    r6_graph = {row["case_id"]: row for row in read_jsonl(ROUND6_RUN / "round6_traces.jsonl") if row.get("method") == "graph_neo4j_v6"}
    for prefix in sorted(SANITY_CASE_PREFIXES):
        row = next(r for r in rows if r["case_id"].startswith(prefix) and r["method"] == "graph_neo4j_v7")
        before = r6_graph[row["case_id"]]
        lines.append(
            f"| {row['ticker']} | {row['formula_type']} | {before['answer_correctness']} | {row['answer_correctness']} | "
            f"{round(float(row['answer_correctness']) - float(before['answer_correctness']), 4)} | {RESOLVED_BY[prefix]} | {row['failure_reason']} |"
        )
    lines.extend(["", "## Sanity checks", ""])
    for key, value in sanity.get("checks", {}).items():
        lines.append(f"- {key}: {value}")
    lines.extend(
        [
            "",
            "## Claim Limit",
            "",
            "Round 07 is a targeted diagnostic rerun of known Round 06 failure cases, not a clean held-out benchmark.",
        ]
    )
    write_text(run_dir / "round7_summary.md", "\n".join(lines))


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-dir", default="")
    parser.add_argument("--resume", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--sanity-only", action="store_true")
    return parser.parse_args()


def update_state(state: dict[str, Any], rows: list[dict[str, Any]]) -> None:
    state["runs_completed"] = len(rows)
    state["runs_failed"] = [
        {"case_id": row["case_id"], "method": row["method"], "failure_reason": row.get("failure_reason"), "error_type": row.get("error_type", "")}
        for row in rows
        if row.get("failure_reason") == "provider_error"
    ]
    test = [row for row in rows if row.get("split") == "round3_test"]
    state["test_ac_vector"] = avg([row["answer_correctness"] for row in test if row["method"] == "vector_only_v7"])
    state["test_ac_graph"] = avg([row["answer_correctness"] for row in test if row["method"] == "graph_neo4j_v7"])
    state["test_ac_hybrid"] = avg([row["answer_correctness"] for row in test if row["method"] == "hybrid_neo4j_v7"])
    state["graph_beats_vector_test"] = state["test_ac_graph"] > state["test_ac_vector"]
    write_json(STATE_PATH, state)


def initial_state(run_dir: Path) -> dict[str, Any]:
    return {
        "phase": "running",
        "round": ROUND,
        "kg_batch": KG_BATCH,
        "xel_kg_patched": True,
        "prompt_version": PROMPT_VERSION,
        "scoring_version": SCORING_VERSION,
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


def main() -> None:
    args = parse_args()
    cases, scorer, visible, required, fallback_cases = load_eval_context()
    selected = [case for case in cases if any(case["case_id"].startswith(prefix) for prefix in SANITY_CASE_PREFIXES)] if args.sanity_only else cases
    out_dir = ROOT / "outputs" / "round7_eval"
    out_dir.mkdir(parents=True, exist_ok=True)
    run_dir = Path(args.run_dir).resolve() if args.run_dir else OUT_ROOT / f"round7_eval_{ts()}"
    run_dir.mkdir(parents=True, exist_ok=True)
    trace_path = out_dir / "sanity_run_15.jsonl" if args.sanity_only else run_dir / "round7_traces.jsonl"
    facts_path = out_dir / "sanity_neo4j_facts_cache.jsonl" if args.sanity_only else run_dir / "neo4j_facts_cache.jsonl"
    driver = r6.r5.create_driver()
    try:
        rows, facts_cache = run_eval(selected, trace_path, facts_path, driver, scorer, visible, required, fallback_cases, args.resume)
    finally:
        driver.close()
    sanity = sanity_report(rows) if args.sanity_only else {"passed": True, "checks": {}}
    if args.sanity_only:
        write_json(out_dir / "sanity_report.json", sanity)
        print(json.dumps({"sanity": sanity, "trace": rel(trace_path)}, ensure_ascii=False, indent=2))
        if not sanity["passed"]:
            raise SystemExit(2)
        return

    sanity_path = out_dir / "sanity_report.json"
    sanity = read_json(sanity_path) if sanity_path.exists() else {"passed": None, "checks": {}}
    write_summary(run_dir, rows, sanity)
    state = initial_state(run_dir)
    state["phase"] = "done"
    state["completed_at"] = utc_now()
    state["codex_handoff_message"] = "Round 7 complete. Check round7_summary.md."
    update_state(state, rows)
    print(json.dumps({"run_dir": rel(run_dir), "runs_completed": len(rows), "summary": rel(run_dir / "round7_summary.md"), "graph_beats_vector_test": state["graph_beats_vector_test"]}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
