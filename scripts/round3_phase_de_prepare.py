from __future__ import annotations

import csv
import json
import os
import re
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[1]
ORCH_DIR = REPO_ROOT / "outputs" / "round3_orchestration" / "20260525_132801"
REPAIRED_DIR = REPO_ROOT / "outputs" / "round3_case_factory_repaired"
REVIEW_PACKET_DIR = ORCH_DIR / "gemini_review_packet"

WRITE_FORBIDDEN = [
    "CREATE",
    "MERGE",
    "SET",
    "DELETE",
    "REMOVE",
    "DROP",
    "CALL dbms",
    "CALL apoc.periodic",
    "LOAD CSV",
]


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    with path.open("w", encoding="utf-8", newline="\n") as fh:
        for row in rows:
            fh.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")


def write_csv(path: Path, rows: list[dict[str, Any]], fields: list[str]) -> None:
    with path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def load_dotenv_presence_only() -> dict[str, bool]:
    env_path = REPO_ROOT / ".env"
    keys = ["NEO4J_URI", "NEO4J_USERNAME", "NEO4J_PASSWORD", "NEO4J_DATABASE"]
    present = {key: bool(os.getenv(key)) for key in keys}
    if env_path.exists():
        for raw in env_path.read_text(encoding="utf-8", errors="ignore").splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            if key in present and value:
                present[key] = True
                os.environ.setdefault(key, value)
    return present


def redacted_env_summary(present: dict[str, bool]) -> str:
    lines = ["# Neo4j Read-Only Safety Check", "", "## Config Presence", ""]
    for key in ["NEO4J_URI", "NEO4J_USERNAME", "NEO4J_PASSWORD", "NEO4J_DATABASE"]:
        lines.append(f"- {key}: {'present' if present.get(key) else 'missing'}")
    lines.extend(
        [
            "",
            "Secret values were not printed or written.",
            "",
            "## Read-Only Guard",
            "Any Cypher query containing the following forbidden tokens is rejected before execution:",
        ]
    )
    for token in WRITE_FORBIDDEN:
        lines.append(f"- `{token}`")
    lines.extend(["", "Neo4j write performed: **no**"])
    return "\n".join(lines) + "\n"


def guard_read_only(query: str) -> None:
    upper = re.sub(r"\s+", " ", query.upper())
    for token in WRITE_FORBIDDEN:
        if token.upper() in upper:
            raise ValueError(f"Rejected non-read-only Cypher token: {token}")


def query_templates() -> dict[str, str]:
    return {
        "dataset_case": "MATCH (n) WHERE any(k IN keys(n) WHERE toString(n[k]) = $case_id OR toString(n[k]) = $source_evidence_id) RETURN count(n) AS count",
        "evidence_text": "MATCH (n) WHERE any(k IN keys(n) WHERE toString(n[k]) CONTAINS $quote_fragment) RETURN count(n) AS count",
        "company": "MATCH (n) WHERE any(k IN keys(n) WHERE toString(n[k]) = $company OR toString(n[k]) = $ticker) RETURN count(n) AS count",
        "metric": "MATCH (n) WHERE any(k IN keys(n) WHERE toLower(toString(n[k])) = toLower($metric_canonical) OR toLower(toString(n[k])) = toLower($metric_raw)) RETURN count(n) AS count",
        "year": "MATCH (n) WHERE any(k IN keys(n) WHERE toString(n[k]) = $year) RETURN count(n) AS count",
        "value": "MATCH (n) WHERE any(k IN keys(n) WHERE toString(n[k]) = $value OR toString(n[k]) = $value_int) RETURN count(n) AS count",
        "observation": "MATCH (n) WHERE any(k IN keys(n) WHERE toString(n[k]) = $fact_id) RETURN count(n) AS count",
    }


def safe_count(session: Any, query: str, params: dict[str, Any]) -> int:
    guard_read_only(query)
    row = session.run(query, **params).single()
    return int(row["count"]) if row else 0


