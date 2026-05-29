"""Build Round 3B formula-contract-first recovery evaluation package.

This script is reporting/selection only. It does not call models, run eval,
connect to Neo4j, or apply KG patches.
"""

from __future__ import annotations

import csv
import hashlib
import json
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import scripts.round3_formula_contract_v3_2 as fc


LOCKED_RUN = ROOT / "outputs" / "round3_eval_runs" / "locked_test_v3_2_track_b_20260528_145253"
FREEZE_DIR = LOCKED_RUN / "validity_freeze"
OUT = ROOT / "outputs" / "round3_recovery_eval_set"
POOL_DIR = OUT / "candidate_pool"
VALIDATION_DIR = OUT / "formula_contract_validation"
SPLIT_DIR = OUT / "final_split"
APPROVAL_DIR = OUT / "dev_run_approval_package"
PROMPT_DIR = ROOT / "outputs" / "round3_dual_track_eval_prep" / "prompt_formatter_v3_2"
SCORER_DIR = ROOT / "outputs" / "round3_eval_harness" / "scorer_v3_2_formula_aware"
CONTRACT_DIR = ROOT / "outputs" / "round3_eval_harness" / "formula_contract_v3_2"
CLAIM = (
    "Track B uses a shadow overlay of exact-quote verified source facts. "
    "It does not represent live Neo4j KG performance. No Neo4j write or KG patch was applied."
)
CASE_RE = re.compile(r"\b(?:round3_(?:dev|test)_\d{3}_[0-9a-f]{8}|baseline_control_\d{3}_[0-9a-f]{8})\b")


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    if not path.exists():
        return rows
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            rows.append(json.loads(line))
    return rows


def write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text.rstrip() + "\n", encoding="utf-8")


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


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


def rel(path: Path) -> str:
    return path.resolve().relative_to(ROOT.resolve()).as_posix()


def sha_file(path: Path) -> str:
    if not path.exists():
        return ""
    return hashlib.sha256(path.read_bytes()).hexdigest()


def sha_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def load_cases_from_dir(path: Path) -> tuple[dict[str, dict[str, Any]], dict[str, list[dict[str, Any]]]]:
    cases: dict[str, dict[str, Any]] = {}
    facts: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for name in ["round3_selected_cases.jsonl", "eval_ready_cases.jsonl"]:
        for row in read_jsonl(path / name):
            row = dict(row)
            row["_source_pool"] = rel(path)
            cases.setdefault(row["case_id"], row)
    for name in ["round3_required_facts.jsonl", "eval_ready_required_facts.jsonl"]:
        for fact in read_jsonl(path / name):
            fact = dict(fact)
            fact["_source_pool"] = rel(path)
            facts[fact["case_id"]].append(fact)
    return cases, facts


def load_all_source_candidates() -> tuple[dict[str, dict[str, Any]], dict[str, list[dict[str, Any]]]]:
    ordered_dirs = [
        ROOT / "outputs" / "round3_case_factory_repaired",
        ROOT / "outputs" / "round3_case_factory_max_quality13",
        ROOT / "outputs" / "round3_case_factory_expanded",
        ROOT / "outputs" / "round3_case_factory",
    ]
    cases: dict[str, dict[str, Any]] = {}
    facts: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for directory in ordered_dirs:
        if not directory.exists():
            continue
        loaded_cases, loaded_facts = load_cases_from_dir(directory)
        for case_id, case in loaded_cases.items():
            cases.setdefault(case_id, case)
        for case_id, rows in loaded_facts.items():
            if case_id not in facts:
                facts[case_id] = rows
    return cases, facts


def case_ids_from_json(path: Path) -> set[str]:
    if not path.exists():
        return set()
    data = read_json(path)
    if isinstance(data, dict):
        ids = set()
        for value in data.values():
            if isinstance(value, list):
                ids.update(str(item) for item in value if isinstance(item, str))
        return ids
    if isinstance(data, list):
        return {str(row.get("case_id")) for row in data if isinstance(row, dict) and row.get("case_id")}
    return set()


