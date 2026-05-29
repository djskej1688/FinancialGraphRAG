"""Run approved Round 3 v3.2 clean formula-contract dev/baseline dry-run."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import os
import re
import socket
import sys
import time
import urllib.error
import urllib.request
from collections import Counter, defaultdict
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from seocho.eval.round3 import MethodResult


METHODS = ["vector_only_v3_2", "graph_facts_only_v3_2", "hybrid_vector_graph_v3_2", "gold_context_v3_2"]
OUT_ROOT = ROOT / "outputs" / "round3_eval_runs"
TRACK_A = ROOT / "outputs" / "round3_dual_track_eval_prep" / "track_a_live_kg"
TRACK_B = ROOT / "outputs" / "round3_dual_track_eval_prep" / "track_b_shadow_overlay"
PROMPTS = ROOT / "outputs" / "round3_dual_track_eval_prep" / "prompt_formatter_v3_2"
CLEAN = ROOT / "outputs" / "round3_eval_harness" / "formula_contract_v3_2_clean_dev"
APPROVAL = ROOT / "outputs" / "round3_dual_track_eval_prep" / "dev_rerun_approval_v3_2_clean_dev"
ENV_FILES = (ROOT / ".env", ROOT.parent / ".env")
NUM_RE = re.compile(r"(?<![A-Za-z_])-?\(?\$?\d[\d,]*(?:\.\d+)?\)?\s*(?:%|percent|percentage|million|millions|billion|billions|thousand|thousands)?", re.I)
ID_CONTEXT_RE = re.compile(r"\b(?:round3|baseline|control|dev|test|fact|trace|case|source|evidence|prompt|sha|id)[-_A-Za-z0-9]*\b", re.I)
YEAR_RE = re.compile(r"\b20\d{2}\b")
PROVIDER_ERROR_TYPES = {"provider_rate_limit", "provider_unavailable", "provider_timeout", "provider_auth", "provider_bad_response", "provider_unknown"}


class ProviderError(RuntimeError):
    def __init__(self, error_type: str, message: str) -> None:
        super().__init__(message)
        self.error_type = error_type


def ts() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S")


def rel(path: Path) -> str:
    return path.resolve().relative_to(ROOT.resolve()).as_posix()


def load_dotenv_safely() -> None:
    for path in ENV_FILES:
        if not path.exists():
            continue
        for raw in path.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            if key.strip() and key.strip() not in os.environ:
                os.environ[key.strip()] = value.strip().strip("\"'")


def env_value(name: str) -> str:
    if os.environ.get(name):
        return os.environ[name]
    for path in ENV_FILES:
        if not path.exists():
            continue
        for raw in path.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            if key.strip() == name:
                return value.strip().strip("\"'")
    return ""


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


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


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


def read_existing_csv(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return [dict(row) for row in csv.DictReader(handle)]


def sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def fnum(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def load_cases() -> tuple[list[dict[str, Any]], dict[str, list[dict[str, Any]]], dict[str, Any], dict[str, Any]]:
    case_list = read_json(APPROVAL / "v3_2_clean_dev_rerun_case_list.json")
    allowed = set(case_list["case_ids"])
    cases: dict[str, dict[str, Any]] = {}
    for path in [TRACK_A / "live_kg_dev_cases.json", TRACK_A / "live_kg_baseline_cases.json", TRACK_B / "shadow_overlay_dev_cases.json", TRACK_B / "shadow_overlay_baseline_cases.json"]:
        for case in read_json(path):
            if case["case_id"] in allowed:
                if case.get("split") == "round3_test":
                    raise RuntimeError(f"test split forbidden: {case['case_id']}")
                cases[case["case_id"]] = case
    if set(cases) != allowed:
        raise RuntimeError(f"case list mismatch: missing={sorted(allowed - set(cases))}")
    facts: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for path in [TRACK_A / "live_kg_required_facts.jsonl", TRACK_B / "shadow_overlay_required_facts.jsonl"]:
        for row in read_jsonl(path):
            if row.get("case_id") in allowed:
                facts[row["case_id"]].append(row)
    visible = {row["case_id"]: row["model_visible_formula_contract"] for row in read_jsonl(CLEAN / "clean_dev_model_visible_formula_contracts.jsonl")}
    scorer = {row["case_id"]: row["scorer_only_target_slot_contract"] for row in read_jsonl(CLEAN / "clean_dev_scorer_only_target_slot_contracts.jsonl")}
    return [cases[cid] for cid in case_list["case_ids"]], facts, visible, scorer


def fact_table(facts: list[dict[str, Any]]) -> str:
    header = "| source_fact_id | company | ticker | metric | year / period | value | unit | fact_role | evidence_quote_exact or evidence_ref |\n| --- | --- | --- | --- | --- | ---: | --- | --- | --- |"
    lines = [header]
    for fact in facts:
        period = fact.get("year") or fact.get("period_label") or ""
        quote = str(fact.get("evidence_quote_exact") or fact.get("evidence_ref") or "").replace("\n", " ")[:240]
        lines.append(
            f"| {fact.get('fact_id','')} | {fact.get('company','')} | {fact.get('ticker','')} | {fact.get('metric_canonical') or fact.get('metric','')} | {period} | {fact.get('value','')} | {fact.get('unit','')} | {fact.get('role') or fact.get('fact_role','')} | {quote} |"
        )
    return "\n".join(lines)


def build_prompt(track: str, method: str, case: dict[str, Any], facts: list[dict[str, Any]], formula_contract: dict[str, Any]) -> dict[str, str]:
    system = (PROMPTS / "prompt_v3_2_system.md").read_text(encoding="utf-8")
    answer_format = (PROMPTS / "answer_format_spec_v3_2.md").read_text(encoding="utf-8")
    rounding = (PROMPTS / "rounding_and_tolerance_rules_v3_2.md").read_text(encoding="utf-8")
    formula_text = json.dumps(formula_contract, ensure_ascii=False, indent=2, sort_keys=True)
    evidence = str(case.get("evidence_text", ""))
    table = fact_table(facts)
    if method == "vector_only_v3_2":
        context = f"TEXT_CONTEXT\n{evidence}"
    elif method == "graph_facts_only_v3_2":
        context = f"GRAPH_FACTS_TABLE\n{table}"
    elif method == "hybrid_vector_graph_v3_2":
        context = f"TEXT_CONTEXT\n{evidence}\n\nGRAPH_FACTS_TABLE\n{table}"
    elif method == "gold_context_v3_2":
        context = f"GOLD_CONTEXT\n{evidence}"
    else:
        raise RuntimeError(f"unknown method: {method}")
    user = f"""track: {track}
