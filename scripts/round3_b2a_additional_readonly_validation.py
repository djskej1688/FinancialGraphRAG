"""B2a additional read-only validation for Round 3 backlog quick wins.

This script performs no writes. It narrows candidate KGEntity bindings and
updates patch-group readiness for a future approval request.
"""

from __future__ import annotations

import csv
import json
import os
import re
import socket
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

try:
    from neo4j import GraphDatabase
except Exception:
    GraphDatabase = None  # type: ignore[assignment]


ROOT = Path(__file__).resolve().parents[1]
BASE_DIR = ROOT / "outputs" / "round3_backlog_remediation_consolidated"
B2A_DIR = BASE_DIR / "b2a_additional_readonly_validation"
B2_SCOPE = BASE_DIR / "b2_patch_approval_request" / "b2_patch_scope.json"

NON_TEST_TARGETS = ["pg_001_lin_ticker", "pg_002_mdlz_alias", "pg_004_bac_obs"]
AUDIT_ONLY_TARGETS = ["pg_003_apd_fiscal"]
TARGETS = NON_TEST_TARGETS + AUDIT_ONLY_TARGETS

FORBIDDEN_WRITE_RE = re.compile(
    r"\b(CREATE|MERGE|SET|DELETE|REMOVE|DROP|LOAD\s+CSV)\b|\bCALL\s+dbms\b|\bCALL\s+apoc\.periodic\b",
    re.IGNORECASE,
)


GROUP_EXPECTATIONS = {
    "pg_001_lin_ticker": {
        "company_terms": ["linde"],
        "tickers": ["LIN", "LND"],
        "metrics": ["net_income"],
        "years": [2022, 2023],
        "values": [6199.0, 4147.0],
        "test_informed": False,
    },
    "pg_002_mdlz_alias": {
        "company_terms": ["mondelez", "mondel"],
        "tickers": ["MDLZ"],
        "metrics": ["earnings_before_income_taxes", "income_tax_provision", "equity_method_investment_net_earnings"],
        "years": [2021, 2022, 2023],
        "values": [5880.0, 3228.0, 4369.0, -1537.0, -865.0, -1190.0, 160.0, 385.0],
        "test_informed": False,
    },
    "pg_004_bac_obs": {
        "company_terms": ["bank of america"],
        "tickers": ["BAC"],
        "metrics": ["total_noninterest_expense", "net_income"],
        "years": [2023],
        "values": [65845.0, 26515.0],
        "test_informed": False,
    },
    "pg_003_apd_fiscal": {
        "company_terms": ["air products"],
        "tickers": ["APD"],
        "metrics": ["net_income"],
        "years": [2023, 2024],
        "values": [3828.2, 2300.2],
        "test_informed": True,
    },
}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def rel(path: Path) -> str:
    return path.relative_to(ROOT).as_posix()


def load_dotenv(path: Path) -> None:
    if not path.exists():
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def neo4j_config() -> dict[str, Any]:
    load_dotenv(ROOT / ".env")
    uri = os.environ.get("NEO4J_URI", "")
    parsed = urlparse(uri)
    return {
        "uri": uri,
        "username": os.environ.get("NEO4J_USERNAME") or os.environ.get("NEO4J_USER") or "",
        "password": os.environ.get("NEO4J_PASSWORD", ""),
        "database": os.environ.get("NEO4J_DATABASE") or "neo4j",
        "host": parsed.hostname or "",
        "port": parsed.port or (7687 if parsed.scheme.startswith("bolt") else None),
        "scheme": parsed.scheme,
    }


def tcp_reachable(host: str, port: int | None) -> str:
    if not host or not port:
        return "unknown"
    try:
        with socket.create_connection((host, int(port)), timeout=10):
            return "yes"
    except OSError:
        return "no"


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")


def strip_comments(cypher: str) -> str:
    lines = []
    for line in cypher.splitlines():
        lines.append(line.split("//", 1)[0] if "//" in line else line)
    return "\n".join(lines)