def gather_exposure_registry() -> tuple[list[dict[str, Any]], dict[str, set[str]]]:
    categories: dict[str, set[str]] = defaultdict(set)
    registry: dict[tuple[str, str], dict[str, Any]] = {}

    def add(case_id: str, category: str, source: str) -> None:
        if not case_id:
            return
        categories[category].add(case_id)
        key = (case_id, category)
        registry.setdefault(key, {"case_id": case_id, "exposure_category": category, "sources": []})
        registry[key]["sources"].append(source)

    locked_rows = read_jsonl(LOCKED_RUN / "locked_test_v3_2_traces.jsonl")
    for row in locked_rows:
        add(str(row.get("case_id", "")), "previous_locked_test_model_call", rel(LOCKED_RUN / "locked_test_v3_2_traces.jsonl"))

    clean_list = ROOT / "outputs" / "round3_dual_track_eval_prep" / "dev_rerun_approval_v3_2_clean_dev" / "v3_2_clean_dev_rerun_case_list.json"
    for cid in case_ids_from_json(clean_list):
        add(cid, "previous_clean_dev_baseline_case", rel(clean_list))

    excluded_contracts = ROOT / "outputs" / "round3_eval_harness" / "formula_contract_v3_2_clean_dev" / "excluded_formula_contracts.jsonl"
    for row in read_jsonl(excluded_contracts):
        add(str(row.get("case_id", "")), "ambiguous_formula_contract_excluded_case", rel(excluded_contracts))

    exposure_files = [
        ROOT / "outputs" / "round3_eval_runs" / "dev_dryrun_v3_1_20260528_005749" / "root_cause_audit" / "representative_failed_trace_sample.csv",
        ROOT / "outputs" / "round3_eval_runs" / "dev_dryrun_v3_1_20260528_005749" / "root_cause_audit" / "representative_failed_trace_audit.jsonl",
        ROOT / "outputs" / "round3_eval_runs" / "dev_dryrun_v3_1_20260528_005749" / "root_cause_audit" / "prompt_patch_candidates.jsonl",
        ROOT / "outputs" / "round3_eval_runs" / "dev_dryrun_v3_1_20260528_005749" / "root_cause_audit" / "rescore_candidates.jsonl",
        ROOT / "outputs" / "round3_eval_runs" / "dev_dryrun_v3_1_20260528_005749" / "root_cause_audit" / "context_patch_candidates.jsonl",
        ROOT / "outputs" / "round3_eval_runs" / "dev_dryrun_v3_1_20260528_005749" / "root_cause_audit" / "case_exclusion_candidates.jsonl",
        ROOT / "outputs" / "round3_eval_harness" / "formula_contract_v3_2_clean_dev" / "formula_contract_manual_review_packet.md",
        ROOT / "outputs" / "round3_backlog_remediation_consolidated" / "b2c_final_readonly_disambiguation" / "b2c_user_manual_selection_packet.md",
    ]
    exposure_dirs = [
        ROOT / "outputs" / "round3_eval_runs" / "dev_dryrun_v3_20260527_230440",
        ROOT / "outputs" / "round3_eval_runs" / "dev_dryrun_v3_1_20260528_005749",
        ROOT / "outputs" / "round3_eval_runs" / "dev_dryrun_v3_2_clean_20260528_141556",
        LOCKED_RUN,
    ]
    for directory in exposure_dirs:
        if directory.exists():
            exposure_files.extend([p for p in directory.rglob("*") if p.is_file() and p.suffix.lower() in {".jsonl", ".json", ".csv", ".md"}])
    for path in exposure_files:
        if not path.exists():
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        category = "prompt_scorer_or_failure_analysis_exposure"
        if "apd" in text.lower() or "pg_003_apd_fiscal" in text:
            category = "apd_or_test_informed_patch_artifact"
        for cid in sorted(set(CASE_RE.findall(text))):
            add(cid, category, rel(path))
    return list(registry.values()), categories


def exact_quote_ok(case: dict[str, Any], facts: list[dict[str, Any]]) -> tuple[bool, str]:
    if not facts:
        return False, "no_required_facts"
    text = str(case.get("evidence_text", ""))
    for fact in facts:
        quote = str(fact.get("evidence_quote_exact") or fact.get("evidence_quote") or "").strip()
        if not quote:
            return False, f"missing_quote:{fact.get('fact_id')}"
        if fact.get("quote_is_exact_excerpt") is False:
            return False, f"quote_flag_false:{fact.get('fact_id')}"
        if quote not in text:
            return False, f"quote_not_exact_substring:{fact.get('fact_id')}"
    return True, "all_required_fact_quotes_exact"


