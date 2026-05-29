"""Round 3 file-based orchestration runner.

This runner coordinates local files only. It does not run dry-run/full eval,
does not connect to Neo4j, does not write KG patches, and does not call Gemini.
Gemini artifacts are historical only for Round 3.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import textwrap
from datetime import datetime
from pathlib import Path
from typing import Any


SCRIPT_VERSION = "1.0-round3-file-runner"
REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_RUN_DIR = REPO_ROOT / "outputs" / "round3_orchestration" / "20260525_132801"
MERGE_SCRIPT = REPO_ROOT / "scripts" / "merge_multi_agent_reviews.py"

DIRS = (
    "input",
    "reviews",
    "reviews/inbox",
    "reviews/archive",
    "merged",
    "logs",
    "automation",
    "approvals",
)

LOCAL_ARTIFACTS = (
    "missing_prompt_scorer_implementation_plan.md",
    "missing_prompt_scorer_plan_summary.md",
    "prompt_scorer_implementation_report.md",
    "prompt_scorer_gate_status.md",
    "neo4j_coverage_report.md",
    "dry_run_readiness_check.md",
    "gate_ledger.md",
    "artifact_manifest.json",
    "final_go_no_go_decision.md",
    "final_eval_lock_status.md",
    "gemini_review_status.md",
    "codex_phase_de_summary.json",
    "codex_gate_update_proposal.md",
    "missing_blockers_report.md",
    "automation/gpt_advisory_status.md",
)

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

METHODS = ("vector_only", "graph_facts_only", "hybrid_vector_graph", "gold_context")
GPT_ADVISORY_REQUEST = "gpt_advisory_request_neo4j_mapping.md"
GPT_REVIEW_FILE = "gpt_review.md"
GPT_RESPONSE_NAME_HINTS = ("gpt_advisory_response", "gpt_response", "gpt_review", "chatgpt_response", "chatgpt_review")
GPT_EVIDENCE_FILES = (
    "automation/final_operator_report.md",
    "automation/blockers.md",
    "automation/next_action.md",
    "neo4j_schema_introspection_report.md",
    "neo4j_label_mapping_proposal.md",
    "neo4j_case_presence_probe.md",
    "neo4j_readonly_coverage_report.md",
    "neo4j_coverage_gate_status.md",
    "prompt_scorer_gate_status.md",
    "gemini_review_status.md",
)


def now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def rel(path: Path) -> str:
    try:
        return path.resolve().relative_to(REPO_ROOT.resolve()).as_posix()
    except ValueError:
        return str(path)


def write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text.rstrip() + "\n", encoding="utf-8", newline="\n")


def write_json(path: Path, data: Any) -> None:
    write_text(path, json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True))


def read_text(path: Path, max_chars: int | None = None) -> str:
    text = path.read_text(encoding="utf-8")
    return text[:max_chars] if max_chars is not None else text


def append_log(run_dir: Path, mode: str, status: str, message: str, extra: dict[str, Any] | None = None) -> None:
    row = {
        "timestamp": now(),
        "script_version": SCRIPT_VERSION,
        "mode": mode,
        "status": status,
        "message": message,
        "extra": extra or {},
        "safety": {
            "full_eval_executed": False,
            "dry_run_executed": False,
            "neo4j_write_performed": False,
            "kg_patch_applied": False,
            "round02_modified": False,
            "repaired_subset_modified": False,
            "model_api_called": bool(extra.get("model_api_called")) if extra else False,
        },
    }
    log_jsonl = run_dir / "automation" / "run_log.jsonl"
    log_jsonl.parent.mkdir(parents=True, exist_ok=True)
    with log_jsonl.open("a", encoding="utf-8", newline="\n") as f:
        f.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
    log_md = run_dir / "automation" / "run_log.md"
    with log_md.open("a", encoding="utf-8", newline="\n") as f:
        f.write(f"- {row['timestamp']} `{mode}` {status}: {message}\n")


def run_dir_from_arg(value: str | None) -> Path:
    if value:
        path = Path(value)
        return path if path.is_absolute() else REPO_ROOT / path
    return DEFAULT_RUN_DIR


def approval_path(run_dir: Path, name: str) -> Path:
    return run_dir / "approvals" / name


def approvals(run_dir: Path) -> dict[str, bool]:
    return {
        "allow_dry_run": approval_path(run_dir, "allow_dry_run.txt").exists(),
        "allow_full_eval_proposal": approval_path(run_dir, "allow_full_eval_proposal.txt").exists(),
        "allow_neo4j_readonly_coverage": approval_path(run_dir, "allow_neo4j_readonly_coverage.txt").exists(),
    }


def ensure_init(run_dir: Path) -> None:
    for subdir in DIRS:
        (run_dir / subdir).mkdir(parents=True, exist_ok=True)
    write_text(
        run_dir / "automation" / "README_round3_orchestrator.md",
        f"""# Round 3 File-Based Orchestrator

