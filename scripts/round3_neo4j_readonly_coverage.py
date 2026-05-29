"""Round 3 Neo4j read-only coverage check.

This script writes coverage reports under a Round 3 orchestration run
directory. It never runs evaluation, never applies KG patches, and rejects
write-capable Cypher before execution.
"""

from __future__ import annotations

import argparse
import csv
import json
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

REQUIRED_ENV = ("NEO4J_URI", "NEO4J_USERNAME", "NEO4J_PASSWORD", "NEO4J_DATABASE")
ENV_FILES = (REPO_ROOT / ".env", REPO_ROOT.parent / ".env")
REQUIRED_LABELS = ("DatasetCase", "EvidenceText", "Company", "Metric", "Year", "Value", "Observation")
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


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    if not path.exists():
        return rows
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def run_dir_from_arg(value: str | None) -> Path:
    if value:
        path = Path(value)
        return path if path.is_absolute() else REPO_ROOT / path
    return DEFAULT_RUN_DIR


def create_approval_marker(run_dir: Path) -> Path:
    path = run_dir / "approvals" / "allow_neo4j_readonly_coverage.txt"
    write_text(
        path,
        f"""approved_by_user: true
scope: neo4j_readonly_coverage_only
neo4j_write_allowed: false
kg_patch_allowed: false
dry_run_allowed: false
full_eval_allowed: false
timestamp: {now()}
""",
    )
    return path


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
    values: dict[str, str] = {}
    for key in REQUIRED_ENV:
        values[key] = os.environ.get(key) or file_values.get(key, "")
    if not values["NEO4J_USERNAME"]:
        values["NEO4J_USERNAME"] = os.environ.get("NEO4J_USER") or file_values.get("NEO4J_USER", "")
    return values


def env_presence(values: dict[str, str]) -> dict[str, bool]:
    return {key: bool(values.get(key)) for key in REQUIRED_ENV}


def guard_readonly_cypher(cypher: str) -> None:
    for pattern in FORBIDDEN_CYPHER:
        if re.search(pattern, cypher, flags=re.IGNORECASE):
            raise ValueError(f"Unsafe Cypher rejected by read-only guard: {pattern}")
    first = cypher.strip().split(None, 1)[0].upper() if cypher.strip() else ""
    if first not in {"MATCH", "OPTIONAL"}:
        raise ValueError("Only MATCH / OPTIONAL MATCH / RETURN-style read queries are allowed.")