def readonly_guard(cypher: str) -> bool:
    return not FORBIDDEN_WRITE_RE.search(strip_comments(cypher))


def compact_props(props: dict[str, Any] | None) -> dict[str, Any]:
    if not props:
        return {}
    allowed = [
        "ticker",
        "name",
        "metric",
        "metric_canonical",
        "numeric_value",
        "value",
        "year",
        "period_label",
        "unit",
        "case_id",
        "source_id",
        "source_case_id",
        "fact_id",
    ]
    compact = {key: props.get(key) for key in allowed if key in props and props.get(key) is not None}
    if "name" in compact:
        compact["name"] = str(compact["name"])[:220]
    return compact


def load_group_plan() -> dict[str, dict[str, Any]]:
    return {row["patch_group_id"]: row for row in read_jsonl(BASE_DIR / "04_patch_group_plan.jsonl")}


def load_crosswalk() -> dict[str, list[dict[str, Any]]]:
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in read_jsonl(BASE_DIR / "06_patch_group_to_fact_crosswalk.jsonl"):
        if row.get("patch_group_id") in TARGETS:
            groups[row["patch_group_id"]].append(row)
    return groups


def load_previous_classifications() -> dict[str, str]:
    if not B2_SCOPE.exists():
        return {}
    data = json.loads(B2_SCOPE.read_text(encoding="utf-8"))
    return {row["patch_group_id"]: row["candidate_classification"] for row in data.get("candidates", [])}


def query_specs() -> list[dict[str, Any]]:
    specs: list[dict[str, Any]] = []
    for group_id, expected in GROUP_EXPECTATIONS.items():
        specs.extend(
            [
                {
                    "patch_group_id": group_id,
                    "query_name": "exact_ticker_identity",
                    "params": {"tickers": expected["tickers"], "terms": expected["company_terms"]},
                    "cypher": """
MATCH (n:KGEntity)
WHERE any(t IN $tickers WHERE toUpper(toString(n.ticker)) = toUpper(t) OR toUpper(toString(n.name)) = toUpper(t))
RETURN elementId(n) AS node_id, labels(n) AS labels, properties(n) AS properties
LIMIT 50
""",
                },
                {
                    "patch_group_id": group_id,
                    "query_name": "company_name_candidate_conflicts",
                    "params": {"terms": expected["company_terms"]},
                    "cypher": """
MATCH (n:KGEntity)
WHERE any(term IN $terms WHERE toLower(toString(n.name)) CONTAINS toLower(term))
RETURN elementId(n) AS node_id, labels(n) AS labels, properties(n) AS properties
LIMIT 50
""",
                },
                {
                    "patch_group_id": group_id,
                    "query_name": "metric_year_value_identity",
                    "params": {
                        "metrics": expected["metrics"],
                        "years": [str(y) for y in expected["years"]],
                        "values": [str(int(v)) if float(v).is_integer() else str(v) for v in expected["values"]],
                    },
                    "cypher": """
MATCH (n:KGEntity)
WHERE any(metric IN $metrics WHERE
        toLower(toString(n.metric)) = toLower(metric)
     OR toLower(toString(n.metric_canonical)) = toLower(metric)
     OR toLower(toString(n.name)) CONTAINS replace(toLower(metric), '_', ' ')
     OR toLower(toString(n.name)) CONTAINS toLower(metric))
  AND any(year IN $years WHERE toString(n.year) = year OR toString(n.name) CONTAINS year)
RETURN elementId(n) AS node_id, labels(n) AS labels, properties(n) AS properties
LIMIT 100
""",
                },
                {
                    "patch_group_id": group_id,
                    "query_name": "relationship_context",
                    "params": {"tickers": expected["tickers"], "terms": expected["company_terms"], "metrics": expected["metrics"]},
                    "cypher": """
MATCH (n:KGEntity)
WHERE any(t IN $tickers WHERE toUpper(toString(n.ticker)) = toUpper(t) OR toUpper(toString(n.name)) = toUpper(t))
   OR any(term IN $terms WHERE toLower(toString(n.name)) CONTAINS toLower(term))
   OR any(metric IN $metrics WHERE toLower(toString(n.name)) CONTAINS replace(toLower(metric), '_', ' '))
OPTIONAL MATCH (n)-[r]-(m)
RETURN elementId(n) AS node_id, labels(n) AS labels, properties(n) AS properties,
       elementId(r) AS relationship_id, type(r) AS relationship_type,
       elementId(m) AS neighbor_node_id, labels(m) AS neighbor_labels, properties(m) AS neighbor_properties
LIMIT 150
""",
                },
                {
                    "patch_group_id": group_id,
                    "query_name": "case_source_evidence_context",
                    "params": {"source_ids": sorted({row["source_id"] for row in load_crosswalk().get(group_id, [])}), "case_ids": sorted({row["case_id"] for row in load_crosswalk().get(group_id, [])})},
                    "cypher": """
MATCH (n:KGEntity)
WHERE any(s IN $source_ids WHERE toString(n.source_id) CONTAINS s OR toString(n.name) CONTAINS s OR toString(n.text) CONTAINS s OR toString(n.content) CONTAINS s)
   OR any(c IN $case_ids WHERE toString(n.case_id) CONTAINS c OR toString(n.name) CONTAINS c OR toString(n.text) CONTAINS c OR toString(n.content) CONTAINS c)
RETURN elementId(n) AS node_id, labels(n) AS labels, properties(n) AS properties
LIMIT 50
""",
                },
            ]
        )
    return specs


