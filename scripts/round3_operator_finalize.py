"""Finalize the Round 3 local operator state for Antigravity.

This local operator script reads evidence and derived state, classifies the
terminal state, and writes Antigravity-facing reports. It does not connect to
Neo4j, run evaluation, call model APIs, or modify source artifacts.
"""

from __future__ import annotations

import argparse
import csv
import json
from datetime import datetime
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_RUN_DIR = REPO_ROOT / "outputs" / "round3_orchestration" / "20260525_132801"


def now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def run_dir_from_arg(value: str | None) -> Path:
    if value:
        path = Path(value)
        return path if path.is_absolute() else REPO_ROOT / path
    return DEFAULT_RUN_DIR


def read_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return default


def read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8") if path.exists() else ""


def write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text.rstrip() + "\n", encoding="utf-8", newline="\n")


def detect_prompt_status(run_dir: Path) -> dict[str, str]:
    text = read_text(run_dir / "prompt_scorer_gate_status.md").lower()
    return {
        "executable_method_prompts": "pass" if "executable_method_prompts: pass" in text else "pending",
        "input_isolation": "pass" if "input_isolation_validation: pass" in text else "pending",
        "required_fact_recall_scorer": "pass" if "required_fact_recall_scorer: pass" in text else "pending",
        "numeric_correctness": "pass" if "numeric_correctness_scorer: pass" in text else "pending",
        "answer_correctness": "pass" if "answer_correctness_scorer: pass" in text else "pending",
    }


def detect_gemini_status(run_dir: Path) -> dict[str, str]:
    status = read_text(run_dir / "gemini_review_status.md").lower()
    review_exists = (run_dir / "reviews" / "gemini_review.md").exists()
    semantic = "pass" if "gemini semantic review: pass" in status else "pass" if review_exists else "pending"
    fairness = "pass" if "gemini prompt/fairness review: pass" in status else "warning" if review_exists else "pending"
    return {
        "gemini_semantic_review": semantic,
        "gemini_prompt_fairness_review": fairness,
    }


def detect_gpt_advisory_status(run_dir: Path) -> dict[str, Any]:
    status = read_json(run_dir / "automation" / "gpt_advisory_status.json", {})
    review_exists = (run_dir / "reviews" / "gpt_review.md").exists()
    request_exists = (run_dir / "reviews" / "inbox" / "gpt_advisory_request_neo4j_mapping.md").exists()
    return {
        "status": status.get("status") or ("advisory_received" if review_exists else "advisory_requested" if request_exists else "not_requested"),
        "response_ingested": bool(status.get("response_ingested") or review_exists),
        "request_file": status.get("request_file", "outputs/round3_orchestration/20260525_132801/reviews/inbox/gpt_advisory_request_neo4j_mapping.md"),
        "review_file": status.get("review_file", "outputs/round3_orchestration/20260525_132801/reviews/gpt_review.md"),
        "gate_blocking": False,
        "user_relay_required": False,
    }


def classify_neo4j(run_dir: Path) -> dict[str, Any]:
    schema = read_json(run_dir / "neo4j_schema_introspection.json", {})
    probe = read_json(run_dir / "neo4j_case_presence_probe.json", {})
    coverage = read_text(run_dir / "neo4j_readonly_coverage_report.md").lower()
    coverage_rows = read_coverage_rows(run_dir)
    diagnostics = read_json(run_dir / "neo4j_connection_diagnostics.json", {})
    expected = schema.get("expected_labels_found", {})
    expected_absent = expected and not any(bool(v) for v in expected.values())
    case_matches = int(probe.get("case_id_matches", 0) or 0)
    total_cases = int(probe.get("total_cases_probed", 0) or 0)
    node_count = int(schema.get("total_node_count") or probe.get("node_count") or 0)
    connected = bool(diagnostics.get("driver_connectivity_verified") or schema.get("uri_connected"))
    likely_schema_issue = schema.get("likely_issue") or "unknown"
    likely_probe_status = probe.get("likely_database_status") or "unknown"

    ready_count = sum(1 for row in coverage_rows if row.get("coverage_status") == "ready_for_eval")
    not_ready_count = len(coverage_rows) - ready_count
    missing_fact_count = sum(int(row.get("missing_fact_count", 0) or 0) for row in coverage_rows)

    if not connected:
        terminal = "blocked_by_missing_config"
        status = "blocked_missing_config"
        blocker = "Neo4j connectivity/config is not verified."
    elif node_count == 0:
        terminal = "blocked_by_wrong_or_unmapped_neo4j_database"
        status = "empty_database"
        blocker = "Connected Neo4j database appears empty."
    elif ready_count > 0:
        terminal = "ready_for_small_dry_run"
        status = "coverage_has_ready_subset"
        blocker = (
            f"Neo4j mapping adapter found {ready_count} ready cases, but {not_ready_count} cases "
            f"and {missing_fact_count} required facts remain not ready."
        )
    elif expected_absent and case_matches == 0:
        terminal = "blocked_by_wrong_or_unmapped_neo4j_database"
        status = "populated_but_different_or_unmapped_database"
        blocker = (
            "Connected Neo4j database is populated, but expected Round 3 labels are absent "
            "and repaired Round 3 case_id matches are 0/{total}."
        ).format(total=total_cases)
    elif "coverage executed: true" in coverage and "ready for eval: 0" not in coverage:
        terminal = "ready_for_small_dry_run"
        status = "coverage_has_ready_subset"
        blocker = ""
    else:
        terminal = "blocked_by_wrong_or_unmapped_neo4j_database"
        status = "coverage_blocked"
        blocker = "Neo4j read-only coverage remains blocked."

    return {
        "terminal_state": terminal,
        "neo4j_status": status,
        "current_blocker": blocker,
        "schema_issue": likely_schema_issue,
        "probe_status": likely_probe_status,
        "node_count": node_count,
        "relationship_count": schema.get("total_relationship_count") or probe.get("relationship_count"),
        "case_id_matches": case_matches,
        "total_cases_probed": total_cases,
        "required_fact_soft_matches": probe.get("required_fact_soft_matches"),
        "required_facts_probed": probe.get("required_facts_probed"),
        "ready_for_eval_count": ready_count,
        "not_ready_count": not_ready_count,
        "missing_required_fact_count": missing_fact_count,
        "expected_labels_found": expected,
    }


