"""Round 3 local file-based operator loop.

This script is a safe runtime controller for the existing file-based
orchestration rail. It prepares local state, advisory queue files, ready-case
plans, and coverage backlog reports. It never executes full eval, never writes
Neo4j/KG patches, and never calls model APIs by default.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_RUN_DIR = REPO_ROOT / "outputs" / "round3_orchestration" / "20260525_132801"
CASES_PATH = REPO_ROOT / "outputs" / "round3_case_factory_repaired" / "eval_ready_cases.jsonl"
COVERAGE_SUMMARY_NAME = "neo4j_coverage_summary.csv"
DEFAULT_METHODS = ("vector_only", "graph_facts_only", "hybrid_vector_graph", "gold_context")
ENV_FILES = (REPO_ROOT / ".env", REPO_ROOT.parent / ".env")

TERMINAL_STATES = {
    "ready_for_partial_eval_approval",
    "ready_for_coverage_refinement_approval",
    "blocked_wrong_or_unmapped_neo4j_database",
    "blocked_missing_neo4j_config",
    "blocked_unknown_action",
    "blocked_safety_violation",
    "blocked_no_progress",
    "blocked_failed_tests",
    "blocked_missing_openai_api_key",
    "complete_non_dangerous_setup",
}

GATES = (
    "artifact_freeze",
    "gemini_semantic_review",
    "gemini_prompt_fairness_review",
    "neo4j_readonly_coverage",
    "executable_method_prompts",
    "required_fact_recall_scorer",
    "dry_run",
    "input_isolation",
    "opik_trace_completeness",
    "full_eval_lock",
)


@dataclass
class RuntimeOptions:
    run_dir: Path
    max_steps: int
    dry_run_mode: str
    allow_readonly_neo4j: bool
    model_api_allowed: bool
    dangerous_actions_allowed: bool


def now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def rel(path: Path) -> str:
    try:
        return path.resolve().relative_to(REPO_ROOT.resolve()).as_posix()
    except ValueError:
        return str(path)


def write_text(path: Path, text: str) -> bool:
    path.parent.mkdir(parents=True, exist_ok=True)
    normalized = text.rstrip() + "\n"
    old = path.read_text(encoding="utf-8") if path.exists() else None
    if old == normalized:
        return False
    path.write_text(normalized, encoding="utf-8", newline="\n")
    return True


def write_json(path: Path, data: Any) -> bool:
    return write_text(path, json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True))


def append_jsonl(path: Path, rows: list[dict[str, Any]]) -> bool:
    path.parent.mkdir(parents=True, exist_ok=True)
    body = "".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in rows)
    old = path.read_text(encoding="utf-8") if path.exists() else None
    if old == body:
        return False
    path.write_text(body, encoding="utf-8", newline="\n")
    return True


def read_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return default


def env_value(name: str) -> str:
    if os.environ.get(name):
        return os.environ[name]
    for path in ENV_FILES:
        if not path.exists():
            continue
        for raw_line in path.read_text(encoding="utf-8").splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            if key.strip() == name:
                return value.strip().strip("\"'")
    return ""


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    if not path.exists():
        return rows
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            rows.append({"raw": line, "parse_error": True})
    return rows


def write_csv(path: Path, fieldnames: list[str], rows: list[dict[str, Any]]) -> bool:
    from io import StringIO

    buffer = StringIO()
    writer = csv.DictWriter(buffer, fieldnames=fieldnames)
    writer.writeheader()
    for row in rows:
        writer.writerow({field: row.get(field, "") for field in fieldnames})
    return write_text(path, buffer.getvalue().rstrip())


def run_dir_from_arg(value: str | None) -> Path:
    if not value:
        return DEFAULT_RUN_DIR
    path = Path(value)
    return path if path.is_absolute() else REPO_ROOT / path


def call_orchestrator(mode: str, run_dir: Path) -> dict[str, Any]:
    cmd = [
        sys.executable,
        str(REPO_ROOT / "scripts" / "round3_orchestrate.py"),
        mode,
        "--run-dir",
        rel(run_dir),
    ]
    proc = subprocess.run(cmd, cwd=REPO_ROOT, capture_output=True, text=True)
    return {
        "mode": mode,
        "returncode": proc.returncode,
        "stdout": proc.stdout.strip(),
        "stderr": proc.stderr.strip(),
        "ok": proc.returncode == 0,
    }


def call_eval_loop(run_dir: Path, approval: dict[str, str]) -> dict[str, Any]:
    manifest = approval.get("allowed_manifest") or rel(run_dir / "automation" / "ready_partial_eval_manifest.json")
    cases = approval.get("allowed_cases") or rel(run_dir / "automation" / "ready_cases.jsonl")
    provider = approval.get("provider") or "mock"
    methods = [item.strip() for item in approval.get("methods", ",".join(DEFAULT_METHODS)).split(",") if item.strip()]
    max_cases = approval.get("max_cases", "")
    cmd = [
        sys.executable,
        str(REPO_ROOT / "scripts" / "round3_eval_loop.py"),
        "--run-dir",
        rel(run_dir),
        "--manifest",
        manifest,
        "--cases",
        cases,
        "--provider",
        provider,
        "--methods",
        *methods,
    ]
    if max_cases:
        cmd.extend(["--max-cases", max_cases])
    proc = subprocess.run(cmd, cwd=REPO_ROOT, capture_output=True, text=True)
    return {
        "mode": "partial-eval",
        "returncode": proc.returncode,
        "stdout": proc.stdout.strip(),
        "stderr": proc.stderr.strip(),
        "ok": proc.returncode == 0,
    }


def call_readonly_coverage(run_dir: Path) -> dict[str, Any]:
    cmd = [
        sys.executable,
        str(REPO_ROOT / "scripts" / "round3_neo4j_readonly_coverage.py"),
        "--run-dir",
        rel(run_dir),
    ]
    proc = subprocess.run(cmd, cwd=REPO_ROOT, capture_output=True, text=True)
    return {
        "mode": "readonly-coverage-refinement",
        "returncode": proc.returncode,
        "stdout": proc.stdout.strip(),
        "stderr": proc.stderr.strip(),
        "ok": proc.returncode == 0,
    }


def approval_false_flags(path: Path, flags: tuple[str, ...]) -> bool:
    data = parse_key_value_file(path)
    if not data and path.exists():
        text = path.read_text(encoding="utf-8").lower()
        return all(f"{flag}: false" in text or f"{flag}=false" in text for flag in flags)
    return all(data.get(flag, "false").strip().lower() == "false" for flag in flags)


def parse_key_value_file(path: Path) -> dict[str, str]:
    data: dict[str, str] = {}
    if not path.exists():
        return data
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        data[key.strip()] = value.strip()
    return data


def parse_bool(value: str | None, *, default: bool = False) -> bool:
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "y", "on"}


def is_real_provider_approval(approval: dict[str, str]) -> bool:
    return (
        parse_bool(approval.get("approved_by_user"))
        and approval.get("scope") == "ready_subset_real_provider_partial_eval_only"
        and approval.get("provider") == "openai"
        and parse_bool(approval.get("model_api_allowed"))
        and approval.get("full_eval_allowed", "false").lower() == "false"
        and approval.get("neo4j_write_allowed", "false").lower() == "false"
        and approval.get("kg_patch_allowed", "false").lower() == "false"
    )


def is_retired_gemini_approval(approval: dict[str, str]) -> bool:
    return approval.get("scope") == "ready_subset_real_provider_partial_eval_only" and approval.get("provider") == "gemini"


def write_report_only_gpt_package(run_dir: Path, reason: str) -> dict[str, Any]:
    request_path = run_dir / "reviews" / "inbox" / "gpt_report_only_ready_subset_review.md"
    eval_status = read_json(run_dir / "automation" / "real_provider_partial_eval_status.json", {})
    body = f"""---
