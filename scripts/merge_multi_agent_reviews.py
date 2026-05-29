"""File-based Round 3 multi-agent review orchestration.

This script only creates and merges local orchestration files. It does not
connect to Neo4j, call model APIs, run evaluation, or modify Round 02,
selected7, original Round 3, or repaired subset artifacts.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any


SCRIPT_VERSION = "1.1-file-orchestration"
REPO_ROOT = Path(__file__).resolve().parents[1]
BASE_DIR = REPO_ROOT / "outputs" / "round3_orchestration"
PREFERRED_RUN = BASE_DIR / "20260525_132801"

REVIEWERS = ("gpt", "gemini", "codex", "antigravity")
REQUIRED_REVIEWERS = ("codex", "antigravity")
ADVISORY_REVIEWERS = ("gpt",)
REVIEW_TYPES = {
    "gpt": "synthesis",
    "gemini": "semantic_fairness",
    "codex": "deterministic_execution",
    "antigravity": "gate_state",
}
METHODS = ("vector_only", "graph_facts_only", "hybrid_vector_graph", "gold_context")
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
REQUIRED_GATES = tuple(g for g in GATES if g != "full_eval_lock")
ALLOWED_GATE_STATUSES = {
    "pass",
    "fail",
    "pending",
    "blocked",
    "warning",
    "locked",
    "unlocked_proposal_only",
    "unknown",
}
CONFLICT_FIELDS = (
    "overall_recommendation",
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
    "vector_only_fairness",
    "graph_facts_only_fairness",
    "hybrid_vector_graph_fairness",
    "gold_context_fairness",
)
FALLBACK_KEYWORDS = (
    "agreement",
    "disagreement",
    "blocking issue",
    "blocker",
    "warning",
    "recommendation",
    "action item",
    "pass",
    "fail",
    "pending",
    "conditional_go",
    "no_go",
    "go",
    "vector_only",
    "graph_facts_only",
    "hybrid_vector_graph",
    "gold_context",
    "neo4j",
    "input isolation",
    "scorer",
    "prompt",
    "leakage",
    "contamination",
)

MANIFEST_ITEMS = (
    (
        "outputs/kg_build/curation_round_02",
        "round02_curation_baseline",
        "Protected Round 02 curation baseline. Reference only.",
    ),
    (
        "outputs/kg_build/eval_round02",
        "round02_eval_baseline",
        "Protected Round 02 evaluation baseline. Reference only.",
    ),
    (
        "outputs/round3_case_factory/round3_selected_cases.jsonl",
        "original_round3_candidate_pool",
        "Original candidate pool. Must not be modified.",
    ),
    (
        "outputs/round3_case_factory/round3_required_facts.jsonl",
        "original_round3_required_facts",
        "Original candidate required facts. Must not be modified.",
    ),
    (
        "outputs/round3_case_factory/round3_dev_cases.json",
        "original_round3_dev_split",
        "Original dev split. Reference only.",
    ),
    (
        "outputs/round3_case_factory/round3_test_cases.json",
        "original_round3_test_split",
        "Held-out test split. No prompt/Cypher tuning.",
    ),
    (
        "outputs/round3_case_factory/round3_baseline_control_cases.json",
        "original_round3_baseline_controls",
        "Baseline controls. Reference only.",
    ),
    (
        "outputs/round3_case_factory/round3_integration_demo_cases.json",
        "integration_demo_cases",
        "Demo-only cases. Must not enter scoring benchmark metrics.",
    ),
    (
        "outputs/round3_case_factory_review/preflight_validation_report.md",
        "preflight_review_report",
        "Preflight validation report. Reference only.",
    ),
    (
        "outputs/round3_case_factory_review/go_no_go_decision.md",
        "preflight_go_no_go",
        "Preflight decision. Reference only.",
    ),
    (
        "outputs/round3_case_factory_repaired/eval_ready_cases.jsonl",
        "repaired_eval_ready_cases",
        "Repaired local-evidence-ready cases. Must not be modified.",
    ),
    (
        "outputs/round3_case_factory_repaired/eval_ready_required_facts.jsonl",
        "repaired_eval_ready_required_facts",
        "Repaired required facts. Must not be modified.",
    ),
    (
        "outputs/round3_case_factory_repaired/company_ticker_patch_review.jsonl",
        "company_ticker_review",
        "Company/ticker review-only patch record. Must not be auto-applied.",
    ),
    (
        "outputs/round3_case_factory_repaired/parser_artifact_exclusions.jsonl",
        "parser_artifact_exclusions",
        "Parser artifact exclusions. Reference only.",
    ),
    (
        "outputs/round3_case_factory_repaired/exact_quote_recovery_report.md",
        "exact_quote_report",
        "Exact quote recovery report. Reference only.",
    ),
    (
        "outputs/round3_case_factory_repaired/neo4j_readonly_coverage_report.md",
        "repaired_neo4j_coverage_report",
        "Existing coverage status report. Script does not run Neo4j.",
    ),
    (
        "outputs/round3_case_factory_repaired/go_no_go_decision.md",
        "repaired_go_no_go",
        "Repaired subset decision. Reference only.",
    ),
)


@dataclass
class WarningRecord:
    reviewer: str
    file: str
    message: str
    severity: str = "warning"


@dataclass
class MergeContext:
    run_dir: Path
    strict: bool
    generated_at: str = field(default_factory=lambda: datetime.now().isoformat(timespec="seconds"))
    missing_files: list[str] = field(default_factory=list)
    warnings: list[WarningRecord] = field(default_factory=list)
    files_read: list[str] = field(default_factory=list)
    files_generated: list[str] = field(default_factory=list)
    parse_results: dict[str, str] = field(default_factory=dict)


def rel(path: Path) -> str:
    try:
        return path.resolve().relative_to(REPO_ROOT.resolve()).as_posix()
    except ValueError:
        return str(path)


def read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def write_text(path: Path, text: str, ctx: MergeContext | None = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text.rstrip() + "\n", encoding="utf-8", newline="\n")
    if ctx is not None:
        ctx.files_generated.append(rel(path))


def write_json(path: Path, data: Any, ctx: MergeContext | None = None) -> None:
    write_text(path, json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True), ctx)


def write_jsonl(path: Path, rows: list[dict[str, Any]], ctx: MergeContext | None = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
    if ctx is not None:
        ctx.files_generated.append(rel(path))


def default_gate_assessments() -> dict[str, str]:
    return {
        "artifact_freeze": "pending",
        "gemini_semantic_review": "not_required_historical",
        "gemini_prompt_fairness_review": "not_required_historical",
        "neo4j_readonly_coverage": "pending",
        "executable_method_prompts": "pending",
        "required_fact_recall_scorer": "pending",
        "dry_run": "pending",
        "input_isolation": "pending",
        "opik_trace_completeness": "pending",
        "full_eval_lock": "locked",
    }


def empty_review(reviewer: str, *, present: bool, parsed: bool, status: str = "pending") -> dict[str, Any]:
    return {
        "present": present,
        "parsed": parsed,
        "status": status,
        "overall_recommendation": "pending",
        "blocking_issues": [],
        "non_blocking_warnings": [],
        "agreements": [],
        "disagreements": [],
        "method_issues": {method: [] for method in METHODS},
        "gate_assessments": default_gate_assessments(),
        "recommended_actions": [],
        "files_referenced": [],
        "front_matter": {},
        "fallback_keywords": [],
    }


def normalize_gate_status(value: Any) -> str:
    if value is None:
        return "unknown"
    text = str(value).strip().lower().replace(" ", "_").replace("-", "_")
    aliases = {
        "ok": "pass",
        "passed": "pass",
        "true": "pass",
        "blocker": "fail",
        "blocked": "blocked",
        "missing": "pending",
        "not_checked": "pending",
        "not_passed": "pending",
        "unlocked": "unlocked_proposal_only",
        "unlock": "unlocked_proposal_only",
        "proposal_unlocked": "unlocked_proposal_only",
        "proposed_unlocked": "unlocked_proposal_only",
    }
    text = aliases.get(text, text)
    return text if text in ALLOWED_GATE_STATUSES else "unknown"


def normalize_overall(value: Any) -> str:
    if value is None:
        return "pending"
    text = str(value).strip().lower().replace(" ", "_").replace("-", "_")
    if text in {"final_go", "full_go", "approved", "go"}:
        return "go"
    if "conditional" in text and "block" in text:
        return "conditional_go_with_blockers"
    if "conditional" in text:
        return "conditional_go"
    if "no_go" in text or text == "nogo":
        return "no_go"
    if "block" in text:
        return "blocked"
    if "unlock" in text:
        return "unlocked_proposal_only"
    return text or "pending"


def listify(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    return [value]


def normalize_review_data(reviewer: str, data: dict[str, Any], front_matter: dict[str, str]) -> dict[str, Any]:
    review = empty_review(reviewer, present=True, parsed=True, status="pending")
    review["front_matter"] = front_matter
    review["status"] = str(data.get("status") or front_matter.get("status") or "pending").strip().lower()
    review["overall_recommendation"] = normalize_overall(
        data.get("overall_recommendation") or data.get("round3_decision") or front_matter.get("round3_decision")
    )
    for key in ("blocking_issues", "non_blocking_warnings", "agreements", "disagreements", "recommended_actions", "files_referenced"):
        review[key] = [str(item) if not isinstance(item, dict) else item for item in listify(data.get(key))]
    method_issues = data.get("method_issues") if isinstance(data.get("method_issues"), dict) else {}
    review["method_issues"] = {method: [str(item) for item in listify(method_issues.get(method))] for method in METHODS}
    gate_data = data.get("gate_assessments") if isinstance(data.get("gate_assessments"), dict) else {}
    gates = default_gate_assessments()
    for gate in GATES:
        if gate in gate_data:
            gates[gate] = normalize_gate_status(gate_data.get(gate))
    review["gate_assessments"] = gates
    for fairness in ("vector_only_fairness", "graph_facts_only_fairness", "hybrid_vector_graph_fairness", "gold_context_fairness"):
        if fairness in data:
            review[fairness] = normalize_gate_status(data.get(fairness))
    return review


def parse_front_matter(text: str) -> tuple[dict[str, str], str]:
    if not text.startswith("---"):
        return {}, text
    match = re.match(r"\A---\s*\n(.*?)\n---\s*\n?", text, flags=re.DOTALL)
    if not match:
        return {}, text
    front: dict[str, str] = {}
    for line in match.group(1).splitlines():
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        front[key.strip()] = value.strip().strip('"').strip("'")
    return front, text[match.end() :]


def extract_machine_json(text: str) -> tuple[dict[str, Any] | None, str | None]:
    heading = re.search(r"^#\s+Machine Readable Review\s*$", text, flags=re.IGNORECASE | re.MULTILINE)
    search_from = heading.end() if heading else 0
    block = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text[search_from:], flags=re.DOTALL | re.IGNORECASE)
    if not block:
        return None, "machine-readable JSON block not found"
    raw = block.group(1)
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        return None, f"machine-readable JSON parse failed: {exc}"
    if not isinstance(parsed, dict):
        return None, "machine-readable JSON block is not an object"
    return parsed, None


def collect_matching_lines(text: str, patterns: tuple[str, ...]) -> list[str]:
    rows: list[str] = []
    for raw in text.splitlines():
        line = raw.strip(" -*\t")
        if not line:
            continue
        low = line.lower()
        if any(pattern in low for pattern in patterns):
            rows.append(line)
    return rows


def fallback_extract(reviewer: str, text: str, front_matter: dict[str, str]) -> dict[str, Any]:
    review = empty_review(reviewer, present=True, parsed=True, status=front_matter.get("status", "pending"))
    review["front_matter"] = front_matter
    low = text.lower()
    review["fallback_keywords"] = [kw for kw in FALLBACK_KEYWORDS if kw.lower() in low]
    if "conditional_go" in low:
        review["overall_recommendation"] = "conditional_go"
    elif re.search(r"\bno_go\b|\bno go\b", low):
        review["overall_recommendation"] = "no_go"
    elif "blocked" in low:
        review["overall_recommendation"] = "blocked"
    elif re.search(r"\bfinal go\b|\bfull go\b|\bfull_eval.*go\b", low):
        review["overall_recommendation"] = "go"
    elif re.search(r"\bgo\b", low):
        review["overall_recommendation"] = "go"
    review["blocking_issues"] = collect_matching_lines(text, ("blocking issue", "blocker", "p0", "must not", "critical"))
    review["non_blocking_warnings"] = collect_matching_lines(text, ("warning", "risk", "caution"))
    review["agreements"] = collect_matching_lines(text, ("agreement", "agree"))
    review["disagreements"] = collect_matching_lines(text, ("disagreement", "disagree", "conflict"))
    review["recommended_actions"] = collect_matching_lines(text, ("recommendation", "action item", "next step", "should", "must"))
    for method in METHODS:
        review["method_issues"][method] = collect_matching_lines(text, (method,))
    gates = default_gate_assessments()
    if "neo4j" in low:
        if any(term in low for term in ("not passed", "not checked", "missing", "pending", "blocked")):
            gates["neo4j_readonly_coverage"] = "pending"
        elif "fail" in low:
            gates["neo4j_readonly_coverage"] = "fail"
        elif "pass" in low:
            gates["neo4j_readonly_coverage"] = "pass"
    if "gemini" in low and "semantic" in low:
        if "fail" in low or "blocker" in low:
            gates["gemini_semantic_review"] = "fail"
        elif "pass" in low:
            gates["gemini_semantic_review"] = "pass"
    if "prompt" in low and "fairness" in low:
        if "fail" in low or "blocker" in low:
            gates["gemini_prompt_fairness_review"] = "fail"
        elif "pass" in low:
            gates["gemini_prompt_fairness_review"] = "pass"
    if "full_eval_lock" in low or "full eval" in low or "full evaluation" in low:
        if "unlock" in low:
            gates["full_eval_lock"] = "unlocked_proposal_only"
        elif "locked" in low:
            gates["full_eval_lock"] = "locked"
    if "dry-run" in low or "dry_run" in low:
        if "pass" in low:
            gates["dry_run"] = "pass"
        elif "block" in low or "locked" in low:
            gates["dry_run"] = "blocked"
    review["gate_assessments"] = gates
    return review


def parse_review_file(reviewer: str, path: Path, ctx: MergeContext) -> dict[str, Any]:
    if not path.exists():
        ctx.missing_files.append(rel(path))
        ctx.parse_results[reviewer] = "missing"
        return empty_review(reviewer, present=False, parsed=False)
    text = read_text(path)
    ctx.files_read.append(rel(path))
    if not text.strip():
        ctx.warnings.append(WarningRecord(reviewer, rel(path), "review file is empty"))
        ctx.parse_results[reviewer] = "empty"
        return empty_review(reviewer, present=True, parsed=False)
    front_matter, body = parse_front_matter(text)
    data, error = extract_machine_json(body)
    if data is not None:
        ctx.parse_results[reviewer] = "machine_json"
        return normalize_review_data(reviewer, data, front_matter)
    ctx.warnings.append(WarningRecord(reviewer, rel(path), error or "machine-readable JSON unavailable; fallback extraction used"))
    review = fallback_extract(reviewer, text, front_matter)
    if not review["fallback_keywords"]:
        ctx.warnings.append(WarningRecord(reviewer, rel(path), "fallback extraction found no known keywords", severity="error"))
        review["parsed"] = False
        ctx.parse_results[reviewer] = "unparsable"
    else:
        ctx.parse_results[reviewer] = "fallback"
    return review


def template_for(reviewer: str) -> str:
    gates = default_gate_assessments()
    payload = {
        "reviewer": reviewer,
        "status": "pending",
        "overall_recommendation": "conditional_go",
        "blocking_issues": [],
        "non_blocking_warnings": [],
        "agreements": [],
        "disagreements": [],
        "method_issues": {method: [] for method in METHODS},
        "gate_assessments": gates,
        "recommended_actions": [],
        "files_referenced": [],
    }
    return f"""---