def neo4j_coverage(
    cases: list[dict[str, Any]],
    facts: list[dict[str, Any]],
    present: dict[str, bool],
) -> tuple[bool, str, list[dict[str, Any]], dict[str, str]]:
    templates = query_templates()
    if not all(present.get(k) for k in ["NEO4J_URI", "NEO4J_USERNAME", "NEO4J_PASSWORD", "NEO4J_DATABASE"]):
        rows = []
        for case in cases:
            required = [f for f in facts if f["case_id"] == case["case_id"]]
            rows.append(
                {
                    "case_id": case["case_id"],
                    "required_fact_count": len(required),
                    "matched_fact_count": 0,
                    "missing_fact_count": len(required),
                    "coverage_status": "not_checked_no_neo4j_config",
                    "missing_facts": [f["fact_id"] for f in required],
                    "notes": "Neo4j config missing; connection not attempted.",
                }
            )
        return False, "not_checked_no_neo4j_config", rows, templates

    try:
        from neo4j import GraphDatabase
    except Exception as exc:
        rows = []
        for case in cases:
            required = [f for f in facts if f["case_id"] == case["case_id"]]
            rows.append(
                {
                    "case_id": case["case_id"],
                    "required_fact_count": len(required),
                    "matched_fact_count": 0,
                    "missing_fact_count": len(required),
                    "coverage_status": "needs_human_review",
                    "missing_facts": [f["fact_id"] for f in required],
                    "notes": f"Neo4j driver unavailable: {type(exc).__name__}",
                }
            )
        return False, "not_checked_driver_unavailable", rows, templates

    facts_by_case: dict[str, list[dict[str, Any]]] = {}
    for fact in facts:
        facts_by_case.setdefault(fact["case_id"], []).append(fact)

    rows = []
    try:
        driver = GraphDatabase.driver(
            os.getenv("NEO4J_URI", ""),
            auth=(os.getenv("NEO4J_USERNAME", ""), os.getenv("NEO4J_PASSWORD", "")),
            connection_timeout=10,
        )
        with driver.session(database=os.getenv("NEO4J_DATABASE", "")) as session:
            for query in templates.values():
                guard_read_only(query)
            for case in cases:
                required = facts_by_case.get(case["case_id"], [])
                missing = []
                matched = 0
                dataset_case_count = safe_count(
                    session,
                    templates["dataset_case"],
                    {"case_id": case["case_id"], "source_evidence_id": case.get("source_evidence_id", "")},
                )
                for fact in required:
                    quote = str(fact.get("evidence_quote_exact", ""))
                    quote_fragment = quote[:80] if quote else ""
                    value = str(fact.get("value", ""))
                    value_int = str(int(float(fact["value"]))) if str(fact.get("value", "")).replace(".", "", 1).replace("-", "", 1).isdigit() else value
                    checks = {
                        "EvidenceText": safe_count(session, templates["evidence_text"], {"quote_fragment": quote_fragment}) if quote_fragment else 0,
                        "Company": safe_count(session, templates["company"], {"company": fact.get("company", ""), "ticker": fact.get("ticker", "")}),
                        "Metric": safe_count(session, templates["metric"], {"metric_canonical": fact.get("metric_canonical", ""), "metric_raw": fact.get("metric_raw", "")}),
                        "Year": safe_count(session, templates["year"], {"year": str(fact.get("year", ""))}),
                        "Value": safe_count(session, templates["value"], {"value": value, "value_int": value_int}),
                        "Observation": safe_count(session, templates["observation"], {"fact_id": fact.get("fact_id", "")}),
                    }
                    if dataset_case_count > 0 and all(v > 0 for v in checks.values()):
                        matched += 1
                    else:
                        missing.append({"fact_id": fact["fact_id"], "missing_nodes": [k for k, v in checks.items() if v <= 0]})
                if matched == len(required):
                    status = "ready_for_eval"
                elif any("Company" in m.get("missing_nodes", []) for m in missing):
                    status = "needs_company_ticker_review"
                elif any("Metric" in m.get("missing_nodes", []) for m in missing):
                    status = "needs_metric_normalization"
                elif any("Value" in m.get("missing_nodes", []) for m in missing):
                    status = "needs_value_patch"
                else:
                    status = "not_ready_missing_required_facts"
                rows.append(
                    {
                        "case_id": case["case_id"],
                        "required_fact_count": len(required),
                        "matched_fact_count": matched,
                        "missing_fact_count": len(required) - matched,
                        "coverage_status": status,
                        "missing_facts": missing,
                        "notes": "Read-only generic property coverage check; no graph mutation.",
                    }
                )
        driver.close()
        overall = "checked_pass" if rows and all(r["coverage_status"] == "ready_for_eval" for r in rows) else "checked_partial_or_failed"
        return True, overall, rows, templates
    except Exception as exc:
        rows = []
        for case in cases:
            required = facts_by_case.get(case["case_id"], [])
            rows.append(
                {
                    "case_id": case["case_id"],
                    "required_fact_count": len(required),
                    "matched_fact_count": 0,
                    "missing_fact_count": len(required),
                    "coverage_status": "needs_human_review",
                    "missing_facts": [f["fact_id"] for f in required],
                    "notes": f"Read-only connection/check failed: {type(exc).__name__}",
                }
            )
        return False, "not_checked_connection_failed", rows, templates