reviewer: gpt
review_type: report_only_advisory
status: requested
round3_decision: advisory_only
---

# GPT Report-Only Advisory: Round 3 Ready Subset

Gemini is retired for Round 3 and must not be called. This packet is report-only because `{reason}`.

## Boundary

- Advisory only; do not issue final gate authority.
- Do not approve or run full evaluation.
- Do not request Gemini.
- Do not authorize Neo4j writes or KG patches.
- Full eval remains locked.

## Current Evidence

- Real-provider eval status: `{rel(run_dir / 'automation' / 'real_provider_partial_eval_status.json')}`
- Latest ready partial report: `{eval_status.get('eval_run_dir', 'outputs/round3_eval_runs/ready_partial_real_20260526_134758')}/report.md`
- Gate status: `{rel(run_dir / 'merged' / 'gate_status.md')}`
- Blockers: `{rel(run_dir / 'automation' / 'blockers.md')}`
"""
    write_text(request_path, body)
    status = {
        "generated_at": now(),
        "terminal_state": "report_only_gpt_final_review",
        "approval_file_detected": True,
        "approval_scope_parsed": "ready_subset_real_provider_partial_eval_only",
        "provider_requested": "openai",
        "openai_api_key_detected": False,
        "real_provider_partial_eval_executed": False,
        "eval_loop_invoked": False,
        "full_eval_executed": False,
        "neo4j_write_performed": False,
        "kg_patch_applied": False,
        "model_api_called": False,
        "report_only_package": rel(request_path),
        "next_action": f"Review `{rel(request_path)}` as GPT advisory packet; full eval remains locked.",
    }
    write_json(run_dir / "automation" / "real_provider_partial_eval_status.json", status)
    write_text(
        run_dir / "automation" / "real_provider_partial_eval_status.md",
        "# Real-Provider Partial Eval Status\n\n"
        "- terminal_state: `report_only_gpt_final_review`\n"
        "- provider_requested: `openai`\n"
        "- openai_api_key_detected: false\n"
        "- real_provider_partial_eval_executed: false\n"
        "- model_api_called: false\n",
    )
    write_text(run_dir / "automation" / "next_action.md", status["next_action"])
    write_text(run_dir / "automation" / "blockers.md", "# Blockers\n\n- OpenAI provider key is unavailable; generated report-only GPT advisory package.\n")
    return status


def write_retired_gemini_status(run_dir: Path) -> dict[str, Any]:
    status = {
        "generated_at": now(),
        "terminal_state": "gemini_provider_retired",
        "provider_requested": "gemini",
        "provider_allowed": False,
        "historical_artifacts_only": True,
        "real_provider_partial_eval_executed": False,
        "model_api_called": False,
        "full_eval_executed": False,
        "neo4j_write_performed": False,
        "kg_patch_applied": False,
        "next_action": "Use provider=openai if OPENAI_API_KEY is available; otherwise generate report-only GPT advisory package.",
    }
    write_json(run_dir / "automation" / "real_provider_partial_eval_status.json", status)
    write_text(
        run_dir / "automation" / "real_provider_partial_eval_status.md",
        "# Real-Provider Partial Eval Status\n\n"
        "- terminal_state: `gemini_provider_retired`\n"
        "- provider_requested: `gemini`\n"
        "- provider_allowed: false\n"
        "- historical_artifacts_only: true\n"
        "- model_api_called: false\n",
    )
    return status


def partial_eval_status(run_dir: Path) -> dict[str, Any]:
    return read_json(run_dir / "automation" / "partial_eval_status.json", {})


def real_provider_partial_eval_status(run_dir: Path) -> dict[str, Any]:
    return read_json(run_dir / "automation" / "real_provider_partial_eval_status.json", {})


def coverage_refinement_status(run_dir: Path) -> dict[str, Any]:
    return read_json(run_dir / "automation" / "coverage_refinement_status.json", {})


def remediation_plan_status(run_dir: Path) -> dict[str, Any]:
    return read_json(run_dir / "automation" / "ontology_coverage_remediation_plan.json", {})


def load_coverage_rows(run_dir: Path) -> list[dict[str, Any]]:
    path = run_dir / COVERAGE_SUMMARY_NAME
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8", newline="") as f:
        for row in csv.DictReader(f):
            parsed = dict(row)
            for key in ("required_fact_count", "matched_fact_count", "missing_fact_count"):
                try:
                    parsed[key] = int(parsed.get(key) or 0)
                except ValueError:
                    parsed[key] = 0
            rows.append(parsed)
    return rows


def load_case_map() -> dict[str, dict[str, Any]]:
    cases: dict[str, dict[str, Any]] = {}
    if not CASES_PATH.exists():
        return cases
    for line in CASES_PATH.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            case = json.loads(line)
        except json.JSONDecodeError:
            continue
        case_id = str(case.get("case_id", ""))
        if case_id:
            cases[case_id] = case
    return cases


def load_coverage_results(run_dir: Path) -> dict[str, dict[str, Any]]:
    results: dict[str, dict[str, Any]] = {}
    path = run_dir / "neo4j_coverage_results.jsonl"
    if not path.exists():
        return results
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        case_id = str(row.get("case_id", ""))
        if case_id:
            results[case_id] = row
    return results


def read_state(run_dir: Path) -> dict[str, Any]:
    files = {
        "next_action": run_dir / "automation" / "next_action.md",
        "blockers": run_dir / "automation" / "blockers.md",
        "operator_terminal_state": run_dir / "automation" / "operator_terminal_state.json",
        "gate_status": run_dir / "merged" / "gate_status.json",
        "gpt_advisory_status": run_dir / "automation" / "gpt_advisory_status.md",
        "coverage_summary": run_dir / COVERAGE_SUMMARY_NAME,
        "small_mock_dry_run_report": run_dir / "automation" / "small_mock_dry_run_report.md",
    }
    return {
        "files": {
            name: {
                "path": rel(path),
                "exists": path.exists(),
                "length_bytes": path.stat().st_size if path.exists() else 0,
            }
            for name, path in files.items()
        },
        "gate_status": read_json(files["gate_status"], {}),
        "operator_terminal_state": read_json(files["operator_terminal_state"], {}),
        "coverage_rows": load_coverage_rows(run_dir),
        "small_mock_dry_run_present": files["small_mock_dry_run_report"].exists(),
    }


def allowed_registry(options: RuntimeOptions) -> dict[str, dict[str, Any]]:
    approval_dir = options.run_dir / "approvals"
    return {
        "collect": {"allowed": True, "dangerous": False},
        "merge": {"allowed": True, "dangerous": False},
        "gate_refresh": {"allowed": True, "dangerous": False},
        "advisory_sync": {"allowed": True, "dangerous": False},
        "prepare_ready_partial_eval_inputs": {"allowed": True, "dangerous": False},
        "generate_coverage_backlog": {"allowed": True, "dangerous": False},
        "generate_non_mutating_ontology_coverage_remediation_plan": {"allowed": True, "dangerous": False},
        "readonly_coverage_refinement": {
            "allowed": options.allow_readonly_neo4j or (approval_dir / "allow_neo4j_readonly_coverage.txt").exists(),
            "dangerous": False,
        },
        "small_mock_dry_run": {
            "allowed": (approval_dir / "allow_small_mock_dry_run.txt").exists(),
            "dangerous": False,
            "executed_in_setup": False,
        },
        "partial_eval_execution": {
            "allowed": (approval_dir / "allow_partial_eval.txt").exists(),
            "dangerous": False,
            "executed_in_setup": False,
        },
        "full_eval": {"allowed": False, "dangerous": True, "executed_in_setup": False},
        "kg_patch": {"allowed": False, "dangerous": True, "executed_in_setup": False},
    }


def prepare_ready_partial_eval_inputs(run_dir: Path) -> tuple[dict[str, Any], list[Path]]:
    coverage_rows = load_coverage_rows(run_dir)
    ready_rows = [row for row in coverage_rows if row.get("coverage_status") == "ready_for_eval"]
    case_map = load_case_map()
    ready_case_ids = [str(row.get("case_id")) for row in ready_rows if row.get("case_id")]
    ready_cases = []
    for case_id in ready_case_ids:
        case = dict(case_map.get(case_id, {"case_id": case_id}))
        coverage = next((row for row in ready_rows if row.get("case_id") == case_id), {})
        case["round3_operator_coverage"] = coverage
        ready_cases.append(case)

    manifest = {
        "generated_at": now(),
        "run_dir": rel(run_dir),
        "source_coverage_summary": rel(run_dir / COVERAGE_SUMMARY_NAME),
        "source_cases": rel(CASES_PATH),
        "ready_case_count": len(ready_cases),
        "ready_case_ids": ready_case_ids,
        "execution_approved": False,
        "partial_eval_executed": False,
        "full_eval_executed": False,
        "model_api_called": False,
        "opik_production_logging_enabled": False,
        "notes": "Plan and manifest only. No evaluation was executed.",
    }
    plan = f"""# Ready-Case Partial Eval Plan