reviewer: {reviewer}
review_type: {REVIEW_TYPES[reviewer]}
status: pending
round3_decision: conditional_go
---

# Reviewer Summary

Write a short human-readable summary here.

# Machine Readable Review

```json
{json.dumps(payload, ensure_ascii=False, indent=2)}
```

# Human Notes

Paste the model's raw answer or additional notes here.
"""


def resolve_run_dir(args: argparse.Namespace) -> Path:
    if args.run_dir:
        path = Path(args.run_dir)
        return path if path.is_absolute() else REPO_ROOT / path
    if args.latest:
        BASE_DIR.mkdir(parents=True, exist_ok=True)
        dirs = [p for p in BASE_DIR.iterdir() if p.is_dir()]
        if dirs:
            return max(dirs, key=lambda p: p.stat().st_mtime)
    if PREFERRED_RUN.exists():
        return PREFERRED_RUN
    return BASE_DIR / datetime.now().strftime("%Y%m%d_%H%M%S")


def ensure_structure(ctx: MergeContext) -> None:
    for subdir in ("input", "reviews", "reviews/templates", "merged", "logs"):
        (ctx.run_dir / subdir).mkdir(parents=True, exist_ok=True)
    for reviewer in REVIEWERS:
        write_text(ctx.run_dir / "reviews" / "templates" / f"{reviewer}_review_template.md", template_for(reviewer), ctx)
    placeholders = {
        "input/cases.jsonl": "",
        "input/method_prompts.md": "# Method Prompts\n\nPending. Do not run dry-run/full eval until executable four-method prompts exist.\n",
        "input/scoring_config.md": "# Scoring Config\n\nPending. Required fact recall scorer is not implemented by this orchestration script.\n",
        "input/judge_prompt.md": "# Judge Prompt\n\nPending. This file-based orchestration layer does not call judges or model APIs.\n",
    }
    for rel_path, content in placeholders.items():
        target = ctx.run_dir / rel_path
        if not target.exists():
            write_text(target, content, ctx)


def manifest_item(path_text: str, role: str, notes: str, run_dir: Path) -> dict[str, Any]:
    if "<timestamp>" in path_text:
        path_text = path_text.replace("<timestamp>", run_dir.name)
    path = REPO_ROOT / path_text
    if path.exists() and path.is_file():
        typ = "file"
    elif path.exists() and path.is_dir():
        typ = "directory"
    else:
        typ = "missing"
    return {
        "path": path_text.replace("\\", "/"),
        "exists": path.exists(),
        "type": typ,
        "role": role,
        "should_modify": False,
        "notes": notes if path.exists() else f"Missing. {notes}",
    }


def create_manifest(ctx: MergeContext) -> dict[str, Any]:
    items = [manifest_item(path, role, notes, ctx.run_dir) for path, role, notes in MANIFEST_ITEMS]
    packet_rel = f"outputs/round3_orchestration/{ctx.run_dir.name}/gemini_review_packet"
    items.append(
        manifest_item(
            packet_rel,
            "gemini_review_packet",
            "Existing Gemini review packet if present. Reference only.",
            ctx.run_dir,
        )
    )
    manifest = {
        "script_version": SCRIPT_VERSION,
        "generated_at": ctx.generated_at,
        "run_dir": rel(ctx.run_dir),
        "items": items,
        "safety": {
            "should_modify_context_files": False,
            "neo4j_connection_attempted": False,
            "model_api_called": False,
            "full_eval_executed": False,
            "dry_run_executed": False,
        },
    }
    write_json(ctx.run_dir / "input" / "manifest.json", manifest, ctx)
    rows = [
        "# Context Files",
        "",
        "| Path | Exists | Type | Role | Should Modify | Notes |",
        "|---|---:|---|---|---:|---|",
    ]
    for item in items:
        rows.append(
            f"| `{item['path']}` | {item['exists']} | {item['type']} | {item['role']} | {item['should_modify']} | {item['notes']} |"
        )
    write_text(ctx.run_dir / "input" / "context_files.md", "\n".join(rows), ctx)
    return manifest


def create_readme(ctx: MergeContext) -> None:
    run_rel = rel(ctx.run_dir)
    write_text(
        ctx.run_dir / "README_file_based_orchestration.md",
        f"""# File-Based Multi-Agent Orchestration Workflow

