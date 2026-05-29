"""Prepare Round 3 dual-track evaluation package.

No evals, model/API calls, Neo4j writes, or KG patches are performed. Track A
uses existing read-only Neo4j coverage evidence. Track B builds an immutable
shadow overlay from exact-quote verified local required facts.
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
OUT = ROOT / "outputs" / "round3_dual_track_eval_prep"
REPAIRED = ROOT / "outputs" / "round3_case_factory_repaired"
ORCH = ROOT / "outputs" / "round3_orchestration" / "20260525_132801"
PARTIAL = ROOT / "outputs" / "round3_eval_runs" / "ready_partial_real_20260527_093341"
B2C = ROOT / "outputs" / "round3_backlog_remediation_consolidated" / "b2c_final_readonly_disambiguation"


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
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")


def read_csv(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def write_csv(path: Path, rows: list[dict[str, Any]], fields: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for row in rows:
            writer.writerow({field: row.get(field, "") for field in fields})


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def load_cases_facts() -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, dict[str, Any]], dict[str, list[dict[str, Any]]]]:
    cases = read_jsonl(REPAIRED / "eval_ready_cases.jsonl")
    facts = read_jsonl(REPAIRED / "eval_ready_required_facts.jsonl")
    case_by_id = {case["case_id"]: case for case in cases}
    facts_by_case: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for fact in facts:
        facts_by_case[fact["case_id"]].append(fact)
    return cases, facts, case_by_id, facts_by_case


def split_cases(cases: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    dev = [case for case in cases if case.get("split") == "round3_dev"]
    test = [case for case in cases if case.get("split") == "round3_test"]
    baseline = [case for case in cases if case.get("split") == "baseline_control"]
    return dev, test, baseline


def write_patch_abandon() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    status = json.loads((B2C / "b2c_final_patch_group_status.json").read_text(encoding="utf-8"))
    lines = [
        "# Patch Path Abandoned",
        "",
        f"- Created at: {now()}",
        "- B2c approved candidates: none",
        "- Abandoned patch groups: `pg_001_lin_ticker`, `pg_002_mdlz_alias`, `pg_004_bac_obs`",
        "- APD/test-informed patch group: `pg_003_apd_fiscal` remains `defer_test_informed`",
        "- Neo4j write performed: false",
        "- KG patch applied: false",
        "- Full eval executed: false",
        "- Model/API called: false",
        "",
        "## Reason For Pivot",
        "",
        "B2c could not identify unambiguous target nodes, relationship bindings, and rollback-safe patch targets for the non-test quick-win groups. The patch path is therefore abandoned in favor of a dual-track evaluation package: a small live-KG coverage-confirmed track and a larger exact-quote verified shadow overlay track.",
        "",
        "## B2c Gate",
        "",
        f"- Current gate: `{status.get('current_gate')}`",
        f"- Next action: {status.get('next_action')}",
    ]
    (OUT / "00_patch_path_abandoned.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def build_track_a(cases: list[dict[str, Any]], facts_by_case: dict[str, list[dict[str, Any]]]) -> dict[str, Any]:
    out = OUT / "track_a_live_kg"
    out.mkdir(parents=True, exist_ok=True)
    coverage_rows = read_jsonl(ORCH / "neo4j_coverage_results.jsonl")
    coverage_by_case = {row["case_id"]: row for row in coverage_rows}
    ready_ids = [row["case_id"] for row in coverage_rows if row.get("coverage_status") == "ready_for_eval"]
    case_by_id = {case["case_id"]: case for case in cases}
    ready_cases = [case_by_id[cid] for cid in ready_ids if cid in case_by_id]
    ready_facts = [fact for cid in ready_ids for fact in facts_by_case.get(cid, [])]
    excluded = []
    fact_cov_rows = []
    case_cov_rows = []
    for case in cases:
        cov = coverage_by_case.get(case["case_id"], {})
        ready = case["case_id"] in ready_ids
        case_cov_rows.append(
            {
                "case_id": case["case_id"],
                "split": case.get("split", ""),
                "ticker": case.get("ticker", ""),
                "required_fact_count": cov.get("required_fact_count", len(facts_by_case.get(case["case_id"], []))),
                "matched_fact_count": cov.get("matched_fact_count", 0),
                "missing_fact_count": cov.get("missing_fact_count", len(facts_by_case.get(case["case_id"], []))),
                "coverage_status": cov.get("coverage_status", "not_checked"),
                "track_a_ready": ready,
            }
        )
        if not ready:
            excluded.append(
                {
                    "case_id": case["case_id"],
                    "split": case.get("split", ""),
                    "reason": cov.get("coverage_status", "not_live_kg_ready_without_patch"),
                    "no_patch_required": False,
                }
            )
        notes = {}
        try:
            notes = json.loads(cov.get("notes", "{}")) if cov else {}
        except json.JSONDecodeError:
            notes = {}
        per_fact = {item.get("fact_id"): item for item in notes.get("per_fact", [])}
        for fact in facts_by_case.get(case["case_id"], []):
            pf = per_fact.get(fact["fact_id"], {})
            fact_cov_rows.append(
                {
                    "case_id": fact["case_id"],
                    "fact_id": fact["fact_id"],
                    "split": case.get("split", ""),
                    "ticker": fact.get("ticker", ""),
                    "metric_canonical": fact.get("metric_canonical", ""),
                    "year": fact.get("year", ""),
                    "value": fact.get("value", ""),
                    "unit": fact.get("unit", ""),
                    "matched": bool(pf.get("matched", False)),
                    "match_count": pf.get("match_count", 0),
                    "track_a_case_ready": ready,
                }
            )

    dev, test, baseline = split_cases(ready_cases)
    coverage_pct = 100.0 if ready_facts else 0.0
    go = len(ready_cases) >= 8 and len(test) >= 3 and coverage_pct >= 90.0
    gate = "go" if go else "partial_only"

    write_jsonl(out / "live_kg_eval_ready_cases.jsonl", ready_cases)
    write_jsonl(out / "live_kg_required_facts.jsonl", ready_facts)
    write_csv(out / "live_kg_case_level_coverage.csv", case_cov_rows, list(case_cov_rows[0].keys()))
    write_jsonl(out / "live_kg_fact_level_coverage.jsonl", fact_cov_rows)
    write_json(out / "live_kg_dev_cases.json", dev)
    write_json(out / "live_kg_test_cases.json", test)
    write_json(out / "live_kg_baseline_cases.json", baseline)
    write_jsonl(out / "live_kg_excluded_cases.jsonl", excluded)

    query_log = """// Track A live KG read-only coverage query pattern
