"""Run Round 3 backlog remediation B1-B2 and stop before unapproved writes.

This operator is deliberately conservative:
- B1 executes read-only Neo4j validation queries only.
- B2 generates approval artifacts only.
- B3/B4/B5 are not fabricated when approval is absent.
"""

from __future__ import annotations

import json
import os
import re
import socket
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

try:
    from neo4j import GraphDatabase
except Exception:  # pragma: no cover - reported in artifacts
    GraphDatabase = None  # type: ignore[assignment]


ROOT = Path(__file__).resolve().parents[1]
BASE_DIR = ROOT / "outputs" / "round3_backlog_remediation_consolidated"
B1_DIR = BASE_DIR / "b1_readonly_validation"
B2_DIR = BASE_DIR / "b2_patch_approval_request"
APPROVAL_FILE = B2_DIR / "USER_APPROVED_B3_PATCH_SCOPE.json"

PRIMARY_TARGETS = ["pg_001_lin_ticker", "pg_002_mdlz_alias", "pg_004_bac_obs"]
AUDIT_ONLY_TARGETS = ["pg_003_apd_fiscal"]
TARGETS = PRIMARY_TARGETS + AUDIT_ONLY_TARGETS

WRITE_FORBIDDEN_RE = re.compile(
    r"\b(CREATE|MERGE|SET|DELETE|REMOVE|DROP|LOAD\s+CSV)\b|\bCALL\s+dbms\b|\bCALL\s+apoc\.periodic\b",
    re.IGNORECASE,
)


def now() -> str:
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
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        os.environ.setdefault(key, value)


def load_neo4j_config() -> dict[str, Any]:
    load_dotenv(ROOT / ".env")
    uri = os.environ.get("NEO4J_URI", "")
    username = os.environ.get("NEO4J_USERNAME") or os.environ.get("NEO4J_USER") or ""
    password = os.environ.get("NEO4J_PASSWORD", "")
    database = os.environ.get("NEO4J_DATABASE") or "neo4j"
    parsed = urlparse(uri)
    return {
        "uri": uri,
        "username": username,
        "password": password,
        "database": database,
        "config_present": bool(uri and username and password and database),
        "host": parsed.hostname or "",
        "port": parsed.port or (7687 if parsed.scheme.startswith("bolt") else None),
        "scheme": parsed.scheme,
    }


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    if not path.exists():
        return rows
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


def strip_cypher_comments(query: str) -> str:
    cleaned = []
    for line in query.splitlines():
        if "//" in line:
            line = line.split("//", 1)[0]
        cleaned.append(line)
    return "\n".join(cleaned)


def is_readonly(query: str) -> bool:
    return not WRITE_FORBIDDEN_RE.search(strip_cypher_comments(query))


def tcp_reachable(host: str, port: int | None) -> str:
    if not host or not port:
        return "unknown"
    try:
        with socket.create_connection((host, int(port)), timeout=10):
            return "yes"
    except OSError:
        return "no"


def group_plan_by_id() -> dict[str, dict[str, Any]]:
    return {row["patch_group_id"]: row for row in read_jsonl(BASE_DIR / "04_patch_group_plan.jsonl")}


def crosswalk_by_group() -> dict[str, list[dict[str, Any]]]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for row in read_jsonl(BASE_DIR / "06_patch_group_to_fact_crosswalk.jsonl"):
        grouped.setdefault(row["patch_group_id"], []).append(row)
    return grouped


