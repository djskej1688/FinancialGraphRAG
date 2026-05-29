"""No-model Round 6 rescore with Round 7 scorer semantics."""

from __future__ import annotations

import importlib.util
import json
import sys
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

RUN_DIR = ROOT / "outputs" / "round3_eval_runs" / "round6_eval_20260528_233753"
TRACE_PATH = RUN_DIR / "round6_traces.jsonl"
FACTS_PATH = RUN_DIR / "neo4j_facts_cache.jsonl"
OUT_JSONL = ROOT / "outputs" / "round6_eval" / "r6_rescored_v7.jsonl"
OUT_SUMMARY = ROOT / "outputs" / "round6_eval" / "r6_rescore_v7_summary.md"
STATE_PATH = ROOT / "outputs" / "round6_eval" / "state.json"


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def avg(values: list[Any]) -> float:
    nums = [float(value) for value in values]
    return round(sum(nums) / len(nums), 4) if nums else 0.0


def score_v7(method: str, result: Any, required_facts: list[dict[str, Any]], facts: list[dict[str, Any]], scorer: dict[str, Any]) -> dict[str, Any]:
    base = r6.score_result(method, result, required_facts, facts, scorer)
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
    base["scoring_version"] = "v7_no_faith_gate"
    return base


def summarize(rows: list[dict[str, Any]], method_suffix: str) -> dict[str, float]:
    test = [row for row in rows if row.get("split") == "round3_test" and row.get("method", "").endswith(method_suffix)]
    return {
        "ac": avg([row["answer_correctness"] for row in test]),
        "nc": avg([row["numerical_closeness"] for row in test]),
        "rfr": avg([row["required_fact_recall"] for row in test]),
    }


def main() -> None:
    rows = r6.read_jsonl(TRACE_PATH)
    facts_cache = r6.read_jsonl(FACTS_PATH)
    facts_by_case = {row["case_id"]: row["facts"] for row in facts_cache}
    cases = r6.r5.load_cases()
    scorer, _visible = r6.r5.load_all_formula_contracts()
    required_by_case = r6.r5.load_required_facts()
    r6.r5.fill_source_target_slots(cases, scorer, required_by_case)
    rescored: list[dict[str, Any]] = []
    for row in rows:
        result = r6.r5.r4.MethodResult(
            final_answer=row.get("final_answer", ""),
            calculation=row.get("calculation", ""),
            source_fact_ids_used=[],
            citations=[],
            missing_information=[],
        )
        required = r6.required_facts_for_case(row["case_id"], scorer[row["case_id"]], required_by_case.get(row["case_id"], []))
        scores = score_v7(row["method"], result, required, facts_by_case.get(row["case_id"], []), scorer[row["case_id"]])
        out = {**row, **scores, "scoring_version": "v7_no_faith_gate", "model_api_called": False}
        rescored.append(out)

    r6.write_jsonl(OUT_JSONL, rescored)
    original = rows
    lines = [
        "# R6 Rescore v7 Summary",
        "",
        "| Method | R6_original_ac | R6_rescored_ac | delta_scorer |",
        "|---|---:|---:|---:|",
    ]
    state_updates: dict[str, float] = {}
    for label, suffix in [("vector_only", "vector_only_v6"), ("graph_neo4j", "graph_neo4j_v6"), ("hybrid_neo4j", "hybrid_neo4j_v6")]:
        orig = summarize(original, suffix)
        new = summarize(rescored, suffix)
        lines.append(f"| {label} | {orig['ac']} | {new['ac']} | {round(new['ac'] - orig['ac'], 4)} |")
        state_updates[f"r6_rescored_test_ac_{label.split('_')[0] if label != 'graph_neo4j' else 'graph'}"] = new["ac"]
    r6.write_text(OUT_SUMMARY, "\n".join(lines))

    state = r6.read_json(STATE_PATH)
    state["r6_rescored_v7"] = r6.rel(OUT_JSONL)
    state["r6_rescored_v7_summary"] = r6.rel(OUT_SUMMARY)
    state["r6_rescored_test_ac_vector"] = summarize(rescored, "vector_only_v6")["ac"]
    state["r6_rescored_test_ac_graph"] = summarize(rescored, "graph_neo4j_v6")["ac"]
    state["r6_rescored_test_ac_hybrid"] = summarize(rescored, "hybrid_neo4j_v6")["ac"]
    state["r6_rescored_at"] = utc_now()
    r6.write_json(STATE_PATH, state)
    print(json.dumps({"rows": len(rescored), "summary": r6.rel(OUT_SUMMARY)}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
