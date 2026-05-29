"""Create B2b manual adjudication packet from B2a read-only outputs.

This is a file-only reporting step. It does not connect to Neo4j, execute
Cypher, call models, run eval, or create approval files.
"""

from __future__ import annotations

import csv
import json
import re
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
BASE_DIR = ROOT / "outputs" / "round3_backlog_remediation_consolidated"
B2A_DIR = BASE_DIR / "b2a_additional_readonly_validation"
B2B_DIR = BASE_DIR / "b2b_manual_adjudication"

RISKY_GROUPS = ["pg_001_lin_ticker", "pg_002_mdlz_alias", "pg_004_bac_obs"]
AUDIT_ONLY_GROUPS = ["pg_003_apd_fiscal"]
FORBIDDEN_RE = re.compile(
    r"\b(CREATE|MERGE|SET|DELETE|REMOVE|DROP|LOAD\s+CSV)\b|\bCALL\s+dbms\b|\bCALL\s+apoc\.periodic\b",
    re.IGNORECASE,
)


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def rel(path: Path) -> str:
    return path.relative_to(ROOT).as_posix()


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    if not path.exists():
        return rows
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            rows.append(json.loads(line))
    return rows


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")


def write_csv(path: Path, rows: list[dict[str, Any]], fields: list[str]) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for row in rows:
            writer.writerow({field: row.get(field, "") for field in fields})


def load_inputs() -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    results = read_jsonl(B2A_DIR / "b2a_readonly_results.jsonl")
    binding = read_jsonl(B2A_DIR / "b2a_node_relationship_binding_report.jsonl")
    scope_path = B2A_DIR / "b2a_updated_patch_scope.json"
    scope = json.loads(scope_path.read_text(encoding="utf-8")) if scope_path.exists() else {}
    return results, binding, scope


def strip_comment(line: str) -> str:
    return line.split("//", 1)[0] if "//" in line else line


def scan_no_write() -> tuple[str, list[tuple[str, int, str]]]:
    hits: list[tuple[str, int, str]] = []
    candidates = [
        B2A_DIR / "b2a_query_log.cypher",
        BASE_DIR / "b2_patch_approval_request" / "b2_candidate_patch_preview.disabled.cypher",
        BASE_DIR / "b2_patch_approval_request" / "b2_rollback_plan.cypher",
    ]
    for path in candidates:
        if not path.exists():
            continue
        for line_no, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
            if FORBIDDEN_RE.search(strip_comment(line)):
                hits.append((rel(path), line_no, line))
    return ("PASS" if not hits else "NO_GO"), hits


