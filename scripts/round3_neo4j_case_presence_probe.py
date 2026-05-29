"""Round 3 Neo4j read-only case/fact presence probe.

This probe checks whether the configured Neo4j database plausibly contains the
Round 3 FinDER repaired subset. It does not score coverage and does not write.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import re
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_RUN_DIR = REPO_ROOT / "outputs" / "round3_orchestration" / "20260525_132801"
REPAIRED_DIR = REPO_ROOT / "outputs" / "round3_case_factory_repaired"
CASE_PATH = REPAIRED_DIR / "eval_ready_cases.jsonl"
FACT_PATH = REPAIRED_DIR / "eval_ready_required_facts.jsonl"
ENV_FILES = (REPO_ROOT / ".env", REPO_ROOT.parent / ".env")
CASE_KEYS = (
    "case_id",
    "id",
    "source_case_id",
    "dataset_case_id",
    "finder_case_id",
    "source_id",
    "name",
    "text",
    "content",
    "source_dataset",
    "split",
)
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


def write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text.rstrip() + "\n", encoding="utf-8", newline="\n")


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


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
            raise ValueError(f"Unsafe Cypher rejected: {pattern}")
    first = query.strip().split(None, 1)[0].upper() if query.strip() else ""
    if first not in {"MATCH", "OPTIONAL", "SHOW", "UNWIND"}:
        raise ValueError("Only read-only MATCH/OPTIONAL/SHOW/UNWIND queries are allowed.")


def run_query(session: Any, query: str, params: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    guard_readonly(query)
    return [dict(row) for row in session.run(query, params or {})]


def normalize_text(value: Any) -> str:
    return re.sub(r"[^a-z0-9.]+", " ", str(value or "").lower()).strip()


def token_variants(value: Any) -> list[str]:
    text = normalize_text(value)
    parts = [p for p in text.split() if len(p) >= 2]
    variants = {text}
    variants.update(parts)
    if "_" in str(value):
        variants.add(normalize_text(str(value).replace("_", " ")))
    return [v for v in variants if v]


def numeric_variants(value: Any) -> list[str]:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return []
    variants = {f"{number:g}", f"{abs(number):g}"}
    if abs(number).is_integer():
        variants.add(str(int(abs(number))))
        variants.add(f"{int(abs(number)):,}")
    if math.isfinite(number):
        variants.add(f"{number:.1f}")
    return sorted(v for v in variants if v)


def case_probe(session: Any, case: dict[str, Any]) -> dict[str, Any]:
    case_id = str(case.get("case_id", ""))
    params = {"case_id": case_id, "keys": list(CASE_KEYS)}
    kg_rows = run_query(
        session,
        """
MATCH (n:KGEntity)
WHERE any(k IN $keys WHERE k IN keys(n) AND toString(n[k]) CONTAINS $case_id)
RETURN labels(n) AS labels, keys(n) AS properties, properties(n) AS sample
LIMIT 3
""",
        params,
    )
    all_rows = kg_rows
    if not kg_rows:
        all_rows = run_query(
            session,
            """
MATCH (n)
WHERE any(k IN $keys WHERE k IN keys(n) AND toString(n[k]) CONTAINS $case_id)
RETURN labels(n) AS labels, keys(n) AS properties, properties(n) AS sample
LIMIT 3
""",
            params,
        )
    return {
        "case_id": case_id,
        "split": case.get("split"),
        "matched": bool(all_rows),
        "match_scope": "KGEntity" if kg_rows else "all_nodes" if all_rows else "none",
        "sample": [sanitize_sample(row) for row in all_rows],
    }


def fetch_candidate_nodes(session: Any) -> list[dict[str, Any]]:
    rows = run_query(
        session,
        """
MATCH (n:KGEntity)
WHERE n.source_dataset IS NOT NULL
   OR n.case_id IS NOT NULL
   OR n.source_case_id IS NOT NULL
   OR n.dataset_case_id IS NOT NULL
   OR n.finder_case_id IS NOT NULL
   OR n.normalized_metric_key IS NOT NULL
   OR n.normalized_metric IS NOT NULL
   OR n.metric IS NOT NULL
   OR n.metric_canonical IS NOT NULL
   OR n.metric_raw IS NOT NULL
   OR n.numeric_value IS NOT NULL
   OR n.value IS NOT NULL
   OR n.year IS NOT NULL
   OR n.unit IS NOT NULL
   OR n.ticker IS NOT NULL
   OR n.cik IS NOT NULL