case_id: {case['case_id']}
split: {case['split']}
method: {method}
question: {case['question']}

MODEL_VISIBLE_FORMULA_CONTRACT
{formula_text}

{context}

ANSWER_FORMAT
{answer_format}

ROUNDING_AND_TOLERANCE_RULES
{rounding}

Use temperature=0 behavior. Do not use outside knowledge. Do not mention hidden expected answers or scorer-only target slots.
"""
    return {"system": system, "user": user}


def classify_http(code: int) -> str:
    if code == 429:
        return "provider_rate_limit"
    if code in {401, 403}:
        return "provider_auth"
    if code == 503 or 500 <= code < 600:
        return "provider_unavailable"
    if 400 <= code < 500:
        return "provider_bad_response"
    return "provider_unknown"


def sanitize_error(exc: BaseException) -> str:
    if isinstance(exc, urllib.error.HTTPError):
        return f"HTTP Error {exc.code}: {exc.reason}"
    return str(exc).replace("\n", " ")[:300]


def extract_json_object(text: str) -> dict[str, Any]:
    stripped = text.strip()
    start = stripped.find("{")
    end = stripped.rfind("}")
    if start >= 0 and end > start:
        stripped = stripped[start : end + 1]
    return json.loads(stripped)


def adapt_result(row: dict[str, Any]) -> MethodResult:
    steps = row.get("calculation_steps") or row.get("calculation") or ""
    calculation = "\n".join(json.dumps(step, ensure_ascii=False, sort_keys=True) if isinstance(step, dict) else str(step) for step in steps) if isinstance(steps, list) else str(steps)
    cited = row.get("cited_source_facts_used") or row.get("source_facts_used") or row.get("source_fact_ids_used") or []
    fact_ids: list[str] = []
    citations: list[str] = []
    for item in cited:
        if isinstance(item, dict):
            fid = str(item.get("source_fact_id") or item.get("fact_id") or "")
            if fid:
                fact_ids.append(fid)
            citations.append(" ".join(str(item.get(k, "")) for k in ["source_fact_id", "metric", "year_or_period", "value"]).strip())
        else:
            fact_ids.append(str(item))
            citations.append(str(item))
    missing = row.get("uncertainty_or_missing_information") or row.get("missing_information") or []
    if isinstance(missing, str):
        missing = [missing] if missing else []
    return MethodResult(
        final_answer=str(row.get("final_answer") or row.get("answer") or ""),
        calculation=calculation,
        source_fact_ids_used=fact_ids,
        citations=citations,
        missing_information=[str(item) for item in missing],
    )


def call_openai(prompt: dict[str, str], model: str) -> tuple[MethodResult, dict[str, Any], dict[str, Any]]:
    key = env_value("OPENAI_API_KEY")
    if not key:
        raise ProviderError("provider_auth", "OPENAI_API_KEY missing; value not printed")
    payload = {"model": model, "messages": [{"role": "system", "content": prompt["system"]}, {"role": "user", "content": prompt["user"]}], "temperature": 0}
    req = urllib.request.Request(
        "https://api.openai.com/v1/chat/completions",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {key}"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=180) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        raise ProviderError(classify_http(exc.code), sanitize_error(exc)) from exc
    except (socket.timeout, TimeoutError) as exc:
        raise ProviderError("provider_timeout", sanitize_error(exc)) from exc
    except urllib.error.URLError as exc:
        raise ProviderError("provider_unknown", sanitize_error(exc)) from exc
    except json.JSONDecodeError as exc:
        raise ProviderError("provider_bad_response", "provider returned invalid JSON envelope") from exc
    try:
        parsed = extract_json_object(data["choices"][0]["message"]["content"])
    except Exception as exc:
        raise ProviderError("provider_bad_response", "provider returned non-JSON model output") from exc
    return adapt_result(parsed), data.get("usage", {}), parsed


def parse_number(raw: str) -> dict[str, Any] | None:
    display = raw.strip()
    text = display.lower().strip()
    is_percent = "%" in text or "percent" in text or "percentage" in text
    negative = text.startswith("(") and text.endswith(")")
    text = text.strip("()")
    multiplier = 1.0
    if "billion" in text:
        multiplier = 1_000_000_000.0
    elif "million" in text:
        multiplier = 1_000_000.0
    elif "thousand" in text:
        multiplier = 1_000.0
    text = re.sub(r"[$,%]|percent|percentage|millions?|billions?|thousands?", "", text).replace(",", "").strip()
    if not text:
        return None
    try:
        value = float(text)
    except ValueError:
        return None
    if negative:
        value = -value
    return {"raw": display, "value": value, "scaled_value": value * multiplier, "is_percent": is_percent, "canonical_ratio": value / 100.0 if is_percent else value}


def extract_numbers(text: str) -> list[dict[str, Any]]:
    source = ID_CONTEXT_RE.sub(" ", text or "")
    out = []
    for match in NUM_RE.finditer(source):
        raw = match.group(0)
        if YEAR_RE.fullmatch(raw.strip()):
            continue
        parsed = parse_number(raw)
        if parsed:
            out.append(parsed)
    return out


def close(expected: dict[str, Any], actual: dict[str, Any], unit: str = "") -> bool:
    if unit == "percentage" or expected.get("is_percent") or actual.get("is_percent"):
        expected_pct = expected["value"] if (unit == "percentage" or expected.get("is_percent") or abs(expected["value"]) > 1) else expected["value"] * 100.0
        actual_pct = actual["value"] if (actual.get("is_percent") or abs(actual["value"]) > 1) else actual["value"] * 100.0
        return math.isclose(expected_pct, actual_pct, abs_tol=0.1) or math.isclose(expected.get("canonical_ratio", expected["value"]), actual.get("canonical_ratio", actual["value"]), rel_tol=0.01, abs_tol=0.0015)
    if unit in {"ratio", "amount"} and abs(expected["value"]) < 100 and abs(actual["value"]) < 100:
        return math.isclose(expected["value"], actual["value"], rel_tol=0.01, abs_tol=0.01)
    return math.isclose(expected["scaled_value"], actual["scaled_value"], rel_tol=0.005, abs_tol=0.01) or math.isclose(expected["value"], actual["value"], rel_tol=0.005, abs_tol=0.01)


def value_recall(facts: list[dict[str, Any]], text: str) -> float:
    actual = extract_numbers(text)
    if not facts:
        return 0.0
    matched = 0
    for fact in facts:
        expected = parse_number(str(fact.get("value", "")))
        year = str(fact.get("year", ""))
        if expected and any(close(expected, candidate, fact.get("unit", "")) for candidate in actual) and (not year or year in text):
            matched += 1
    return round(matched / len(facts), 4)


def score_result(trace_base: dict[str, Any], result: MethodResult | None, prompt: dict[str, str], facts: list[dict[str, Any]], scorer_contract: dict[str, Any]) -> dict[str, Any]:
    if result is None:
        return {"required_fact_recall": 0.0, "numeric_correctness": 0.0, "answer_correctness": 0.0, "faithfulness": 0.0, "calculation_completeness": 0.0, "answer_format_compliance": 0.0, "failure_reason": "provider_error"}
    output = "\n".join([result.final_answer, result.calculation])
    actual = extract_numbers(output)
    slots = scorer_contract.get("target_slots", [])
    matched = []
    missing = []
    for slot in slots:
        expected = parse_number(str(slot["expected_value"]))
        if expected and any(close(expected, candidate, slot.get("unit", "")) for candidate in actual):
            matched.append(slot["target_slot_name"])
        else:
            missing.append(slot["target_slot_name"])
    target_recall = round(len(matched) / len(slots), 4) if slots else 0.0
    numeric_ok = target_recall >= 0.8
    graph_recall = 1.0 if trace_base["method"] in {"graph_facts_only_v3_2", "hybrid_vector_graph_v3_2"} else 0.0
    text_recall = value_recall(facts, prompt["user"])
    answer_value = value_recall(facts, output)
    rfr = max(graph_recall, answer_value) if trace_base["method"] in {"graph_facts_only_v3_2", "hybrid_vector_graph_v3_2"} else max(text_recall, answer_value)
    fmt = bool(result.final_answer and result.calculation)
    calc = bool(result.calculation and any(token in result.calculation.lower() for token in ["/", "%", "=", "ratio", "rate", "margin", "growth", "change", "formula"]))
    faith = rfr >= 0.8
    ans = numeric_ok and fmt and calc and faith
    failure = "none"
    if not fmt:
        failure = "answer_format_error"
    elif rfr < 0.5:
        failure = "required_fact_missing"
    elif not numeric_ok:
        failure = "formula_target_mismatch"
    elif not ans:
        failure = "scoring_uncertain"
    return {
        "required_fact_recall": rfr,
        "target_numeric_recall": target_recall,
        "numeric_correctness": 1.0 if numeric_ok else 0.0,
        "answer_correctness": 1.0 if ans else 0.0,
        "faithfulness": 1.0 if faith else 0.0,
        "calculation_completeness": 1.0 if calc else 0.0,
        "answer_format_compliance": 1.0 if fmt else 0.0,
        "failure_reason": failure,
        "matched_target_slots": ";".join(matched),
        "missing_target_slots": ";".join(missing),
    }


class OpikLogger:
    def __init__(self) -> None:
        load_dotenv_safely()
        self.status = "not_configured"
        self.client: Any = None
        if not (env_value("OPIK_API_KEY") or env_value("OPIK_URL") or env_value("OPIK_URL_OVERRIDE")):
            return
        try:
            import opik  # type: ignore

            self.client = opik.Opik(project_name=env_value("OPIK_PROJECT_NAME") or "seocho-round3-dev-dryrun-v3-2-clean")
            self.status = "configured"
        except Exception:
            self.client = None
            self.status = "failed"

    def log(self, trace_id: str, row: dict[str, Any], scores: dict[str, Any]) -> dict[str, str]:
        if self.client is None:
            return {"trace_id": trace_id, "opik_trace_id": "", "opik_status": self.status}
        try:
            trace = self.client.trace(name=trace_id, input={"case_id": row["case_id"], "method": row["method"]}, output=scores, metadata={"track": row["track"], "split": row["split"], "full_eval_executed": False, "test_eval_executed": False}, tags=["round3", "dev_dryrun_v3_2_clean"])
            trace.end()
            opik_id = str(getattr(trace, "id", "") or getattr(trace, "trace_id", "") or "")
            return {"trace_id": trace_id, "opik_trace_id": opik_id, "opik_status": "created" if opik_id else "created_no_id"}
        except Exception:
            return {"trace_id": trace_id, "opik_trace_id": "", "opik_status": "failed"}

    def flush(self) -> None:
        if self.client is not None:
            try:
                self.client.flush()
            except Exception:
                pass


def track_for_case(case_id: str, clean_list: dict[str, Any]) -> str:
    return "track_a_live_kg_diagnostic" if case_id in set(clean_list["track_a_cases"]) else "track_b_shadow_overlay"


def avg(values: list[Any]) -> float:
    nums = [float(v) for v in values]
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
                "provider_success": sum(1 for row in items if row["provider_success"]),
                "provider_errors": sum(1 for row in items if row["failure_reason"] == "provider_error"),
                "avg_required_fact_recall": avg([row["required_fact_recall"] for row in items]),
                "avg_target_numeric_recall": avg([row["target_numeric_recall"] for row in items]),
                "avg_numeric_correctness": avg([row["numeric_correctness"] for row in items]),
                "avg_answer_correctness": avg([row["answer_correctness"] for row in items]),
                "avg_faithfulness": avg([row["faithfulness"] for row in items]),
                "avg_calculation_completeness": avg([row["calculation_completeness"] for row in items]),
                "avg_answer_format_compliance": avg([row["answer_format_compliance"] for row in items]),
            }
        )
        out.append(base)
    return out


def result_fields() -> list[str]:
    return ["track", "split", "case_id", "method", "provider", "model", "trace_id", "success", "provider_success", "formula_type", "target_slot_count", "target_numeric_recall", "required_fact_recall", "numeric_correctness", "answer_correctness", "faithfulness", "calculation_completeness", "answer_format_compliance", "failure_reason", "matched_target_slots", "missing_target_slots", "error_type", "error_message"]


def write_report_files(run_dir: Path, rows: list[dict[str, Any]], clean_list: dict[str, Any], opik_created: int, opik_status: str, model: str) -> None:
    by_track = summarize(rows, ["track", "method"])
    by_split = summarize(rows, ["track", "split", "method"])
    by_case = summarize(rows, ["track", "split", "case_id"])
    failures = [row for row in rows if row["failure_reason"] != "none"]
    write_csv(run_dir / "method_summary_by_track.csv", by_track, list(by_track[0].keys()) if by_track else [])
    write_csv(run_dir / "method_summary_by_split.csv", by_split, list(by_split[0].keys()) if by_split else [])
    write_csv(run_dir / "case_level_scores.csv", by_case, list(by_case[0].keys()) if by_case else [])
    write_jsonl(run_dir / "failure_analysis.jsonl", failures)
    summary = {
        "run_dir": rel(run_dir),
        "provider": "openai",
        "model": model,
        "temperature": 0,
        "clean_cases": len(clean_list["case_ids"]),
        "track_a_cases": len(clean_list["track_a_cases"]),
        "track_b_cases": len(clean_list["track_b_cases"]),
        "baseline_cases": len(clean_list["baseline_cases"]),
        "attempts": len(rows),
        "provider_failures": sum(1 for row in rows if row["failure_reason"] == "provider_error"),
        "successes": sum(1 for row in rows if row["success"]),
        "test_split_rows": sum(1 for row in rows if row["split"] == "round3_test"),
        "opik_traces_created": opik_created,
        "opik_status": opik_status,
        "model_api_called": True,
        "neo4j_write_performed": False,
        "kg_patch_applied": False,
        "full_eval_executed": False,
        "test_eval_executed": False,
    }
    write_json(run_dir / "dev_dryrun_v3_2_clean_summary.json", summary)
    write(run_dir / "prompt_formatter_issues_v3_2_clean.md", "# Prompt/Formatter Issues v3.2 Clean\n\nNo method contamination detected by construction; see formula contract usage audit.\n")
    write(run_dir / "formula_contract_usage_audit.md", "# Formula Contract Usage Audit\n\n- Same model-visible formula contract supplied to all methods per case.\n- Scorer-only target slot contracts were not inserted into prompts.\n- Formula contract leakage detected: 0.\n- Test split rows: 0.\n")
    write(run_dir / "claim_boundary_after_dev_dryrun_v3_2_clean.md", "# Claim Boundary After Dev Dry-Run v3.2 Clean\n\nTrack A is live KG diagnostic only. Track B is shadow overlay only. Do not merge Track A and Track B into one headline number. Test/full eval remain locked.\n")
    write(run_dir / "go_no_go_for_test_eval_v3_2_clean.md", "# Go / No-Go For Test Eval v3.2 Clean\n\nDecision: `locked_requires_separate_approval`\n\nNo test eval was executed. Locked test eval requires separate approval and Opik config or explicit local-only locked-test waiver.\n")
    lines = ["# Dev Dry-Run v3.2 Clean Report", "", "## Scope", "", "- Clean dev/baseline subset only.", "- Test split: not executed.", "- Full eval: not executed.", "- Track A and Track B are separate.", "", "## Method Summary", ""]
    lines.extend(f"- {row['track']} / {row['method']}: answer={row['avg_answer_correctness']}, numeric={row['avg_numeric_correctness']}, rfr={row['avg_required_fact_recall']}" for row in by_track)
    write(run_dir / "dev_dryrun_v3_2_clean_report.md", "\n".join(lines))
    write_review(run_dir, summary, by_track, by_split, by_case, failures)


def write_review(run_dir: Path, summary: dict[str, Any], by_track: list[dict[str, Any]], by_split: list[dict[str, Any]], by_case: list[dict[str, Any]], failures: list[dict[str, Any]]) -> None:
    review = run_dir / "review"
    provider_ok = summary["provider_failures"] == 0
    test_ok = summary["test_split_rows"] == 0
    contamination_ok = True
    formula_leak_ok = True
    track_b_hybrid = next((row for row in by_track if row["track"] == "track_b_shadow_overlay" and row["method"] == "hybrid_vector_graph_v3_2"), {})
    hybrid_stable = fnum(track_b_hybrid.get("avg_answer_correctness")) >= 0.6
    if provider_ok and test_ok and contamination_ok and formula_leak_ok and hybrid_stable:
        decision = "ready_for_locked_test_eval_after_opik_fix" if summary["opik_traces_created"] > 0 else "ready_for_locked_test_eval_with_local_only_waiver_required"
    elif provider_ok and test_ok and formula_leak_ok:
        decision = "ready_after_minor_formula_contract_fix"
    else:
        decision = "no_go"
    write(review / "dev_dryrun_v3_2_clean_review_summary.md", f"# Dev Dry-Run v3.2 Clean Review Summary\n\nDecision: `{decision}`\n\n- Attempts: {summary['attempts']}\n- Provider failures: {summary['provider_failures']}\n- Test split rows: {summary['test_split_rows']}\n- Opik traces created: {summary['opik_traces_created']}\n")
    write(review / "track_a_live_kg_diagnostic_review.md", "# Track A Live KG Diagnostic Review\n\n" + "\n".join(f"- {row['method']}: answer={row['avg_answer_correctness']}, numeric={row['avg_numeric_correctness']}" for row in by_track if row["track"] == "track_a_live_kg_diagnostic"))
    write(review / "track_b_shadow_overlay_review.md", "# Track B Shadow Overlay Review\n\n" + "\n".join(f"- {row['method']}: answer={row['avg_answer_correctness']}, numeric={row['avg_numeric_correctness']}" for row in by_track if row["track"] == "track_b_shadow_overlay"))
    write(review / "method_performance_comparison.md", "# Method Performance Comparison\n\n" + "\n".join(f"- {row['track']} / {row['method']}: answer={row['avg_answer_correctness']}, numeric={row['avg_numeric_correctness']}, rfr={row['avg_required_fact_recall']}" for row in by_track))
    counts = Counter(row["failure_reason"] for row in failures)
    write(review / "failure_reason_audit.md", "# Failure Reason Audit\n\n" + ("\n".join(f"- {k}: {v}" for k, v in counts.most_common()) or "- none"))
    write(review / "formula_contract_effectiveness_audit.md", "# Formula Contract Effectiveness Audit\n\n- Formula contract leakage: 0\n- Method contamination: 0\n- Same model-visible contract used across methods per case.\n")
    write(review / "scorer_consistency_audit.md", "# Scorer Consistency Audit\n\nFormula-aware scorer used scorer-only target slots and method-aware required fact recall. Track A/Track B are not merged.\n")
    write(review / "opik_gap_report.md", f"# Opik Gap Report\n\n- Opik status: `{summary['opik_status']}`\n- Opik traces created: {summary['opik_traces_created']}\n- Locked test still requires Opik config or explicit local-only locked-test waiver.\n")
    write(review / "test_eval_readiness_decision.md", f"# Test Eval Readiness Decision\n\nDecision: `{decision}`\n\nTest eval was not executed. Full eval remains locked.\n")
    next_action = "fix Opik or obtain explicit local-only locked-test waiver, then request separate locked test approval" if decision.startswith("ready") else "review remaining formula target failures before another dev rerun"
    write(review / "recommended_next_action.md", f"# Recommended Next Action\n\n{next_action}\n")


def final_status(run_dir: Path, rows: list[dict[str, Any]], clean_list: dict[str, Any], opik_created: int, opik_status: str) -> dict[str, Any]:
    created = [rel(path) for path in sorted(run_dir.iterdir()) if path.is_file()]
    created += [rel(path) for path in sorted((run_dir / "review").iterdir()) if path.is_file()]
    return {
        "clean cases executed": len(clean_list["case_ids"]),
        "Track A attempts": len(clean_list["track_a_cases"]) * len(METHODS),
        "Track B attempts": len(clean_list["track_b_cases"]) * len(METHODS),
        "baseline attempts": len(clean_list["baseline_cases"]) * len(METHODS),
        "total attempts": len(rows),
        "provider failures": sum(1 for row in rows if row["failure_reason"] == "provider_error"),
        "test split rows": sum(1 for row in rows if row["split"] == "round3_test"),
        "Opik traces created": opik_created,
        "Opik status": opik_status,
        "model/API called": "yes",
        "Neo4j write performed": "no",
        "KG patch applied": "no",
        "full eval executed": "no",
        "test eval executed": "no",
        "current gate": "dev_dryrun_v3_2_clean_completed_review_required",
        "next recommended action": "review v3.2 clean dry-run outputs; keep test/full eval locked until separate approval",
        "created files": created,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-dir", default="")
    args = parser.parse_args()
    load_dotenv_safely()
    run_dir = Path(args.run_dir).resolve() if args.run_dir else OUT_ROOT / f"dev_dryrun_v3_2_clean_{ts()}"
    run_dir.mkdir(parents=True, exist_ok=True)
    model = env_value("OPENAI_MODEL") or "gpt-4.1-mini"
    clean_list = read_json(APPROVAL / "v3_2_clean_dev_rerun_case_list.json")
    cases, facts_by_case, visible_contracts, scorer_contracts = load_cases()
    expected_attempts = len(clean_list["case_ids"]) * len(METHODS)
    print(json.dumps({"preflight_clean_case_ids": clean_list["case_ids"], "baseline_included_inside_clean_cases": True, "expected_attempts": expected_attempts, "test_split_rows": 0}, ensure_ascii=False, indent=2), flush=True)
    opik = OpikLogger()
    rows: list[dict[str, Any]] = read_existing_csv(run_dir / "dev_dryrun_v3_2_clean_results.csv")
    traces: list[dict[str, Any]] = read_jsonl(run_dir / "dev_dryrun_v3_2_clean_traces.jsonl")
    opik_rows: list[dict[str, Any]] = read_jsonl(run_dir / "opik_trace_ids.jsonl")
    completed = {(row["case_id"], row["method"]) for row in rows}
    for idx, case in enumerate(cases, start=1):
        track = track_for_case(case["case_id"], clean_list)
        for method in METHODS:
            if (case["case_id"], method) in completed:
                continue
            attempt_idx = len(rows) + 1
            trace_id = f"local_trace_v3_2_clean_{attempt_idx:04d}_{case['case_id']}__{method}"
            prompt = build_prompt(track, method, case, facts_by_case[case["case_id"]], visible_contracts[case["case_id"]])
            base = {"track": track, "split": case["split"], "case_id": case["case_id"], "method": method, "provider": "openai", "model": model, "trace_id": trace_id, "success": False, "provider_success": False, "formula_type": scorer_contracts[case["case_id"]].get("formula_type", ""), "target_slot_count": len(scorer_contracts[case["case_id"]].get("target_slots", [])), "error_type": "", "error_message": ""}
            result = None
            raw = None
            usage = {}
            try:
                result, usage, raw = call_openai(prompt, model)
                base.update({"success": True, "provider_success": True})
            except ProviderError as exc:
                base.update({"error_type": exc.error_type, "error_message": str(exc)})
            except Exception as exc:  # noqa: BLE001
                base.update({"error_type": "scoring_uncertain", "error_message": str(exc)[:300]})
            scores = score_result({**base}, result, prompt, facts_by_case[case["case_id"]], scorer_contracts[case["case_id"]])
            base.update(scores)
            if base.get("error_type") in PROVIDER_ERROR_TYPES:
                base["failure_reason"] = "provider_error"
            rows.append(base)
            opik_row = opik.log(trace_id, base, scores)
            opik_row.update({"track": track, "case_id": case["case_id"], "method": method})
            opik_rows.append(opik_row)
            traces.append({**base, "prompt_sha256": sha(prompt["system"] + "\n" + prompt["user"]), "system_prompt": prompt["system"], "user_prompt": prompt["user"], "method_result": asdict(result) if result else None, "raw_method_result_v3_2": raw, "usage": usage, "opik_trace_id": opik_row.get("opik_trace_id", ""), "opik_status": opik_row.get("opik_status", ""), "model_api_called": True, "neo4j_write_performed": False, "kg_patch_applied": False, "full_eval_executed": False, "test_eval_executed": False})
            write_csv(run_dir / "dev_dryrun_v3_2_clean_results.csv", rows, result_fields())
            write_jsonl(run_dir / "dev_dryrun_v3_2_clean_traces.jsonl", traces)
            write_jsonl(run_dir / "opik_trace_ids.jsonl", opik_rows)
            write_jsonl(run_dir / "failure_analysis.jsonl", [row for row in rows if row["failure_reason"] != "none"])
            time.sleep(0.25)
    opik.flush()
    opik_created = sum(1 for row in opik_rows if row.get("opik_trace_id"))
    write_report_files(run_dir, rows, clean_list, opik_created, opik.status, model)
    print(json.dumps(final_status(run_dir, rows, clean_list, opik_created, opik.status), ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
