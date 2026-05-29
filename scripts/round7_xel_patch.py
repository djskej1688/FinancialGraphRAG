"""Apply the Round 7 XEL targeted KG patch with snapshots and rollback script."""

from __future__ import annotations

import importlib.util
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
STEP_B_PATH = ROOT / "scripts" / "step_b_targeted_kg_extraction.py"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

spec = importlib.util.spec_from_file_location("step_b_targeted_kg_extraction", STEP_B_PATH)
step_b = importlib.util.module_from_spec(spec)
assert spec and spec.loader
spec.loader.exec_module(step_b)

OUT_DIR = ROOT / "outputs" / "round7_eval"
CASE_ID = "round3_test_004_b035aeed"
TICKER = "XEL"
BATCH = "kg-targeted-ie-v1-20260528"
PATCH_OBS = [
    {
        "obs_id": f"{BATCH}__{CASE_ID}__female_employee_pct__2023__r7patch",
        "metric": "female_employee_pct",
        "value": 23.0,
        "unit": "%",
        "quote": "Employees\t23 \t\t19",
        "source_fact_id": "round3_test_004_b035aeed_fact_01",
    },
    {
        "obs_id": f"{BATCH}__{CASE_ID}__female_management_pct__2023__r7patch",
        "metric": "female_management_pct",
        "value": 26.0,
        "unit": "%",
        "quote": "Management\t26 \t\t13",
        "source_fact_id": "round3_test_004_b035aeed_fact_02",
    },
]


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n")


def snapshot(driver: Any) -> list[dict[str, Any]]:
    env = step_b.neo4j_env()
    with driver.session(database=env["NEO4J_DATABASE"]) as session:
        rows = session.run(
            """
MATCH (obs:LLMObservation)-[:LLM_OBSERVES_METRIC]->(m:LLMFinancialMetric)
WHERE obs.kg_batch = $batch AND obs.case_id = $case_id
RETURN obs.obs_id AS obs_id,
       m.canonical_name AS metric_canonical,
       obs.value AS value,
       obs.unit AS unit,
       obs.validation_status AS validation_status,
       obs.evidence_quote AS evidence_quote
ORDER BY metric_canonical, obs.obs_id
""",
            batch=BATCH,
            case_id=CASE_ID,
        )
        return [dict(row) for row in rows]


def evidence_quotes() -> dict[str, Any]:
    cases = step_b.load_cases()
    text = cases[CASE_ID]["evidence_text"]
    management_ok = bool(re.search(r"Management\s+26\s+13", text))
    employees_ok = bool(re.search(r"Employees\s+23\s+19", text))
    if not management_ok or not employees_ok:
        raise RuntimeError("XEL evidence quotes not found")
    return {
        "evidence_quotes_verified": True,
        "female_employee_pct_quote": "Employees\t23 \t\t19",
        "female_management_pct_quote": "Management\t26 \t\t13",
    }


def write_rollback(before: list[dict[str, Any]]) -> None:
    patch_ids = [row["obs_id"] for row in PATCH_OBS]
    lines = [
        "// Rollback Round 7 XEL patch",
        "MATCH (obs:LLMObservation)",
        f"WHERE obs.obs_id IN {json.dumps(patch_ids)}",
        "DETACH DELETE obs;",
        "",
    ]
    for row in before:
        if row["metric_canonical"] not in {"employees", "management"}:
            continue
        status = row.get("validation_status")
        if status is None:
            lines.extend(
                [
                    "MATCH (obs:LLMObservation {obs_id: " + json.dumps(row["obs_id"]) + "})",
                    "REMOVE obs.validation_status;",
                    "",
                ]
            )
        else:
            lines.extend(
                [
                    "MATCH (obs:LLMObservation {obs_id: " + json.dumps(row["obs_id"]) + "})",
                    "SET obs.validation_status = " + json.dumps(status) + ";",
                    "",
                ]
            )
    (OUT_DIR / "xel_patch_rollback.cypher").write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8", newline="\n")


def apply_patch(driver: Any) -> None:
    env = step_b.neo4j_env()
    with driver.session(database=env["NEO4J_DATABASE"]) as session:
        session.run(
            """
MATCH (obs:LLMObservation)-[:LLM_OBSERVES_METRIC]->(m:LLMFinancialMetric)
WHERE obs.kg_batch = $batch
  AND obs.case_id = $case_id
  AND m.canonical_name IN ['employees', 'management']
SET obs.validation_status = 'deprecated_r7_patch'
""",
            batch=BATCH,
            case_id=CASE_ID,
        )
        for row in PATCH_OBS:
            session.run(
                """
MERGE (c:LLMCompany {ticker: $ticker})
MERGE (yr:LLMFiscalYear {year: 2023})
MERGE (m:LLMFinancialMetric {canonical_name: $metric})
  ON CREATE SET m.display_name = $metric
MERGE (obs:LLMObservation {obs_id: $obs_id})
SET obs.value = $value,
    obs.unit = $unit,
    obs.evidence_quote = $quote,
    obs.kg_batch = $batch,
    obs.extraction_method = 'round7_manual_verified_patch',
    obs.validation_status = 'r7_patch_ok',
    obs.source_fact_id = $source_fact_id,
    obs.case_id = $case_id
MERGE (obs)-[:LLM_MENTIONS_COMPANY]->(c)
MERGE (obs)-[:LLM_OBSERVES_METRIC]->(m)
MERGE (obs)-[:LLM_OBSERVED_IN_YEAR]->(yr)
""",
                ticker=TICKER,
                metric=row["metric"],
                obs_id=row["obs_id"],
                value=row["value"],
                unit=row["unit"],
                quote=row["quote"],
                batch=BATCH,
                source_fact_id=row["source_fact_id"],
                case_id=CASE_ID,
            )


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    quotes = evidence_quotes()
    driver = step_b.create_driver()
    try:
        before = snapshot(driver)
        write_json(OUT_DIR / "xel_patch_before_snapshot.json", before)
        write_rollback(before)
        approval = {
            "approved_by": "user_request_codex_prompt_round7_eval",
            "approved_at": utc_now(),
            "patch_scope": CASE_ID + " only",
            "existing_obs_action": "deprecated_flag_only_no_delete",
            "patch_obs_ids": [row["obs_id"] for row in PATCH_OBS],
            **quotes,
        }
        write_json(OUT_DIR / "xel_patch_approval.json", approval)
        apply_patch(driver)
        after = snapshot(driver)
        write_json(OUT_DIR / "xel_patch_after_snapshot.json", after)
        write_json(
            OUT_DIR / "xel_patch_write_log.json",
            {
                "batch_id": BATCH,
                "case_id": CASE_ID,
                "before_count": len(before),
                "after_count": len(after),
                "patch_obs_ids": [row["obs_id"] for row in PATCH_OBS],
                "completed_at": utc_now(),
            },
        )
    finally:
        driver.close()
    print(json.dumps({"case_id": CASE_ID, "batch_id": BATCH, "patch_obs": len(PATCH_OBS)}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