RETURN labels(n) AS labels, keys(n) AS properties, properties(n) AS sample
LIMIT 50000
""",
    )
    return [sanitize_sample(row) for row in rows]


def sample_to_text(sample: dict[str, Any]) -> str:
    props = sample.get("sample", {}) or {}
    return normalize_text(" ".join(str(v) for v in props.values()))


def batch_case_probe(cases: list[dict[str, Any]], candidate_nodes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    case_ids = [str(case.get("case_id", "")) for case in cases]
    samples_by_case: dict[str, list[dict[str, Any]]] = {case_id: [] for case_id in case_ids}
    for node in candidate_nodes:
        text = sample_to_text(node)
        for case_id in case_ids:
            if case_id.lower() in text and len(samples_by_case[case_id]) < 2:
                samples_by_case[case_id].append(node)
    results: list[dict[str, Any]] = []
    for case in cases:
        case_id = str(case.get("case_id", ""))
        samples = samples_by_case.get(case_id, [])
        results.append(
            {
                "case_id": case_id,
                "split": case.get("split"),
                "matched": bool(samples),
                "match_scope": "KGEntity_candidate_nodes" if samples else "none",
                "match_count": len(samples),
                "sample": samples,
            }
        )
    return results


def sanitize_sample(row: dict[str, Any]) -> dict[str, Any]:
    sample = row.get("sample", {}) or {}
    secret_terms = ("password", "secret", "token", "apikey", "api_key", "credential", "auth")
    clean: dict[str, Any] = {}
    for key, value in sample.items():
        key_text = str(key)
        if any(term in key_text.lower() for term in secret_terms):
            clean[key_text] = "[redacted]"
        elif isinstance(value, str) and len(value) > 180:
            clean[key_text] = value[:180] + "...[truncated]"
        else:
            clean[key_text] = value
        if len(clean) >= 12:
            break
    return {"labels": row.get("labels", []), "properties": row.get("properties", []), "sample": clean}


def fact_terms(fact: dict[str, Any], case: dict[str, Any]) -> dict[str, list[str]]:
    company_terms = token_variants(fact.get("ticker") or case.get("ticker"))
    company_terms += token_variants(fact.get("company") or case.get("company"))
    metric_terms = token_variants(fact.get("metric_canonical")) + token_variants(fact.get("metric_raw"))
    year_terms = token_variants(fact.get("year"))
    value_terms = numeric_variants(fact.get("value"))
    unit_terms = token_variants(fact.get("unit"))
    return {
        "company": sorted(set(company_terms))[:8],
        "metric": sorted(set(metric_terms))[:10],
        "year": sorted(set(year_terms))[:4],
        "value": sorted(set(value_terms))[:8],
        "unit": sorted(set(unit_terms))[:5],
    }


def build_component_presence(
    candidate_nodes: list[dict[str, Any]],
    component_terms: dict[str, list[str]],
) -> dict[str, dict[str, int]]:
    result: dict[str, dict[str, int]] = {}
    node_texts = [sample_to_text(node) for node in candidate_nodes]
    for component, terms in component_terms.items():
        clean_terms = sorted({normalize_text(term) for term in terms if normalize_text(term)})
        result[component] = {term: 0 for term in clean_terms}
        for term in clean_terms:
            result[component][term] = sum(1 for text in node_texts if term and term in text)
    return result


def build_term_presence(session: Any, terms: list[str]) -> dict[str, int]:
    clean_terms = sorted({normalize_text(term) for term in terms if normalize_text(term)})
    if not clean_terms:
        return {}
    presence = {term: 0 for term in clean_terms}
    # Scan the candidate label once. The term list is intentionally capped by
    # the small Round 3 subset, and this remains read-only.
    rows = run_query(
        session,
        """