def safe_run(session: Any, cypher: str, params: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    guard_readonly_cypher(cypher)
    return [dict(row) for row in session.run(cypher, params or {})]


def normalize_token(value: Any) -> str:
    text = str(value or "").lower()
    return re.sub(r"[^a-z0-9.%-]+", "_", text).strip("_")


def numeric_tokens(value: Any) -> set[str]:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return set()
    abs_number = abs(number)
    tokens = {
        str(int(abs_number)) if abs_number.is_integer() else f"{abs_number:g}",
        f"{abs_number:g}",
    }
    if abs_number >= 1000 and abs_number.is_integer():
        tokens.add(f"{int(abs_number):,}")
    return {token for token in tokens if token}


def case_key(case: dict[str, Any], facts: list[dict[str, Any]] | None = None) -> str:
    for fact in facts or []:
        source = str(fact.get("source_evidence_id") or "").strip()
        if source:
            return source
    case_id = str(case.get("case_id") or "")
    tail = case_id.rsplit("_", 1)[-1]
    return tail if tail else case_id


def fact_params(fact: dict[str, Any], case: dict[str, Any], facts: list[dict[str, Any]]) -> dict[str, Any]:
    metric = normalize_token(fact.get("metric_canonical") or fact.get("metric_raw"))
    metric_raw = normalize_token(fact.get("metric_raw"))
    value_candidates = sorted(numeric_tokens(fact.get("value")))
    try:
        value_num = float(fact.get("value"))
    except (TypeError, ValueError):
        value_num = None
    return {
        "case_key": case_key(case, facts),
        "ticker": normalize_token(fact.get("ticker")),
        "company": normalize_token(fact.get("company")),
        "metric": metric,
        "metric_raw": metric_raw,
        "year": str(fact.get("year") or ""),
        "value_candidates": value_candidates,
        "value_num": value_num,
        "abs_value_num": abs(value_num) if value_num is not None else None,
        "tolerance": max(0.01, abs(value_num) * 0.0001) if value_num is not None else 0.01,
        "unit": normalize_token(fact.get("unit")),
    }


def coverage_query() -> str:
    return """
MATCH (o:KGEntity)
WHERE o.label = "Observation"
  AND toString(o.case_id) = $case_key
WITH o,
     toLower(
       coalesce(toString(o.metric), "") + " " +
       coalesce(toString(o.normalized_metric), "") + " " +
       coalesce(toString(o.normalized_metric_key), "") + " " +
       coalesce(toString(o.metric_family), "")
     ) AS metric_text,
     toString(o.year) AS year_text,
     toLower(coalesce(toString(o.unit), "")) AS unit_text,
     toString(o.value) AS value_text,
     CASE
       WHEN o.numeric_value IS NULL THEN null
       ELSE abs(toFloat(o.numeric_value))
     END AS numeric_value
WHERE ($metric = "" OR metric_text CONTAINS $metric OR metric_text CONTAINS $metric_raw OR metric_text CONTAINS replace($metric, "_", " "))
  AND ($year = "" OR year_text = $year OR year_text CONTAINS $year)
  AND ($unit = "" OR unit_text CONTAINS $unit OR unit_text CONTAINS replace($unit, "_", " "))
  AND (
    $value_num IS NULL
    OR (numeric_value IS NOT NULL AND abs(numeric_value - $abs_value_num) <= $tolerance)
    OR any(v IN $value_candidates WHERE value_text CONTAINS v)
  )
RETURN count(DISTINCT o) AS match_count
"""


def observations_query() -> str:
    return """
MATCH (o:KGEntity)
WHERE o.label = "Observation"
  AND toString(o.case_id) IN $case_keys
RETURN properties(o) AS props
"""


def normalize_text(value: Any) -> str:
    return re.sub(r"[^a-z0-9.%-]+", "_", str(value or "").lower()).strip("_")


def observation_matches(fact: dict[str, Any], case: dict[str, Any], facts: list[dict[str, Any]], obs: dict[str, Any]) -> bool:
    params = fact_params(fact, case, facts)
    metric_text = " ".join(
        normalize_text(obs.get(key))
        for key in ("metric", "normalized_metric", "normalized_metric_key", "metric_family")
    )
    unit_text = normalize_text(obs.get("unit"))
    year_text = str(obs.get("year") or "")
    value_text = str(obs.get("value") or "")
    metric_ok = (
        not params["metric"]
        or params["metric"] in metric_text
        or params["metric_raw"] in metric_text
        or params["metric"].replace("_", " ") in metric_text.replace("_", " ")
    )
    year_ok = not params["year"] or params["year"] == year_text or params["year"] in year_text
    unit_ok = not params["unit"] or params["unit"] in unit_text or params["unit"].replace("_", " ") in unit_text.replace("_", " ")
    value_ok = params["value_num"] is None
    if params["value_num"] is not None:
        try:
            obs_value = abs(float(obs.get("numeric_value")))
            value_ok = abs(obs_value - params["abs_value_num"]) <= params["tolerance"]
        except (TypeError, ValueError):
            value_ok = False
        if not value_ok:
            value_ok = any(token in value_text for token in params["value_candidates"])
    return metric_ok and year_ok and unit_ok and value_ok


def label_count_query(label: str) -> str:
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", label):
        raise ValueError(f"Invalid label: {label}")
    return f"MATCH (n:{label}) RETURN count(n) AS count"


def concept_count_query() -> str:
    return """
MATCH (n:KGEntity)
WHERE n.label = $label
RETURN count(n) AS count
"""


def case_results_without_config(cases: list[dict[str, Any]], facts_by_case: dict[str, list[dict[str, Any]]]) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    for case in cases:
        case_id = str(case.get("case_id", ""))
        facts = facts_by_case.get(case_id, [])
        results.append(
            {
                "case_id": case_id,
                "split": case.get("split", ""),
                "required_fact_count": len(facts),
                "matched_fact_count": 0,
                "missing_fact_count": len(facts),
                "coverage_status": "not_checked_no_neo4j_config",
                "missing_facts": [fact.get("fact_id") for fact in facts],
                "notes": "Neo4j env config is incomplete; no connection was attempted.",
            }
        )
    return results


def classify_case(facts: list[dict[str, Any]], per_fact: list[dict[str, Any]]) -> str:
    if not facts:
        return "needs_human_review"
    if all(row["matched"] for row in per_fact):
        return "ready_for_eval"
    missing = [row for row in per_fact if not row["matched"]]
    if any(row.get("company_ticker_sensitive") for row in missing):
        return "needs_company_ticker_review"
    if any(row.get("metric_sensitive") for row in missing):
        return "needs_metric_normalization"
    if any(row.get("value_sensitive") for row in missing):
        return "needs_value_patch"
    return "needs_human_review"


def execute_coverage(
    cases: list[dict[str, Any]],
    facts_by_case: dict[str, list[dict[str, Any]]],
    env_values: dict[str, str],
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    try:
        from neo4j import GraphDatabase
    except ImportError as exc:
        raise RuntimeError("neo4j Python package is not installed") from exc

    driver = GraphDatabase.driver(
        env_values["NEO4J_URI"],
        auth=(env_values["NEO4J_USERNAME"], env_values["NEO4J_PASSWORD"]),
    )
    query = coverage_query()
    results: list[dict[str, Any]] = []
    label_counts: dict[str, int] = {}
    try:
        with driver.session(database=env_values["NEO4J_DATABASE"]) as session:
            for label in REQUIRED_LABELS:
                rows = safe_run(session, concept_count_query(), {"label": label})
                label_counts[label] = int(rows[0].get("count", 0)) if rows else 0
            case_keys_by_case = {
                str(case.get("case_id", "")): case_key(case, facts_by_case.get(str(case.get("case_id", "")), []))
                for case in cases
            }
            obs_rows = safe_run(session, observations_query(), {"case_keys": sorted(set(case_keys_by_case.values()))})
            observations_by_case_key: dict[str, list[dict[str, Any]]] = defaultdict(list)
            for row in obs_rows:
                props = row.get("props", {}) or {}
                observations_by_case_key[str(props.get("case_id", ""))].append(props)
            for case in cases:
                case_id = str(case.get("case_id", ""))
                facts = facts_by_case.get(case_id, [])
                observations = observations_by_case_key.get(case_keys_by_case.get(case_id, ""), [])
                per_fact: list[dict[str, Any]] = []
                for fact in facts:
                    match_count = sum(1 for obs in observations if observation_matches(fact, case, facts, obs))
                    per_fact.append(
                        {
                            "fact_id": fact.get("fact_id"),
                            "matched": match_count > 0,
                            "match_count": match_count,
                            "metric_sensitive": bool(fact.get("metric_canonical") or fact.get("metric_raw")),
                            "company_ticker_sensitive": bool(fact.get("ticker") or case.get("ticker")),
                            "value_sensitive": fact.get("value") is not None,
                        }
                    )
                missing = [row["fact_id"] for row in per_fact if not row["matched"]]
                status = classify_case(facts, per_fact)
                results.append(
                    {
                        "case_id": case_id,
                        "split": case.get("split", ""),
                        "required_fact_count": len(facts),
                        "matched_fact_count": len(facts) - len(missing),
                        "missing_fact_count": len(missing),
                        "coverage_status": status,
                        "missing_facts": missing,
                        "notes": json.dumps(
                            {
                                "case_key": case_keys_by_case.get(case_id, ""),
                                "observation_candidates": len(observations),
                                "per_fact": per_fact,
                            },
                            ensure_ascii=False,
                            sort_keys=True,
                        ),
                    }
                )
    finally:
        driver.close()
    return results, label_counts


def write_summary_csv(path: Path, results: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=(
                "case_id",
                "split",
                "required_fact_count",
                "matched_fact_count",
                "missing_fact_count",
                "coverage_status",
            ),
        )
        writer.writeheader()
        for row in results:
            writer.writerow({key: row.get(key, "") for key in writer.fieldnames})


def render_report(
    *,
    env: dict[str, bool],
    connection_attempted: bool,
    coverage_executed: bool,
    results: list[dict[str, Any]],
    label_counts: dict[str, int],
    error: str | None,
) -> str:
    status_counts: dict[str, int] = defaultdict(int)
    for row in results:
        status_counts[str(row.get("coverage_status"))] += 1
    total_missing = sum(int(row.get("missing_fact_count", 0) or 0) for row in results)
    ready = status_counts.get("ready_for_eval", 0)
    overall = "pass" if coverage_executed and ready > 0 and total_missing == 0 else "blocked"
    if not coverage_executed and not all(env.values()):
        overall = "not_checked_no_neo4j_config"
    label_rows = "\n".join(f"- `{label}`: {label_counts.get(label, 0)}" for label in REQUIRED_LABELS)
    return f"""# Neo4j Read-Only Coverage Report

## Status

`{overall}`

## Configuration Presence

- `NEO4J_URI`: {env['NEO4J_URI']}
- `NEO4J_USERNAME`: {env['NEO4J_USERNAME']}
- `NEO4J_PASSWORD`: {env['NEO4J_PASSWORD']}
- `NEO4J_DATABASE`: {env['NEO4J_DATABASE']}
- secret values printed: false

## Execution

- Neo4j connection attempted: {connection_attempted}
- Coverage executed: {coverage_executed}
- Neo4j write performed: false
- KG patch applied: false
- Dry-run executed: false
- Full eval executed: false
- Error: {error or 'none'}

## Label Coverage Probe

{label_rows if label_rows else '- not checked'}

## Case Coverage Summary

- Total cases checked: {len(results)}
- Ready for eval: {ready}
- Not ready / not checked: {len(results) - ready}
- Missing required fact count: {total_missing}
- Status counts: `{json.dumps(dict(sorted(status_counts.items())), ensure_ascii=False, sort_keys=True)}`
"""


def write_gate_status(run_dir: Path, results: list[dict[str, Any]], env: dict[str, bool], coverage_executed: bool) -> None:
    ready = sum(1 for row in results if row.get("coverage_status") == "ready_for_eval")
    missing = sum(int(row.get("missing_fact_count", 0) or 0) for row in results)
    if not all(env.values()):
        gate = "blocked"
        reason = "not_checked_no_neo4j_config"
    elif coverage_executed and ready > 0 and missing == 0:
        gate = "pass"
        reason = "all_checked_required_facts_supported"
    elif coverage_executed and ready > 0:
        gate = "warning"
        reason = "small_eligible_subset_ready_but_missing_required_facts_remain"
    else:
        gate = "blocked"
        reason = "coverage_failed_or_no_ready_cases"
    write_text(
        run_dir / "neo4j_coverage_gate_status.md",
        f"""# Neo4j Coverage Gate Status

neo4j_readonly_coverage: {gate}
coverage_reason: {reason}
ready_for_eval_count: {ready}
missing_required_fact_count: {missing}
dry_run_status: blocked
full_eval_lock: locked
neo4j_write_performed: no
kg_patch_applied: no
full_eval_executed: no
dry_run_executed: no
""",
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run Round 3 Neo4j read-only coverage.")
    parser.add_argument("--run-dir", default=None)
    parser.add_argument("--create-approval", action="store_true")
    args = parser.parse_args(argv)
    run_dir = run_dir_from_arg(args.run_dir)
    run_dir.mkdir(parents=True, exist_ok=True)
    if args.create_approval:
        create_approval_marker(run_dir)

    cases = load_jsonl(CASE_PATH)
    facts = load_jsonl(FACT_PATH)
    facts_by_case: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for fact in facts:
        facts_by_case[str(fact.get("case_id", ""))].append(fact)

    env_values = effective_env()
    env = env_presence(env_values)
    connection_attempted = False
    coverage_executed = False
    label_counts: dict[str, int] = {}
    error: str | None = None
    if not all(env.values()):
        results = case_results_without_config(cases, facts_by_case)
    else:
        try:
            connection_attempted = True
            results, label_counts = execute_coverage(cases, facts_by_case, env_values)
            coverage_executed = True
        except Exception as exc:  # noqa: BLE001 - report safely without secrets.
            error = f"{type(exc).__name__}: {exc}"
            results = [
                {
                    "case_id": str(case.get("case_id", "")),
                    "split": case.get("split", ""),
                    "required_fact_count": len(facts_by_case.get(str(case.get("case_id", "")), [])),
                    "matched_fact_count": 0,
                    "missing_fact_count": len(facts_by_case.get(str(case.get("case_id", "")), [])),
                    "coverage_status": "needs_human_review",
                    "missing_facts": [fact.get("fact_id") for fact in facts_by_case.get(str(case.get("case_id", "")), [])],
                    "notes": "Neo4j read-only coverage failed safely; no write was performed.",
                }
                for case in cases
            ]

    report = render_report(
        env=env,
        connection_attempted=connection_attempted,
        coverage_executed=coverage_executed,
        results=results,
        label_counts=label_counts,
        error=error,
    )
    write_text(run_dir / "neo4j_readonly_coverage_report.md", report)
    write_text(run_dir / "neo4j_coverage_report.md", report)
    write_jsonl(run_dir / "neo4j_coverage_results.jsonl", results)
    write_summary_csv(run_dir / "neo4j_coverage_summary.csv", results)
    write_gate_status(run_dir, results, env, coverage_executed)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
