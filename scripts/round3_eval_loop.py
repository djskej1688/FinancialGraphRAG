"""Round 3 ready-subset partial evaluation loop.

This script evaluates only the ready partial subset declared by the
orchestration manifest. Full evaluation, Neo4j writes, KG patches, and backlog
case evaluation are blocked by construction.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import socket
import sys
import time
import urllib.error
import urllib.request
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from seocho.eval.round3 import (
    MethodResult,
    Round3InputIsolationError,
    Round3Method,
    Round3MethodInput,
    build_round3_prompt,
    score_answer_correctness,
    score_numeric_correctness,
    score_required_fact_recall,
)
from seocho.eval.round3.scoring import extract_numeric_values, numeric_values_close


DEFAULT_RUN_DIR = REPO_ROOT / "outputs" / "round3_orchestration" / "20260525_132801"
DEFAULT_METHODS = ("vector_only", "graph_facts_only", "hybrid_vector_graph", "gold_context")
REQUIRED_FACTS_PATH = REPO_ROOT / "outputs" / "round3_case_factory_repaired" / "eval_ready_required_facts.jsonl"
ENV_FILES = (REPO_ROOT / ".env", REPO_ROOT.parent / ".env")
PROVIDER_ERROR_TYPES = {
    "provider_rate_limit",
    "provider_unavailable",
    "provider_timeout",
    "provider_auth",
    "provider_bad_response",
    "provider_unknown",
}
RETRYABLE_PROVIDER_ERROR_TYPES = {"provider_rate_limit", "provider_unavailable", "provider_timeout"}
RETIRED_PROVIDERS = {"gemini"}


class SafetyViolation(RuntimeError):
    """Raised when approval/configuration would exceed the partial-eval scope."""


class ProviderError(RuntimeError):
    """Sanitized provider failure with a stable error type."""

    def __init__(self, error_type: str, message: str) -> None:
        super().__init__(message)
        self.error_type = error_type


def now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def timestamp() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S")


def rel(path: Path) -> str:
    try:
        return path.resolve().relative_to(REPO_ROOT.resolve()).as_posix()
    except ValueError:
        return str(path)


def resolve_path(value: str | Path) -> Path:
    path = Path(value)
    return path if path.is_absolute() else REPO_ROOT / path


def write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text.rstrip() + "\n", encoding="utf-8", newline="\n")


def write_json(path: Path, data: Any) -> None:
    write_text(path, json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True))


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")


def parse_key_value_file(path: Path) -> dict[str, str]:
    data: dict[str, str] = {}
    if not path.exists():
        return data
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        data[key.strip()] = value.strip()
    return data


def env_value(name: str) -> str:
    if os.environ.get(name):
        return os.environ[name]
    for path in ENV_FILES:
        if not path.exists():
            continue
        for raw_line in path.read_text(encoding="utf-8").splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            if key.strip() == name:
                return value.strip().strip("\"'")
    return ""


def parse_bool(value: str | None, *, default: bool = False) -> bool:
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "y", "on"}


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    if not path.exists():
        return rows
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        rows.append(json.loads(line))
    return rows


def load_required_facts() -> dict[str, list[dict[str, Any]]]:
    facts: dict[str, list[dict[str, Any]]] = {}
    for row in load_jsonl(REQUIRED_FACTS_PATH):
        facts.setdefault(str(row.get("case_id", "")), []).append(row)
    return facts


def sanitize_graph_fact(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "fact_id": row.get("fact_id", ""),
        "metric_canonical": row.get("metric_canonical") or row.get("metric_raw") or row.get("metric", ""),
        "metric_raw": row.get("metric_raw", ""),
        "value": row.get("value"),
        "year": row.get("year"),
        "unit": row.get("unit", ""),
        "company": row.get("company") or row.get("entity") or row.get("ticker") or "",
        "ticker": row.get("ticker", ""),
        "source_fact": bool(row.get("source_fact", True)),
        "derived_answer_value": bool(row.get("derived_answer_value", False)),
    }


def build_method_input(case: dict[str, Any], method: str, required_facts: list[dict[str, Any]]) -> Round3MethodInput:
    base = {
        "case_id": str(case.get("case_id", "")),
        "split": str(case.get("split", "")),
        "question": str(case.get("question", "")),
        "metadata": {
            "source_dataset": case.get("source_dataset", ""),
            "ticker": case.get("ticker", ""),
            "category": case.get("category", ""),
        },
    }
    graph_facts = [sanitize_graph_fact(row) for row in required_facts]
    evidence_text = str(case.get("evidence_text", ""))
    if method == "vector_only":
        return Round3MethodInput(**base, vector_context=evidence_text)
    if method == "graph_facts_only":
        return Round3MethodInput(**base, graph_facts=graph_facts)
    if method == "hybrid_vector_graph":
        return Round3MethodInput(**base, vector_context=evidence_text, graph_facts=graph_facts)
    if method == "gold_context":
        return Round3MethodInput(**base, gold_context=evidence_text)
    raise SafetyViolation(f"unknown method: {method}")


def method_input_to_safe_dict(method_input: Round3MethodInput) -> dict[str, Any]:
    return {
        "case_id": method_input.case_id,
        "split": method_input.split,
        "question": method_input.question,
        "vector_context": method_input.vector_context,
        "graph_facts": [fact.to_prompt_dict() for fact in method_input.normalized_graph_facts()] if method_input.graph_facts else None,
        "gold_context": method_input.gold_context,
        "metadata": dict(method_input.metadata),
    }


def mock_provider_answer(case: dict[str, Any], required_facts: list[dict[str, Any]], method: str) -> MethodResult:
    fact_ids = [str(row.get("fact_id", "")) for row in required_facts if row.get("fact_id")]
    return MethodResult(
        final_answer=str(case.get("expected_answer", "")),
        calculation=f"mock provider pipeline validation for {method}; no model API called",
        source_fact_ids_used=fact_ids,
        citations=[str(case.get("case_id", ""))],
        missing_information=[],
    )


def extract_json_object(text: str) -> dict[str, Any]:
    stripped = text.strip()
    if stripped.startswith("```"):
        stripped = stripped.strip("`")
        if stripped.lower().startswith("json"):
            stripped = stripped[4:].strip()
    start = stripped.find("{")
    end = stripped.rfind("}")
    if start >= 0 and end > start:
        stripped = stripped[start : end + 1]
    return json.loads(stripped)


def classify_http_status(status_code: int) -> str:
    if status_code == 429:
        return "provider_rate_limit"
    if status_code == 503:
        return "provider_unavailable"
    if status_code in {401, 403}:
        return "provider_auth"
    if 500 <= status_code < 600:
        return "provider_unavailable"
    if 400 <= status_code < 500:
        return "provider_bad_response"
    return "provider_unknown"


def classify_exception(exc: BaseException) -> str:
    if isinstance(exc, ProviderError):
        return exc.error_type
    if isinstance(exc, urllib.error.HTTPError):
        return classify_http_status(exc.code)
    if isinstance(exc, TimeoutError) or isinstance(exc, socket.timeout):
        return "provider_timeout"
    if isinstance(exc, urllib.error.URLError):
        reason = str(getattr(exc, "reason", "")).lower()
        if "timed out" in reason or "timeout" in reason:
            return "provider_timeout"
        return "provider_unknown"
    if isinstance(exc, json.JSONDecodeError):
        return "model_output_parse_error"
    return "provider_unknown"


def sanitize_error_message(exc: BaseException) -> str:
    if isinstance(exc, urllib.error.HTTPError):
        return f"HTTP Error {exc.code}: {exc.reason}"
    text = str(exc)
    if "key=" in text:
        text = text.split("key=", 1)[0] + "key=<redacted>"
    return f"{type(exc).__name__}: {text}".strip()


def gemini_provider_answer(prompt: Any, model: str) -> MethodResult:
    raise SafetyViolation("provider gemini is retired for Round 3 and must not be called")


def openai_provider_answer(prompt: Any, model: str) -> MethodResult:
    key = env_value("OPENAI_API_KEY")
    if not key:
        raise SafetyViolation("provider openai requires OPENAI_API_KEY; value was not printed")
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": prompt.system},
            {"role": "user", "content": prompt.user + "\n\nReturn only the JSON object requested by the system prompt."},
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
        with urllib.request.urlopen(req, timeout=120) as response:
            data = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        raise ProviderError(classify_http_status(exc.code), sanitize_error_message(exc)) from exc
    except (TimeoutError, socket.timeout) as exc:
        raise ProviderError("provider_timeout", sanitize_error_message(exc)) from exc
    except urllib.error.URLError as exc:
        raise ProviderError(classify_exception(exc), sanitize_error_message(exc)) from exc
    except json.JSONDecodeError as exc:
        raise ProviderError("provider_bad_response", "provider returned invalid JSON envelope") from exc
    try:
        text = data["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as exc:
        raise ProviderError("provider_bad_response", "provider response did not contain chat completion content") from exc
    if not str(text).strip():
        raise ProviderError("provider_bad_response", "provider returned an empty response")
    try:
        parsed = extract_json_object(str(text))
    except (json.JSONDecodeError, ValueError) as exc:
        raise ProviderError("model_output_parse_error", "provider returned non-JSON model output") from exc
    return MethodResult.from_mapping(parsed)


def provider_answer(
    provider: str,
    case: dict[str, Any],
    required_facts: list[dict[str, Any]],
    method: str,
    prompt: Any,
    model: str,
) -> MethodResult:
    if provider == "mock":
        return mock_provider_answer(case, required_facts, method)
    if provider == "gemini":
        raise SafetyViolation("provider gemini is retired for Round 3 and must not be called")
    if provider == "openai":
        return openai_provider_answer(prompt, model)
    raise SafetyViolation(f"provider {provider!r} is not enabled in this local partial eval loop")


def provider_answer_with_retries(
    *,
    provider: str,
    case: dict[str, Any],
    required_facts: list[dict[str, Any]],
    method: str,
    prompt: Any,
    model: str,
    max_retries: int,
    retry_backoff_seconds: float,
    request_sleep_seconds: float,
) -> tuple[MethodResult, int]:
    attempts = 0
    while True:
        attempts += 1
        try:
            result = provider_answer(provider, case, required_facts, method, prompt, model)
            if provider != "mock" and request_sleep_seconds > 0:
                time.sleep(request_sleep_seconds)
            return result, attempts
        except ProviderError as exc:
            if provider != "mock" and request_sleep_seconds > 0:
                time.sleep(request_sleep_seconds)
            if exc.error_type not in RETRYABLE_PROVIDER_ERROR_TYPES or attempts > max_retries:
                raise
            time.sleep(retry_backoff_seconds * (2 ** (attempts - 1)))


def approval_methods(approval: dict[str, str], cli_methods: list[str]) -> list[str]:
    raw = approval.get("methods")
    methods = [item.strip() for item in raw.split(",") if item.strip()] if raw else cli_methods
    allowed = {method.value for method in Round3Method}
    unknown = sorted(set(methods) - allowed)
    if unknown:
        raise SafetyViolation(f"approval contains unknown methods: {unknown}")
    cli_set = set(cli_methods)
    if not set(methods).issubset(cli_set):
        raise SafetyViolation("approval methods must be a subset of CLI methods")
    return methods


def validate_safety(
    *,
    run_dir: Path,
    manifest_path: Path,
    cases_path: Path,
    provider: str,
    methods: list[str],
    approval: dict[str, str],
    manifest: dict[str, Any],
    cases: list[dict[str, Any]],
    max_concurrency: int,
) -> None:
    if not approval:
        raise SafetyViolation("missing approvals/allow_partial_eval.txt")
    if not parse_bool(approval.get("approved_by_user")):
        raise SafetyViolation("approval file must contain approved_by_user=true")
    allowed_scopes = {"ready_subset_partial_eval_only", "ready_subset_real_provider_partial_eval_only"}
    if approval.get("scope") not in allowed_scopes:
        raise SafetyViolation("approval scope must be ready subset partial eval only")
    for key in ("neo4j_write_allowed", "kg_patch_allowed", "full_eval_allowed", "round3_test_tuning_allowed"):
        if parse_bool(approval.get(key), default=False):
            raise SafetyViolation(f"safety violation: {key}=true")
    if max_concurrency != 1:
        raise SafetyViolation("max_concurrency must remain 1 for this local partial eval loop")
    allowed_manifest = resolve_path(approval.get("allowed_manifest", rel(manifest_path)))
    allowed_cases = resolve_path(approval.get("allowed_cases", rel(cases_path)))
    if allowed_manifest.resolve() != manifest_path.resolve():
        raise SafetyViolation("manifest path is not approved")
    if allowed_cases.resolve() != cases_path.resolve():
        raise SafetyViolation("cases path is not approved")
    if provider in RETIRED_PROVIDERS:
        raise SafetyViolation(f"provider {provider} is retired for Round 3 and must not be called")
    if approval.get("provider", provider) != provider:
        raise SafetyViolation("provider differs from approval file")
    if provider == "openai":
        if not parse_bool(approval.get("model_api_allowed"), default=False):
            raise SafetyViolation("provider openai requires model_api_allowed=true")
        if not env_value("OPENAI_API_KEY"):
            raise SafetyViolation("provider openai requires OPENAI_API_KEY; value was not printed")
    elif provider != "mock":
        raise SafetyViolation(f"provider {provider!r} is not enabled")
    ready_ids = {str(item) for item in manifest.get("ready_case_ids", [])}
    case_ids = {str(case.get("case_id", "")) for case in cases}
    if not case_ids:
        raise SafetyViolation("ready cases file is empty")
    if not case_ids.issubset(ready_ids):
        raise SafetyViolation("ready cases file contains case IDs outside manifest ready_case_ids")
    backlog_ids = load_backlog_case_ids(run_dir)
    overlap = sorted(case_ids & backlog_ids)
    if overlap:
        raise SafetyViolation(f"backlog cases are present in ready_cases: {overlap}")
    max_cases_raw = approval.get("max_cases")
    if max_cases_raw:
        try:
            max_cases = int(max_cases_raw)
        except ValueError as exc:
            raise SafetyViolation("max_cases must be an integer") from exc
        if len(cases) > max_cases:
            raise SafetyViolation(f"ready case count {len(cases)} exceeds approved max_cases {max_cases}")
    if set(methods) - {method.value for method in Round3Method}:
        raise SafetyViolation("methods contain values outside Round3Method")


def load_backlog_case_ids(run_dir: Path) -> set[str]:
    path = run_dir / "automation" / "coverage_refinement_backlog.jsonl"
    if not path.exists():
        return set()
    ids = set()
    for row in load_jsonl(path):
        case_id = str(row.get("case_id", ""))
        if case_id:
            ids.add(case_id)
    return ids


def score_attempt(
    *,
    case: dict[str, Any],
    method: str,
    method_input: Round3MethodInput,
    method_result: MethodResult,
    required_facts: list[dict[str, Any]],
) -> dict[str, Any]:
    recall = score_required_fact_recall(
        case_id=str(case.get("case_id", "")),
        required_facts=required_facts,
        method=method,
        method_input=method_input,
        method_result=method_result,
        threshold=0.95,
    )
    numeric = score_numeric_correctness(str(case.get("expected_answer", "")), method_result)
    answer = score_answer_correctness(
        expected_answer=str(case.get("expected_answer", "")),
        required_facts=required_facts,
        method=method,
        method_input=method_input,
        method_result=method_result,
        required_fact_threshold=0.95,
    )
    return {
        "required_fact_recall": recall.to_dict(),
        "numeric_correctness": numeric.to_dict(),
        "answer_correctness": answer.to_dict(),
    }


def numeric_diagnostics(expected_answer: str, method_result: MethodResult | None) -> dict[str, list[str]]:
    expected_values = extract_numeric_values(expected_answer)
    actual_values = extract_numeric_values(method_result.searchable_text() if method_result else "")
    matched = [
        expected.display
        for expected in expected_values
        if any(numeric_values_close(expected, actual) for actual in actual_values)
    ]
    missing = [
        expected.display
        for expected in expected_values
        if not any(numeric_values_close(expected, actual) for actual in actual_values)
    ]
    return {
        "expected_numeric_values": [value.display for value in expected_values],
        "actual_numeric_values": [value.display for value in actual_values],
        "matched_numeric_values": matched,
        "missing_numeric_values": missing,
    }


def build_scorer_diagnostic(
    *,
    case: dict[str, Any],
    method: str,
    method_result: MethodResult | None,
    scores: dict[str, Any] | None,
    notes: str,
) -> dict[str, Any]:
    numeric = numeric_diagnostics(str(case.get("expected_answer", "")), method_result)
    recall = scores.get("required_fact_recall", {}) if scores else {}
    numeric_score = scores.get("numeric_correctness", {}) if scores else {}
    return {
        "case_id": str(case.get("case_id", "")),
        "method": method,
        **numeric,
        "required_facts_total": int(recall.get("total_required_facts", 0) or 0),
        "required_facts_matched": int(recall.get("matched_required_facts", 0) or 0),
        "source_fact_recall": float(recall.get("required_fact_recall", 0.0) or 0.0),
        "answer_sufficient_fact_recall": float(recall.get("required_fact_recall", 0.0) or 0.0),
        "final_answer_numeric_correctness": bool(numeric_score.get("numeric_correctness", False)),
        "notes": notes,
    }


def row_from_success_payload(
    *,
    case: dict[str, Any],
    method: str,
    provider: str,
    eval_run_dir: Path,
    payload: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    scores = payload.get("scores", {})
    method_result = MethodResult.from_mapping(payload.get("method_result", {}))
    row = base_result_row(case=case, method=method, provider=provider, eval_run_dir=eval_run_dir)
    row.update(
        {
            "success": True,
            "provider_success": True,
            "required_fact_recall": scores.get("required_fact_recall", {}).get("required_fact_recall", 0.0),
            "numeric_correctness": 1.0 if scores.get("numeric_correctness", {}).get("numeric_correctness") else 0.0,
            "answer_correctness": 1.0 if scores.get("answer_correctness", {}).get("answer_correctness") else 0.0,
            "error_type": "none",
        }
    )
    error = {"case_id": row["case_id"], "method": method, "error_type": "none", "error_message": "", "is_blocking": False}
    diagnostic = build_scorer_diagnostic(case=case, method=method, method_result=method_result, scores=scores, notes="reused successful raw output")
    return row, error, diagnostic


def row_from_failure_payload(
    *,
    case: dict[str, Any],
    method: str,
    provider: str,
    eval_run_dir: Path,
    payload: dict[str, Any] | None,
    notes: str,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    error_type = str((payload or {}).get("error_type", "provider_bad_response"))
    error_message = str((payload or {}).get("error_message", "existing output is missing a successful method result"))
    row = base_result_row(case=case, method=method, provider=provider, eval_run_dir=eval_run_dir)
    row.update({"error_type": error_type, "error_message": error_message})
    error = {
        "case_id": row["case_id"],
        "method": method,
        "error_type": error_type,
        "error_message": error_message,
        "is_blocking": True,
    }
    diagnostic = build_scorer_diagnostic(
        case=case,
        method=method,
        method_result=None,
        scores=None,
        notes=notes or error_message,
    )
    return row, error, diagnostic


def base_result_row(*, case: dict[str, Any], method: str, provider: str, eval_run_dir: Path) -> dict[str, Any]:
    return {
        "case_id": str(case.get("case_id", "")),
        "split": case.get("split", ""),
        "method": method,
        "provider": provider,
        "success": False,
        "provider_success": False,
        "required_fact_recall": 0.0,
        "numeric_correctness": 0.0,
        "answer_correctness": 0.0,
        "error_type": "none",
        "error_message": "",
        "eval_run_dir": rel(eval_run_dir),
    }


def existing_output_state(path: Path) -> tuple[str, dict[str, Any] | None]:
    if not path.exists():
        return "missing", None
    try:
        payload = load_json(path)
    except (json.JSONDecodeError, OSError):
        return "provider_error", None
    if payload.get("success") is False or payload.get("provider_error"):
        error_type = str(payload.get("error_type", "provider_unknown"))
        if error_type in PROVIDER_ERROR_TYPES:
            return "provider_error", payload
        return "method_failure", payload
    if payload.get("method_result") and payload.get("scores"):
        return "success", payload
    return "missing", payload


def find_latest_eval_run(output_dir: Path, prefix: str) -> Path | None:
    if not output_dir.exists():
        return None
    candidates = sorted(
        [path for path in output_dir.iterdir() if path.is_dir() and path.name.startswith(prefix + "_")],
        key=lambda path: path.name,
        reverse=True,
    )
    return candidates[0] if candidates else None


def write_csv(path: Path, fieldnames: list[str], rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def summarize_methods(methods: list[str], result_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    summary: list[dict[str, Any]] = []
    for method in methods:
        rows = [row for row in result_rows if row["method"] == method]
        attempts = len(rows)
        provider_errors = [row for row in rows if row["error_type"] in PROVIDER_ERROR_TYPES]
        provider_successes = [row for row in rows if row.get("provider_success")]
        scored = [row for row in rows if row["success"]]
        method_failures = [row for row in rows if not row["success"] and row["error_type"] not in PROVIDER_ERROR_TYPES]
        summary.append(
            {
                "method": method,
                "case_count": len({row["case_id"] for row in rows}),
                "attempt_count": attempts,
                "provider_success_count": len(provider_successes),
                "provider_error_count": len(provider_errors),
                "provider_error_rate": round(len(provider_errors) / max(1, attempts), 4),
                "scored_count": len(scored),
                "method_failure_count": len(method_failures),
                "avg_required_fact_recall_scored_only": round(sum(float(row["required_fact_recall"]) for row in scored) / max(1, len(scored)), 4),
                "avg_required_fact_recall_all_attempts": round(sum(float(row["required_fact_recall"]) for row in rows) / max(1, attempts), 4),
                "avg_numeric_correctness_scored_only": round(sum(float(row["numeric_correctness"]) for row in scored) / max(1, len(scored)), 4),
                "avg_answer_correctness_scored_only": round(sum(float(row["answer_correctness"]) for row in scored) / max(1, len(scored)), 4),
                "input_contamination_count": sum(1 for row in rows if row.get("error_type") == "method_input_error"),
            }
        )
    return summary


def write_scorer_diagnostics(eval_run_dir: Path, rows: list[dict[str, Any]]) -> None:
    write_jsonl(eval_run_dir / "scorer_diagnostics.jsonl", rows)
    lines = [
        "# Scorer Diagnostics",
        "",
        f"Generated: {now()}",
        "",
        "| Case | Method | Required Facts | Fact Recall | Numeric OK | Notes |",
        "|---|---|---:|---:|---|---|",
    ]
    for row in rows:
        lines.append(
            f"| `{row['case_id']}` | `{row['method']}` | "
            f"{row['required_facts_matched']}/{row['required_facts_total']} | "
            f"{row['source_fact_recall']} | `{str(row['final_answer_numeric_correctness']).lower()}` | "
            f"{row['notes']} |"
        )
    write_text(eval_run_dir / "scorer_diagnostics.md", "\n".join(lines))


def update_orchestration_files(
    *,
    run_dir: Path,
    eval_run_dir: Path,
    provider: str,
    cases: list[dict[str, Any]],
    methods: list[str],
    result_rows: list[dict[str, Any]],
    summary_rows: list[dict[str, Any]],
    error_rows: list[dict[str, Any]],
    resume: bool,
    retry_failed_only: bool,
) -> None:
    attempts = len(result_rows)
    failures = sum(1 for row in result_rows if not row["success"])
    provider_failures = sum(1 for row in result_rows if row["error_type"] in PROVIDER_ERROR_TYPES)
    status = {
        "generated_at": now(),
        "partial_eval_executed": True,
        "provider": provider,
        "ready_cases_loaded": len(cases),
        "methods": methods,
        "attempt_count": attempts,
        "failure_count": failures,
        "provider_failure_count": provider_failures,
        "eval_run_dir": rel(eval_run_dir),
        "model_api_called": provider != "mock",
        "real_provider_partial_eval_executed": provider != "mock",
        "resume": resume,
        "retry_failed_only": retry_failed_only,
        "neo4j_write_performed": False,
        "kg_patch_applied": False,
        "full_eval_executed": False,
        "full_eval_lock": "locked",
    }
    write_json(run_dir / "automation" / "partial_eval_status.json", status)
    write_text(
        run_dir / "automation" / "partial_eval_status.md",
        f"""# Ready Partial Eval Status