MATCH (n:KGEntity)
WITH n, [term IN $terms WHERE any(k IN keys(n) WHERE toLower(toString(n[k])) CONTAINS term)] AS hits
WHERE size(hits) > 0
UNWIND hits AS term
RETURN term, count(n) AS hit_count
""",
        {"terms": clean_terms},
    )
    for row in rows:
        presence[str(row.get("term"))] = int(row.get("hit_count", 0) or 0)
    return presence


def relationship_types_present(session: Any) -> set[str]:
    rows = run_query(
        session,
        """
MATCH ()-[r]->()
WHERE type(r) IN ["HAS_OBSERVATION", "OBSERVES_METRIC", "OBSERVED_IN_YEAR"]
RETURN type(r) AS rel_type, count(r) AS count
""",
    )
    return {str(row.get("rel_type")) for row in rows if int(row.get("count", 0) or 0) > 0}


def terms_have_hits(terms: list[str], presence: dict[str, int]) -> int:
    return sum(presence.get(normalize_text(term), 0) for term in terms if normalize_text(term))


def fact_probe(
    fact: dict[str, Any],
    case: dict[str, Any],
    component_presence: dict[str, dict[str, int]],
    present_rel_types: set[str],
) -> dict[str, Any]:
    terms = fact_terms(fact, case)
    component_hits = {
        "company": terms_have_hits(terms["company"], component_presence.get("company", {})),
        "metric": terms_have_hits(terms["metric"], component_presence.get("metric", {})),
        "year": terms_have_hits(terms["year"], component_presence.get("year", {})),
        "value": terms_have_hits(terms["value"], component_presence.get("value", {})),
        "unit": terms_have_hits(terms["unit"], component_presence.get("unit", {})),
        "observation_relationship": len(present_rel_types),
    }
    soft_matched_components = [key for key, value in component_hits.items() if value > 0]
    soft_match = (
        component_hits["company"] > 0
        and component_hits["metric"] > 0
        and (component_hits["year"] > 0 or component_hits["observation_relationship"] > 0)
        and (component_hits["value"] > 0 or component_hits["observation_relationship"] > 0)
    )
    return {
        "case_id": fact.get("case_id"),
        "fact_id": fact.get("fact_id"),
        "metric_canonical": fact.get("metric_canonical"),
        "year": fact.get("year"),
        "value": fact.get("value"),
        "unit": fact.get("unit"),
        "soft_match": soft_match,
        "soft_matched_components": soft_matched_components,
        "component_hits": component_hits,
    }


def likely_status(case_match_rate: float, fact_match_rate: float, node_count: int | None) -> str:
    if node_count == 0:
        return "wrong_database"
    if case_match_rate >= 0.8 and fact_match_rate >= 0.5:
        return "correct_database_but_mapping_needed"
    if case_match_rate >= 0.2 or fact_match_rate >= 0.2:
        return "mixed_database"
    if case_match_rate == 0 and fact_match_rate == 0:
        return "wrong_database"
    return "unknown"


def render_report(data: dict[str, Any]) -> str:
    return f"""# Neo4j Case Presence Probe

Generated: {data['generated_at']}

## Summary

- database used: `{data['database_used']}`
- node count: {data.get('node_count')}
- relationship count: {data.get('relationship_count')}
- total cases probed: {data['total_cases_probed']}
- case id matches: {data['case_id_matches']}
- case id match rate: {data['case_id_match_rate']:.4f}
- required facts probed: {data['required_facts_probed']}
- required fact soft matches: {data['required_fact_soft_matches']}
- required fact soft match rate: {data['required_fact_soft_match_rate']:.4f}
- likely database status: `{data['likely_database_status']}`

## Sample Matched Cases

```json
{json.dumps(data['sample_matched_cases'], ensure_ascii=False, indent=2, sort_keys=True)}
```

## Sample Unmatched Cases

```json
{json.dumps(data['sample_unmatched_cases'], ensure_ascii=False, indent=2, sort_keys=True)}
```

## Sample Matched Fact Evidence

```json
{json.dumps(data['sample_matched_fact_evidence'], ensure_ascii=False, indent=2, sort_keys=True)}
```

## Safety

