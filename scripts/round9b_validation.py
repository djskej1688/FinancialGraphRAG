from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path
from typing import Any

import round8_common as c
from scorer_v9 import score_trace


OUT_DIR = c.ROOT / "outputs" / "round9b_validation"
REPORT_OUT = OUT_DIR / "validation_report.json"
STATE_OUT = OUT_DIR / "state.json"
R8_TRACES = c.ROOT / "outputs" / "round3_eval_runs" / "round8_eval_20260529_103625" / "round8_traces.jsonl"

EXPECTED = {
    ("graph_neo4j_v8", "FinQA"): 0.45,
    ("graph_neo4j_v8", "FinDER"): 0.50,
    ("graph_neo4j_v8", "overall"): 0.48,
    ("vector_only_v8", "FinQA"): 0.55,
    ("vector_only_v8", "FinDER"): 0.30,
    ("vector_only_v8", "overall"): 0.40,
}


def avg(values: list[Any]) -> float:
    nums = [float(value) for value in values]
    return round(sum(nums) / len(nums), 4) if nums else 0.0


def score_rows(rows: list[dict[str, Any]], contracts: dict[str, Any]) -> list[dict[str, Any]]:
    rescored = []
    for row in rows:
        scored = score_trace(row, contracts[row["case_id"]], row["method"])
        rescored.append(scored)
    return rescored


def summarize(rows: list[dict[str, Any]]) -> dict[tuple[str, str], float]:
    out: dict[tuple[str, str], float] = {}
    methods = sorted({row["method"] for row in rows})
    for method in methods:
        selected = [row for row in rows if row["method"] == method]
        out[(method, "overall")] = avg([row["answer_correctness"] for row in selected])
        for dataset in ["FinDER", "FinQA"]:
            ds_rows = [row for row in selected if row["source_dataset"] == dataset]
            out[(method, dataset)] = avg([row["answer_correctness"] for row in ds_rows])
    return out


def build_checks(summary: dict[tuple[str, str], float]) -> list[dict[str, Any]]:
    checks = []
    for key, expected in EXPECTED.items():
        actual = summary.get(key, 0.0)
        delta = round(actual - expected, 4)
        checks.append(
            {
                "method": key[0],
                "dataset": key[1],
                "expected": expected,
                "actual": actual,
                "delta": delta,
                "passed": abs(delta) <= 0.01,
            }
        )
    return checks


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    rows = c.read_jsonl(R8_TRACES)
    contracts, _visible = c.load_contract_maps()
    if len(rows) != 150:
        raise RuntimeError(f"Expected 150 R8 traces, found {len(rows)}")
    missing = sorted({row["case_id"] for row in rows if row["case_id"] not in contracts})
    if missing:
        raise RuntimeError(f"Missing scorer contracts: {missing[:5]}")

    rescored = score_rows(rows, contracts)
    summary = summarize(rescored)
    checks = build_checks(summary)
    validation_passed = all(check["passed"] for check in checks)
    by_method = defaultdict(dict)
    for (method, dataset), value in sorted(summary.items()):
        by_method[method][dataset] = value

    report = {
        "round": "round9b",
        "claim_boundary": "pipeline_fix_no_model_eval",
        "scorer_version": "v9",
        "r8_traces_source": c.rel(R8_TRACES),
        "checks": checks,
        "summary": dict(by_method),
        "validation_passed": validation_passed,
        "model_calls": 0,
        "completed_at": c.utc_now(),
    }
    c.write_json(REPORT_OUT, report)
    state = {
        "phase": "9b_done",
        "round": "round9b",
        "scorer_version": "v9",
        "changes": [
            "finqa_tolerance_2pct",
            "vector_unit_normalization",
            "other_formula_output_spec",
            "hybrid_kgfirst_prompt",
            "ticker_denylist",
        ],
        "validation_passed": validation_passed,
        "model_calls": 0,
        "validation_report": c.rel(REPORT_OUT),
        "completed_at": c.utc_now(),
    }
    c.write_json(STATE_OUT, state)
    print(json.dumps(state, ensure_ascii=False, indent=2))
    if not validation_passed:
        raise SystemExit(2)


if __name__ == "__main__":
    main()