def rows_by_group(rows: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[row["patch_group_id"]].append(row)
    return grouped


def compact_node(obs: dict[str, Any]) -> dict[str, Any]:
    return {
        "node_id": obs.get("node_id"),
        "labels": obs.get("labels"),
        "properties": obs.get("properties") or {},
        "relationship_id": obs.get("relationship_id"),
        "relationship_type": obs.get("relationship_type"),
        "neighbor_node_id": obs.get("neighbor_node_id"),
        "neighbor_labels": obs.get("neighbor_labels"),
        "neighbor_properties": obs.get("neighbor_properties") or {},
    }


def candidate_comparison(results_by_group: dict[str, list[dict[str, Any]]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for group_id in RISKY_GROUPS + AUDIT_ONLY_GROUPS:
        query_rows = results_by_group.get(group_id, [])
        nodes_by_query: dict[str, list[dict[str, Any]]] = {}
        relationships_by_query: dict[str, list[dict[str, Any]]] = {}
        for query_row in query_rows:
            nodes: dict[str, dict[str, Any]] = {}
            relationships: dict[str, dict[str, Any]] = {}
            for obs in query_row.get("observations_found", []):
                node_id = obs.get("node_id")
                if node_id:
                    nodes[node_id] = compact_node(obs)
                rel_id = obs.get("relationship_id")
                if rel_id:
                    relationships[rel_id] = {
                        "relationship_id": rel_id,
                        "relationship_type": obs.get("relationship_type"),
                        "node_id": obs.get("node_id"),
                        "neighbor_node_id": obs.get("neighbor_node_id"),
                    }
            nodes_by_query[query_row["query_name"]] = list(nodes.values())
            relationships_by_query[query_row["query_name"]] = list(relationships.values())
        rows.append(
            {
                "patch_group_id": group_id,
                "candidate_nodes_by_query": nodes_by_query,
                "candidate_relationships_by_query": relationships_by_query,
                "query_counts": {
                    row["query_name"]: {
                        "result_count": row.get("result_count", 0),
                        "validation_status": row.get("validation_status", ""),
                    }
                    for row in query_rows
                },
            }
        )
    return rows


def ambiguity_types(report: dict[str, Any]) -> list[str]:
    criteria = report.get("exact_match_criteria", {})
    types: list[str] = []
    if criteria.get("duplicate_or_conflicting_candidates"):
        types.extend(["duplicate_company_candidate", "duplicate_metric_candidate"])
    if not criteria.get("metric_year_value_confirmed"):
        types.append("incomplete_metric_year_value_binding")
    if not report.get("relationship_context_found"):
        types.append("missing_relationship_binding")
    elif not report.get("rollback_possible"):
        types.append("missing_relationship_binding")
    if not criteria.get("source_or_case_context_found"):
        types.append("unclear_case_source_mapping")
    if report.get("patch_group_id") == "pg_004_bac_obs":
        types.extend(["alias_conflict", "unsafe_identity_merge"])
    if report.get("patch_group_id") == "pg_001_lin_ticker":
        types.append("alias_conflict")
    return list(dict.fromkeys(types or ["other"]))


def adjudication_for(report: dict[str, Any]) -> dict[str, Any]:
    group_id = report["patch_group_id"]
    if group_id == "pg_003_apd_fiscal":
        return {
            "manual_decision": "defer_test_informed",
            "salvage_status": "should_be_abandoned",
            "selected_target_node_id": "",
            "selected_relationship_id_or_match_criteria": "",
            "evidence_basis": "round3_test audit-only target; not eligible for patch approval path",
            "rollback_possibility": False,
            "risk_level": "defer_test_informed",
            "patching_unsafe_reason": "APD is test-informed and cannot be used to tune or patch this route.",
        }
    if group_id in {"pg_001_lin_ticker", "pg_002_mdlz_alias"}:
        return {
            "manual_decision": "not_patch_ready",
            "salvage_status": "needs_more_readonly_query",
            "selected_target_node_id": "",
            "selected_relationship_id_or_match_criteria": "",
            "evidence_basis": "Exact ticker node exists, but metric/year/value and relationship binding remain ambiguous.",
            "rollback_possibility": False,
            "risk_level": "medium_high",
            "patching_unsafe_reason": "Patching now would require selecting among duplicate metric/relationship candidates without a unique observation target.",
        }
    if group_id == "pg_004_bac_obs":
        return {
            "manual_decision": "not_patch_ready",
            "salvage_status": "should_be_abandoned",
            "selected_target_node_id": "",
            "selected_relationship_id_or_match_criteria": "",
            "evidence_basis": "Exact BAC ticker identity was not found; company-name search returns conflicting Bank of America candidates.",
            "rollback_possibility": False,
            "risk_level": "high",
            "patching_unsafe_reason": "Patching risks merging ambiguous Bank of America references into a financial-company identity.",
        }
    return {
        "manual_decision": "not_patch_ready",
        "salvage_status": "needs_more_readonly_query",
        "selected_target_node_id": "",
        "selected_relationship_id_or_match_criteria": "",
        "evidence_basis": "No safe single target identified.",
        "rollback_possibility": False,
        "risk_level": "high",
        "patching_unsafe_reason": "Ambiguity remains unresolved.",
    }


def build_risk_rows(binding_reports: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    risk_rows: list[dict[str, Any]] = []
    ambiguity_rows: list[dict[str, Any]] = []
    for report in binding_reports:
        group_id = report["patch_group_id"]
        if group_id not in RISKY_GROUPS + AUDIT_ONLY_GROUPS:
            continue
        types = ambiguity_types(report)
        adjudication = adjudication_for(report)
        risk_rows.append(
            {
                "patch_group_id": group_id,
                "previous_classification": report.get("previous_classification", ""),
                "b2a_classification": report.get("updated_classification", ""),
                "manual_decision": adjudication["manual_decision"],
                "salvage_status": adjudication["salvage_status"],
                "ambiguity_types": ";".join(types),
                "target_node_count": len(report.get("target_node_ids", [])),
                "target_relationship_count": len(report.get("target_relationship_ids", [])),
                "relationship_context_found": report.get("relationship_context_found", False),
                "rollback_possible": adjudication["rollback_possibility"],
                "risk_level": adjudication["risk_level"],
                "include_in_future_approval_request": False,
            }
        )
        ambiguity_rows.append(
            {
                "patch_group_id": group_id,
                "ambiguity_types": types,
                "target_node_ids": report.get("target_node_ids", []),
                "target_relationship_ids": report.get("target_relationship_ids", []),
                "exact_match_criteria": report.get("exact_match_criteria", {}),
                "why_ambiguous": adjudication["patching_unsafe_reason"],
                "salvage_status": adjudication["salvage_status"],
                "selected_target_node_id": adjudication["selected_target_node_id"],
                "selected_relationship_id_or_match_criteria": adjudication["selected_relationship_id_or_match_criteria"],
                "evidence_basis": adjudication["evidence_basis"],
                "rollback_possible": adjudication["rollback_possibility"],
                "risk_level": adjudication["risk_level"],
            }
        )
    return risk_rows, ambiguity_rows


def write_summary(risk_rows: list[dict[str, Any]], final_decision: str) -> None:
    lines = [
        "# B2b Manual Adjudication Summary",
        "",
        f"- Created at: {now()}",
        "- Source: B2a read-only outputs only.",
        "- Neo4j write performed: false",
        "- KG patch applied: false",
        "- Full eval executed: false",
        "- Model/API called: false",
        "- USER_APPROVED_B3_PATCH_SCOPE.json created: false",
        f"- Final output: `{final_decision}`",
        "",
        "## Risky Non-Test Groups",
        "",
        "| Patch Group | B2a Classification | Manual Status | Ambiguity | Patch Safety |",
        "| --- | --- | --- | --- | --- |",
    ]
    for row in risk_rows:
        if row["patch_group_id"] in AUDIT_ONLY_GROUPS:
            continue
        lines.append(
            f"| `{row['patch_group_id']}` | `{row['b2a_classification']}` | `{row['salvage_status']}` | "
            f"{row['ambiguity_types']} | not safe for B3 |"
        )
    lines.extend(
        [
            "",
            "## Audit-Only",
            "",
            "- `pg_003_apd_fiscal` remains `defer_test_informed` and is not used for prompt, ontology, patch logic, or KG patch.",
            "",
            "No non-test patch group is safe enough to include in a B3 approval scope.",
        ]
    )
    (B2B_DIR / "b2b_manual_adjudication_summary.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def write_salvage(risk_rows: list[dict[str, Any]], ambiguity_rows: list[dict[str, Any]], final_decision: str) -> None:
    by_group = {row["patch_group_id"]: row for row in ambiguity_rows}
    lines = [
        "# B2b Salvage Recommendations",
        "",
        f"Final output: `{final_decision}`",
        "",
        "## Recommendations",
        "",
    ]
    for group_id in RISKY_GROUPS:
        row = by_group[group_id]
        lines.extend(
            [
                f"### {group_id}",
                "",
                f"- Status: `{row['salvage_status']}`",
                f"- Risk level: `{row['risk_level']}`",
                f"- Evidence basis: {row['evidence_basis']}",
                f"- Why unsafe now: {row['why_ambiguous']}",
                f"- Selected target node id: `{row['selected_target_node_id']}`",
                f"- Selected relationship/match criteria: `{row['selected_relationship_id_or_match_criteria']}`",
                f"- Rollback possible: {str(row['rollback_possible']).lower()}",
                "",
            ]
        )
    lines.extend(
        [
            "## Next Read-Only Direction",
            "",
            "- For LIN/MDLZ, use stricter source-evidence containment plus exact metric/value/year matching before any approval request.",
            "- For BAC, abandon the current quick-win patch unless an authoritative BAC ticker KGEntity is found by read-only query.",
        ]
    )
    (B2B_DIR / "b2b_salvage_recommendations.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def write_decision(final_decision: str) -> None:
    lines = [
        "# B2b Patch Or Abandon Decision",
        "",
        f"Decision: `{final_decision}`",
        "",
        "- No group is patch-safe after B2a.",
        "- Do not proceed to B3.",
        "- Do not create `USER_APPROVED_B3_PATCH_SCOPE.json`.",
        "- Do not execute write Cypher.",
        "- Do not apply KG patch.",
        "- Do not run eval.",
        "- Do not call model/API.",
    ]
    (B2B_DIR / "b2b_patch_or_abandon_decision.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def write_safety_scan(status: str, hits: list[tuple[str, int, str]]) -> None:
    lines = [
        "# B2b No-Write Safety Scan",
        "",
        f"- Decision: `{status}`",
        "- B2b executes no Cypher and performs file-only adjudication.",
        "- Checked prior B2a/B2 disabled query logs for uncommented forbidden write tokens.",
        f"- Forbidden hits: {len(hits)}",
        "- Neo4j write performed: false",
        "- KG patch applied: false",
        "- Full eval executed: false",
        "- Model/API called: false",
    ]
    for path, line_no, line in hits:
        lines.append(f"- {path}:{line_no}: `{line}`")
    (B2B_DIR / "b2b_no_write_safety_scan.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    B2B_DIR.mkdir(parents=True, exist_ok=True)
    results, binding_reports, _scope = load_inputs()
    comparison_rows = candidate_comparison(rows_by_group(results))
    risk_rows, ambiguity_rows = build_risk_rows(binding_reports)
    safety_status, safety_hits = scan_no_write()

    final_decision = "needs_additional_readonly_queries"
    write_jsonl(B2B_DIR / "b2b_candidate_node_comparison.jsonl", comparison_rows)
    write_jsonl(B2B_DIR / "b2b_relationship_binding_ambiguities.jsonl", ambiguity_rows)
    write_csv(
        B2B_DIR / "b2b_patch_group_risk_table.csv",
        risk_rows,
        [
            "patch_group_id",
            "previous_classification",
            "b2a_classification",
            "manual_decision",
            "salvage_status",
            "ambiguity_types",
            "target_node_count",
            "target_relationship_count",
            "relationship_context_found",
            "rollback_possible",
            "risk_level",
            "include_in_future_approval_request",
        ],
    )
    write_summary(risk_rows, final_decision)
    write_salvage(risk_rows, ambiguity_rows, final_decision)
    write_decision(final_decision)
    write_safety_scan(safety_status, safety_hits)

    print(
        json.dumps(
            {
                "B2b completed": True,
                "final_output": final_decision,
                "patch_safe_groups": [],
                "needs_more_readonly_query": ["pg_001_lin_ticker", "pg_002_mdlz_alias"],
                "should_be_abandoned": ["pg_004_bac_obs"],
                "defer_test_informed": ["pg_003_apd_fiscal"],
                "Neo4j write performed": False,
                "KG patch applied": False,
                "Full eval executed": False,
                "Model/API called": False,
                "safety_scan": safety_status,
            },
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
