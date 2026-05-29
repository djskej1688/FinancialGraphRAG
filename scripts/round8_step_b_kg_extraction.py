from __future__ import annotations

import json
import re

import round8_common as c


OUT = c.KG_DIR
STATE = OUT / "state.json"
TRACE = OUT / "extraction_trace.jsonl"
WRITE_LOG = OUT / "kg_write_log.jsonl"
FAILED = OUT / "failed_extractions.jsonl"
ROLLBACK = OUT / "round8_kg_rollback.cypher"


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
    obs_id = f"{c.ROUND8_BATCH}__{case['case_id']}__{metric}__{year}__{fact.get('fact_id', '')}"
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
    obs.extraction_method = 'round8_contract_targeted',
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
        batch=c.ROUND8_BATCH,
        source_fact_id=fact.get("fact_id", ""),
        case_id=case["case_id"],
        source_dataset=case["source_dataset"],
    )
    return {"case_id": case["case_id"], "ticker": ticker, "obs_id": obs_id, "metric": metric, "year": year, "value": value, "batch_id": c.ROUND8_BATCH}


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    for path in [TRACE, WRITE_LOG, FAILED]:
        if path.exists():
            path.unlink()
    ROLLBACK.write_text(
        f"MATCH (obs:LLMObservation {{kg_batch: '{c.ROUND8_BATCH}'}}) DETACH DELETE obs;\n",
        encoding="utf-8",
        newline="\n",
    )
    cases = {row["case_id"]: row for row in c.load_all_round8_cases()}
    scorer, _visible = c.load_contract_maps()
    facts_targeted = 0
    facts_written = 0
    facts_failed = 0
    driver = c.create_driver()
    try:
        env = c.neo4j_env()
        with driver.session(database=env["NEO4J_DATABASE"]) as session:
            existing = session.run("MATCH (obs:LLMObservation {kg_batch: $batch}) RETURN count(obs) AS n", batch=c.ROUND8_BATCH).single()["n"]
            if int(existing):
                print(json.dumps({"batch_id": c.ROUND8_BATCH, "existing": existing, "action": "append_or_update"}, ensure_ascii=False))
            for cid, contract in scorer.items():
                case = cases[cid]
                written = []
                failed = []
                for fact in contract.get("source_fact_numbers", []):
                    facts_targeted += 1
                    try:
                        row = write_fact(session, case, fact)
                        written.append(row)
                        c.append_jsonl(WRITE_LOG, row)
                        facts_written += 1
                    except Exception as exc:  # noqa: BLE001
                        fail = {"case_id": cid, "fact": fact, "error": str(exc)}
                        failed.append(fail)
                        c.append_jsonl(FAILED, fail)
                        facts_failed += 1
                c.append_jsonl(TRACE, {"case_id": cid, "source_dataset": case["source_dataset"], "facts_targeted": len(contract.get("source_fact_numbers", [])), "facts_written": len(written), "facts_failed": len(failed)})
    finally:
        driver.close()
    success = facts_written / facts_targeted if facts_targeted else 0.0
    state = {
        "phase": "D_done",
        "batch_id": c.ROUND8_BATCH,
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
        raise RuntimeError(f"Round8 KG write success below threshold: {success}")
    print(json.dumps(state, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
