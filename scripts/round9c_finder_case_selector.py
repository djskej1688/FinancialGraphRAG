from __future__ import annotations

import hashlib
import json
import re
from collections import Counter

import round8_common as r8
import round9c_common as c
from ticker_filter import filter_ticker


def infer_valid_ticker(allowlist: set[str], *parts: str) -> str | None:
    text = " ".join(parts)
    candidates = []
    candidates.extend(re.findall(r"\(([A-Z]{1,5})\)", text))
    candidates.extend(re.findall(r"\b[A-Z]{2,5}\b", text))
    for raw in candidates:
        ticker = filter_ticker(raw)
        if ticker and ticker in allowlist and ticker not in c.EXCLUDED_TICKERS:
            return ticker
    return None


def main() -> None:
    c.assert_round9b_ready()
    raw = r8.read_json(r8.FINDER_FULL)
    finqa_raw = r8.read_json(r8.FINQA_TRAIN)
    allowlist = {str(row.get("filename") or "").split("/", 1)[0].upper() for row in finqa_raw if row.get("filename")}
    total = len(raw)
    rows = []
    counts = Counter()
    r8_source_ids = {row.get("source_id") for row in c.read_jsonl(c.R8_FINDER_CANDIDATES)}
    for item in raw:
        row = {
            "source_id": str(item.get("id") or item.get("_id") or ""),
            "category": item.get("category", ""),
            "reasoning_type": r8.normalize_reasoning(item.get("reasoning_type") or item.get("type") or ""),
            "evidence_text": item.get("text") or item.get("references") or "",
            "question": item.get("question") or item.get("text") or "",
            "expected_answer": str(item.get("expected_answer") or item.get("answer") or ""),
        }
        if row["source_id"] in r8_source_ids:
            counts["r8_source_excluded"] += 1
            continue
        if row["category"] != "Financials":
            continue
        counts["after_category_filter"] += 1
        if row["reasoning_type"] not in {"Calculation", "Compositional", "Subtraction"}:
            continue
        counts["after_reasoning_filter"] += 1
        if len(row["evidence_text"]) < 200 or len(row["question"]) < 20:
            continue
        if r8.parse_number(row["expected_answer"]) is None:
            continue
        ticker = infer_valid_ticker(allowlist, row["question"], row["expected_answer"], row["evidence_text"][:300])
        if not ticker:
            continue
        counts["after_ticker_filter"] += 1
        row["ticker"] = ticker
        row["company"] = r8.infer_company(row["evidence_text"], ticker)
        row["years"] = r8.years_in_text(row["evidence_text"] + " " + row["question"])
        row["quality_score"] = r8.quality_score(row["evidence_text"], row["question"], row["expected_answer"], row["reasoning_type"])
        rows.append(row)

    rows.sort(key=lambda r: (-r["quality_score"], r["ticker"], hashlib.sha256(r["source_id"].encode()).hexdigest()))
    selected = []
    used_tickers = set()
    for row in rows:
        if row["ticker"] in used_tickers:
            continue
        idx = len(selected) + 1
        case_id = f"round9c_finder_{idx:03d}_{hashlib.sha256(row['source_id'].encode()).hexdigest()[:8]}"
        selected.append(
            {
                "case_id": case_id,
                "split": "round9c_test",
                "source_dataset": "FinDER",
                **row,
                "curation_round": "09C",
                "kg_batch": c.ROUND9C_BATCH,
                "created_at": c.utc_now(),
                "anti_cherrypick_notes": "Selected by deterministic quality scoring after excluding Round 3/Round 8 tickers and suspect ticker tokens.",
            }
        )
        used_tickers.add(row["ticker"])
        if len(selected) >= 30:
            break
    if len(selected) < 30:
        raise RuntimeError(f"Expected 30 FinDER cases, selected {len(selected)}")

    c.write_jsonl(c.FINDER_CANDIDATES, selected)
    state = {
        "phase": "A_done",
        "round": c.ROUND,
        "dataset": "FinDER",
        "total_input": total,
        "r8_source_excluded": counts["r8_source_excluded"],
        "after_category_filter": counts["after_category_filter"],
        "after_reasoning_filter": counts["after_reasoning_filter"],
        "after_ticker_filter": counts["after_ticker_filter"],
        "after_dedup_ticker": len({row["ticker"] for row in rows}),
        "selected": len(selected),
        "selected_tickers": [row["ticker"] for row in selected],
        "output": c.rel(c.FINDER_CANDIDATES),
        "kg_batch": c.ROUND9C_BATCH,
        "completed_at": c.utc_now(),
    }
    c.write_json(c.SELECTION_STATE, state)
    print(json.dumps(state, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