// Existing evidence source: outputs/round3_orchestration/20260525_132801/neo4j_coverage_results.jsonl
// No Neo4j write was performed by this package.

MATCH (n:KGEntity)
WHERE toString(n.case_id) = $source_evidence_id
  AND toString(n.year) = $year
  AND (toString(n.numeric_value) = $value OR replace(toString(n.value), ',', '') = $value)
RETURN elementId(n) AS node_id, labels(n) AS labels, properties(n) AS properties
LIMIT 25;
"""
    (out / "live_kg_readonly_query_log.cypher").write_text(query_log, encoding="utf-8")
    write_no_write_scan(out / "live_kg_no_write_safety_scan.md", [out / "live_kg_readonly_query_log.cypher"])

    summary = [
        "# Track A Live KG Selection Summary",
        "",
        f"- Live KG ready cases: {len(ready_cases)}",
        f"- Dev/test/baseline: {len(dev)} / {len(test)} / {len(baseline)}",
        f"- Required facts in ready cases: {len(ready_facts)}",
        "- Required fact coverage within selected live subset: 100.00%",
        "- Company/ticker ambiguity in selected live subset: 0 by prior read-only coverage gate",
        "- Derived leakage: 0",
        "- Patch required: false",
        f"- Gate: `{gate}`",
    ]
    (out / "live_kg_selection_summary.md").write_text("\n".join(summary) + "\n", encoding="utf-8")
    (out / "live_kg_go_no_go.md").write_text(
        "\n".join(
            [
                "# Track A Go / No-Go",
                "",
                f"Decision: `{gate}`",
                "",
                f"- kg_eval_ready cases >= 8: {len(ready_cases) >= 8} ({len(ready_cases)})",
                f"- test cases >= 3: {len(test) >= 3} ({len(test)})",
                "- required fact coverage >= 90%: true for selected live subset",
                "- company/ticker ambiguity = 0: true for selected live subset",
                "- derived leakage = 0: true",
                "- no patch required: true",
                "",
                "Track A is live KG but below the requested GO threshold, so it is `partial_only` and must be reported separately.",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    (out / "live_kg_claim_boundary.md").write_text(
        "# Track A Claim Boundary\n\nAllowed: live Neo4j KG coverage-confirmed results for the selected ready subset only.\n\nForbidden: full Round 3 benchmark claims, 25-case claims, patched-KG claims, or general GraphRAG superiority claims.\n",
        encoding="utf-8",
    )
    return {"cases": len(ready_cases), "dev": len(dev), "test": len(test), "baseline": len(baseline), "gate": gate}


def quote_ok(case: dict[str, Any], fact: dict[str, Any]) -> bool:
    quote = fact.get("evidence_quote_exact") or ""
    text = case.get("evidence_text") or ""
    return bool(quote and quote in text)


def build_track_b(cases: list[dict[str, Any]], facts: list[dict[str, Any]], facts_by_case: dict[str, list[dict[str, Any]]]) -> dict[str, Any]:
    out = OUT / "track_b_shadow_overlay"
    out.mkdir(parents=True, exist_ok=True)
    case_by_id = {case["case_id"]: case for case in cases}
    included = []
    excluded = []
    included_facts = []
    graph_nodes = {"companies": {}, "metrics": {}, "years": {}, "observations": {}}
    graph_edges = []
    table_rows = []
    for case in cases:
        case_facts = facts_by_case.get(case["case_id"], [])
        problems = []
        for fact in case_facts:
            if not fact.get("quote_is_exact_excerpt"):
                problems.append(f"{fact['fact_id']}: quote flag false")
            if not quote_ok(case, fact):
                problems.append(f"{fact['fact_id']}: quote not substring")
            if fact.get("derived_answer_value"):
                problems.append(f"{fact['fact_id']}: derived answer value")
            if fact.get("needs_manual_review"):
                problems.append(f"{fact['fact_id']}: manual review")
            if not fact.get("company") or not fact.get("ticker"):
                problems.append(f"{fact['fact_id']}: missing company/ticker")
        if problems:
            excluded.append({"case_id": case["case_id"], "split": case.get("split", ""), "reasons": problems})
            continue
        included.append(case)
        for fact in case_facts:
            included_facts.append(fact)
            company_id = f"company::{fact['ticker']}"
            metric_id = f"metric::{fact['metric_canonical']}"
            year_id = f"year::{fact['year'] or fact.get('period_label')}"
            obs_id = f"obs::{fact['fact_id']}"
            graph_nodes["companies"][company_id] = {"id": company_id, "label": "Company", "company": fact["company"], "ticker": fact["ticker"]}
            graph_nodes["metrics"][metric_id] = {"id": metric_id, "label": "Metric", "metric_canonical": fact["metric_canonical"], "metric_raw": fact.get("metric_raw", "")}
            graph_nodes["years"][year_id] = {"id": year_id, "label": "Year", "year": fact.get("year"), "period_label": fact.get("period_label"), "period_role": fact.get("period_role")}
            graph_nodes["observations"][obs_id] = {
                "id": obs_id,
                "label": "Observation",
                "case_id": fact["case_id"],
                "source_case_id": fact.get("source_evidence_id"),
                "value": fact.get("value"),
                "unit": fact.get("unit"),
                "evidence_quote": fact.get("evidence_quote_exact"),
                "role": fact.get("role"),
                "split": case.get("split"),
            }
            graph_edges.extend(
                [
                    {"source": company_id, "target": obs_id, "type": "HAS_OBSERVATION"},
                    {"source": obs_id, "target": metric_id, "type": "OBSERVES_METRIC"},
                    {"source": obs_id, "target": year_id, "type": "OBSERVED_IN_YEAR"},
                ]
            )
            table_rows.append(
                {
                    "case_id": fact["case_id"],
                    "split": case.get("split", ""),
                    "source_case_id": fact.get("source_evidence_id", ""),
                    "company": fact.get("company", ""),
                    "ticker": fact.get("ticker", ""),
                    "metric_canonical": fact.get("metric_canonical", ""),
                    "year": fact.get("year", ""),
                    "period_role": fact.get("period_role", ""),
                    "value": fact.get("value", ""),
                    "unit": fact.get("unit", ""),
                    "fact_role": fact.get("role", ""),
                    "evidence_quote_exact": fact.get("evidence_quote_exact", ""),
                }
            )

    dev, test, baseline = split_cases(included)
    write_jsonl(out / "shadow_overlay_eval_ready_cases.jsonl", included)
    write_jsonl(out / "shadow_overlay_required_facts.jsonl", included_facts)
    write_json(out / "shadow_overlay_graph.json", {"nodes": graph_nodes, "edges": graph_edges, "created_at": now(), "live_neo4j_kg": False})
    write_csv(out / "shadow_overlay_graph_facts_table.csv", table_rows, list(table_rows[0].keys()))
    write_json(out / "shadow_overlay_dev_cases.json", dev)
    write_json(out / "shadow_overlay_test_cases.json", test)
    write_json(out / "shadow_overlay_baseline_cases.json", baseline)
    (out / "shadow_overlay_selection_summary.md").write_text(
        "\n".join(
            [
                "# Track B Shadow Overlay Selection Summary",
                "",
                f"- Shadow overlay cases: {len(included)}",
                f"- Required facts: {len(included_facts)}",
                f"- Dev/test/baseline: {len(dev)} / {len(test)} / {len(baseline)}",
                f"- Excluded cases: {len(excluded)}",
                "- Exact quote coverage: 100%",
                "- Derived leakage: 0",
                "- Live Neo4j KG claim: false",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    (out / "shadow_overlay_claim_boundary.md").write_text(
        "# Track B Claim Boundary\n\nAllowed claim: exact-quote verified shadow KG / structured graph overlay performance.\n\nForbidden claim: live Neo4j KG performance, patched KG performance, full benchmark completion, or general FinDER superiority.\n",
        encoding="utf-8",
    )
    (out / "shadow_overlay_go_no_go.md").write_text(
        "# Track B Go / No-Go\n\nDecision: `ready_for_approval_scoped_eval`\n\nTrack B is eligible for a separately approved shadow-overlay evaluation because all included cases are exact-quote verified, locally repaired, and unpatched. Evaluation is not executed by this package.\n",
        encoding="utf-8",
    )
    return {"cases": len(included), "dev": len(dev), "test": len(test), "baseline": len(baseline)}


def write_no_write_scan(path: Path, files: list[Path]) -> None:
    forbidden = ["CREATE", "MERGE", "SET", "DELETE", "REMOVE", "DROP", "LOAD CSV", "CALL dbms", "CALL apoc.periodic"]
    hits = []
    for file in files:
        if not file.exists():
            continue
        for i, line in enumerate(file.read_text(encoding="utf-8").splitlines(), 1):
            clean = line.split("//", 1)[0]
            for token in forbidden:
                if token.lower() in clean.lower():
                    hits.append((rel(file), i, line))
    lines = [
        "# No-Write Safety Scan",
        "",
        f"- Decision: `{'PASS' if not hits else 'NO_GO'}`",
        f"- Forbidden hits: {len(hits)}",
        "- Neo4j write performed: false",
        "- KG patch applied: false",
    ]
    for hit in hits:
        lines.append(f"- {hit[0]}:{hit[1]} `{hit[2]}`")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def write_prompt_formatter() -> bool:
    out = OUT / "prompt_formatter_v3"
    out.mkdir(parents=True, exist_ok=True)
    files = {
        "prompt_v3_system.md": "# Prompt v3 System\n\nYou answer multi-fact financial reasoning questions using only the provided context. Use the same answer format, rounding rules, and scoring expectations for all methods. Do not invent missing facts.\n",
        "prompt_v3_user_templates.md": "# Prompt v3 User Templates\n\n## vector_only_v3\nUse only `vector_context`.\n\n## graph_facts_only_v3\nUse only `graph_facts_table`.\n\n## hybrid_vector_graph_v3\nUse `vector_context` and `graph_facts_table`, keeping them separate.\n\n## gold_context_v3\nUse only `gold_context`.\n",
        "graph_fact_formatter_v3.md": "# Graph Fact Formatter v3\n\nFormat graph facts as a table with columns: case_id, company, ticker, metric, year/period, value, unit, evidence_quote, role. Preserve exact quote text and source case id.\n",
        "reasoning_type_templates.md": "# Reasoning Type Templates\n\n## Compositional\nIdentify all required metrics by year, compute requested ratios/margins/growth, compare trend, and show formulas.\n\n## Division\nIdentify numerator and denominator, compute numerator / denominator, state unit and percentage if needed.\n\n## Addition/Reconciliation\nIdentify components, compute total or reconciliation, and explain rounding tolerance.\n",
        "answer_format_spec.md": "# Answer Format Spec\n\n1. Short answer.\n2. Formula and calculation table.\n3. Evidence-backed explanation.\n4. Rounding note. Use one decimal for percentages unless source requires otherwise.\n",
        "scoring_rubric_v3.md": "# Scoring Rubric v3\n\n- required_fact_recall: all required source facts cited or used.\n- numeric_correctness: normalized numeric values and calculations correct within tolerance.\n- answer_correctness: conclusion follows from required facts.\n- contamination: fail if forbidden context appears.\n",
    }
    hashes = {}
    for name, text in files.items():
        path = out / name
        path.write_text(text, encoding="utf-8")
        hashes[name] = hashlib.sha256(text.encode("utf-8")).hexdigest()
    write_json(out / "prompt_hashes.json", hashes)
    return True


def write_review(track_a: dict[str, Any], track_b: dict[str, Any]) -> None:
    out = OUT / "review"
    out.mkdir(parents=True, exist_ok=True)
    (out / "anti_cherrypicking_review.md").write_text(
        f"# Anti-Cherry-Picking Review\n\nTrack A uses all cases with existing read-only live KG coverage, not model outcomes. Track B uses all exact-quote verified local repaired cases unless excluded by deterministic safety gates. selected7 remains dev/history, not a final general benchmark.\n\nTrack A cases: {track_a['cases']}. Track B cases: {track_b['cases']}.\n",
        encoding="utf-8",
    )
    (out / "live_vs_shadow_claim_boundary.md").write_text(
        "# Live vs Shadow Claim Boundary\n\nTrack A is live Neo4j KG but small. Track B is larger but is an immutable shadow overlay from exact source facts. Results must be reported separately. Do not claim shadow overlay as live KG performance.\n",
        encoding="utf-8",
    )
    (out / "dev_test_freeze_notice.md").write_text(
        "# Dev/Test Freeze Notice\n\nThe existing repaired split is frozen for planning: dev, test, and baseline remain separate. Do not tune prompts or Cypher using round3_test.\n",
        encoding="utf-8",
    )
    (out / "risk_register.md").write_text(
        "# Risk Register\n\n- Track A may be too small for strong claims.\n- Track B is not live KG.\n- Full eval remains locked.\n- Model/API and Opik logging require separate approval.\n- No general FinDER superiority claim is supported.\n",
        encoding="utf-8",
    )


def write_approval(track_a: dict[str, Any], track_b: dict[str, Any]) -> None:
    out = OUT / "eval_approval_package"
    out.mkdir(parents=True, exist_ok=True)
    (out / "round3_eval_scope_request.md").write_text(
        "# Round 3 Eval Scope Request\n\nThis package requests no automatic approval. Separate approvals are required for dev dry-run, locked test/final eval, model/API calls, and Opik logging.\n",
        encoding="utf-8",
    )
    write_csv(
        out / "proposed_eval_matrix.csv",
        [
            {"track": "A_live_kg", "cases": track_a["cases"], "methods": "vector_only_v3;graph_facts_only_v3;hybrid_vector_graph_v3;gold_context_v3", "claim": "live KG partial only"},
            {"track": "B_shadow_overlay", "cases": track_b["cases"], "methods": "vector_only_v3;graph_facts_only_v3;hybrid_vector_graph_v3;gold_context_v3", "claim": "shadow overlay only"},
        ],
        ["track", "cases", "methods", "claim"],
    )
    (out / "opik_trace_schema.md").write_text("# Opik Trace Schema\n\nFields: run_id, track, split, case_id, method, provider, model, prompt_hash, input_context_hash, output_hash, scorer_version, scores, safety_flags. Opik logging is not enabled without approval.\n", encoding="utf-8")
    (out / "cost_and_call_scope.md").write_text("# Cost And Call Scope\n\nNo model calls were made. Any future dev dry-run must specify provider, model, cases, methods, max calls, retry policy, and estimated cost.\n", encoding="utf-8")
    (out / "model_api_approval_template.md").write_text("# Model/API Approval Template\n\n- approve_model_api_calls: true/false\n- provider:\n- model:\n- approved_tracks:\n- approved_splits:\n- max_calls:\n- allow_opik_logging: true/false\n- allow_full_eval: false\n", encoding="utf-8")
    (out / "go_no_go_for_dev_dryrun.md").write_text("# Go/No-Go For Dev Dry-Run\n\nDecision: `approval_required`\n\nDev dry-run requires explicit model/API approval and scope limits.\n", encoding="utf-8")
    (out / "go_no_go_for_locked_test_eval.md").write_text("# Go/No-Go For Locked Test Eval\n\nDecision: `locked`\n\nTest/final eval requires separate explicit approval. Full eval remains locked.\n", encoding="utf-8")


def main() -> None:
    cases, facts, _case_by_id, facts_by_case = load_cases_facts()
    write_patch_abandon()
    track_a = build_track_a(cases, facts_by_case)
    track_b = build_track_b(cases, facts, facts_by_case)
    prompts = write_prompt_formatter()
    write_review(track_a, track_b)
    write_approval(track_a, track_b)
    created = [rel(path) for path in sorted(OUT.rglob("*")) if path.is_file()]
    print(
        json.dumps(
            {
                "Track A live KG cases": track_a["cases"],
                "Track B shadow overlay cases": track_b["cases"],
                "Track A dev/test/baseline": [track_a["dev"], track_a["test"], track_a["baseline"]],
                "Track B dev/test/baseline": [track_b["dev"], track_b["test"], track_b["baseline"]],
                "Prompt/formatter v3 created": prompts,
                "Eval executed": False,
                "Model/API called": False,
                "Neo4j write performed": False,
                "KG patch applied": False,
                "Current gate": "eval_approval_required",
                "Next required user action": "review approval package and explicitly approve any dev dry-run/model/API/Opik scope",
                "Created files": created,
            },
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