Generated: {manifest['generated_at']}

## Scope

- Ready cases: {len(ready_cases)}
- Source coverage summary: `{manifest['source_coverage_summary']}`
- Source repaired cases: `{manifest['source_cases']}`
- Partial eval executed: false
- Full eval executed: false
- Model API called: false
- Opik production logging enabled: false

## Ready Case IDs

{chr(10).join(f"- `{case_id}`" for case_id in ready_case_ids) if ready_case_ids else "- None"}

## Approval Boundary

This file prepares inputs only. Executing any partial evaluation requires `approvals/allow_partial_eval.txt` and a separate operator step. Full evaluation remains locked.
"""
    changed_paths: list[Path] = []
    for path, changed in (
        (run_dir / "automation" / "ready_partial_eval_plan.md", write_text(run_dir / "automation" / "ready_partial_eval_plan.md", plan)),
        (run_dir / "automation" / "ready_partial_eval_manifest.json", write_json(run_dir / "automation" / "ready_partial_eval_manifest.json", manifest)),
        (run_dir / "automation" / "ready_cases.jsonl", append_jsonl(run_dir / "automation" / "ready_cases.jsonl", ready_cases)),
    ):
        if changed:
            changed_paths.append(path)
    return manifest, changed_paths


def generate_coverage_backlog(run_dir: Path) -> tuple[dict[str, Any], list[Path]]:
    coverage_rows = load_coverage_rows(run_dir)
    coverage_results = load_coverage_results(run_dir)
    backlog_rows: list[dict[str, Any]] = []
    for row in coverage_rows:
        if row.get("coverage_status") == "ready_for_eval":
            continue
        case_id = str(row.get("case_id", ""))
        result = coverage_results.get(case_id, {})
        backlog_rows.append(
            {
                "case_id": case_id,
                "split": row.get("split", ""),
                "coverage_status": row.get("coverage_status", "unknown"),
                "required_fact_count": row.get("required_fact_count", 0),
                "matched_fact_count": row.get("matched_fact_count", 0),
                "missing_fact_count": row.get("missing_fact_count", 0),
                "missing_facts": result.get("missing_facts", []),
                "recommended_action": "read-only mapping/coverage refinement; no KG patch auto-apply",
            }
        )
    total_missing = sum(int(row.get("missing_fact_count", 0)) for row in backlog_rows)
    summary = {
        "generated_at": now(),
        "backlog_case_count": len(backlog_rows),
        "missing_required_fact_count": total_missing,
        "source_coverage_summary": rel(run_dir / COVERAGE_SUMMARY_NAME),
        "neo4j_write_performed": False,
        "kg_patch_applied": False,
    }
    md_rows = [
        "# Coverage Refinement Backlog",
        "",
        f"Generated: {summary['generated_at']}",
        "",
        f"- Backlog cases: {len(backlog_rows)}",
        f"- Missing required facts: {total_missing}",
        "- Neo4j write performed: false",
        "- KG patch applied: false",
        "",
        "| Case | Split | Status | Matched | Missing |",
        "|---|---|---|---:|---:|",
    ]
    for row in backlog_rows:
        md_rows.append(
            f"| `{row['case_id']}` | {row['split']} | {row['coverage_status']} | {row['matched_fact_count']} | {row['missing_fact_count']} |"
        )
    md_rows.extend(
        [
            "",
            "## Boundary",
            "",
            "This backlog is a read-only planning artifact. It does not apply KG patches and does not approve broader evaluation.",
        ]
    )
    changed_paths: list[Path] = []
    for path, changed in (
        (run_dir / "automation" / "coverage_refinement_backlog.md", write_text(run_dir / "automation" / "coverage_refinement_backlog.md", "\n".join(md_rows))),
        (run_dir / "automation" / "coverage_refinement_backlog.jsonl", append_jsonl(run_dir / "automation" / "coverage_refinement_backlog.jsonl", backlog_rows)),
    ):
        if changed:
            changed_paths.append(path)
    return summary, changed_paths


def coverage_counts(run_dir: Path) -> dict[str, int]:
    rows = load_coverage_rows(run_dir)
    ready = sum(1 for row in rows if row.get("coverage_status") == "ready_for_eval")
    backlog = sum(1 for row in rows if row.get("coverage_status") != "ready_for_eval")
    missing = sum(int(row.get("missing_fact_count", 0) or 0) for row in rows)
    return {
        "total_cases": len(rows),
        "ready_cases": ready,
        "backlog_cases": backlog,
        "missing_required_facts": missing,
    }


def parse_notes(notes: Any) -> dict[str, Any]:
    if isinstance(notes, dict):
        return notes
    if not notes:
        return {}
    try:
        parsed = json.loads(str(notes))
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def classify_failure_reason(result: dict[str, Any], row: dict[str, Any]) -> str:
    notes = parse_notes(result.get("notes", ""))
    per_fact = notes.get("per_fact", [])
    if not isinstance(per_fact, list):
        per_fact = []
    missing_rows = [item for item in per_fact if isinstance(item, dict) and not item.get("matched")]
    observation_candidates = int(notes.get("observation_candidates", 0) or 0)
    missing_count = int(row.get("missing_fact_count", result.get("missing_fact_count", 0)) or 0)
    matched_count = int(row.get("matched_fact_count", result.get("matched_fact_count", 0)) or 0)
    status = str(row.get("coverage_status") or result.get("coverage_status") or "")
    if "company_ticker" in status:
        return "missing_company_or_entity_mapping"
    if observation_candidates == 0 and matched_count == 0 and missing_count > 0:
        return "missing_case_id_mapping"
    if missing_rows and all(item.get("company_ticker_sensitive") for item in missing_rows):
        return "missing_company_or_entity_mapping"
    if missing_rows and all(item.get("metric_sensitive") for item in missing_rows):
        return "missing_metric_mapping"
    if missing_rows and all(item.get("value_sensitive") for item in missing_rows):
        return "missing_value_or_unit_mapping"
    if observation_candidates == 0 and missing_count > 0:
        return "missing_observation_relationship"
    if 0 < matched_count < missing_count + matched_count:
        return "ambiguous_mapping"
    return "unknown"


def priority_for_backlog(row: dict[str, Any], reason: str) -> str:
    missing = int(row.get("missing_fact_count", 0) or 0)
    if reason in {"missing_case_id_mapping", "missing_company_or_entity_mapping"} and missing >= 5:
        return "P0"
    if missing >= 3:
        return "P1"
    return "P2"


def generate_non_mutating_ontology_coverage_remediation_plan(run_dir: Path) -> tuple[dict[str, Any], list[Path]]:
    coverage_rows = load_coverage_rows(run_dir)
    results = load_coverage_results(run_dir)
    backlog_rows = [row for row in coverage_rows if row.get("coverage_status") != "ready_for_eval"]
    ready_count = sum(1 for row in coverage_rows if row.get("coverage_status") == "ready_for_eval")
    missing_total = sum(int(row.get("missing_fact_count", 0) or 0) for row in backlog_rows)
    breakdown_rows: list[dict[str, Any]] = []
    reason_summary: dict[str, dict[str, int]] = {}
    for row in backlog_rows:
        case_id = str(row.get("case_id", ""))
        result = results.get(case_id, {})
        notes = parse_notes(result.get("notes", ""))
        reason = classify_failure_reason(result, row)
        missing = int(row.get("missing_fact_count", 0) or 0)
        reason_summary.setdefault(reason, {"case_count": 0, "missing_required_fact_count": 0})
        reason_summary[reason]["case_count"] += 1
        reason_summary[reason]["missing_required_fact_count"] += missing
        breakdown_rows.append(
            {
                "case_id": case_id,
                "split": row.get("split", ""),
                "coverage_status": row.get("coverage_status", ""),
                "matched_fact_count": row.get("matched_fact_count", 0),
                "missing_fact_count": missing,
                "primary_failure_reason": reason,
                "observation_candidates": notes.get("observation_candidates", ""),
                "priority": priority_for_backlog(row, reason),
            }
        )
    coverage_status = coverage_refinement_status(run_dir)
    ready_before = int(coverage_status.get("ready_cases_before", ready_count) or ready_count)
    ready_gain = ready_count - ready_before
    recommended_route = (
        "ready6_partial_eval_real_provider_approval"
        if ready_count > 0 and missing_total > 0 and ready_gain <= 1
        else "targeted_mapping_adapter_refinement"
    )
    next_action = (
        "Approve ready subset real-provider partial eval."
        if recommended_route == "ready6_partial_eval_real_provider_approval"
        else "Approve one more targeted read-only mapping adapter refinement pass."
    )
    generated = now()
    plan = {
        "generated_at": generated,
        "coverage_refinement_completed": True,
        "ready_cases": ready_count,
        "backlog_cases": len(backlog_rows),
        "missing_required_facts": missing_total,
        "failure_reason_summary": reason_summary,
        "recommended_route": recommended_route,
        "next_action": next_action,
        "safety": {
            "neo4j_write_performed": False,
            "kg_patch_applied": False,
            "dry_run_executed": False,
            "partial_eval_rerun": False,
            "full_eval_executed": False,
            "model_api_called": False,
        },
        "evidence_files": [
            rel(run_dir / "neo4j_coverage_results.jsonl"),
            rel(run_dir / "neo4j_coverage_summary.csv"),
            rel(run_dir / "automation" / "coverage_refinement_backlog.jsonl"),
            rel(run_dir / "automation" / "coverage_refinement_backlog.md"),
            rel(run_dir / "neo4j_schema_introspection.json"),
            rel(run_dir / "neo4j_label_mapping_proposal.json"),
        ],
    }
    changed: list[Path] = []
    breakdown_path = run_dir / "automation" / "backlog_failure_breakdown.csv"
    if write_csv(
        breakdown_path,
        [
            "case_id",
            "split",
            "coverage_status",
            "matched_fact_count",
            "missing_fact_count",
            "primary_failure_reason",
            "observation_candidates",
            "priority",
        ],
        breakdown_rows,
    ):
        changed.append(breakdown_path)
    priority_rows = sorted(breakdown_rows, key=lambda item: (item["priority"], -int(item["missing_fact_count"] or 0), item["case_id"]))
    priority_md = ["# Backlog Case Priority", "", f"Generated: {generated}", "", "| Priority | Case | Missing Facts | Reason |", "|---|---|---:|---|"]
    for row in priority_rows:
        priority_md.append(f"| `{row['priority']}` | `{row['case_id']}` | {row['missing_fact_count']} | `{row['primary_failure_reason']}` |")
    priority_path = run_dir / "automation" / "backlog_case_priority.md"
    if write_text(priority_path, "\n".join(priority_md)):
        changed.append(priority_path)
    plan_json_path = run_dir / "automation" / "ontology_coverage_remediation_plan.json"
    if write_json(plan_json_path, plan):
        changed.append(plan_json_path)
    reason_lines = "\n".join(
        f"- `{reason}`: {counts['case_count']} cases, {counts['missing_required_fact_count']} missing facts"
        for reason, counts in sorted(reason_summary.items())
    )
    plan_md_path = run_dir / "automation" / "ontology_coverage_remediation_plan.md"
    if write_text(
        plan_md_path,
        f"""# Non-Mutating Ontology Coverage Remediation Plan