## 1. Initialize

```bash
python scripts/merge_multi_agent_reviews.py --init-only --run-dir {run_rel}
```

## 2. Paste Model Responses

Paste each model response into:

```text
reviews/gpt_review.md
reviews/gemini_review.md
reviews/codex_review.md
reviews/antigravity_review.md
```

## 3. Merge Reviews

```bash
python scripts/merge_multi_agent_reviews.py --run-dir {run_rel}
```

## 4. Read Outputs

```text
merged/final_orchestration_report.md
merged/action_items.md
merged/conflict_matrix.md
merged/gate_status.md
```

## Recommended Copy/Paste Convention

- Paste each model's answer below the template.
- If possible, fill the JSON block at the top.
- If not possible, paste the raw answer under `# Human Notes`.
- Re-run the merge script after adding or editing any review file.

## Safety Boundary

This layer merges review files only. It does not connect to Neo4j, call model APIs, run dry-run/full eval, or modify candidate/KG artifacts.
""",
        ctx,
    )


def is_full_go(value: str) -> bool:
    text = normalize_overall(value)
    return text == "go"


def recommends_eval(review: dict[str, Any]) -> bool:
    fields: list[str] = [str(review.get("overall_recommendation", ""))]
    fields.extend(str(item) for item in review.get("recommended_actions", []))
    fields.extend(str(item) for item in review.get("blocking_issues", []))
    text = "\n".join(fields).lower()
    return any(term in text for term in ("dry-run", "dry_run", "full eval", "full_eval", "full evaluation", "benchmark run"))


def review_text_blob(review: dict[str, Any]) -> str:
    parts: list[str] = [
        str(review.get("overall_recommendation", "")),
        *[str(x) for x in review.get("blocking_issues", [])],
        *[str(x) for x in review.get("non_blocking_warnings", [])],
        *[str(x) for x in review.get("recommended_actions", [])],
        *[str(x) for x in review.get("agreements", [])],
        *[str(x) for x in review.get("disagreements", [])],
    ]
    for items in review.get("method_issues", {}).values():
        parts.extend(str(x) for x in items)
    return "\n".join(parts).lower()


def generate_conflicts(normalized: dict[str, Any], gate_status: dict[str, str]) -> dict[str, Any]:
    reviewers = normalized["reviewers"]
    fields: dict[str, dict[str, str]] = {}
    conflicts: list[dict[str, Any]] = []
    for field in CONFLICT_FIELDS:
        judgments: dict[str, str] = {}
        for reviewer, review in reviewers.items():
            if not review.get("present") or not review.get("parsed"):
                continue
            if field == "overall_recommendation":
                value = str(review.get("overall_recommendation", "pending"))
            elif field in GATES:
                value = str(review.get("gate_assessments", {}).get(field, "pending"))
            else:
                value = str(review.get(field, "unknown"))
            judgments[reviewer] = value
        fields[field] = judgments
        values = set(judgments.values())
        if "pass" in values and any(v in {"fail", "blocked"} for v in values):
            conflicts.append(
                {
                    "field": field,
                    "severity": "conflict",
                    "rule": "pass_vs_fail_or_blocked",
                    "reviewer_values": judgments,
                    "description": f"{field} has both pass and fail/blocked judgments.",
                }
            )

    any_required_pending_or_fail = any(gate_status.get(gate) in {"pending", "fail", "blocked", "unknown"} for gate in REQUIRED_GATES)
    for reviewer, review in reviewers.items():
        lock = str(review.get("gate_assessments", {}).get("full_eval_lock", "locked"))
        if lock in {"unlocked", "unlocked_proposal_only"} and any_required_pending_or_fail:
            conflicts.append(
                {
                    "field": "full_eval_lock",
                    "severity": "critical",
                    "rule": "full_eval_unlocked_before_required_gates",
                    "reviewer_values": {reviewer: lock},
                    "description": "A reviewer proposed unlocking full eval while required gates remain pending/fail/blocked.",
                }
            )

    neo4j_status = gate_status.get("neo4j_readonly_coverage", "pending")
    if neo4j_status in {"pending", "fail", "blocked", "unknown"}:
        full_go_reviewers = {
            reviewer: review.get("overall_recommendation")
            for reviewer, review in reviewers.items()
            if is_full_go(str(review.get("overall_recommendation", "")))
        }
        if full_go_reviewers:
            conflicts.append(
                {
                    "field": "neo4j_readonly_coverage",
                    "severity": "critical",
                    "rule": "neo4j_missing_but_full_go",
                    "reviewer_values": full_go_reviewers,
                    "description": "Neo4j coverage is missing/pending but at least one reviewer recommends full go.",
                }
            )

    if gate_status.get("executable_method_prompts") in {"pending", "fail", "blocked", "unknown"}:
        recs = {r: v.get("overall_recommendation") for r, v in reviewers.items() if recommends_eval(v)}
        if recs:
            conflicts.append(
                {
                    "field": "executable_method_prompts",
                    "severity": "conflict",
                    "rule": "missing_prompts_but_eval_recommended",
                    "reviewer_values": recs,
                    "description": "Executable method prompts are missing/pending but dry-run/full eval was recommended.",
                }
            )
    if gate_status.get("required_fact_recall_scorer") in {"pending", "fail", "blocked", "unknown"}:
        recs = {r: v.get("overall_recommendation") for r, v in reviewers.items() if recommends_eval(v)}
        if recs:
            conflicts.append(
                {
                    "field": "required_fact_recall_scorer",
                    "severity": "conflict",
                    "rule": "missing_scorer_but_eval_recommended",
                    "reviewer_values": recs,
                    "description": "Required fact recall scorer is missing/pending but dry-run/full eval was recommended.",
                }
            )

    for reviewer, review in reviewers.items():
        blob = review_text_blob(review)
        if "integration demo" in blob or "integration_demo" in blob:
            if "scoring" in blob or "benchmark" in blob:
                conflicts.append(
                    {
                        "field": "integration_demo",
                        "severity": "critical",
                        "rule": "integration_demo_in_scoring",
                        "reviewer_values": {reviewer: "integration demo mentioned with scoring/benchmark"},
                        "description": "Integration demo appears in a scoring benchmark recommendation.",
                    }
                )
        if "round3_test" in blob and ("prompt tuning" in blob or "cypher tuning" in blob or "tune prompt" in blob or "tune cypher" in blob):
            conflicts.append(
                {
                    "field": "round3_test",
                    "severity": "critical",
                    "rule": "round3_test_tuning",
                    "reviewer_values": {reviewer: "round3_test tuning mentioned"},
                    "description": "round3_test appears to be used for prompt/Cypher tuning.",
                }
            )

    return {"fields": fields, "conflicts": conflicts}


def aggregate_gate_status(reviewers: dict[str, dict[str, Any]]) -> dict[str, str]:
    status = default_gate_assessments()
    for gate in REQUIRED_GATES:
        values = [
            review.get("gate_assessments", {}).get(gate)
            for review in reviewers.values()
            if review.get("present") and review.get("parsed")
        ]
        values = [normalize_gate_status(v) for v in values if v is not None]
        if "fail" in values:
            status[gate] = "fail"
        elif "blocked" in values:
            status[gate] = "blocked"
        elif "warning" in values:
            status[gate] = "warning"
        elif values and all(v == "pass" for v in values):
            status[gate] = "pass"
        elif "pass" in values:
            status[gate] = "warning"
        else:
            status[gate] = "pending"
    if all(status[gate] == "pass" for gate in REQUIRED_GATES):
        status["full_eval_lock"] = "unlocked_proposal_only"
    else:
        status["full_eval_lock"] = "locked"
    return status


def apply_local_gate_overrides(run_dir: Path, gate_status: dict[str, str]) -> dict[str, str]:
    """Use generated local gate ledgers to prevent stale review blockers from reappearing."""
    gates = dict(gate_status)
    prompt_path = run_dir / "prompt_scorer_gate_status.md"
    prompt_text = prompt_path.read_text(encoding="utf-8").lower() if prompt_path.exists() else ""
    if "executable_method_prompts: pass" in prompt_text:
        gates["executable_method_prompts"] = "pass"
    if "required_fact_recall_scorer: pass" in prompt_text:
        gates["required_fact_recall_scorer"] = "pass"
    if "input_isolation_validation: pass" in prompt_text:
        gates["input_isolation"] = "pass"
    gates["gemini_semantic_review"] = "not_required_historical"
    gates["gemini_prompt_fairness_review"] = "not_required_historical"

    neo4j_text = ""
    for name in ("neo4j_coverage_gate_status.md", "neo4j_readonly_coverage_report.md"):
        path = run_dir / name
        if path.exists():
            neo4j_text += "\n" + path.read_text(encoding="utf-8").lower()
    if "neo4j_readonly_coverage: pass" in neo4j_text:
        gates["neo4j_readonly_coverage"] = "pass"
    elif "neo4j_readonly_coverage: warning" in neo4j_text:
        gates["neo4j_readonly_coverage"] = "warning"
    elif "neo4j_readonly_coverage: blocked" in neo4j_text or "not_checked_no_neo4j_config" in neo4j_text:
        gates["neo4j_readonly_coverage"] = "blocked"
    if gates.get("full_eval_lock") != "unlocked_proposal_only":
        gates["full_eval_lock"] = "locked"
    return gates


def is_resolved_stale_issue(issue: Any, gate_status: dict[str, str]) -> bool:
    text = str(issue).lower()
    prompt_resolved = gate_status.get("executable_method_prompts") == "pass"
    scorer_resolved = gate_status.get("required_fact_recall_scorer") == "pass"
    if prompt_resolved and (
        any(term in text for term in ("four-method", "method prompt", "prompts", "prompt"))
        or ("method" in text and "blocker" in text)
    ):
        return True
    if scorer_resolved and any(term in text for term in ("required_fact_recall", "numeric_correctness", "scorer", "evaluator")):
        return True
    return False


def action(
    idx: int,
    priority: str,
    owner: str,
    title: str,
    description: str,
    source_reviewers: list[str] | None = None,
    blocks: list[str] | None = None,
    next_step: str | None = None,
    status: str = "open",
) -> dict[str, Any]:
    return {
        "action_id": f"A{idx:03d}",
        "priority": priority,
        "owner": owner,
        "status": status,
        "title": title,
        "description": description,
        "source_reviewers": source_reviewers or [],
        "blocks": blocks or [],
        "recommended_next_step": next_step or description,
    }


def generate_actions(
    normalized: dict[str, Any],
    conflicts: dict[str, Any],
    gate_status: dict[str, str],
    missing_files: list[str],
) -> list[dict[str, Any]]:
    actions: list[dict[str, Any]] = []

    def add(priority: str, owner: str, title: str, description: str, **kwargs: Any) -> None:
        actions.append(action(len(actions) + 1, priority, owner, title, description, **kwargs))

    for reviewer, review in normalized["reviewers"].items():
        for issue in review.get("blocking_issues", []):
            if is_resolved_stale_issue(issue, gate_status):
                continue
            add(
                "P0",
                "user",
                f"Resolve blocker from {reviewer}",
                str(issue),
                source_reviewers=[reviewer],
                blocks=["dry_run", "full_eval"],
            )
        for rec in review.get("recommended_actions", []):
            add(
                "P1",
                reviewer,
                f"Review recommended action from {reviewer}",
                str(rec),
                source_reviewers=[reviewer],
                blocks=[],
                status="pending_review",
            )

    for conflict in conflicts["conflicts"]:
        add(
            "P0" if conflict["severity"] == "critical" else "P1",
            "antigravity",
            f"Resolve {conflict['severity']} conflict: {conflict['rule']}",
            conflict["description"],
            source_reviewers=list(conflict.get("reviewer_values", {}).keys()),
            blocks=["dry_run", "full_eval"] if conflict["severity"] == "critical" else ["full_eval"],
        )

    for missing in missing_files:
        if "/reviews/" in missing:
            reviewer = Path(missing).name.replace("_review.md", "")
            if reviewer in ADVISORY_REVIEWERS:
                add(
                    "P2",
                    reviewer,
                    f"Track optional {reviewer} advisory",
                    f"`{missing}` is optional advisory input. If a direct advisory response arrives, save or sync it into the review file; do not block gates on it.",
                    source_reviewers=[],
                    blocks=[],
                )
                continue
            add(
                "P1",
                reviewer if reviewer in REVIEWERS else "user",
                f"Provide {reviewer} review",
                f"Paste the {reviewer} review into `{missing}`.",
                source_reviewers=[],
                blocks=["full_eval"],
            )

    default_specs = [
        (
            "executable_method_prompts",
            "P0",
            "codex",
            "Provide or implement executable four-method prompts",
            "Create executable prompts for vector_only, graph_facts_only, hybrid_vector_graph, and gold_context before dry-run/full eval.",
            ["dry_run", "full_eval"],
        ),
        (
            "required_fact_recall_scorer",
            "P0",
            "codex",
            "Implement executable required_fact_recall scorer",
            "Implement or provide the required_fact_recall scorer before dry-run/full eval.",
            ["dry_run", "full_eval"],
        ),
        (
            "neo4j_readonly_coverage",
            "P0",
            "codex",
            "Complete Neo4j read-only coverage",
            "Run read-only coverage only after config exists; do not perform writes.",
            ["dry_run", "full_eval"],
        ),
        (
            "dry_run",
            "P1",
            "codex",
            "Run small dry-run only after gates pass",
            "Keep dry-run locked until semantic review, prompt/scorer, Neo4j coverage, and isolation gates pass.",
            ["full_eval"],
        ),
        (
            "full_eval_lock",
            "P0",
            "antigravity",
            "Keep full eval locked until Antigravity final approval",
            "The script can only report unlocked_proposal_only; actual approval must come from Antigravity outside this script.",
            ["full_eval"],
        ),
    ]
    for gate, priority, owner, title, description, blocks in default_specs:
        if gate == "full_eval_lock":
            if gate_status.get(gate) == "locked":
                add(priority, owner, title, description, blocks=blocks)
        elif gate_status.get(gate) in {"pending", "fail", "blocked", "unknown", "warning"}:
            add(priority, owner, title, description, blocks=blocks)
    return actions


def current_decision(conflicts: dict[str, Any], actions: list[dict[str, Any]], gate_status: dict[str, str]) -> str:
    if any(conflict["severity"] == "critical" for conflict in conflicts["conflicts"]):
        return "blocked"
    if any(item["priority"] == "P0" and item["status"] in {"open", "blocked", "pending_review"} for item in actions):
        return "conditional_go_with_blockers"
    if all(gate_status[gate] == "pass" for gate in REQUIRED_GATES):
        return "unlocked_proposal_only"
    return "conditional_go"


def render_gate_status(gate_status: dict[str, str]) -> str:
    rows = ["# Gate Status", "", "| Gate | Status |", "|---|---|"]
    rows.extend(f"| `{gate}` | {status} |" for gate, status in gate_status.items())
    return "\n".join(rows)


def render_conflicts(conflicts: dict[str, Any]) -> str:
    rows = ["# Conflict Matrix", "", "## Reviewer Judgments", ""]
    rows.append("| Field | Judgments |")
    rows.append("|---|---|")
    for field, judgments in conflicts["fields"].items():
        rows.append(f"| `{field}` | `{json.dumps(judgments, ensure_ascii=False, sort_keys=True)}` |")
    rows.append("")
    rows.append("## Conflicts")
    rows.append("")
    if not conflicts["conflicts"]:
        rows.append("No conflicts detected.")
    else:
        rows.append("| Severity | Rule | Field | Description |")
        rows.append("|---|---|---|---|")
        for conflict in conflicts["conflicts"]:
            rows.append(
                f"| {conflict['severity']} | `{conflict['rule']}` | `{conflict['field']}` | {conflict['description']} |"
            )
    return "\n".join(rows)


def render_actions(actions: list[dict[str, Any]]) -> str:
    rows = ["# Action Items", ""]
    if not actions:
        rows.append("No open action items.")
        return "\n".join(rows)
    rows.append("| ID | Priority | Owner | Status | Title | Blocks |")
    rows.append("|---|---|---|---|---|---|")
    for item in actions:
        rows.append(
            f"| {item['action_id']} | {item['priority']} | {item['owner']} | {item['status']} | {item['title']} | `{', '.join(item['blocks'])}` |"
        )
    rows.append("")
    for item in actions:
        rows.append(f"## {item['action_id']} - {item['title']}")
        rows.append("")
        rows.append(item["description"])
        rows.append("")
        rows.append(f"Next step: {item['recommended_next_step']}")
        rows.append("")
    return "\n".join(rows)


def render_final_report(
    normalized: dict[str, Any],
    conflicts: dict[str, Any],
    actions: list[dict[str, Any]],
    gate_status: dict[str, str],
    manifest: dict[str, Any],
    decision: str,
) -> str:
    reviewers = normalized["reviewers"]
    present = [name for name, review in reviewers.items() if review.get("present")]
    missing = [name for name, review in reviewers.items() if not review.get("present")]
    required_missing = [name for name in missing if name in REQUIRED_REVIEWERS]
    advisory_missing = [name for name in missing if name in ADVISORY_REVIEWERS]
    rows = [
        "# Round 3 Multi-Agent Orchestration Report",
        "",
        "## Run Info",
        f"- run_dir: `{normalized['run_dir']}`",
        f"- generated_at: `{normalized['generated_at']}`",
        f"- reviewers present: {', '.join(present) if present else 'none'}",
        f"- required reviewers missing: {', '.join(required_missing) if required_missing else 'none'}",
        f"- advisory reviewers missing: {', '.join(advisory_missing) if advisory_missing else 'none'}",
        "",
        "## Current Decision",
        f"- decision: `{decision}`",
        f"- full eval lock status: `{gate_status['full_eval_lock']}`",
        "",
        "## Gate Status Summary",
        "",
    ]
    rows.extend(f"- `{gate}`: {status}" for gate, status in gate_status.items())
    rows.append("")
    rows.append("## Reviewer Summaries")
    for reviewer in REVIEWERS:
        review = reviewers[reviewer]
        rows.extend(
            [
                f"### {reviewer.upper() if reviewer == 'gpt' else reviewer.capitalize()}",
                f"- present: {review['present']}",
                f"- parsed: {review['parsed']}",
                f"- status: {review['status']}",
                f"- overall_recommendation: {review['overall_recommendation']}",
                f"- blocking_issues: {len(review['blocking_issues'])}",
                f"- warnings: {len(review['non_blocking_warnings'])}",
                "",
            ]
        )
    agreements = [item for review in reviewers.values() for item in review.get("agreements", [])]
    disagreements = [item for review in reviewers.values() for item in review.get("disagreements", [])]
    blockers = [
        f"{reviewer}: {issue}"
        for reviewer, review in reviewers.items()
        for issue in review.get("blocking_issues", [])
    ]
    rows.extend(["## Agreements", ""])
    rows.extend([f"- {item}" for item in agreements] or ["No explicit agreements recorded."])
    rows.extend(["", "## Disagreements / Conflicts", ""])
    rows.extend([f"- {item}" for item in disagreements] or ["No explicit disagreements recorded."])
    rows.extend(
        [
            f"- {conflict['severity']}: {conflict['description']}"
            for conflict in conflicts["conflicts"]
        ]
        or ["No conflict rules triggered."]
    )
    rows.extend(["", "## Blocking Issues", ""])
    rows.extend([f"- {item}" for item in blockers] or ["No reviewer blocking issues recorded."])
    rows.extend(["", "## Method-Level Issues", ""])
    for method in METHODS:
        rows.append(f"### {method}")
        method_rows = [
            f"{reviewer}: {issue}"
            for reviewer, review in reviewers.items()
            for issue in review.get("method_issues", {}).get(method, [])
        ]
        rows.extend([f"- {item}" for item in method_rows] or ["- No method-level issues recorded."])
        rows.append("")
    rows.extend(["## Action Items", ""])
    rows.extend([f"- {item['action_id']} [{item['priority']}] {item['title']}" for item in actions] or ["No open action items."])
    round02_items = [
        item
        for item in manifest.get("items", [])
        if item["role"] in {"round02_curation_baseline", "round02_eval_baseline"}
    ]
    round02_state = "unknown"
    if round02_items and all(item["exists"] for item in round02_items):
        round02_state = "pass"
    rows.extend(
        [
            "",
            "## Safety Check",
            f"- Round 02 modified: {round02_state} based on manifest only",
            "- repaired subset modified by this script: false",
            "- Neo4j write performed: false",
            "- KG patch applied: false",
            "- full eval executed: false",
            "",
            "## Next Recommended Step",
            "",
        ]
    )
    if decision == "blocked":
        rows.append("Resolve critical conflicts before any dry-run or full evaluation work.")
    elif decision == "conditional_go_with_blockers":
        rows.append("Close P0 action items, especially prompt/scorer and Neo4j coverage gates. Gemini artifacts are historical only.")
    elif decision == "unlocked_proposal_only":
        rows.append("Ask Antigravity for explicit final approval outside this script before any full evaluation.")
    else:
        rows.append("Continue file-based orchestration; optional GPT advisory is used when available but is not a gate blocker.")
    return "\n".join(rows)


def write_logs(ctx: MergeContext) -> None:
    missing_rows = ["# Missing Files", ""]
    if ctx.missing_files:
        missing_rows.extend(f"- `{path}`" for path in ctx.missing_files)
    else:
        missing_rows.append("No missing files.")
    write_text(ctx.run_dir / "logs" / "missing_files.md", "\n".join(missing_rows), ctx)
    warning_rows = [
        {
            "generated_at": ctx.generated_at,
            "reviewer": warning.reviewer,
            "file": warning.file,
            "severity": warning.severity,
            "message": warning.message,
        }
        for warning in ctx.warnings
    ]
    write_jsonl(ctx.run_dir / "logs" / "parse_warnings.jsonl", warning_rows, ctx)
    log_data = {
        "script_version": SCRIPT_VERSION,
        "generated_at": ctx.generated_at,
        "run_dir": rel(ctx.run_dir),
        "files_read": ctx.files_read,
        "files_missing": ctx.missing_files,
        "parse_results": ctx.parse_results,
        "warnings": warning_rows,
        "files_generated": ctx.files_generated,
        "safety": {
            "neo4j_connection_attempted": False,
            "model_api_called": False,
            "full_eval_executed": False,
            "dry_run_executed": False,
        },
    }
    write_json(ctx.run_dir / "logs" / "merge_log.json", log_data, ctx)
    rows = [
        "# Merge Log",
        "",
        f"- script_version: `{SCRIPT_VERSION}`",
        f"- generated_at: `{ctx.generated_at}`",
        f"- run_dir: `{rel(ctx.run_dir)}`",
        f"- files read: {len(ctx.files_read)}",
        f"- files missing: {len(ctx.missing_files)}",
        f"- warnings: {len(ctx.warnings)}",
        "",
        "## Parse Results",
        "",
    ]
    rows.extend(f"- {reviewer}: {result}" for reviewer, result in sorted(ctx.parse_results.items()))
    if ctx.warnings:
        rows.extend(["", "## Warnings", ""])
        rows.extend(f"- [{w.severity}] {w.reviewer} `{w.file}`: {w.message}" for w in ctx.warnings)
    write_text(ctx.run_dir / "logs" / "merge_log.md", "\n".join(rows), ctx)


def merge(ctx: MergeContext, manifest: dict[str, Any]) -> int:
    reviewers: dict[str, dict[str, Any]] = {}
    for reviewer in REVIEWERS:
        reviewers[reviewer] = parse_review_file(reviewer, ctx.run_dir / "reviews" / f"{reviewer}_review.md", ctx)
    normalized = {
        "run_dir": rel(ctx.run_dir),
        "generated_at": ctx.generated_at,
        "reviewers": reviewers,
    }
    gate_status = apply_local_gate_overrides(ctx.run_dir, aggregate_gate_status(reviewers))
    conflicts = generate_conflicts(normalized, gate_status)
    actions = generate_actions(normalized, conflicts, gate_status, ctx.missing_files)
    decision = current_decision(conflicts, actions, gate_status)

    write_json(ctx.run_dir / "merged" / "normalized_reviews.json", normalized, ctx)
    write_json(ctx.run_dir / "merged" / "conflict_matrix.json", conflicts, ctx)
    write_text(ctx.run_dir / "merged" / "conflict_matrix.md", render_conflicts(conflicts), ctx)
    write_jsonl(ctx.run_dir / "merged" / "action_items.jsonl", actions, ctx)
    write_text(ctx.run_dir / "merged" / "action_items.md", render_actions(actions), ctx)
    write_json(
        ctx.run_dir / "merged" / "gate_status.json",
        {
            "run_dir": rel(ctx.run_dir),
            "generated_at": ctx.generated_at,
            "gate_status": gate_status,
            "full_eval_approved": False,
        },
        ctx,
    )
    write_text(ctx.run_dir / "merged" / "gate_status.md", render_gate_status(gate_status), ctx)
    write_text(
        ctx.run_dir / "merged" / "final_orchestration_report.md",
        render_final_report(normalized, conflicts, actions, gate_status, manifest, decision),
        ctx,
    )
    if ctx.strict:
        missing_or_bad = [
            reviewer
            for reviewer, review in reviewers.items()
            if reviewer in REQUIRED_REVIEWERS
            if not review.get("present") or not review.get("parsed")
        ]
        if missing_or_bad:
            ctx.warnings.append(
                WarningRecord(
                    "strict",
                    rel(ctx.run_dir),
                    f"strict mode failed; missing or unparsable reviews: {', '.join(missing_or_bad)}",
                    severity="error",
                )
            )
            return 2
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Merge file-based Round 3 multi-agent reviews.")
    parser.add_argument("--run-dir", help="Orchestration run directory to use.")
    parser.add_argument("--latest", action="store_true", help="Use most recently modified run directory.")
    parser.add_argument("--init-only", action="store_true", help="Create structure/templates/manifest only.")
    parser.add_argument("--strict", action="store_true", help="Fail when any required review is missing or unparsable.")
    args = parser.parse_args(argv)

    run_dir = resolve_run_dir(args)
    ctx = MergeContext(run_dir=run_dir, strict=bool(args.strict))
    ensure_structure(ctx)
    manifest = create_manifest(ctx)
    create_readme(ctx)
    rc = 0 if args.init_only else merge(ctx, manifest)
    write_logs(ctx)
    print(rel(run_dir))
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
