from __future__ import annotations

import hashlib
import json
import re
from collections import Counter
from typing import Any

import round8_common as r8
import round10_common as c
from ticker_filter import filter_ticker


TARGET = 70
FALLBACK_MIN = 20
SCALES = {"", "percent", "million", "billion", "thousand"}
COMPANY_RE = re.compile(
    r"\b([A-Z][A-Za-z0-9&,' -]{2,70}?\s(?:Corporation|Corp\.?|Incorporated|Inc\.?|Ltd\.?|Limited|Holdings|Company|Co\.?|PLC|plc|AG|SE|SA|NV))\b"
)
GENERIC_NAMES = {"The Company", "The Group", "Company", "Group"}


def extract_company_name(doc: dict[str, Any]) -> str | None:
    texts = [str(p.get("text", "")) for p in doc.get("paragraphs", [])[:8]]
    for text in texts:
        for sentence in re.split(r"(?<=[.;])\s+", text.replace("\n", " ")):
            match = COMPANY_RE.search(sentence)
            if not match:
                continue
            name = re.sub(r"\s+", " ", match.group(1)).strip(" .,")
            if name in GENERIC_NAMES or name.startswith(("The ", "This ")):
                continue
            return name
    return None


def build_tatqa_evidence(doc: dict[str, Any]) -> str:
    table = doc.get("table", {}).get("table", [])
    table_text = "\n".join("\t".join(str(cell) for cell in row) for row in table)
    para_text = "\n".join(str(p.get("text", "")) for p in doc.get("paragraphs", []))
    return f"TABLE\n{table_text}\n\nCONTEXT\n{para_text}"


def parse_answer(value: Any) -> float | None:
    if isinstance(value, list):
        if len(value) != 1:
            return None
        value = value[0]
    return r8.parse_number(str(value))


def map_company(company: str, cache: dict[str, str]) -> str:
    if company in cache:
        return cache[company]
    prompt = f"""Return JSON only.
Given a company name from a financial report, return the stock ticker symbol.
Return {{"ticker": "AAPL"}} or {{"ticker": "UNKNOWN"}} if not identifiable.
Do not guess. Only return tickers you are confident about.

Company name: {company}
"""
    try:
        raw, _usage = r8.call_openai_json(prompt, temperature=0.0)
        ticker = str(raw.get("ticker") or "UNKNOWN").upper().strip()
    except Exception:
        ticker = "UNKNOWN"
    ticker = filter_ticker(ticker) or "UNKNOWN"
    cache[company] = ticker
    c.write_json(c.TATQA_TICKER_MAP, cache)
    return ticker


def quality_score(evidence: str, question: str, derivation: str) -> float:
    return round(min(6.0, len(r8.all_numbers(evidence)) / 5) + min(3.0, len(derivation) / 20) + min(3.0, len(question) / 40), 4)


def main() -> None:
    c.assert_round9c_done()
    if not c.TATQA_TRAIN.exists():
        c.write_jsonl(c.TATQA_CANDIDATES, [])
        state = c.read_json(c.SELECTION_STATE)
        state.update({"phase": "C_done", "tatqa_available": 0, "tatqa_selected": 0, "tatqa_shortfall": TARGET, "tatqa_warning": "dataset_missing"})
        c.write_json(c.SELECTION_STATE, state)
        print(json.dumps(state, ensure_ascii=False, indent=2))
        return
    excluded = c.excluded_tickers()
    cache = c.read_json(c.TATQA_TICKER_MAP) if c.TATQA_TICKER_MAP.exists() else {}
    data = c.read_json(c.TATQA_TRAIN)
    counts = Counter()
    candidates = []
    for doc in data:
        company = extract_company_name(doc)
        if not company:
            counts["no_company"] += 1
            continue
        evidence = build_tatqa_evidence(doc)
        if len(evidence) < 200 or len(r8.all_numbers(evidence)) <= 2:
            continue
        for q in doc.get("questions", []):
            counts["questions_seen"] += 1
            if q.get("answer_type") != "arithmetic":
                continue
            if q.get("answer_from") not in {"table", "table-text"}:
                continue
            if str(q.get("scale") or "") not in SCALES:
                continue
            derivation = str(q.get("derivation") or "").strip()
            answer_num = parse_answer(q.get("answer"))
            if not derivation or answer_num is None:
                continue
            counts["eligible_pre_ticker"] += 1
            ticker = map_company(company, cache)
            if ticker == "UNKNOWN":
                counts["ticker_unknown"] += 1
                continue
            if ticker in excluded:
                counts["ticker_excluded"] += 1
                continue
            candidates.append(
                {
                    "source_uid": q["uid"],
                    "source_doc_uid": doc.get("table", {}).get("uid", ""),
                    "ticker": ticker,
                    "company": company,
                    "answer_type": q.get("answer_type"),
                    "answer_from": q.get("answer_from"),
                    "derivation": derivation,
                    "scale": str(q.get("scale") or ""),
                    "evidence_text": evidence,
                    "question": q.get("question", ""),
                    "expected_answer": str(q.get("answer")),
                    "expected_answer_numeric": answer_num,
                    "years": r8.years_in_text(evidence + " " + str(q.get("question", ""))),
                    "quality_score": quality_score(evidence, str(q.get("question", "")), derivation),
                }
            )
    candidates.sort(key=lambda row: (-row["quality_score"], row["ticker"], hashlib.sha256(row["source_uid"].encode()).hexdigest()))
    selected = []
    seen_uid = set()
    per_ticker = Counter()
    for row in candidates:
        if row["source_uid"] in seen_uid or per_ticker[row["ticker"]] >= 3:
            continue
        idx = len(selected) + 1
        selected.append(
            {
                "case_id": f"round10_tatqa_{idx:03d}_{hashlib.sha256(row['source_uid'].encode()).hexdigest()[:8]}",
                "split": "round10_test",
                "source_dataset": "TAT-QA",
                **row,
                "curation_round": "10",
                "kg_batch": c.ROUND10_BATCH,
                "created_at": c.utc_now(),
                "anti_cherrypick_notes": "Selected by deterministic scoring; arithmetic answer_type only; ticker mapped from report company name.",
            }
        )
        seen_uid.add(row["source_uid"])
        per_ticker[row["ticker"]] += 1
        if len(selected) >= TARGET:
            break
    if len(selected) < FALLBACK_MIN:
        selected = []
        counts["below_fallback_min_excluded"] = 1
    c.write_jsonl(c.TATQA_CANDIDATES, selected)
    state = c.read_json(c.SELECTION_STATE)
    unknown_rate = round(counts["ticker_unknown"] / counts["eligible_pre_ticker"], 4) if counts["eligible_pre_ticker"] else 0.0
    state.update(
        {
            "phase": "C_done",
            "tatqa_target": TARGET,
            "tatqa_fallback_min": FALLBACK_MIN,
            "tatqa_available": len(candidates),
            "tatqa_selected": len(selected),
            "tatqa_shortfall": max(0, TARGET - len(selected)),
            "tatqa_ticker_extraction_rate": round(1.0 - unknown_rate, 4) if counts["eligible_pre_ticker"] else 0.0,
            "tatqa_unknown_rate": unknown_rate,
            "tatqa_counts": dict(counts),
            "cases_total_actual": state.get("finder_selected", 0) + state.get("finqa_selected", 0) + len(selected),
            "completed_at": c.utc_now(),
        }
    )
    c.write_json(c.SELECTION_STATE, state)
    print(json.dumps(state, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