def derived_leakage_ok(facts: list[dict[str, Any]]) -> bool:
    return not any(bool(fact.get("derived_answer_value")) for fact in facts)


def unresolved_company_ticker(case: dict[str, Any], facts: list[dict[str, Any]]) -> bool:
    if not case.get("ticker") or str(case.get("ticker")).strip().upper() in {"UNKNOWN", "N/A", "NA"}:
        return True
    return any(str(fact.get("ticker", "")).strip().upper() in {"", "UNKNOWN", "N/A", "NA"} for fact in facts)


def contract_for(case: dict[str, Any], facts: list[dict[str, Any]]) -> tuple[dict[str, Any], dict[str, Any], list[str]]:
    inferred = fc.infer_formula(case, facts)
    slots, slot_issues = fc.compute_targets(inferred, facts)
    issues = [item for item in [inferred.get("issue", "")] + slot_issues if item]
    visible = {key: value for key, value in inferred.items() if key not in {"ambiguous", "issue"}}
    scorer = {
        "formula_type": inferred["formula_type"],
        "target_slots": slots,
        "source_fact_numbers": [
            {"fact_id": fact.get("fact_id"), "metric": fact.get("metric_canonical"), "year": fact.get("year"), "value": fact.get("value"), "unit": fact.get("unit")}
            for fact in facts
        ],
        "non_target_numbers": ["case_id", "fact_id", "trace_id", "source_id", "prompt_hash", "metric IDs", "evidence IDs"],
        "intermediate_numbers": [],
        "final_target_numbers": [slot["target_slot_name"] for slot in slots],
    }
    if inferred.get("ambiguous"):
        issues.append("formula_type_ambiguous")
    if not slots:
        issues.append("target_slot_count_zero")
    return visible, scorer, issues


def oracle_answers(scorer: dict[str, Any]) -> dict[str, str]:
    slots = scorer.get("target_slots", [])
    if not slots:
        return {key: "" for key in ["correct", "blank", "wrong_formula", "wrong_year", "wrong_denominator", "unit_mismatch", "source_only"]}
    correct_values = ", ".join(f"{slot['target_slot_name']} = {slot['expected_value']} {slot['unit']}" for slot in slots)
    wrong_values = ", ".join(f"{slot['target_slot_name']} = {round(float(slot['expected_value']) * 1.25 + 1, 4)} {slot['unit']}" for slot in slots)
    source_values = ", ".join(f"{fact['metric']} {fact['year']} {fact['value']} {fact['unit']}" for fact in scorer.get("source_fact_numbers", [])[:6])
    first = slots[0]
    wrong_year = f"{first['target_slot_name']}_wrong_year = {first['expected_value']} {first['unit']}"
    wrong_denom = f"{first['target_slot_name']}_wrong_denominator = {round(float(first['expected_value']) * 0.5, 4)} {first['unit']}"
    unit_mismatch = f"{first['target_slot_name']}_unit_mismatch = {first['expected_value']} USD_millions"
    return {
        "correct": correct_values,
        "blank": "",
        "wrong_formula": wrong_values,
        "wrong_year": wrong_year,
        "wrong_denominator": wrong_denom,
        "unit_mismatch": unit_mismatch,
        "source_only": source_values,
    }


def score_oracle(answer: str, scorer: dict[str, Any], *, require_full: bool = False) -> float:
    slots = scorer.get("target_slots", [])
    if not slots:
        return 0.0
    nums = fc.extract_numbers(answer)
    matched = 0
    for slot in slots:
        expected = fc.parse_number(str(slot.get("expected_value", "")))
        if expected and any(fc.close(expected, value, slot.get("unit", "")) for value in nums):
            matched += 1
    score = matched / len(slots)
    return 1.0 if require_full and score >= 0.999 else round(score, 4)


