"""Run approved Round 3 dual-track dev/baseline dry-run v3.

Approved scope only:
- Track B shadow overlay dev + baseline
- Track A live KG diagnostic dev + baseline
- Methods: vector_only_v3, graph_facts_only_v3, hybrid_vector_graph_v3, gold_context_v3

Forbidden by construction:
- test split
- full eval
- Neo4j write / KG patch
- model/API calls outside approved dev/baseline scope
"""

from __future__ import annotations

import csv
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


METHODS = ["vector_only_v3", "graph_facts_only_v3", "hybrid_vector_graph_v3", "gold_context_v3"]
TRACK_A = ROOT / "outputs" / "round3_dual_track_eval_prep" / "track_a_live_kg"
TRACK_B = ROOT / "outputs" / "round3_dual_track_eval_prep" / "track_b_shadow_overlay"
PROMPTS = ROOT / "outputs" / "round3_dual_track_eval_prep" / "prompt_formatter_v3"
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


def sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def load_cases() -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, list[dict[str, Any]]], dict[str, list[dict[str, Any]]]]:
    track_a_cases = read_json(TRACK_A / "live_kg_dev_cases.json") + read_json(TRACK_A / "live_kg_baseline_cases.json")
    track_b_cases = read_json(TRACK_B / "shadow_overlay_dev_cases.json") + read_json(TRACK_B / "shadow_overlay_baseline_cases.json")
    track_a_facts = group_by_case(read_jsonl(TRACK_A / "live_kg_required_facts.jsonl"))
    track_b_facts = group_by_case(read_jsonl(TRACK_B / "shadow_overlay_required_facts.jsonl"))
    for case in track_a_cases + track_b_cases:
        if case.get("split") == "round3_test":
            raise RuntimeError(f"test split is forbidden in dev dry-run: {case.get('case_id')}")
    return track_a_cases, track_b_cases, track_a_facts, track_b_facts


