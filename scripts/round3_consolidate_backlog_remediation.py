"""Create the consolidated Round 3 backlog remediation package.

The consolidated package preserves the earlier fact-level Codex artifacts and
adds a guarded case/group-level plan, crosswalk, disabled Cypher preview, and
safety scan. This script does not connect to Neo4j and does not call models.
"""

from __future__ import annotations

import ast
import csv
import json
import re
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
SOURCE_DIR = ROOT / "outputs" / "round3_backlog_remediation"
OUT_DIR = ROOT / "outputs" / "round3_backlog_remediation_consolidated"
PARTIAL_DIR = ROOT / "outputs" / "round3_eval_runs" / "ready_partial_real_20260527_093341"
RUN_DIR = ROOT / "outputs" / "round3_orchestration" / "20260525_132801"

FORBIDDEN_UNCOMMENTED_PATTERN = re.compile(
    r"^(?!\s*//).*\b(MATCH|MERGE|CREATE|SET|DELETE|REMOVE|CALL|LOAD\s+CSV)\b",
    re.IGNORECASE,
)


def rel(path: Path) -> str:
    return path.relative_to(ROOT).as_posix()


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


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def write_csv(path: Path, rows: list[dict[str, Any]], fields: list[str]) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for row in rows:
            writer.writerow({field: row.get(field, "") for field in fields})


def load_patch_groups() -> list[dict[str, Any]]:
    script_text = (SOURCE_DIR / "gen_patches.py").read_text(encoding="utf-8")
    module = ast.parse(script_text)
    patches: list[dict[str, Any]] = []
    for node in module.body:
        if isinstance(node, ast.Assign) and any(getattr(target, "id", None) == "patches" for target in node.targets):
            patches = ast.literal_eval(node.value)
            break
    if not patches:
        raise RuntimeError("No patch group list found in gen_patches.py")
    return patches


def group_id(index: int, patch: dict[str, Any]) -> str:
    ticker = str(patch.get("expected_ticker", "na")).lower().replace(" ", "_")
    case_tail = str(patch.get("case_id", "case")).split("_")[-1]
    return f"pg_{index:03d}_{ticker}_{case_tail}"


def normalize_group(index: int, patch: dict[str, Any]) -> dict[str, Any]:
    group = dict(patch)
    group["patch_group_id"] = group_id(index, patch)
    group["status"] = "preview_only_requires_readonly_validation_and_user_approval"
    group["neo4j_write_performed"] = False
    group["kg_patch_applied"] = False
    return group