def run_oracle_sanity(case_id: str, scorer: dict[str, Any]) -> tuple[list[dict[str, Any]], bool]:
    answers = oracle_answers(scorer)
    rows = []
    expected = {
        "oracle_correct_answer": ("pass_high", True),
        "oracle_blank_answer": ("fail", False),
        "oracle_wrong_formula_answer": ("lower", False),
        "oracle_near_miss_wrong_year": ("lower", False),
        "oracle_near_miss_wrong_denominator": ("lower", False),
        "oracle_near_miss_unit_mismatch": ("normalize_only_if_equivalent", False),
        "oracle_source_facts_only_no_derived_target": ("not_full", False),
    }
    answer_map = {
        "oracle_correct_answer": answers["correct"],
        "oracle_blank_answer": answers["blank"],
        "oracle_wrong_formula_answer": answers["wrong_formula"],
        "oracle_near_miss_wrong_year": answers["wrong_year"],
        "oracle_near_miss_wrong_denominator": answers["wrong_denominator"],
        "oracle_near_miss_unit_mismatch": answers["unit_mismatch"],
        "oracle_source_facts_only_no_derived_target": answers["source_only"],
    }
    all_pass = True
    for check, (expectation, should_pass_full) in expected.items():
        score = score_oracle(answer_map[check], scorer, require_full=should_pass_full)
        if check == "oracle_correct_answer":
            passed = score >= 0.999
        else:
            passed = score < 0.999
        all_pass = all_pass and passed
        rows.append({"case_id": case_id, "check_name": check, "score": score, "expected_behavior": expectation, "passed": passed})
    return rows, all_pass