def query_specs() -> list[dict[str, Any]]:
    return [
        {
            "patch_group_id": "pg_001_lin_ticker",
            "case_id": "round3_dev_009_4e1c0ef4",
            "query_name": "lin_entity_candidates",
            "params": {"ticker": "LIN", "legacy_ticker": "LND", "company_term": "linde"},
            "cypher": """
MATCH (c:KGEntity)
WHERE toUpper(toString(c.ticker)) IN [toUpper($ticker), toUpper($legacy_ticker)]
   OR toLower(toString(c.name)) CONTAINS toLower($company_term)
RETURN id(c) AS node_id, labels(c) AS labels, c.ticker AS ticker, c.name AS name
LIMIT 25
""",
        },
        {
            "patch_group_id": "pg_001_lin_ticker",
            "case_id": "round3_dev_009_4e1c0ef4",
            "query_name": "lin_net_income_observations",
            "params": {"ticker": "LIN", "metric": "net_income", "years": [2022, 2023]},
            "cypher": """
MATCH (obs:KGEntity)
WHERE toUpper(toString(obs.ticker)) = toUpper($ticker)
  AND toLower(toString(obs.metric)) = toLower($metric)
  AND obs.year IN $years
RETURN id(obs) AS node_id, labels(obs) AS labels, obs.ticker AS ticker, obs.metric AS metric,
       obs.numeric_value AS numeric_value, obs.value AS value, obs.year AS year, obs.unit AS unit
LIMIT 50
""",
        },
        {
            "patch_group_id": "pg_002_mdlz_alias",
            "case_id": "round3_dev_010_4a66fa95",
            "query_name": "mdlz_entity_candidates",
            "params": {"ticker": "MDLZ", "company_term": "mondelez"},
            "cypher": """
MATCH (c:KGEntity)
WHERE toUpper(toString(c.ticker)) = toUpper($ticker)
   OR toLower(toString(c.name)) CONTAINS toLower($company_term)
RETURN id(c) AS node_id, labels(c) AS labels, c.ticker AS ticker, c.name AS name
LIMIT 25
""",
        },
        {
            "patch_group_id": "pg_002_mdlz_alias",
            "case_id": "round3_dev_010_4a66fa95",
            "query_name": "mdlz_tax_observations",
            "params": {
                "ticker": "MDLZ",
                "metrics": ["earnings_before_income_taxes", "income_tax_provision"],
                "years": [2021, 2022, 2023],
            },
            "cypher": """
MATCH (obs:KGEntity)
WHERE toUpper(toString(obs.ticker)) = toUpper($ticker)
  AND obs.metric IN $metrics
  AND obs.year IN $years
RETURN id(obs) AS node_id, labels(obs) AS labels, obs.ticker AS ticker, obs.metric AS metric,
       obs.numeric_value AS numeric_value, obs.value AS value, obs.year AS year, obs.unit AS unit
LIMIT 75
""",
        },
        {
            "patch_group_id": "pg_002_mdlz_alias",
            "case_id": "round3_dev_010_4a66fa95",
            "query_name": "mdlz_equity_method_candidates",
            "params": {"metric_term": "equity_method_investment"},
            "cypher": """
MATCH (obs:KGEntity)
WHERE toLower(toString(obs.metric)) CONTAINS toLower($metric_term)
   OR toLower(toString(obs.name)) CONTAINS toLower($metric_term)
RETURN id(obs) AS node_id, labels(obs) AS labels, obs.ticker AS ticker, obs.metric AS metric,
       obs.numeric_value AS numeric_value, obs.value AS value, obs.year AS year, obs.unit AS unit
LIMIT 50
""",
        },
        {
            "patch_group_id": "pg_004_bac_obs",
            "case_id": "round3_dev_016_f488430a",
            "query_name": "bac_entity_candidates",
            "params": {"ticker": "BAC", "company_term": "bank of america"},
            "cypher": """
MATCH (c:KGEntity)
WHERE toUpper(toString(c.ticker)) = toUpper($ticker)
   OR toLower(toString(c.name)) CONTAINS toLower($company_term)
RETURN id(c) AS node_id, labels(c) AS labels, c.ticker AS ticker, c.name AS name
LIMIT 25
""",
        },
        {
            "patch_group_id": "pg_004_bac_obs",
            "case_id": "round3_dev_016_f488430a",
            "query_name": "bac_2023_observations",
            "params": {
                "ticker": "BAC",
                "metrics": ["total_noninterest_expense", "net_income", "interest_expense", "net_interest_income"],
                "year": 2023,
            },
            "cypher": """
MATCH (obs:KGEntity)
WHERE toUpper(toString(obs.ticker)) = toUpper($ticker)
  AND obs.metric IN $metrics
  AND obs.year = $year
RETURN id(obs) AS node_id, labels(obs) AS labels, obs.ticker AS ticker, obs.metric AS metric,
       obs.numeric_value AS numeric_value, obs.value AS value, obs.year AS year, obs.unit AS unit
LIMIT 75
""",
        },
        {
            "patch_group_id": "pg_003_apd_fiscal",
            "case_id": "round3_test_016_707dc83f",
            "query_name": "apd_entity_candidates_audit_only",
            "params": {"ticker": "APD", "company_term": "air products"},
            "cypher": """
MATCH (c:KGEntity)
WHERE toUpper(toString(c.ticker)) = toUpper($ticker)
   OR toLower(toString(c.name)) CONTAINS toLower($company_term)
RETURN id(c) AS node_id, labels(c) AS labels, c.ticker AS ticker, c.name AS name
LIMIT 25
""",
        },
        {
            "patch_group_id": "pg_003_apd_fiscal",
            "case_id": "round3_test_016_707dc83f",
            "query_name": "apd_net_income_observations_audit_only",
            "params": {"ticker": "APD", "metric": "net_income"},
            "cypher": """
MATCH (obs:KGEntity)
WHERE toUpper(toString(obs.ticker)) = toUpper($ticker)
  AND toLower(toString(obs.metric)) = toLower($metric)
RETURN id(obs) AS node_id, labels(obs) AS labels, obs.ticker AS ticker, obs.metric AS metric,
       obs.numeric_value AS numeric_value, obs.value AS value, obs.year AS year, obs.period_label AS period_label, obs.unit AS unit
LIMIT 50
""",
        },
    ]