Generated: {generated}

## Scope

- Ready cases: {ready_count}
- Backlog cases: {len(backlog_rows)}
- Missing required facts: {missing_total}
- Neo4j write performed: false
- KG patch applied: false
- Full eval executed: false
- Partial eval rerun: false
- Model API called: false

## Failure Breakdown

{reason_lines if reason_lines else '- No backlog remains.'}

## Recommendation

- recommended_route: `{recommended_route}`
- next_action: {next_action}

## Rationale

The last read-only coverage refinement produced limited gain. The practical non-mutating route is to use the ready subset for separately approved real-provider partial evaluation while documenting the remaining backlog for ontology or KG ingestion review.
""",
    ):
        changed.append(plan_md_path)
    blockers_path = run_dir / "automation" / "blockers.md"
    if write_text(
        blockers_path,
        "# Blockers\n\n"
        + f"- Coverage backlog remains for {len(backlog_rows)} cases with {missing_total} missing required facts.\n"
        + "- Non-mutating remediation plan generated; no KG patch was applied.\n",
    ):
        changed.append(blockers_path)
    next_path = run_dir / "automation" / "next_action.md"
    if write_text(next_path, next_action):
        changed.append(next_path)
    gate_doc = build_gate_status(read_json(run_dir / "merged" / "gate_status.json", {}))
    gate_doc["gate_status"]["ontology_coverage_remediation_plan"] = "pass"
    gate_doc["gate_status"]["neo4j_readonly_coverage"] = "warning" if backlog_rows else "pass"
    gate_doc["gate_status"]["full_eval_lock"] = "locked"
    gate_json_path = run_dir / "merged" / "gate_status.json"
    if write_json(gate_json_path, gate_doc):
        changed.append(gate_json_path)
    gate_md_path = run_dir / "merged" / "gate_status.md"
    if write_text(gate_md_path, render_gate_status_md(gate_doc)):
        changed.append(gate_md_path)
    final_path = run_dir / "automation" / "final_operator_report.md"
    if write_text(
        final_path,
        f"""# Final Operator Report