def build_crosswalk(
    fact_rows: list[dict[str, Any]], groups: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    fact_by_id = {row["fact_id"]: row for row in fact_rows}
    rows: list[dict[str, Any]] = []
    for group in groups:
        for fact_id in group.get("fact_ids", []):
            fact = fact_by_id.get(fact_id, {})
            rows.append(
                {
                    "patch_group_id": group["patch_group_id"],
                    "case_id": group.get("case_id", fact.get("case_id", "")),
                    "source_id": group.get("source_id", fact.get("source_id", "")),
                    "fact_id": fact_id,
                    "expected_metric_canonical": fact.get("metric_canonical", ""),
                    "expected_year": fact.get("year", ""),
                    "expected_value": fact.get("value", ""),
                    "expected_unit": fact.get("unit", ""),
                    "case_level_root_cause": group.get("root_cause", ""),
                    "missing_or_mismatch_type": fact.get("primary_missing_or_mismatch_type", group.get("missing_or_mismatch_type", "")),
                    "proposed_patch_type": group.get("proposed_patch_type", ""),
                    "risk_level": group.get("risk_level", ""),
                    "requires_manual_approval": True,
                }
            )
    return rows


def write_fact_level_files(fact_rows: list[dict[str, Any]]) -> None:
    write_jsonl(OUT_DIR / "02_fact_level_missing_analysis.jsonl", fact_rows)
    fields = list(fact_rows[0].keys()) if fact_rows else []
    write_csv(OUT_DIR / "03_fact_level_missing_analysis.csv", fact_rows, fields)


def write_group_plan(groups: list[dict[str, Any]]) -> None:
    write_jsonl(OUT_DIR / "04_patch_group_plan.jsonl", groups)


def write_crosswalk(rows: list[dict[str, Any]]) -> None:
    fields = [
        "patch_group_id",
        "case_id",
        "source_id",
        "fact_id",
        "expected_metric_canonical",
        "expected_year",
        "expected_value",
        "expected_unit",
        "case_level_root_cause",
        "missing_or_mismatch_type",
        "proposed_patch_type",
        "risk_level",
        "requires_manual_approval",
    ]
    write_csv(OUT_DIR / "05_patch_group_to_fact_crosswalk.csv", rows, fields)
    write_jsonl(OUT_DIR / "06_patch_group_to_fact_crosswalk.jsonl", rows)


def write_classification_summary(fact_rows: list[dict[str, Any]], groups: list[dict[str, Any]]) -> None:
    fact_counts = Counter(row.get("primary_missing_or_mismatch_type", "unknown") for row in fact_rows)
    root_counts = Counter(group.get("root_cause", "unknown") for group in groups)
    patch_type_counts = Counter(group.get("proposed_patch_type", "unknown") for group in groups)
    rows: list[dict[str, Any]] = []
    for label, count in sorted(fact_counts.items()):
        rows.append(
            {
                "classification_layer": "fact_level_classification",
                "classification": label,
                "count": count,
                "source_of_truth_file": "02_fact_level_missing_analysis.jsonl",
                "mismatch_explanation": "Official fact-level classification used for auditability.",
            }
        )
    for label, count in root_counts.most_common():
        rows.append(
            {
                "classification_layer": "case_level_root_cause",
                "classification": label,
                "count": count,
                "source_of_truth_file": "04_patch_group_plan.jsonl",
                "mismatch_explanation": "Case/group-level diagnosis retained separately; it does not override fact-level labels.",
            }
        )
    for label, count in sorted(patch_type_counts.items()):
        rows.append(
            {
                "classification_layer": "patch_group_count",
                "classification": label,
                "count": count,
                "source_of_truth_file": "04_patch_group_plan.jsonl",
                "mismatch_explanation": "Patch grouping is operational strategy, not fact-level scoring evidence.",
            }
        )
    write_csv(
        OUT_DIR / "08_classification_reconciled_summary.csv",
        rows,
        ["classification_layer", "classification", "count", "source_of_truth_file", "mismatch_explanation"],
    )

    lines = [
        "# Classification Reconciled Summary",
        "",
        "The consolidated package separates fact-level audit labels from case-level operational diagnosis.",
        "",
        "## Fact-Level Classification Counts",
        "",
    ]
    for label, count in fact_counts.most_common():
        lines.append(f"- {label}: {count}")
    lines.extend(["", "## Patch Group Counts", ""])
    for label, count in patch_type_counts.most_common():
        lines.append(f"- {label}: {count}")
    lines.extend(
        [
            "",
            "## Reconciliation Rule",
            "",
            "- `02_fact_level_missing_analysis.jsonl` is the source of truth for 81 fact-level records.",
            "- `04_patch_group_plan.jsonl` is case/group-level remediation strategy.",
            "- Soft matches are priority evidence only; they are not coverage pass and not kg_eval_ready.",
        ]
    )
    (OUT_DIR / "07_classification_reconciled_summary.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def write_safe_queries(groups: list[dict[str, Any]]) -> None:
    quickwins = [g for g in groups if g.get("expected_ticker") in {"LIN", "BAC", "APD", "MDLZ"} or g.get("expected_ticker") == "LND"]
    lines = [
        "# Safe Read-Only Validation Queries",
        "",
        "Use only read-only queries for B1 quick-win validation. Do not execute writes.",
        "",
        "## Global Safety",
        "",
        "Forbidden: CREATE, MERGE, SET, DELETE, REMOVE, DROP, LOAD CSV, CALL dbms, CALL apoc.periodic.",
        "",
        "```cypher",
        "SHOW DATABASES;",
        "```",
        "",
        "```cypher",
        "MATCH (n) RETURN count(n) AS node_count;",
        "```",
        "",
        "```cypher",
        "MATCH (n)",
        "UNWIND labels(n) AS label",
        "RETURN label, count(*) AS count",
        "ORDER BY count DESC;",
        "```",
        "",
        "## Parameterized Candidate Validation",
        "",
        "```cypher",
        "MATCH (c:KGEntity)",
        "WHERE toUpper(toString(c.ticker)) = toUpper($ticker)",
        "   OR toLower(toString(c.name)) CONTAINS toLower($company)",
        "OPTIONAL MATCH (c)-[:HAS_OBSERVATION]->(obs:KGEntity)",
        "OPTIONAL MATCH (obs)-[:OBSERVES_METRIC]->(metric:KGEntity)",
        "OPTIONAL MATCH (obs)-[:OBSERVED_IN_YEAR]->(year_node:KGEntity)",
        "RETURN c, obs, metric, year_node",
        "LIMIT 100;",
        "```",
        "",
        "```cypher",
        "MATCH (obs:KGEntity)",
        "WHERE toLower(toString(obs.metric_canonical)) = toLower($metric)",
        "   OR toLower(toString(obs.metric)) CONTAINS toLower($metric)",
        "   OR toLower(toString(obs.name)) CONTAINS toLower($metric)",
        "RETURN labels(obs) AS labels, keys(obs) AS properties, obs",
        "LIMIT 100;",
        "```",
        "",
        "## Tier 1 Quick-Win Candidates",
        "",
    ]
    for group in quickwins:
        lines.append(
            f"- `{group['patch_group_id']}`: {group.get('expected_ticker')} / {group.get('case_id')} / "
            f"{group.get('proposed_patch_type')} / risk={group.get('risk_level')}"
        )
    lines.extend(
        [
            "",
            "Specific Tier 1 checks: LND-to-LIN ticker correction, BAC missing observations, "
            "APD fiscal period mapping, and MDLZ routing/mapping.",
        ]
    )
    (OUT_DIR / "09_safe_readonly_validation_queries.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def comment_every_line(text: str) -> str:
    commented: list[str] = []
    for line in text.splitlines():
        if line.strip().startswith("//"):
            commented.append(line)
        elif line.strip():
            commented.append(f"// {line}")
        else:
            commented.append("//")
    return "\n".join(commented) + "\n"


def write_disabled_cypher(groups: list[dict[str, Any]]) -> tuple[Path, list[tuple[int, str]]]:
    lines = [
        "DANGEROUS WRITE PATCH PREVIEW - DISABLED",
        "Every line in this file is commented. Do not execute without explicit user approval.",
        "Full evaluation remains locked.",
        "",
    ]
    for group in groups:
        lines.extend(
            [
                f"PATCH_GROUP: {group['patch_group_id']}",
                f"RISK: {group.get('risk_level')}",
                "STATUS: preview_only_not_executable",
                f"CASE_ID: {group.get('case_id')}",
                f"PATCH_TYPE: {group.get('proposed_patch_type')}",
                str(group.get("proposed_node_relationship_pattern", "Manual review only")),
                "DO NOT EXECUTE WITHOUT USER APPROVAL",
                "",
            ]
        )
    disabled_text = comment_every_line("\n".join(lines))
    path = OUT_DIR / "10_dangerous_write_patch_preview.disabled.cypher"
    path.write_text(disabled_text, encoding="utf-8")
    violations: list[tuple[int, str]] = []
    for line_no, line in enumerate(disabled_text.splitlines(), start=1):
        if FORBIDDEN_UNCOMMENTED_PATTERN.search(line):
            violations.append((line_no, line))
    return path, violations


def write_safety_scan(disabled_path: Path, violations: list[tuple[int, str]], crosswalk_rows: list[dict[str, Any]]) -> None:
    decision = "PASS" if not violations else "NO_GO"
    lines = [
        "# Cypher Safety Scan Report",
        "",
        f"- File scanned: `{rel(disabled_path)}`",
        f"- Decision: `{decision}`",
        "- Rule: no uncommented MATCH / MERGE / CREATE / SET / DELETE / REMOVE / CALL / LOAD CSV lines.",
        f"- Crosswalk rows: {len(crosswalk_rows)}",
        "",
    ]
    if violations:
        lines.append("## Violations")
        for line_no, line in violations:
            lines.append(f"- Line {line_no}: `{line}`")
    else:
        lines.append("No uncommented write/read Cypher statements were found in the disabled preview.")
    (OUT_DIR / "11_cypher_safety_scan_report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def write_approval_request(groups: list[dict[str, Any]]) -> None:
    quickwins = [g for g in groups if g.get("expected_ticker") in {"LIN", "BAC", "APD", "MDLZ"} or g.get("expected_ticker") == "LND"]
    lines = [
        "# Approval Request For Phase B1 Quick Wins",
        "",
        "Current request is for read-only validation only. No KG patch is approved by this file.",
        "",
        "## Phase Plan",
        "",
        "- B0 artifact safety normalization: completed by this consolidated package.",
        "- B1 quick-win read-only validation: next recommended step.",
        "- B2 user approval request: required after B1 evidence.",
        "- B3 approved patch only: forbidden until explicit approval.",
        "- B4 read-only coverage rerun: after approved patch only.",
        "- B5 expanded partial eval decision: after coverage improves.",
        "",
        "## Quick-Win Validation Candidates",
        "",
    ]
    for group in quickwins:
        lines.append(f"- `{group['patch_group_id']}`: {group.get('expected_ticker')} / {group.get('proposed_patch_type')}")
    lines.extend(
        [
            "",
            "To approve patch execution later, the user must explicitly approve a concrete patch scope. "
            "This package does not grant patch approval.",
        ]
    )
    (OUT_DIR / "12_approval_request_for_phase_b1_quickwins.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def write_go_no_go(violations: list[tuple[int, str]], crosswalk_rows: list[dict[str, Any]]) -> None:
    decision = "NO_GO" if violations or len(crosswalk_rows) != 81 else "needs_manual_review"
    lines = [
        "# Go / No-Go After Consolidation",
        "",
        f"Decision: `{decision}`",
        "",
        "## Current State",
        "",
        "- Full evaluation: locked",
        "- Neo4j write: not performed",
        "- KG patch: not applied",
        "- Model/API calls: not performed",
        "- Fact-to-group crosswalk: " + ("complete" if len(crosswalk_rows) == 81 else "incomplete"),
        "- Dangerous Cypher safety scan: " + ("pass" if not violations else "fail"),
        "",
        "## Required Before Patch",
        "",
        "1. Run B1 quick-win read-only validation.",
        "2. Resolve exact KGEntity node ids and relationship targets.",
        "3. Request explicit user approval for a concrete patch scope.",
        "4. Apply only approved patches.",
        "5. Rerun read-only coverage before any expanded evaluation decision.",
    ]
    (OUT_DIR / "13_go_no_go_after_consolidation.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def write_report(
    fact_rows: list[dict[str, Any]],
    groups: list[dict[str, Any]],
    crosswalk_rows: list[dict[str, Any]],
    violations: list[tuple[int, str]],
) -> None:
    now = datetime.now(timezone.utc).isoformat()
    risk_counts = Counter(group.get("risk_level", "unknown") for group in groups)
    split_counts = Counter(group.get("split", "unknown") for group in groups)
    lines = [
        "# Consolidated Round 3 Backlog Remediation Report",
        "",
        f"- Created at: {now}",
        f"- Source fact-level package: `{rel(SOURCE_DIR)}`",
        f"- Frozen partial eval run: `{rel(PARTIAL_DIR)}`",
        f"- Claim boundary: `{rel(PARTIAL_DIR / 'final_claim_boundary.md')}`",
        "",
        "## Safety Summary",
        "",
        "- Full evaluation executed: false",
        "- Dry-run evaluation executed: false",
        "- Neo4j write performed: false",
        "- KG patch applied: false",
        "- Model/API call performed: false",
        "- Gemini active path used: false",
        "",
        "## Scope",
        "",
        f"- Backlog facts: {len(fact_rows)}",
        f"- Patch groups: {len(groups)}",
        f"- Crosswalk rows: {len(crosswalk_rows)}",
        "- Current gate: `needs_manual_review`",
        "",
        "## Role Separation",
        "",
        "- Codex fact-level outputs are the audit source of truth.",
        "- Case/group-level patch strategy is retained separately as operational diagnosis.",
        "- Soft matches support prioritization only; they do not imply coverage pass.",
        "",
        "## Patch Group Risk Counts",
        "",
    ]
    for label, count in risk_counts.most_common():
        lines.append(f"- {label}: {count}")
    lines.extend(["", "## Patch Group Split Counts", ""])
    for label, count in split_counts.most_common():
        lines.append(f"- {label}: {count}")
    lines.extend(
        [
            "",
            "## Recommended Strategy",
            "",
            "Adopt Option B as a phased and guarded path:",
            "",
            "- B0 artifact safety normalization: completed.",
            "- B1 quick-win read-only validation: next.",
            "- B2 user approval request: after validation evidence.",
            "- B3 approved patch only.",
            "- B4 read-only coverage rerun.",
            "- B5 expanded partial eval decision.",
            "",
            "## Safety Scan",
            "",
            f"- Dangerous preview disabled file violations: {len(violations)}",
            "- Gate is not upgraded to GO or patch_ready.",
        ]
    )
    (OUT_DIR / "01_consolidated_backlog_remediation_report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    fact_rows = read_jsonl(SOURCE_DIR / "backlog_missing_fact_analysis.jsonl")
    groups = [normalize_group(i, patch) for i, patch in enumerate(load_patch_groups(), start=1)]
    crosswalk_rows = build_crosswalk(fact_rows, groups)

    write_fact_level_files(fact_rows)
    write_group_plan(groups)
    write_crosswalk(crosswalk_rows)
    write_classification_summary(fact_rows, groups)
    write_safe_queries(groups)
    disabled_path, violations = write_disabled_cypher(groups)
    write_safety_scan(disabled_path, violations, crosswalk_rows)
    write_approval_request(groups)
    write_go_no_go(violations, crosswalk_rows)
    write_report(fact_rows, groups, crosswalk_rows, violations)

    result = {
        "out_dir": rel(OUT_DIR),
        "fact_rows": len(fact_rows),
        "patch_groups": len(groups),
        "crosswalk_rows": len(crosswalk_rows),
        "cypher_safety_violations": len(violations),
        "decision": "needs_manual_review" if not violations and len(crosswalk_rows) == 81 else "NO_GO",
        "full_eval_executed": False,
        "neo4j_write_performed": False,
        "kg_patch_applied": False,
        "model_api_called": False,
    }
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