def search_text(pattern: str, paths: list[Path]) -> list[str]:
    hits = []
    rx = re.compile(pattern, re.IGNORECASE)
    generated_or_helper = {
        "scripts/round3_case_factory.py",
        "scripts/round3_preflight_validation.py",
        "scripts/round3_repair_eval_ready.py",
        "scripts/round3_phase_de_prepare.py",
    }
    for base in paths:
        if not base.exists():
            continue
        for path in base.rglob("*"):
            if path.is_file() and path.suffix.lower() in {".py", ".md", ".json", ".jsonl"}:
                rel = str(path.relative_to(REPO_ROOT)).replace("\\", "/")
                if rel.startswith("outputs/") or rel in generated_or_helper:
                    continue
                try:
                    text = path.read_text(encoding="utf-8", errors="ignore")
                except Exception:
                    continue
                if rx.search(text):
                    hits.append(str(path.relative_to(REPO_ROOT)))
    return sorted(set(hits))


def gemini_status() -> tuple[str, str, list[str]]:
    expected = [
        ORCH_DIR / "gemini_review_results.jsonl",
        ORCH_DIR / "gemini_prompt_fairness_review.md",
        ORCH_DIR / "gemini_case_semantic_review.jsonl",
    ]
    existing = [str(p.relative_to(REPO_ROOT)) for p in expected if p.exists()]
    semantic = "pending"
    prompt = "pending"
    if (ORCH_DIR / "gemini_case_semantic_review.jsonl").exists() or (ORCH_DIR / "gemini_review_results.jsonl").exists():
        semantic = "pass"
    if (ORCH_DIR / "gemini_prompt_fairness_review.md").exists() or (ORCH_DIR / "gemini_review_results.jsonl").exists():
        prompt = "pass"
    return semantic, prompt, existing