def group_by_case(rows: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[row["case_id"]].append(row)
    return grouped


def fact_table(facts: list[dict[str, Any]]) -> str:
    header = "| fact_id | company | ticker | metric | year/period | value | unit | role | evidence_quote |\n| --- | --- | --- | --- | --- | ---: | --- | --- | --- |"
    lines = [header]
    for fact in facts:
        year = fact.get("year") or fact.get("period_label") or ""
        quote = str(fact.get("evidence_quote_exact", "")).replace("\n", " ")[:220]
        lines.append(
            f"| {fact.get('fact_id','')} | {fact.get('company','')} | {fact.get('ticker','')} | "
            f"{fact.get('metric_canonical','')} | {year} | {fact.get('value','')} | {fact.get('unit','')} | "
            f"{fact.get('role','')} | {quote} |"
        )
    return "\n".join(lines)


def build_prompt(track: str, method: str, case: dict[str, Any], facts: list[dict[str, Any]]) -> dict[str, str]:
    system = (PROMPTS / "prompt_v3_system.md").read_text(encoding="utf-8")
    context_label = ""
    context = ""
    table = fact_table(facts)
    evidence = str(case.get("evidence_text", ""))
    if method == "vector_only_v3":
        context_label, context = "VECTOR_CONTEXT", evidence
    elif method == "graph_facts_only_v3":
        context_label, context = "GRAPH_FACTS_TABLE", table
    elif method == "hybrid_vector_graph_v3":
        context_label, context = "VECTOR_CONTEXT\n" + evidence + "\n\nGRAPH_FACTS_TABLE", table
    elif method == "gold_context_v3":
        context_label, context = "GOLD_CONTEXT", evidence
    else:
        raise RuntimeError(f"unknown method: {method}")
    user = f"""track: {track}
case_id: {case['case_id']}
split: {case['split']}
method: {method}
question: {case['question']}

{context_label}:
{context}

Answer format:
Return JSON only with keys:
{{
  "final_answer": "...",
  "calculation": "...",
  "source_fact_ids_used": ["..."],
  "citations": ["..."],
  "missing_information": []
}}

Use the same rounding and scoring rules across methods. Use temperature=0 behavior. Do not use outside knowledge. Do not mention hidden expected answers.
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
    text = str(exc).replace("\n", " ")
    return text[:300]


def extract_json_object(text: str) -> dict[str, Any]:
    stripped = text.strip()
    start = stripped.find("{")
    end = stripped.rfind("}")
    if start >= 0 and end > start:
        stripped = stripped[start : end + 1]
    return json.loads(stripped)


def call_openai(prompt: dict[str, str], model: str) -> tuple[MethodResult, dict[str, Any]]:
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
    return MethodResult.from_mapping(parsed), data.get("usage", {})


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
    calc_complete = bool(result.calculation and any(token in result.calculation for token in ["/", "%", "=", "ratio", "rate", "margin", "growth", "change"]))
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


def summarize(rows: list[dict[str, Any]], group_fields: list[str]) -> list[dict[str, Any]]:
    groups: dict[tuple[Any, ...], list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        groups[tuple(row[field] for field in group_fields)].append(row)
    out = []
    for key, items in sorted(groups.items()):
        scored = [r for r in items if r["success"]]
        base = {field: value for field, value in zip(group_fields, key)}
        base.update(
            {
                "attempts": len(items),
                "provider_success": sum(1 for r in items if r["provider_success"]),
                "provider_errors": sum(1 for r in items if r["failure_reason"] == "provider_error"),
                "avg_required_fact_recall": avg([r["required_fact_recall"] for r in scored]),
                "avg_numeric_correctness": avg([r["numeric_correctness"] for r in scored]),
                "avg_answer_correctness": avg([r["answer_correctness"] for r in scored]),
                "avg_faithfulness": avg([r["faithfulness"] for r in scored]),
                "avg_calculation_completeness": avg([r["calculation_completeness"] for r in scored]),
                "avg_answer_format_compliance": avg([r["answer_format_compliance"] for r in scored]),
            }
        )
        out.append(base)
    return out


def avg(values: list[float]) -> float:
    return round(sum(values) / len(values), 4) if values else 0.0


def main() -> None:
    model = env_value("OPENAI_MODEL") or "gpt-4.1-mini"
    run_dir = OUT_ROOT / f"dev_dryrun_v3_{ts()}"
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

    result_rows: list[dict[str, Any]] = []
    trace_rows: list[dict[str, Any]] = []
    opik_rows: list[dict[str, Any]] = []
    failure_rows: list[dict[str, Any]] = []
    issue_lines = ["# Prompt/Formatter Issues", ""]
    for idx, (track, case, facts, method) in enumerate(work, start=1):
        trace_id = f"local_trace_{idx:04d}_{case['case_id']}__{method}"
        prompt = build_prompt(track, method, case, facts)
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
        scores = None
        usage = {}
        try:
            method_result, usage = call_openai(prompt, model)
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
        trace_rows.append(
            {
                **base,
                "prompt_sha256": sha(prompt["system"] + "\n" + prompt["user"]),
                "system_prompt": prompt["system"],
                "user_prompt": prompt["user"],
                "method_result": asdict(method_result) if method_result else None,
                "scores": scores,
                "usage": usage,
                "model_api_called": True,
                "neo4j_write_performed": False,
                "kg_patch_applied": False,
                "full_eval_executed": False,
                "test_eval_executed": False,
            }
        )
        opik_rows.append(
            {
                "trace_id": trace_id,
                "opik_trace_id": "",
                "opik_status": "not_configured",
                "track": track,
                "case_id": case["case_id"],
                "method": method,
            }
        )
        if method_result and not scores:
            issue_lines.append(f"- {case['case_id']} / {method}: provider output existed but scoring failed.")
        time.sleep(0.25)

        write_jsonl(run_dir / "dev_dryrun_traces.jsonl", trace_rows)
        write_jsonl(run_dir / "opik_trace_ids.jsonl", opik_rows)
        write_csv(run_dir / "dev_dryrun_results.csv", result_rows, result_fields())
        write_jsonl(run_dir / "failure_analysis.jsonl", failure_rows)

    case_scores = summarize(result_rows, ["track", "split", "case_id"])
    by_track = summarize(result_rows, ["track", "method"])
    by_split = summarize(result_rows, ["track", "split", "method"])
    write_csv(run_dir / "method_summary_by_track.csv", by_track, list(by_track[0].keys()) if by_track else [])
    write_csv(run_dir / "method_summary_by_split.csv", by_split, list(by_split[0].keys()) if by_split else [])
    write_csv(run_dir / "case_level_scores.csv", case_scores, list(case_scores[0].keys()) if case_scores else [])
    write_json(run_dir / "dev_dryrun_summary.json", summary_json(run_dir, result_rows, track_a_cases, track_b_cases, model))
    (run_dir / "prompt_formatter_issues.md").write_text("\n".join(issue_lines) + "\n", encoding="utf-8")
    write_report(run_dir, result_rows, by_track, by_split)
    write_boundaries(run_dir)
    print(json.dumps(final_status(run_dir, result_rows, track_a_cases, track_b_cases), ensure_ascii=False, indent=2, sort_keys=True))


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


def summary_json(run_dir: Path, rows: list[dict[str, Any]], track_a_cases: list[dict[str, Any]], track_b_cases: list[dict[str, Any]], model: str) -> dict[str, Any]:
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
        "opik_traces_created": 0,
        "model_api_called": True,
        "neo4j_write_performed": False,
        "kg_patch_applied": False,
        "full_eval_executed": False,
        "test_eval_executed": False,
    }


def write_report(run_dir: Path, rows: list[dict[str, Any]], by_track: list[dict[str, Any]], by_split: list[dict[str, Any]]) -> None:
    track_counts = Counter(row["track"] for row in rows)
    failure_counts = Counter(row["failure_reason"] for row in rows)
    lines = [
        "# Round 3 Dev Dry-Run v3 Report",
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
    (run_dir / "dev_dryrun_report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def write_boundaries(run_dir: Path) -> None:
    (run_dir / "claim_boundary_after_dev_dryrun.md").write_text(
        "# Claim Boundary After Dev Dry-Run\n\nAllowed: approved dev/baseline diagnostic dry-run results for Track A and Track B separately.\n\nForbidden: Track B as live Neo4j KG, test results, full eval completion, merged Track A/B headline averages, or general GraphRAG superiority.\n",
        encoding="utf-8",
    )
    (run_dir / "go_no_go_for_test_eval.md").write_text(
        "# Go / No-Go For Test Eval\n\nDecision: `locked_requires_separate_approval`\n\nNo test eval was executed. Test/final eval requires separate explicit approval after reviewing dev dry-run outputs.\n",
        encoding="utf-8",
    )


def final_status(run_dir: Path, rows: list[dict[str, Any]], track_a_cases: list[dict[str, Any]], track_b_cases: list[dict[str, Any]]) -> dict[str, Any]:
    created = [rel(path) for path in sorted(run_dir.iterdir()) if path.is_file()]
    return {
        "Track A dev/baseline attempts": len(track_a_cases) * len(METHODS),
        "Track B dev/baseline attempts": len(track_b_cases) * len(METHODS),
        "Opik traces created": 0,
        "model/API called": "yes",
        "Neo4j write performed": "no",
        "KG patch applied": "no",
        "full eval executed": "no",
        "test eval executed": "no",
        "current gate": "dev_dryrun_completed_review_required",
        "next recommended action": "review dev dry-run outputs; keep test/full eval locked until separate approval",
        "created files": created,
    }


if __name__ == "__main__":
    main()
