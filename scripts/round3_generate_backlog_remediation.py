"""Generate Round 3 backlog remediation artifacts without mutating the KG.

This script is intentionally file-only. It reads the frozen ready-subset
partial-eval outputs plus existing read-only coverage artifacts, then writes
fact-level analysis and patch-candidate review files under
outputs/round3_backlog_remediation/.
"""

from __future__ import annotations

import csv
import hashlib
import json
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
RUN_DIR = ROOT / "outputs" / "round3_orchestration" / "20260525_132801"
PARTIAL_RUN_DIR = ROOT / "outputs" / "round3_eval_runs" / "ready_partial_real_20260527_093341"
REPAIRED_DIR = ROOT / "outputs" / "round3_case_factory_repaired"
OUT_DIR = ROOT / "outputs" / "round3_backlog_remediation"


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


def read_csv(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def write_csv(path: Path, rows: list[dict[str, Any]], fieldnames: list[str]) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow({key: row.get(key, "") for key in fieldnames})


def rel(path: Path) -> str:
    return path.relative_to(ROOT).as_posix()


def stable_candidate_id(fact_id: str) -> str:
    digest = hashlib.sha1(fact_id.encode("utf-8")).hexdigest()[:10]
    return f"round3_patch_candidate_{digest}"


def parse_notes(raw_notes: Any) -> dict[str, Any]:
    if isinstance(raw_notes, dict):
        return raw_notes
    if not raw_notes:
        return {}
    try:
        return json.loads(str(raw_notes))
    except json.JSONDecodeError:
        return {"raw_notes": str(raw_notes)}


def split_rank(split: str) -> int:
    order = {"round3_test": 0, "round3_dev": 1, "baseline_control": 2}
    return order.get(split, 3)


def reasoning_rank(reasoning_type: str) -> int:
    preferred = ("compositional", "division", "addition")
    text = (reasoning_type or "").lower()
    return 0 if any(token in text for token in preferred) else 1


def truthy(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"true", "1", "yes", "pass"}


def classify_fact(
    case: dict[str, Any],
    fact: dict[str, Any],
    coverage: dict[str, Any],
    per_fact: dict[str, Any],
) -> tuple[str, list[str], str]:
    classes: list[str] = []
    status = str(coverage.get("coverage_status", ""))
    obs_candidates = parse_notes(coverage.get("notes")).get("observation_candidates")

    if status == "needs_company_ticker_review" or truthy(per_fact.get("company_ticker_sensitive")):
        classes.extend(["KGEntity_mapping_issue", "case_id_source_id_mismatch"])
    if obs_candidates in {0, "0", None}:
        classes.append("missing_observation")
    if truthy(per_fact.get("metric_sensitive")):
        classes.append("metric_alias_mismatch")
    if truthy(per_fact.get("value_sensitive")):
        classes.append("value_mismatch")
    if not fact.get("unit"):
        classes.append("unit_mismatch")
    if not fact.get("year") and not fact.get("period_label"):
        classes.append("year_or_period_mismatch")
    if not truthy(fact.get("quote_is_exact_excerpt")):
        classes.append("evidence_quote_mismatch")

    if not classes:
        classes.append("unresolved_manual_review")

    deduped = list(dict.fromkeys(classes))
    primary = deduped[0]
    note = (
        "Existing read-only coverage found observation candidates but no exact fact match; "
        "company/ticker-sensitive KGEntity mapping is the dominant blocker."
        if "KGEntity_mapping_issue" in deduped
        else "Fact remains unresolved by existing read-only coverage artifacts."
    )
    return primary, deduped, note


def proposed_patch_type(classes: list[str]) -> str:
    if "KGEntity_mapping_issue" in classes or "case_id_source_id_mismatch" in classes:
        return "manual_approve_link_company_case_and_observation_kgentity"
    if "missing_observation" in classes:
        return "manual_approve_create_financial_observation_kgentity"
    return "manual_approve_normalize_existing_observation_properties"


def risk_level(case: dict[str, Any], fact: dict[str, Any], classes: list[str]) -> str:
    clear_company = bool(case.get("company") and case.get("ticker"))
    exact_quote = truthy(fact.get("quote_is_exact_excerpt"))
    if "evidence_quote_mismatch" in classes or not clear_company:
        return "high"
    if exact_quote and set(classes).issubset(
        {"KGEntity_mapping_issue", "case_id_source_id_mismatch", "metric_alias_mismatch", "value_mismatch"}
    ):
        return "medium"
    return "medium_high"


def pattern_for_candidate(case: dict[str, Any], fact: dict[str, Any]) -> str:
    return (
        "(:KGEntity {ticker/name})-[:HAS_OBSERVATION]->"
        "(:KGEntity {fact_id, source_id, metric_canonical, numeric_value, unit, year, period_label}) "
        "-[:OBSERVES_METRIC]->(:KGEntity {metric_canonical}); "
        "(:KGEntity {fact_id})-[:OBSERVED_IN_YEAR]->(:KGEntity {year})"
    )


def load_inputs() -> dict[str, Any]:
    cases = {row["case_id"]: row for row in read_jsonl(REPAIRED_DIR / "eval_ready_cases.jsonl")}
    facts = read_jsonl(REPAIRED_DIR / "eval_ready_required_facts.jsonl")
    facts_by_id = {row["fact_id"]: row for row in facts}
    facts_by_case: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for fact in facts:
        facts_by_case[fact["case_id"]].append(fact)

    coverage_results = {row["case_id"]: row for row in read_jsonl(RUN_DIR / "neo4j_coverage_results.jsonl")}
    coverage_summary = read_csv(RUN_DIR / "neo4j_coverage_summary.csv")
    failure_breakdown = {row["case_id"]: row for row in read_csv(RUN_DIR / "automation" / "backlog_failure_breakdown.csv")}

    return {
        "cases": cases,
        "facts_by_id": facts_by_id,
        "facts_by_case": facts_by_case,
        "coverage_results": coverage_results,
        "coverage_summary": coverage_summary,
        "failure_breakdown": failure_breakdown,
    }


def build_rows(data: dict[str, Any]) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    cases = data["cases"]
    facts_by_id = data["facts_by_id"]
    coverage_results = data["coverage_results"]

    backlog_coverages = [
        row
        for row in coverage_results.values()
        if row.get("coverage_status") != "ready_for_eval" and int(row.get("missing_fact_count", 0) or 0) > 0
    ]

    analysis_rows: list[dict[str, Any]] = []
    candidate_rows: list[dict[str, Any]] = []
    case_summaries: dict[str, dict[str, Any]] = {}

    for coverage in sorted(backlog_coverages, key=lambda row: row["case_id"]):
        case_id = coverage["case_id"]
        case = cases.get(case_id, {})
        notes = parse_notes(coverage.get("notes"))
        per_fact_by_id = {item.get("fact_id"): item for item in notes.get("per_fact", [])}

        for fact_id in coverage.get("missing_facts", []):
            fact = facts_by_id.get(fact_id, {"fact_id": fact_id, "case_id": case_id})
            per_fact = per_fact_by_id.get(fact_id, {})
            primary, classes, note = classify_fact(case, fact, coverage, per_fact)

            row = {
                "case_id": case_id,
                "split": case.get("split", coverage.get("split", "")),
                "fact_id": fact_id,
                "source_id": fact.get("source_evidence_id", notes.get("case_key", "")),
                "original_case_id": notes.get("case_key", ""),
                "company": fact.get("company", case.get("company", "")),
                "ticker": fact.get("ticker", case.get("ticker", "")),
                "metric_canonical": fact.get("metric_canonical", ""),
                "metric_raw": fact.get("metric_raw", ""),
                "year": fact.get("year", ""),
                "period_label": fact.get("period_label", ""),
                "value": fact.get("value", ""),
                "unit": fact.get("unit", ""),
                "evidence_quote_exact": fact.get("evidence_quote_exact", ""),
                "quote_is_exact_excerpt": truthy(fact.get("quote_is_exact_excerpt")),
                "coverage_status": coverage.get("coverage_status", ""),
                "observation_candidates": notes.get("observation_candidates", ""),
                "primary_missing_or_mismatch_type": primary,
                "all_missing_or_mismatch_types": ";".join(classes),
                "classification_notes": note,
                "requires_manual_review": True,
            }
            analysis_rows.append(row)

            candidate = {
                "candidate_id": stable_candidate_id(fact_id),
                "case_id": case_id,
                "source_id": row["source_id"],
                "original_case_id": row["original_case_id"],
                "fact_id": fact_id,
                "expected_company": row["company"],
                "expected_ticker": row["ticker"],
                "expected_metric_canonical": row["metric_canonical"],
                "expected_year": row["year"],
                "expected_period_label": row["period_label"],
                "expected_value": row["value"],
                "expected_unit": row["unit"],
                "evidence_quote_exact": row["evidence_quote_exact"],
                "matching_candidate_KGEntity_nodes": [],
                "matching_candidate_notes": (
                    "Not resolved from existing coverage artifacts. Use safe_readonly_cypher_debug_queries.md "
                    "to identify exact KGEntity node ids before any patch approval."
                ),
                "missing_or_mismatch_type": primary,
                "all_missing_or_mismatch_types": classes,
                "proposed_patch_type": proposed_patch_type(classes),
                "proposed_node_relationship_pattern": pattern_for_candidate(case, fact),
                "risk_level": risk_level(case, fact, classes),
                "requires_manual_approval": True,
                "kg_write_executed": False,
            }
            candidate_rows.append(candidate)

        case_facts = [facts_by_id[fid] for fid in coverage.get("missing_facts", []) if fid in facts_by_id]
        exact_quote_count = sum(1 for fact in case_facts if truthy(fact.get("quote_is_exact_excerpt")))
        case_summaries[case_id] = {
            "case_id": case_id,
            "split": case.get("split", coverage.get("split", "")),
            "company": case.get("company", ""),
            "ticker": case.get("ticker", ""),
            "category": case.get("category", ""),
            "reasoning_type": case.get("reasoning_type", ""),
            "missing_fact_count": int(coverage.get("missing_fact_count", 0) or 0),
            "observation_candidates": notes.get("observation_candidates", ""),
            "exact_quote_count": exact_quote_count,
            "coverage_status": coverage.get("coverage_status", ""),
        }

    priority_rows = sorted(
        case_summaries.values(),
        key=lambda row: (
            split_rank(str(row["split"])),
            0 if str(row["category"]).lower() == "financials" else 1,
            reasoning_rank(str(row["reasoning_type"])),
            int(row["missing_fact_count"]),
            0 if row["company"] and row["ticker"] else 1,
            -int(row["exact_quote_count"]),
            row["case_id"],
        ),
    )
    for index, row in enumerate(priority_rows, start=1):
        row["priority_rank"] = index
        row["recommended_lane"] = "Option B first wave" if index <= 12 else "Option A tail / manual review"
        row["priority_rationale"] = (
            "Ranked by test split, financial typed reasoning, compositional/division/addition, "
            "fewer missing facts, clear company/ticker, and exact quote availability."
        )

    return analysis_rows, candidate_rows, priority_rows


def write_freeze_manifest(now: str) -> None:
    files = [
        "final_ready_subset_partial_report.md",
        "clean_subset_method_summary.csv",
        "final_claim_boundary.md",
        "case_review_flags.csv",
    ]
    entries = []
    for name in files:
        path = PARTIAL_RUN_DIR / name
        entries.append(
            {
                "path": rel(path),
                "exists": path.exists(),
                "sha256": hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else None,
                "role": "frozen_ready_subset_partial_eval_evidence",
                "should_modify": False,
            }
        )
    manifest = {
        "created_at": now,
        "frozen_run_dir": rel(PARTIAL_RUN_DIR),
        "claim_boundary_source": rel(PARTIAL_RUN_DIR / "final_claim_boundary.md"),
        "full_eval_executed": False,
        "neo4j_write_performed": False,
        "kg_patch_applied": False,
        "entries": entries,
    }
    (OUT_DIR / "partial_result_freeze_manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    lines = [
        "# Partial Result Freeze Manifest",
        "",
        f"- Created at: {now}",
        f"- Frozen run dir: `{rel(PARTIAL_RUN_DIR)}`",
        f"- Claim boundary preserved from: `{rel(PARTIAL_RUN_DIR / 'final_claim_boundary.md')}`",
        "- Files in the partial eval run were not overwritten by this remediation step.",
        "- Full evaluation remains locked.",
        "",
        "| File | Exists | SHA256 |",
        "| --- | --- | --- |",
    ]
    for entry in entries:
        lines.append(f"| `{entry['path']}` | {entry['exists']} | `{entry['sha256'] or ''}` |")
    (OUT_DIR / "partial_result_freeze_manifest.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def write_priority_md(rows: list[dict[str, Any]]) -> None:
    lines = [
        "# Round 3 Backlog Case Priority",
        "",
        "Priority follows: round3_test split first, Financials typed reasoning first, "
        "Compositional/Division/Addition first, fewer missing facts first, clear company/ticker, "
        "then exact quote availability.",
        "",
        "| Rank | Case ID | Split | Company | Ticker | Reasoning | Missing Facts | Exact Quotes | Lane |",
        "| ---: | --- | --- | --- | --- | --- | ---: | ---: | --- |",
    ]
    for row in rows:
        lines.append(
            "| {priority_rank} | `{case_id}` | {split} | {company} | {ticker} | {reasoning_type} | "
            "{missing_fact_count} | {exact_quote_count} | {recommended_lane} |".format(**row)
        )
    (OUT_DIR / "backlog_case_priority.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def write_safe_queries() -> None:
    text = """# Safe Read-Only Cypher Debug Queries

Use these only for manual candidate resolution before any patch approval. They are read-only
and intentionally use parameter placeholders.

Forbidden in this phase: CREATE, MERGE, SET, DELETE, REMOVE, DROP, LOAD CSV, CALL dbms,
CALL apoc.periodic.

```cypher
SHOW DATABASES;
```

```cypher
MATCH (n)
WHERE any(v IN [n.case_id, n.id, n.source_case_id, n.dataset_case_id, n.finder_case_id, n.source_id, n.name, n.text, n.content] WHERE toString(v) CONTAINS $case_id)
RETURN labels(n) AS labels, keys(n) AS properties, n AS node
LIMIT 25;
```

```cypher
MATCH (company:KGEntity)
WHERE toUpper(toString(company.ticker)) = toUpper($ticker)
   OR toLower(toString(company.name)) CONTAINS toLower($company)
OPTIONAL MATCH (company)-[r:HAS_OBSERVATION]->(obs:KGEntity)
RETURN company, type(r) AS rel_type, obs
LIMIT 50;
```

```cypher
MATCH (obs:KGEntity)
WHERE toLower(toString(obs.metric_canonical)) = toLower($metric_canonical)
   OR toLower(toString(obs.metric)) CONTAINS toLower($metric_canonical)
   OR toLower(toString(obs.name)) CONTAINS toLower($metric_canonical)
RETURN labels(obs) AS labels, keys(obs) AS properties, obs
LIMIT 50;
```

```cypher
MATCH (company:KGEntity)-[:HAS_OBSERVATION]->(obs:KGEntity)
WHERE (toUpper(toString(company.ticker)) = toUpper($ticker)
   OR toLower(toString(company.name)) CONTAINS toLower($company))
  AND (toString(obs.year) = toString($year)
   OR toString(obs.period_label) = toString($period_label))
RETURN company, obs
LIMIT 50;
```

```cypher
MATCH (company:KGEntity)-[:HAS_OBSERVATION]->(obs:KGEntity)
OPTIONAL MATCH (obs)-[:OBSERVES_METRIC]->(metric:KGEntity)
OPTIONAL MATCH (obs)-[:OBSERVED_IN_YEAR]->(year_node:KGEntity)
WHERE (toUpper(toString(company.ticker)) = toUpper($ticker)
   OR toLower(toString(company.name)) CONTAINS toLower($company))
RETURN company, obs, metric, year_node
LIMIT 100;
```
"""
    (OUT_DIR / "safe_readonly_cypher_debug_queries.md").write_text(text, encoding="utf-8")


def write_dangerous_preview(candidates: list[dict[str, Any]]) -> None:
    sample = candidates[:5]
    lines = [
        "// DANGEROUS WRITE PATCH PREVIEW - DO NOT EXECUTE",
        "// This file is a non-executed preview only. Every write line is commented out.",
        "// User approval and exact KGEntity node-id resolution are required before any patch.",
        "// Full evaluation remains locked.",
        "",
        "// Example parameter shape:",
        "// :param patch => {candidate_id: '...', fact_id: '...', company_node_id: 0, observation_node_id: 0};",
        "",
    ]
    for candidate in sample:
        lines.extend(
            [
                f"// Candidate {candidate['candidate_id']} for {candidate['fact_id']}",
                f"// Expected: {candidate['expected_ticker']} {candidate['expected_metric_canonical']} {candidate['expected_year']} {candidate['expected_value']} {candidate['expected_unit']}",
                "// MATCH (company:KGEntity) WHERE id(company) = $company_node_id",
                "// MATCH (obs:KGEntity) WHERE id(obs) = $observation_node_id",
                "// MERGE (company)-[:HAS_OBSERVATION]->(obs)",
                "// SET obs.fact_id = $fact_id, obs.source_id = $source_id, obs.metric_canonical = $metric_canonical, obs.numeric_value = $numeric_value, obs.unit = $unit, obs.year = $year",
                "",
            ]
        )
    (OUT_DIR / "dangerous_write_patch_preview.cypher").write_text("\n".join(lines), encoding="utf-8")


def option_lines(priority_rows: list[dict[str, Any]], candidates: list[dict[str, Any]]) -> list[str]:
    top12 = {row["case_id"] for row in priority_rows[:12]}
    top12_patch_count = sum(1 for candidate in candidates if candidate["case_id"] in top12)
    return [
        "## Remediation Options",
        "",
        "| Option | Scope | Expected Patch Count | Expected Risk | Estimated Time | Claim Strength | Recommendation |",
        "| --- | --- | ---: | --- | --- | --- | --- |",
        f"| A | Patch all 19 backlog cases for a possible 25/25 full-eval candidate set | {len(candidates)} | High until node ids are manually resolved | 1-2 focused review days plus approval | Strongest if all patches validate; still not full eval approval | Not first choice before manual candidate resolution |",
        f"| B | Patch top 10-15 priority cases, first wave modeled here as top 12 | {top12_patch_count} | Medium to medium-high | Half-day to 1 day review plus approval | Expanded partial-eval claim, cleaner than current ready6 | Recommended next remediation path |",
        "| C | Keep only the 6 ready cases and defer full evaluation | 0 | Low | Immediate | Narrow ready-subset claim only | Safe fallback, not sufficient for full-eval readiness |",
    ]


def write_summary(
    analysis_rows: list[dict[str, Any]],
    candidates: list[dict[str, Any]],
    priority_rows: list[dict[str, Any]],
    now: str,
) -> None:
    type_counter = Counter(row["primary_missing_or_mismatch_type"] for row in analysis_rows)
    case_count = len({row["case_id"] for row in analysis_rows})
    exact_quotes = sum(1 for row in analysis_rows if row["quote_is_exact_excerpt"])

    lines = [
        "# Round 3 Backlog Remediation Summary",
        "",
        f"- Created at: {now}",
        "- Scope: backlog remediation planning and KG patch candidate generation only.",
        "- Full evaluation executed: false",
        "- Neo4j write performed: false",
        "- KG patch applied: false",
        "- Gemini used: false",
        f"- Frozen partial result: `{rel(PARTIAL_RUN_DIR)}`",
        f"- Frozen claim boundary: `{rel(PARTIAL_RUN_DIR / 'final_claim_boundary.md')}`",
        "",
        "## Backlog Totals",
        "",
        f"- Backlog cases analyzed: {case_count}",
        f"- Missing required facts analyzed: {len(analysis_rows)}",
        f"- Patch candidates generated: {len(candidates)}",
        f"- Exact evidence quote facts: {exact_quotes}",
        "",
        "## Missing/Mismatch Classification",
        "",
    ]
    for label, count in type_counter.most_common():
        lines.append(f"- {label}: {count}")
    lines.extend(["", *option_lines(priority_rows, candidates)])
    lines.extend(
        [
            "",
            "## Gate Decision Before Patch",
            "",
            "Gate decision: `needs_manual_review`.",
            "",
            "The candidates are specific at fact level, but exact KGEntity node ids and relationship targets "
            "are not resolved in the existing file evidence. Use the safe read-only debug queries to resolve "
            "candidate nodes, then request explicit user approval before any KG patch is applied.",
        ]
    )
    (OUT_DIR / "backlog_remediation_summary.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def write_patch_plan(
    analysis_rows: list[dict[str, Any]],
    candidates: list[dict[str, Any]],
    priority_rows: list[dict[str, Any]],
) -> None:
    lines = [
        "# Round 3 Targeted KG Patch Plan",
        "",
        "This plan is a candidate-only remediation package. It does not execute writes.",
        "",
        "## Actual Ontology Target",
        "",
        "- Expected coverage labels are absent: DatasetCase, EvidenceText, Company, Metric, Year, Value, Observation.",
        "- Populated schema uses KGEntity-centered nodes and relationships such as HAS_OBSERVATION, OBSERVES_METRIC, and OBSERVED_IN_YEAR.",
        "- Patch candidates therefore target KGEntity observation linkage and normalization, not the absent expected labels.",
        "",
        "## Execution Preconditions",
        "",
        "1. Resolve candidate company and observation KGEntity node ids with read-only queries.",
        "2. Review every candidate in `round3_targeted_kg_patch_candidates.jsonl`.",
        "3. Obtain explicit user approval for KG patch application.",
        "4. Keep full eval locked until post-patch read-only coverage and separate approval.",
        "",
        *option_lines(priority_rows, candidates),
        "",
        "## First-Wave Cases",
        "",
    ]
    for row in priority_rows[:12]:
        lines.append(
            f"- Rank {row['priority_rank']}: `{row['case_id']}` ({row['split']}, {row['ticker']}, "
            f"{row['reasoning_type']}, missing facts: {row['missing_fact_count']})"
        )
    lines.extend(
        [
            "",
            "## Patch Candidate Count",
            "",
            f"- Total candidate facts: {len(candidates)}",
            f"- Total backlog cases: {len({row['case_id'] for row in analysis_rows})}",
        ]
    )
    (OUT_DIR / "round3_targeted_kg_patch_plan.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def write_approval_and_go_no_go(candidates: list[dict[str, Any]], priority_rows: list[dict[str, Any]]) -> None:
    approval = [
        "# Approval Request For KG Patch",
        "",
        "No KG patch has been applied.",
        "",
        "Explicit user approval is required before executing any write Cypher or applying any KG patch.",
        "Approval should identify the approved scope, for example Option B first wave or Option A all backlog cases.",
        "",
        "## Requested Approval Scope",
        "",
        "- Recommended: Option B first wave after read-only KGEntity node-id resolution.",
        f"- Candidate facts available: {len(candidates)}",
        f"- First-wave cases listed: {min(12, len(priority_rows))}",
        "",
        "## Safety Boundaries",
        "",
        "- Full evaluation remains locked.",
        "- Neo4j write remains forbidden until explicit approval.",
        "- KG patch application remains forbidden until explicit approval.",
        "- Round 02 artifacts and repaired subset source files must not be modified.",
    ]
    (OUT_DIR / "approval_request_for_kg_patch.md").write_text("\n".join(approval) + "\n", encoding="utf-8")

    go_no_go = [
        "# Go / No-Go Before Patch",
        "",
        "Decision: `needs_manual_review`",
        "",
        "## Rationale",
        "",
        "- Fact-level patch candidates are available for the 19-case / 81-fact backlog.",
        "- Existing coverage evidence points to KGEntity/company/ticker mapping as the dominant blocker.",
        "- Exact target KGEntity node ids are not present in the file evidence and must be resolved with read-only queries.",
        "- Applying patches without manual review would be too risky.",
        "",
        "## Current Locks",
        "",
        "- Full evaluation: locked",
        "- Dry-run/full eval execution in this step: not performed",
        "- Neo4j write: not performed",
        "- KG patch: not applied",
    ]
    (OUT_DIR / "go_no_go_before_patch.md").write_text("\n".join(go_no_go) + "\n", encoding="utf-8")


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    now = datetime.now(timezone.utc).isoformat()
    data = load_inputs()
    analysis_rows, candidates, priority_rows = build_rows(data)

    analysis_fields = [
        "case_id",
        "split",
        "fact_id",
        "source_id",
        "original_case_id",
        "company",
        "ticker",
        "metric_canonical",
        "metric_raw",
        "year",
        "period_label",
        "value",
        "unit",
        "evidence_quote_exact",
        "quote_is_exact_excerpt",
        "coverage_status",
        "observation_candidates",
        "primary_missing_or_mismatch_type",
        "all_missing_or_mismatch_types",
        "classification_notes",
        "requires_manual_review",
    ]
    write_csv(OUT_DIR / "backlog_missing_fact_analysis.csv", analysis_rows, analysis_fields)
    write_jsonl(OUT_DIR / "backlog_missing_fact_analysis.jsonl", analysis_rows)
    write_jsonl(OUT_DIR / "round3_targeted_kg_patch_candidates.jsonl", candidates)
    write_priority_md(priority_rows)
    write_safe_queries()
    write_dangerous_preview(candidates)
    write_summary(analysis_rows, candidates, priority_rows, now)
    write_patch_plan(analysis_rows, candidates, priority_rows)
    write_approval_and_go_no_go(candidates, priority_rows)
    write_freeze_manifest(now)

    print(
        json.dumps(
            {
                "out_dir": rel(OUT_DIR),
                "backlog_cases": len({row["case_id"] for row in analysis_rows}),
                "missing_facts": len(analysis_rows),
                "patch_candidates": len(candidates),
                "gate_decision": "needs_manual_review",
                "full_eval_executed": False,
                "neo4j_write_performed": False,
                "kg_patch_applied": False,
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
