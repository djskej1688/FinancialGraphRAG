from __future__ import annotations

import hashlib
import json
from collections import Counter

import round8_common as c


def main() -> None:
    finder = c.read_jsonl(c.FINDER_CANDIDATES)
    used_tickers = {row["ticker"] for row in finder}
    raw = c.read_json(c.FINQA_TRAIN)
    counts = Counter()
    candidates = []
    for row in raw:
        qa = row.get("qa") or {}
        answer = str(qa.get("answer") or "")
        answer_num = c.parse_number(answer)
        if answer_num is None:
            continue
        counts["answer_numeric"] += 1
        program = str(qa.get("program") or "")
        if not program or not any(op in program for op in ["divide", "multiply", "subtract", "add"]):
            continue
        counts["program_calc"] += 1
        filename = str(row.get("filename") or "")
        ticker = filename.split("/", 1)[0].upper()
        if not ticker or ticker in c.STOP_TICKERS or ticker in c.EXCLUDED_TICKERS or ticker in used_tickers:
            continue
        counts["ticker_ok"] += 1
        evidence = c.finqa_evidence(row)
        if len(evidence) < 200:
            continue
        nums = c.program_numbers(program)
        if len(nums) < 2:
            continue
        quality = c.quality_score(evidence, qa.get("question", ""), answer, "Calculation")
        quality += min(3.0, len(nums) * 0.6)
        candidates.append({
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
            "years": c.years_in_text(evidence + " " + qa.get("question", "")),
            "quality_score": round(quality, 4),
        })

    candidates.sort(key=lambda r: (-r["quality_score"], r["ticker"], hashlib.sha256(r["source_filename"].encode()).hexdigest()))
    selected = []
    seen = set()
    for row in candidates:
        if row["ticker"] in seen:
            continue
        idx = len(selected) + 1
        case_id = f"round8_finqa_{idx:03d}_{hashlib.sha256(row['source_filename'].encode()).hexdigest()[:8]}"
        selected.append({
            "case_id": case_id,
            "split": "round8_test",
            "source_dataset": "FinQA",
            **row,
            "curation_round": "08",
            "kg_batch": c.ROUND8_BATCH,
            "created_at": c.utc_now(),
            "anti_cherrypick_notes": "Selected by deterministic quality scoring; not selected by observed model outcome.",
        })
        seen.add(row["ticker"])
        if len(selected) >= 20:
            break

    c.write_jsonl(c.FINQA_CANDIDATES, selected)
    state = c.read_json(c.SELECTION_STATE) if c.SELECTION_STATE.exists() else {}
    state.update({
        "phase": "B_done",
        "finqa_total_input": len(raw),
        "finqa_answer_numeric": counts["answer_numeric"],
        "finqa_program_calc": counts["program_calc"],
        "finqa_after_ticker_filter": counts["ticker_ok"],
        "finqa_selected": len(selected),
        "finqa_selected_tickers": [r["ticker"] for r in selected],
        "finqa_output": c.rel(c.FINQA_CANDIDATES),
        "completed_at": c.utc_now(),
    })
    c.write_json(c.SELECTION_STATE, state)
    print(json.dumps(state, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
