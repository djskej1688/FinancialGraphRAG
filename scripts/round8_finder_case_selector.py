from __future__ import annotations

import hashlib
import json
from collections import Counter

import round8_common as c


def main() -> None:
    raw = c.read_json(c.FINDER_FULL)
    total = len(raw)
    rows = []
    counts = Counter()
    for item in raw:
        row = {
            "source_id": str(item.get("id") or item.get("_id") or ""),
            "category": item.get("category", ""),
            "reasoning_type": c.normalize_reasoning(item.get("reasoning_type") or item.get("type") or ""),
            "evidence_text": item.get("text") or item.get("references") or "",
            "question": item.get("question") or item.get("text") or "",
            "expected_answer": str(item.get("expected_answer") or item.get("answer") or ""),
        }
        if row["category"] != "Financials":
            continue
        counts["after_category_filter"] += 1
        if row["reasoning_type"] not in {"Calculation", "Compositional", "Subtraction"}:
            continue
        counts["after_reasoning_filter"] += 1
        if len(row["evidence_text"]) < 200 or len(row["question"]) < 20:
            continue
        if c.parse_number(row["expected_answer"]) is None:
            continue
        ticker = c.infer_ticker_from_text(row["question"], row["expected_answer"], row["evidence_text"][:300])
        if not ticker or ticker in c.EXCLUDED_TICKERS:
            continue
        counts["after_ticker_filter"] += 1
        row["ticker"] = ticker
        row["company"] = c.infer_company(row["evidence_text"], ticker)
        row["years"] = c.years_in_text(row["evidence_text"] + " " + row["question"])
        row["quality_score"] = c.quality_score(row["evidence_text"], row["question"], row["expected_answer"], row["reasoning_type"])
        rows.append(row)

    rows.sort(key=lambda r: (-r["quality_score"], r["ticker"], hashlib.sha256(r["source_id"].encode()).hexdigest()))
    selected = []
    used_tickers = set()
    for row in rows:
        if row["ticker"] in used_tickers:
            continue
        idx = len(selected) + 1
        case_id = f"round8_finder_{idx:03d}_{hashlib.sha256(row['source_id'].encode()).hexdigest()[:8]}"
        selected.append({
            "case_id": case_id,
            "split": "round8_test",
            "source_dataset": "FinDER",
            **row,
            "curation_round": "08",
            "kg_batch": c.ROUND8_BATCH,
            "created_at": c.utc_now(),
            "anti_cherrypick_notes": "Selected by deterministic quality scoring; not selected by observed model outcome.",
        })
        used_tickers.add(row["ticker"])
        if len(selected) >= 30:
            break

    c.write_jsonl(c.FINDER_CANDIDATES, selected)
    state = {
        "phase": "A_done",
        "dataset": "FinDER",
        "total_input": total,
        "after_category_filter": counts["after_category_filter"],
        "after_reasoning_filter": counts["after_reasoning_filter"],
        "after_ticker_filter": counts["after_ticker_filter"],
        "after_dedup_ticker": len({r["ticker"] for r in rows}),
        "selected": len(selected),
        "selected_tickers": [r["ticker"] for r in selected],
        "output": c.rel(c.FINDER_CANDIDATES),
        "completed_at": c.utc_now(),
    }
    c.write_json(c.SELECTION_STATE, state)
    print(json.dumps(state, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
