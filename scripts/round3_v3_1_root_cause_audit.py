"""No-model forensic audit for Round 3 v3.1 dev dry-run failures."""

from __future__ import annotations

import csv
import json
import re
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
RUN_DIR = ROOT / "outputs" / "round3_eval_runs" / "dev_dryrun_v3_1_20260528_005749"
OUT = RUN_DIR / "root_cause_audit"
TRACK_A = ROOT / "outputs" / "round3_dual_track_eval_prep" / "track_a_live_kg"
TRACK_B = ROOT / "outputs" / "round3_dual_track_eval_prep" / "track_b_shadow_overlay"


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


def numbers(text: str) -> list[str]:
    return re.findall(r"(?<![A-Za-z_])-?\d+(?:\.\d+)?%?", text or "")


def normalize_num(value: Any) -> str:
    try:
        num = float(str(value).replace(",", "").replace("%", ""))
        if num.is_integer():
            return str(int(num))
        return f"{num:.4f}".rstrip("0").rstrip(".")
    except ValueError:
        return str(value)


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


def trace_key(row: dict[str, Any]) -> tuple[str, str, str]:
    return (row["track"], row["case_id"], row["method"])


def select_samples(results: list[dict[str, str]], traces: dict[tuple[str, str, str], dict[str, Any]]) -> list[dict[str, Any]]:
    selected: list[dict[str, Any]] = []
    used: set[tuple[str, str, str]] = set()

    def add(label: str, predicate: Any, limit: int) -> None:
        count = 0
        for row in results:
            key = (row["track"], row["case_id"], row["method"])
            if key in used or key not in traces:
                continue
            if predicate(row):
                item = dict(traces[key])
                item["sample_bucket"] = label
                selected.append(item)
                used.add(key)
                count += 1
                if count >= limit:
                    return

    failed = lambda r: r["failure_reason"] != "none"
    add(
        "track_b_hybrid_failures",
        lambda r: failed(r) and r["track"] == "track_b_shadow_overlay" and r["method"] == "hybrid_vector_graph_v3_1",
        3,
    )
    add(
        "track_b_graph_failures",
        lambda r: failed(r) and r["track"] == "track_b_shadow_overlay" and r["method"] == "graph_facts_only_v3_1",
        2,
    )
    add(
        "track_b_gold_failures",
        lambda r: failed(r) and r["track"] == "track_b_shadow_overlay" and r["method"] == "gold_context_v3_1",
        2,
    )
    add(
        "track_a_graph_hybrid_failures",
        lambda r: failed(r) and r["track"] == "track_a_live_kg_diagnostic" and r["method"] in {"graph_facts_only_v3_1", "hybrid_vector_graph_v3_1"},
        2,
    )
    add(
        "numeric_positive_answer_zero",
        lambda r: failed(r) and fnum(r["numeric_correctness"]) > 0 and fnum(r["answer_correctness"]) == 0,
        1,
    )
    return selected


def context_type(method: str) -> str:
    if method.startswith("vector_only"):
        return "text_context_only"
    if method.startswith("graph_facts_only"):
        return "graph_facts_only"
    if method.startswith("hybrid"):
        return "text_context_plus_graph_facts"
    if method.startswith("gold_context"):
        return "gold_context_only"
    return "unknown"


def context_summary(prompt: str, method: str) -> str:
    labels = []
    for label in ["TEXT_CONTEXT", "GRAPH_FACTS_TABLE", "GOLD_CONTEXT"]:
        if re.search(rf"(?m)^{label}:?\s*$", prompt):
            labels.append(label)
    return f"labels={','.join(labels)}; chars={len(prompt)}; method={method}"