Generated: {status['generated_at']}

- partial_eval_executed: true
- provider: `{provider}`
- ready_cases_loaded: {len(cases)}
- methods: `{','.join(methods)}`
- attempt_count: {attempts}
- failure_count: {failures}
- provider_failure_count: {provider_failures}
- eval_run_dir: `{rel(eval_run_dir)}`
- resume: {str(resume).lower()}
- retry_failed_only: {str(retry_failed_only).lower()}
- model_api_called: {str(provider != "mock").lower()}
- Neo4j write performed: false
- KG patch applied: false
- full_eval_executed: false
- full_eval_lock: locked
""",
    )
    if provider != "mock":
        real_status = {**status, "real_provider_partial_eval_executed": True, "model_api_called": True}
        write_json(run_dir / "automation" / "real_provider_partial_eval_status.json", real_status)
        write_text(
            run_dir / "automation" / "real_provider_partial_eval_status.md",
            f"""# Real-Provider Partial Eval Status

Generated: {status['generated_at']}

- real_provider_partial_eval_executed: true
- provider: `{provider}`
- ready_cases_loaded: {len(cases)}
- methods: `{','.join(methods)}`
- attempt_count: {attempts}
- failure_count: {failures}
- provider_failure_count: {provider_failures}
- eval_run_dir: `{rel(eval_run_dir)}`
- model_api_called: true
- Neo4j write performed: false
- KG patch applied: false
- full_eval_executed: false
- full_eval_lock: locked
""",
        )
    if provider_failures:
        next_action = (
            f"Review `{rel(eval_run_dir / 'report.md')}` and rerun with --resume --retry-failed-only after provider rate limits clear; "
            "keep full eval locked."
        )
    elif failures:
        next_action = f"Review scorer diagnostics in `{rel(eval_run_dir / 'scorer_diagnostics.md')}`; keep full eval locked."
    else:
        next_action = f"Review completed ready partial eval in `{rel(eval_run_dir / 'report.md')}`; keep full eval locked pending Antigravity approval."
    write_text(run_dir / "automation" / "next_action.md", next_action)
    blockers = ["# Blockers", ""]
    if provider_failures:
        blockers.append(f"- Provider failures remain for {provider_failures} attempts; inspect `{rel(eval_run_dir / 'error_analysis.csv')}`.")
    if failures - provider_failures:
        blockers.append(f"- Non-provider partial-eval failures remain for {failures - provider_failures} attempts.")
    backlog = run_dir / "automation" / "coverage_refinement_backlog.jsonl"
    if backlog.exists():
        backlog_count = sum(1 for line in backlog.read_text(encoding="utf-8").splitlines() if line.strip())
        if backlog_count:
            blockers.append(f"- Coverage backlog remains for {backlog_count} not-ready cases.")
    if len(blockers) == 2:
        blockers.append("- No ready partial eval blocker detected; full eval remains locked pending separate authority.")
    write_text(run_dir / "automation" / "blockers.md", "\n".join(blockers))
    gate_doc = load_json(run_dir / "merged" / "gate_status.json") if (run_dir / "merged" / "gate_status.json").exists() else {}
    gates = dict(gate_doc.get("gate_status", {}))
    gates["partial_eval"] = "warning" if failures else "pass"
    gates["dry_run"] = "warning" if failures else "pass"
    gates["full_eval_lock"] = "locked"
    gate_doc = {
        "generated_at": now(),
        "run_dir": rel(run_dir),
        "full_eval_approved": False,
        "gate_status": gates,
    }
    write_json(run_dir / "merged" / "gate_status.json", gate_doc)
    rows = ["# Gate Status", "", f"Generated: {gate_doc['generated_at']}", "", "| Gate | Status |", "|---|---|"]
    for gate, value in gates.items():
        rows.append(f"| `{gate}` | `{value}` |")
    rows.extend(["", "- Full evaluation approved: false", "- Full evaluation executed: false"])
    write_text(run_dir / "merged" / "gate_status.md", "\n".join(rows))
    write_text(
        run_dir / "automation" / "final_operator_report.md",
        f"""# Final Operator Report

