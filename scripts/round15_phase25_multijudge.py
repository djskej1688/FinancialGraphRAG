from __future__ import annotations

import argparse
import csv
import json
import math
import os
import socket
import sys
import time
import urllib.error
import urllib.request
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

SEOCHO = Path(__file__).resolve().parents[1]
R15 = SEOCHO / "outputs" / "round15_vector_rehab"
PANEL = R15 / "05_multijudge"
REEVAL_VEC = R15 / "04_reeval" / "reeval_traces.jsonl"
R14_TRACES = SEOCHO / "outputs" / "round3_eval_runs" / "round14_cross_company_20260530_133644" / "round14_traces.jsonl"
R14_CASES = SEOCHO / "outputs" / "round14_cross_company" / "04_cross_company_queries" / "cross_company_cases.jsonl"
JUDGE_PROMPT = R15 / "03_judge_layer" / "judge_prompt.txt"
STATE = OUT / "state_phase25.json"
ENV_FILE = SEOCHO / ".env"

JUDGES = {
    "openai_gpt4o": {"reuse_existing": True, "model": "gpt-4o"},
    "deepseek": {
        "base_url": "https://api.deepseek.com",
        "model": "deepseek-v4-pro",
        "key_env": "DEEPSEEK_API_KEY",
    },
    "kimi": {
        "base_url": "https://api.moonshot.ai/v1",
        "model": "moonshot-v1-128k",
        "requested_model": "kimi-k2.6",
        "fallback_reason": "kimi-k2.6 was present in /models but failed strict-JSON smoke by running to max_tokens without closing JSON; moonshot-v1-128k passed the same judge prompt.",
        "key_env": "MOONSHOT_API_KEY",
        "temperature": 1,
    },
    "grok": {
        "base_url": "https://api.x.ai/v1",
        "model": "grok-4.3",
        "key_env": "XAI_API_KEY",
    },
}
NEW_JUDGES = ["deepseek", "kimi", "grok"]
METHODS = [
    "vector_single_chunk_v15",
    "vector_multi_by_company_chunk_v15",
    "graph_structured_v14",
    "graph_guided_text_v14",
    "source_text_concat_v14",
]
VECTOR_METHODS = {"vector_single_chunk_v15", "vector_multi_by_company_chunk_v15"}
GRAPH_METHOD = "graph_structured_v14"

PRICE_PER_CALL_ESTIMATE = {
    "deepseek": 0.003,
    "kimi": 0.003,
    "grok": 0.006,
}
SOFT_BUDGET_USD = 5.0
HARD_BUDGET_USD = 10.0


def now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def load_env() -> None:
    if not ENV_FILE.exists():
        return
    for line in ENV_FILE.read_text(encoding="utf-8", errors="ignore").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows = []
    if not path.exists():
        return rows
    with path.open("r", encoding="utf-8", errors="ignore") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return rows


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")


def write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text.rstrip() + "\n", encoding="utf-8", newline="\n")