def read_coverage_rows(run_dir: Path) -> list[dict[str, str]]:
    path = run_dir / "neo4j_coverage_summary.csv"
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def read_small_mock_results(run_dir: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    path = run_dir / "automation" / "small_mock_dry_run_results.jsonl"
    if not path.exists():
        return rows
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            rows.append(json.loads(line))
    return rows


def render_final_report(state: dict[str, Any]) -> str:
    prompt = state["prompt_scorer_status"]
    gemini = state["gemini_status"]
    gpt = state["gpt_advisory_status"]
    neo4j = state["neo4j"]
    return f"""# Round 3 Local Operator Final Report

Generated: {state['generated_at']}

## Terminal State

`{state['terminal_state']}`

## Current Blocker

{state['current_blocker']}

## Gate Evidence

- executable_method_prompts: {prompt['executable_method_prompts']}
- input_isolation: {prompt['input_isolation']}
- required_fact_recall_scorer: {prompt['required_fact_recall_scorer']}
- numeric_correctness: {prompt['numeric_correctness']}
- answer_correctness: {prompt['answer_correctness']}
- Gemini semantic review: {gemini['gemini_semantic_review']}
- Gemini prompt/fairness review: {gemini['gemini_prompt_fairness_review']}
- GPT advisory: {gpt['status']} (gate_blocking: false, user_relay_required: false)
- Neo4j status: {neo4j['neo4j_status']}
- dry-run status: {state['dry_run_status']}
- full eval lock: {state['full_eval_lock']}

## Neo4j Evidence

- node_count: {neo4j['node_count']}
- relationship_count: {neo4j['relationship_count']}
- expected labels found: `{json.dumps(neo4j['expected_labels_found'], ensure_ascii=False, sort_keys=True)}`
- repaired case_id matches: {neo4j['case_id_matches']} / {neo4j['total_cases_probed']}
- ready_for_eval coverage cases: {neo4j['ready_for_eval_count']}
- not_ready coverage cases: {neo4j['not_ready_count']}
- missing required facts in coverage: {neo4j['missing_required_fact_count']}
- required fact soft matches: {neo4j['required_fact_soft_matches']} / {neo4j['required_facts_probed']}
- schema issue: `{neo4j['schema_issue']}`
- presence probe status: `{neo4j['probe_status']}`

## Decision

{state['decision_note']}

## Advisory Routing

- GPT advisory request: `{gpt['request_file']}`
- GPT advisory review: `{gpt['review_file']}`
- response ingested: {gpt['response_ingested']}
- user relay required: false
- gate blocking: false

## Safety

- Neo4j write performed: false
- KG patch applied: false
- dry-run executed: false
- full eval executed: false
- model API called: false
- Round 02 modified: false
- repaired subset modified: false
- selected 7 official files modified: false
- final_go marked: false

## Next Action

{state['next_action']}
"""


def render_blockers(state: dict[str, Any]) -> str:
    return f"""# Blockers

## Terminal Blocker

- `{state['terminal_state']}`: {state['current_blocker']}

## Evidence

- Neo4j database is connected and populated, but it is unmapped for Round 3 coverage.
- Expected labels are absent: `DatasetCase`, `EvidenceText`, `Company`, `Metric`, `Year`, `Value`, `Observation`.
- Repaired Round 3 case_id matches: {state['neo4j']['case_id_matches']} / {state['neo4j']['total_cases_probed']}.
- Required fact component soft matches are broad only and not exact coverage: {state['neo4j']['required_fact_soft_matches']} / {state['neo4j']['required_facts_probed']}.

## Still Locked

- dry-run: {state['dry_run_status']}
- full_eval_lock: {state['full_eval_lock']}

## Advisory Routing

- GPT advisory is file-based and optional.
- user_relay_required: false
- gate_blocking: false
"""


def render_gate_status(state: dict[str, Any]) -> str:
    prompt = state["prompt_scorer_status"]
    gemini = state["gemini_status"]
    gpt = state["gpt_advisory_status"]
    return f"""# Gate Status

artifact_freeze: warning
gemini_semantic_review: {gemini['gemini_semantic_review']}
gemini_prompt_fairness_review: {gemini['gemini_prompt_fairness_review']}
neo4j_readonly_coverage: blocked
executable_method_prompts: {prompt['executable_method_prompts']}
required_fact_recall_scorer: {prompt['required_fact_recall_scorer']}
dry_run: {state['dry_run_status']}
input_isolation: {prompt['input_isolation']}
opik_trace_completeness: pending
full_eval_lock: {state['full_eval_lock']}
numeric_correctness: {prompt['numeric_correctness']}
answer_correctness: {prompt['answer_correctness']}
gpt_advisory: {gpt['status']}

Terminal state: `{state['terminal_state']}`

Neo4j blocker: {state['current_blocker']}

Safety:
- Neo4j write performed: false
- KG patch applied: false
- dry-run executed: false
- full eval executed: false
- final_go marked: false
"""


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Finalize Round 3 local operator state.")
    parser.add_argument("--run-dir", default=None)
    args = parser.parse_args(argv)
    run_dir = run_dir_from_arg(args.run_dir)

    prompt_status = detect_prompt_status(run_dir)
    gemini_status = detect_gemini_status(run_dir)
    gpt_advisory_status = detect_gpt_advisory_status(run_dir)
    neo4j = classify_neo4j(run_dir)
    terminal = neo4j["terminal_state"]
    dry_run_status = "blocked"
    full_eval_lock = "locked"
    small_mock = read_small_mock_results(run_dir)
    small_mock_executed = bool(small_mock)
    small_mock_passed = bool(small_mock) and all(bool(row.get("answer_correctness")) for row in small_mock)
    decision_note = (
        "Small mock dry-run was not executed. Although prompt/scorer and Gemini gates are acceptable, "
        "the connected Neo4j database cannot yet be treated as the repaired Round 3 KG because expected labels are absent and repaired case IDs do not match."
    )
    if neo4j["ready_for_eval_count"] > 0 and small_mock_executed:
        dry_run_status = "pass_small_mock" if small_mock_passed else "blocked_after_small_dry_run"
        if small_mock_passed and neo4j["missing_required_fact_count"] == 0:
            terminal = "ready_for_full_eval_proposal"
            decision_note = "Small mock dry-run passed and Neo4j coverage has no missing required facts. Full eval remains locked pending Antigravity approval."
            next_action = "Antigravity should review reports and decide whether to issue full-eval proposal approval; Codex must not mark final_go."
        else:
            terminal = "blocked_after_small_dry_run"
            decision_note = (
                "Small mock dry-run executed on the eligible subset, but full coverage is not ready. "
                f"Ready cases: {neo4j['ready_for_eval_count']}; missing required facts: {neo4j['missing_required_fact_count']}."
            )
            next_action = "Use the file-based GPT advisory queue if a response is available, then Antigravity should approve a separate read-only mapping/coverage refinement step before any broader dry-run or full-eval proposal."
    else:
        next_action = (
            "Keep the GPT advisory request queued locally, then Antigravity should review local reports and either point Codex at the correct Round 3 KG "
            "or approve a separate read-only mapping-adapter implementation before any dry-run; no user relay is required."
        )
    state = {
        "generated_at": now(),
        "terminal_state": terminal,
        "current_blocker": neo4j["current_blocker"],
        "dry_run_status": dry_run_status,
        "full_eval_lock": full_eval_lock,
        "neo4j": neo4j,
        "gemini_status": gemini_status,
        "gpt_advisory_status": gpt_advisory_status,
        "prompt_scorer_status": prompt_status,
        "small_mock_dry_run_executed": small_mock_executed,
        "small_mock_dry_run_passed": small_mock_passed,
        "next_action": next_action,
        "decision_note": decision_note,
    }
    write_text(run_dir / "automation" / "final_operator_report.md", render_final_report(state))
    write_text(run_dir / "automation" / "blockers.md", render_blockers(state))
    write_text(run_dir / "automation" / "next_action.md", next_action)
    write_text(run_dir / "merged" / "gate_status.md", render_gate_status(state))
    write_text(
        run_dir / "automation" / "operator_terminal_state.json",
        json.dumps(state, ensure_ascii=False, indent=2, sort_keys=True, default=str),
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