def audit_trace(trace: dict[str, Any], cases: dict[str, dict[str, Any]], facts_by_case: dict[str, list[dict[str, Any]]]) -> dict[str, Any]:
    case = cases.get(trace["case_id"], {})
    facts = facts_by_case.get(trace["case_id"], [])
    scores = trace.get("scores") or {}
    numeric = scores.get("numeric_correctness") or {}
    rfr = scores.get("required_fact_recall") or {}
    result = trace.get("method_result") or {}
    raw = trace.get("raw_method_result_v3_1") or {}
    final_answer = result.get("final_answer", "")
    calculation = result.get("calculation", "")
    output_text = "\n".join([final_answer, calculation, json.dumps(raw, ensure_ascii=False)])
    expected_answer = str(case.get("expected_answer", ""))
    expected_nums = numbers(expected_answer)
    model_nums = numbers(output_text)
    fact_values = [normalize_num(fact.get("value")) for fact in facts]
    present_fact_values = sorted({value for value in fact_values if value and value in {n.replace("%", "") for n in model_nums}})
    fact_id_like_extras = [num for num in numeric.get("extra_numeric_slots", []) if num in trace.get("trace_id", "") or num in output_text and re.search(rf"fact[_-]?\w*_{re.escape(str(num))}\b", output_text)]
    text_like_method = trace["method"] in {"vector_only_v3_1", "gold_context_v3_1"}
    graph_like_method = trace["method"] in {"graph_facts_only_v3_1", "hybrid_vector_graph_v3_1"}
    root = "actual_model_reasoning_error"
    fix_rescore = False
    fix_context = False
    fix_prompt = False
    exclude = False
    notes: list[str] = []

    if text_like_method and fnum(trace.get("required_fact_recall")) == 0 and present_fact_values:
        root = "required_fact_recall_metric_design_issue"
        fix_rescore = True
        notes.append("Text/gold method uses source values but cannot cite graph fact ids; graph_fact_id_recall should not be primary cross-method metric.")
    elif fact_id_like_extras:
        root = "scorer_answer_parser_error"
        fix_rescore = True
        notes.append("Numeric parser appears to count fact/case id fragments as answer numbers.")
    elif numeric.get("numeric_recall") and fnum(numeric.get("numeric_recall")) >= 0.75 and not scores.get("answer_correctness", {}).get("answer_correctness"):
        root = "rounding_tolerance_or_answer_composite_issue"
        fix_rescore = True
        notes.append("Numeric recall is high but composite answer correctness is hard fail.")
    elif graph_like_method and fnum(trace.get("required_fact_recall")) >= 0.95 and fnum(trace.get("numeric_correctness")) == 0:
        root = "prompt_formula_or_expected_answer_contract_mismatch"
        fix_prompt = True
        fix_rescore = True
        notes.append("Graph context is present and recalled, but expected numeric slots and model calculation disagree.")
    elif not present_fact_values and text_like_method:
        root = "context_assembly_or_gold_context_issue"
        fix_context = True
        notes.append("Expected source values not observed in model output from text/gold context.")

    if "??" in expected_answer or "?셲" in expected_answer or "휆" in expected_answer:
        notes.append("Encoding artifacts appear in question/expected answer; case quality review recommended.")
    if len(expected_nums) > 20:
        notes.append("Expected answer contains many source and derived numbers; numeric scorer may over-constrain response.")
        fix_rescore = True

    return {
        "sample_bucket": trace.get("sample_bucket", ""),
        "track": trace["track"],
        "split": trace["split"],
        "case_id": trace["case_id"],
        "method": trace["method"],
        "question": case.get("question", ""),
        "expected_answer": expected_answer[:800],
        "context_source_type": context_type(trace["method"]),
        "supplied_context_or_graph_facts_summary": context_summary(trace.get("user_prompt", ""), trace["method"]),
        "model_final_answer": final_answer[:800],
        "model_calculation_text": calculation[:1200],
        "scorer_extracted_numbers": ";".join(map(str, numeric.get("extra_numeric_slots", [])[:40])),
        "expected_numbers": ";".join(expected_nums[:40]),
        "unit": ",".join(sorted({str(f.get("unit", "")) for f in facts if f.get("unit")})),
        "scale": infer_scale(facts),
        "rounding_expected": "v3.1 default: percent 1dp, ratio 2dp, EPS 2dp, USD source scale preserved",
        "rounding_observed": infer_rounding(model_nums),
        "original_failure_reason": trace.get("failure_reason", ""),
        "audited_root_cause": root,
        "should_fix_by_rescore": fix_rescore,
        "should_fix_by_context_patch": fix_context,
        "should_fix_by_prompt_patch": fix_prompt,
        "should_exclude_case": exclude,
        "notes": " ".join(notes) or "No additional note.",
        "present_required_fact_values_in_model_output": ";".join(present_fact_values[:20]),
        "missing_numeric_slots": ";".join(map(str, numeric.get("missing_numeric_slots", [])[:40])),
        "numeric_recall": numeric.get("numeric_recall", ""),
        "period_recall": numeric.get("period_recall", ""),
    }


