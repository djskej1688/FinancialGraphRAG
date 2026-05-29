"""Run approved Round 3 dual-track dev/baseline dry-run v3.1.

Approved scope only:
- Track A live KG diagnostic dev + baseline
- Track B shadow overlay dev + baseline
- Methods: vector_only_v3_1, graph_facts_only_v3_1, hybrid_vector_graph_v3_1, gold_context_v3_1

Forbidden by construction:
- test split
- full eval
- Neo4j write / KG patch
- model/API calls outside approved dev/baseline scope
"""

from __future__ import annotations

import csv
import argparse
import hashlib
import json
import os
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

from seocho.eval.round3 import MethodResult, score_answer_correctness, score_numeric_correctness, score_required_fact_recall
from seocho.eval.round3.scoring import RequiredFact


METHODS = ["vector_only_v3_1", "graph_facts_only_v3_1", "hybrid_vector_graph_v3_1", "gold_context_v3_1"]
TRACK_A = ROOT / "outputs" / "round3_dual_track_eval_prep" / "track_a_live_kg"
TRACK_B = ROOT / "outputs" / "round3_dual_track_eval_prep" / "track_b_shadow_overlay"
PROMPTS = ROOT / "outputs" / "round3_dual_track_eval_prep" / "prompt_formatter_v3_1"
OUT_ROOT = ROOT / "outputs" / "round3_eval_runs"
ENV_FILES = (ROOT / ".env", ROOT.parent / ".env")
PROVIDER_ERROR_TYPES = {
    "provider_rate_limit",
    "provider_unavailable",
    "provider_timeout",
    "provider_auth",
    "provider_bad_response",
    "provider_unknown",
}


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
            key = key.strip()
            if key and key not in os.environ:
                os.environ[key] = value.strip().strip("\"'")


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


def group_by_case(rows: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[row["case_id"]].append(row)
    return grouped


def load_cases() -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, list[dict[str, Any]]], dict[str, list[dict[str, Any]]]]:
    track_a_cases = read_json(TRACK_A / "live_kg_dev_cases.json") + read_json(TRACK_A / "live_kg_baseline_cases.json")
    track_b_cases = read_json(TRACK_B / "shadow_overlay_dev_cases.json") + read_json(TRACK_B / "shadow_overlay_baseline_cases.json")
    track_a_facts = group_by_case(read_jsonl(TRACK_A / "live_kg_required_facts.jsonl"))
    track_b_facts = group_by_case(read_jsonl(TRACK_B / "shadow_overlay_required_facts.jsonl"))
    for case in track_a_cases + track_b_cases:
        if case.get("split") == "round3_test":
            raise RuntimeError(f"test split is forbidden in dev dry-run: {case.get('case_id')}")
    return track_a_cases, track_b_cases, track_a_facts, track_b_facts


def fact_table(facts: list[dict[str, Any]]) -> str:
    header = (
        "| source_fact_id | company | ticker | metric | year / period | value | unit | fact_role | evidence_quote_exact or evidence_ref |\n"
        "| --- | --- | --- | --- | --- | ---: | --- | --- | --- |"
    )
    lines = [header]
    for fact in facts:
        period = fact.get("year") or fact.get("period_label") or ""
        quote = str(fact.get("evidence_quote_exact") or fact.get("evidence_ref") or "").replace("\n", " ")[:240]
        lines.append(
            f"| {fact.get('fact_id','')} | {fact.get('company','')} | {fact.get('ticker','')} | "
            f"{fact.get('metric_canonical') or fact.get('metric','')} | {period} | {fact.get('value','')} | "
            f"{fact.get('unit','')} | {fact.get('role') or fact.get('fact_role','')} | {quote} |"
        )
    return "\n".join(lines)


