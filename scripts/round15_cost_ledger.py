"""Offline cost ledger: sum usage tokens across every round's trace JSONL and
apply per-model pricing. No API calls. Dedupes by trace_id so duplicate run dirs
do not double-count.

PRICE is assumed standard OpenAI rates (USD per 1M tokens). Adjust to actuals.
Cross-vendor judge cost (R15 P2.5 = $4.80) is recorded separately, not re-derived here.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from collections import defaultdict

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

REPO = Path(__file__).resolve().parents[1]
OUT = REPO / "outputs"
LEDGER_MD = OUT / "round15_vector_rehab" / "cost_ledger.md"
LEDGER_JSON = OUT / "round15_vector_rehab" / "cost_ledger.json"

# USD per 1M tokens (input, output). ADJUST to current rates if different.
PRICE = {
    "gpt-4o-mini": (0.15, 0.60),
    "gpt-4o": (2.50, 10.00),
}
EMBED_NOTE = "embeddings (text-embedding-3-small) not in these traces; ~negligible"


def price_for(model: str):
    m = (model or "").lower()
    if "mini" in m:
        return PRICE["gpt-4o-mini"], "gpt-4o-mini"
    if "gpt-4o" in m or "gpt4o" in m:
        return PRICE["gpt-4o"], "gpt-4o"
    return None, model or "unknown"


def round_key(path: Path) -> str:
    s = str(path).replace("\\", "/").lower()   # full path: only for unambiguous special dirs
    name = path.name.lower()                    # filename: for generic round-number match
    if "naive_baseline/v2" in s:
        return "naive_v2"
    if "naive_baseline" in s:
        return "naive_v1"
    if "round10_rescore_v2" in s:
        return "R10_v2_rescore"
    if "round11_ablation_v2" in s:
        return "R11_v2"
    if "round14b" in s:
        return "R14B"
    if "round15_reeval" in s or "round15_vector_rehab" in s:
        return "R15_reeval"
    # generic: infer from FILENAME, not full path (avoids 'round3_eval_runs' shadowing R4-R14)
    m = re.search(r"round(\d+[a-z]?)", name)
    if m:
        return f"R{m.group(1).upper()}"
    if "locked_test" in name:
        return "R3_locked_test"
    if "dev_dryrun" in name:
        return "R3_dev_dryrun"
    m2 = re.search(r"round(\d+[a-z]?)", path.parent.name.lower())
    if m2:
        return f"R{m2.group(1).upper()}"
    return "other"


def tokens(row: dict):
    """Return (prompt, completion) robustly."""
    u = row.get("usage") if isinstance(row.get("usage"), dict) else {}
    p = row.get("prompt_tokens", u.get("prompt_tokens"))
    c = row.get("completion_tokens", u.get("completion_tokens"))
    if p is None and c is None:
        tot = row.get("total_tokens", u.get("total_tokens"))
        return (0, tot or 0)  # unknown split -> treat as output (conservative)
    return (int(p or 0), int(c or 0))


def main():
    files = sorted(OUT.glob("**/*traces*.jsonl"))
    seen_trace_ids: set[str] = set()
    agg = defaultdict(lambda: {"calls": 0, "prompt": 0, "completion": 0,
                               "cost": 0.0, "models": set(), "unpriced_calls": 0})
    skipped_files = []

    for fp in files:
        rkey = round_key(fp)
        try:
            with fp.open("r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        row = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if not isinstance(row, dict):
                        continue
                    tid = row.get("trace_id")
                    if tid:
                        if tid in seen_trace_ids:
                            continue
                        seen_trace_ids.add(tid)
                    p, c = tokens(row)
                    if p == 0 and c == 0:
                        continue
                    rate, mname = price_for(row.get("model", ""))
                    a = agg[rkey]
                    a["calls"] += 1
                    a["prompt"] += p
                    a["completion"] += c
                    a["models"].add(mname)
                    if rate:
                        a["cost"] += p / 1e6 * rate[0] + c / 1e6 * rate[1]
                    else:
                        a["unpriced_calls"] += 1
        except Exception as e:
            skipped_files.append(f"{fp}: {e}")

    # order rounds roughly chronologically
    order = ["R3_dev_dryrun", "R3_locked_test", "R4", "R5", "R6", "R7", "R8", "R9C",
             "R10", "R10_v2_rescore", "R11", "R11_v2", "naive_v1", "naive_v2",
             "R12", "R13", "R14", "R14B", "R15_reeval"]
    keys = [k for k in order if k in agg] + sorted(k for k in agg if k not in order)

    total_cost = sum(a["cost"] for a in agg.values())
    total_calls = sum(a["calls"] for a in agg.values())
    total_tok = sum(a["prompt"] + a["completion"] for a in agg.values())

    rows_md = ["| Round | Calls | Prompt tok | Completion tok | Model(s) | Est. cost (USD) |",
               "|---|---:|---:|---:|---|---:|"]
    for k in keys:
        a = agg[k]
        models = ",".join(sorted(a["models"]))
        flag = f" ⚠️{a['unpriced_calls']} unpriced" if a["unpriced_calls"] else ""
        rows_md.append(
            f"| {k} | {a['calls']} | {a['prompt']:,} | {a['completion']:,} | {models}{flag} | "
            f"${a['cost']:.4f} |"
        )
    rows_md.append(f"| **TOTAL (OpenAI gen, deduped)** | **{total_calls}** | | | | **${total_cost:.2f}** |")

    md = [
        "# Cost Ledger — OpenAI generation across all rounds (offline reconstruction)",
        "",
        f"Reconstructed by summing `usage` tokens across {len(files)} trace files, deduped by "
        f"`trace_id`. Pricing = assumed standard OpenAI rates (gpt-4o-mini 0.15/0.60, gpt-4o 2.50/10.00 "
        "per 1M in/out). Adjust `PRICE` in `scripts/round15_cost_ledger.py` for actuals.",
        "",
        *rows_md,
        "",
        f"- Total OpenAI **generation** tokens (deduped): {total_tok:,}",
        "- **Cross-vendor judge panel (R15 P2.5)** = **$4.80** (DeepSeek/Kimi/Grok + gpt-4o re-judge; "
        "recorded separately, not in these generation traces).",
        f"- R15 P1 vector index: {EMBED_NOTE}.",
        f"- **Grand total (generation ${total_cost:.2f} + judge panel $4.80) ≈ ${total_cost + 4.80:.2f}**",
        "",
    ]
    if skipped_files:
        md += ["**Unreadable files:**", *[f"- {s}" for s in skipped_files], ""]

    LEDGER_MD.parent.mkdir(parents=True, exist_ok=True)
    LEDGER_MD.write_text("\n".join(md), encoding="utf-8", newline="\n")
    LEDGER_JSON.write_text(json.dumps(
        {k: {**v, "models": sorted(v["models"])} for k, v in agg.items()}
        | {"_total_cost_openai_gen": round(total_cost, 4),
           "_total_calls": total_calls,
           "_judge_panel_usd": 4.80,
           "_grand_total_usd": round(total_cost + 4.80, 2)},
        indent=2, ensure_ascii=False), encoding="utf-8", newline="\n")

    print("\n".join(rows_md))
    print(f"\nGen total ${total_cost:.2f} + judge $4.80 = ${total_cost + 4.80:.2f}")
    print(f"Wrote {LEDGER_MD}")


if __name__ == "__main__":
    main()