This runner coordinates local orchestration files under `{rel(run_dir)}`.

## Safe Commands

```bash
python scripts/round3_orchestrate.py init --run-dir {rel(run_dir)}
python scripts/round3_orchestrate.py collect --run-dir {rel(run_dir)}
python scripts/round3_orchestrate.py merge --run-dir {rel(run_dir)}
python scripts/round3_orchestrate.py gate-refresh --run-dir {rel(run_dir)}
python scripts/round3_orchestrate.py next-action --run-dir {rel(run_dir)}
python scripts/round3_orchestrate.py all --run-dir {rel(run_dir)}
```

## Gemini

Gemini is retired for Round 3. Existing Gemini artifacts are historical only
and Gemini missing/pending never blocks gates.

## Safety Boundary

This script does not run full evaluation, does not run dry-run without explicit approval, does not write to Neo4j, does not apply KG patches, and does not modify Round 02, selected 7, original candidate pool, or repaired subset source files.
""",
    )


def call_merge_init(run_dir: Path) -> None:
    subprocess.run(
        [sys.executable, str(MERGE_SCRIPT), "--init-only", "--run-dir", rel(run_dir)],
        cwd=REPO_ROOT,
        check=True,
        capture_output=True,
        text=True,
    )


def call_merge(run_dir: Path) -> None:
    subprocess.run(
        [sys.executable, str(MERGE_SCRIPT), "--run-dir", rel(run_dir)],
        cwd=REPO_ROOT,
        check=True,
        capture_output=True,
        text=True,
    )


def collect_artifact_records(run_dir: Path) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for name in LOCAL_ARTIFACTS:
        path = run_dir / name
        records.append(
            {
                "path": rel(path),
                "exists": path.exists(),
                "status": "available" if path.exists() else "pending",
                "length_bytes": path.stat().st_size if path.exists() else None,
                "notes": "local orchestration artifact",
            }
        )
    return records


def artifact_text(run_dir: Path, name: str, max_chars: int = 5000) -> str:
    path = run_dir / name
    if not path.exists():
        return f"## {name}\n\nStatus: pending; file not found.\n"
    return f"## {name}\n\n```text\n{read_text(path, max_chars=max_chars)}\n```\n"


def infer_gate_assessments(run_dir: Path) -> dict[str, str]:
    gates = {gate: "pending" for gate in GATES}
    gates["full_eval_lock"] = "locked"
    gates["gemini_semantic_review"] = "not_required_historical"
    gates["gemini_prompt_fairness_review"] = "not_required_historical"
    if (run_dir / "input" / "manifest.json").exists():
        gates["artifact_freeze"] = "pass"

    dry_status = (run_dir / "dry_run_readiness_check.md").read_text(encoding="utf-8").lower() if (run_dir / "dry_run_readiness_check.md").exists() else ""
    if "input_isolation_rules_implemented\": true" in dry_status:
        gates["input_isolation"] = "pass"
    if "executable_method_prompts_exist\": false" in dry_status:
        gates["executable_method_prompts"] = "blocked"
    if "executable_required_fact_recall_scorer_exists\": false" in dry_status:
        gates["required_fact_recall_scorer"] = "blocked"
    if "dry_run_status: `blocked`" in dry_status:
        gates["dry_run"] = "blocked"

    neo4j_status = (run_dir / "neo4j_coverage_report.md").read_text(encoding="utf-8").lower() if (run_dir / "neo4j_coverage_report.md").exists() else ""
    if "not_checked_no_neo4j_config" in neo4j_status or "coverage executed: no" in neo4j_status:
        gates["neo4j_readonly_coverage"] = "pending"
    elif "ready_for_eval" in neo4j_status:
        gates["neo4j_readonly_coverage"] = "pass"

    final_lock = (run_dir / "final_eval_lock_status.md").read_text(encoding="utf-8").lower() if (run_dir / "final_eval_lock_status.md").exists() else ""
    if "full_eval_locked" in final_lock or "full evaluation was not started" in final_lock:
        gates["full_eval_lock"] = "locked"

    prompt_scorer_status = (
        (run_dir / "prompt_scorer_gate_status.md").read_text(encoding="utf-8").lower()
        if (run_dir / "prompt_scorer_gate_status.md").exists()
        else ""
    )
    if "executable_method_prompts: pass" in prompt_scorer_status:
        gates["executable_method_prompts"] = "pass"
    if "required_fact_recall_scorer: pass" in prompt_scorer_status:
        gates["required_fact_recall_scorer"] = "pass"
    if "numeric_correctness_scorer: pass" in prompt_scorer_status:
        gates.setdefault("numeric_correctness_scorer", "pass")
    if "answer_correctness_scorer: pass" in prompt_scorer_status:
        gates.setdefault("answer_correctness_scorer", "pass")
    return gates


def collect_mode(run_dir: Path) -> None:
    call_merge_init(run_dir)
    gpt_status = sync_gpt_advisory(run_dir)
    records = collect_artifact_records(run_dir)
    gates = infer_gate_assessments(run_dir)
    missing = [record["path"] for record in records if not record["exists"]]
    context_rows = ["# Collected Local Orchestration Artifacts", ""]
    context_rows.append("| Path | Exists | Status | Notes |")
    context_rows.append("|---|---:|---|---|")
    for record in records:
        context_rows.append(f"| `{record['path']}` | {record['exists']} | {record['status']} | {record['notes']} |")
    write_text(run_dir / "input" / "context_files.md", "\n".join(context_rows))

    # Preserve merge script manifest behavior, then add runner collection metadata.
    manifest_path = run_dir / "input" / "manifest.json"
    manifest = json.loads(read_text(manifest_path)) if manifest_path.exists() else {}
    manifest["orchestrator_collection"] = {
        "generated_at": now(),
        "artifacts": records,
        "missing_artifacts": missing,
        "should_modify_context_files": False,
    }
    write_json(manifest_path, manifest)

    blocking = []
    if gates["executable_method_prompts"] != "pass":
        blocking.append("Executable four-method prompts are missing or blocked.")
    if gates["required_fact_recall_scorer"] != "pass":
        blocking.append("Executable required_fact_recall scorer is missing or blocked.")
    if gates["neo4j_readonly_coverage"] != "pass":
        blocking.append("Neo4j read-only coverage has not passed.")

    codex_payload = {
        "reviewer": "codex",
        "status": "warning" if blocking else "pass",
        "overall_recommendation": "conditional_go",
        "blocking_issues": blocking,
        "non_blocking_warnings": [f"Missing local artifact: {path}" for path in missing],
        "agreements": [
            "Full evaluation remains locked.",
            "No dry-run/full eval/Neo4j write/KG patch was executed by the orchestrator.",
            "GPT advisory is synchronized through local files and is not a gate authority.",
        ],
        "disagreements": [],
        "method_issues": {method: [] for method in METHODS},
        "gate_assessments": gates,
        "recommended_actions": [
            "Implement executable four-method prompts and deterministic required_fact_recall scorer before dry-run.",
            "Complete Neo4j read-only coverage only after explicit approval/config exists.",
            "Keep full eval locked until Antigravity approval outside this script.",
        ],
        "files_referenced": [record["path"] for record in records if record["exists"]],
    }
    if gpt_status.get("status") == "advisory_requested":
        codex_payload["non_blocking_warnings"].append(
            f"Optional GPT advisory pending in `{gpt_status['request_file']}`; no user relay is required."
        )
    if gpt_status.get("response_ingested"):
        codex_payload["agreements"].append("GPT advisory response was ingested automatically from reviews/inbox.")
    write_review(run_dir / "reviews" / "codex_review.md", "codex", "deterministic_execution", codex_payload, run_dir)

    antigravity_payload = {
        "reviewer": "antigravity",
        "status": "warning",
        "overall_recommendation": "conditional_go",
        "blocking_issues": [
            "Antigravity final approval file is not present; full evaluation remains locked."
        ],
        "non_blocking_warnings": [],
        "agreements": [
            "Round 3 remains conditional_go until all gates pass.",
            "Only unlocked_proposal_only may be reported by scripts; actual final approval is external.",
        ],
        "disagreements": [],
        "method_issues": {method: [] for method in METHODS},
        "gate_assessments": {**gates, "full_eval_lock": "locked"},
        "recommended_actions": [
            "Resolve P0 blockers, rerun merge, then request Antigravity approval if all required gates pass."
        ],
        "files_referenced": [rel(run_dir / "final_eval_lock_status.md")] if (run_dir / "final_eval_lock_status.md").exists() else [],
    }
    write_review(run_dir / "reviews" / "antigravity_review.md", "antigravity", "gate_state", antigravity_payload, run_dir)


def write_review(path: Path, reviewer: str, review_type: str, payload: dict[str, Any], run_dir: Path) -> None:
    notes = "\n\n".join(artifact_text(run_dir, name) for name in LOCAL_ARTIFACTS if (run_dir / name).exists())
    write_text(
        path,
        f"""---
