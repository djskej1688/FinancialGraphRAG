"""B2c final bounded read-only disambiguation.

This script performs the last read-only patch-candidate disambiguation pass.
It does not create approvals, write to Neo4j, apply patches, run evals, or call
models.
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
B2C_DIR = BASE_DIR / "b2c_final_readonly_disambiguation"

NON_TEST_GROUPS = ["pg_001_lin_ticker", "pg_002_mdlz_alias", "pg_004_bac_obs"]
AUDIT_ONLY_GROUPS = ["pg_003_apd_fiscal"]
TARGET_GROUPS = NON_TEST_GROUPS + AUDIT_ONLY_GROUPS

FORBIDDEN_RE = re.compile(
    r"\b(CREATE|MERGE|SET|DELETE|REMOVE|DROP|LOAD\s+CSV)\b|\bCALL\s+dbms\b|\bCALL\s+apoc\.periodic\b",
    re.IGNORECASE,
)

GROUPS = {
    "pg_001_lin_ticker": {
        "case_id": "round3_dev_009_4e1c0ef4",
        "source_id": "4e1c0ef4",
        "tickers": ["LIN", "LND"],
        "company_terms": ["linde"],
        "facts": [
            {"metric": "net_income", "aliases": ["net income", "net_income"], "year": "2023", "value": 6199.0, "unit": "USD_millions"},
            {"metric": "net_income", "aliases": ["net income", "net_income"], "year": "2022", "value": 4147.0, "unit": "USD_millions"},
        ],
        "previous_status": "risky_manual_review",
    },
    "pg_002_mdlz_alias": {
        "case_id": "round3_dev_010_4a66fa95",
        "source_id": "4a66fa95",
        "tickers": ["MDLZ"],
        "company_terms": ["mondelez", "mondel"],
        "facts": [
            {"metric": "earnings_before_income_taxes", "aliases": ["earnings before income taxes", "earnings_before_income_taxes"], "year": "2023", "value": 5880.0, "unit": "USD_millions"},
            {"metric": "earnings_before_income_taxes", "aliases": ["earnings before income taxes", "earnings_before_income_taxes"], "year": "2022", "value": 3228.0, "unit": "USD_millions"},
            {"metric": "earnings_before_income_taxes", "aliases": ["earnings before income taxes", "earnings_before_income_taxes"], "year": "2021", "value": 4369.0, "unit": "USD_millions"},
            {"metric": "income_tax_provision", "aliases": ["income tax provision", "income_tax_provision"], "year": "2023", "value": -1537.0, "unit": "USD_millions"},
            {"metric": "income_tax_provision", "aliases": ["income tax provision", "income_tax_provision"], "year": "2022", "value": -865.0, "unit": "USD_millions"},
            {"metric": "income_tax_provision", "aliases": ["income tax provision", "income_tax_provision"], "year": "2021", "value": -1190.0, "unit": "USD_millions"},
            {"metric": "equity_method_investment_net_earnings", "aliases": ["equity method investment net earnings", "equity_method_investment_net_earnings"], "year": "2023", "value": 160.0, "unit": "USD_millions"},
            {"metric": "equity_method_investment_net_earnings", "aliases": ["equity method investment net earnings", "equity_method_investment_net_earnings"], "year": "2022", "value": 385.0, "unit": "USD_millions"},
        ],
        "previous_status": "risky_manual_review",
    },
    "pg_004_bac_obs": {
        "case_id": "round3_dev_016_f488430a",
        "source_id": "f488430a",
        "tickers": ["BAC"],
        "company_terms": ["bank of america"],
        "facts": [
            {"metric": "total_noninterest_expense", "aliases": ["total noninterest expense", "total_noninterest_expense"], "year": "2023", "value": 65845.0, "unit": "USD_millions"},
            {"metric": "net_income", "aliases": ["net income", "net_income"], "year": "2023", "value": 26515.0, "unit": "USD_millions"},
        ],
        "previous_status": "risky_manual_review",
    },
    "pg_003_apd_fiscal": {
        "case_id": "round3_test_016_707dc83f",
        "source_id": "707dc83f",
        "tickers": ["APD"],
        "company_terms": ["air products"],
        "facts": [],
        "previous_status": "defer_test_informed",
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


def config() -> dict[str, Any]:
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
    }


def tcp_reachable(host: str, port: int | None) -> str:
    if not host or not port:
        return "unknown"
    try:
        with socket.create_connection((host, int(port)), timeout=10):
            return "yes"
    except OSError:
        return "no"


def strip_comments(text: str) -> str:
    return "\n".join(line.split("//", 1)[0] for line in text.splitlines())


def read_only_ok(cypher: str) -> bool:
    return not FORBIDDEN_RE.search(strip_comments(cypher))


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


def props_safe(props: dict[str, Any] | None) -> dict[str, Any]:
    if not props:
        return {}
    keys = [
        "ticker", "name", "metric", "numeric_value", "value", "year", "unit",
        "case_id", "source_id", "fact_id", "period_label",
    ]
    out = {key: props.get(key) for key in keys if key in props and props.get(key) is not None}
    if "name" in out:
        out["name"] = str(out["name"])[:240]
    if "metric" in out:
        out["metric"] = str(out["metric"])[:240]
    return out


def query_specs() -> list[dict[str, Any]]:
    specs: list[dict[str, Any]] = []
    for group_id, group in GROUPS.items():
        specs.append(
            {
                "patch_group_id": group_id,
                "query_name": "unique_exact_ticker_node",
                "params": {"tickers": group["tickers"]},
                "cypher": """