def context_availability(case: dict[str, Any], facts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    text = str(case.get("evidence_text", ""))
    values_present = all(str(fact.get("value", "")).replace(".0", "") in text.replace(",", "") or str(fact.get("value", "")) in text for fact in facts)
    fact_rows_ok = all(fact.get("metric_canonical") and fact.get("year") is not None and fact.get("value") is not None and fact.get("unit") for fact in facts)
    rows = []
    for method in ["vector_only_v3_2", "graph_facts_only_v3_2", "hybrid_vector_graph_v3_2", "gold_context_v3_2"]:
        row = {
            "case_id": case["case_id"],
            "method": method,
            "text_context_present": method in {"vector_only_v3_2", "hybrid_vector_graph_v3_2", "gold_context_v3_2"},
            "text_context_non_empty": bool(text) if method in {"vector_only_v3_2", "hybrid_vector_graph_v3_2", "gold_context_v3_2"} else "",
            "text_context_contains_required_values": values_present if method in {"vector_only_v3_2", "hybrid_vector_graph_v3_2", "gold_context_v3_2"} else "",
            "graph_fact_table_present": method in {"graph_facts_only_v3_2", "hybrid_vector_graph_v3_2"},
            "graph_fact_table_non_empty": bool(facts) if method in {"graph_facts_only_v3_2", "hybrid_vector_graph_v3_2"} else "",
            "graph_fact_count": len(facts) if method in {"graph_facts_only_v3_2", "hybrid_vector_graph_v3_2"} else "",
            "required_fact_count": case.get("required_fact_count", len(facts)),
            "graph_facts_include_metric_year_value_unit": fact_rows_ok if method in {"graph_facts_only_v3_2", "hybrid_vector_graph_v3_2"} else "",
            "passed": True,
        }
        row["passed"] = all(value is not False for value in row.values())
        rows.append(row)
    return rows


def main() -> None:
    cases, facts_by_case = load_all_source_candidates()
    exposure_rows, exposure_categories = gather_exposure_registry()
    locked_case_ids = exposure_categories["previous_locked_test_model_call"]
    clean_case_ids = exposure_categories["previous_clean_dev_baseline_case"]
    ambiguous_case_ids = exposure_categories["ambiguous_formula_contract_excluded_case"]
    tuning_case_ids = exposure_categories["prompt_scorer_or_failure_analysis_exposure"]
    apd_case_ids = exposure_categories["apd_or_test_informed_patch_artifact"]
    excluded_ids = set().union(locked_case_ids, clean_case_ids, ambiguous_case_ids, tuning_case_ids, apd_case_ids)

    FREEZE_DIR.mkdir(parents=True, exist_ok=True)
    write(
        FREEZE_DIR / "locked_test_validity_failure_report.md",
        "# Locked Test Validity Failure Report\n\n"
        "The locked test run `locked_test_v3_2_track_b_20260528_145253` is frozen as a validity diagnostic only.\n\n"
        "- Track B test cases executed: 10\n"
        "- Total attempts: 40\n"
        "- Provider failures: 0\n"
        "- Validity note: `validity_limited_by_formula_contract_ambiguity`\n"
        "- Formula contract issue attempts: 36\n"
        "- 9/10 test cases had missing or ambiguous scorer target slots.\n\n"
        "Do not use this run for performance claims, prompt tuning, scorer tuning, or formula-contract tuning.\n",
    )
    write(
        FREEZE_DIR / "do_not_use_for_performance_claims.md",
        "# Do Not Use For Performance Claims\n\n"
        "This run is a diagnostic showing that the locked Track B test split was not formula-contract-ready. "
        "It must not be reported as valid Round 3 performance.\n\n" + CLAIM + "\n",
    )
    write_json(
        FREEZE_DIR / "excluded_from_recovery_selection.json",
        {
            "locked_test_case_ids": sorted(locked_case_ids),
            "clean_dev_baseline_case_ids": sorted(clean_case_ids),
            "ambiguous_formula_contract_case_ids": sorted(ambiguous_case_ids),
            "apd_or_test_informed_case_ids": sorted(apd_case_ids),
            "previous_prompt_scorer_tuning_case_ids": sorted(tuning_case_ids),
            "all_excluded_case_ids": sorted(excluded_ids),
        },
    )

    candidate_rows = []
    exclusion_rows = []
    for case_id, case in sorted(cases.items()):
        facts = facts_by_case.get(case_id, [])
        reasons = []
        if case_id in locked_case_ids:
            reasons.append("previous_locked_test_case")
        if case_id in clean_case_ids:
            reasons.append("previous_clean_dev_baseline_case")
        if case_id in ambiguous_case_ids:
            reasons.append("previous_ambiguous_formula_contract_case")
        if case_id in tuning_case_ids:
            reasons.append("prior_prompt_scorer_or_failure_analysis_exposure")
        if case_id in apd_case_ids or "apd" in case_id.lower() or "apd" in str(case.get("ticker", "")).lower():
            reasons.append("apd_or_test_informed_exclusion")
        exact_ok, exact_reason = exact_quote_ok(case, facts)
        if not exact_ok:
            reasons.append(f"missing_exact_evidence_quote:{exact_reason}")
        if unresolved_company_ticker(case, facts):
            reasons.append("unresolved_company_or_ticker")
        if not derived_leakage_ok(facts):
            reasons.append("derived_answer_value_leakage")
        visible, scorer, contract_issues = contract_for(case, facts)
        if visible.get("formula_type") == "ambiguous_manual_review" or contract_issues:
            reasons.append("formula_contract_not_unambiguous")
        if not scorer.get("target_slots"):
            reasons.append("target_slot_count_zero")
        row = {
            "case_id": case_id,
            "split": case.get("split", ""),
            "category": case.get("category", ""),
            "company": case.get("company", ""),
            "ticker": case.get("ticker", ""),
            "question": case.get("question", ""),
            "quality_score": case.get("quality_score", ""),
            "source_pool": case.get("_source_pool", ""),
            "formula_type": visible.get("formula_type", ""),
            "target_slot_count": len(scorer.get("target_slots", [])),
            "exact_quote_status": exact_reason,
        }
        if reasons:
            exclusion_rows.append({**row, "excluded": True, "reasons": "|".join(sorted(set(reasons)))})
        else:
            candidate_rows.append({**row, "excluded": False, "reasons": ""})
    write_jsonl(POOL_DIR / "recovery_candidate_pool.jsonl", candidate_rows)
    write_jsonl(POOL_DIR / "recovery_candidate_exclusion_report.jsonl", exclusion_rows)
    reason_counts = Counter(reason for row in exclusion_rows for reason in row["reasons"].split("|"))
    write(
        POOL_DIR / "recovery_candidate_pool_summary.md",
        "# Recovery Candidate Pool Summary\n\n"
        f"- Source candidates scanned: {len(cases)}\n"
        f"- Recovery candidate pool after hard exclusions: {len(candidate_rows)}\n"
        f"- Excluded candidates: {len(exclusion_rows)}\n\n"
        "## Top Exclusion Reasons\n\n"
        + "\n".join(f"- {reason}: {count}" for reason, count in reason_counts.most_common(20))
        + "\n",
    )

    validation_rows = []
    rejected_rows = []
    oracle_rows = []
    near_miss_rows = []
    context_rows = []
    visible_rows = []
    scorer_rows = []
    required_fact_rows = []
    selected_cases = []
    for row in candidate_rows:
        case = cases[row["case_id"]]
        facts = facts_by_case[row["case_id"]]
        visible, scorer, issues = contract_for(case, facts)
        oracle, oracle_ok = run_oracle_sanity(row["case_id"], scorer)
        availability = context_availability(case, facts)
        context_ok = all(item["passed"] for item in availability)
        leakage = not any(str(slot.get("expected_value")) in json.dumps(visible, ensure_ascii=False) for slot in scorer.get("target_slots", []))
        passed = not issues and oracle_ok and context_ok and leakage
        validation = {
            "case_id": row["case_id"],
            "split": row["split"],
            "formula_type": visible.get("formula_type", ""),
            "target_slot_count": len(scorer.get("target_slots", [])),
            "model_visible_unambiguous": not issues,
            "scorer_contract_valid": bool(scorer.get("target_slots")),
            "oracle_sanity_pass": oracle_ok,
            "context_availability_pass": context_ok,
            "formula_contract_leakage": not leakage,
            "method_fairness_pass": leakage,
            "validation_pass": passed,
            "issues": issues,
        }
        validation_rows.append(validation)
        oracle_rows.extend(oracle)
        near_miss_rows.extend([item for item in oracle if item["check_name"] != "oracle_correct_answer"])
        context_rows.extend(availability)
        if passed:
            selected_cases.append(case)
            visible_rows.append({"case_id": row["case_id"], "split": row["split"], "model_visible_formula_contract": visible})
            scorer_rows.append({"case_id": row["case_id"], "split": row["split"], "scorer_only_target_slot_contract": scorer})
            required_fact_rows.extend(facts)
        else:
            rejected_rows.append(validation)

    write_jsonl(VALIDATION_DIR / "formula_contract_validation_results.jsonl", validation_rows)
    write_jsonl(VALIDATION_DIR / "oracle_scorer_sanity_results.jsonl", oracle_rows)
    write_jsonl(VALIDATION_DIR / "oracle_near_miss_sanity_results.jsonl", near_miss_rows)
    write_jsonl(VALIDATION_DIR / "rejected_formula_contract_cases.jsonl", rejected_rows)
    write_jsonl(VALIDATION_DIR / "prior_exposure_registry.jsonl", exposure_rows)
    write_jsonl(VALIDATION_DIR / "recovery_test_exclusion_due_to_prior_exposure.jsonl", [{"case_id": cid, "reason": "prior_exposure_registry"} for cid in sorted(excluded_ids)])
    write_csv(
        VALIDATION_DIR / "per_method_context_availability.csv",
        context_rows,
        [
            "case_id",
            "method",
            "text_context_present",
            "text_context_non_empty",
            "text_context_contains_required_values",
            "graph_fact_table_present",
            "graph_fact_table_non_empty",
            "graph_fact_count",
            "required_fact_count",
            "graph_facts_include_metric_year_value_unit",
            "passed",
        ],
    )
    pass_count = sum(1 for row in validation_rows if row["validation_pass"])
    oracle_pass_rate = 0.0
    if validation_rows:
        oracle_pass_rate = round(sum(1 for row in validation_rows if row["oracle_sanity_pass"]) / len(validation_rows), 4)
    write(
        VALIDATION_DIR / "formula_contract_validation_summary.md",
        "# Formula-Contract-First Validation Summary\n\n"
        f"- Candidates validated: {len(candidate_rows)}\n"
        f"- Validation pass: {pass_count}\n"
        f"- Oracle sanity pass rate: {oracle_pass_rate}\n"
        f"- Model/API called: no\n\n{CLAIM}\n",
    )
    write(
        VALIDATION_DIR / "oracle_sanity_summary.md",
        "# Oracle Sanity Summary\n\n"
        f"- Oracle checks run: {len(oracle_rows)}\n"
        f"- Candidate-level oracle pass rate: {oracle_pass_rate}\n"
        "- A case can enter recovery_test only if all oracle checks pass.\n",
    )
    write(
        VALIDATION_DIR / "formula_contract_leakage_check.md",
        "# Formula Contract Leakage Check\n\n"
        "- Model-visible contracts must not contain expected final target values.\n"
        f"- Candidates checked: {len(validation_rows)}\n"
        f"- Leakage rows: {sum(1 for row in validation_rows if row['formula_contract_leakage'])}\n",
    )
    write(
        VALIDATION_DIR / "recovery_test_cleanliness_report.md",
        "# Recovery Test Cleanliness Report\n\n"
        f"- Prior exposure registry entries: {len(exposure_rows)}\n"
        f"- Case IDs barred from recovery test: {len(excluded_ids)}\n"
        "- Previously exposed cases may be used for recovery_dev only if later approved, but recovery_test must be clean.\n",
    )
    write(
        VALIDATION_DIR / "per_method_context_availability_report.md",
        "# Per-Method Context Availability Report\n\n"
        f"- Rows checked: {len(context_rows)}\n"
        f"- Failed rows: {sum(1 for row in context_rows if not row.get('passed'))}\n"
        "- No context was fabricated.\n",
    )

    selected_sorted = sorted(selected_cases, key=lambda c: (c.get("split") != "baseline_control", c.get("split") != "round3_dev", str(c.get("case_id"))))
    baseline = [c for c in selected_sorted if c.get("split") == "baseline_control"][:3]
    dev_cases = [c for c in selected_sorted if c.get("split") == "round3_dev"][:8]
    test_cases = [c for c in selected_sorted if c.get("split") == "round3_test" and c["case_id"] not in excluded_ids][:6]
    all_selected = baseline + dev_cases + test_cases
    selected_ids = {case["case_id"] for case in all_selected}
    write_json(SPLIT_DIR / "recovery_dev_cases.json", dev_cases)
    write_json(SPLIT_DIR / "recovery_test_cases.json", test_cases)
    write_json(SPLIT_DIR / "recovery_baseline_cases.json", baseline)
    write_jsonl(SPLIT_DIR / "recovery_all_selected_cases.jsonl", all_selected)
    write_jsonl(SPLIT_DIR / "recovery_required_facts.jsonl", [fact for fact in required_fact_rows if fact.get("case_id") in selected_ids])
    write_jsonl(SPLIT_DIR / "recovery_model_visible_formula_contracts.jsonl", [row for row in visible_rows if row["case_id"] in selected_ids])
    write_jsonl(SPLIT_DIR / "recovery_scorer_only_target_slot_contracts.jsonl", [row for row in scorer_rows if row["case_id"] in selected_ids])
    go = len(test_cases) >= 5 and all(len(row["scorer_only_target_slot_contract"].get("target_slots", [])) > 0 for row in scorer_rows if row["case_id"] in selected_ids) and bool(all_selected)
    decision = "GO" if go else "NO_GO"
    write(
        SPLIT_DIR / "recovery_split_summary.md",
        "# Recovery Split Summary\n\n"
        f"- recovery_dev cases: {len(dev_cases)}\n"
        f"- recovery_test cases: {len(test_cases)}\n"
        f"- recovery_baseline cases: {len(baseline)}\n"
        f"- GO/NO_GO: `{decision}`\n",
    )
    write(SPLIT_DIR / "recovery_claim_boundary.md", "# Recovery Claim Boundary\n\n" + CLAIM + "\n")
    write(SPLIT_DIR / "report_front_page_claim_boundary.md", "# Report Front Page Claim Boundary\n\n" + CLAIM + "\n")
    write(
        SPLIT_DIR / "recovery_go_no_go.md",
        "# Recovery Go / No-Go\n\n"
        f"Decision: `{decision}`\n\n"
        "GO criteria require recovery_test >= 5, target_slot_count > 0 for all selected cases, oracle sanity 100%, exact quote coverage 100%, formula ambiguity 0, derived leakage 0, and method fairness 100%.\n",
    )
    version = {
        "formula_contract_generator_version": sha_file(ROOT / "scripts" / "round3_formula_contract_v3_2.py"),
        "formula_type_dictionary_hash": sha_file(CONTRACT_DIR / "formula_type_dictionary.json"),
        "scorer_version": sha_file(SCORER_DIR / "scorer_v3_2_formula_aware.py"),
        "prompt_hash": sha_file(PROMPT_DIR / "prompt_v3_2_system.md") + ":" + sha_file(PROMPT_DIR / "prompt_v3_2_user_templates.md"),
        "graph_fact_formatter_hash": sha_file(PROMPT_DIR / "graph_fact_formatter_v3_2.md"),
        "oracle_sanity_check_hash": sha_text("oracle_correct|blank|wrong_formula|wrong_year|wrong_denominator|unit_mismatch|source_facts_only"),
        "selected_case_list_hash": sha_text(json.dumps([case["case_id"] for case in all_selected], sort_keys=True)),
        "required_facts_hash": sha_text(json.dumps([fact for fact in required_fact_rows if fact.get("case_id") in selected_ids], ensure_ascii=False, sort_keys=True)),
        "test_locked": True,
        "requires_new_user_approval_if_changed": True,
    }
    write_json(SPLIT_DIR / "recovery_eval_version_freeze.json", version)
    write(SPLIT_DIR / "recovery_eval_version_freeze.md", "# Recovery Eval Version Freeze\n\n" + "\n".join(f"- {key}: `{value}`" for key, value in version.items()) + "\n")

    method_rows = []
    for case in dev_cases + baseline:
        for method in ["vector_only_v3_2", "graph_facts_only_v3_2", "hybrid_vector_graph_v3_2", "gold_context_v3_2"]:
            method_rows.append({"case_id": case["case_id"], "split": case.get("split", ""), "method": method, "approved": False})
    write(
        APPROVAL_DIR / "recovery_dev_run_scope.md",
        "# Recovery Dev Run Scope\n\n"
        f"- Proposed recovery_dev cases: {len(dev_cases)}\n"
        f"- Proposed recovery_baseline cases: {len(baseline)}\n"
        "- Model/API calls are not approved by this package.\n"
        "- Test evaluation is not approved by this package.\n",
    )
    write_csv(APPROVAL_DIR / "recovery_dev_run_method_matrix.csv", method_rows, ["case_id", "split", "method", "approved"])
    write(
        APPROVAL_DIR / "recovery_dev_run_approval_template.md",
        "# Recovery Dev Run Approval Template\n\n"
        "To approve a future dev run, explicitly approve: dev run scope, model/API calls, and logging mode. This file is not approval by itself.\n",
    )
    write(APPROVAL_DIR / "recovery_opik_status.md", "# Recovery Opik Status\n\nOpik is not configured in the prior locked test output. Locked test requires Opik or explicit local-only waiver.\n")
    write(APPROVAL_DIR / "recovery_local_only_waiver_template.md", "# Recovery Local-Only Waiver Template\n\nNo waiver is granted by this file. User must explicitly approve local-only logging for any run.\n")

    created = [
        *[rel(path) for path in sorted(FREEZE_DIR.iterdir()) if path.is_file()],
        *[rel(path) for path in sorted(POOL_DIR.iterdir()) if path.is_file()],
        *[rel(path) for path in sorted(VALIDATION_DIR.iterdir()) if path.is_file()],
        *[rel(path) for path in sorted(SPLIT_DIR.iterdir()) if path.is_file()],
        *[rel(path) for path in sorted(APPROVAL_DIR.iterdir()) if path.is_file()],
    ]
    final = {
        "invalid locked test frozen": "yes",
        "recovery candidate pool created": "yes",
        "formula-contract-first validation completed": "yes",
        "recovery dev cases": len(dev_cases),
        "recovery test cases": len(test_cases),
        "recovery baseline cases": len(baseline),
        "oracle sanity pass rate": oracle_pass_rate,
        "GO/NO_GO": decision,
        "model/API called": "no",
        "test eval executed": "no",
        "full eval executed": "no",
        "Neo4j write performed": "no",
        "KG patch applied": "no",
        "next required user action": "review NO_GO package and approve a new exact-quote recovery case factory; no model run is currently valid" if decision == "NO_GO" else "review dev run approval package",
        "created files": created,
    }
    print(json.dumps(final, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