- Neo4j write performed: false
- KG patch applied: false
- dry-run executed: false
- full eval executed: false
- secrets printed: false
"""


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Probe Round 3 case/fact presence in Neo4j.")
    parser.add_argument("--run-dir", default=None)
    args = parser.parse_args(argv)
    run_dir = Path(args.run_dir) if args.run_dir else DEFAULT_RUN_DIR
    if not run_dir.is_absolute():
        run_dir = REPO_ROOT / run_dir

    cases = load_jsonl(CASE_PATH)
    facts = load_jsonl(FACT_PATH)
    cases_by_id = {str(case.get("case_id")): case for case in cases}
    env = effective_env()

    from neo4j import GraphDatabase

    driver = GraphDatabase.driver(env["NEO4J_URI"], auth=(env["NEO4J_USERNAME"], env["NEO4J_PASSWORD"]))
    try:
        with driver.session(database=env["NEO4J_DATABASE"]) as session:
            node_count = int(run_query(session, "MATCH (n) RETURN count(n) AS node_count")[0]["node_count"])
            rel_count = int(
                run_query(session, "MATCH ()-[r]->() RETURN count(r) AS relationship_count")[0][
                    "relationship_count"
                ]
            )
            candidate_nodes = fetch_candidate_nodes(session)
            case_results = batch_case_probe(cases, candidate_nodes)
            component_terms: dict[str, list[str]] = {
                "company": [],
                "metric": [],
                "year": [],
                "value": [],
                "unit": [],
            }
            for fact in facts:
                terms = fact_terms(fact, cases_by_id.get(str(fact.get("case_id")), {}))
                for component, values in terms.items():
                    component_terms[component].extend(values)
            component_presence = build_component_presence(candidate_nodes, component_terms)
            present_rel_types = relationship_types_present(session)
            fact_results = [
                fact_probe(
                    fact,
                    cases_by_id.get(str(fact.get("case_id")), {}),
                    component_presence,
                    present_rel_types,
                )
                for fact in facts
            ]
    finally:
        driver.close()

    matched_cases = [row for row in case_results if row["matched"]]
    matched_facts = [row for row in fact_results if row["soft_match"]]
    case_rate = len(matched_cases) / max(1, len(case_results))
    fact_rate = len(matched_facts) / max(1, len(fact_results))
    data = {
        "generated_at": now(),
        "database_used": env["NEO4J_DATABASE"],
        "node_count": node_count,
        "relationship_count": rel_count,
        "total_cases_probed": len(case_results),
        "case_id_matches": len(matched_cases),
        "case_id_match_rate": case_rate,
        "required_facts_probed": len(fact_results),
        "required_fact_soft_matches": len(matched_facts),
        "required_fact_soft_match_rate": fact_rate,
        "sample_matched_cases": matched_cases[:5],
        "sample_unmatched_cases": [row["case_id"] for row in case_results if not row["matched"]][:10],
        "sample_matched_fact_evidence": matched_facts[:5],
        "case_results": case_results,
        "fact_results": fact_results,
        "likely_database_status": likely_status(case_rate, fact_rate, node_count),
        "recommended_next_action": "",
        "safety": {
            "neo4j_write_performed": False,
            "kg_patch_applied": False,
            "dry_run_executed": False,
            "full_eval_executed": False,
            "round02_modified": False,
            "repaired_subset_modified": False,
            "secrets_printed": False,
        },
    }
    if data["likely_database_status"] == "correct_database_but_mapping_needed":
        data["recommended_next_action"] = "Implement a read-only coverage adapter using the observed KGEntity ontology."
    elif data["likely_database_status"] == "mixed_database":
        data["recommended_next_action"] = "Inspect unmatched cases/facts and confirm whether this database contains the full Round 3 repaired subset."
    elif data["likely_database_status"] == "wrong_database":
        data["recommended_next_action"] = "Point NEO4J_DATABASE/NEO4J_URI to the FinDER/Seocho KG that contains the repaired Round 3 case IDs."
    else:
        data["recommended_next_action"] = "Review probe samples and decide whether to update mapping or switch databases."

    write_text(
        run_dir / "neo4j_case_presence_probe.json",
        json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True, default=str),
    )
    write_text(run_dir / "neo4j_case_presence_probe.md", render_report(data))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