reviewer: {reviewer}
review_type: {review_type}
status: {payload.get("status", "pending")}
round3_decision: {payload.get("overall_recommendation", "conditional_go")}
---

# Reviewer Summary

Automatically collected local file-based orchestration status. This is not a semantic finance judgment and not an evaluation run.

# Machine Readable Review

```json
{json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True)}
```

# Human Notes

{notes if notes.strip() else "No local artifacts were available."}
""",
    )


def packet_text(run_dir: Path, max_chars: int = 45000) -> str:
    packet = run_dir / "gemini_review_packet"
    if not packet.exists():
        return "Gemini review packet directory is missing."
    parts: list[str] = []
    for path in sorted(p for p in packet.rglob("*") if p.is_file()):
        try:
            body = read_text(path, max_chars=6000)
        except UnicodeDecodeError:
            continue
        parts.append(f"## {rel(path)}\n\n```text\n{body}\n```")
        if sum(len(part) for part in parts) > max_chars:
            break
    return "\n\n".join(parts)[:max_chars]


def create_gemini_manual_request(run_dir: Path) -> None:
    write_text(
        run_dir / "automation" / "gemini_manual_request.md",
        """# Gemini Retired

Gemini is retired for Round 3. Do not upload packets to Gemini and do not call Gemini API.
Existing Gemini review artifacts remain historical only and never block gates.
""",
    )


def parse_front_matter(text: str) -> dict[str, str]:
    if not text.startswith("---"):
        return {}
    parts = text.split("---", 2)
    if len(parts) < 3:
        return {}
    data: dict[str, str] = {}
    for line in parts[1].splitlines():
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        data[key.strip().lower()] = value.strip().strip("\"'")
    return data


def looks_like_requested_advisory(text: str, path: Path) -> bool:
    fm = parse_front_matter(text)
    low_name = path.name.lower()
    low_text = text[:3000].lower()
    if "request" in low_name and "response" not in low_name:
        return True
    if fm.get("status") == "requested":
        return True
    return "# gpt advisory request" in low_text or "requested output" in low_text


def wrap_gpt_advisory_response(text: str, source: Path, run_dir: Path) -> str:
    if text.lstrip().startswith("---") and "# Machine Readable Review" in text:
        return text
    payload = {
        "reviewer": "gpt",
        "status": "advisory_received",
        "overall_recommendation": "advisory_only",
        "blocking_issues": [],
        "non_blocking_warnings": [
            "GPT advisory is optional and must not be treated as gate authority."
        ],
        "agreements": [],
        "disagreements": [],
        "method_issues": {method: [] for method in METHODS},
        "gate_assessments": {"full_eval_lock": "locked"},
        "recommended_actions": [
            "Use this advisory as input for Codex/Antigravity local reports; do not route go/no-go authority to GPT."
        ],
        "files_referenced": [rel(source)],
    }
    return f"""---
