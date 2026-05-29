from __future__ import annotations

import hashlib
import json
import re
from collections import Counter

import round8_common as r8
import round10_common as c
from round9c_formula_contract_gen import infer_formula_type
from ticker_filter import filter_ticker


EXCLUDED_FORMULA_TYPES_PREFLIGHT = {"eps_dilution"}
TARGET = 130
FALLBACK_MIN = 100


def infer_valid_ticker(excluded: set[str], *parts: str) -> str | None:
    text = " ".join(parts)
    candidates = re.findall(r"\(([A-Z]{1,5})\)", text)
    candidates.extend(re.findall(r"\b[A-Z]{2,5}\b", text))
    for raw in candidates:
        ticker = filter_ticker(raw)
        if ticker and ticker not in excluded:
            return ticker
    return None


def main() -> None:
    c.assert_round9c_done()
    raw = r8.read_json(r8.FINDER_FULL)
    excluded = c.excluded_tickers()
    used_source_ids = c.used_finder_source_ids()
    counts = Counter()
    rows = []
    for item in raw:
        row = {
            "source_id": str(item.get("id") or item.get("_id") or ""),
            "category": item.get("category", ""),
            "reasoning_type": r8.normalize_reasoning(item.get("reasoning_type") or item.get("type") or ""),
            "evidence_text": item.get("text") or item.get("references") or "",
            "question": item.get("question") or item.get("text") or "",
            "expected_answer": str(item.get("expected_answer") or item.get("answer") or ""),
        }
        if row["source_id"] in used_source_ids:
            counts["previous_source_excluded"] += 1
            continue
        if row["category"] != "Financials":
            continue
        counts["after_category_filter"] += 1
        if row["reasoning_type"] not in {"Calculation", "Compositional", "Subtraction"}:
            continue
        counts["after_reasoning_filter"] += 1
        if len(row["evidence_text"]) < 200 or len(r8.all_numbers(row["evidence_text"])) <= 2:
            continue
        if r8.parse_number(row["expected_answer"]) is None:
            continue
        formula_type = infer_formula_type(row["question"], row["evidence_text"])
        if formula_type in EXCLUDED_FORMULA_TYPES_PREFLIGHT:
            counts["excluded_formula_type"] += 1
            continue
        ticker = infer_valid_ticker(excluded, row["question"], row["expected_answer"], row["evidence_text"][:400])
        if not ticker:
            continue
        counts["after_ticker_filter"] += 1
        row["ticker"] = ticker
        row["company"] = r8.infer_company(row["evidence_text"], ticker)
        row["years"] = r8.years_in_text(row["evidence_text"] + " " + row["question"])
        row["formula_type_preflight"] = formula_type
        row["quality_score"] = r8.quality_score(row["evidence_text"], row["question"], row["expected_answer"], row["reasoning_type"])
        rows.append(row)
    rows.sort(key=lambda row: (-row["quality_score"], row["ticker"], hashlib.sha256(row["source_id"].encode()).hexdigest()))
    selected = []
    used_tickers = set()
    for row in rows:
        if row["ticker"] in used_tickers:
            continue
        idx = len(selected) + 1
        case_id = f"round10_finder_{idx:03d}_{hashlib.sha256(row['source_id'].encode()).hexdigest()[:8]}"
        selected.append(
            {
                "case_id": case_id,
                "split": "round10_test",
                "source_dataset": "FinDER",
                **row,
                "curation_round": "10",
                "kg_batch": c.ROUND10_BATCH,
                "created_at": c.utc_now(),
                "anti_cherrypick_notes": "Deterministic quality scoring; excludes prior rounds, suspect tickers, and eps_dilution preflight.",
            }
        )
        used_tickers.add(row["ticker"])
        if len(selected) >= TARGET:
            break
    if len(selected) < FALLBACK_MIN:
        raise RuntimeError(f"FinDER selected {len(selected)}, below fallback minimum {FALLBACK_MIN}")
    c.write_jsonl(c.FINDER_CANDIDATES, selected)
    state = {
        "phase": "A_done",
        "round": c.ROUND,
        "finder_target": TARGET,
        "finder_fallback_min": FALLBACK_MIN,
        "finder_selected": len(selected),
        "finder_shortfall": max(0, TARGET - len(selected)),
        "finder_selected_tickers": [row["ticker"] for row in selected],
        "finder_counts": dict(counts),
        "kg_batch": c.ROUND10_BATCH,
        "completed_at": c.utc_now(),
    }
    c.write_json(c.SELECTION_STATE, state)
    print(json.dumps(state, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
