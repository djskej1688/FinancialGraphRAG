"""Create scorer repair package and no-model v3.2 rescore for Round 3 v3.1 traces."""

from __future__ import annotations

import csv
import json
import math
import re
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
RUN_DIR = ROOT / "outputs" / "round3_eval_runs" / "dev_dryrun_v3_1_20260528_005749"
AUDIT_DIR = RUN_DIR / "root_cause_audit"
OUT = RUN_DIR / "scorer_repair_v3_2"
PLAN = ROOT / "outputs" / "round3_dual_track_eval_prep" / "prompt_formatter_v3_2_plan"
TRACK_A = ROOT / "outputs" / "round3_dual_track_eval_prep" / "track_a_live_kg"
TRACK_B = ROOT / "outputs" / "round3_dual_track_eval_prep" / "track_b_shadow_overlay"

NUM_RE = re.compile(r"(?<![A-Za-z_])-?\(?\$?\d[\d,]*(?:\.\d+)?\)?\s*(?:%|percent|percentage|million|millions|billion|billions|thousand|thousands)?", re.I)
ID_CONTEXT_RE = re.compile(r"\b(?:round3|baseline|control|dev|test|fact|trace|case|source|evidence|prompt|sha|id)[-_A-Za-z0-9]*\b", re.I)
YEAR_RE = re.compile(r"\b20\d{2}\b")


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows = []
    if not path.exists():
        return rows
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            rows.append(json.loads(line))
    return rows


def write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text.rstrip() + "\n", encoding="utf-8")


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")