def build_prompt(track: str, method: str, case: dict[str, Any], facts: list[dict[str, Any]]) -> dict[str, str]:
    system = (PROMPTS / "prompt_v3_1_system.md").read_text(encoding="utf-8")
    answer_format = (PROMPTS / "answer_format_spec_v3_1.md").read_text(encoding="utf-8")
    rounding = (PROMPTS / "rounding_and_tolerance_rules_v3_1.md").read_text(encoding="utf-8")
    reasoning = (PROMPTS / "reasoning_type_templates_v3_1.md").read_text(encoding="utf-8")
    table = fact_table(facts)
    evidence = str(case.get("evidence_text", ""))
    reasoning_type = case.get("reasoning_type") or case.get("question_type") or "unknown"
    if method == "vector_only_v3_1":
        context = f"TEXT_CONTEXT\n{evidence}"
    elif method == "graph_facts_only_v3_1":
        context = f"GRAPH_FACTS_TABLE\n{table}"
    elif method == "hybrid_vector_graph_v3_1":
        context = f"TEXT_CONTEXT\n{evidence}\n\nGRAPH_FACTS_TABLE\n{table}"
    elif method == "gold_context_v3_1":
        context = f"GOLD_CONTEXT\n{evidence}"
    else:
        raise RuntimeError(f"unknown method: {method}")
    user = f"""track: {track}
case_id: {case['case_id']}
split: {case['split']}
method: {method}
reasoning_type: {reasoning_type}
question: {case['question']}

{context}

REASONING_TYPE_TEMPLATES
{reasoning}

ANSWER_FORMAT
{answer_format}

ROUNDING_AND_TOLERANCE_RULES
{rounding}

Use temperature=0 behavior. Do not use outside knowledge. Do not mention hidden expected answers.
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


def adapt_v3_1_result(row: dict[str, Any]) -> MethodResult:
    steps = row.get("calculation_steps") or row.get("calculation") or ""
    if isinstance(steps, list):
        calculation = "\n".join(
            json.dumps(step, ensure_ascii=False, sort_keys=True) if isinstance(step, dict) else str(step)
            for step in steps
        )
    else:
        calculation = str(steps)
    cited = row.get("cited_source_facts_used") or row.get("source_fact_ids_used") or []
    fact_ids: list[str] = []
    citations: list[str] = []
    for item in cited:
        if isinstance(item, dict):
            fact_id = str(item.get("source_fact_id") or item.get("fact_id") or "")
            if fact_id:
                fact_ids.append(fact_id)
            metric = item.get("metric", "")
            period = item.get("year_or_period", "")
            value = item.get("value", "")
            citations.append(f"{fact_id} {metric} {period} {value}".strip())
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
        calculation_parse_error=str(row.get("calculation_parse_error")) if row.get("calculation_parse_error") else None,
    )


def call_openai(prompt: dict[str, str], model: str) -> tuple[MethodResult, dict[str, Any], dict[str, Any]]:
    key = env_value("OPENAI_API_KEY")
    if not key:
        raise ProviderError("provider_auth", "OPENAI_API_KEY missing; value not printed")
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": prompt["system"]},
            {"role": "user", "content": prompt["user"]},
        ],
        "temperature": 0,
    }
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
        content = data["choices"][0]["message"]["content"]
        parsed = extract_json_object(content)
    except Exception as exc:
        raise ProviderError("provider_bad_response", "provider returned non-JSON model output") from exc
    return adapt_v3_1_result(parsed), data.get("usage", {}), parsed


def score_result(case: dict[str, Any], facts: list[dict[str, Any]], method: str, result: MethodResult) -> dict[str, Any]:
    req_facts = [RequiredFact.from_mapping(row) for row in facts]
    metadata = {"retrieved_fact_ids": [row["fact_id"] for row in facts]} if "graph" in method else {}
    rfr = score_required_fact_recall(
        case_id=case["case_id"],
        required_facts=req_facts,
        method=method,
        method_input=None,
        method_result=result,
        retrieved_metadata=metadata,
    )
    num = score_numeric_correctness(str(case.get("expected_answer", "")), result)
    ans = score_answer_correctness(
        expected_answer=str(case.get("expected_answer", "")),
        required_facts=req_facts,
        method=method,
        method_result=result,
        method_input=None,
        retrieved_metadata=metadata,
    )
    format_ok = bool(result.final_answer and result.calculation and isinstance(result.source_fact_ids_used, list))
    calc_complete = bool(result.calculation and any(token in result.calculation.lower() for token in ["/", "%", "=", "ratio", "rate", "margin", "growth", "change", "formula"]))
    faithfulness = bool(rfr.required_fact_recall >= 0.95 and not result.missing_information)
    return {
        "required_fact_recall": rfr.to_dict(),
        "numeric_correctness": num.to_dict(),
        "answer_correctness": ans.to_dict(),
        "faithfulness": faithfulness,
        "calculation_completeness": calc_complete,
        "answer_format_compliance": format_ok,
    }


def failure_reason(row: dict[str, Any], scores: dict[str, Any] | None) -> str:
    if row.get("error_type") in PROVIDER_ERROR_TYPES:
        return "provider_error"
    if not row.get("success"):
        return row.get("error_type") or "scoring_uncertain"
    if not scores:
        return "scoring_uncertain"
    if not scores["answer_format_compliance"]:
        return "answer_format_error"
    if not scores["required_fact_recall"]["pass"]:
        return "graph_fact_missing" if "graph" in row["method"] else "vector_context_missing"
    if not scores["numeric_correctness"]["numeric_correctness"]:
        return "model_reasoning_error"
    if not scores["calculation_completeness"]:
        return "model_reasoning_error"
    return "none"


class OpikLogger:
    def __init__(self) -> None:
        load_dotenv_safely()
        self.status = "not_configured"
        self.client: Any = None
        if not (env_value("OPIK_API_KEY") or env_value("OPIK_URL") or env_value("OPIK_URL_OVERRIDE")):
            return
        try:
            import opik  # type: ignore

            project = env_value("OPIK_PROJECT_NAME") or "seocho-round3-dev-dryrun-v3-1"
            self.client = opik.Opik(project_name=project)
            self.status = "configured"
        except Exception:
            self.client = None
            self.status = "failed"

    def log(self, *, trace_id: str, prompt_hash: str, row: dict[str, Any], scores: dict[str, Any] | None) -> dict[str, str]:
        if self.client is None:
            return {"trace_id": trace_id, "opik_trace_id": "", "opik_status": self.status}
        try:
            trace = self.client.trace(
                name=trace_id,
                input={"prompt_hash": prompt_hash, "case_id": row["case_id"], "method": row["method"]},
                output={"success": row["success"], "scores": scores or {}},
                metadata={
                    "track": row["track"],
                    "split": row["split"],
                    "provider": row["provider"],
                    "model": row["model"],
                    "scorer_version": "round3_v3_1",
                    "full_eval_executed": False,
                    "test_eval_executed": False,
                },
                tags=["round3", "dev_dryrun_v3_1"],
            )
            trace.end()
            opik_id = str(getattr(trace, "id", "") or getattr(trace, "trace_id", "") or "")
            return {"trace_id": trace_id, "opik_trace_id": opik_id, "opik_status": "created" if opik_id else "created_no_id"}
        except Exception:
            return {"trace_id": trace_id, "opik_trace_id": "", "opik_status": "failed"}

    def flush(self) -> None:
        if self.client is None:
            return
        try:
            self.client.flush()
        except Exception:
            pass


def avg(values: list[float]) -> float:
    nums = [float(value) for value in values]
    return round(sum(nums) / len(nums), 4) if nums else 0.0


def summarize(rows: list[dict[str, Any]], group_fields: list[str]) -> list[dict[str, Any]]:
    groups: dict[tuple[Any, ...], list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        groups[tuple(row[field] for field in group_fields)].append(row)
    out = []
    for key, items in sorted(groups.items()):
        scored = [row for row in items if row["success"]]
        base = {field: value for field, value in zip(group_fields, key)}
        base.update(
            {
                "attempts": len(items),
                "provider_success": sum(1 for row in items if row["provider_success"]),
                "provider_errors": sum(1 for row in items if row["failure_reason"] == "provider_error"),
                "avg_required_fact_recall": avg([row["required_fact_recall"] for row in scored]),
                "avg_numeric_correctness": avg([row["numeric_correctness"] for row in scored]),
                "avg_answer_correctness": avg([row["answer_correctness"] for row in scored]),
                "avg_faithfulness": avg([row["faithfulness"] for row in scored]),
                "avg_calculation_completeness": avg([row["calculation_completeness"] for row in scored]),
                "avg_answer_format_compliance": avg([row["answer_format_compliance"] for row in scored]),
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
        "required_fact_recall",
        "numeric_correctness",
        "answer_correctness",
        "faithfulness",
        "calculation_completeness",
        "answer_format_compliance",
        "failure_reason",
        "error_type",
        "error_message",
    ]


def summary_json(run_dir: Path, rows: list[dict[str, Any]], track_a_cases: list[dict[str, Any]], track_b_cases: list[dict[str, Any]], model: str, opik_created: int, opik_status: str) -> dict[str, Any]:
    return {
        "run_dir": rel(run_dir),
        "provider": "openai",
        "model": model,
        "temperature": 0,
        "track_a_cases": len(track_a_cases),
        "track_b_cases": len(track_b_cases),
        "attempts": len(rows),
        "provider_failures": sum(1 for row in rows if row["failure_reason"] == "provider_error"),
        "successes": sum(1 for row in rows if row["success"]),
        "opik_traces_created": opik_created,
        "opik_status": opik_status,
        "model_api_called": True,
        "neo4j_write_performed": False,
        "kg_patch_applied": False,
        "full_eval_executed": False,
        "test_eval_executed": False,
        "prompt_package": rel(PROMPTS),
        "methods": METHODS,
    }


def write_report(run_dir: Path, rows: list[dict[str, Any]], by_track: list[dict[str, Any]]) -> None:
    track_counts = Counter(row["track"] for row in rows)
    failure_counts = Counter(row["failure_reason"] for row in rows)
    lines = [
        "# Round 3 Dev Dry-Run v3.1 Report",
        "",
        "## Scope",
        "",
        "- Track A live KG diagnostic dev/baseline only.",
        "- Track B shadow overlay dev/baseline only.",
        "- Test split: not executed.",
        "- Full eval: not executed.",
        "- Track averages are reported separately and must not be merged into one headline number.",
        "",
        "## Attempts",
        "",
    ]
    for track, count in track_counts.items():
        lines.append(f"- {track}: {count}")
    lines.extend(["", "## Failure Reasons", ""])
    for reason, count in failure_counts.items():
        lines.append(f"- {reason}: {count}")
    lines.extend(["", "## Method Summary By Track", ""])
    for row in by_track:
        lines.append(
            f"- {row['track']} / {row['method']}: attempts={row['attempts']}, avg_rfr={row['avg_required_fact_recall']}, "
            f"avg_numeric={row['avg_numeric_correctness']}, avg_answer={row['avg_answer_correctness']}"
        )
    (run_dir / "dev_dryrun_v3_1_report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def write_boundaries(run_dir: Path) -> None:
    (run_dir / "claim_boundary_after_dev_dryrun_v3_1.md").write_text(
        "# Claim Boundary After Dev Dry-Run v3.1\n\nAllowed: approved dev/baseline diagnostic dry-run results for Track A and Track B separately.\n\nForbidden: Track B as live Neo4j KG, test results, full eval completion, merged Track A/B headline averages, or general GraphRAG superiority.\n",
        encoding="utf-8",
    )
    (run_dir / "go_no_go_for_test_eval_v3_1.md").write_text(
        "# Go / No-Go For Test Eval v3.1\n\nDecision: `locked_requires_separate_approval`\n\nNo test eval was executed. Test/final eval requires separate explicit approval after reviewing dev dry-run outputs.\n",
        encoding="utf-8",
    )


def final_status(run_dir: Path, rows: list[dict[str, Any]], track_a_cases: list[dict[str, Any]], track_b_cases: list[dict[str, Any]], opik_created: int) -> dict[str, Any]:
    created = [rel(path) for path in sorted(run_dir.iterdir()) if path.is_file()]
    return {
        "Track A dev/baseline attempts": len(track_a_cases) * len(METHODS),
        "Track B dev/baseline attempts": len(track_b_cases) * len(METHODS),
        "provider failures": sum(1 for row in rows if row["failure_reason"] == "provider_error"),
        "test split rows": sum(1 for row in rows if row["split"] == "round3_test"),
        "Opik traces created": opik_created,
        "model/API called": "yes",
        "Neo4j write performed": "no",
        "KG patch applied": "no",
        "full eval executed": "no",
        "test eval executed": "no",
        "current gate": "dev_dryrun_v3_1_completed_review_required",
        "next recommended action": "review v3.1 dev dry-run outputs; keep test/full eval locked until separate approval",
        "created files": created,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-dir", default="")
    args = parser.parse_args()
    load_dotenv_safely()
    model = env_value("OPENAI_MODEL") or "gpt-4.1-mini"
    run_dir = Path(args.run_dir).resolve() if args.run_dir else OUT_ROOT / f"dev_dryrun_v3_1_{ts()}"
    run_dir.mkdir(parents=True, exist_ok=True)
    track_a_cases, track_b_cases, track_a_facts, track_b_facts = load_cases()
    work = []
    for track, cases, facts_by_case in [
        ("track_a_live_kg_diagnostic", track_a_cases, track_a_facts),
        ("track_b_shadow_overlay", track_b_cases, track_b_facts),
    ]:
        for case in cases:
            for method in METHODS:
                work.append((track, case, facts_by_case[case["case_id"]], method))

    opik = OpikLogger()
    result_rows: list[dict[str, Any]] = read_existing_csv(run_dir / "dev_dryrun_v3_1_results.csv")
    trace_rows: list[dict[str, Any]] = read_jsonl(run_dir / "dev_dryrun_v3_1_traces.jsonl")
    opik_rows: list[dict[str, Any]] = read_jsonl(run_dir / "opik_trace_ids.jsonl")
    failure_rows: list[dict[str, Any]] = read_jsonl(run_dir / "failure_analysis.jsonl")
    issue_lines = ["# Prompt/Formatter Issues v3.1", ""]
    completed = {(row["track"], row["case_id"], row["method"]) for row in result_rows}

    for idx, (track, case, facts, method) in enumerate(work, start=1):
        if (track, case["case_id"], method) in completed:
            continue
        trace_id = f"local_trace_v3_1_{idx:04d}_{case['case_id']}__{method}"
        prompt = build_prompt(track, method, case, facts)
        prompt_hash = sha(prompt["system"] + "\n" + prompt["user"])
        base = {
            "track": track,
            "split": case["split"],
            "case_id": case["case_id"],
            "method": method,
            "provider": "openai",
            "model": model,
            "trace_id": trace_id,
            "success": False,
            "provider_success": False,
            "error_type": "",
            "error_message": "",
        }
        method_result = None
        raw_method_result = None
        scores = None
        usage = {}
        try:
            method_result, usage, raw_method_result = call_openai(prompt, model)
            scores = score_result(case, facts, method, method_result)
            base.update(
                {
                    "success": True,
                    "provider_success": True,
                    "required_fact_recall": scores["required_fact_recall"]["required_fact_recall"],
                    "numeric_correctness": 1.0 if scores["numeric_correctness"]["numeric_correctness"] else 0.0,
                    "answer_correctness": 1.0 if scores["answer_correctness"]["answer_correctness"] else 0.0,
                    "faithfulness": 1.0 if scores["faithfulness"] else 0.0,
                    "calculation_completeness": 1.0 if scores["calculation_completeness"] else 0.0,
                    "answer_format_compliance": 1.0 if scores["answer_format_compliance"] else 0.0,
                }
            )
        except ProviderError as exc:
            base.update({"error_type": exc.error_type, "error_message": str(exc)})
        except Exception as exc:  # noqa: BLE001
            base.update({"error_type": "scoring_uncertain", "error_message": str(exc)[:300]})
        base.setdefault("required_fact_recall", 0.0)
        base.setdefault("numeric_correctness", 0.0)
        base.setdefault("answer_correctness", 0.0)
        base.setdefault("faithfulness", 0.0)
        base.setdefault("calculation_completeness", 0.0)
        base.setdefault("answer_format_compliance", 0.0)
        base["failure_reason"] = failure_reason(base, scores)
        result_rows.append(base)
        if base["failure_reason"] != "none":
            failure_rows.append({**base, "scores": scores or {}})
        opik_row = opik.log(trace_id=trace_id, prompt_hash=prompt_hash, row=base, scores=scores)
        opik_row.update({"track": track, "case_id": case["case_id"], "method": method})
        opik_rows.append(opik_row)
        trace_rows.append(
            {
                **base,
                "prompt_sha256": prompt_hash,
                "system_prompt": prompt["system"],
                "user_prompt": prompt["user"],
                "method_result": asdict(method_result) if method_result else None,
                "raw_method_result_v3_1": raw_method_result,
                "scores": scores,
                "usage": usage,
                "opik_trace_id": opik_row.get("opik_trace_id", ""),
                "opik_status": opik_row.get("opik_status", ""),
                "model_api_called": True,
                "neo4j_write_performed": False,
                "kg_patch_applied": False,
                "full_eval_executed": False,
                "test_eval_executed": False,
            }
        )
        if method_result and not scores:
            issue_lines.append(f"- {case['case_id']} / {method}: provider output existed but scoring failed.")
        write_jsonl(run_dir / "dev_dryrun_v3_1_traces.jsonl", trace_rows)
        write_jsonl(run_dir / "opik_trace_ids.jsonl", opik_rows)
        write_csv(run_dir / "dev_dryrun_v3_1_results.csv", result_rows, result_fields())
        write_jsonl(run_dir / "failure_analysis.jsonl", failure_rows)
        time.sleep(0.25)

    opik.flush()
    opik_created = sum(1 for row in opik_rows if row.get("opik_trace_id"))
    case_scores = summarize(result_rows, ["track", "split", "case_id"])
    by_track = summarize(result_rows, ["track", "method"])
    by_split = summarize(result_rows, ["track", "split", "method"])
    write_csv(run_dir / "method_summary_by_track.csv", by_track, list(by_track[0].keys()) if by_track else [])
    write_csv(run_dir / "method_summary_by_split.csv", by_split, list(by_split[0].keys()) if by_split else [])
    write_csv(run_dir / "case_level_scores.csv", case_scores, list(case_scores[0].keys()) if case_scores else [])
    write_json(run_dir / "dev_dryrun_v3_1_summary.json", summary_json(run_dir, result_rows, track_a_cases, track_b_cases, model, opik_created, opik.status))
    (run_dir / "prompt_formatter_issues_v3_1.md").write_text("\n".join(issue_lines) + "\n", encoding="utf-8")
    write_report(run_dir, result_rows, by_track)
    write_boundaries(run_dir)
    print(json.dumps(final_status(run_dir, result_rows, track_a_cases, track_b_cases, opik_created), ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
