"""Create isolated Round 3 orchestration setup artifacts.

This script is intentionally read-only with respect to the repaired subset,
Round 02 artifacts, and Neo4j. It writes only under
``outputs/round3_orchestration/<timestamp>/``.
"""

from __future__ import annotations

import csv
import hashlib
import json
import os
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any


REPO = Path(__file__).resolve().parents[1]
REPAIRED_DIR = REPO / "outputs" / "round3_case_factory_repaired"
OUTPUT_ROOT = REPO / "outputs" / "round3_orchestration"

STRATEGY_PATH = Path(
    r"C:\Users\USER\Documents\Finance graphRAG\실제구축정리\finder_round3_final_multi_orchestration_strategy.md"
)

REPAIRED_FILES = [
    "repair_summary.md",
    "repair_summary.json",
    "eval_ready_cases.jsonl",
    "eval_ready_required_facts.jsonl",
    "exact_quote_recovery_report.md",
    "company_ticker_patch_review.jsonl",
    "parser_artifact_exclusions.jsonl",
    "neo4j_readonly_coverage_report.md",
    "go_no_go_decision.md",
]

CASE_REQUIRED = {
    "case_id",
    "split",
    "question",
    "expected_answer",
    "evidence_text",
    "company",
    "ticker",
    "required_fact_count",
}

FACT_REQUIRED = {
    "case_id",
    "fact_id",
    "metric_raw",
    "metric_canonical",
    "value",
    "unit",
    "year",
    "evidence_quote_exact",
    "quote_is_exact_excerpt",
    "source_fact",
    "derived_answer_value",
}


def sha256_file(path: Path) -> str | None:
    if not path.exists():
        return None
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    if not path.exists():
        return rows
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    with path.open("w", encoding="utf-8", newline="\n") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")


def file_record(path: Path) -> dict[str, Any]:
    exists = path.exists()
    stat = path.stat() if exists else None
    record: dict[str, Any] = {
        "path": str(path.relative_to(REPO)) if path.is_relative_to(REPO) else str(path),
        "exists": exists,
        "length_bytes": stat.st_size if stat else None,
        "modified_time_local": datetime.fromtimestamp(stat.st_mtime).isoformat(timespec="seconds")
        if stat
        else None,
        "sha256": sha256_file(path),
        "row_count": None,
        "notes": "",
    }
    if exists and path.suffix == ".jsonl":
        record["row_count"] = sum(1 for line in path.open("r", encoding="utf-8") if line.strip())
    elif exists and path.suffix == ".json":
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            record["json_type"] = type(data).__name__
            if isinstance(data, list):
                record["row_count"] = len(data)
            elif isinstance(data, dict):
                record["top_level_keys"] = sorted(data.keys())
        except json.JSONDecodeError as exc:
            record["notes"] = f"json_decode_error: {exc}"
    return record


