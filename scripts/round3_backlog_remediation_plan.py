"""Generate Round 3 backlog remediation package without writes or model calls."""

from __future__ import annotations

import csv
import json
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[1]
ORCH_DIR = REPO_ROOT / "outputs" / "round3_orchestration" / "20260525_132801"
READY_RUN_DIR = REPO_ROOT / "outputs" / "round3_eval_runs" / "ready_partial_real_20260527_093341"
OUT_DIR = REPO_ROOT / "outputs" / "round3_backlog_remediation"
CASES_PATH = REPO_ROOT / "outputs" / "round3_case_factory_repaired" / "eval_ready_cases.jsonl"
FACTS_PATH = REPO_ROOT / "outputs" / "round3_case_factory_repaired" / "eval_ready_required_facts.jsonl"


def now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def rel(path: Path) -> str:
    try:
        return path.resolve().relative_to(REPO_ROOT.resolve()).as_posix()
    except ValueError:
        return str(path)


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            rows.append(json.loads(line))
    return rows


def load_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text.rstrip() + "\n", encoding="utf-8", newline="\n")


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")


def write_csv(path: Path, fieldnames: list[str], rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def parse_notes(row: dict[str, Any]) -> dict[str, Any]:
    try:
        parsed = json.loads(str(row.get("notes", "")))
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def split_score(split: str) -> int:
    if split == "round3_test":
        return 0
    if split == "round3_dev":
        return 1
    return 2


def reasoning_score(case: dict[str, Any]) -> int:
    reasoning = str(case.get("reasoning_type", "")).lower()
    if any(item in reasoning for item in ("compositional", "division", "addition")):
        return 0
    return 1


def company_confidence(case: dict[str, Any], facts: list[dict[str, Any]]) -> str:
    ticker = str(case.get("ticker", "")).strip()
    companies = {str(fact.get("company", "")).strip() for fact in facts if fact.get("company")}
    if ticker and len(companies) == 1:
        return "high"
    if ticker or companies:
        return "medium"
    return "low"


def exact_quote_confidence(facts: list[dict[str, Any]]) -> str:
    if facts and all(bool(fact.get("quote_is_exact_excerpt")) for fact in facts):
        return "high"
    if any(bool(fact.get("quote_is_exact_excerpt")) for fact in facts):
        return "medium"
    return "low"


def classify_missing_fact(fact: dict[str, Any], coverage_fact_note: dict[str, Any], coverage_status: str) -> tuple[str, list[str]]:
    classes: list[str] = []
    if "company_ticker" in coverage_status or coverage_fact_note.get("company_ticker_sensitive"):
        classes.extend(["KGEntity_mapping_issue", "case_id_source_id_mismatch"])
    if coverage_fact_note.get("metric_sensitive"):
        classes.append("metric_alias_mismatch")
    if coverage_fact_note.get("value_sensitive"):
        classes.append("value_mismatch")
    if not fact.get("year") or not fact.get("period_label"):
        classes.append("year_or_period_mismatch")
    if not fact.get("unit"):
        classes.append("unit_mismatch")
    if not fact.get("evidence_quote_exact") or not fact.get("quote_is_exact_excerpt"):
        classes.append("evidence_quote_mismatch")
    if "needs_company_ticker_review" in coverage_status:
        classes.append("ontology_label_mapping_issue")
    if not classes:
        classes.append("missing_observation")
    classes = sorted(set(classes))
    primary_order = [
        "KGEntity_mapping_issue",
        "case_id_source_id_mismatch",
        "metric_alias_mismatch",
        "value_mismatch",
        "year_or_period_mismatch",
        "unit_mismatch",
        "evidence_quote_mismatch",
        "ontology_label_mapping_issue",
        "missing_observation",
        "unresolved_manual_review",
    ]
    primary = next((item for item in primary_order if item in classes), "unresolved_manual_review")
    return primary, classes


def proposed_patch_type(primary: str) -> str:
    mapping = {
        "KGEntity_mapping_issue": "resolve_company_ticker_kgentity_mapping",
        "case_id_source_id_mismatch": "link_case_source_id_to_existing_observations",
        "metric_alias_mismatch": "add_metric_alias_or_normalized_metric_key",
        "value_mismatch": "upsert_observation_value_for_existing_fact",
        "year_or_period_mismatch": "link_observation_to_year_or_period",
        "unit_mismatch": "set_observation_unit",
        "evidence_quote_mismatch": "attach_or_correct_evidence_quote",
        "ontology_label_mapping_issue": "adapt_coverage_to_kgentity_observation_ontology",
        "missing_observation": "create_missing_observation_candidate",
    }
    return mapping.get(primary, "manual_review_required")


def risk_level(primary: str, confidence: str) -> str:
    if primary in {"KGEntity_mapping_issue", "case_id_source_id_mismatch"} and confidence != "high":
        return "high"
    if primary in {"value_mismatch", "missing_observation"}:
        return "medium"
    return "medium" if confidence == "high" else "high"


def cypher_string(value: Any) -> str:
    return json.dumps("" if value is None else str(value), ensure_ascii=False)


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    cases = {row["case_id"]: row for row in load_jsonl(CASES_PATH)}
    facts_by_case: dict[str, list[dict[str, Any]]] = defaultdict(list)
    facts_by_id: dict[str, dict[str, Any]] = {}
    for fact in load_jsonl(FACTS_PATH):
        facts_by_case[str(fact.get("case_id", ""))].append(fact)
        facts_by_id[str(fact.get("fact_id", ""))] = fact
    coverage_results = {row["case_id"]: row for row in load_jsonl(ORCH_DIR / "neo4j_coverage_results.jsonl")}
    breakdown = {row["case_id"]: row for row in load_csv(ORCH_DIR / "automation" / "backlog_failure_breakdown.csv")}
    backlog_case_ids = [case_id for case_id, row in breakdown.items() if int(row.get("missing_fact_count", "0") or 0) > 0]

    analysis_rows: list[dict[str, Any]] = []
    patch_rows: list[dict[str, Any]] = []
    case_priority_rows: list[dict[str, Any]] = []

    for case_id in backlog_case_ids:
        case = cases.get(case_id, {})
        facts = facts_by_case.get(case_id, [])
        coverage = coverage_results.get(case_id, {})
        notes = parse_notes(coverage)
        per_fact_notes = {item.get("fact_id"): item for item in notes.get("per_fact", []) if isinstance(item, dict)}
        missing_facts = coverage.get("missing_facts", [])
        missing_fact_objs = [facts_by_id[fid] for fid in missing_facts if fid in facts_by_id]
        conf = company_confidence(case, facts)
        quote_conf = exact_quote_confidence(missing_fact_objs)
        split = str(case.get("split", coverage.get("split", "")))
        priority_tuple = (
            split_score(split),
            reasoning_score(case),
            int(coverage.get("missing_fact_count", len(missing_facts)) or 0),
            0 if conf == "high" else 1 if conf == "medium" else 2,
            0 if quote_conf == "high" else 1 if quote_conf == "medium" else 2,
        )
        case_priority_rows.append(
            {
                "case_id": case_id,
                "split": split,
                "reasoning_type": case.get("reasoning_type", ""),
                "category": case.get("category", ""),
                "ticker": case.get("ticker", ""),
                "company": case.get("company", ""),
                "missing_fact_count": int(coverage.get("missing_fact_count", 0) or 0),
                "matched_fact_count": int(coverage.get("matched_fact_count", 0) or 0),
                "company_ticker_confidence": conf,
                "exact_quote_confidence": quote_conf,
                "priority_score": "|".join(str(item) for item in priority_tuple),
            }
        )
        for fact in missing_fact_objs:
            fact_id = str(fact.get("fact_id", ""))
            primary, classes = classify_missing_fact(fact, per_fact_notes.get(fact_id, {}), str(coverage.get("coverage_status", "")))
            source_id = fact.get("source_evidence_id") or notes.get("case_key") or case_id.rsplit("_", 1)[-1]
            analysis = {
                "case_id": case_id,
                "split": split,
                "fact_id": fact_id,
                "source_id": source_id,
                "expected_company": fact.get("company") or case.get("company", ""),
                "expected_ticker": fact.get("ticker") or case.get("ticker", ""),
                "expected_metric_canonical": fact.get("metric_canonical", ""),
                "expected_year": fact.get("year", ""),
                "period_label": fact.get("period_label", ""),
                "expected_value": fact.get("value", ""),
                "expected_unit": fact.get("unit", ""),
                "evidence_quote_exact": fact.get("evidence_quote_exact", ""),
                "primary_missing_or_mismatch_type": primary,
                "all_missing_or_mismatch_types": ";".join(classes),
                "coverage_status": coverage.get("coverage_status", ""),
                "observation_candidates": notes.get("observation_candidates", ""),
                "requires_manual_review": "true",
            }
            analysis_rows.append(analysis)
            patch_type = proposed_patch_type(primary)
            risk = risk_level(primary, conf)
            patch_rows.append(
                {
                    **analysis,
                    "original_case_id": case_id,
                    "matching_candidate_kgentity_nodes": [],
                    "missing_or_mismatch_type": primary,
                    "proposed_patch_type": patch_type,
                    "proposed_node_relationship_pattern": (
                        "(:KGEntity {source_id, source_dataset:'FinDER'})-[:HAS_OBSERVATION]->"
                        "(:KGEntity {fact_id, metric, numeric_value, unit})-[:OBSERVES_METRIC]->(:KGEntity {normalized_metric_key}); "
                        "observation-[:OBSERVED_IN_YEAR]->(:KGEntity {year}); evidence link by source_id/evidence_quote_exact"
                    ),
                    "risk_level": risk,
                    "requires_manual_approval": True,
                }
            )

    case_priority_rows.sort(key=lambda row: tuple(int(part) for part in row["priority_score"].split("|")))
    for rank, row in enumerate(case_priority_rows, start=1):
        row["priority_rank"] = rank
        row["priority_band"] = "P0" if rank <= 8 else "P1" if rank <= 15 else "P2"

    write_csv(
        OUT_DIR / "backlog_missing_fact_analysis.csv",
        [
            "case_id",
            "split",
            "fact_id",
            "source_id",
            "expected_company",
            "expected_ticker",
            "expected_metric_canonical",
            "expected_year",
            "period_label",
            "expected_value",
            "expected_unit",
            "evidence_quote_exact",
            "primary_missing_or_mismatch_type",
            "all_missing_or_mismatch_types",
            "coverage_status",
            "observation_candidates",
            "requires_manual_review",
        ],
        analysis_rows,
    )
    write_jsonl(OUT_DIR / "backlog_missing_fact_analysis.jsonl", analysis_rows)
    write_jsonl(OUT_DIR / "round3_targeted_kg_patch_candidates.jsonl", patch_rows)

    priority_md = [
        "# Round 3 Backlog Case Priority",
        "",
        f"Generated: {now()}",
        "",
        "| Rank | Band | Case | Split | Missing | Matched | Ticker | Reasoning | Confidence |",
        "|---:|---|---|---|---:|---:|---|---|---|",
    ]
    for row in case_priority_rows:
        priority_md.append(
            f"| {row['priority_rank']} | `{row['priority_band']}` | `{row['case_id']}` | `{row['split']}` | "
            f"{row['missing_fact_count']} | {row['matched_fact_count']} | `{row['ticker']}` | "
            f"`{row['reasoning_type']}` | `{row['company_ticker_confidence']}/{row['exact_quote_confidence']}` |"
        )
    write_text(OUT_DIR / "backlog_case_priority.md", "\n".join(priority_md))

    missing_counts = Counter(row["primary_missing_or_mismatch_type"] for row in analysis_rows)
    option_a_patch_count = len(patch_rows)
    option_b_cases = case_priority_rows[:15]
    option_b_ids = {row["case_id"] for row in option_b_cases}
    option_b_patch_count = sum(1 for row in patch_rows if row["case_id"] in option_b_ids)

    summary = f"""# Round 3 Backlog Remediation Summary

Generated: {now()}

## Frozen Partial Result

- Frozen ready-subset run: `{rel(READY_RUN_DIR)}`
- Final report: `{rel(READY_RUN_DIR / 'final_ready_subset_partial_report.md')}`
- Claim boundary retained: `{rel(READY_RUN_DIR / 'final_claim_boundary.md')}`
- Ready-subset partial eval: complete
- Ready cases evaluated: 6
- Provider route recorded: OpenAI / gpt-4.1-mini
- Attempts: 24
- Provider failures: 0
- Input contamination: 0
- Full eval: locked
- Neo4j write: no
- KG patch applied: no
- Backlog: 19 cases / 81 missing required facts

## Missing Fact Classification

{chr(10).join(f'- `{key}`: {value}' for key, value in sorted(missing_counts.items()))}

## Gate Judgment Before Patch

`needs_manual_review`

Patch candidates are specific enough to discuss, but matching candidate KGEntity node IDs are not fully resolved in the existing evidence files. Manual approval and/or read-only candidate-node verification is required before any write patch.

## Recommended Route

Option B is recommended: patch the top priority 10-15 cases first, then run coverage refresh and consider expanded partial evaluation. Do not run full eval yet.
"""
    write_text(OUT_DIR / "backlog_remediation_summary.md", summary)

    plan = f"""# Round 3 Targeted KG Patch Plan

Generated: {now()}

## Safety Boundary

- Neo4j write performed: false
- KG patch applied: false
- Full eval executed: false
- Dangerous write Cypher is preview-only and must not be executed without explicit user approval.

## Actual Ontology Target

The populated database uses `KGEntity` with relationships such as `HAS_OBSERVATION`, `OBSERVES_METRIC`, and `OBSERVED_IN_YEAR`; expected labels like `DatasetCase`, `EvidenceText`, `Company`, `Metric`, `Year`, `Value`, and `Observation` are absent.

## Patch Candidate Shape

Patch candidates target KGEntity-style observations and source/evidence links:

```text
(:KGEntity {{source_id, source_dataset:'FinDER'}})
  -[:HAS_OBSERVATION]->
(:KGEntity {{fact_id, metric, numeric_value, unit}})
  -[:OBSERVES_METRIC]->(:KGEntity {{normalized_metric_key}})
(:KGEntity {{fact_id}})-[:OBSERVED_IN_YEAR]->(:KGEntity {{year}})
```

## Options

| Option | Scope | Estimated Patch Count | Risk | Estimated Time | Claim Strength | Recommendation |
|---|---|---:|---|---|---|---|
| A | Patch all 19 backlog cases toward 25/25 full eval readiness | {option_a_patch_count} | high | high | strongest if verified, but too risky before manual review | not first |
| B | Patch top priority 10-15 cases, then expanded partial eval | {option_b_patch_count} | medium | medium | stronger than ready6 while limiting risk | recommended |
| C | Keep 6 ready subset only and defer full eval | 0 | low | low | limited partial-eval claim only | acceptable fallback |

## Priority

See `{rel(OUT_DIR / 'backlog_case_priority.md')}`.
"""
    write_text(OUT_DIR / "round3_targeted_kg_patch_plan.md", plan)

    safe_queries = """# Safe Read-Only Cypher Debug Queries

These queries are read-only. They are for candidate-node verification only.

```cypher
// 1. Find source/case-like KGEntity nodes by source id.
MATCH (n:KGEntity)
WHERE n.source_id = $source_id OR n._source_id = $source_id OR n.case_id = $case_id OR n.id = $case_id
RETURN labels(n) AS labels, properties(n) AS properties
LIMIT 25;

// 2. Find company/ticker candidate nodes.
MATCH (n:KGEntity)
WHERE toLower(coalesce(n.ticker, '')) = toLower($ticker)
   OR toLower(coalesce(n.name, '')) CONTAINS toLower($company)
RETURN labels(n) AS labels, properties(n) AS properties
LIMIT 25;

// 3. Find observation candidates near source id and value/year.
MATCH (s:KGEntity)-[:HAS_OBSERVATION]->(o:KGEntity)
OPTIONAL MATCH (o)-[:OBSERVES_METRIC]->(m:KGEntity)
OPTIONAL MATCH (o)-[:OBSERVED_IN_YEAR]->(y:KGEntity)
WHERE (s.source_id = $source_id OR s._source_id = $source_id)
  AND (o.numeric_value = $value OR o.value = $value OR toString(o.value) = toString($value))
  AND (y.year = $year OR o.year = $year)
RETURN properties(s) AS source, properties(o) AS observation, properties(m) AS metric, properties(y) AS year
LIMIT 50;

// 4. Metric alias probe.
MATCH (m:KGEntity)
WHERE toLower(coalesce(m.normalized_metric_key, '')) CONTAINS toLower($metric)
   OR toLower(coalesce(m.metric, '')) CONTAINS toLower($metric)
   OR toLower(coalesce(m.name, '')) CONTAINS toLower($metric)
RETURN labels(m) AS labels, properties(m) AS properties
LIMIT 50;
```
"""
    write_text(OUT_DIR / "safe_readonly_cypher_debug_queries.md", safe_queries)

    preview_lines = [
        "// DANGEROUS WRITE PATCH PREVIEW - DO NOT EXECUTE",
        "// Requires explicit user approval in approval_request_for_kg_patch.md before use.",
        "// Uses KGEntity ontology: HAS_OBSERVATION / OBSERVES_METRIC / OBSERVED_IN_YEAR.",
        "",
        "/*",
    ]
    for row in patch_rows[:20]:
        preview_lines.extend(
            [
                f"// case_id={row['case_id']} fact_id={row['fact_id']} risk={row['risk_level']}",
                "MERGE (src:KGEntity {source_id: " + cypher_string(row["source_id"]) + "})",
                "  ON CREATE SET src.source_dataset = 'FinDER', src.case_id = " + cypher_string(row["case_id"]),
                "MERGE (obs:KGEntity {fact_id: " + cypher_string(row["fact_id"]) + "})",
                "  SET obs.metric = " + cypher_string(row["expected_metric_canonical"]) + ",",
                "      obs.numeric_value = " + json.dumps(row["expected_value"]) + ",",
                "      obs.unit = " + cypher_string(row["expected_unit"]) + ",",
                "      obs.evidence_quote_exact = " + cypher_string(row["evidence_quote_exact"]),
                "MERGE (src)-[:HAS_OBSERVATION]->(obs);",
                "",
            ]
        )
    preview_lines.append("*/")
    write_text(OUT_DIR / "dangerous_write_patch_preview.cypher", "\n".join(preview_lines))

    approval = f"""# Approval Request For KG Patch

Generated: {now()}

## Requested Scope

Apply targeted KG patch candidates for Round 3 backlog remediation.

## Required Explicit User Approval

No KG patch may be applied unless the user explicitly approves this file's scope in a separate instruction. Until then:

- Neo4j write allowed: false
- KG patch applied: false
- Full eval allowed: false

## Candidate Files

- `{rel(OUT_DIR / 'round3_targeted_kg_patch_candidates.jsonl')}`
- `{rel(OUT_DIR / 'dangerous_write_patch_preview.cypher')}`

## Current Gate Judgment

`needs_manual_review`

Reason: candidate KGEntity node IDs must be verified with read-only queries before write execution.
"""
    write_text(OUT_DIR / "approval_request_for_kg_patch.md", approval)

    go = f"""# Go / No-Go Before Patch

Generated: {now()}

## Decision

`needs_manual_review`

## Rationale

- Patch candidates are fact-specific and tied to repaired required facts.
- Existing coverage evidence indicates company/ticker or KGEntity mapping is the common blocker.
- Matching candidate KGEntity node IDs are not fully resolved in current files.
- Dangerous write preview exists but must not be executed.

## Safety

- Neo4j write performed: false
- KG patch applied: false
- Full eval executed: false
- Round 02 artifacts modified: false
- repaired subset source files modified: false
"""
    write_text(OUT_DIR / "go_no_go_before_patch.md", go)

    print(json.dumps({"missing_facts": len(analysis_rows), "patch_candidates": len(patch_rows), "gate": "needs_manual_review"}, sort_keys=True))


if __name__ == "__main__":
    main()