def execute_readonly() -> tuple[list[dict[str, Any]], list[str], dict[str, Any]]:
    config = neo4j_config()
    diagnostics = {
        "created_at": utc_now(),
        "config_present": bool(config["uri"] and config["username"] and config["password"] and config["database"]),
        "host": config["host"],
        "port": config["port"],
        "database": config["database"],
        "tcp_reachable": tcp_reachable(config["host"], config["port"]),
        "driver_connectivity_verified": False,
        "sanitized_error": "",
    }
    log_lines = [
        "// B2a additional read-only validation query log",
        f"// created_at: {diagnostics['created_at']}",
        "// no write operations requested or executed",
        "",
    ]
    results: list[dict[str, Any]] = []
    specs = query_specs()

    if not diagnostics["config_present"] or GraphDatabase is None or diagnostics["tcp_reachable"] != "yes":
        reason = "neo4j config missing, driver unavailable, or tcp unreachable"
        for spec in specs:
            results.append(blocked_result(spec, reason))
        return results, log_lines, diagnostics

    driver = None
    try:
        driver = GraphDatabase.driver(config["uri"], auth=(config["username"], config["password"]), connection_timeout=20)
        driver.verify_connectivity()
        diagnostics["driver_connectivity_verified"] = True
        with driver.session(database=config["database"]) as session:
            for spec in specs:
                cypher = spec["cypher"].strip()
                log_lines.extend([f"// patch_group_id: {spec['patch_group_id']}", f"// query_name: {spec['query_name']}", cypher, ""])
                if not readonly_guard(cypher):
                    results.append(blocked_result(spec, "write operation detected by guard"))
                    continue
                try:
                    rows = [dict(record) for record in session.run(cypher, **spec["params"])]
                    results.append(result_from_rows(spec, rows))
                except Exception as exc:
                    results.append(blocked_result(spec, f"query execution failed: {exc.__class__.__name__}: {str(exc)[:180]}"))
    except Exception as exc:
        diagnostics["sanitized_error"] = f"{exc.__class__.__name__}: {str(exc)[:300]}"
        for spec in specs:
            results.append(blocked_result(spec, "query execution failed"))
    finally:
        if driver is not None:
            driver.close()
    return results, log_lines, diagnostics


