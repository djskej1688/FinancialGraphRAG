"""Small Round 3 mock dry-run.

This script does not call models. It validates prompt construction and
deterministic scoring on 1-2 Neo4j-ready cases using mock method outputs.
"""

from __future__ import annotations

import argparse
import csv
import json
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any

from seocho.eval.round3 import (
    MethodResult,
    Round3MethodInput,
    build_round3_prompt,
    score_answer_correctness,
)


REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_RUN_DIR = REPO_ROOT / "outputs" / "round3_orchestration" / "20260525_132801"
REPAIRED_DIR = REPO_ROOT / "outputs" / "round3_case_factory_repaired"
CASE_PATH = REPAIRED_DIR / "eval_ready_cases.jsonl"
FACT_PATH = REPAIRED_DIR / "eval_ready_required_facts.jsonl"
METHODS = ("graph_facts_only", "hybrid_vector_graph")


def now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def run_dir_from_arg(value: str | None) -> Path:
    if value:
        path = Path(value)
        return path if path.is_absolute() else REPO_ROOT / path
    return DEFAULT_RUN_DIR


def write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text.rstrip() + "\n", encoding="utf-8", newline="\n")


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def ready_case_ids(run_dir: Path) -> list[str]:
    path = run_dir / "neo4j_coverage_summary.csv"
    ready: list[str] = []
    with path.open("r", encoding="utf-8", newline="") as f:
        for row in csv.DictReader(f):
            if row.get("coverage_status") == "ready_for_eval":
                ready.append(str(row.get("case_id")))
    return ready


def mock_answer(case: dict[str, Any], facts: list[dict[str, Any]]) -> MethodResult:
    fact_ids = [str(fact.get("fact_id")) for fact in facts]
    calc = "; ".join(
        f"{fact.get('metric_canonical') or fact.get('metric_raw')} {fact.get('year')}={fact.get('value')} {fact.get('unit')}"
        for fact in facts
    )
    return MethodResult(
        final_answer=str(case.get("expected_answer", "")),
        calculation=calc,
        source_fact_ids_used=fact_ids,
        citations=fact_ids,
    )


def render_report(rows: list[dict[str, Any]]) -> str:
    passed = sum(1 for row in rows if row.get("answer_correctness"))
    return f"""# Small Mock Dry-Run Report

Generated: {now()}

## Scope

- Cases: {len({row['case_id'] for row in rows})}
- Method attempts: {len(rows)}
- Passed deterministic answer correctness: {passed} / {len(rows)}
- Model API called: false
- Neo4j write performed: false
- KG patch applied: false
- Full eval executed: false
- Opik production logging enabled: false

## Notes

This is a local mock dry-run only. It validates executable prompts, input isolation, and deterministic scorers on Neo4j-ready cases using mock outputs derived from local expected answers and required fact IDs. It is not a model evaluation and not a full benchmark.
"""


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run small mock dry-run for Round 3.")
    parser.add_argument("--run-dir", default=None)
    parser.add_argument("--limit", type=int, default=2)
    args = parser.parse_args(argv)
    run_dir = run_dir_from_arg(args.run_dir)

    cases = {str(row.get("case_id")): row for row in load_jsonl(CASE_PATH)}
    facts_by_case: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for fact in load_jsonl(FACT_PATH):
        facts_by_case[str(fact.get("case_id"))].append(fact)

    selected = [case_id for case_id in ready_case_ids(run_dir) if case_id in cases][: args.limit]
    rows: list[dict[str, Any]] = []
    for case_id in selected:
        case = cases[case_id]
        facts = facts_by_case[case_id]
        method_result = mock_answer(case, facts)
        for method in METHODS:
            method_input = Round3MethodInput(
                case_id=case_id,
                split=str(case.get("split", "")),
                question=str(case.get("question", "")),
                vector_context=str(case.get("evidence_text", "")) if method == "hybrid_vector_graph" else None,
                graph_facts=facts,
            )
            prompt = build_round3_prompt(method, method_input)
            score = score_answer_correctness(
                expected_answer=str(case.get("expected_answer", "")),
                required_facts=facts,
                method=method,
                method_input=method_input,
                method_result=method_result,
                retrieved_metadata={"retrieved_fact_ids": method_result.source_fact_ids_used},
                required_fact_threshold=1.0,
            )
            rows.append(
                {
                    "case_id": case_id,
                    "split": case.get("split"),
                    "method": method,
                    "prompt_built": True,
                    "prompt_version": prompt.prompt_version,
                    "prompt_sha256": prompt.prompt_sha256,
                    "answer_correctness": score.answer_correctness,
                    "numeric_correctness": score.numeric_correctness.numeric_correctness,
                    "required_fact_recall": score.required_fact_recall.required_fact_recall,
                    "missing_required_facts": score.required_fact_recall.missing_required_facts,
                    "model_api_called": False,
                    "neo4j_write_performed": False,
                    "kg_patch_applied": False,
                    "full_eval_executed": False,
                }
            )
    write_jsonl(run_dir / "automation" / "small_mock_dry_run_results.jsonl", rows)
    write_text(run_dir / "automation" / "small_mock_dry_run_report.md", render_report(rows))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
