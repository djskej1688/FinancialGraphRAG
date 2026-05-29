from __future__ import annotations

import hashlib
import json
from collections import Counter

import round8_common as r8
import round10_common as c
from ticker_filter import filter_ticker


TARGET = 100
FALLBACK_MIN = 80


def main() -> None:
    c.assert_round9c_done()
    excluded = c.excluded_tickers()
    used_filenames = c.used_finqa_filenames()
    raw = r8.read_json(r8.FINQA_TRAIN)
    counts = Counter()
    candidates = []
    for row in raw:
        qa = row.get("qa") or {}
        answer = str(qa.get("answer") or "")
        answer_num = r8.parse_number(answer)
        if answer_num is None:
            continue
        counts["answer_numeric"] += 1
        program = str(qa.get("program") or "")
        if not program or not any(op in program for op in ["divide", "multiply", "subtract", "add"]):
            continue
        counts["program_calc"] += 1
        filename = str(row.get("filename") or "")
        if filename in used_filenames:
            counts["previous_source_excluded"] += 1
            continue
        ticker = filter_ticker(filename.split("/", 1)[0])
        if not ticker or ticker in excluded:
            continue
        evidence = r8.finqa_evidence(row)
        if len(evidence) < 200 or len(r8.program_numbers(program)) < 2:
            continue
        quality = r8.quality_score(evidence, qa.get("question", ""), answer, "Calculation")
        quality += min(3.0, len(r8.program_numbers(program)) * 0.6)
        candidates.append(
            {
                "source_filename": filename,
                "ticker": ticker,
                "company": ticker,
                "category": "Financials",
                "reasoning_type": "Calculation",
                "evidence_text": evidence,
                "table_ori": row.get("table_ori") or [],
                "question": qa.get("question", ""),
                "expected_answer": answer,
                "expected_answer_numeric": answer_num,
                "program": program,
                "ops": qa.get("program_re") or qa.get("ops") or "",
                "years": r8.years_in_text(evidence + " " + qa.get("question", "")),
                "quality_score": round(quality, 4),
            }
        )
    candidates.sort(key=lambda row: (-row["quality_score"], row["ticker"], hashlib.sha256(row["source_filename"].encode()).hexdigest()))
    selected = []
    seen = set()
    for row in candidates:
        if row["ticker"] in seen:
            continue
        idx = len(selected) + 1
        selected.append(
            {
                "case_id": f"round10_finqa_{idx:03d}_{hashlib.sha256(row['source_filename'].encode()).hexdigest()[:8]}",
                "split": "round10_test",
                "source_dataset": "FinQA",
                **row,
                "curation_round": "10",
                "kg_batch": c.ROUND10_BATCH,
                "created_at": c.utc_now(),
                "anti_cherrypick_notes": "Deterministic quality scoring; excludes prior rounds by source filename and ticker.",
            }
        )
        seen.add(row["ticker"])
        if len(selected) >= TARGET:
            break
    below_fallback = len(selected) < FALLBACK_MIN
    c.write_jsonl(c.FINQA_CANDIDATES, selected)
    state = c.read_json(c.SELECTION_STATE)
    state.update(
        {
            "phase": "B_done",
            "finqa_target": TARGET,
            "finqa_fallback_min": FALLBACK_MIN,
            "finqa_selected": len(selected),
            "finqa_shortfall": max(0, TARGET - len(selected)),
            "finqa_below_fallback_min": below_fallback,
            "finqa_selected_tickers": [row["ticker"] for row in selected],
            "finqa_counts": dict(counts),
            "completed_at": c.utc_now(),
        }
    )
    c.write_json(c.SELECTION_STATE, state)
    print(json.dumps(state, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