Generated: {status['generated_at']}

## Terminal State

`ready_partial_eval_completed`

## Partial Eval

- eval_run_dir: `{rel(eval_run_dir)}`
- provider: `{provider}`
- ready cases: {len(cases)}
- methods: `{','.join(methods)}`
- failures: {failures}
- provider failures: {provider_failures}

## Safety

- Full eval executed: false
- Neo4j write performed: false
- KG patch applied: false
- model API called: {str(provider != "mock").lower()}
- full_eval_lock: locked

## Next Action

{next_action}
""",
    )


def run_eval(args: argparse.Namespace) -> dict[str, Any]:
    run_dir = resolve_path(args.run_dir)
    manifest_path = resolve_path(args.manifest)
    cases_path = resolve_path(args.cases)
    approval = parse_key_value_file(run_dir / "approvals" / "allow_partial_eval.txt")
    manifest = load_json(manifest_path)
    cases = load_jsonl(cases_path)
    methods = approval_methods(approval, args.methods)
    provider = approval.get("provider", args.provider)
    model = approval.get("model") or env_value("OPENAI_MODEL") or args.model
    validate_safety(
        run_dir=run_dir,
        manifest_path=manifest_path,
        cases_path=cases_path,
        provider=provider,
        methods=methods,
        approval=approval,
        manifest=manifest,
        cases=cases,
        max_concurrency=args.max_concurrency,
    )
    required_by_case = load_required_facts()
    prefix = "ready_partial_real" if provider != "mock" else "ready_partial"
    output_dir = resolve_path(args.output_dir)
    eval_run_dir = find_latest_eval_run(output_dir, prefix) if args.resume else None
    if eval_run_dir is None:
        eval_run_dir = output_dir / f"{prefix}_{timestamp()}"
    raw_inputs = eval_run_dir / "raw_inputs"
    raw_outputs = eval_run_dir / "raw_outputs"
    raw_inputs.mkdir(parents=True, exist_ok=True)
    raw_outputs.mkdir(parents=True, exist_ok=True)

    result_rows: list[dict[str, Any]] = []
    error_rows: list[dict[str, Any]] = []
    diagnostic_rows: list[dict[str, Any]] = []
    contamination_count = 0

    for case in cases:
        case_id = str(case.get("case_id", ""))
        facts = required_by_case.get(case_id, [])
        for method in methods:
            output_path = raw_outputs / f"{case_id}__{method}.json"
            state, payload = existing_output_state(output_path)
            if state == "success" and not args.force:
                method_result = MethodResult.from_mapping((payload or {}).get("method_result", {}))
                method_input = build_method_input(case, method, facts)
                scores = score_attempt(
                    case=case,
                    method=method,
                    method_input=method_input,
                    method_result=method_result,
                    required_facts=facts,
                )
                updated_payload = dict(payload or {})
                updated_payload["scores"] = scores
                write_json(output_path, updated_payload)
                row = base_result_row(case=case, method=method, provider=provider, eval_run_dir=eval_run_dir)
                row.update(
                    {
                        "success": True,
                        "provider_success": True,
                        "required_fact_recall": scores["required_fact_recall"]["required_fact_recall"],
                        "numeric_correctness": 1.0 if scores["numeric_correctness"]["numeric_correctness"] else 0.0,
                        "answer_correctness": 1.0 if scores["answer_correctness"]["answer_correctness"] else 0.0,
                        "error_type": "none",
                    }
                )
                error = {"case_id": row["case_id"], "method": method, "error_type": "none", "error_message": "", "is_blocking": False}
                diagnostic = build_scorer_diagnostic(
                    case=case,
                    method=method,
                    method_result=method_result,
                    scores=scores,
                    notes="reused raw output and rescored locally",
                )
                result_rows.append(row)
                error_rows.append(error)
                diagnostic_rows.append(diagnostic)
                write_jsonl(eval_run_dir / "case_results.jsonl", result_rows)
                continue
            if args.finalize_existing and not args.force:
                row, error, diagnostic = row_from_failure_payload(
                    case=case,
                    method=method,
                    provider=provider,
                    eval_run_dir=eval_run_dir,
                    payload=payload,
                    notes=f"finalized existing {state} checkpoint without provider call",
                )
                result_rows.append(row)
                error_rows.append(error)
                diagnostic_rows.append(diagnostic)
                write_jsonl(eval_run_dir / "case_results.jsonl", result_rows)
                continue
            if args.retry_failed_only and state == "method_failure" and not args.force:
                row, error, diagnostic = row_from_failure_payload(
                    case=case,
                    method=method,
                    provider=provider,
                    eval_run_dir=eval_run_dir,
                    payload=payload,
                    notes="reused non-provider failure",
                )
                result_rows.append(row)
                error_rows.append(error)
                diagnostic_rows.append(diagnostic)
                write_jsonl(eval_run_dir / "case_results.jsonl", result_rows)
                continue

            row = base_result_row(case=case, method=method, provider=provider, eval_run_dir=eval_run_dir)
            error = {"case_id": case_id, "method": method, "error_type": "none", "error_message": "", "is_blocking": False}
            method_result: MethodResult | None = None
            scores: dict[str, Any] | None = None
            diagnostic_note = ""
            try:
                method_input = build_method_input(case, method, facts)
                prompt = build_round3_prompt(method, method_input)
                input_payload = {
                    "case_id": case_id,
                    "method": method,
                    "prompt_version": prompt.prompt_version,
                    "prompt_sha256": prompt.prompt_sha256,
                    "allowed_context_fields": list(prompt.allowed_context_fields),
                    "method_input": method_input_to_safe_dict(method_input),
                    "system_prompt": prompt.system,
                    "user_prompt": prompt.user,
                }
                write_json(raw_inputs / f"{case_id}__{method}.json", input_payload)
                method_result, provider_attempts = provider_answer_with_retries(
                    provider=provider,
                    case=case,
                    required_facts=facts,
                    method=method,
                    prompt=prompt,
                    model=model,
                    max_retries=args.max_retries,
                    retry_backoff_seconds=args.retry_backoff_seconds,
                    request_sleep_seconds=args.request_sleep_seconds,
                )
                scores = score_attempt(
                    case=case,
                    method=method,
                    method_input=method_input,
                    method_result=method_result,
                    required_facts=facts,
                )
                output_payload = {
                    "case_id": case_id,
                    "method": method,
                    "provider": provider,
                    "success": True,
                    "provider_error": False,
                    "provider_attempts": provider_attempts,
                    "method_result": asdict(method_result),
                    "scores": scores,
                    "model_api_called": provider != "mock",
                }
                write_json(output_path, output_payload)
                row.update(
                    {
                        "success": True,
                        "provider_success": True,
                        "required_fact_recall": scores["required_fact_recall"]["required_fact_recall"],
                        "numeric_correctness": 1.0 if scores["numeric_correctness"]["numeric_correctness"] else 0.0,
                        "answer_correctness": 1.0 if scores["answer_correctness"]["answer_correctness"] else 0.0,
                        "error_type": "none",
                    }
                )
                diagnostic_note = f"scored after {provider_attempts} provider attempt(s)"
            except Round3InputIsolationError as exc:
                contamination_count += 1
                row.update({"error_type": "method_input_error", "error_message": str(exc)})
                error.update({"error_type": "method_input_error", "error_message": str(exc), "is_blocking": True})
                diagnostic_note = str(exc)
            except ProviderError as exc:
                row.update({"error_type": exc.error_type, "error_message": str(exc)})
                error.update({"error_type": exc.error_type, "error_message": str(exc), "is_blocking": True})
                write_json(
                    output_path,
                    {
                        "case_id": case_id,
                        "method": method,
                        "provider": provider,
                        "success": False,
                        "provider_error": True,
                        "error_type": exc.error_type,
                        "error_message": str(exc),
                        "model_api_called": provider != "mock",
                    },
                )
                diagnostic_note = str(exc)
            except SafetyViolation as exc:
                row.update({"error_type": "provider_auth", "error_message": str(exc)})
                error.update({"error_type": "provider_auth", "error_message": str(exc), "is_blocking": True})
                diagnostic_note = str(exc)
            except ValueError as exc:
                row.update({"error_type": "scoring_error", "error_message": str(exc)})
                error.update({"error_type": "scoring_error", "error_message": str(exc), "is_blocking": True})
                diagnostic_note = str(exc)
            except Exception as exc:  # noqa: BLE001 - per-attempt failure must be recorded, not crash the run.
                error_type = classify_exception(exc)
                row.update({"error_type": error_type, "error_message": sanitize_error_message(exc)})
                error.update({"error_type": error_type, "error_message": sanitize_error_message(exc), "is_blocking": True})
                diagnostic_note = sanitize_error_message(exc)
            result_rows.append(row)
            error_rows.append(error)
            diagnostic_rows.append(
                build_scorer_diagnostic(case=case, method=method, method_result=method_result, scores=scores, notes=diagnostic_note)
            )
            write_jsonl(eval_run_dir / "case_results.jsonl", result_rows)

    summary_rows = summarize_methods(methods, result_rows)
    method_summary_fields = [
        "method",
        "case_count",
        "attempt_count",
        "provider_success_count",
        "provider_error_count",
        "provider_error_rate",
        "scored_count",
        "method_failure_count",
        "avg_required_fact_recall_scored_only",
        "avg_required_fact_recall_all_attempts",
        "avg_numeric_correctness_scored_only",
        "avg_answer_correctness_scored_only",
        "input_contamination_count",
    ]
    write_csv(eval_run_dir / "method_summary.csv", method_summary_fields, summary_rows)
    write_csv(eval_run_dir / "error_analysis.csv", ["case_id", "method", "error_type", "error_message", "is_blocking"], error_rows)
    write_scorer_diagnostics(eval_run_dir, diagnostic_rows)
    config = {
        "generated_at": now(),
        "run_dir": rel(run_dir),
        "manifest": rel(manifest_path),
        "cases": rel(cases_path),
        "provider": provider,
        "model": model if provider != "mock" else "",
        "methods": methods,
        "ready_cases_loaded": len(cases),
        "resume": args.resume,
        "retry_failed_only": args.retry_failed_only,
        "max_retries": args.max_retries,
        "request_sleep_seconds": args.request_sleep_seconds,
        "max_concurrency": args.max_concurrency,
        "model_api_called": provider != "mock",
        "neo4j_write_performed": False,
        "kg_patch_applied": False,
        "full_eval_executed": False,
    }
    write_json(eval_run_dir / "run_config.json", config)
    write_text(
        eval_run_dir / "input_isolation_report.md",
        f"""# Input Isolation Report