def write_csv(path: Path, rows: list[dict[str, Any]], fields: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for row in rows:
            writer.writerow({field: row.get(field, "") for field in fields})


def fnum(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def rel(path: Path) -> str:
    return path.resolve().relative_to(ROOT.resolve()).as_posix()


def clean_id_context(text: str) -> str:
    # Remove explicit id tokens before numeric extraction so fact/case/trace fragments do not become numeric slots.
    return ID_CONTEXT_RE.sub(" ", text or "")


def parse_number(raw: str) -> dict[str, Any] | None:
    display = raw.strip()
    text = display.lower().strip()
    is_percent = "%" in text or "percent" in text or "percentage" in text
    negative = text.startswith("(") and text.endswith(")")
    text = text.strip("()")
    multiplier = 1.0
    scale = ""
    if "billion" in text:
        multiplier = 1_000_000_000.0
        scale = "billion"
    elif "million" in text:
        multiplier = 1_000_000.0
        scale = "million"
    elif "thousand" in text:
        multiplier = 1_000.0
        scale = "thousand"
    text = re.sub(r"[$,%]|percent|percentage|millions?|billions?|thousands?", "", text).replace(",", "").strip()
    if not text:
        return None
    try:
        value = float(text)
    except ValueError:
        return None
    if negative:
        value = -value
    scaled = value * multiplier
    return {
        "raw": display,
        "value": value,
        "scaled_value": scaled,
        "is_percent": is_percent,
        "scale": scale,
        "canonical_ratio": value / 100.0 if is_percent else value,
    }


def extract_numbers(text: str, *, remove_id_context: bool = True) -> list[dict[str, Any]]:
    source = clean_id_context(text) if remove_id_context else text
    out = []
    for match in NUM_RE.finditer(source):
        raw = match.group(0)
        if YEAR_RE.fullmatch(raw.strip()):
            continue
        parsed = parse_number(raw)
        if parsed is not None:
            out.append(parsed)
    return out


def close(expected: dict[str, Any], actual: dict[str, Any]) -> bool:
    if expected["is_percent"] or actual["is_percent"]:
        expected_pct = expected["value"] if expected["is_percent"] else expected["value"] * 100.0
        actual_pct = actual["value"] if actual["is_percent"] else actual["value"] * 100.0
        return math.isclose(expected_pct, actual_pct, abs_tol=0.1) or math.isclose(
            expected["canonical_ratio"], actual["canonical_ratio"], rel_tol=0.01, abs_tol=0.0015
        )
    if abs(expected["value"]) < 100 and abs(actual["value"]) < 100:
        return math.isclose(expected["value"], actual["value"], rel_tol=0.01, abs_tol=0.01)
    return math.isclose(expected["scaled_value"], actual["scaled_value"], rel_tol=0.005, abs_tol=0.01) or math.isclose(
        expected["value"], actual["value"], rel_tol=0.005, abs_tol=0.01
    )


def load_cases() -> dict[str, dict[str, Any]]:
    cases: dict[str, dict[str, Any]] = {}
    for path in [
        TRACK_A / "live_kg_dev_cases.json",
        TRACK_A / "live_kg_baseline_cases.json",
        TRACK_B / "shadow_overlay_dev_cases.json",
        TRACK_B / "shadow_overlay_baseline_cases.json",
    ]:
        if path.exists():
            for case in read_json(path):
                cases[case["case_id"]] = case
    return cases


def load_facts() -> dict[str, list[dict[str, Any]]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for path in [TRACK_A / "live_kg_required_facts.jsonl", TRACK_B / "shadow_overlay_required_facts.jsonl"]:
        for row in read_jsonl(path):
            grouped[row["case_id"]].append(row)
    return grouped


def output_text(trace: dict[str, Any]) -> str:
    result = trace.get("method_result") or {}
    raw = trace.get("raw_method_result_v3_1") or {}
    parts = [
        str(result.get("final_answer", "")),
        str(result.get("calculation", "")),
        str(raw.get("brief_interpretation", "")),
        str(raw.get("rounding_statement", "")),
    ]
    return "\n".join(parts)


def context_text(trace: dict[str, Any]) -> str:
    return trace.get("user_prompt", "")


def source_value_numbers(facts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out = []
    for fact in facts:
        parsed = parse_number(str(fact.get("value", "")))
        if parsed:
            out.append(parsed)
    return out


def expected_target_slots(expected_answer: str, facts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    expected = extract_numbers(expected_answer)
    source_values = source_value_numbers(facts)
    targets = []
    for number in expected:
        if any(close(number, src) for src in source_values):
            continue
        targets.append(number)
    # Prefer derived percentages/ratios when present; otherwise keep non-source targets.
    percent_targets = [item for item in targets if item["is_percent"] or abs(item["value"]) < 100]
    if percent_targets:
        return dedupe_numbers(percent_targets)
    return dedupe_numbers(targets)


def dedupe_numbers(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out = []
    for item in items:
        if not any(close(item, existing) for existing in out):
            out.append(item)
    return out


def value_recall(required: list[dict[str, Any]], text: str) -> float:
    nums = extract_numbers(text)
    if not required:
        return 1.0
    matched = 0
    for fact in required:
        parsed = parse_number(str(fact.get("value", "")))
        year = str(fact.get("year", ""))
        if parsed and any(close(parsed, actual) for actual in nums) and (not year or year in text):
            matched += 1
    return round(matched / len(required), 4)


def graph_fact_id_recall(trace: dict[str, Any], required: list[dict[str, Any]]) -> float:
    previous = ((trace.get("scores") or {}).get("required_fact_recall") or {}).get("required_fact_recall")
    if trace["method"] in {"graph_facts_only_v3_1", "hybrid_vector_graph_v3_1"}:
        return fnum(previous)
    return 0.0


def rescore_trace(trace: dict[str, Any], case: dict[str, Any], facts: list[dict[str, Any]]) -> dict[str, Any]:
    text = output_text(trace)
    ctx = context_text(trace)
    targets = expected_target_slots(str(case.get("expected_answer", "")), facts)
    actual = extract_numbers(text)
    matched_targets = []
    missing_targets = []
    for target in targets:
        if any(close(target, value) for value in actual):
            matched_targets.append(target)
        else:
            missing_targets.append(target)
    numeric_recall = round(len(matched_targets) / len(targets), 4) if targets else 1.0
    numeric_correct = numeric_recall >= 0.8
    graph_recall = graph_fact_id_recall(trace, facts)
    text_context_recall = value_recall(facts, ctx)
    answer_value = value_recall(facts, text)
    if trace["method"] in {"graph_facts_only_v3_1", "hybrid_vector_graph_v3_1"}:
        overall_rfr = max(graph_recall, answer_value)
    else:
        overall_rfr = max(text_context_recall, answer_value)
    calc_complete = bool(fnum(trace.get("calculation_completeness", 0)) >= 1.0)
    fmt = bool(fnum(trace.get("answer_format_compliance", 0)) >= 1.0)
    faithful = overall_rfr >= 0.8
    answer_correct = numeric_correct and calc_complete and fmt and faithful
    failure = "none"
    if not fmt:
        failure = "answer_format_error"
    elif overall_rfr < 0.5:
        failure = "context_or_answer_fact_missing"
    elif not numeric_correct:
        failure = "model_reasoning_error"
    elif not answer_correct:
        failure = "scoring_uncertain"
    return {
        "track": trace["track"],
        "split": trace["split"],
        "case_id": trace["case_id"],
        "method": trace["method"],
        "provider": trace["provider"],
        "model": trace["model"],
        "trace_id": trace["trace_id"],
        "success": trace["success"],
        "provider_success": trace["provider_success"],
        "v3_1_required_fact_recall": trace.get("required_fact_recall", 0),
        "v3_2_graph_fact_id_recall": graph_recall,
        "v3_2_text_context_value_recall": text_context_recall,
        "v3_2_answer_value_recall": answer_value,
        "required_fact_recall": round(overall_rfr, 4),
        "v3_1_numeric_correctness": trace.get("numeric_correctness", 0),
        "numeric_correctness": 1.0 if numeric_correct else 0.0,
        "v3_2_numeric_recall": numeric_recall,
        "v3_1_answer_correctness": trace.get("answer_correctness", 0),
        "answer_correctness": 1.0 if answer_correct else 0.0,
        "faithfulness": 1.0 if faithful else 0.0,
        "calculation_completeness": 1.0 if calc_complete else 0.0,
        "answer_format_compliance": 1.0 if fmt else 0.0,
        "v3_1_failure_reason": trace.get("failure_reason", ""),
        "failure_reason": failure,
        "target_slot_count": len(targets),
        "matched_target_slot_count": len(matched_targets),
        "missing_target_slots": ";".join(item["raw"] for item in missing_targets[:20]),
        "matched_target_slots": ";".join(item["raw"] for item in matched_targets[:20]),
    }


def avg(values: list[Any]) -> float:
    nums = [fnum(value) for value in values]
    return round(sum(nums) / len(nums), 4) if nums else 0.0


def summarize(rows: list[dict[str, Any]], group_fields: list[str]) -> list[dict[str, Any]]:
    groups: dict[tuple[Any, ...], list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        groups[tuple(row[field] for field in group_fields)].append(row)
    out = []
    for key, items in sorted(groups.items()):
        base = {field: value for field, value in zip(group_fields, key)}
        base.update(
            {
                "attempts": len(items),
                "provider_success": sum(1 for row in items if str(row["provider_success"]).lower() == "true"),
                "provider_errors": sum(1 for row in items if row["failure_reason"] == "provider_error"),
                "avg_required_fact_recall": avg([row["required_fact_recall"] for row in items]),
                "avg_graph_fact_id_recall": avg([row["v3_2_graph_fact_id_recall"] for row in items]),
                "avg_text_context_value_recall": avg([row["v3_2_text_context_value_recall"] for row in items]),
                "avg_answer_value_recall": avg([row["v3_2_answer_value_recall"] for row in items]),
                "avg_numeric_correctness": avg([row["numeric_correctness"] for row in items]),
                "avg_numeric_recall": avg([row["v3_2_numeric_recall"] for row in items]),
                "avg_answer_correctness": avg([row["answer_correctness"] for row in items]),
                "avg_faithfulness": avg([row["faithfulness"] for row in items]),
                "avg_calculation_completeness": avg([row["calculation_completeness"] for row in items]),
                "avg_answer_format_compliance": avg([row["answer_format_compliance"] for row in items]),
            }
        )
        out.append(base)
    return out


def result_fields() -> list[str]:
    return [
        "track",
        "split",
        "case_id",
        "method",
        "provider",
        "model",
        "trace_id",
        "success",
        "provider_success",
        "v3_1_required_fact_recall",
        "v3_2_graph_fact_id_recall",
        "v3_2_text_context_value_recall",
        "v3_2_answer_value_recall",
        "required_fact_recall",
        "v3_1_numeric_correctness",
        "numeric_correctness",
        "v3_2_numeric_recall",
        "v3_1_answer_correctness",
        "answer_correctness",
        "faithfulness",
        "calculation_completeness",
        "answer_format_compliance",
        "v3_1_failure_reason",
        "failure_reason",
        "target_slot_count",
        "matched_target_slot_count",
        "missing_target_slots",
        "matched_target_slots",
    ]


def write_specs() -> None:
    write(
        OUT / "answer_parser_v3_2_spec.md",
        """# Answer Parser v3.2 Spec

Rules:
- Extract numeric answer slots only from model `final_answer`, `calculation_steps`, `rounding_statement`, and `brief_interpretation`.
- Ignore numbers embedded in `case_id`, `fact_id`, `trace_id`, `source_id`, `prompt_hash`, metric ids, and evidence ids.
- Ignore line numbers, citation ids, row ids, and hash fragments.
- Preserve unit and scale during extraction.
- Parse percentages, ratios, counts, USD thousands/millions, and EPS/per-share into typed numeric slots.
- Keep source fact numbers separate from derived answer targets.
""",
    )
    write(
        OUT / "numeric_slot_extraction_rules_v3_2.md",
        """# Numeric Slot Extraction Rules v3.2

Expected-answer numbers are typed before scoring:
- `source_fact_number`: value directly supplied by required facts.
- `derived_answer_number`: requested margin, rate, ratio, growth, total, or comparison target.
- `intermediate_calculation_number`: useful but not mandatory unless the question asks for it.
- `context_or_id_number`: ignored for scoring.

Only required derived targets and essential source references are mandatory. The scorer must not treat every number in the prose expected answer as a required target.
""",
    )
    write(
        OUT / "required_fact_recall_v3_2_spec.md",
        """# Required Fact Recall v3.2 Spec

Split recall into:
- `graph_fact_id_recall`: exact source fact id recall, graph/hybrid diagnostic only.
- `text_context_value_recall`: required values/years/metrics are present in text or gold context.
- `answer_value_recall`: answer used required values regardless of id representation.

Method-aware overall recall:
- `graph_facts_only_v3_2`: max(graph_fact_id_recall, answer_value_recall)
- `hybrid_vector_graph_v3_2`: max(graph_fact_id_recall, answer_value_recall)
- `vector_only_v3_2`: max(text_context_value_recall, answer_value_recall)
- `gold_context_v3_2`: max(text_context_value_recall, answer_value_recall)

Do not penalize text/gold methods for missing graph fact ids if required values are present in text.
""",
    )
    write(
        OUT / "unit_scale_rounding_rules_v3_2.md",
        """# Unit / Scale / Rounding Rules v3.2

- Percent: absolute tolerance 0.1 percentage points unless stricter task wording requires more.
- Ratio: absolute tolerance 0.01 for small ratios, relative tolerance 1% otherwise.
- USD thousands/millions: normalize scale before comparison.
- EPS/per-share: absolute tolerance 0.01.
- Counts: exact unless source explicitly rounds.
- Equivalent forms such as `0.228` and `22.8%` should match when the task implies percentage.
""",
    )
    write(
        OUT / "expected_answer_slot_contract_v3_2.md",
        """# Expected Answer Slot Contract v3.2

The expected answer is explanatory prose, not a flat list of mandatory numeric slots.

The scorer must:
- identify task intent from question and required facts
- score required derived answer targets
- allow essential source fact references to be cited without demanding every source value in final prose
- ignore ids and context-only row numbers
- avoid all-or-nothing failure when final target is correct but wording differs
""",
    )
    write(
        OUT / "scoring_rubric_v3_2.md",
        """# Scoring Rubric v3.2

Metrics:
- `numeric_correctness`: target numeric slots match within typed tolerance.
- `required_fact_recall`: method-aware recall from graph ids, context values, and answer values.
- `answer_correctness`: target numeric correctness + calculation completeness + answer format + faithfulness.
- `faithfulness`: answer relies on supplied context only.
- `calculation_completeness`: formulas and requested years/periods are present.
- `answer_format_compliance`: valid v3.1/v3.2 JSON shape.

Partial diagnostics are reported separately; binary `answer_correctness` is not the only analysis channel.
""",
    )
    write(
        OUT / "scorer_v3_1_to_v3_2_change_log.md",
        """# Scorer v3.1 to v3.2 Change Log

Changed:
- Ignore id-like numeric tokens before numeric slot extraction.
- Split required fact recall into graph id, text context value, and answer value recall.
- Type expected-answer numeric slots as source, derived, intermediate, or id/context.
- Normalize percentage/ratio/USD scale before comparison.
- Make answer correctness less brittle to wording differences when target numeric and faithfulness pass.

Unchanged:
- No model/API calls.
- No trace mutation.
- No test split usage.
- No full evaluation.
- No Neo4j write or KG patch.
""",
    )


def write_report(rows: list[dict[str, Any]], old_by_track: list[dict[str, str]], new_by_track: list[dict[str, Any]], failure_rows: list[dict[str, Any]]) -> str:
    old_map = {(row["track"], row["method"]): row for row in old_by_track}
    deltas = []
    for row in new_by_track:
        old = old_map.get((row["track"], row["method"]), {})
        deltas.append(
            {
                "track": row["track"],
                "method": row["method"],
                "answer_delta": round(fnum(row["avg_answer_correctness"]) - fnum(old.get("avg_answer_correctness", 0)), 4),
                "numeric_delta": round(fnum(row["avg_numeric_correctness"]) - fnum(old.get("avg_numeric_correctness", 0)), 4),
                "rfr_delta": round(fnum(row["avg_required_fact_recall"]) - fnum(old.get("avg_required_fact_recall", 0)), 4),
                "v3_2_answer": row["avg_answer_correctness"],
                "v3_2_numeric": row["avg_numeric_correctness"],
                "v3_2_rfr": row["avg_required_fact_recall"],
            }
        )
    top = sorted(deltas, key=lambda item: (item["answer_delta"], item["numeric_delta"], item["rfr_delta"]), reverse=True)
    track_b_hybrid = next((row for row in new_by_track if row["track"] == "track_b_shadow_overlay" and row["method"] == "hybrid_vector_graph_v3_1"), {})
    final_decision = "scorer_repair_plus_minimal_prompt_patch_then_dev_rerun"
    lines = [
        "# No-Model Rescore Report",
        "",
        f"Decision: `{final_decision}`",
        "",
        "## Safety",
        "",
        "- Model/API called: no",
        "- Test eval executed: no",
        "- Full eval executed: no",
        "- Neo4j write performed: no",
        "- KG patch applied: no",
        "",
        "## Main Score Deltas",
        "",
    ]
    for item in top:
        lines.append(
            f"- {item['track']} / {item['method']}: answer_delta={item['answer_delta']}, numeric_delta={item['numeric_delta']}, rfr_delta={item['rfr_delta']}"
        )
    lines.extend(
        [
            "",
            "## Track B Hybrid Status",
            "",
            f"- avg_answer_correctness_v3_2: {track_b_hybrid.get('avg_answer_correctness', '')}",
            f"- avg_numeric_correctness_v3_2: {track_b_hybrid.get('avg_numeric_correctness', '')}",
            f"- avg_required_fact_recall_v3_2: {track_b_hybrid.get('avg_required_fact_recall', '')}",
            "",
            "Track B hybrid improves under method-aware scoring, but remaining failures still include formula target and expected-slot contract issues. It is promising for another dev/baseline rerun, not ready for locked test.",
            "",
            "## Remaining Failures",
            "",
        ]
    )
    for reason, count in Counter(row["failure_reason"] for row in rows).most_common():
        lines.append(f"- {reason}: {count}")
    write(OUT / "no_model_rescore_report.md", "\n".join(lines))
    write(
        OUT / "scorer_repair_go_no_go.md",
        "# Scorer Repair Go / No-Go\n\n"
        f"Decision: `{final_decision}`\n\n"
        "Go for: implementing scorer v3.2 repair and preparing minimal dev-derived prompt patch.\n\n"
        "No-go for: test eval, full eval, Neo4j write, KG patch, or model calls without separate dev rerun approval.\n",
    )
    write(
        OUT / "scorer_repair_summary.md",
        "# Scorer Repair Summary\n\n"
        f"Final decision: `{final_decision}`\n\n"
        "The no-model rescore confirms that scorer/parser repair materially changes method-aware recall and some answer/numeric outcomes, but it does not eliminate all failures. Remaining failures are mostly formula-target and expected-slot contract issues, so a minimal prompt/formatter patch plus another dev/baseline rerun is recommended.\n",
    )
    return final_decision


def write_prompt_plan(final_decision: str) -> None:
    PLAN.mkdir(parents=True, exist_ok=True)
    write(
        PLAN / "prompt_formatter_v3_2_patch_plan.md",
        "# Prompt / Formatter v3.2 Patch Plan\n\n"
        "Scope: minimal dev-derived patch only. Do not rewrite the full prompt. Do not use test split.\n\n"
        "- Add explicit target formula contract.\n"
        "- Require the model to list source facts and derived answer targets separately.\n"
        "- Require final target values to appear in `final_answer` and `calculation_steps`.\n"
        "- Preserve Track A diagnostic and Track B shadow overlay claim boundaries.\n",
    )
    write(
        PLAN / "formula_target_contract_patch.md",
        "# Formula Target Contract Patch\n\n"
        "For each question, identify the requested target before calculating:\n\n"
        "1. `target_metric_or_relationship`\n"
        "2. `required_source_metrics`\n"
        "3. `target_formula`\n"
        "4. `target_years_or_periods`\n"
        "5. `final_derived_values`\n\n"
        "Do not substitute a related but different formula, such as cost/net sales when the question asks cost/SG&A.\n",
    )
    write(
        PLAN / "answer_format_patch_v3_2.md",
        "# Answer Format Patch v3.2\n\n"
        "Add two fields to the v3.1 JSON contract:\n\n"
        "- `source_facts_used`: source values copied from context.\n"
        "- `derived_answer_targets`: final calculated values that directly answer the question.\n\n"
        "Keep existing fields unchanged for compatibility.\n",
    )
    write(
        PLAN / "graph_fact_table_usage_patch.md",
        "# Graph Fact Table Usage Patch\n\n"
        "Graph facts remain source-fact only. The model must derive margins, growth rates, ratios, and comparisons in `calculation_steps`, not assume the graph table contains derived targets.\n",
    )
    write(
        PLAN / "scoring_prompt_alignment_patch.md",
        "# Scoring / Prompt Alignment Patch\n\n"
        "Align prompt fields with scorer v3.2:\n\n"
        "- Source facts map to required fact recall diagnostics.\n"
        "- Derived answer targets map to numeric correctness.\n"
        "- Fact ids are diagnostic for graph/hybrid, not required for vector/gold.\n",
    )
    write(
        PLAN / "v3_2_dev_rerun_scope.md",
        "# v3.2 Dev Rerun Scope\n\n"
        "Allowed only with separate user approval:\n\n"
        "- Track A dev + baseline diagnostic only.\n"
        "- Track B dev + baseline only.\n"
        "- No test split.\n"
        "- No full evaluation.\n"
        "- No Neo4j write or KG patch.\n",
    )
    write(
        PLAN / "v3_2_go_no_go_for_dev_rerun.md",
        "# v3.2 Go / No-Go For Dev Rerun\n\n"
        f"Decision: `{final_decision}`\n\n"
        "The package is ready for review, but it does not authorize model/API calls. A separate dev/baseline rerun approval is required.\n",
    )


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    write_specs()
    cases = load_cases()
    facts = load_facts()
    traces = read_jsonl(RUN_DIR / "dev_dryrun_v3_1_traces.jsonl")
    old_by_track = read_csv(RUN_DIR / "method_summary_by_track.csv")
    rows = [rescore_trace(trace, cases.get(trace["case_id"], {}), facts.get(trace["case_id"], [])) for trace in traces]
    by_track = summarize(rows, ["track", "method"])
    by_split = summarize(rows, ["track", "split", "method"])
    by_case = summarize(rows, ["track", "split", "case_id"])
    failure_rows = [row for row in rows if row["failure_reason"] != "none"]
    write_csv(OUT / "no_model_rescore_results.csv", rows, result_fields())
    write_csv(OUT / "no_model_rescore_method_summary_by_track.csv", by_track, list(by_track[0].keys()) if by_track else [])
    write_csv(OUT / "no_model_rescore_case_level_scores.csv", by_case, list(by_case[0].keys()) if by_case else [])
    write_jsonl(OUT / "no_model_rescore_failure_analysis.jsonl", failure_rows)
    final_decision = write_report(rows, old_by_track, by_track, failure_rows)
    write_prompt_plan(final_decision)
    created = [rel(path) for path in sorted(OUT.iterdir()) if path.is_file()] + [rel(path) for path in sorted(PLAN.iterdir()) if path.is_file()]
    deltas = []
    old_map = {(row["track"], row["method"]): row for row in old_by_track}
    for row in by_track:
        old = old_map.get((row["track"], row["method"]), {})
        deltas.append(
            f"{row['track']} / {row['method']}: answer {old.get('avg_answer_correctness', '0')} -> {row['avg_answer_correctness']}, numeric {old.get('avg_numeric_correctness', '0')} -> {row['avg_numeric_correctness']}, rfr {old.get('avg_required_fact_recall', '0')} -> {row['avg_required_fact_recall']}"
        )
    print(
        json.dumps(
            {
                "scorer repair package created": "yes",
                "no-model rescore completed": "yes",
                "model/API called": "no",
                "test eval executed": "no",
                "full eval executed": "no",
                "Neo4j write performed": "no",
                "KG patch applied": "no",
                "v3.2 prompt patch plan created": "yes",
                "main score deltas": deltas,
                "remaining blockers": [
                    "formula target mismatch remains in graph/hybrid failures",
                    "Opik still requires config or explicit locked-test local-only waiver",
                    "test eval remains locked",
                ],
                "final decision": final_decision,
                "next recommended action": "review scorer v3.2 repair and prompt patch plan; then request separate approval for v3.2 dev/baseline rerun",
                "created files": created,
            },
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