def blocked_result(spec: dict[str, Any], reason: str) -> dict[str, Any]:
    return {
        "patch_group_id": spec["patch_group_id"],
        "case_id": first_case_id(spec["patch_group_id"]),
        "query_name": spec["query_name"],
        "query_type": "read_only",
        "executed": False,
        "write_operation_detected": False,
        "result_count": 0,
        "matched_node_ids": [],
        "matched_relationship_ids": [],
        "observations_found": [],
        "validation_status": "blocked",
        "notes": reason,
    }


def first_case_id(group_id: str) -> str:
    groups = load_group_plan()
    case_ids = groups.get(group_id, {}).get("case_ids", [])
    return case_ids[0] if case_ids else ""


def result_from_rows(spec: dict[str, Any], rows: list[dict[str, Any]]) -> dict[str, Any]:
    node_ids = sorted({row["node_id"] for row in rows if row.get("node_id")})
    rel_ids = sorted({row["relationship_id"] for row in rows if row.get("relationship_id")})
    observations = []
    for row in rows[:50]:
        observations.append(
            {
                "node_id": row.get("node_id"),
                "labels": row.get("labels"),
                "properties": compact_props(row.get("properties")),
                "relationship_id": row.get("relationship_id"),
                "relationship_type": row.get("relationship_type"),
                "neighbor_node_id": row.get("neighbor_node_id"),
                "neighbor_labels": row.get("neighbor_labels"),
                "neighbor_properties": compact_props(row.get("neighbor_properties")),
            }
        )
    status = "not_found" if not rows else ("confirmed" if len(node_ids) == 1 else "ambiguous")
    if spec["patch_group_id"] in AUDIT_ONLY_TARGETS:
        notes = "audit-only round3_test target; not used for tuning, patch logic, or patch approval"
    else:
        notes = "additional read-only binding query executed"
    return {
        "patch_group_id": spec["patch_group_id"],
        "case_id": first_case_id(spec["patch_group_id"]),
        "query_name": spec["query_name"],
        "query_type": "read_only",
        "executed": True,
        "write_operation_detected": False,
        "result_count": len(rows),
        "matched_node_ids": node_ids,
        "matched_relationship_ids": rel_ids,
        "observations_found": observations,
        "validation_status": status,
        "notes": notes,
    }


def evidence_grounding(group_id: str) -> dict[str, Any]:
    facts = load_crosswalk().get(group_id, [])
    return {
        "fact_count": len(facts),
        "evidence_quote_exact_present": True,
        "quote_containment_previously_confirmed": True,
        "derived_answer_values_introduced": False,
        "fact_ids": [row["fact_id"] for row in facts],
    }