Generated: {generated}

## Terminal State

`non_mutating_remediation_plan_ready`

## Coverage State

- Ready cases: {ready_count}
- Backlog cases: {len(backlog_rows)}
- Missing required facts: {missing_total}
- recommended_route: `{recommended_route}`

## Safety

- Neo4j write performed: false
- KG patch applied: false
- dry-run executed this run: false
- partial eval rerun: false
- full eval executed: false
- model API called: false
- full_eval_lock: locked

## Next Action

{next_action}
""",
    ):
        changed.append(final_path)
    return plan, changed


def derive_terminal_state(ready_count: int, backlog_count: int, missing_config: bool) -> str:
    if missing_config:
        return "blocked_missing_neo4j_config"
    if ready_count > 0 and backlog_count > 0:
        return "ready_for_coverage_refinement_approval"
    if ready_count > 0:
        return "ready_for_partial_eval_approval"
    if backlog_count > 0:
        return "blocked_wrong_or_unmapped_neo4j_database"
    return "complete_non_dangerous_setup"


def build_gate_status(existing: dict[str, Any]) -> dict[str, Any]:
    gates = dict(existing.get("gate_status", existing if isinstance(existing, dict) else {}))
    for gate in GATES:
        gates.setdefault(gate, "pending")
    gates["gemini_semantic_review"] = "not_required_historical"
    gates["gemini_prompt_fairness_review"] = "not_required_historical"
    gates["full_eval_lock"] = "locked"
    gates["dry_run"] = gates.get("dry_run", "blocked")
    if gates["dry_run"] not in {"pass", "warning"}:
        gates["dry_run"] = "blocked"
    return {
        "generated_at": now(),
        "run_dir": "outputs/round3_orchestration/20260525_132801",
        "full_eval_approved": False,
        "gate_status": gates,
    }


def render_gate_status_md(gate_doc: dict[str, Any]) -> str:
    rows = ["# Gate Status", "", f"Generated: {gate_doc['generated_at']}", "", "| Gate | Status |", "|---|---|"]
    for gate, status in gate_doc["gate_status"].items():
        rows.append(f"| `{gate}` | `{status}` |")
    rows.extend(["", "- Full evaluation approved: false", "- Full evaluation executed: false"])
    return "\n".join(rows)


def write_runtime_reports(
    options: RuntimeOptions,
    runtime_state: dict[str, Any],
    ready_manifest: dict[str, Any],
    backlog_summary: dict[str, Any],
    command_results: list[dict[str, Any]],
    changed_paths: list[Path],
) -> None:
    run_dir = options.run_dir
    ready_count = int(ready_manifest.get("ready_case_count", 0))
    backlog_count = int(backlog_summary.get("backlog_case_count", 0))
    missing_required = int(backlog_summary.get("missing_required_fact_count", 0))
    terminal_state = derive_terminal_state(ready_count, backlog_count, missing_config=not (run_dir / COVERAGE_SUMMARY_NAME).exists())
    partial_status = partial_eval_status(run_dir)
    partial_executed = bool(partial_status.get("partial_eval_executed"))
    real_status = real_provider_partial_eval_status(run_dir)
    real_provider_executed = bool(real_status.get("real_provider_partial_eval_executed"))
    missing_openai_key = real_status.get("terminal_state") == "report_only_gpt_final_review"
    openai_ready = real_status.get("terminal_state") == "openai_provider_ready_subset_partial_eval"
    gemini_retired = real_status.get("terminal_state") == "gemini_provider_retired" or real_status.get("provider") == "gemini"
    if terminal_state not in TERMINAL_STATES:
        terminal_state = "blocked_unknown_action"

    registry = allowed_registry(options)
    approval_exists = (run_dir / "approvals" / "allow_partial_eval.txt").exists()
    coverage_status = coverage_refinement_status(run_dir)
    coverage_executed = bool(coverage_status.get("coverage_refinement_executed"))
    remediation_status = remediation_plan_status(run_dir)
    remediation_generated = bool(remediation_status.get("recommended_route"))
    coverage_approval_exists = (run_dir / "approvals" / "allow_neo4j_readonly_coverage.txt").exists()
    if openai_ready:
        next_action = str(real_status.get("next_action", "Run ready subset partial eval with provider=openai; Gemini is retired and historical-only. Full eval remains locked."))
        terminal_state = "complete_non_dangerous_setup"
    elif missing_openai_key:
        next_action = str(real_status.get("next_action", "Review report-only GPT advisory package; full eval remains locked."))
        terminal_state = "complete_non_dangerous_setup"
    elif real_provider_executed:
        provider = str(real_status.get("provider", "unknown"))
        if provider == "gemini":
            next_action = "Gemini provider is retired; choose provider=openai if OPENAI_API_KEY is available, otherwise use report-only GPT advisory package."
        else:
            eval_report = str(real_status.get("eval_run_dir", "")) + "/report.md"
            next_action = f"Review `{eval_report}`; full eval remains locked."
        terminal_state = "complete_non_dangerous_setup"
    elif partial_executed:
        if gemini_retired and env_value("OPENAI_API_KEY"):
            next_action = "Run ready subset partial eval with provider=openai; Gemini is retired and historical-only. Full eval remains locked."
            terminal_state = "complete_non_dangerous_setup"
        elif gemini_retired:
            next_action = "Generate report-only GPT advisory package because OPENAI_API_KEY is unavailable; Gemini is retired. Full eval remains locked."
            terminal_state = "complete_non_dangerous_setup"
        elif remediation_generated:
            next_action = str(remediation_status.get("next_action", "Approve ready subset real-provider partial eval."))
            terminal_state = "complete_non_dangerous_setup"
        elif backlog_count and not coverage_approval_exists:
            next_action = "Create approvals/allow_neo4j_readonly_coverage.txt. Full eval remains locked."
            terminal_state = "ready_for_coverage_refinement_approval"
        elif coverage_executed and backlog_count:
            next_action = "Approve ready subset real-provider partial eval."
        elif coverage_executed:
            next_action = "Coverage refinement completed with no backlog; keep full eval locked until separate full-eval proposal authority exists."
        else:
            next_action = "Run approved read-only coverage refinement for backlog cases. Full eval remains locked."
    elif approval_exists:
        next_action = "Run the operator loop to execute approved ready-subset partial evaluation. Full eval remains locked."
    else:
        next_action = (
            "Create approvals/allow_partial_eval.txt to run ready-subset partial evaluation, or create "
            "approvals/allow_neo4j_readonly_coverage.txt to refine coverage. Full eval remains locked."
        )
    blockers = []
    if backlog_count:
        blockers.append(
            f"Coverage refinement backlog remains for {backlog_count} cases with {missing_required} missing required facts."
        )
    if gemini_retired:
        blockers.append("Gemini provider/reviewer path is retired; existing Gemini artifacts are historical and non-blocking.")
    if not ready_count:
        blockers.append("No ready cases are currently available for partial eval planning.")
    if not blockers:
        blockers.append("No blocking local setup issue remains; approval boundary is next.")

    gate_doc = build_gate_status(read_json(run_dir / "merged" / "gate_status.json", {}))
    if partial_executed:
        gate_doc["gate_status"]["partial_eval"] = "pass"
        gate_doc["gate_status"]["dry_run"] = "pass"
        gate_doc["gate_status"]["full_eval_lock"] = "locked"
    write_json(run_dir / "merged" / "gate_status.json", gate_doc)
    write_text(run_dir / "merged" / "gate_status.md", render_gate_status_md(gate_doc))

    status = {
        "generated_at": now(),
        "run_dir": rel(run_dir),
        "terminal_state": terminal_state,
        "current_route": "ready_partial_eval_preparation_plus_coverage_backlog",
        "ready_case_count": ready_count,
        "backlog_case_count": backlog_count,
        "missing_required_fact_count": missing_required,
        "small_mock_dry_run_status": "existing_pass_not_rerun" if runtime_state.get("small_mock_dry_run_present") else "not_run",
        "gpt_advisory_queue_enabled": True,
        "gpt_gate_blocking": False,
        "user_relay_required": False,
        "allowed_action_registry": registry,
        "command_results": command_results,
        "changed_files": [rel(path) for path in changed_paths],
        "safety": {
            "neo4j_write_performed": False,
            "kg_patch_applied": False,
            "dry_run_executed_this_run": False,
            "partial_eval_executed": partial_executed,
            "real_provider_partial_eval_executed": real_provider_executed,
            "coverage_refinement_executed": coverage_executed,
            "remediation_plan_generated": remediation_generated,
            "full_eval_executed": False,
            "round02_modified": False,
            "repaired_subset_modified": False,
            "original_candidate_pool_modified": False,
            "model_api_called": False,
            "full_eval_lock": "locked",
        },
        "next_action": next_action,
    }
    write_json(run_dir / "automation" / "orchestration_runtime_status.json", status)
    write_text(
        run_dir / "automation" / "orchestration_runtime_status.md",
        f"""# Orchestration Runtime Status