def write_csv(path: Path, rows: list[dict[str, Any]], fields: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


def update_state(patch: dict[str, Any]) -> None:
    state = {}
    if STATE.exists():
        try:
            state = json.loads(STATE.read_text(encoding="utf-8"))
        except Exception:
            state = {}
    state.update(patch)
    state["updated_at"] = now_iso()
    write_json(STATE, state)


def rel(path: Path) -> str:
    try:
        return str(path.relative_to(SEOCHO)).replace("\\", "/")
    except ValueError:
        return str(path)


def candidate_text(row: dict[str, Any]) -> str:
    return "\n".join([str(row.get("final_answer", "")), str(row.get("calculation", ""))]).strip()


def gold_for_case(case: dict[str, Any]) -> dict[str, Any]:
    slots = case.get("scorer_only_target_slot_contract", {}).get("target_slots", [])
    values = {
        str(slot.get("target_slot_name", "")): float(slot["expected_value"])
        for slot in slots
        if slot.get("expected_value") is not None
    }
    gold_text = (
        f"{case.get('company_a')} {values.get('company_a_value', '')}; "
        f"{case.get('company_b')} {values.get('company_b_value', '')}; "
        f"winner {case.get('winner', '')}; difference {values.get('difference', '')}"
    )
    return {"text": gold_text, "slot_values": values}


def load_candidates() -> tuple[list[dict[str, Any]], dict[str, dict[str, Any]]]:
    traces = read_jsonl(REEVAL_VEC)
    cases = {row["case_id"]: row for row in read_jsonl(R14_CASES)}
    rows = []
    for row in traces:
        if row.get("method") in METHODS and row.get("case_id") in cases:
            rows.append(row)
    expected = 80 * len(METHODS)
    if len(rows) != expected:
        raise RuntimeError(f"expected {expected} candidate rows, got {len(rows)}")
    return rows, cases


def judge_prompt_text() -> str:
    if JUDGE_PROMPT.exists():
        return JUDGE_PROMPT.read_text(encoding="utf-8")
    return (
        'Return ONLY strict JSON with exactly: '
        '{"verdict":"correct|partial|incorrect","score":1.0|0.5|0.0,'
        '"matched":["..."],"missing_or_wrong":["..."],"rationale":"..."}'
    )


def judge_messages(case: dict[str, Any], row: dict[str, Any]) -> list[dict[str, str]]:
    return [
        {"role": "system", "content": judge_prompt_text()},
        {
            "role": "user",
            "content": (
                f"QUESTION:\n{case['question']}\n\n"
                f"GOLD_ANSWER:\n{gold_for_case(case)['text']}\n\n"
                f"CANDIDATE_METHOD:\n{row.get('method')}\n\n"
                f"CANDIDATE_ANSWER:\n{candidate_text(row)}\n"
            ),
        },
    ]


def endpoint(base_url: str) -> str:
    base = base_url.rstrip("/")
    if base.endswith("/v1"):
        return f"{base}/chat/completions"
    return f"{base}/chat/completions"


def parse_result(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, dict):
        return {"verdict": "parse_fail", "score": None, "matched": [], "missing_or_wrong": ["not_json"], "rationale": ""}
    verdict = raw.get("verdict")
    score = raw.get("score")
    if verdict not in {"correct", "partial", "incorrect"}:
        return {"verdict": "parse_fail", "score": None, "matched": [], "missing_or_wrong": ["bad_verdict"], "rationale": str(raw)[:500]}
    try:
        fscore = float(score)
    except Exception:
        return {"verdict": "parse_fail", "score": None, "matched": [], "missing_or_wrong": ["bad_score"], "rationale": str(raw)[:500]}
    if fscore not in {0.0, 0.5, 1.0}:
        return {"verdict": "parse_fail", "score": None, "matched": [], "missing_or_wrong": ["bad_score"], "rationale": str(raw)[:500]}
    return {
        "verdict": verdict,
        "score": fscore,
        "matched": raw.get("matched", []) if isinstance(raw.get("matched", []), list) else [],
        "missing_or_wrong": raw.get("missing_or_wrong", []) if isinstance(raw.get("missing_or_wrong", []), list) else [],
        "rationale": str(raw.get("rationale", ""))[:700],
    }


def extract_json_object(text: str) -> dict[str, Any]:
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        start = text.find("{")
        end = text.rfind("}")
        if start >= 0 and end > start:
            return json.loads(text[start:end + 1])
        raise


def call_compatible_judge(judge: str, case: dict[str, Any], row: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any], str]:
    cfg = JUDGES[judge]
    key = os.environ.get(str(cfg["key_env"]))
    if not key:
        raise RuntimeError(f"{cfg['key_env']} absent")
    messages = judge_messages(case, row)
    payload = {
        "model": cfg["model"],
        "messages": messages,
        "temperature": cfg.get("temperature", 0),
        "max_tokens": 1500,
        "response_format": {"type": "json_object"},
    }
    last_error = ""
    for outer_attempt, use_response_format in enumerate([True, False], start=1):
        body = dict(payload)
        if not use_response_format:
            body.pop("response_format", None)
        req = urllib.request.Request(
            endpoint(str(cfg["base_url"])),
            data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
            headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
            method="POST",
        )
        for attempt in range(1, 4):
            try:
                with urllib.request.urlopen(req, timeout=180) as resp:
                    data = json.loads(resp.read().decode("utf-8"))
                choice = data["choices"][0]
                message = choice.get("message", {})
                content = message.get("content") or message.get("reasoning_content") or ""
                if isinstance(content, list):
                    content = "".join(str(part.get("text", part)) if isinstance(part, dict) else str(part) for part in content)
                if not str(content).strip():
                    raise ValueError(f"empty content, finish_reason={choice.get('finish_reason')}")
                parsed = parse_result(extract_json_object(content))
                return parsed, data.get("usage", {}), data, ""
            except urllib.error.HTTPError as exc:
                last_error = f"HTTP {exc.code}: {exc.read().decode('utf-8', errors='ignore')[:300]}"
                if "response_format" in last_error and use_response_format:
                    break
                if exc.code in {408, 409, 429, 500, 502, 503, 504} and attempt < 3:
                    time.sleep(2 * attempt)
                    continue
                break
            except (urllib.error.URLError, socket.timeout, TimeoutError, json.JSONDecodeError, KeyError, ValueError, Exception) as exc:
                last_error = str(exc)[:300]
                if attempt < 3:
                    time.sleep(2 * attempt)
                    continue
                break
    return {"verdict": "parse_fail", "score": None, "matched": [], "missing_or_wrong": ["call_failed"], "rationale": ""}, {}, {}, last_error