def summarize_record(record: dict[str, Any]) -> dict[str, Any]:
    safe_keys = ["node_id", "rel_id", "labels", "ticker", "name", "metric", "numeric_value", "value", "year", "period_label", "unit"]
    return {key: record.get(key) for key in safe_keys if key in record and record.get(key) is not None}


def run_b1() -> tuple[list[dict[str, Any]], dict[str, Any]]:
    B1_DIR.mkdir(parents=True, exist_ok=True)
    config = load_neo4j_config()
    diagnostics = {
        "created_at": now(),
        "config_present": config["config_present"],
        "uri_scheme": config["scheme"],
        "host": config["host"],
        "port": config["port"],
        "tcp_reachable": tcp_reachable(config["host"], config["port"]),
        "database": config["database"],
        "driver_connectivity_verified": False,
        "sanitized_error": "",
    }
    results: list[dict[str, Any]] = []
    log_lines: list[str] = [
        "// B1 read-only validation query log",
        f"// created_at: {diagnostics['created_at']}",
        "// no write operations requested or executed",
        "",
    ]

    specs = query_specs()
    if not config["config_present"] or GraphDatabase is None:
        reason = "neo4j config missing" if not config["config_present"] else "neo4j driver unavailable"
        for spec in specs:
            results.append(blocked_result(spec, reason))
        write_b1_files(results, diagnostics, log_lines)
        return results, diagnostics

    if diagnostics["tcp_reachable"] != "yes":
        for spec in specs:
            results.append(blocked_result(spec, "tcp not reachable"))
        write_b1_files(results, diagnostics, log_lines)
        return results, diagnostics

    driver = None
    try:
        driver = GraphDatabase.driver(
            config["uri"],
            auth=(config["username"], config["password"]),
            connection_timeout=20,
        )
        driver.verify_connectivity()
        diagnostics["driver_connectivity_verified"] = True
        with driver.session(database=config["database"]) as session:
            for spec in specs:
                query = spec["cypher"].strip()
                log_lines.extend(
                    [
                        f"// patch_group_id: {spec['patch_group_id']}",
                        f"// query_name: {spec['query_name']}",
                        query,
                        "",
                    ]
                )
                if not is_readonly(query):
                    results.append(blocked_result(spec, "write operation detected by guard"))
                    continue
                records = [dict(record) for record in session.run(query, **spec["params"])]
                results.append(result_from_records(spec, records))
    except Exception as exc:
        diagnostics["sanitized_error"] = f"{exc.__class__.__name__}: {str(exc)[:300]}"
        for spec in specs:
            results.append(blocked_result(spec, "driver connectivity or query failed"))
    finally:
        if driver is not None:
            driver.close()

    write_b1_files(results, diagnostics, log_lines)
    return results, diagnostics