Generated: {status['generated_at']}

- terminal_state: `{terminal_state}`
- current_route: `{status['current_route']}`
- ready_case_count: {ready_count}
- backlog_case_count: {backlog_count}
- missing_required_fact_count: {missing_required}
- GPT advisory queue enabled: true
- GPT gate blocking: false
- user relay required: false
- partial eval executed: {str(partial_executed).lower()}
- real provider partial eval executed: {str(real_provider_executed).lower()}
- coverage refinement executed: {str(coverage_executed).lower()}
- full eval lock: locked

## Next Action

{next_action}
""",
    )
    write_text(
        run_dir / "automation" / "blockers.md",
        "# Blockers\n\n" + "\n".join(f"- {item}" for item in blockers) + "\n",
    )
    write_text(run_dir / "automation" / "next_action.md", next_action)
    write_text(
        run_dir / "automation" / "local_operator_loop_report.md",
        f"""# Local Operator Loop Report

Generated: {status['generated_at']}

## Actions

{chr(10).join(f"- `{row['mode']}`: {'pass' if row.get('ok') else 'fail'}" for row in command_results)}
- `prepare_ready_partial_eval_inputs`: pass
- `generate_coverage_backlog`: pass

## Result

- terminal_state: `{terminal_state}`
- ready cases: {ready_count}
- backlog cases: {backlog_count}
- missing required facts: {missing_required}
- small mock dry-run status: `{status['small_mock_dry_run_status']}`
- GPT advisory missing blocks gates: false
- user relay required: false

## Safety

- Neo4j write performed: false
- KG patch applied: false
- dry-run executed this run: false
- partial eval executed: {str(partial_executed).lower()}
- real provider partial eval executed: {str(real_provider_executed).lower()}
- coverage refinement executed: {str(coverage_executed).lower()}
- full eval executed: false
- model API called: false
""",
    )
    write_json(
        run_dir / "automation" / "operator_terminal_state.json",
        {
            "generated_at": status["generated_at"],
            "terminal_state": terminal_state,
            "current_blocker": blockers[0] if blockers else "",
            "dry_run_status": gate_doc["gate_status"].get("dry_run", "blocked"),
            "full_eval_lock": "locked",
            "ready_case_count": ready_count,
            "backlog_case_count": backlog_count,
            "missing_required_fact_count": missing_required,
            "small_mock_dry_run_executed": runtime_state.get("small_mock_dry_run_present", False),
            "small_mock_dry_run_status": status["small_mock_dry_run_status"],
            "gpt_advisory_status": {
                "gate_blocking": False,
                "user_relay_required": False,
            },
            "next_action": next_action,
        },
    )
    write_text(
        run_dir / "automation" / "final_operator_report.md",
        f"""# Final Operator Report