def openai_reuse_rows(candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out = []
    for row in candidates:
        out.append(
            {
                "case_id": row["case_id"],
                "method": row["method"],
                "judge": "openai_gpt4o",
                "judge_model": row.get("judge_model", "gpt-4o"),
                "verdict": row.get("judge_verdict"),
                "score": row.get("judge_score"),
                "matched": row.get("judge_matched", []),
                "missing_or_wrong": row.get("judge_missing_or_wrong", []),
                "rationale": row.get("judge_rationale", ""),
                "reuse_existing": True,
                "error": "",
                "usage": row.get("judge_usage", {}),
                "generation_call": False,
                "neo4j_write_performed": False,
            }
        )
    return out


def active_judges() -> list[str]:
    judges = ["openai_gpt4o"]
    for name in NEW_JUDGES:
        if os.environ.get(str(JUDGES[name]["key_env"])):
            judges.append(name)
    return judges


def cost_estimate(rows: list[dict[str, Any]]) -> float:
    cost = 0.0
    for row in rows:
        judge = row.get("judge")
        if judge in PRICE_PER_CALL_ESTIMATE and not row.get("reuse_existing"):
            cost += PRICE_PER_CALL_ESTIMATE[judge]
    return round(cost, 4)


def run_judges(candidates: list[dict[str, Any]], cases: dict[str, dict[str, Any]], smoke: bool) -> list[dict[str, Any]]:
    PANEL.mkdir(parents=True, exist_ok=True)
    score_path = PANEL / ("smoke_multijudge_scores.jsonl" if smoke else "multijudge_scores.jsonl")
    rows = read_jsonl(score_path)
    done = {
        (r.get("case_id"), r.get("method"), r.get("judge"))
        for r in rows
        if r.get("verdict") in {"correct", "partial", "incorrect"}
    }
    if not rows:
        reuse = openai_reuse_rows(candidates)
        if smoke:
            smoke_keys = {(r["case_id"], r["method"]) for r in candidates}
            reuse = [r for r in reuse if (r["case_id"], r["method"]) in smoke_keys]
        rows.extend(reuse)
        write_jsonl(score_path, rows)
        done = {
            (r.get("case_id"), r.get("method"), r.get("judge"))
            for r in rows
            if r.get("verdict") in {"correct", "partial", "incorrect"}
        }

    for judge in NEW_JUDGES:
        if not os.environ.get(str(JUDGES[judge]["key_env"])):
            continue
        subset = candidates[:3] if smoke else candidates
        for idx, row in enumerate(subset, start=1):
            key = (row["case_id"], row["method"], judge)
            if key in done:
                continue
            print(json.dumps({"phase": "multijudge", "judge": judge, "done": len(rows), "case_id": row["case_id"], "method": row["method"]}, ensure_ascii=False), flush=True)
            parsed, usage, raw, error = call_compatible_judge(judge, cases[row["case_id"]], row)
            out = {
                "case_id": row["case_id"],
                "method": row["method"],
                "judge": judge,
                "judge_model": JUDGES[judge]["model"],
                "verdict": parsed["verdict"],
                "score": parsed["score"],
                "matched": parsed.get("matched", []),
                "missing_or_wrong": parsed.get("missing_or_wrong", []),
                "rationale": parsed.get("rationale", ""),
                "reuse_existing": False,
                "error": error,
                "usage": usage,
                "raw": raw if error else None,
                "generation_call": False,
                "neo4j_write_performed": False,
            }
            rows.append(out)
            write_jsonl(score_path, rows)
            done.add(key)
            if cost_estimate(rows) > HARD_BUDGET_USD:
                raise SystemExit("Hard budget exceeded")
    return rows


def verdict_code(verdict: Any) -> int | None:
    return {"incorrect": 0, "partial": 1, "correct": 2}.get(str(verdict))


def cohen_kappa(a: list[int], b: list[int], labels: list[int] = [0, 1, 2]) -> float | None:
    if len(a) != len(b) or not a:
        return None
    n = len(a)
    po = sum(1 for x, y in zip(a, b) if x == y) / n
    pa = Counter(a)
    pb = Counter(b)
    pe = sum((pa[l] / n) * (pb[l] / n) for l in labels)
    if abs(1 - pe) < 1e-12:
        return None
    return round((po - pe) / (1 - pe), 4)


def fleiss_kappa(matrix: list[list[int]], labels: list[int] = [0, 1, 2]) -> float | None:
    if not matrix:
        return None
    n_items = len(matrix)
    n_raters = len(matrix[0])
    if n_raters < 2 or any(len(row) != n_raters for row in matrix):
        return None
    p_j = {}
    for label in labels:
        p_j[label] = sum(row.count(label) for row in matrix) / (n_items * n_raters)
    p_i = []
    for row in matrix:
        counts = Counter(row)
        p_i.append((sum(counts[label] ** 2 for label in labels) - n_raters) / (n_raters * (n_raters - 1)))
    p_bar = sum(p_i) / n_items
    p_e = sum(v * v for v in p_j.values())
    if abs(1 - p_e) < 1e-12:
        return None
    return round((p_bar - p_e) / (1 - p_e), 4)


def analyze(rows: list[dict[str, Any]], active: list[str]) -> dict[str, Any]:
    valid = [r for r in rows if r.get("judge") in active and verdict_code(r.get("verdict")) is not None]
    score_rows = []
    for (method, judge), vals in sorted(group_by(valid, "method", "judge").items()):
        scores = [float(v["score"]) for v in vals if isinstance(v.get("score"), (int, float))]
        dist = Counter(v.get("verdict") for v in vals)
        score_rows.append(
            {
                "method": method,
                "judge": judge,
                "n": len(vals),
                "mean_score": round(sum(scores) / len(scores), 4) if scores else "",
                "correct": dist.get("correct", 0),
                "partial": dist.get("partial", 0),
                "incorrect": dist.get("incorrect", 0),
                "parse_fail": dist.get("parse_fail", 0),
            }
        )

    by_item: dict[tuple[str, str], dict[str, int]] = defaultdict(dict)
    for row in valid:
        by_item[(row["case_id"], row["method"])][row["judge"]] = verdict_code(row["verdict"])  # type: ignore[assignment]
    complete_items = {k: v for k, v in by_item.items() if all(j in v for j in active)}
    pairwise = []
    for i, j1 in enumerate(active):
        for j2 in active[i + 1:]:
            pairs = [(v[j1], v[j2]) for v in complete_items.values() if j1 in v and j2 in v]
            agreement = round(sum(1 for x, y in pairs if x == y) / len(pairs), 4) if pairs else None
            kappa = cohen_kappa([x for x, _ in pairs], [y for _, y in pairs]) if pairs else None
            pairwise.append({"judge_a": j1, "judge_b": j2, "n": len(pairs), "agreement": agreement, "cohen_kappa": kappa})
    matrix = [[v[j] for j in active] for v in complete_items.values()]
    fleiss = fleiss_kappa(matrix)
    full_agreement = round(sum(1 for row in matrix if len(set(row)) == 1) / len(matrix), 4) if matrix else None

    method_scores = {(r["method"], r["judge"]): r["mean_score"] for r in score_rows}
    margins = []
    graph_gt_all_vectors_all_judges = True
    for judge in active:
        g = method_scores.get((GRAPH_METHOD, judge))
        vec_scores = [method_scores.get((m, judge)) for m in VECTOR_METHODS]
        vec_scores_num = [float(v) for v in vec_scores if isinstance(v, (int, float))]
        if not isinstance(g, (int, float)) or not vec_scores_num:
            graph_gt_all_vectors_all_judges = False
            continue
        best_vec = max(vec_scores_num)
        margins.append({"judge": judge, "graph_structured": float(g), "best_vector": best_vec, "margin": round(float(g) - best_vec, 4)})
        if not float(g) > best_vec:
            graph_gt_all_vectors_all_judges = False

    return {
        "score_rows": score_rows,
        "pairwise": pairwise,
        "fleiss_kappa": fleiss,
        "full_agreement": full_agreement,
        "complete_items": len(complete_items),
        "margins": margins,
        "graph_gt_all_vectors_all_judges": graph_gt_all_vectors_all_judges,
        "parse_fail_by_judge": dict(Counter(r["judge"] for r in rows if r.get("verdict") == "parse_fail")),
    }


def group_by(rows: list[dict[str, Any]], *keys: str) -> dict[tuple[Any, ...], list[dict[str, Any]]]:
    out: dict[tuple[Any, ...], list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        out[tuple(row.get(k) for k in keys)].append(row)
    return out


def write_reports(rows: list[dict[str, Any]], active: list[str], analysis: dict[str, Any]) -> None:
    write_csv(
        PANEL / "panel_method_scores.csv",
        analysis["score_rows"],
        ["method", "judge", "n", "mean_score", "correct", "partial", "incorrect", "parse_fail"],
    )
    lines = [
        "# Round 15 Phase 2.5 - Inter-Judge Agreement",
        "",
        f"Active judges: {', '.join(active)}",
        f"Complete judged items: {analysis['complete_items']}",
        f"Fleiss' kappa: `{analysis['fleiss_kappa']}`",
        f"Full agreement: `{analysis['full_agreement']}`",
        "",
        "## Pairwise Cohen's Kappa",
        "",
        "| judge A | judge B | n | agreement | Cohen kappa |",
        "|---|---|---:|---:|---:|",
    ]
    for row in analysis["pairwise"]:
        lines.append(f"| {row['judge_a']} | {row['judge_b']} | {row['n']} | {row['agreement']} | {row['cohen_kappa']} |")
    write_text(PANEL / "kappa_agreement.md", "\n".join(lines))

    rob = [
        "# Round 15 Phase 2.5 - Judge Robustness Verdict",
        "",
        f"Graph structured > all vector arms under ALL active judges: **{analysis['graph_gt_all_vectors_all_judges']}**",
        "",
        "## Judge Margins",
        "",
        "| judge | graph_structured | best_vector | margin |",
        "|---|---:|---:|---:|",
    ]
    for row in analysis["margins"]:
        rob.append(f"| {row['judge']} | {row['graph_structured']:.4f} | {row['best_vector']:.4f} | {row['margin']:.4f} |")
    rob += [
        "",
        "## Parse Failures",
        "",
        json.dumps(analysis["parse_fail_by_judge"], ensure_ascii=False, indent=2, sort_keys=True),
    ]
    write_text(PANEL / "judge_robustness_verdict.md", "\n".join(rob))

    examples = disagreement_examples(rows, active)
    write_text(PANEL / "disagreement_examples.md", examples)


def disagreement_examples(rows: list[dict[str, Any]], active: list[str], limit: int = 20) -> str:
    by_item: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        if row.get("judge") in active:
            by_item[(row.get("case_id", ""), row.get("method", ""))].append(row)
    lines = ["# Round 15 Phase 2.5 - Disagreement Examples", ""]
    shown = 0
    for (case_id, method), vals in sorted(by_item.items()):
        valids = [v for v in vals if v.get("verdict") in {"correct", "partial", "incorrect"}]
        if len(valids) < len(active):
            continue
        if len({v.get("verdict") for v in valids}) <= 1:
            continue
        lines += [f"## {case_id} / {method}", ""]
        for v in sorted(valids, key=lambda x: str(x.get("judge"))):
            lines.append(f"- `{v.get('judge')}`: `{v.get('verdict')}` score={v.get('score')} — {v.get('rationale','')}")
        lines.append("")
        shown += 1
        if shown >= limit:
            break
    if shown == 0:
        lines.append("No complete-item disagreements found.")
    return "\n".join(lines)


def panel_config(active: list[str]) -> dict[str, Any]:
    return {
        "active_judges": active,
        "judges": {name: {k: ("SET" if k == "key_env" and os.environ.get(str(v)) else v) for k, v in cfg.items() if k != "reuse_existing"} for name, cfg in JUDGES.items()},
        "env_file_loaded": str(ENV_FILE),
        "generation_calls": 0,
        "neo4j_write_performed": False,
        "existing_outputs_overwritten": False,
        "soft_budget_usd": SOFT_BUDGET_USD,
        "hard_budget_usd": HARD_BUDGET_USD,
    }


def run(smoke_only: bool = False, full_only: bool = False) -> None:
    load_env()
    PANEL.mkdir(parents=True, exist_ok=True)
    candidates, cases = load_candidates()
    active = active_judges()
    config = panel_config(active)
    write_json(PANEL / "panel_config.json", config)
    print(json.dumps({"active_judges": active, "env_keys": {j: bool(os.environ.get(str(JUDGES[j].get("key_env", "")))) for j in NEW_JUDGES}}, ensure_ascii=False, indent=2), flush=True)
    update_state({"phase": "phase25_start", "active_judges": active, "config": config})

    if not full_only:
        smoke_candidates = candidates[:3]
        smoke_rows = run_judges(smoke_candidates, cases, smoke=True)
        smoke_active = [j for j in active if j != "openai_gpt4o"]
        smoke_ok = {}
        expected_keys = {(row["case_id"], row["method"]) for row in smoke_candidates}
        for judge in smoke_active:
            success_keys = {
                (r.get("case_id"), r.get("method"))
                for r in smoke_rows
                if r.get("judge") == judge and r.get("verdict") in {"correct", "partial", "incorrect"}
            }
            smoke_ok[judge] = expected_keys <= success_keys
        write_json(PANEL / "smoke_report.json", {"smoke_parse_success": smoke_ok, "rows": len(smoke_rows)})
        print(json.dumps({"smoke_parse_success": smoke_ok}, ensure_ascii=False), flush=True)
        update_state({"phase": "smoke_done", "smoke_parse_success": smoke_ok})
        if not all(smoke_ok.values()):
            raise SystemExit("Smoke failed for one or more judges")
        if smoke_only:
            return

    rows = run_judges(candidates, cases, smoke=False)
    analysis = analyze(rows, active)
    write_reports(rows, active, analysis)
    state = {
        "phase": "done",
        "active_judges": active,
        "smoke_parse_success": {j: True for j in active if j != "openai_gpt4o"},
        "fleiss_kappa": analysis["fleiss_kappa"],
        "pairwise_kappa": analysis["pairwise"],
        "full_agreement": analysis["full_agreement"],
        "graph_structured_gt_all_vector_under_all_judges": analysis["graph_gt_all_vectors_all_judges"],
        "judge_margins": analysis["margins"],
        "parse_fail_by_judge": analysis["parse_fail_by_judge"],
        "generation_calls": 0,
        "neo4j_write_performed": False,
        "existing_outputs_overwritten": False,
        "estimated_total_cost_usd": cost_estimate(rows),
        "generated_files": [
            rel(PANEL / "multijudge_scores.jsonl"),
            rel(PANEL / "panel_method_scores.csv"),
            rel(PANEL / "kappa_agreement.md"),
            rel(PANEL / "judge_robustness_verdict.md"),
            rel(PANEL / "disagreement_examples.md"),
            rel(PANEL / "panel_config.json"),
        ],
    }
    update_state(state)
    print(json.dumps({
        "active_judges": active,
        "smoke_parse_success": state["smoke_parse_success"],
        "fleiss_kappa": state["fleiss_kappa"],
        "pairwise_kappa_range": kappa_range(analysis["pairwise"]),
        "graph_structured_gt_all_vector_under_all_judges": state["graph_structured_gt_all_vector_under_all_judges"],
        "judge_margins": state["judge_margins"],
        "parse_fail_by_judge": state["parse_fail_by_judge"],
        "generation_calls": 0,
        "neo4j_write": False,
        "existing_overwritten": False,
        "total_cost": state["estimated_total_cost_usd"],
    }, ensure_ascii=False, indent=2, sort_keys=True), flush=True)


def kappa_range(pairwise: list[dict[str, Any]]) -> dict[str, Any]:
    vals = [row["cohen_kappa"] for row in pairwise if isinstance(row.get("cohen_kappa"), (int, float))]
    if not vals:
        return {"min": None, "max": None}
    return {"min": min(vals), "max": max(vals)}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--smoke-only", action="store_true")
    parser.add_argument("--full-only", action="store_true")
    args = parser.parse_args()
    run(smoke_only=args.smoke_only, full_only=args.full_only)


if __name__ == "__main__":
    main()