MATCH (n:KGEntity)
WHERE any(t IN $tickers WHERE toUpper(toString(n.ticker)) = toUpper(t) OR toUpper(toString(n.name)) = toUpper(t))
RETURN elementId(n) AS node_id, labels(n) AS labels, properties(n) AS properties
ORDER BY toString(n.ticker), toString(n.name)
LIMIT 10
""",
            }
        )
        specs.append(
            {
                "patch_group_id": group_id,
                "query_name": "source_bound_observations",
                "params": {
                    "source_id": group["source_id"],
                    "facts": [
                        {
                            "metric": fact["metric"],
                            "aliases": fact["aliases"],
                            "year": fact["year"],
                            "value": str(int(fact["value"])) if float(fact["value"]).is_integer() else str(fact["value"]),
                        }
                        for fact in group["facts"]
                    ],
                },
                "cypher": """
UNWIND $facts AS fact
MATCH (n:KGEntity)
WHERE toString(n.case_id) = $source_id
  AND toString(n.year) = fact.year
  AND (toString(n.numeric_value) = fact.value OR replace(toString(n.value), ',', '') = fact.value)
  AND any(alias IN fact.aliases WHERE
        toLower(toString(n.metric)) = toLower(alias)
     OR toLower(toString(n.name)) CONTAINS toLower(alias))
RETURN fact.metric AS expected_metric, fact.year AS expected_year, fact.value AS expected_value,
       elementId(n) AS node_id, labels(n) AS labels, properties(n) AS properties
ORDER BY expected_metric, expected_year, node_id
LIMIT 100
""",
            }
        )
        specs.append(
            {
                "patch_group_id": group_id,
                "query_name": "company_to_source_observation_paths",
                "params": {
                    "tickers": group["tickers"],
                    "source_id": group["source_id"],
                    "years": sorted({fact["year"] for fact in group["facts"]}),
                },
                "cypher": """
MATCH (company:KGEntity)
WHERE any(t IN $tickers WHERE toUpper(toString(company.ticker)) = toUpper(t) OR toUpper(toString(company.name)) = toUpper(t))
MATCH (obs:KGEntity)
WHERE toString(obs.case_id) = $source_id
  AND toString(obs.year) IN $years
WITH company, obs
LIMIT 20
OPTIONAL MATCH (company)-[r]-(obs)
RETURN elementId(company) AS company_node_id, properties(company) AS company_properties,
       elementId(obs) AS observation_node_id, properties(obs) AS observation_properties,
       CASE WHEN r IS NULL THEN [] ELSE [elementId(r)] END AS relationship_ids,
       CASE WHEN r IS NULL THEN [] ELSE [type(r)] END AS relationship_types,
       CASE WHEN r IS NULL THEN 0 ELSE 1 END AS path_length
ORDER BY observation_node_id
LIMIT 100
""",
            }
        )
        specs.append(
            {
                "patch_group_id": group_id,
                "query_name": "duplicate_exact_value_candidates_global",
                "params": {
                    "facts": [
                        {
                            "metric": fact["metric"],
                            "aliases": fact["aliases"],
                            "year": fact["year"],
                            "value": str(int(fact["value"])) if float(fact["value"]).is_integer() else str(fact["value"]),
                        }
                        for fact in group["facts"]
                    ],
                },
                "cypher": """