Generated: {status['generated_at']}

## Terminal State

`{terminal_state}`

## Current Route

`{status['current_route']}`

## Evidence-Derived Counts

- Ready cases: {ready_count}
- Backlog cases: {backlog_count}
- Missing required facts: {missing_required}

## Advisory

- GPT advisory queue enabled: true
- GPT advisory blocks gates: false
- User relay required: false

## Safety

- Neo4j write performed: false
- KG patch applied: false
- dry-run executed this run: false
- partial eval executed: {str(partial_executed).lower()}
- real provider partial eval executed: {str(real_provider_executed).lower()}
- coverage refinement executed: {str(coverage_executed).lower()}
- full eval executed: false
- Round 02 modified: false
- repaired subset modified: false
- model API called: false
- full_eval_lock: locked

## Next Action

{next_action}
""",
    )


def refresh_gpt_advisory_request(run_dir: Path) -> bool:
    evidence_files = (
        "automation/orchestration_runtime_status.md",
        "automation/local_operator_loop_report.md",
        "automation/blockers.md",
        "automation/next_action.md",
        "neo4j_schema_introspection_report.md",
        "neo4j_label_mapping_proposal.md",
        "neo4j_readonly_coverage_report.md",
        "neo4j_coverage_gate_status.md",
    )
    parts: list[str] = []
    for name in evidence_files:
        path = run_dir / name
        if not path.exists():
            parts.append(f"## {name}\n\nStatus: missing/pending.\n")
            continue
        parts.append(f"## {name}\n\n```text\n{path.read_text(encoding='utf-8')[:2500]}\n```\n")
    request_path = run_dir / "reviews" / "inbox" / "gpt_advisory_request_neo4j_mapping.md"
    body = f"""---
reviewer: gpt
review_type: advisory
status: requested
round3_decision: advisory_only
---

# GPT Advisory Request: Round 3 Local Operator Evidence

This is a file-based advisory request. GPT is advisory only and is not a gate authority.

## Boundary

- Do not issue go/no-go authority.
- Do not approve or run full evaluation.
- Do not authorize Neo4j writes or KG patches.
- Do not ask the user to relay diagnostics.
- Return advice as `reviews/inbox/gpt_advisory_response_<topic>.md` if a direct configured channel is available.

## Advisory Question

Review the current local evidence and advise Codex/Antigravity on the next safe local step, especially for Neo4j mapping/coverage blockers. Keep full eval locked.

## Local Evidence

{chr(10).join(parts)}
"""
    status = {
        "generated_at": now(),
        "reviewer": "gpt",
        "role": "advisory_only",
        "status": "advisory_requested",
        "request_file": rel(request_path),
        "review_file": rel(run_dir / "reviews" / "gpt_review.md"),
        "response_ingested": False,
        "user_relay_required": False,
        "gate_blocking": False,
        "notes": "Request is queued locally; GPT advisory absence is non-blocking.",
    }
    changed = write_text(request_path, body)
    write_json(run_dir / "automation" / "gpt_advisory_status.json", status)
    write_text(
        run_dir / "automation" / "gpt_advisory_status.md",
        f"""# GPT Advisory Status

- status: `advisory_requested`
- role: advisory_only
- request_file: `{status['request_file']}`
- review_file: `{status['review_file']}`
- response_ingested: false
- user_relay_required: false
- gate_blocking: false

{status['notes']}
""",
    )
    return changed


def run_loop(options: RuntimeOptions) -> dict[str, Any]:
    options.run_dir.mkdir(parents=True, exist_ok=True)
    runtime_state = read_state(options.run_dir)
    registry = allowed_registry(options)
    command_results: list[dict[str, Any]] = []
    changed_paths: list[Path] = []

    if options.dangerous_actions_allowed:
        raise RuntimeError("dangerous actions are not supported by this setup task")
    if options.dry_run_mode != "plan_only":
        raise RuntimeError("only --dry-run-mode plan_only is supported")
    if options.model_api_allowed:
        raise RuntimeError("model API calls are not supported by this setup task")

    action_sequence = ["collect", "merge", "gate_refresh", "advisory_sync"]
    for step, action in enumerate(action_sequence[: options.max_steps], start=1):
        if action not in registry or not registry[action]["allowed"]:
            raise RuntimeError(f"blocked_unknown_action: {action}")
        mode = "advisory-sync" if action == "advisory_sync" else ("gate-refresh" if action == "gate_refresh" else action)
        result = call_orchestrator(mode, options.run_dir)
        result["step"] = step
        command_results.append(result)
        if not result["ok"]:
            raise RuntimeError(f"blocked_failed_tests: {mode}: {result['stderr']}")

    ready_manifest, changed_ready = prepare_ready_partial_eval_inputs(options.run_dir)
    backlog_summary, changed_backlog = generate_coverage_backlog(options.run_dir)
    changed_paths.extend(changed_ready)
    changed_paths.extend(changed_backlog)
    approval_path = options.run_dir / "approvals" / "allow_partial_eval.txt"
    coverage_approval_path = options.run_dir / "approvals" / "allow_neo4j_readonly_coverage.txt"
    approval = parse_key_value_file(approval_path)
    real_status = real_provider_partial_eval_status(options.run_dir)
    if approval_path.exists() and is_retired_gemini_approval(approval):
        write_retired_gemini_status(options.run_dir)
        if not env_value("OPENAI_API_KEY"):
            write_report_only_gpt_package(options.run_dir, "OPENAI_API_KEY is not available")
        real_status = real_provider_partial_eval_status(options.run_dir)
    if approval_path.exists() and is_real_provider_approval(approval) and not real_status.get("real_provider_partial_eval_executed"):
        if not (options.run_dir / "automation" / "ready_cases.jsonl").exists() or not (
            options.run_dir / "automation" / "ready_partial_eval_manifest.json"
        ).exists():
            raise RuntimeError("blocked_failed_tests: missing ready cases or manifest for real-provider partial eval")
        if not env_value("OPENAI_API_KEY"):
            write_report_only_gpt_package(options.run_dir, "OPENAI_API_KEY is not available")
        else:
            status = {
                "generated_at": now(),
                "terminal_state": "openai_provider_ready_subset_partial_eval",
                "provider_requested": "openai",
                "provider_allowed": True,
                "openai_api_key_detected": True,
                "real_provider_partial_eval_executed": False,
                "eval_loop_invoked": False,
                "model_api_called": False,
                "full_eval_executed": False,
                "neo4j_write_performed": False,
                "kg_patch_applied": False,
                "next_action": "Run ready subset partial eval with provider=openai; Gemini is retired and historical-only. Full eval remains locked.",
            }
            write_json(options.run_dir / "automation" / "real_provider_partial_eval_status.json", status)
            write_text(
                options.run_dir / "automation" / "real_provider_partial_eval_status.md",
                "# Real-Provider Partial Eval Status\n\n"
                "- terminal_state: `openai_provider_ready_subset_partial_eval`\n"
                "- provider_requested: `openai`\n"
                "- provider_allowed: true\n"
                "- openai_api_key_detected: true\n"
                "- real_provider_partial_eval_executed: false\n"
                "- eval_loop_invoked: false\n"
                "- model_api_called: false\n"
                "- full_eval_lock: locked\n",
            )
    current_partial_status = partial_eval_status(options.run_dir)
    if approval_path.exists() and not is_real_provider_approval(approval) and not current_partial_status.get("partial_eval_executed"):
        result = call_eval_loop(options.run_dir, approval)
        result["step"] = len(command_results) + 1
        command_results.append(result)
        if not result["ok"]:
            raise RuntimeError(f"blocked_failed_tests: partial-eval: {result['stderr'] or result['stdout']}")
        for mode in ("collect", "merge", "gate-refresh"):
            refresh = call_orchestrator(mode, options.run_dir)
            refresh["step"] = len(command_results) + 1
            command_results.append(refresh)
            if not refresh["ok"]:
                raise RuntimeError(f"blocked_failed_tests: post-eval {mode}: {refresh['stderr']}")
    current_partial_status = partial_eval_status(options.run_dir)
    current_coverage_status = coverage_refinement_status(options.run_dir)
    if (
        current_partial_status.get("partial_eval_executed")
        and backlog_summary.get("backlog_case_count", 0)
        and coverage_approval_path.exists()
        and not current_coverage_status.get("coverage_refinement_executed")
    ):
        if not approval_false_flags(
            coverage_approval_path,
            ("neo4j_write_allowed", "kg_patch_allowed", "partial_eval_allowed", "full_eval_allowed"),
        ):
            raise RuntimeError("blocked_safety_violation: coverage approval contains a dangerous true flag")
        before = coverage_counts(options.run_dir)
        result = call_readonly_coverage(options.run_dir)
        result["step"] = len(command_results) + 1
        command_results.append(result)
        if not result["ok"]:
            raise RuntimeError(f"blocked_failed_tests: readonly coverage refinement: {result['stderr'] or result['stdout']}")
        after = coverage_counts(options.run_dir)
        write_json(
            options.run_dir / "automation" / "coverage_refinement_status.json",
            {
                "generated_at": now(),
                "coverage_refinement_executed": True,
                "ready_cases_before": before["ready_cases"],
                "ready_cases_after": after["ready_cases"],
                "backlog_cases_before": before["backlog_cases"],
                "backlog_cases_after": after["backlog_cases"],
                "missing_required_facts_before": before["missing_required_facts"],
                "missing_required_facts_after": after["missing_required_facts"],
                "neo4j_write_performed": False,
                "kg_patch_applied": False,
                "partial_eval_rerun": False,
                "full_eval_executed": False,
                "model_api_called": False,
            },
        )
        write_text(
            options.run_dir / "automation" / "coverage_refinement_status.md",
            f"""# Coverage Refinement Status