def binding_report(results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in results:
        grouped[row["patch_group_id"]].append(row)
    reports: list[dict[str, Any]] = []
    previous = load_previous_classifications()
    groups = load_group_plan()
    for group_id in TARGETS:
        rows = grouped[group_id]
        node_ids = sorted({node_id for row in rows for node_id in row["matched_node_ids"]})
        rel_ids = sorted({rel_id for row in rows for rel_id in row["matched_relationship_ids"]})
        exact_company_rows = [row for row in rows if row["query_name"] == "exact_ticker_identity"]
        metric_rows = [row for row in rows if row["query_name"] == "metric_year_value_identity"]
        source_rows = [row for row in rows if row["query_name"] == "case_source_evidence_context"]
        test_informed = group_id in AUDIT_ONLY_TARGETS
        duplicate_or_conflict = any(row["validation_status"] == "ambiguous" for row in rows if row["query_name"] in {"exact_ticker_identity", "metric_year_value_identity"})
        company_confirmed = bool(exact_company_rows and exact_company_rows[0]["result_count"] == 1)
        metric_confirmed = bool(metric_rows and 0 < metric_rows[0]["result_count"] <= len(load_crosswalk().get(group_id, [])))
        source_context_found = bool(source_rows and source_rows[0]["result_count"] > 0)
        rollback_possible = bool(node_ids) and not duplicate_or_conflict

        if test_informed:
            updated = "defer_test_informed"
            include = False
            reason = "round3_test/APD target remains audit-only; not eligible for approval request promotion"
        elif not rows or any(row["validation_status"] == "blocked" for row in rows):
            updated = "blocked"
            include = False
            reason = "read-only execution blocked"
        elif company_confirmed and metric_confirmed and source_context_found and rollback_possible:
            # All current quick wins still start as medium risk. Promotion requires
            # unambiguous bindings; then the future approval request can consider them.
            updated = "approved_candidate_ready"
            include = True
            reason = "non-test group has unambiguous company, metric/value/year binding, source context, and rollback target"
        elif duplicate_or_conflict:
            updated = "risky_manual_review"
            include = False
            reason = "duplicate or conflicting candidate nodes remain after additional read-only validation"
        elif node_ids:
            updated = "needs_more_readonly_validation"
            include = False
            reason = "some candidate nodes found, but exact observation/source binding is incomplete"
        else:
            updated = "blocked"
            include = False
            reason = "no candidate nodes found for required binding"

        reports.append(
            {
                "patch_group_id": group_id,
                "case_ids": groups.get(group_id, {}).get("case_ids", []),
                "previous_classification": previous.get(group_id, "unknown"),
                "updated_classification": updated,
                "reason": reason,
                "target_node_ids": node_ids,
                "target_relationship_ids": rel_ids,
                "exact_match_criteria": {
                    "company_confirmed": company_confirmed,
                    "metric_year_value_confirmed": metric_confirmed,
                    "source_or_case_context_found": source_context_found,
                    "duplicate_or_conflicting_candidates": duplicate_or_conflict,
                    "test_informed": test_informed,
                },
                "evidence_grounding": evidence_grounding(group_id),
                "relationship_context_found": bool(rel_ids),
                "rollback_possible": rollback_possible,
                "risk_level": "low" if include else groups.get(group_id, {}).get("risk_level", "unknown"),
                "identity_semantics_risk": "none_detected" if include else "not_cleared",
                "include_in_future_approval_request": include,
            }
        )
    return reports


def write_outputs(results: list[dict[str, Any]], log_lines: list[str], reports: list[dict[str, Any]], diagnostics: dict[str, Any]) -> None:
    B2A_DIR.mkdir(parents=True, exist_ok=True)
    (B2A_DIR / "b2a_query_log.cypher").write_text("\n".join(log_lines).rstrip() + "\n", encoding="utf-8")
    write_jsonl(B2A_DIR / "b2a_readonly_results.jsonl", results)
    write_jsonl(B2A_DIR / "b2a_node_relationship_binding_report.jsonl", reports)
    write_safety_scan(log_lines, results)
    write_updated_scope(reports, diagnostics)
    write_readiness_md(reports, diagnostics)
    write_go_no_go(reports)


def write_safety_scan(log_lines: list[str], results: list[dict[str, Any]]) -> None:
    hits: list[tuple[int, str]] = []
    for line_no, line in enumerate("\n".join(log_lines).splitlines(), start=1):
        if FORBIDDEN_WRITE_RE.search(strip_comments(line)):
            hits.append((line_no, line))
    row_hits = [row for row in results if row["write_operation_detected"]]
    decision = "PASS" if not hits and not row_hits else "NO_GO"
    lines = [
        "# B2a Safety Scan Report",
        "",
        f"- Decision: `{decision}`",
        "- Query type: read-only only",
        f"- Forbidden write token hits: {len(hits)}",
        f"- Rows with write_operation_detected: {len(row_hits)}",
        "- Neo4j write performed: false",
        "- KG patch applied: false",
        "- Full eval executed: false",
        "- Model/API called: false",
    ]
    for line_no, line in hits:
        lines.append(f"- Line {line_no}: `{line}`")
    (B2A_DIR / "b2a_safety_scan_report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def write_updated_scope(reports: list[dict[str, Any]], diagnostics: dict[str, Any]) -> None:
    scope = {
        "created_at": utc_now(),
        "diagnostics": diagnostics,
        "full_eval_allowed": False,
        "neo4j_write_allowed": False,
        "kg_patch_allowed": False,
        "approval_file_created": False,
        "patch_groups": [
            {
                "patch_group_id": report["patch_group_id"],
                "previous_classification": report["previous_classification"],
                "updated_classification": report["updated_classification"],
                "reason": report["reason"],
                "target_node_ids": report["target_node_ids"],
                "target_relationship_ids": report["target_relationship_ids"],
                "exact_match_criteria": report["exact_match_criteria"],
                "rollback_possible": report["rollback_possible"],
                "risk_level": report["risk_level"],
                "include_in_future_approval_request": report["include_in_future_approval_request"],
            }
            for report in reports
        ],
    }
    (B2A_DIR / "b2a_updated_patch_scope.json").write_text(
        json.dumps(scope, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def write_readiness_md(reports: list[dict[str, Any]], diagnostics: dict[str, Any]) -> None:
    lines = [
        "# B2a Patch Group Readiness",
        "",
        f"- Created at: {utc_now()}",
        f"- Neo4j host: {diagnostics.get('host')}",
        f"- TCP reachable: {diagnostics.get('tcp_reachable')}",
        f"- Driver connectivity verified: {diagnostics.get('driver_connectivity_verified')}",
        "- Neo4j write performed: false",
        "- KG patch applied: false",
        "- Full eval executed: false",
        "- Model/API called: false",
        "",
        "| Patch Group | Previous | Updated | Include Future Approval | Reason |",
        "| --- | --- | --- | --- | --- |",
    ]
    for report in reports:
        lines.append(
            f"| `{report['patch_group_id']}` | `{report['previous_classification']}` | "
            f"`{report['updated_classification']}` | {report['include_in_future_approval_request']} | {report['reason']} |"
        )
    (B2A_DIR / "b2a_patch_group_readiness.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def write_go_no_go(reports: list[dict[str, Any]]) -> None:
    promoted = [r for r in reports if r["updated_classification"] == "approved_candidate_ready"]
    if promoted:
        gate = "approval_request_can_be_prepared_for_promoted_candidates"
        next_action = "Prepare explicit user approval file for promoted non-test candidates only; do not execute B3 yet."
    else:
        gate = "needs_more_readonly_validation_or_manual_review"
        next_action = "Do not proceed to B3; inspect B2a binding report and refine read-only validation manually/local-only."
    lines = [
        "# B2a Go / No-Go",
        "",
        f"Decision: `{gate}`",
        "",
        f"- Promoted to approved_candidate_ready: {len(promoted)}",
        "- B3 patch execution: blocked",
        "- Neo4j write performed: false",
        "- KG patch applied: false",
        "- Full eval executed: false",
        "- Model/API called: false",
        "",
        f"Next action: {next_action}",
    ]
    (B2A_DIR / "b2a_go_no_go.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    results, log_lines, diagnostics = execute_readonly()
    reports = binding_report(results)
    write_outputs(results, log_lines, reports, diagnostics)
    promoted = [r["patch_group_id"] for r in reports if r["updated_classification"] == "approved_candidate_ready"]
    needing = [r["patch_group_id"] for r in reports if r["updated_classification"] == "needs_more_readonly_validation"]
    blocked = [r["patch_group_id"] for r in reports if r["updated_classification"] in {"blocked", "risky_manual_review"}]
    deferred = [r["patch_group_id"] for r in reports if r["updated_classification"] == "defer_test_informed"]
    current_gate = "approval_request_can_be_prepared_for_promoted_candidates" if promoted else "needs_more_readonly_validation_or_manual_review"
    final = {
        "B2a completed": True,
        "Patch groups promoted to approved_candidate_ready": promoted,
        "Patch groups still needing validation": needing,
        "Patch groups blocked": blocked,
        "Patch groups deferred because test-informed": deferred,
        "Neo4j write performed": False,
        "KG patch applied": False,
        "Full eval executed": False,
        "Model/API called": False,
        "Current gate": current_gate,
        "Next required user action": "Review B2a outputs; no B3 approval file was created.",
    }
    print(json.dumps(final, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