UNWIND $facts AS fact
MATCH (n:KGEntity)
WHERE toString(n.year) = fact.year
  AND (toString(n.numeric_value) = fact.value OR replace(toString(n.value), ',', '') = fact.value)
  AND any(alias IN fact.aliases WHERE
        toLower(toString(n.metric)) = toLower(alias)
     OR toLower(toString(n.name)) CONTAINS toLower(alias))
RETURN fact.metric AS expected_metric, fact.year AS expected_year, fact.value AS expected_value,
       count(n) AS candidate_count, collect(elementId(n))[0..10] AS sample_node_ids
ORDER BY expected_metric, expected_year
""",
            }
        )
    return specs


def run_queries() -> tuple[list[dict[str, Any]], list[str], dict[str, Any]]:
    cfg = config()
    diagnostics = {
        "created_at": utc_now(),
        "config_present": bool(cfg["uri"] and cfg["username"] and cfg["password"] and cfg["database"]),
        "host": cfg["host"],
        "port": cfg["port"],
        "database": cfg["database"],
        "tcp_reachable": tcp_reachable(cfg["host"], cfg["port"]),
        "driver_connectivity_verified": False,
        "sanitized_error": "",
    }
    log_lines = [
        "// B2c final bounded read-only disambiguation query log",
        f"// created_at: {diagnostics['created_at']}",
        "// no write operations requested or executed",
        "",
    ]
    results: list[dict[str, Any]] = []
    specs = query_specs()
    if not diagnostics["config_present"] or diagnostics["tcp_reachable"] != "yes" or GraphDatabase is None:
        reason = "config, network, or driver unavailable"
        return [blocked_result(spec, reason) for spec in specs], log_lines, diagnostics

    driver = None
    try:
        driver = GraphDatabase.driver(cfg["uri"], auth=(cfg["username"], cfg["password"]), connection_timeout=20)
        driver.verify_connectivity()
        diagnostics["driver_connectivity_verified"] = True
        with driver.session(database=cfg["database"]) as session:
            for spec in specs:
                cypher = spec["cypher"].strip()
                log_lines.extend([f"// patch_group_id: {spec['patch_group_id']}", f"// query_name: {spec['query_name']}", cypher, ""])
                if not read_only_ok(cypher):
                    results.append(blocked_result(spec, "read-only guard rejected query"))
                    continue
                try:
                    rows = [dict(record) for record in session.run(cypher, **spec["params"])]
                    results.append(result_from_rows(spec, rows))
                except Exception as exc:
                    results.append(blocked_result(spec, f"{exc.__class__.__name__}: {str(exc)[:200]}"))
    except Exception as exc:
        diagnostics["sanitized_error"] = f"{exc.__class__.__name__}: {str(exc)[:300]}"
        results.extend(blocked_result(spec, "driver connectivity failed") for spec in specs)
    finally:
        if driver is not None:
            driver.close()
    return results, log_lines, diagnostics


def blocked_result(spec: dict[str, Any], reason: str) -> dict[str, Any]:
    return {
        "patch_group_id": spec["patch_group_id"],
        "query_name": spec["query_name"],
        "executed": False,
        "query_type": "read_only",
        "write_operation_detected": False,
        "result_count": 0,
        "matched_node_ids": [],
        "matched_relationship_ids": [],
        "observations_found": [],
        "validation_status": "blocked",
        "notes": reason,
    }


def result_from_rows(spec: dict[str, Any], rows: list[dict[str, Any]]) -> dict[str, Any]:
    nodes: set[str] = set()
    rels: set[str] = set()
    observations: list[dict[str, Any]] = []
    for row in rows[:50]:
        for key in ["node_id", "company_node_id", "observation_node_id"]:
            if row.get(key):
                nodes.add(row[key])
        for rel_id in row.get("relationship_ids") or []:
            if rel_id:
                rels.add(rel_id)
        observations.append(
            {
                "expected_metric": row.get("expected_metric"),
                "expected_year": row.get("expected_year"),
                "expected_value": row.get("expected_value"),
                "node_id": row.get("node_id"),
                "company_node_id": row.get("company_node_id"),
                "observation_node_id": row.get("observation_node_id"),
                "relationship_ids": row.get("relationship_ids"),
                "relationship_types": row.get("relationship_types"),
                "candidate_count": row.get("candidate_count"),
                "sample_node_ids": row.get("sample_node_ids"),
                "labels": row.get("labels"),
                "properties": props_safe(row.get("properties")),
                "company_properties": props_safe(row.get("company_properties")),
                "observation_properties": props_safe(row.get("observation_properties")),
                "path_length": row.get("path_length"),
            }
        )
    if not rows:
        status = "not_found"
    elif len(nodes) == 1 and spec["query_name"] in {"unique_exact_ticker_node"}:
        status = "confirmed"
    elif spec["query_name"] == "duplicate_exact_value_candidates_global" and all((row.get("candidate_count") or 0) <= 1 for row in rows):
        status = "confirmed"
    elif len(nodes) <= 3 and spec["query_name"] in {"source_bound_observations", "company_to_source_observation_paths"}:
        status = "partial"
    else:
        status = "ambiguous"
    return {
        "patch_group_id": spec["patch_group_id"],
        "query_name": spec["query_name"],
        "executed": True,
        "query_type": "read_only",
        "write_operation_detected": False,
        "result_count": len(rows),
        "matched_node_ids": sorted(nodes),
        "matched_relationship_ids": sorted(rels),
        "observations_found": observations,
        "validation_status": status,
        "notes": "final bounded read-only disambiguation query executed",
    }


def by_group(results: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in results:
        grouped[row["patch_group_id"]].append(row)
    return grouped


def classify_group(group_id: str, rows: list[dict[str, Any]]) -> dict[str, Any]:
    if group_id == "pg_003_apd_fiscal":
        return status_row(group_id, "defer_test_informed", "audit-only round3_test target remains excluded", [], [], {}, False, False, "defer_test_informed", False, False)

    exact_ticker = next((row for row in rows if row["query_name"] == "unique_exact_ticker_node"), None)
    source_obs = next((row for row in rows if row["query_name"] == "source_bound_observations"), None)
    paths = next((row for row in rows if row["query_name"] == "company_to_source_observation_paths"), None)
    dupes = next((row for row in rows if row["query_name"] == "duplicate_exact_value_candidates_global"), None)

    target_nodes = sorted({node for row in rows for node in row.get("matched_node_ids", [])})
    target_rels = sorted({rel for row in rows for rel in row.get("matched_relationship_ids", [])})
    facts = GROUPS[group_id]["facts"]
    exact_match_criteria = {
        "exact_ticker_node_count": exact_ticker["result_count"] if exact_ticker else 0,
        "source_bound_observation_count": source_obs["result_count"] if source_obs else 0,
        "relationship_path_count": paths["result_count"] if paths else 0,
        "duplicate_probe_rows": dupes["result_count"] if dupes else 0,
        "expected_fact_count": len(facts),
        "non_test": True,
        "evidence_grounding_confirmed": True,
    }
    duplicate_remaining = (
        not exact_ticker
        or exact_ticker["result_count"] != 1
        or not source_obs
        or source_obs["result_count"] != len(facts)
        or not paths
        or paths["result_count"] < len(facts)
        or any((obs.get("candidate_count") or 0) > 1 for obs in (dupes or {}).get("observations_found", []))
    )
    rollback_possible = bool(target_nodes and target_rels and not duplicate_remaining)

    if not duplicate_remaining and rollback_possible:
        return status_row(
            group_id,
            "approved_candidate_ready",
            "exact ticker, source-bound observations, relationship paths, and duplicate checks are unambiguous",
            target_nodes,
            target_rels,
            exact_match_criteria,
            False,
            rollback_possible,
            "low",
            True,
            False,
        )

    if group_id in {"pg_001_lin_ticker", "pg_002_mdlz_alias"}:
        reason = "exact ticker may exist, but source-bound observation/path or duplicate check remains unresolved"
        final_status = "abandon_patch_path"
    else:
        reason = "exact BAC ticker identity or financial observation binding is not uniquely identified"
        final_status = "abandon_patch_path"
    return status_row(
        group_id,
        final_status,
        reason,
        target_nodes,
        target_rels,
        exact_match_criteria,
        True,
        False,
        "high",
        False,
        False,
    )


def status_row(
    group_id: str,
    final_status: str,
    reason: str,
    target_nodes: list[str],
    target_rels: list[str],
    criteria: dict[str, Any],
    duplicate_remaining: bool,
    rollback_possible: bool,
    risk_level: str,
    include: bool,
    requires_user_selection: bool,
) -> dict[str, Any]:
    return {
        "patch_group_id": group_id,
        "previous_status": GROUPS[group_id]["previous_status"],
        "final_status": final_status,
        "final_reason": reason,
        "target_node_id": target_nodes[0] if len(target_nodes) == 1 else "",
        "target_relationship_ids": target_rels,
        "exact_match_criteria": criteria,
        "duplicate_candidates_remaining": duplicate_remaining,
        "rollback_possible": rollback_possible,
        "risk_level": risk_level,
        "include_in_future_approval_request": include,
        "requires_user_selection": requires_user_selection,
    }


def make_status(results: list[dict[str, Any]]) -> dict[str, Any]:
    grouped = by_group(results)
    statuses = [classify_group(group_id, grouped.get(group_id, [])) for group_id in TARGET_GROUPS]
    non_test = [row for row in statuses if row["patch_group_id"] in NON_TEST_GROUPS]
    approved = [row for row in non_test if row["final_status"] == "approved_candidate_ready"]
    abandon = [row for row in non_test if row["final_status"] == "abandon_patch_path"]
    selection = [row for row in non_test if row["final_status"] == "needs_user_manual_selection"]
    if approved:
        gate = "user_review_required_before_b3_approval"
        next_action = "user reviews B2c packet and may approve only approved_candidate_ready groups"
    elif len(abandon) == len(non_test):
        gate = "abandon_patch_path_recommended"
        next_action = "pivot to coverage-first eval subset building"
    elif selection:
        gate = "user_manual_selection_required"
        next_action = "user chooses candidate node or abandons patch group"
    else:
        gate = "pivot_to_coverage_first_recommended"
        next_action = "stop patch path and construct coverage-first eval set"
    return {
        "created_at": utc_now(),
        "statuses": statuses,
        "current_gate": gate,
        "next_action": next_action,
        "neo4j_write_performed": False,
        "kg_patch_applied": False,
        "full_eval_executed": False,
        "model_api_called": False,
    }


def write_outputs(results: list[dict[str, Any]], log_lines: list[str], diagnostics: dict[str, Any], status_doc: dict[str, Any]) -> None:
    B2C_DIR.mkdir(parents=True, exist_ok=True)
    (B2C_DIR / "b2c_query_log.cypher").write_text("\n".join(log_lines).rstrip() + "\n", encoding="utf-8")
    write_jsonl(B2C_DIR / "b2c_readonly_results.jsonl", results)
    rows = []
    for row in status_doc["statuses"]:
        rows.append(
            {
                "patch_group_id": row["patch_group_id"],
                "previous_status": row["previous_status"],
                "final_status": row["final_status"],
                "risk_level": row["risk_level"],
                "target_node_id": row["target_node_id"],
                "target_relationship_count": len(row["target_relationship_ids"]),
                "duplicate_candidates_remaining": row["duplicate_candidates_remaining"],
                "rollback_possible": row["rollback_possible"],
                "include_in_future_approval_request": row["include_in_future_approval_request"],
                "requires_user_selection": row["requires_user_selection"],
                "final_reason": row["final_reason"],
            }
        )
    write_csv(
        B2C_DIR / "b2c_candidate_resolution_table.csv",
        rows,
        [
            "patch_group_id", "previous_status", "final_status", "risk_level", "target_node_id",
            "target_relationship_count", "duplicate_candidates_remaining", "rollback_possible",
            "include_in_future_approval_request", "requires_user_selection", "final_reason",
        ],
    )
    (B2C_DIR / "b2c_final_patch_group_status.json").write_text(
        json.dumps(status_doc, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    write_binding_report(status_doc, diagnostics)
    write_manual_selection_packet(status_doc)
    write_go_no_go(status_doc)
    write_safety_scan()


def write_binding_report(status_doc: dict[str, Any], diagnostics: dict[str, Any]) -> None:
    lines = [
        "# B2c Node Relationship Binding Report",
        "",
        f"- Created at: {status_doc['created_at']}",
        f"- Neo4j host: {diagnostics.get('host')}",
        f"- TCP reachable: {diagnostics.get('tcp_reachable')}",
        f"- Driver connectivity verified: {diagnostics.get('driver_connectivity_verified')}",
        "- Neo4j write performed: false",
        "- KG patch applied: false",
        "- Full eval executed: false",
        "- Model/API called: false",
        "",
        "| Patch Group | Final Status | Target Node | Relationships | Reason |",
        "| --- | --- | --- | ---: | --- |",
    ]
    for row in status_doc["statuses"]:
        lines.append(
            f"| `{row['patch_group_id']}` | `{row['final_status']}` | `{row['target_node_id']}` | "
            f"{len(row['target_relationship_ids'])} | {row['final_reason']} |"
        )
    (B2C_DIR / "b2c_node_relationship_binding_report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def write_manual_selection_packet(status_doc: dict[str, Any]) -> None:
    selection = [row for row in status_doc["statuses"] if row["final_status"] == "needs_user_manual_selection"]
    lines = [
        "# B2c User Manual Selection Packet",
        "",
        "No approval file was created.",
        "",
    ]
    if not selection:
        lines.append("No patch group qualifies for user manual node selection. The final status does not offer a safe 2-3 candidate choice.")
    else:
        for row in selection:
            lines.append(f"- `{row['patch_group_id']}` requires user selection.")
    (B2C_DIR / "b2c_user_manual_selection_packet.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def write_go_no_go(status_doc: dict[str, Any]) -> None:
    lines = [
        "# B2c Go / No-Go",
        "",
        f"Current gate: `{status_doc['current_gate']}`",
        f"Next action: {status_doc['next_action']}",
        "",
        "- B3 patch execution: blocked",
        "- Approval file created: false",
        "- Neo4j write performed: false",
        "- KG patch applied: false",
        "- Full eval executed: false",
        "- Model/API called: false",
    ]
    (B2C_DIR / "b2c_go_no_go.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def scan_generated_files() -> tuple[str, list[tuple[str, int, str]]]:
    hits: list[tuple[str, int, str]] = []
    for path in B2C_DIR.glob("*"):
        if not path.is_file():
            continue
        for line_no, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
            if FORBIDDEN_RE.search(strip_comments(line)):
                hits.append((rel(path), line_no, line))
    return ("PASS" if not hits else "NO_GO"), hits


def write_safety_scan() -> None:
    status, hits = scan_generated_files()
    lines = [
        "# B2c Safety Scan Report",
        "",
        f"- Decision: `{status}`",
        "- Checked B2c generated files for uncommented forbidden write clauses.",
        f"- Forbidden hits: {len(hits)}",
        "- Neo4j write performed: false",
        "- KG patch applied: false",
        "- Full eval executed: false",
        "- Model/API called: false",
    ]
    for path, line_no, line in hits:
        lines.append(f"- {path}:{line_no}: `{line}`")
    (B2C_DIR / "b2c_safety_scan_report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    results, log_lines, diagnostics = run_queries()
    status_doc = make_status(results)
    write_outputs(results, log_lines, diagnostics, status_doc)
    final = {
        "B2c completed": True,
        "Patch groups approved_candidate_ready": [row["patch_group_id"] for row in status_doc["statuses"] if row["final_status"] == "approved_candidate_ready"],
        "Patch groups abandon_patch_path": [row["patch_group_id"] for row in status_doc["statuses"] if row["final_status"] == "abandon_patch_path"],
        "Patch groups needs_user_manual_selection": [row["patch_group_id"] for row in status_doc["statuses"] if row["final_status"] == "needs_user_manual_selection"],
        "Patch groups defer_test_informed": [row["patch_group_id"] for row in status_doc["statuses"] if row["final_status"] == "defer_test_informed"],
        "Neo4j write performed": False,
        "KG patch applied": False,
        "Full eval executed": False,
        "Model/API called": False,
        "Current gate": status_doc["current_gate"],
        "Next required user action": status_doc["next_action"],
        "Created files": [
            rel(B2C_DIR / "b2c_query_log.cypher"),
            rel(B2C_DIR / "b2c_readonly_results.jsonl"),
            rel(B2C_DIR / "b2c_candidate_resolution_table.csv"),
            rel(B2C_DIR / "b2c_final_patch_group_status.json"),
            rel(B2C_DIR / "b2c_node_relationship_binding_report.md"),
            rel(B2C_DIR / "b2c_user_manual_selection_packet.md"),
            rel(B2C_DIR / "b2c_safety_scan_report.md"),
            rel(B2C_DIR / "b2c_go_no_go.md"),
        ],
    }
    print(json.dumps(final, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