reviewer: gpt
review_type: advisory
status: advisory_received
round3_decision: advisory_only
---

# Reviewer Summary

GPT advisory was ingested automatically from `{rel(source)}`. GPT is advisory only and is not a gate authority.

# Machine Readable Review

```json
{json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True)}
```

# Human Notes

{text.strip()}
"""


def find_gpt_advisory_response(run_dir: Path) -> Path | None:
    inbox = run_dir / "reviews" / "inbox"
    if not inbox.exists():
        return None
    candidates: list[Path] = []
    for path in sorted(inbox.glob("*.md"), key=lambda p: p.stat().st_mtime, reverse=True):
        low_name = path.name.lower()
        if not any(hint in low_name for hint in GPT_RESPONSE_NAME_HINTS):
            continue
        try:
            text = read_text(path)
        except UnicodeDecodeError:
            continue
        if looks_like_requested_advisory(text, path):
            continue
        candidates.append(path)
    return candidates[0] if candidates else None


def evidence_excerpt(run_dir: Path, name: str, max_chars: int = 2500) -> str:
    path = run_dir / name
    if not path.exists():
        return f"## {name}\n\nStatus: missing/pending.\n"
    try:
        body = read_text(path, max_chars=max_chars)
    except UnicodeDecodeError:
        return f"## {name}\n\nStatus: binary/unreadable; skipped.\n"
    return f"## {name}\n\n```text\n{body}\n```\n"


def write_gpt_advisory_request(run_dir: Path) -> Path:
    request_path = run_dir / "reviews" / "inbox" / GPT_ADVISORY_REQUEST
    evidence = "\n\n".join(evidence_excerpt(run_dir, name) for name in GPT_EVIDENCE_FILES)
    write_text(
        request_path,
        f"""---
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

