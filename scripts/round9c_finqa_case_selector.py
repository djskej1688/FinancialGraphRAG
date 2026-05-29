from __future__ import annotations

import hashlib
import json
from collections import Counter

import round8_common as r8
import round9c_common as c
from ticker_filter import filter_ticker


def main() -> None:
    c.assert_round9b_ready()
    finder = c.read_jsonl(c.FINDER_CANDIDATES)
    used_tickers = {row["ticker"] for row in finder}
    r8_finqa_used = {row["source_filename"] for row in c.read_jsonl(c.R8_FINQA_CANDIDATES)}
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
        if filename in r8_finqa_used:
            counts["r8_source_excluded"] += 1
            continue
        ticker = filter_ticker(filename.split("/", 1)[0])
        if not ticker or ticker in c.EXCLUDED_TICKERS or ticker in used_tickers:
            continue
        counts["ticker_ok"] += 1
        evidence = r8.finqa_evidence(row)
        if len(evidence) < 200:
            continue
        nums = r8.program_numbers(program)
        if len(nums) < 2:
            continue
        quality = r8.quality_score(evidence, qa.get("question", ""), answer, "Calculation")
        quality += min(3.0, len(nums) * 0.6)
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

    candidates.sort(key=lambda r: (-r["quality_score"], r["ticker"], hashlib.sha256(r["source_filename"].encode()).hexdigest()))
    selected = []
    seen = set()
    for row in candidates:
        if row["ticker"] in seen:
            continue
        idx = len(selected) + 1
        case_id = f"round9c_finqa_{idx:03d}_{hashlib.sha256(row['source_filename'].encode()).hexdigest()[:8]}"
        selected.append(
            {
                "case_id": case_id,
                "split": "round9c_test",
                "source_dataset": "FinQA",
                **row,
                "curation_round": "09C",
                "kg_batch": c.ROUND9C_BATCH,
                "created_at": c.utc_now(),
                "anti_cherrypick_notes": "Selected by deterministic quality scoring after excluding Round 3/Round 8 tickers and source records.",
            }
        )
        seen.add(row["ticker"])
        if len(selected) >= 20:
            break
    if len(selected) < 20:
        raise RuntimeError(f"Expected 20 FinQA cases, selected {len(selected)}")

    c.write_jsonl(c.FINQA_CANDIDATES, selected)
    state = c.read_json(c.SELECTION_STATE) if c.SELECTION_STATE.exists() else {}
    state.update(
        {
            "phase": "B_done",
            "finqa_total_input": len(raw),
            "finqa_answer_numeric": counts["answer_numeric"],
            "finqa_program_calc": counts["program_calc"],
            "finqa_r8_source_excluded": counts["r8_source_excluded"],
            "finqa_after_ticker_filter": counts["ticker_ok"],
            "finqa_selected": len(selected),
            "finqa_selected_tickers": [row["ticker"] for row in selected],
            "finqa_output": c.rel(c.FINQA_CANDIDATES),
            "completed_at": c.utc_now(),
        }
    )
    c.write_json(c.SELECTION_STATE, state)
    print(json.dumps(state, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