def parse_env_file(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.exists():
        return values
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip()
    return values


def env_status() -> dict[str, Any]:
    env_file = REPO / ".env"
    env_values = parse_env_file(env_file)
    required = ["NEO4J_URI", "NEO4J_PASSWORD", "NEO4J_DATABASE"]
    user_keys = ["NEO4J_USERNAME", "NEO4J_USER"]
    present: dict[str, bool] = {}
    for key in required + user_keys:
        value = os.environ.get(key) or env_values.get(key)
        present[key] = bool(value)
    user_present = present["NEO4J_USERNAME"] or present["NEO4J_USER"]
    complete = present["NEO4J_URI"] and user_present and present["NEO4J_PASSWORD"]
    return {
        "env_file_exists": env_file.exists(),
        "neo4j_required_present": present,
        "neo4j_user_key_acceptable": user_present,
        "neo4j_database_missing_is_defaultable": not present["NEO4J_DATABASE"],
        "neo4j_config_complete_for_readonly_check": complete,
        "secrets_printed": False,
    }


def protected_status() -> dict[str, Any]:
    candidates = [
        REPO / "outputs" / "kg_build" / "curation_round_02",
        REPO / "outputs" / "kg_build" / "eval_round02",
    ]
    selected7 = [
        p
        for p in REPO.rglob("*")
        if p.is_file() and "selected7" in p.name.lower() and ".venv" not in p.parts
    ]
    return {
        "curation_round_02": [file_record(p) for p in candidates if p.exists()],
        "eval_round02": [file_record(p) for p in candidates[1:] if p.exists()],
        "selected7_files": [file_record(p) for p in selected7],
        "round02_artifacts_found": any(p.exists() for p in candidates) or bool(selected7),
        "round02_artifacts_modified_by_this_script": False,
    }


def validate_cases_and_facts(cases: list[dict[str, Any]], facts: list[dict[str, Any]]) -> dict[str, Any]:
    case_ids = [str(row.get("case_id", "")) for row in cases]
    fact_case_ids = [str(row.get("case_id", "")) for row in facts]
    case_id_set = set(case_ids)
    split_counts = Counter(str(row.get("split", "")) for row in cases)
    missing_case_fields = {
        str(row.get("case_id", f"row_{idx}")): sorted(CASE_REQUIRED - row.keys())
        for idx, row in enumerate(cases, start=1)
        if CASE_REQUIRED - row.keys()
    }
    missing_fact_fields = {
        str(row.get("fact_id", f"row_{idx}")): sorted(FACT_REQUIRED - row.keys())
        for idx, row in enumerate(facts, start=1)
        if FACT_REQUIRED - row.keys()
    }
    facts_by_case = Counter(fact_case_ids)
    declared_mismatch = []
    for row in cases:
        case_id = str(row.get("case_id", ""))
        declared = row.get("required_fact_count")
        actual = facts_by_case.get(case_id, 0)
        if declared != actual:
            declared_mismatch.append(
                {"case_id": case_id, "declared_required_fact_count": declared, "actual_fact_count": actual}
            )
    return {
        "case_count": len(cases),
        "fact_count": len(facts),
        "split_counts": dict(sorted(split_counts.items())),
        "duplicate_case_ids": sorted([k for k, v in Counter(case_ids).items() if v > 1]),
        "facts_with_unknown_case_id": sorted(set(fact_case_ids) - case_id_set),
        "cases_without_facts": sorted(case_id_set - set(fact_case_ids)),
        "missing_case_fields": missing_case_fields,
        "missing_fact_fields": missing_fact_fields,
        "required_fact_count_mismatches": declared_mismatch,
    }


def exact_quote_status(facts: list[dict[str, Any]]) -> dict[str, Any]:
    quote_false = [row.get("fact_id") for row in facts if row.get("quote_is_exact_excerpt") is not True]
    source_false = [row.get("fact_id") for row in facts if row.get("source_fact") is not True]
    derived_true = [row.get("fact_id") for row in facts if row.get("derived_answer_value") is True]
    missing_quote = [row.get("fact_id") for row in facts if not row.get("evidence_quote_exact")]
    return {
        "fact_count": len(facts),
        "quote_is_exact_excerpt_true_count": len(facts) - len(quote_false),
        "quote_is_exact_excerpt_false_fact_ids": quote_false,
        "source_fact_false_fact_ids": source_false,
        "derived_answer_value_true_fact_ids": derived_true,
        "missing_evidence_quote_fact_ids": missing_quote,
        "exact_quote_coverage": (len(facts) - len(quote_false)) / max(1, len(facts)),
        "derived_leakage_count": len(derived_true),
    }


def build_gemini_queue(cases: list[dict[str, Any]], facts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    facts_by_case: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for fact in facts:
        facts_by_case[str(fact.get("case_id"))].append(fact)

    queue = []
    for case in cases:
        case_id = str(case.get("case_id"))
        queue.append(
            {
                "case_id": case_id,
                "split": case.get("split"),
                "company": case.get("company"),
                "ticker": case.get("ticker"),
                "category": case.get("category"),
                "question": case.get("question"),
                "expected_answer": case.get("expected_answer"),
                "evidence_text": case.get("evidence_text"),
                "required_facts": facts_by_case.get(case_id, []),
                "review_contract": {
                    "semantic_pass": None,
                    "company_ticker_pass": None,
                    "calculation_reproducible": None,
                    "required_facts_are_source_facts": None,
                    "evidence_quote_sufficient": None,
                    "unit_year_metric_pass": None,
                    "fairness_pass": None,
                    "recommended_split": None,
                    "blocking_issues": [],
                    "non_blocking_warnings": [],
                    "review_notes": "",
                },
            }
        )
    return queue


def write_markdown(path: Path, text: str) -> None:
    path.write_text(text.strip() + "\n", encoding="utf-8", newline="\n")


def main() -> None:
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_dir = OUTPUT_ROOT / timestamp
    out_dir.mkdir(parents=True, exist_ok=False)

    cases = load_jsonl(REPAIRED_DIR / "eval_ready_cases.jsonl")
    facts = load_jsonl(REPAIRED_DIR / "eval_ready_required_facts.jsonl")
    validation = validate_cases_and_facts(cases, facts)
    quote_status = exact_quote_status(facts)
    env = env_status()

    manifest = {
        "created_at_local": datetime.now().isoformat(timespec="seconds"),
        "strategy_path": str(STRATEGY_PATH),
        "strategy_sha256": sha256_file(STRATEGY_PATH),
        "output_dir": str(out_dir.relative_to(REPO)),
        "source_repaired_dir": str(REPAIRED_DIR.relative_to(REPO)),
        "repaired_subset_files": [file_record(REPAIRED_DIR / name) for name in REPAIRED_FILES],
        "config_files": [
            file_record(REPO / ".env.example"),
            file_record(REPO / "extraction" / "conf" / "config.yaml"),
            file_record(REPO / "extraction" / "conf" / "graphs" / "default.yaml"),
            file_record(REPO / "seocho" / "agent_config.py"),
            file_record(REPO / "seocho" / "benchmarking.py"),
            file_record(REPO / "seocho" / "query" / "cypher_builder.py"),
            file_record(REPO / "seocho" / "query" / "cypher_validator.py"),
        ],
        "prompt_files": [
            file_record(path)
            for path in sorted((REPO / "extraction" / "conf" / "prompts").glob("*.yaml"))
        ],
        "protected_artifacts": protected_status(),
        "environment_status": env,
        "safety": {
            "curation_round_02_modified": False,
            "eval_round02_modified": False,
            "selected7_files_modified": False,
            "kg_writes_performed": False,
            "kg_patches_applied": False,
            "round3_test_used_for_tuning": False,
            "original_round3_candidate_pool_modified": False,
        },
    }
    (out_dir / "artifact_manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
        newline="\n",
    )

    (out_dir / "schema_validation_report.json").write_text(
        json.dumps(validation, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
        newline="\n",
    )
    write_markdown(
        out_dir / "schema_validation_report.md",
        f"""
# Schema Validation Report

- Case count: {validation["case_count"]}
- Required fact count: {validation["fact_count"]}
- Split counts: `{json.dumps(validation["split_counts"], ensure_ascii=False, sort_keys=True)}`
- Missing case fields: {len(validation["missing_case_fields"])}
- Missing fact fields: {len(validation["missing_fact_fields"])}
- Facts with unknown case_id: {len(validation["facts_with_unknown_case_id"])}
- Cases without facts: {len(validation["cases_without_facts"])}
- Required fact count mismatches: {len(validation["required_fact_count_mismatches"])}

This is structural validation only. Semantic correctness remains pending Gemini review.
""",
    )

    write_markdown(
        out_dir / "duplicate_check_report.md",
        f"""
# Duplicate Check Report

- Duplicate case_id count: {len(validation["duplicate_case_ids"])}
- Duplicate case_ids: `{json.dumps(validation["duplicate_case_ids"], ensure_ascii=False)}`
""",
    )

    (out_dir / "exact_quote_check_report.json").write_text(
        json.dumps(quote_status, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
        newline="\n",
    )
    write_markdown(
        out_dir / "exact_quote_check_report.md",
        f"""
# Exact Quote Check Report

- Fact count: {quote_status["fact_count"]}
- Exact quote coverage: {quote_status["exact_quote_coverage"]:.2%}
- `quote_is_exact_excerpt=false`: {len(quote_status["quote_is_exact_excerpt_false_fact_ids"])}
- `source_fact=false`: {len(quote_status["source_fact_false_fact_ids"])}
- `derived_answer_value=true`: {quote_status["derived_leakage_count"]}
- Missing evidence quotes: {len(quote_status["missing_evidence_quote_fact_ids"])}

This trusts the repaired subset flags. Gemini should still challenge evidence sufficiency.
""",
    )

    queue = build_gemini_queue(cases, facts)
    write_jsonl(out_dir / "gemini_review_queue.jsonl", queue)
    write_markdown(
        out_dir / "gemini_prompt.md",
        """
# Gemini Review Prompt

You are the independent semantic reviewer for FinDER GraphRAG / HybridRAG Round 3.

Do not mutate files. Do not apply KG patches. Do not write to Neo4j. Do not tune prompts.

Review `gemini_review_queue.jsonl`. Return one JSON object per input case with:

```json
{
  "case_id": "...",
  "semantic_pass": true,
  "company_ticker_pass": true,
  "calculation_reproducible": true,
  "required_facts_are_source_facts": true,
  "evidence_quote_sufficient": true,
  "unit_year_metric_pass": true,
  "fairness_pass": true,
  "recommended_split": "round3_dev|round3_test|baseline_control|demo_only|reject",
  "blocking_issues": [],
  "non_blocking_warnings": [],
  "review_notes": "..."
}
```

Be adversarial but fair. Challenge derived leakage, insufficient evidence quotes, parser/header artifacts, company/ticker ambiguity, unit/year mistakes, and unfair method input advantages.
""",
    )

    gate1_pass = (
        validation["case_count"] >= 15
        and validation["split_counts"].get("round3_test", 0) >= 10
        and quote_status["exact_quote_coverage"] == 1.0
        and quote_status["derived_leakage_count"] == 0
        and len(validation["duplicate_case_ids"]) == 0
    )
    write_markdown(
        out_dir / "gate_ledger.md",
        f"""
# Round 3 Gate Ledger

## Gate 0 - Artifact Freeze

Status: PASS

- Repaired subset manifest written: `artifact_manifest.json`
- Original repaired subset mutated: no
- Original Round 3 candidate pool mutated: no
- Round 02 artifacts found in this checkout: {manifest["protected_artifacts"]["round02_artifacts_found"]}
- Round 02 artifacts mutated: no

## Gate 1 - Local Integrity

Status: {"PASS" if gate1_pass else "FAIL"}

- eval-ready cases >= 15: {validation["case_count"] >= 15} ({validation["case_count"]})
- round3_test cases >= 10: {validation["split_counts"].get("round3_test", 0) >= 10} ({validation["split_counts"].get("round3_test", 0)})
- exact evidence quote coverage = 100%: {quote_status["exact_quote_coverage"] == 1.0} ({quote_status["exact_quote_coverage"]:.2%})
- derived leakage = 0: {quote_status["derived_leakage_count"] == 0} ({quote_status["derived_leakage_count"]})
- duplicate case_id = 0: {len(validation["duplicate_case_ids"]) == 0} ({len(validation["duplicate_case_ids"])})

## Gate 2 - Gemini Semantic Review

Status: PENDING

- Queue written: `gemini_review_queue.jsonl`
- Prompt written: `gemini_prompt.md`

## Gate 3 - Neo4j Read-Only Coverage

Status: {"READY_TO_RUN" if env["neo4j_config_complete_for_readonly_check"] else "BLOCKED_CONFIG_INCOMPLETE"}

- Neo4j config complete: {env["neo4j_config_complete_for_readonly_check"]}
- Secret values printed: no

## Gate 4 - Dry Run

Status: BLOCKED_UNTIL_GATES_2_AND_3_PASS

## Gate 5 - Full Evaluation

Status: BLOCKED_UNTIL_ANTIGRAVITY_FINAL_GO
""",
    )

    write_markdown(
        out_dir / "decision_log.md",
        f"""
# Decision Log

- {datetime.now().isoformat(timespec="seconds")} Created isolated Round 3 orchestration setup directory.
- {datetime.now().isoformat(timespec="seconds")} Froze repaired subset by hash and timestamp in `artifact_manifest.json`.
- {datetime.now().isoformat(timespec="seconds")} Created Gemini review queue. No semantic pass/fail decisions made by Codex.
- {datetime.now().isoformat(timespec="seconds")} Neo4j coverage not executed by setup script. Read-only check requires complete config and explicit next step.
""",
    )

    write_markdown(
        out_dir / "neo4j_coverage_status.md",
        f"""
# Neo4j Coverage Status

Status: {"ready_to_run_read_only" if env["neo4j_config_complete_for_readonly_check"] else "blocked_config_incomplete"}

Required variables:

- `NEO4J_URI`: {env["neo4j_required_present"]["NEO4J_URI"]}
- `NEO4J_USERNAME` or `NEO4J_USER`: {env["neo4j_user_key_acceptable"]}
- `NEO4J_PASSWORD`: {env["neo4j_required_present"]["NEO4J_PASSWORD"]}
- `NEO4J_DATABASE`: {env["neo4j_required_present"]["NEO4J_DATABASE"]} (can default to `neo4j` if omitted)

No Neo4j queries were executed. No Neo4j writes were performed.
""",
    )

    write_markdown(
        out_dir / "env_setup_checklist.md",
        """
# Environment Setup Checklist

Create `.env` from `.env.example` if needed, then fill secrets locally.

Minimum for Neo4j read-only coverage:

```text
NEO4J_URI=bolt://localhost:7687
NEO4J_USER=neo4j
NEO4J_USERNAME=neo4j
NEO4J_PASSWORD=<your local password>
NEO4J_DATABASE=neo4j
```

Optional for final trace validation:

```text
SEOCHO_TRACE_BACKEND=jsonl
SEOCHO_TRACE_JSONL_PATH=./traces/round3-orchestration.jsonl
```

Optional for final Opik traces only after dry-run passes:

```text
SEOCHO_TRACE_BACKEND=opik
OPIK_WORKSPACE=<workspace>
OPIK_PROJECT_NAME=seocho-round3
OPIK_API_KEY=<your key, hosted mode only>
```

Keep Round 02 outputs and the repaired subset read-only during orchestration.
""",
    )

    write_markdown(
        out_dir / "final_go_no_go_decision.md",
        """
# Final Go / No-Go Decision

Decision: conditional_go

Round 3 is local-evidence-ready but not final benchmark-ready.

Blocking gates:

- Gemini semantic/fairness review pending.
- Neo4j read-only coverage pending or config incomplete.
- Small dry run pending.
- Antigravity final go pending.
""",
    )

    safety_rows = [
        ["curation_round_02 modified", "no"],
        ["eval_round02 modified", "no"],
        ["selected7 files modified", "no"],
        ["KG writes performed", "no"],
        ["KG patches applied", "no"],
        ["round3_test used for tuning", "no"],
    ]
    with (out_dir / "safety_status.csv").open("w", encoding="utf-8", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["check", "status"])
        writer.writerows(safety_rows)

    print(out_dir)


if __name__ == "__main__":
    main()