{evidence}
""",
    )
    return request_path


def sync_gpt_advisory(run_dir: Path) -> dict[str, Any]:
    response = find_gpt_advisory_response(run_dir)
    review_path = run_dir / "reviews" / GPT_REVIEW_FILE
    status: dict[str, Any] = {
        "generated_at": now(),
        "reviewer": "gpt",
        "role": "advisory_only",
        "direct_channel_available": False,
        "response_ingested": False,
        "request_file": rel(run_dir / "reviews" / "inbox" / GPT_ADVISORY_REQUEST),
        "review_file": rel(review_path),
        "user_relay_required": False,
        "gate_blocking": False,
        "notes": "GPT advisory is optional; absence must not block gates.",
    }
    if response is not None:
        text = read_text(response)
        write_text(review_path, wrap_gpt_advisory_response(text, response, run_dir))
        status.update(
            {
                "response_ingested": True,
                "response_file": rel(response),
                "status": "advisory_received",
                "notes": "GPT advisory response was ingested automatically from reviews/inbox.",
            }
        )
    else:
        request = write_gpt_advisory_request(run_dir)
        status.update(
            {
                "status": "advisory_requested",
                "request_file": rel(request),
                "notes": "No GPT response file found; request is queued locally and gates continue without user relay.",
            }
        )
    write_json(run_dir / "automation" / "gpt_advisory_status.json", status)
    write_text(
        run_dir / "automation" / "gpt_advisory_status.md",
        f"""# GPT Advisory Status

- status: `{status['status']}`
- role: advisory_only
- request_file: `{status['request_file']}`
- review_file: `{status['review_file']}`
- response_ingested: {status['response_ingested']}
- user_relay_required: false
- gate_blocking: false

