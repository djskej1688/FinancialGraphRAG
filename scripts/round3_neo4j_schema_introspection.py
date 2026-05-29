"""Round 3 Neo4j read-only schema introspection."""

from __future__ import annotations

import argparse
import json
import os
import re
from datetime import datetime
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_RUN_DIR = REPO_ROOT / "outputs" / "round3_orchestration" / "20260525_132801"
ENV_FILES = (REPO_ROOT / ".env", REPO_ROOT.parent / ".env")
EXPECTED_LABELS = ("DatasetCase", "EvidenceText", "Company", "Metric", "Year", "Value", "Observation")
FORBIDDEN_CYPHER = (
    r"\bCREATE\b",
    r"\bMERGE\b",
    r"\bSET\b",
    r"\bDELETE\b",
    r"\bREMOVE\b",
    r"\bDROP\b",
    r"\bLOAD\s+CSV\b",
    r"\bCALL\s+dbms\b",
    r"\bCALL\s+apoc\.periodic\b",
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


def read_env_files() -> dict[str, str]:
    values: dict[str, str] = {}
    for path in ENV_FILES:
        if not path.exists():
            continue
        for raw in path.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            key = key.strip()
            if key.startswith("NEO4J_"):
                values[key] = value.strip().strip("\"'")
    return values


def effective_env() -> dict[str, str]:
    file_values = read_env_files()
    values = {
        "NEO4J_URI": os.environ.get("NEO4J_URI") or file_values.get("NEO4J_URI", ""),
        "NEO4J_USERNAME": os.environ.get("NEO4J_USERNAME") or file_values.get("NEO4J_USERNAME", ""),
        "NEO4J_PASSWORD": os.environ.get("NEO4J_PASSWORD") or file_values.get("NEO4J_PASSWORD", ""),
        "NEO4J_DATABASE": os.environ.get("NEO4J_DATABASE") or file_values.get("NEO4J_DATABASE", ""),
    }
    if not values["NEO4J_USERNAME"]:
        values["NEO4J_USERNAME"] = os.environ.get("NEO4J_USER") or file_values.get("NEO4J_USER", "")
    return values


def guard_readonly(query: str) -> None:
    for pattern in FORBIDDEN_CYPHER:
        if re.search(pattern, query, flags=re.IGNORECASE):
            raise ValueError(f"Unsafe Cypher rejected by read-only guard: {pattern}")
    first = query.strip().split(None, 1)[0].upper() if query.strip() else ""
    if first not in {"SHOW", "MATCH"}:
        raise ValueError("Only SHOW and MATCH read queries are allowed.")


def run_query(session: Any, query: str) -> list[dict[str, Any]]:
    guard_readonly(query)
    return [dict(row) for row in session.run(query)]


def run_query_params(session: Any, query: str, params: dict[str, Any]) -> list[dict[str, Any]]:
    guard_readonly(query)
    return [dict(row) for row in session.run(query, params)]


def safe_database_name(row: dict[str, Any]) -> str:
    for key in ("name", "database", "databaseName"):
        if row.get(key):
            return str(row[key])
    return ""


def sanitize_property_value(value: Any) -> Any:
    if isinstance(value, str):
        if len(value) > 240:
            return value[:240] + "...[truncated]"
        return value
    if isinstance(value, (int, float, bool)) or value is None:
        return value
    if isinstance(value, list):
        return [sanitize_property_value(item) for item in value[:20]]
    if isinstance(value, dict):
        return sanitize_properties(value)
    return str(value)[:240]


def sanitize_properties(props: dict[str, Any]) -> dict[str, Any]:
    secret_terms = ("password", "secret", "token", "apikey", "api_key", "credential", "auth")
    sanitized: dict[str, Any] = {}
    for key, value in props.items():
        key_text = str(key)
        if any(term in key_text.lower() for term in secret_terms):
            sanitized[key_text] = "[redacted]"
        else:
            sanitized[key_text] = sanitize_property_value(value)
    return sanitized


def infer_issue(node_count: int | None, label_counts: list[dict[str, Any]], database_used: str) -> str:
    if node_count == 0:
        return "empty_database"
    labels = {str(row.get("label")) for row in label_counts}
    if any(label in labels for label in EXPECTED_LABELS):
        return "unknown"
    if database_used and node_count and labels:
        return "label_mapping_mismatch"
    if database_used and node_count is None:
        return "wrong_database"
    return "unknown"


def recommended_fix(issue: str) -> str:
    if issue == "empty_database":
        return "Point NEO4J_DATABASE to the populated KG database, or load the KG in a separately approved step."
    if issue == "label_mapping_mismatch":
        return "Map the actual Neo4j labels/properties to the Round 3 coverage checker before rerunning read-only coverage."
    if issue == "wrong_database":
        return "Verify NEO4J_DATABASE against SHOW DATABASES and rerun read-only introspection on the populated database."
    if issue == "expected_schema_not_loaded":
        return "Expected Round 3 labels are absent; either load the expected ontology in a separately approved step or map coverage to the actual ontology."
    if issue == "populated_but_different_ontology":
        return "Update the read-only coverage checker to use the proposed actual label/property mapping before rerunning coverage."
    return "Review the schema introspection report and decide whether the coverage checker needs database or label mapping updates."


def keyword_score(text: str, keywords: tuple[str, ...]) -> int:
    lowered = text.lower()
    return sum(1 for keyword in keywords if keyword in lowered)


def propose_mapping(
    label_counts: list[dict[str, Any]],
    relationship_counts: list[dict[str, Any]],
    sample_labels_properties: list[dict[str, Any]],
    sample_nodes: dict[str, list[dict[str, Any]]],
) -> list[dict[str, Any]]:
    labels = [str(row.get("label", "")) for row in label_counts]
    rels = [str(row.get("rel_type", "")) for row in relationship_counts]
    property_by_label: dict[str, set[str]] = {label: set() for label in labels}
    for row in sample_labels_properties:
        for label in row.get("labels", []) or []:
            property_by_label.setdefault(str(label), set()).update(str(p) for p in row.get("properties", []) or [])
    for label, rows in sample_nodes.items():
        for row in rows:
            property_by_label.setdefault(label, set()).update(str(p) for p in (row.get("properties") or {}).keys())

    specs = {
        "DatasetCase": {
            "label_keywords": ("case", "question", "dataset", "item", "benchmark", "eval"),
            "property_keywords": ("case_id", "question", "expected_answer", "split", "dataset", "source_dataset"),
            "relationship_keywords": ("case", "question", "dataset"),
        },
        "EvidenceText": {
            "label_keywords": ("chunk", "document", "evidence", "text", "source", "passage", "doc"),
            "property_keywords": ("evidence_text", "text", "content", "quote", "chunk", "document", "source"),
            "relationship_keywords": ("evidence", "source", "chunk", "document", "mentions"),
        },
        "Company": {
            "label_keywords": ("company", "entity", "organization", "organisation", "issuer", "org"),
            "property_keywords": ("company", "ticker", "cik", "issuer", "entity", "organization", "name"),
            "relationship_keywords": ("company", "entity", "issuer", "mentions"),
        },
        "Metric": {
            "label_keywords": ("metric", "attribute", "financial", "measure", "indicator"),
            "property_keywords": ("metric", "metric_canonical", "metric_raw", "attribute", "measure", "indicator"),
            "relationship_keywords": ("metric", "measure", "attribute"),
        },
        "Year": {
            "label_keywords": ("year", "period", "date", "time"),
            "property_keywords": ("year", "period", "fiscal", "date", "fy"),
            "relationship_keywords": ("year", "period", "date", "time"),
        },
        "Value": {
            "label_keywords": ("value", "amount", "number", "numeric"),
            "property_keywords": ("value", "amount", "number", "numeric", "unit"),
            "relationship_keywords": ("value", "amount"),
        },
        "Observation": {
            "label_keywords": ("observation", "fact", "statement", "record", "triple", "financial"),
            "property_keywords": ("fact_id", "metric", "year", "value", "unit", "source_fact"),
            "relationship_keywords": ("observ", "fact", "reported", "has", "mentions"),
        },
    }
    proposals: list[dict[str, Any]] = []
    for concept, spec in specs.items():
        label_scores: list[tuple[int, str]] = []
        prop_hits: set[str] = set()
        for label in labels:
            props = property_by_label.get(label, set())
            text = " ".join([label, *props])
            score = keyword_score(label, spec["label_keywords"]) * 3 + keyword_score(text, spec["property_keywords"])
            if score:
                label_scores.append((score, label))
                prop_hits.update(p for p in props if keyword_score(p, spec["property_keywords"]))
        rel_scores = [
            (keyword_score(rel, spec["relationship_keywords"]), rel)
            for rel in rels
            if keyword_score(rel, spec["relationship_keywords"])
        ]
        label_scores.sort(reverse=True)
        rel_scores.sort(reverse=True)
        candidates = [label for _, label in label_scores[:5]]
        relationships = [rel for _, rel in rel_scores[:8]]
        confidence = "none"
        if candidates:
            best = label_scores[0][0]
            confidence = "high" if best >= 5 else "medium" if best >= 3 else "low"
        elif relationships or prop_hits:
            confidence = "low"
        notes = "No obvious label/property match found."
        if candidates:
            notes = "Candidate labels inferred from label and property keyword overlap."
        elif prop_hits:
            notes = "Expected concept may be represented as properties rather than a dedicated label."
        proposals.append(
            {
                "expected_concept": concept,
                "candidate_actual_labels": candidates,
                "candidate_properties": sorted(prop_hits)[:15],
                "candidate_relationships": relationships,
                "confidence": confidence,
                "notes": notes,
            }
        )
    return proposals


def classify_schema_issue(
    *,
    node_count: int | None,
    label_counts: list[dict[str, Any]],
    expected_found: dict[str, bool],
    mapping: list[dict[str, Any]],
    populated_databases: list[str],
    database_used: str,
) -> str:
    if node_count == 0:
        return "empty_database"
    if node_count is None:
        return "unknown"
    if any(expected_found.values()):
        return "label_mapping_mismatch"
    if database_used not in populated_databases and populated_databases:
        return "wrong_database"
    if label_counts and any(item.get("confidence") in {"high", "medium"} for item in mapping):
        return "populated_but_different_ontology"
    if label_counts:
        return "expected_schema_not_loaded"
    return "unknown"


def render_report(data: dict[str, Any]) -> str:
    top_labels = data.get("label_counts", [])[:15]
    top_rels = data.get("relationship_type_counts", [])[:15]
    labels_md = "\n".join(f"- `{row.get('label')}`: {row.get('count')}" for row in top_labels) or "- none"
    rels_md = "\n".join(f"- `{row.get('rel_type')}`: {row.get('count')}" for row in top_rels) or "- none"
    expected_md = "\n".join(
        f"- `{label}`: {data['expected_labels_found'].get(label, False)}" for label in EXPECTED_LABELS
    )
    database_md = "\n".join(
        f"- `{row.get('name')}`: populated={row.get('appears_populated')}, current={row.get('current')}, default={row.get('default')}"
        for row in data.get("accessible_databases", [])
    ) or "- unavailable"
    mapping_md = "\n".join(
        f"- `{row['expected_concept']}` -> labels={row['candidate_actual_labels']}, props={row['candidate_properties']}, rels={row['candidate_relationships']}, confidence={row['confidence']}"
        for row in data.get("mapping_proposal", [])
    ) or "- none"
    return f"""# Neo4j Schema Introspection Report

Generated: {data['generated_at']}

## Connection

- uri connected: {data['uri_connected']}
- database used: `{data['database_used']}`
- secret values printed: false
- read-only only: true
- Neo4j write performed: false

## Counts

- total node count: {data.get('total_node_count')}
- total relationship count: {data.get('total_relationship_count')}

## Accessible Databases

{database_md}

## Top Labels

{labels_md}

## Top Relationship Types

{rels_md}

## Expected Labels Found

{expected_md}

## Sample Labels / Properties

```json
{json.dumps(data.get('sample_labels_properties', []), ensure_ascii=False, indent=2, sort_keys=True)}
```

## Sample Nodes From Top Labels

```json
{json.dumps(data.get('sample_nodes_by_top_label', {}), ensure_ascii=False, indent=2, sort_keys=True)}
```

## Label / Property Mapping Proposal

{mapping_md}

## Likely Issue

`{data['likely_issue']}`

## Recommended Fix

{data['recommended_fix']}
"""


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Read-only Neo4j schema introspection for Round 3.")
    parser.add_argument("--run-dir", default=None)
    args = parser.parse_args(argv)
    run_dir = Path(args.run_dir) if args.run_dir else DEFAULT_RUN_DIR
    if not run_dir.is_absolute():
        run_dir = REPO_ROOT / run_dir

    env = effective_env()
    missing = [key for key in ("NEO4J_URI", "NEO4J_USERNAME", "NEO4J_PASSWORD", "NEO4J_DATABASE") if not env.get(key)]
    data: dict[str, Any] = {
        "generated_at": now(),
        "uri_connected": False,
        "database_used": env.get("NEO4J_DATABASE", ""),
        "env_present": {key: bool(env.get(key)) for key in ("NEO4J_URI", "NEO4J_USERNAME", "NEO4J_PASSWORD", "NEO4J_DATABASE")},
        "missing_env_keys": missing,
        "database_rows": [],
        "accessible_databases": [],
        "total_node_count": None,
        "total_relationship_count": None,
        "label_counts": [],
        "relationship_type_counts": [],
        "sample_labels_properties": [],
        "sample_nodes_by_top_label": {},
        "mapping_proposal": [],
        "expected_labels_found": {label: False for label in EXPECTED_LABELS},
        "likely_issue": "unknown",
        "recommended_fix": "Resolve Neo4j config before rerunning read-only introspection.",
        "safety": {
            "neo4j_write_performed": False,
            "kg_patch_applied": False,
            "dry_run_executed": False,
            "full_eval_executed": False,
            "secrets_printed": False,
        },
    }

    if not missing:
        try:
            from neo4j import GraphDatabase

            driver = GraphDatabase.driver(env["NEO4J_URI"], auth=(env["NEO4J_USERNAME"], env["NEO4J_PASSWORD"]))
            try:
                with driver.session(database=env["NEO4J_DATABASE"]) as session:
                    data["database_rows"] = run_query(session, "SHOW DATABASES")
                    accessible: list[dict[str, Any]] = []
                    for db_row in data["database_rows"]:
                        db_name = safe_database_name(db_row)
                        if not db_name:
                            continue
                        db_record = {
                            "name": db_name,
                            "current": db_row.get("current"),
                            "default": db_row.get("default"),
                            "address": db_row.get("address"),
                            "role": db_row.get("role"),
                            "requestedStatus": db_row.get("requestedStatus"),
                            "currentStatus": db_row.get("currentStatus"),
                            "appears_populated": None,
                            "node_count": None,
                            "relationship_count": None,
                        }
                        try:
                            with driver.session(database=db_name) as db_session:
                                node_rows = run_query(db_session, "MATCH (n) RETURN count(n) AS node_count")
                                rel_rows = run_query(db_session, "MATCH ()-[r]->() RETURN count(r) AS relationship_count")
                                db_record["node_count"] = int(node_rows[0].get("node_count", 0) or 0)
                                db_record["relationship_count"] = int(rel_rows[0].get("relationship_count", 0) or 0)
                                db_record["appears_populated"] = bool(
                                    db_record["node_count"] or db_record["relationship_count"]
                                )
                        except Exception as exc:  # noqa: BLE001 - per-db access can vary.
                            db_record["access_error_type"] = type(exc).__name__
                            db_record["appears_populated"] = False
                        accessible.append(db_record)
                    data["accessible_databases"] = accessible
                    data["total_node_count"] = int(
                        run_query(session, "MATCH (n) RETURN count(n) AS node_count")[0]["node_count"]
                    )
                    data["label_counts"] = run_query(
                        session,
                        """
MATCH (n)
UNWIND labels(n) AS label
RETURN label, count(*) AS count
ORDER BY count DESC
""",
                    )
                    rel_counts = run_query(
                        session,
                        """
MATCH ()-[r]->()
RETURN type(r) AS rel_type, count(*) AS count
ORDER BY count DESC
""",
                    )
                    data["relationship_type_counts"] = rel_counts
                    data["total_relationship_count"] = sum(int(row.get("count", 0) or 0) for row in rel_counts)
                    data["sample_labels_properties"] = run_query(
                        session,
                        """
MATCH (n)
RETURN labels(n) AS labels, keys(n) AS properties
LIMIT 50
""",
                    )
                    sample_nodes: dict[str, list[dict[str, Any]]] = {}
                    for label_row in data["label_counts"][:10]:
                        label = str(label_row.get("label", ""))
                        if not label:
                            continue
                        rows = run_query_params(
                            session,
                            """
MATCH (n)
WHERE $label IN labels(n)
RETURN labels(n) AS labels, properties(n) AS properties
LIMIT 5
""",
                            {"label": label},
                        )
                        sample_nodes[label] = [
                            {
                                "labels": row.get("labels", []),
                                "properties": sanitize_properties(row.get("properties", {}) or {}),
                            }
                            for row in rows
                        ]
                    data["sample_nodes_by_top_label"] = sample_nodes
                    present_labels = {str(row.get("label")) for row in data["label_counts"]}
                    data["expected_labels_found"] = {label: label in present_labels for label in EXPECTED_LABELS}
                    data["uri_connected"] = True
                    data["mapping_proposal"] = propose_mapping(
                        data["label_counts"],
                        data["relationship_type_counts"],
                        data["sample_labels_properties"],
                        data["sample_nodes_by_top_label"],
                    )
                    populated_databases = [
                        row["name"] for row in accessible if row.get("appears_populated")
                    ]
                    data["likely_issue"] = classify_schema_issue(
                        node_count=data["total_node_count"],
                        label_counts=data["label_counts"],
                        expected_found=data["expected_labels_found"],
                        mapping=data["mapping_proposal"],
                        populated_databases=populated_databases,
                        database_used=data["database_used"],
                    )
                    data["recommended_fix"] = recommended_fix(data["likely_issue"])
            finally:
                driver.close()
        except Exception as exc:  # noqa: BLE001 - report safely without secrets.
            data["uri_connected"] = False
            data["connection_error_type"] = type(exc).__name__
            data["likely_issue"] = "wrong_instance"
            data["recommended_fix"] = (
                "Start the intended Neo4j instance or update .env to the reachable populated KG database, "
                "then rerun read-only schema introspection."
            )

    json_path = run_dir / "neo4j_schema_introspection.json"
    report_path = run_dir / "neo4j_schema_introspection_report.md"
    write_text(json_path, json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True, default=str))
    write_text(report_path, render_report(data))
    proposal = {
        "generated_at": data["generated_at"],
        "database_used": data["database_used"],
        "likely_issue": data["likely_issue"],
        "mapping_proposal": data.get("mapping_proposal", []),
        "coverage_script_update_needed": data["likely_issue"]
        in {"label_mapping_mismatch", "expected_schema_not_loaded", "populated_but_different_ontology"},
        "safety": data["safety"],
    }
    write_text(
        run_dir / "neo4j_label_mapping_proposal.json",
        json.dumps(proposal, ensure_ascii=False, indent=2, sort_keys=True, default=str),
    )
    mapping_rows = [
        "# Neo4j Label Mapping Proposal",
        "",
        f"- database used: `{proposal['database_used']}`",
        f"- likely issue: `{proposal['likely_issue']}`",
        f"- coverage script update needed: {proposal['coverage_script_update_needed']}",
        "",
        "| Expected Concept | Candidate Labels | Candidate Properties | Candidate Relationships | Confidence | Notes |",
        "|---|---|---|---|---|---|",
    ]
    for row in proposal["mapping_proposal"]:
        mapping_rows.append(
            "| `{expected_concept}` | `{labels}` | `{props}` | `{rels}` | {confidence} | {notes} |".format(
                expected_concept=row["expected_concept"],
                labels=", ".join(row["candidate_actual_labels"]) or "none",
                props=", ".join(row["candidate_properties"]) or "none",
                rels=", ".join(row["candidate_relationships"]) or "none",
                confidence=row["confidence"],
                notes=str(row["notes"]).replace("|", "\\|"),
            )
        )
    write_text(run_dir / "neo4j_label_mapping_proposal.md", "\n".join(mapping_rows))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