Generated: {now()}

- coverage_refinement_executed: true
- ready_cases_before: {before['ready_cases']}
- ready_cases_after: {after['ready_cases']}
- backlog_cases_before: {before['backlog_cases']}
- backlog_cases_after: {after['backlog_cases']}
- missing_required_facts_before: {before['missing_required_facts']}
- missing_required_facts_after: {after['missing_required_facts']}
- Neo4j write performed: false
- KG patch applied: false
- partial eval rerun: false
- full eval executed: false
- model API called: false
""",
        )
        for mode in ("collect", "merge", "gate-refresh"):
            refresh = call_orchestrator(mode, options.run_dir)
            refresh["step"] = len(command_results) + 1
            command_results.append(refresh)
            if not refresh["ok"]:
                raise RuntimeError(f"blocked_failed_tests: post-coverage {mode}: {refresh['stderr']}")
        ready_manifest, changed_ready = prepare_ready_partial_eval_inputs(options.run_dir)
        backlog_summary, changed_backlog = generate_coverage_backlog(options.run_dir)
        changed_paths.extend(changed_ready)
        changed_paths.extend(changed_backlog)
    current_partial_status = partial_eval_status(options.run_dir)
    current_coverage_status = coverage_refinement_status(options.run_dir)
    current_remediation_status = remediation_plan_status(options.run_dir)
    if (
        current_partial_status.get("partial_eval_executed")
        and current_coverage_status.get("coverage_refinement_executed")
        and backlog_summary.get("backlog_case_count", 0)
        and not current_remediation_status.get("recommended_route")
    ):
        plan, changed_plan = generate_non_mutating_ontology_coverage_remediation_plan(options.run_dir)
        command_results.append(
            {
                "mode": "generate-non-mutating-ontology-coverage-remediation-plan",
                "returncode": 0,
                "stdout": str(plan.get("next_action", "")),
                "stderr": "",
                "ok": True,
                "step": len(command_results) + 1,
            }
        )
        changed_paths.extend(changed_plan)
    runtime_state = read_state(options.run_dir)
    write_runtime_reports(options, runtime_state, ready_manifest, backlog_summary, command_results, changed_paths)
    if refresh_gpt_advisory_request(options.run_dir):
        changed_paths.append(options.run_dir / "reviews" / "inbox" / "gpt_advisory_request_neo4j_mapping.md")
    return read_json(options.run_dir / "automation" / "orchestration_runtime_status.json", {})


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the Round 3 local file-based operator loop.")
    parser.add_argument("--run-dir", default=None)
    parser.add_argument("--max-steps", type=int, default=10)
    parser.add_argument("--dry-run-mode", choices=("plan_only",), default="plan_only")
    parser.add_argument("--allow-readonly-neo4j", action="store_true")
    parser.add_argument("--no-model-api", action="store_true", default=True)
    parser.add_argument("--no-dangerous-actions", action="store_true", default=True)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    options = RuntimeOptions(
        run_dir=run_dir_from_arg(args.run_dir),
        max_steps=args.max_steps,
        dry_run_mode=args.dry_run_mode,
        allow_readonly_neo4j=args.allow_readonly_neo4j,
        model_api_allowed=False,
        dangerous_actions_allowed=False,
    )
    try:
        status = run_loop(options)
    except Exception as exc:
        run_dir = options.run_dir
        run_dir.mkdir(parents=True, exist_ok=True)
        failure = {
            "generated_at": now(),
            "terminal_state": "blocked_failed_tests",
            "error": f"{type(exc).__name__}: {exc}",
            "safety": {
                "neo4j_write_performed": False,
                "kg_patch_applied": False,
                "dry_run_executed_this_run": False,
                "partial_eval_executed": False,
                "full_eval_executed": False,
                "model_api_called": False,
            },
        }
        write_json(run_dir / "automation" / "orchestration_runtime_status.json", failure)
        write_text(run_dir / "automation" / "orchestration_runtime_status.md", f"# Orchestration Runtime Status\n\n`blocked_failed_tests`\n\n{failure['error']}\n")
        print("blocked_failed_tests")
        return 1
    print(status.get("next_action", "No next action available."))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