{status['notes']}
""",
    )
    return status


def call_gemini_api(run_dir: Path, model: str) -> bool:
    create_gemini_manual_request(run_dir)
    return False


def gemini_review_mode(run_dir: Path, provider: str, model: str) -> bool:
    create_gemini_manual_request(run_dir)
    return False
    if provider != "gemini":
        create_gemini_manual_request(run_dir)
        return False
    return call_gemini_api(run_dir, model)


def load_json_file(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    try:
        return json.loads(read_text(path))
    except json.JSONDecodeError:
        return default


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    if not path.exists():
        return rows
    for line in read_text(path).splitlines():
        if not line.strip():
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            rows.append({"raw": line, "parse_error": True})
    return rows


def gate_refresh_mode(run_dir: Path) -> dict[str, Any]:
    gpt_status = sync_gpt_advisory(run_dir)
    gate_data = load_json_file(run_dir / "merged" / "gate_status.json", {})
    action_items = load_jsonl(run_dir / "merged" / "action_items.jsonl")
    conflicts = load_json_file(run_dir / "merged" / "conflict_matrix.json", {"conflicts": []})
    gates = gate_data.get("gate_status", {})
    gates = apply_prompt_scorer_gate_overrides(run_dir, dict(gates))
    gates = apply_neo4j_coverage_gate_overrides(run_dir, gates)
    approved = approvals(run_dir)
    required = [gate for gate in GATES if gate != "full_eval_lock"]
    all_required_pass = all(gates.get(gate) == "pass" for gate in required)
    full_eval_lock = "unlocked_proposal_only" if (all_required_pass and approved["allow_full_eval_proposal"]) else "locked"
    blockers = [
        item
        for item in action_items
        if item.get("priority") == "P0" and item.get("status") in {"open", "blocked", "pending_review"}
    ]
    blockers = filter_resolved_prompt_scorer_blockers(run_dir, blockers)
    critical_conflicts = [c for c in conflicts.get("conflicts", []) if c.get("severity") == "critical"]
    state = {
        "generated_at": now(),
        "run_dir": rel(run_dir),
        "gate_status": {**gates, "full_eval_lock": full_eval_lock},
        "approvals": approved,
        "p0_blocker_count": len(blockers),
        "critical_conflict_count": len(critical_conflicts),
        "full_eval_approved": False,
        "gpt_advisory": gpt_status,
        "safety": {
            "full_eval_executed": False,
            "dry_run_executed": False,
            "neo4j_write_performed": False,
            "kg_patch_applied": False,
            "model_api_called": False,
        },
    }
    write_json(run_dir / "automation" / "current_gate_state.json", state)
    write_text(run_dir / "automation" / "current_gate_state.md", render_gate_state(state))
    write_text(run_dir / "automation" / "blockers.md", render_blockers(blockers, critical_conflicts))
    action = choose_next_action(run_dir, state, action_items, conflicts)
    write_text(run_dir / "automation" / "next_action.md", action)
    return state


def prompt_scorer_passes(run_dir: Path) -> dict[str, bool]:
    path = run_dir / "prompt_scorer_gate_status.md"
    text = path.read_text(encoding="utf-8").lower() if path.exists() else ""
    return {
        "prompts": "executable_method_prompts: pass" in text,
        "required_fact_recall": "required_fact_recall_scorer: pass" in text,
        "numeric": "numeric_correctness_scorer: pass" in text,
        "answer": "answer_correctness_scorer: pass" in text,
        "input_isolation": "input_isolation_validation: pass" in text,
    }


def apply_prompt_scorer_gate_overrides(run_dir: Path, gates: dict[str, str]) -> dict[str, str]:
    passes = prompt_scorer_passes(run_dir)
    if passes["prompts"]:
        gates["executable_method_prompts"] = "pass"
    if passes["required_fact_recall"]:
        gates["required_fact_recall_scorer"] = "pass"
    if passes["input_isolation"]:
        gates["input_isolation"] = "pass"
    gates["gemini_semantic_review"] = "not_required_historical"
    gates["gemini_prompt_fairness_review"] = "not_required_historical"
    return gates


def apply_neo4j_coverage_gate_overrides(run_dir: Path, gates: dict[str, str]) -> dict[str, str]:
    status_path = run_dir / "neo4j_coverage_gate_status.md"
    report_path = run_dir / "neo4j_readonly_coverage_report.md"
    text = ""
    if status_path.exists():
        text += status_path.read_text(encoding="utf-8").lower()
    if report_path.exists():
        text += "\n" + report_path.read_text(encoding="utf-8").lower()
    if "neo4j_readonly_coverage: pass" in text:
        gates["neo4j_readonly_coverage"] = "pass"
    elif "not_checked_no_neo4j_config" in text or "neo4j_readonly_coverage: blocked" in text:
        gates["neo4j_readonly_coverage"] = "blocked"
    elif "neo4j_readonly_coverage: warning" in text:
        gates["neo4j_readonly_coverage"] = "warning"
    return gates


def filter_resolved_prompt_scorer_blockers(run_dir: Path, blockers: list[dict[str, Any]]) -> list[dict[str, Any]]:
    passes = prompt_scorer_passes(run_dir)
    if not (passes["prompts"] and passes["required_fact_recall"]):
        return blockers
    resolved_terms = (
        "four-method prompts",
        "4",
        "required_fact_recall",
        "numeric_correctness",
        "scorer/evaluator",
        "scorer",
    )
    filtered = []
    for item in blockers:
        text = f"{item.get('title', '')}\n{item.get('description', '')}".lower()
        if any(term in text for term in resolved_terms) and "neo4j" not in text:
            continue
        filtered.append(item)
    return filtered


def render_gate_state(state: dict[str, Any]) -> str:
    rows = ["# Current Gate State", "", f"- generated_at: `{state['generated_at']}`", f"- run_dir: `{state['run_dir']}`", ""]
    rows.append("| Gate | Status |")
    rows.append("|---|---|")
    for gate, status in state["gate_status"].items():
        rows.append(f"| `{gate}` | {status} |")
    rows.extend(
        [
            "",
            "## Approvals",
            "",
        ]
    )
    rows.extend(f"- `{key}`: {value}" for key, value in state["approvals"].items())
    rows.extend(
        [
            "",
            "## Safety",
            "",
            "- full eval executed: false",
            "- dry-run executed: false",
            "- Neo4j write performed: false",
            "- KG patch applied: false",
        ]
    )
    return "\n".join(rows)


def render_blockers(blockers: list[dict[str, Any]], critical_conflicts: list[dict[str, Any]]) -> str:
    rows = ["# Blockers", ""]
    if not blockers and not critical_conflicts:
        rows.append("No P0 blockers or critical conflicts are currently recorded.")
        rows.extend(["", "## Policy Notes", "- Gemini provider/reviewer path is retired; existing Gemini artifacts are historical and non-blocking."])
        return "\n".join(rows)
    if critical_conflicts:
        rows.append("## Critical Conflicts")
        rows.extend(f"- `{c.get('rule')}`: {c.get('description')}" for c in critical_conflicts)
        rows.append("")
    if blockers:
        rows.append("## P0 Action Items")
        rows.extend(f"- `{b.get('action_id')}` {b.get('title')}: {b.get('description')}" for b in blockers)
    rows.extend(["", "## Policy Notes", "- Gemini provider/reviewer path is retired; existing Gemini artifacts are historical and non-blocking."])
    return "\n".join(rows)


def choose_next_action(run_dir: Path, state: dict[str, Any], action_items: list[dict[str, Any]], conflicts: dict[str, Any]) -> str:
    gates = state.get("gate_status", {})
    real_status = load_json_file(run_dir / "automation" / "real_provider_partial_eval_status.json", {})
    if real_status.get("terminal_state") == "openai_provider_ready_subset_partial_eval":
        return "Run ready subset partial eval with provider=openai; Gemini is retired and historical-only. Full eval remains locked."
    if real_status.get("terminal_state") == "report_only_gpt_final_review":
        return f"Review `{real_status.get('report_only_package', rel(run_dir / 'reviews' / 'inbox' / 'gpt_report_only_ready_subset_review.md'))}` as GPT advisory packet; full eval remains locked."
    if state.get("critical_conflict_count", 0) > 0:
        return "Resolve critical conflicts in automation/blockers.md, then rerun merge and gate-refresh."
    if gates.get("executable_method_prompts") in {"pending", "blocked", "fail", "unknown"} or gates.get("required_fact_recall_scorer") in {"pending", "blocked", "fail", "unknown"}:
        return "Implement executable four-method prompts and required_fact_recall scorer, then rerun collect and merge."
    if gates.get("neo4j_readonly_coverage") != "pass":
        gpt_status = state.get("gpt_advisory", {})
        if gpt_status.get("status") == "advisory_requested":
            return f"GPT advisory request is queued at {gpt_status.get('request_file')}; continue local Antigravity-facing Neo4j mapping/coverage refinement without user relay."
        report_path = run_dir / "neo4j_readonly_coverage_report.md"
        report_text = report_path.read_text(encoding="utf-8").lower() if report_path.exists() else ""
        if "not_checked_no_neo4j_config" in report_text:
            return "Expose Neo4j env config to this Codex process or place it in .env, then rerun read-only coverage; dry-run and full eval remain locked."
        if state["approvals"].get("allow_neo4j_readonly_coverage"):
            return "Rerun Neo4j read-only coverage after resolving the coverage blocker; dry-run and full eval remain locked."
        return f"Create {rel(approval_path(run_dir, 'allow_neo4j_readonly_coverage.txt'))}, then run Neo4j read-only coverage in a separate approved step."
    if gates.get("dry_run") != "pass":
        if state["approvals"].get("allow_dry_run"):
            return "Run small mock dry-run in a separate explicitly approved step; this orchestrator will not execute it."
        return f"Create {rel(approval_path(run_dir, 'allow_dry_run.txt'))} only if you want a later small dry-run step."
    if gates.get("full_eval_lock") == "unlocked_proposal_only":
        return "Request Antigravity final approval outside this script; full eval execution remains disabled here."
    p0 = [item for item in action_items if item.get("priority") == "P0" and item.get("status") in {"open", "blocked", "pending_review"}]
    if p0:
        return p0[0].get("recommended_next_step") or p0[0].get("title")
    return "Keep full eval locked and review merged/final_orchestration_report.md."


def mode_init(args: argparse.Namespace) -> int:
    run_dir = run_dir_from_arg(args.run_dir)
    ensure_init(run_dir)
    call_merge_init(run_dir)
    append_log(run_dir, "init", "pass", "Initialized file-based orchestration directories.")
    return 0


def mode_collect(args: argparse.Namespace) -> int:
    run_dir = run_dir_from_arg(args.run_dir)
    ensure_init(run_dir)
    collect_mode(run_dir)
    append_log(run_dir, "collect", "pass", "Collected local artifacts into Codex/Antigravity review files.")
    return 0


def mode_gemini_review(args: argparse.Namespace) -> int:
    run_dir = run_dir_from_arg(args.run_dir)
    ensure_init(run_dir)
    called = False
    write_text(
        run_dir / "automation" / "gemini_retired.md",
        "# Gemini Retired\n\nGemini is retired for Round 3. No Gemini API call was made. Existing Gemini artifacts are historical only.\n",
    )
    append_log(
        run_dir,
        "gemini-review",
        "retired",
        "Gemini is retired for Round 3; no API call was made.",
        {"model_api_called": called, "provider": args.provider},
    )
    return 0


def mode_merge(args: argparse.Namespace) -> int:
    run_dir = run_dir_from_arg(args.run_dir)
    ensure_init(run_dir)
    call_merge(run_dir)
    append_log(run_dir, "merge", "pass", "Merged review files with merge_multi_agent_reviews.py.")
    return 0


def mode_gate_refresh(args: argparse.Namespace) -> int:
    run_dir = run_dir_from_arg(args.run_dir)
    ensure_init(run_dir)
    state = gate_refresh_mode(run_dir)
    append_log(run_dir, "gate-refresh", "pass", "Refreshed automation gate state.", {"p0_blocker_count": state["p0_blocker_count"]})
    return 0


def mode_next_action(args: argparse.Namespace) -> int:
    run_dir = run_dir_from_arg(args.run_dir)
    ensure_init(run_dir)
    state_path = run_dir / "automation" / "current_gate_state.json"
    if not state_path.exists():
        gate_refresh_mode(run_dir)
    action = read_text(run_dir / "automation" / "next_action.md").strip()
    print(action.splitlines()[0])
    append_log(run_dir, "next-action", "pass", action.splitlines()[0])
    return 0


def mode_advisory_sync(args: argparse.Namespace) -> int:
    run_dir = run_dir_from_arg(args.run_dir)
    ensure_init(run_dir)
    status = sync_gpt_advisory(run_dir)
    append_log(
        run_dir,
        "advisory-sync",
        str(status.get("status", "unknown")),
        "Synchronized GPT advisory request/response files without using the user as relay.",
    )
    print(status.get("status", "unknown"))
    return 0


def mode_all(args: argparse.Namespace) -> int:
    mode_init(args)
    mode_collect(args)
    if args.provider != "none":
        mode_gemini_review(args)
    mode_merge(args)
    mode_gate_refresh(args)
    return mode_next_action(args)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Round 3 file-based orchestration runner.")
    parser.add_argument(
        "mode",
        choices=("init", "collect", "gemini-review", "merge", "gate-refresh", "next-action", "advisory-sync", "all"),
    )
    parser.add_argument("--run-dir", default=None)
    parser.add_argument("--provider", default="none", choices=("none", "openai"))
    parser.add_argument("--gemini-model", default="gemini-1.5-flash")
    parser.add_argument("--allow-dry-run", action="store_true", help="Accepted for future explicit dry-run workflows; this script still never runs dry-run.")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    dispatch = {
        "init": mode_init,
        "collect": mode_collect,
        "gemini-review": mode_gemini_review,
        "merge": mode_merge,
        "gate-refresh": mode_gate_refresh,
        "next-action": mode_next_action,
        "advisory-sync": mode_advisory_sync,
        "all": mode_all,
    }
    return dispatch[args.mode](args)


if __name__ == "__main__":
    raise SystemExit(main())