def blocked_result(spec: dict[str, Any], reason: str) -> dict[str, Any]:
    return {
        "patch_group_id": spec["patch_group_id"],
        "case_id": spec["case_id"],
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


def result_from_records(spec: dict[str, Any], records: list[dict[str, Any]]) -> dict[str, Any]:
    node_ids = sorted({record["node_id"] for record in records if record.get("node_id") is not None})
    rel_ids = sorted({record["rel_id"] for record in records if record.get("rel_id") is not None})
    observations = [summarize_record(record) for record in records[:25]]
    count = len(records)
    if count == 0:
        status = "not_found"
    elif count <= 3:
        status = "confirmed"
    else:
        status = "partial" if "observations" in spec["query_name"] else "ambiguous"
    if spec["patch_group_id"] in AUDIT_ONLY_TARGETS:
        notes = "audit-only round3_test target; do not use for tuning, ontology modification, or patch execution"
    else:
        notes = "read-only validation query executed"
    return {
        "patch_group_id": spec["patch_group_id"],
        "case_id": spec["case_id"],
        "query_name": spec["query_name"],
        "query_type": "read_only",
        "executed": True,
        "write_operation_detected": False,
        "result_count": count,
        "matched_node_ids": node_ids,
        "matched_relationship_ids": rel_ids,
        "observations_found": observations,
        "validation_status": status,
        "notes": notes,
    }


def write_b1_files(results: list[dict[str, Any]], diagnostics: dict[str, Any], log_lines: list[str]) -> None:
    (B1_DIR / "b1_query_log.cypher").write_text("\n".join(log_lines).rstrip() + "\n", encoding="utf-8")
    write_jsonl(B1_DIR / "b1_readonly_validation_results.jsonl", results)
    write_b1_summary(results, diagnostics)
    write_b1_safety_scan(results, log_lines)


def write_b1_summary(results: list[dict[str, Any]], diagnostics: dict[str, Any]) -> None:
    by_group: dict[str, list[dict[str, Any]]] = {}
    for row in results:
        by_group.setdefault(row["patch_group_id"], []).append(row)
    lines = [
        "# B1 Read-Only Validation Summary",
        "",
        f"- Created at: {diagnostics['created_at']}",
        f"- Neo4j config present: {diagnostics['config_present']}",
        f"- URI host: {diagnostics['host']}",
        f"- TCP reachable: {diagnostics['tcp_reachable']}",
        f"- Driver connectivity verified: {diagnostics['driver_connectivity_verified']}",
        f"- Database: {diagnostics['database']}",
        "- Neo4j write performed: false",
        "- KG patch applied: false",
        "- Full eval executed: false",
        "- Model/API called: false",
        "",
        "## Group Results",
        "",
        "| Patch Group | Scope | Queries | Statuses | Total Results | Notes |",
        "| --- | --- | ---: | --- | ---: | --- |",
    ]
    for group_id in TARGETS:
        rows = by_group.get(group_id, [])
        statuses = sorted({row["validation_status"] for row in rows})
        total = sum(int(row["result_count"]) for row in rows)
        scope = "audit_only_test" if group_id in AUDIT_ONLY_TARGETS else "non_test_primary"
        notes = "APD/test observations are audit-only and not patch/tuning evidence." if group_id in AUDIT_ONLY_TARGETS else "Read-only validation only."
        lines.append(f"| `{group_id}` | {scope} | {len(rows)} | {', '.join(statuses)} | {total} | {notes} |")
    if diagnostics["sanitized_error"]:
        lines.extend(["", "## Sanitized Error", "", f"`{diagnostics['sanitized_error']}`"])
    (B1_DIR / "b1_readonly_validation_summary.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def write_b1_safety_scan(results: list[dict[str, Any]], log_lines: list[str]) -> None:
    query_log = "\n".join(log_lines)
    forbidden_hits = []
    for line_no, line in enumerate(query_log.splitlines(), start=1):
        if WRITE_FORBIDDEN_RE.search(strip_cypher_comments(line)):
            forbidden_hits.append((line_no, line))
    row_hits = [row for row in results if row["write_operation_detected"]]
    decision = "PASS" if not forbidden_hits and not row_hits else "NO_GO"
    lines = [
        "# B1 No-Write Safety Scan",
        "",
        f"- Decision: `{decision}`",
        "- Scan rule: no CREATE/MERGE/SET/DELETE/REMOVE/DROP/LOAD CSV/CALL dbms/CALL apoc.periodic in executed queries.",
        f"- Query log: `{rel(B1_DIR / 'b1_query_log.cypher')}`",
        f"- Write operation detected rows: {len(row_hits)}",
        f"- Forbidden token hits: {len(forbidden_hits)}",
        "",
    ]
    if forbidden_hits:
        lines.append("## Forbidden Hits")
        for line_no, line in forbidden_hits:
            lines.append(f"- Line {line_no}: `{line}`")
    else:
        lines.append("No write operation tokens were detected in B1 executed query log.")
    (B1_DIR / "b1_no_write_safety_scan.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def classify_candidate(group: dict[str, Any], group_results: list[dict[str, Any]]) -> str:
    if group["patch_group_id"] in AUDIT_ONLY_TARGETS:
        return "defer_test_informed"
    if not group_results or any(row["validation_status"] == "blocked" for row in group_results):
        return "blocked"
    total_results = sum(int(row["result_count"]) for row in group_results)
    statuses = {row["validation_status"] for row in group_results}
    risk = str(group.get("risk_level", "")).lower()
    if total_results == 0 or statuses == {"not_found"}:
        return "needs_more_readonly_validation"
    if risk == "low" and statuses <= {"confirmed", "partial"}:
        return "approved_candidate_ready"
    if risk in {"high", "medium_high"}:
        return "risky_manual_review"
    return "needs_more_readonly_validation"


def commented_preview_line(line: str) -> str:
    return line if line.strip().startswith("//") else f"// {line}"


def write_b2(results: list[dict[str, Any]]) -> dict[str, Any]:
    B2_DIR.mkdir(parents=True, exist_ok=True)
    groups = group_plan_by_id()
    grouped_results: dict[str, list[dict[str, Any]]] = {}
    for row in results:
        grouped_results.setdefault(row["patch_group_id"], []).append(row)

    scope_rows = []
    for group_id in TARGETS:
        group = groups.get(group_id, {"patch_group_id": group_id, "case_ids": [], "risk_level": "unknown"})
        classification = classify_candidate(group, grouped_results.get(group_id, []))
        scope_rows.append(
            {
                "patch_group_id": group_id,
                "case_ids": group.get("case_ids", []),
                "description": group.get("description", ""),
                "tier": group.get("tier", ""),
                "risk_level": group.get("risk_level", "unknown"),
                "is_test_informed": group_id in AUDIT_ONLY_TARGETS,
                "candidate_classification": classification,
                "b1_result_count": sum(int(row["result_count"]) for row in grouped_results.get(group_id, [])),
                "requires_manual_approval": True,
                "user_approved": False,
                "neo4j_write_allowed": False,
                "kg_patch_allowed": False,
            }
        )

    scope = {
        "created_at": now(),
        "approval_file_required_for_b3": rel(APPROVAL_FILE),
        "user_approval_present": APPROVAL_FILE.exists(),
        "allow_full_eval": False,
        "candidates": scope_rows,
    }
    (B2_DIR / "b2_patch_scope.json").write_text(
        json.dumps(scope, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    write_b2_markdown(scope_rows)
    write_b2_disabled_cypher(scope_rows)
    write_b2_rollback_plan(scope_rows)
    write_b2_safety_scan()
    write_b2_go_no_go(scope)
    if not APPROVAL_FILE.exists():
        write_b2_blocker()
    return scope


def write_b2_markdown(scope_rows: list[dict[str, Any]]) -> None:
    lines = [
        "# B2 Patch Approval Request",
        "",
        "This is an approval request package only. No candidate is approved by this file.",
        "",
        "## Candidate Classification",
        "",
        "| Patch Group | Cases | Risk | Classification | Test-Informed | B1 Results |",
        "| --- | --- | --- | --- | --- | ---: |",
    ]
    for row in scope_rows:
        lines.append(
            f"| `{row['patch_group_id']}` | {', '.join(row['case_ids'])} | {row['risk_level']} | "
            f"`{row['candidate_classification']}` | {row['is_test_informed']} | {row['b1_result_count']} |"
        )
    lines.extend(
        [
            "",
            "## Approval Gate",
            "",
            f"B3 may run only if `{rel(APPROVAL_FILE)}` exists and explicitly allows Neo4j write and KG patch while keeping full eval disabled.",
            "",
            "APD/test-informed candidates remain deferred unless explicitly approved and marked test-informed.",
        ]
    )
    (B2_DIR / "b2_patch_approval_request.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def write_b2_disabled_cypher(scope_rows: list[dict[str, Any]]) -> None:
    lines = [
        "// B2 CANDIDATE PATCH PREVIEW - DISABLED",
        "// Every line is commented; this file is not executable.",
        "// No approval is implied.",
        "",
    ]
    for row in scope_rows:
        lines.extend(
            [
                f"// PATCH_GROUP: {row['patch_group_id']}",
                f"// CLASSIFICATION: {row['candidate_classification']}",
                f"// RISK: {row['risk_level']}",
                "// MATCH (target:KGEntity) WHERE id(target) = $validated_node_id",
                "// MERGE/SET preview intentionally omitted until exact approved write scope exists.",
                "// DO NOT EXECUTE WITHOUT USER_APPROVED_B3_PATCH_SCOPE.json",
                "",
            ]
        )
    (B2_DIR / "b2_candidate_patch_preview.disabled.cypher").write_text("\n".join(lines), encoding="utf-8")


def write_b2_rollback_plan(scope_rows: list[dict[str, Any]]) -> None:
    lines = [
        "// B2 ROLLBACK PLAN PREVIEW - DISABLED",
        "// No patch has been executed, so rollback is a placeholder until B3 records exact writes.",
        "",
    ]
    for row in scope_rows:
        lines.extend(
            [
                f"// PATCH_GROUP: {row['patch_group_id']}",
                "// MATCH (n:KGEntity) WHERE n.__round3_patch_group_id = $patch_group_id",
                "// REMOVE n.__round3_patch_group_id, n.__round3_patch_timestamp",
                "// DELETE preview intentionally disabled and incomplete until B3 execution scope exists.",
                "",
            ]
        )
    (B2_DIR / "b2_rollback_plan.cypher").write_text("\n".join(lines), encoding="utf-8")


def scan_cypher_file(path: Path) -> list[tuple[int, str]]:
    hits = []
    for line_no, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if WRITE_FORBIDDEN_RE.search(strip_cypher_comments(line)):
            hits.append((line_no, line))
    return hits


def write_b2_safety_scan() -> None:
    scanned = [
        B2_DIR / "b2_candidate_patch_preview.disabled.cypher",
        B2_DIR / "b2_rollback_plan.cypher",
    ]
    all_hits: list[tuple[str, int, str]] = []
    for path in scanned:
        for line_no, line in scan_cypher_file(path):
            all_hits.append((path.name, line_no, line))
    decision = "PASS" if not all_hits else "NO_GO"
    lines = [
        "# B2 Safety Scan Report",
        "",
        f"- Decision: `{decision}`",
        "- Scan target: disabled preview and rollback plan.",
        "- Rule: no uncommented write operations.",
        f"- Violations: {len(all_hits)}",
        "",
    ]
    for name, line_no, line in all_hits:
        lines.append(f"- {name}:{line_no}: `{line}`")
    if not all_hits:
        lines.append("No uncommented write operations were found.")
    (B2_DIR / "b2_safety_scan_report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def write_b2_go_no_go(scope: dict[str, Any]) -> None:
    approval_present = bool(scope["user_approval_present"])
    decision = "stop_before_B3_user_approval_required" if not approval_present else "approval_file_present_needs_validation"
    lines = [
        "# B2 Go / No-Go",
        "",
        f"Decision: `{decision}`",
        "",
        "- B1 read-only validation completed.",
        "- B2 approval request generated.",
        "- B3 patch execution is blocked unless the approval file is present and valid.",
        "- Full eval remains locked.",
        "- Neo4j write performed: false",
        "- KG patch applied: false",
    ]
    (B2_DIR / "b2_go_no_go.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def write_b2_blocker() -> None:
    lines = [
        "# B2 Blocker Report",
        "",
        "B3 stopped because explicit user approval is absent.",
        "",
        f"Required approval file: `{rel(APPROVAL_FILE)}`",
        "",
        "No B3, B4, or B5 success files were created.",
    ]
    (B2_DIR / "b2_blocker_report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def validate_approval_file() -> tuple[bool, str, dict[str, Any]]:
    if not APPROVAL_FILE.exists():
        return False, "approval file missing", {}
    try:
        data = json.loads(APPROVAL_FILE.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        return False, f"approval file malformed: {exc}", {}
    required = {
        "user_approved": True,
        "allow_neo4j_write": True,
        "allow_kg_patch": True,
        "allow_full_eval": False,
    }
    for key, expected in required.items():
        if data.get(key) is not expected:
            return False, f"approval field {key} expected {expected}", data
    approved_ids = data.get("approved_patch_group_ids")
    if not isinstance(approved_ids, list) or not approved_ids:
        return False, "approved_patch_group_ids missing or empty", data
    if "pg_003_apd_fiscal" in approved_ids and not data.get("allow_test_informed_patches"):
        return False, "test-informed APD patch included without explicit test-informed approval", data
    for key in ["approval_text", "approval_timestamp"]:
        if not data.get(key):
            return False, f"{key} missing", data
    return True, "approval valid", data


def main() -> None:
    results, _diagnostics = run_b1()
    scope = write_b2(results)
    approval_valid, approval_reason, _approval_data = validate_approval_file()
    terminal = "blocked_before_B3_user_approval_required" if not approval_valid else "ready_for_B3_approved_patch_execution"
    result = {
        "B1 completed": True,
        "B2 approval request generated": True,
        "B3 patch executed": False,
        "B4 coverage rerun completed": False,
        "B5 decision generated": False,
        "Neo4j write performed": False,
        "KG patch applied": False,
        "Full eval executed": False,
        "Model/API called": False,
        "Current gate": terminal,
        "Next required user action": approval_reason if not approval_valid else "run B3 with approved scope",
        "Created files": [
            rel(B1_DIR / "b1_query_log.cypher"),
            rel(B1_DIR / "b1_readonly_validation_results.jsonl"),
            rel(B1_DIR / "b1_readonly_validation_summary.md"),
            rel(B1_DIR / "b1_no_write_safety_scan.md"),
            rel(B2_DIR / "b2_patch_approval_request.md"),
            rel(B2_DIR / "b2_patch_scope.json"),
            rel(B2_DIR / "b2_candidate_patch_preview.disabled.cypher"),
            rel(B2_DIR / "b2_safety_scan_report.md"),
            rel(B2_DIR / "b2_rollback_plan.cypher"),
            rel(B2_DIR / "b2_go_no_go.md"),
        ],
        "Approval present": scope["user_approval_present"],
        "Approval valid": approval_valid,
    }
    if not approval_valid:
        result["Created files"].append(rel(B2_DIR / "b2_blocker_report.md"))
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