Generated: {config['generated_at']}

- attempts: {len(result_rows)}
- input_contamination_count: {contamination_count}
- vector_only: vector_context only
- graph_facts_only: graph_facts only; raw evidence text excluded
- hybrid_vector_graph: vector_context and graph_facts separated
- gold_context: gold_context only
""",
    )
    failures = sum(1 for row in result_rows if not row["success"])
    provider_errors = sum(1 for row in result_rows if row["error_type"] in PROVIDER_ERROR_TYPES)
    provider_error_rate = provider_errors / max(1, len(result_rows))
    warning = ""
    if provider_error_rate > 0.10:
        warning = (
            "\n## Provider Reliability Warning\n\n"
            f"Provider error rate is {provider_error_rate:.1%}, above the 10% warning threshold. "
            "Treat scored averages as scored-output-only diagnostics, not benchmark method comparisons.\n"
        )
    write_text(
        eval_run_dir / "report.md",
        f"""# Round 3 Ready Partial Eval Report

Generated: {config['generated_at']}

## Scope

- Ready cases loaded: {len(cases)}
- Methods: `{','.join(methods)}`
- Provider: `{provider}`
- Attempts: {len(result_rows)}
- Failures: {failures}
- Provider failures: {provider_errors}
- Model API called: {str(provider != "mock").lower()}
- Neo4j write performed: false
- KG patch applied: false
- Full eval executed: false
{warning}
## Method Summary