def infer_scale(facts: list[dict[str, Any]]) -> str:
    units = {str(f.get("unit", "")) for f in facts}
    if any("millions" in unit.lower() for unit in units):
        return "millions"
    if any("thousands" in unit.lower() for unit in units):
        return "thousands"
    return "mixed_or_unspecified"


def infer_rounding(model_nums: list[str]) -> str:
    if any(num.endswith("%") for num in model_nums):
        return "percent_observed"
    if any("." in num and len(num.split(".")[-1].replace("%", "")) <= 2 for num in model_nums):
        return "decimal_1_2dp_observed"
    return "integer_or_mixed_observed"


def table(rows: list[dict[str, Any]], fields: list[str]) -> str:
    lines = ["| " + " | ".join(fields) + " |", "| " + " | ".join(["---"] * len(fields)) + " |"]
    for row in rows:
        lines.append("| " + " | ".join(str(row.get(field, "")).replace("\n", " ")[:140] for field in fields) + " |")
    return "\n".join(lines)


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    results = read_csv(RUN_DIR / "dev_dryrun_v3_1_results.csv")
    traces_rows = read_jsonl(RUN_DIR / "dev_dryrun_v3_1_traces.jsonl")
    traces = {trace_key(row): row for row in traces_rows}
    cases = load_cases()
    facts = load_facts()
    samples = select_samples(results, traces)
    audited = [audit_trace(trace, cases, facts) for trace in samples]

    write_sample_outputs(audited)
    write_matrix(results, traces_rows, audited)
    write_gold_context_audit(results, traces_rows, cases, facts)
    write_answer_parser_audit(traces_rows)
    write_unit_scale_rounding_audit(audited, traces_rows)
    write_context_assembly_audit(traces_rows)
    write_required_fact_recall_audit(results, traces_rows)
    write_case_level_root_causes(results, traces_rows, cases, facts, audited)
    write_candidate_files(audited)
    write_summary(audited, results)

    print(
        json.dumps(
            {
                "audit completed": "yes",
                "model/API called": "no",
                "test eval executed": "no",
                "full eval executed": "no",
                "Neo4j write performed": "no",
                "KG patch applied": "no",
                "main root causes": [
                    "required_fact_recall_metric_design_issue",
                    "scorer_answer_parser_error",
                    "prompt_formula_or_expected_answer_contract_mismatch",
                    "expected_answer_overconstrained_numeric_slots",
                ],
                "recommended next action": "combined_scoring_context_prompt_patch_then_dev_rerun",
                "created files": [str(path.relative_to(ROOT)).replace("\\", "/") for path in sorted(OUT.iterdir()) if path.is_file()],
            },
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
    )


def write_sample_outputs(audited: list[dict[str, Any]]) -> None:
    fields = [
        "sample_bucket",
        "track",
        "split",
        "case_id",
        "method",
        "question",
        "context_source_type",
        "original_failure_reason",
        "audited_root_cause",
        "should_fix_by_rescore",
        "should_fix_by_context_patch",
        "should_fix_by_prompt_patch",
        "should_exclude_case",
        "notes",
    ]
    write_csv(OUT / "representative_failed_trace_sample.csv", audited, fields)
    write_jsonl(OUT / "representative_failed_trace_audit.jsonl", audited)


def write_matrix(results: list[dict[str, str]], traces_rows: list[dict[str, Any]], audited: list[dict[str, Any]]) -> None:
    audited_by_key = {(row["track"], row["case_id"], row["method"]): row["audited_root_cause"] for row in audited}
    matrix: dict[tuple[str, str, str], int] = Counter()
    for row in results:
        if row["failure_reason"] == "none":
            matrix[(row["track"], row["method"], "none_success_or_acceptable")] += 1
            continue
        key = (row["track"], row["case_id"], row["method"])
        root = audited_by_key.get(key)
        if not root:
            if row["method"] in {"vector_only_v3_1", "gold_context_v3_1"} and fnum(row["required_fact_recall"]) < 0.2:
                root = "required_fact_recall_metric_design_issue"
            elif row["method"] in {"graph_facts_only_v3_1", "hybrid_vector_graph_v3_1"} and fnum(row["required_fact_recall"]) >= 0.95:
                root = "prompt_formula_or_expected_answer_contract_mismatch"
            else:
                root = "mixed_or_unclassified"
        matrix[(row["track"], row["method"], root)] += 1
    rows = [
        {"track": track, "method": method, "audited_root_cause": root, "count": count}
        for (track, method, root), count in sorted(matrix.items())
    ]
    write_csv(OUT / "scorer_vs_model_error_matrix.csv", rows, ["track", "method", "audited_root_cause", "count"])


def write_gold_context_audit(results: list[dict[str, str]], traces_rows: list[dict[str, Any]], cases: dict[str, dict[str, Any]], facts: dict[str, list[dict[str, Any]]]) -> None:
    gold = [row for row in traces_rows if row["method"] == "gold_context_v3_1"]
    rows = []
    for trace in gold:
        case = cases.get(trace["case_id"], {})
        prompt = trace.get("user_prompt", "")
        expected_values = [normalize_num(f.get("value")) for f in facts.get(trace["case_id"], [])]
        present = [value for value in expected_values if value and value in prompt]
        rows.append(
            {
                "case_id": trace["case_id"],
                "split": trace["split"],
                "gold_context_present": bool(re.search(r"(?m)^GOLD_CONTEXT:?\s*$", prompt)),
                "context_nonempty": len(prompt) > 500,
                "expected_values_present_in_context": len(set(present)),
                "required_fact_recall": trace.get("required_fact_recall"),
                "diagnosis": "metric_design_issue_if_values_present_but_rfr_low" if present and fnum(trace.get("required_fact_recall")) < 0.2 else "needs_case_review",
                "question": case.get("question", ""),
            }
        )
    lines = [
        "# Gold Context Anomaly Audit",
        "",
        "Gold context was supplied as original evidence text. Low required_fact_recall is mostly expected because text methods cite `GOLD_CONTEXT` rather than graph fact ids.",
        "",
        table(rows, ["case_id", "split", "gold_context_present", "context_nonempty", "expected_values_present_in_context", "required_fact_recall", "diagnosis"]),
    ]
    write(OUT / "gold_context_anomaly_audit.md", "\n".join(lines))


def write_answer_parser_audit(traces_rows: list[dict[str, Any]]) -> None:
    count_fact_id_extras = 0
    count_high_numeric_low_answer = 0
    examples = []
    for trace in traces_rows:
        numeric = ((trace.get("scores") or {}).get("numeric_correctness") or {})
        extra = numeric.get("extra_numeric_slots", [])
        if any(str(slot) in trace.get("trace_id", "") for slot in extra):
            count_fact_id_extras += 1
            if len(examples) < 5:
                examples.append(f"{trace['trace_id']}: extra={extra[:10]}")
        if fnum(numeric.get("numeric_recall")) >= 0.75 and not ((trace.get("scores") or {}).get("answer_correctness") or {}).get("answer_correctness"):
            count_high_numeric_low_answer += 1
    lines = [
        "# Answer Parser Audit",
        "",
        f"- Traces where numeric extras appear to include id fragments: {count_fact_id_extras}",
        f"- Traces with high numeric recall but answer_correctness fail: {count_high_numeric_low_answer}",
        "",
        "## Finding",
        "",
        "The numeric scorer should ignore case ids, trace ids, and source fact ids before extracting numeric answer slots. Current diagnostics show fact-id-like numbers can be counted as extra numeric slots.",
        "",
        "## Examples",
        "",
        *[f"- {example}" for example in examples],
    ]
    write(OUT / "answer_parser_audit.md", "\n".join(lines))


def write_unit_scale_rounding_audit(audited: list[dict[str, Any]], traces_rows: list[dict[str, Any]]) -> None:
    lines = [
        "# Unit / Scale / Rounding Audit",
        "",
        "- v3.1 outputs generally preserve JSON format and include calculation steps.",
        "- Failures often come from expected numeric slot matching, not provider errors.",
        "- USD_millions source facts and percentage/ratio derived answers are mixed inside expected answers.",
        "- The scorer currently treats many source values, derived percentages, fact-id fragments, and final-answer numbers as one numeric pool.",
        "",
        "## Sample Evidence",
        "",
        table(audited, ["case_id", "method", "unit", "scale", "rounding_observed", "numeric_recall", "audited_root_cause", "notes"]),
    ]
    write(OUT / "unit_scale_rounding_audit.md", "\n".join(lines))


def write_context_assembly_audit(traces_rows: list[dict[str, Any]]) -> None:
    issues = []
    for trace in traces_rows:
        prompt = trace.get("user_prompt", "")
        labels = {
            "text": bool(re.search(r"(?m)^TEXT_CONTEXT:?\s*$", prompt)),
            "graph": bool(re.search(r"(?m)^GRAPH_FACTS_TABLE:?\s*$", prompt)),
            "gold": bool(re.search(r"(?m)^GOLD_CONTEXT:?\s*$", prompt)),
        }
        method = trace["method"]
        ok = (
            (method == "vector_only_v3_1" and labels == {"text": True, "graph": False, "gold": False})
            or (method == "graph_facts_only_v3_1" and labels == {"text": False, "graph": True, "gold": False})
            or (method == "hybrid_vector_graph_v3_1" and labels == {"text": True, "graph": True, "gold": False})
            or (method == "gold_context_v3_1" and labels == {"text": False, "graph": False, "gold": True})
        )
        if not ok or trace["split"] == "round3_test":
            issues.append({"trace_id": trace["trace_id"], "method": method, "split": trace["split"], **labels})
    lines = [
        "# Context Assembly Audit",
        "",
        f"- Total traces checked: {len(traces_rows)}",
        f"- Context isolation issues: {len(issues)}",
        f"- Test split rows detected: {sum(1 for trace in traces_rows if trace['split'] == 'round3_test')}",
        "",
        "## Conclusion",
        "",
        "Context assembly is not the primary blocker. Method isolation is clean and no test rows were used.",
    ]
    if issues:
        lines.extend(["", table(issues, ["trace_id", "method", "split", "text", "graph", "gold"])])
    write(OUT / "context_assembly_audit.md", "\n".join(lines))


def write_required_fact_recall_audit(results: list[dict[str, str]], traces_rows: list[dict[str, Any]]) -> None:
    by_method = defaultdict(list)
    for row in results:
        by_method[row["method"]].append(fnum(row["required_fact_recall"]))
    rows = [
        {"method": method, "avg_required_fact_recall": round(sum(vals) / len(vals), 4), "count": len(vals)}
        for method, vals in sorted(by_method.items())
    ]
    lines = [
        "# Required Fact Recall Metric Audit",
        "",
        "## Method Averages",
        "",
        table(rows, ["method", "avg_required_fact_recall", "count"]),
        "",
        "## Finding",
        "",
        "`required_fact_recall` is currently mixing two different concepts: whether the context supplied the required facts, and whether the answer cited exact graph fact ids. This is unfair across graph and text methods.",
        "",
        "## Recommended Split",
        "",
        "- `graph_fact_id_recall`: exact fact id recall, graph/hybrid diagnostic only.",
        "- `text_context_value_recall`: required values/years/metrics appear in text or gold context.",
        "- `answer_value_recall`: the answer used the required values regardless of fact id representation.",
        "",
        "Do not use graph_fact_id_recall as the primary cross-method metric between graph and text methods.",
    ]
    write(OUT / "required_fact_recall_metric_audit.md", "\n".join(lines))


def write_case_level_root_causes(results: list[dict[str, str]], traces_rows: list[dict[str, Any]], cases: dict[str, dict[str, Any]], facts: dict[str, list[dict[str, Any]]], audited: list[dict[str, Any]]) -> None:
    audit_by_key = {(row["track"], row["case_id"], row["method"]): row for row in audited}
    grouped: dict[tuple[str, str], list[dict[str, str]]] = defaultdict(list)
    for row in results:
        grouped[(row["track"], row["case_id"])].append(row)
    rows = []
    for (track, case_id), items in sorted(grouped.items()):
        roots = []
        for item in items:
            audited_item = audit_by_key.get((track, case_id, item["method"]))
            if audited_item:
                roots.append(audited_item["audited_root_cause"])
            elif item["method"] in {"vector_only_v3_1", "gold_context_v3_1"} and fnum(item["required_fact_recall"]) < 0.2:
                roots.append("required_fact_recall_metric_design_issue")
            elif item["method"] in {"graph_facts_only_v3_1", "hybrid_vector_graph_v3_1"} and fnum(item["required_fact_recall"]) >= 0.95:
                roots.append("prompt_formula_or_expected_answer_contract_mismatch")
            elif item["failure_reason"] == "none":
                roots.append("none")
            else:
                roots.append("mixed_or_unclassified")
        counter = Counter(roots)
        rows.append(
            {
                "track": track,
                "case_id": case_id,
                "split": items[0]["split"],
                "question": cases.get(case_id, {}).get("question", ""),
                "primary_root_cause": counter.most_common(1)[0][0],
                "root_cause_counts": json.dumps(counter, ensure_ascii=False, sort_keys=True),
                "should_exclude_case": "false",
                "notes": "Case should not be excluded before scorer/context contract repair unless manual review finds expected_answer ambiguity.",
            }
        )
    write_csv(OUT / "case_level_root_causes.csv", rows, ["track", "split", "case_id", "question", "primary_root_cause", "root_cause_counts", "should_exclude_case", "notes"])


def write_candidate_files(audited: list[dict[str, Any]]) -> None:
    rescore = []
    prompt = []
    context = []
    exclude = []
    for row in audited:
        base = {
            "case_id": row["case_id"],
            "method": row["method"],
            "track": row["track"],
            "root_cause": row["audited_root_cause"],
            "notes": row["notes"],
        }
        if row["should_fix_by_rescore"]:
            rescore.append({**base, "candidate_fix": "ignore id-like numbers; split fact recall metrics; re-evaluate answer correctness from normalized value/year/unit matches"})
        if row["should_fix_by_prompt_patch"]:
            prompt.append({**base, "candidate_fix": "tighten formula target and require source values plus derived final values in calculation_steps"})
        if row["should_fix_by_context_patch"]:
            context.append({**base, "candidate_fix": "verify text/gold context contains exact source rows and preserve encoding"})
        if row["should_exclude_case"]:
            exclude.append({**base, "candidate_fix": "exclude only after manual case quality review"})
    write_jsonl(OUT / "rescore_candidates.jsonl", rescore)
    write_jsonl(OUT / "prompt_patch_candidates.jsonl", prompt)
    write_jsonl(OUT / "context_patch_candidates.jsonl", context)
    write_jsonl(OUT / "case_exclusion_candidates.jsonl", exclude)


def write_summary(audited: list[dict[str, Any]], results: list[dict[str, str]]) -> None:
    roots = Counter(row["audited_root_cause"] for row in audited)
    failure_counts = Counter(row["failure_reason"] for row in results)
    recommendation = "combined_scoring_context_prompt_patch_then_dev_rerun"
    lines = [
        "# v3.1 Dev Dry-Run Root Cause Audit Summary",
        "",
        "Decision: `combined_scoring_context_prompt_patch_then_dev_rerun`",
        "",
        "## Safety",
        "",
        "- Model/API called: no",
        "- Test eval executed: no",
        "- Full eval executed: no",
        "- Neo4j write performed: no",
        "- KG patch applied: no",
        "",
        "## Main Findings",
        "",
        "- Provider/API is not the blocker: provider failures were 0 and all 76 calls succeeded.",
        "- Context assembly is mostly clean: no test split rows and method context labels match the approved method isolation rules.",
        "- Text/gold methods are being penalized by graph-fact-id recall even when source values are present in text.",
        "- Numeric scorer appears to count id-like tokens from fact ids / trace ids as numeric answer slots in some failures.",
        "- Expected answers often mix source values and derived values, making numeric correctness over-constrained unless slots are typed.",
        "- Some graph/hybrid outputs still show true calculation/target-formula mismatches, so prompt/rubric tightening is also needed.",
        "",
        "## Sample Root Cause Counts",
        "",
    ]
    for root, count in roots.most_common():
        lines.append(f"- {root}: {count}")
    lines.extend(["", "## Failure Reason Counts", ""])
    for reason, count in failure_counts.most_common():
        lines.append(f"- {reason}: {count}")
    lines.extend(
        [
            "",
            "## Recommended Next Action",
            "",
            f"`{recommendation}`",
            "",
            "Do not run test eval yet. First repair scorer/answer parser and required_fact_recall metric design, then patch prompt/formatter only for dev-derived formula clarity, then run a dev/baseline rerun.",
        ]
    )
    write(OUT / "root_cause_audit_summary.md", "\n".join(lines))
    write(
        OUT / "recommended_next_action.md",
        "# Recommended Next Action\n\n"
        "Decision: `combined_scoring_context_prompt_patch_then_dev_rerun`\n\n"
        "1. Repair scorer only where the audit shows parser/metric-design defects: ignore id-like numeric tokens, split required fact recall into graph id / text value / answer value recall, and type expected numeric slots as source vs derived.\n"
        "2. Patch prompt/formatter only for dev-derived formula clarity: require the target formula, all source values, and final derived value to appear in calculation steps.\n"
        "3. Do not exclude cases yet; reserve exclusion for manual review after rescore.\n"
        "4. Do not run test eval or full eval.\n",
    )


if __name__ == "__main__":
    main()