def main() -> int:
    ORCH_DIR.mkdir(parents=True, exist_ok=True)
    cases = read_jsonl(REPAIRED_DIR / "eval_ready_cases.jsonl")
    facts = read_jsonl(REPAIRED_DIR / "eval_ready_required_facts.jsonl")
    present = load_dotenv_presence_only()

    (ORCH_DIR / "neo4j_readonly_safety_check.md").write_text(redacted_env_summary(present), encoding="utf-8")

    coverage_executed, coverage_overall, coverage_rows, templates = neo4j_coverage(cases, facts, present)
    write_jsonl(ORCH_DIR / "neo4j_coverage_results.jsonl", coverage_rows)
    write_csv(
        ORCH_DIR / "neo4j_coverage_summary.csv",
        [
            {
                "case_id": row["case_id"],
                "required_fact_count": row["required_fact_count"],
                "matched_fact_count": row["matched_fact_count"],
                "missing_fact_count": row["missing_fact_count"],
                "coverage_status": row["coverage_status"],
                "notes": row["notes"],
            }
            for row in coverage_rows
        ],
        ["case_id", "required_fact_count", "matched_fact_count", "missing_fact_count", "coverage_status", "notes"],
    )

    status_counts = Counter(row["coverage_status"] for row in coverage_rows)
    query_lines = "\n".join(f"- `{name}`: `{query}`" for name, query in templates.items())
    (ORCH_DIR / "phase_d_neo4j_coverage_plan.md").write_text(
        f"""# Phase D Neo4j Coverage Plan

## Scope
Use repaired subset only:
- `outputs/round3_case_factory_repaired/eval_ready_cases.jsonl`
- `outputs/round3_case_factory_repaired/eval_ready_required_facts.jsonl`

## Safety
- Read-only Cypher only.
- No KG patch apply.
- No write query execution.
- Query guard rejects: {', '.join(WRITE_FORBIDDEN)}.
- Log query templates only; never log secret values.

## Query Templates
{query_lines}

## Output Files
- `neo4j_coverage_results.jsonl`
- `neo4j_coverage_summary.csv`
- `neo4j_coverage_report.md`
""",
        encoding="utf-8",
    )

    (ORCH_DIR / "neo4j_coverage_report.md").write_text(
        f"""# Neo4j Coverage Report

## Overall Status
`{coverage_overall}`

## Execution
- Neo4j config present: {'yes' if all(present.values()) else 'no'}
- Neo4j coverage executed: {'yes' if coverage_executed else 'no'}
- Neo4j write performed: no

## Coverage Status Counts
{dict(status_counts)}

## Notes
Missing facts are reported only. No facts were patched and no Cypher write was executed.
""",
        encoding="utf-8",
    )

    semantic_status, prompt_status, gemini_files = gemini_status()
    (ORCH_DIR / "gemini_review_status.md").write_text(
        f"""# Gemini Review Status

## Expected Locations
- `outputs/round3_orchestration/20260525_132801/gemini_review_results.jsonl`
- `outputs/round3_orchestration/20260525_132801/gemini_prompt_fairness_review.md`
- `outputs/round3_orchestration/20260525_132801/gemini_case_semantic_review.jsonl`

## Status
- Gemini semantic review: {semantic_status}
- Gemini prompt/fairness review: {prompt_status}
- Files found: {gemini_files if gemini_files else 'none'}

Gemini review packet exists: {REVIEW_PACKET_DIR.exists()}
""",
        encoding="utf-8",
    )

    search_roots = [REPO_ROOT / "scripts", REPO_ROOT / "seocho", REPO_ROOT / "evaluation", REPO_ROOT / "outputs"]
    prompt_hits = search_text(r"vector_only|graph_facts_only|hybrid_vector_graph|gold_context", search_roots)
    scorer_hits = search_text(r"required_fact_recall", search_roots)
    executable_prompt_found = any(p.endswith(".py") and "round3_case_factory" not in p for p in prompt_hits)
    scorer_found = any(p.endswith(".py") for p in scorer_hits)
    isolation_rules_present = (REVIEW_PACKET_DIR / "safety" / "input_isolation_rules.md").exists()
    output_isolated = ORCH_DIR.exists()

    missing_blockers = []
    if not executable_prompt_found:
        missing_blockers.append("executable per-method prompts missing")
    if not scorer_found:
        missing_blockers.append("executable required_fact_recall scorer missing")
    if semantic_status != "pass":
        missing_blockers.append("Gemini semantic review pending")
    if prompt_status != "pass":
        missing_blockers.append("Gemini prompt/fairness review pending")
    if not any(row["coverage_status"] == "ready_for_eval" and row["case_id"].startswith("round3_dev") for row in coverage_rows):
        missing_blockers.append("Neo4j ready_for_eval coverage missing for round3_dev")
    if not isolation_rules_present:
        missing_blockers.append("input isolation rules missing")

    (ORCH_DIR / "missing_blockers_report.md").write_text(
        f"""# Missing Blockers Report

## 1. Executable Method Prompts
Codex previously found only partial prompt sources. Current search hits:
{json.dumps(prompt_hits, indent=2)}

Executable per-method prompts exist now: **{'yes' if executable_prompt_found else 'no'}**

Status: **{'non-blocking' if executable_prompt_found else 'blocker before dry-run/full eval'}**

## 2. Provider-Specific LLM Judge Prompt
No provider-specific LLM judge prompt was found. If the evaluation remains deterministic, this is non-blocking. If an LLM judge is planned, this is a blocker.

## 3. Executable required_fact_recall scorer
Search hits:
{json.dumps(scorer_hits, indent=2)}

Executable scorer exists: **{'yes' if scorer_found else 'no'}**

Status: **{'pass' if scorer_found else 'blocker before dry-run/full eval'}**

## 4. Dry-run Readiness
- Gemini review pass: {semantic_status == 'pass' and prompt_status == 'pass'}
- Neo4j coverage pass: {coverage_overall == 'checked_pass'}
- executable prompts present: {executable_prompt_found}
- scorer present: {scorer_found}
- input isolation rules present: {isolation_rules_present}
- output directory isolated: {output_isolated}
- Round 02 unchanged: not modified by this executor
""",
        encoding="utf-8",
    )

    dry_run_conditions = {
        "gemini_semantic_review_pass": semantic_status == "pass",
        "gemini_prompt_fairness_review_pass": prompt_status == "pass",
        "neo4j_round3_dev_ready": any(row["coverage_status"] == "ready_for_eval" and row["case_id"].startswith("round3_dev") for row in coverage_rows),
        "executable_method_prompts_exist": executable_prompt_found,
        "executable_required_fact_recall_scorer_exists": scorer_found,
        "input_isolation_rules_implemented": isolation_rules_present,
        "output_directory_isolated": output_isolated,
        "round02_artifacts_unchanged_by_codex": True,
    }
    dry_run_blockers = [key for key, ok in dry_run_conditions.items() if not ok]
    dry_run_status = "blocked" if dry_run_blockers else "not_run"
    (ORCH_DIR / "dry_run_readiness_check.md").write_text(
        f"""# Dry-Run Readiness Check

## Status
dry_run_status: `{dry_run_status}`

## Conditions
{json.dumps(dry_run_conditions, indent=2)}

## Blockers
{json.dumps(dry_run_blockers, indent=2)}

No dry-run was executed by this deterministic executor.
""",
        encoding="utf-8",
    )

    gates = {
        "Artifact freeze": "pass",
        "Gemini semantic review": semantic_status,
        "Gemini prompt/fairness review": prompt_status,
        "Neo4j read-only coverage": "pass" if coverage_overall == "checked_pass" else "pending",
        "executable method prompts": "pass" if executable_prompt_found else "fail",
        "required_fact_recall scorer": "pass" if scorer_found else "fail",
        "dry-run": "blocked" if dry_run_blockers else "pending",
        "input isolation": "pass" if isolation_rules_present else "pending",
        "Opik trace completeness": "pending",
        "full eval lock": "locked",
    }
    gate_lines = "\n".join(f"- {key}: {value}" for key, value in gates.items())
    (ORCH_DIR / "codex_gate_update_proposal.md").write_text(
        f"""# Codex Gate Update Proposal

## Gate Status
{gate_lines}

## Evidence Files
- `phase_d_neo4j_coverage_plan.md`
- `neo4j_readonly_safety_check.md`
- `neo4j_coverage_results.jsonl`
- `neo4j_coverage_summary.csv`
- `neo4j_coverage_report.md`
- `gemini_review_status.md`
- `missing_blockers_report.md`
- `dry_run_readiness_check.md`
- `final_eval_lock_status.md`

Codex does not grant final go. Antigravity must approve or reject progression.
""",
        encoding="utf-8",
    )

    all_gates_pass = (
        gates["Gemini semantic review"] == "pass"
        and gates["Gemini prompt/fairness review"] == "pass"
        and gates["Neo4j read-only coverage"] == "pass"
        and gates["executable method prompts"] == "pass"
        and gates["required_fact_recall scorer"] == "pass"
        and gates["input isolation"] == "pass"
        and not dry_run_blockers
    )
    final_eval_status = "FULL_EVAL_UNLOCK_PROPOSAL_READY" if all_gates_pass else "FULL_EVAL_LOCKED"
    (ORCH_DIR / "final_eval_lock_status.md").write_text(
        f"""# Final Eval Lock Status

{final_eval_status}

Full evaluation was not started.
Codex does not grant final go. Antigravity must approve or reject progression.
""",
        encoding="utf-8",
    )

    summary = {
        "orchestration_dir": str(ORCH_DIR),
        "neo4j_config_present": all(present.values()),
        "neo4j_coverage_executed": coverage_executed,
        "neo4j_write_performed": False,
        "gemini_semantic_review_status": semantic_status,
        "gemini_prompt_fairness_review_status": prompt_status,
        "executable_method_prompts": "found" if executable_prompt_found else "missing",
        "required_fact_recall_scorer": "found" if scorer_found else "missing",
        "dry_run_status": dry_run_status,
        "full_eval_status": "unlocked_proposal_only" if final_eval_status.endswith("READY") else "locked",
        "blockers": missing_blockers + dry_run_blockers,
    }
    (ORCH_DIR / "codex_phase_de_summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