| Method | Attempts | Provider Success | Provider Error | Error Rate | Scored | Method Failure | Avg Fact Recall Scored Only | Avg Fact Recall All Attempts | Avg Numeric Scored Only | Avg Answer Scored Only |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
{chr(10).join(f"| `{row['method']}` | {row['attempt_count']} | {row['provider_success_count']} | {row['provider_error_count']} | {row['provider_error_rate']} | {row['scored_count']} | {row['method_failure_count']} | {row['avg_required_fact_recall_scored_only']} | {row['avg_required_fact_recall_all_attempts']} | {row['avg_numeric_correctness_scored_only']} | {row['avg_answer_correctness_scored_only']} |" for row in summary_rows)}

## Boundary

This is ready-subset partial evaluation only. It is not full evaluation and does not evaluate backlog cases.
""",
    )
    update_orchestration_files(
        run_dir=run_dir,
        eval_run_dir=eval_run_dir,
        provider=provider,
        cases=cases,
        methods=methods,
        result_rows=result_rows,
        summary_rows=summary_rows,
        error_rows=error_rows,
        resume=args.resume,
        retry_failed_only=args.retry_failed_only,
    )
    return {
        "eval_run_dir": rel(eval_run_dir),
        "case_results": rel(eval_run_dir / "case_results.jsonl"),
        "method_summary": rel(eval_run_dir / "method_summary.csv"),
        "scorer_diagnostics": rel(eval_run_dir / "scorer_diagnostics.jsonl"),
        "input_isolation_report": rel(eval_run_dir / "input_isolation_report.md"),
        "ready_cases_loaded": len(cases),
        "attempt_count": len(result_rows),
        "failure_count": failures,
        "provider_failure_count": provider_errors,
        "provider": provider,
        "model_api_called": provider != "mock",
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run Round 3 ready-subset partial eval.")
    parser.add_argument("--run-dir", default=rel(DEFAULT_RUN_DIR))
    parser.add_argument("--manifest", default=rel(DEFAULT_RUN_DIR / "automation" / "ready_partial_eval_manifest.json"))
    parser.add_argument("--cases", default=rel(DEFAULT_RUN_DIR / "automation" / "ready_cases.jsonl"))
    parser.add_argument("--provider", default="mock")
    parser.add_argument("--methods", nargs="+", default=list(DEFAULT_METHODS))
    parser.add_argument("--max-cases", type=int, default=None)
    parser.add_argument("--output-dir", default=rel(REPO_ROOT / "outputs" / "round3_eval_runs"))
    parser.add_argument("--model", default="gpt-4.1-mini")
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--retry-failed-only", action="store_true")
    parser.add_argument("--max-retries", type=int, default=5)
    parser.add_argument("--retry-backoff-seconds", type=float, default=10.0)
    parser.add_argument("--request-sleep-seconds", type=float, default=20.0)
    parser.add_argument("--max-concurrency", type=int, default=1)
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--finalize-existing", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        result = run_eval(args)
    except SafetyViolation as exc:
        run_dir = resolve_path(args.run_dir)
        write_json(
            run_dir / "automation" / "partial_eval_status.json",
            {
                "generated_at": now(),
                "partial_eval_executed": False,
                "blocked": True,
                "blocker": str(exc),
                "full_eval_executed": False,
                "neo4j_write_performed": False,
                "kg_patch_applied": False,
                "model_api_called": False,
            },
        )
        write_text(run_dir / "automation" / "partial_eval_status.md", f"# Ready Partial Eval Status\n\nBlocked: {exc}\n")
        write_text(
            run_dir / "automation" / "next_action.md",
            "Create approvals/allow_partial_eval.txt to run ready-subset partial evaluation, or create approvals/allow_neo4j_readonly_coverage.txt to refine coverage. Full eval remains locked.",
        )
        print(f"blocked: {exc}")
        return 1
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
