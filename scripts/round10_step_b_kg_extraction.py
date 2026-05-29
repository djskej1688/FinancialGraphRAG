from __future__ import annotations

import json
import re
import time

from neo4j.exceptions import ServiceUnavailable, SessionExpired

import round10_common as c


OUT = c.KG_DIR
STATE = OUT / "state.json"
TRACE = OUT / "extraction_trace.jsonl"
WRITE_LOG = OUT / "kg_write_log.jsonl"
FAILED = OUT / "failed_extractions.jsonl"
ROLLBACK = OUT / "round10_kg_rollback.cypher"
MAX_RETRIES = 3
RETRY_DELAY = 5


def quote_for_value(evidence: str, value: float) -> str:
    variants = []
    if value == int(value):
        variants.extend([str(int(value)), f"{int(value):,}"])
    variants.extend([str(value), f"{value:,.1f}", f"{value:,.2f}"])
    for variant in variants:
        if variant and variant in evidence:
            pos = evidence.find(variant)
            return evidence[max(0, pos - 60): pos + len(variant) + 60].strip()
    nums = re.findall(r".{0,40}\d[\d,]*(?:\.\d+)?.{0,40}", evidence)
    return nums[0].strip() if nums else evidence[:120].strip()


def write_fact(session, case: dict, fact: dict) -> dict:
    ticker = str(case["ticker"]).upper()
    year = int(fact.get("year") or max(case.get("years") or [0]) or 0)
    metric = str(fact["metric"])
    value = float(fact["value"])
    obs_id = f"{c.ROUND10_BATCH}__{case['case_id']}__{metric}__{year}__{fact.get('fact_id', '')}"
    quote = quote_for_value(case.get("evidence_text", ""), value)
    session.run(
        """
MERGE (co:LLMCompany {ticker: $ticker})
  ON CREATE SET co.name = $company
MERGE (yr:LLMFiscalYear {year: $year})
MERGE (m:LLMFinancialMetric {canonical_name: $metric})
  ON CREATE SET m.display_name = $metric
MERGE (obs:LLMObservation {obs_id: $obs_id})
SET obs.value = $value,
    obs.unit = $unit,
    obs.evidence_quote = $quote,
    obs.kg_batch = $batch,
    obs.batch_id = $batch,
    obs.extraction_method = 'round10_contract_targeted',
    obs.validation_status = 'contract_source_fact',
    obs.source_fact_id = $source_fact_id,
    obs.case_id = $case_id,
    obs.source_dataset = $source_dataset
MERGE (obs)-[:LLM_MENTIONS_COMPANY]->(co)
MERGE (obs)-[:LLM_OBSERVES_METRIC]->(m)
MERGE (obs)-[:LLM_OBSERVED_IN_YEAR]->(yr)
""",
        ticker=ticker,
        company=case.get("company", ticker),
        year=year,
        metric=metric,
        obs_id=obs_id,
        value=value,
        unit=str(fact.get("unit") or "amount"),
        quote=quote,
        batch=c.ROUND10_BATCH,
        source_fact_id=fact.get("fact_id", ""),
        case_id=case["case_id"],
        source_dataset=case["source_dataset"],
    )
    return {"case_id": case["case_id"], "ticker": ticker, "obs_id": obs_id, "metric": metric, "year": year, "value": value, "batch_id": c.ROUND10_BATCH}


def main() -> None:
    c.assert_round9c_done()
    OUT.mkdir(parents=True, exist_ok=True)
    for path in [TRACE, FAILED]:
        if path.exists():
            path.unlink()
    ROLLBACK.write_text(f"MATCH (obs:LLMObservation {{kg_batch: '{c.ROUND10_BATCH}'}}) DETACH DELETE obs;\n", encoding="utf-8", newline="\n")
    cases = {row["case_id"]: row for row in c.load_all_round10_cases()}
    scorer, _visible = c.load_contract_maps()
    existing_log = c.read_jsonl(WRITE_LOG)
    written_obs = {row["obs_id"] for row in existing_log}
    facts_targeted = facts_written = facts_failed = 0
    driver = c.r8.create_driver()
    try:
        env = c.r8.neo4j_env()
        for cid, contract in scorer.items():
            case = cases[cid]
            written = []
            failed = []
            with driver.session(database=env["NEO4J_DATABASE"]) as session:
                for fact in contract.get("source_fact_numbers", []):
                    facts_targeted += 1
                    obs_id = f"{c.ROUND10_BATCH}__{case['case_id']}__{fact['metric']}__{int(fact.get('year') or max(case.get('years') or [0]) or 0)}__{fact.get('fact_id', '')}"
                    if obs_id in written_obs:
                        facts_written += 1
                        continue
                    for attempt in range(MAX_RETRIES):
                        try:
                            row = write_fact(session, case, fact)
                            written.append(row)
                            c.append_jsonl(WRITE_LOG, row)
                            written_obs.add(row["obs_id"])
                            facts_written += 1
                            break
                        except (ServiceUnavailable, SessionExpired) as exc:
                            if attempt == MAX_RETRIES - 1:
                                fail = {"case_id": cid, "fact": fact, "error": str(exc)}
                                failed.append(fail)
                                c.append_jsonl(FAILED, fail)
                                facts_failed += 1
                            else:
                                time.sleep(RETRY_DELAY * (attempt + 1))
                                try:
                                    driver.close()
                                except Exception:
                                    pass
                                driver = c.r8.create_driver()
                        except Exception as exc:  # noqa: BLE001
                            fail = {"case_id": cid, "fact": fact, "error": str(exc)}
                            failed.append(fail)
                            c.append_jsonl(FAILED, fail)
                            facts_failed += 1
                            break
            c.append_jsonl(TRACE, {"case_id": cid, "source_dataset": case["source_dataset"], "facts_targeted": len(contract.get("source_fact_numbers", [])), "facts_written": len(written), "facts_failed": len(failed)})
    finally:
        driver.close()
    success = facts_written / facts_targeted if facts_targeted else 0.0
    state = {
        "phase": "E_done",
        "round": c.ROUND,
        "batch_id": c.ROUND10_BATCH,
        "cases_total": len(scorer),
        "facts_targeted": facts_targeted,
        "facts_written": facts_written,
        "facts_failed": facts_failed,
        "write_success_rate": round(success, 4),
        "rollback_file": c.rel(ROLLBACK),
        "completed_at": c.utc_now(),
    }
    c.write_json(STATE, state)
    if success < 0.8:
        raise RuntimeError(f"Round10 KG write success below threshold: {success}")
    print(json.dumps(state, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
