"""Step A: Semantic Fact Retrieval Layer.

Pre-compute embeddings for all targeted KG facts, then run 50 evaluations
(25 cases × 2 new methods: graph_neo4j_v6_semfact, hybrid_neo4j_v6_semfact).
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import math
import sys
import time
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
ROUND6_PATH = ROOT / "scripts" / "round6_eval.py"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

spec = importlib.util.spec_from_file_location("round6_eval", ROUND6_PATH)
r6 = importlib.util.module_from_spec(spec)
assert spec and spec.loader
spec.loader.exec_module(r6)

r5 = r6.r5
r4 = r5.r4

EMBEDDING_MODEL = "text-embedding-3-small"
TOP_K = 8
KG_BATCH = "kg-targeted-ie-v1-20260528"
MODEL = "gpt-4o-mini"
ROUND = "round6"
TRACK = "track_b_neo4j_llm_ie"
NEW_METHODS = ["graph_neo4j_v6_semfact", "hybrid_neo4j_v6_semfact"]
CLAIM_BOUNDARY = "method_comparison_valid_absolute_scores_diagnostic"
TEST_FORMULA_SOURCE = "post_hoc"

OUT_ROOT = ROOT / "outputs" / "round3_eval_runs"
SEMFACT_OUT = ROOT / "outputs" / "step_a_semantic_retrieval"
STATE_PATH = SEMFACT_OUT / "state.json"
EMBEDDINGS_PATH = SEMFACT_OUT / "fact_embeddings.jsonl"

# R6 basic results for comparison in summary
R6_BASIC_RUN = ROOT / "outputs" / "round3_eval_runs" / "round6_eval_20260528_233753"


# ---------------------------------------------------------------------------
# Utilities
# ---------------------------------------------------------------------------

def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def ts() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S")


def rel(path: Path) -> str:
    return path.resolve().relative_to(ROOT.resolve()).as_posix()


def avg(values: list[Any]) -> float:
    nums = [float(v) for v in values]
    return round(sum(nums) / len(nums), 4) if nums else 0.0


# ---------------------------------------------------------------------------
# Embedding helpers
# ---------------------------------------------------------------------------

def build_fact_embedding_text(fact: dict) -> str:
    metric = fact.get("metric_canonical", "")
    year = fact.get("year", "")
    value = fact.get("value", "")
    unit = fact.get("unit", "") or ""
    quote = fact.get("evidence_quote", "") or ""
    ticker = fact.get("ticker", "")
    text = f"{ticker} {metric} {year}: {value} {unit}"
    if quote:
        text += f" | {quote[:80]}"
    return text


def embed_texts_batch(texts: list[str], client: Any) -> list[list[float]]:
    embeddings = []
    batch_size = 100
    for i in range(0, len(texts), batch_size):
        batch = texts[i : i + batch_size]
        resp = client.embeddings.create(model=EMBEDDING_MODEL, input=batch)
        embeddings.extend([e.embedding for e in resp.data])
    return embeddings


def cosine_similarity(a: list[float], b: list[float]) -> float:
    av, bv = np.array(a), np.array(b)
    return float(np.dot(av, bv) / (np.linalg.norm(av) * np.linalg.norm(bv) + 1e-9))


# ---------------------------------------------------------------------------
# Pre-compute fact embeddings
# ---------------------------------------------------------------------------

def fetch_all_targeted_facts(driver: Any) -> list[dict]:
    database = r4.neo4j_env()["NEO4J_DATABASE"]
    with driver.session(database=database) as s:
        records = s.run(
            """
MATCH (obs:LLMObservation)-[:LLM_MENTIONS_COMPANY]->(c:LLMCompany),
      (obs)-[:LLM_OBSERVES_METRIC]->(m:LLMFinancialMetric),
      (obs)-[:LLM_OBSERVED_IN_YEAR]->(yr:LLMFiscalYear)
WHERE obs.kg_batch = $batch
RETURN obs.obs_id AS obs_id,
       obs.value AS value,
       obs.unit AS unit,
       obs.evidence_quote AS evidence_quote,
       obs.case_id AS case_id,
       obs.validation_status AS validation_status,
       m.canonical_name AS metric_canonical,
       yr.year AS year,
       c.ticker AS ticker
ORDER BY c.ticker, yr.year, m.canonical_name
""",
            batch=KG_BATCH,
        )
        return [dict(rec) for rec in records]


def build_fact_embeddings(client: Any, driver: Any) -> list[dict]:
    """Fetch facts + embed. Returns cache list ready for JSONL."""
    SEMFACT_OUT.mkdir(parents=True, exist_ok=True)
    all_facts = fetch_all_targeted_facts(driver)
    print(f"Fetched {len(all_facts)} facts from targeted KG")
    texts = [build_fact_embedding_text(f) for f in all_facts]
    embeddings = embed_texts_batch(texts, client)
    cache = []
    for fact, emb in zip(all_facts, embeddings):
        cache.append(
            {
                "obs_id": fact["obs_id"],
                "metric_canonical": fact["metric_canonical"],
                "year": fact["year"],
                "value": fact["value"],
                "unit": fact.get("unit") or "",
                "ticker": fact["ticker"],
                "case_id": fact.get("case_id") or "",
                "validation_status": fact.get("validation_status") or "",
                "evidence_quote": fact.get("evidence_quote") or "",
                "embedding_text": build_fact_embedding_text(fact),
                "embedding": emb,
            }
        )
    r5.write_jsonl(EMBEDDINGS_PATH, [
        {k: v for k, v in row.items() if k != "embedding"} | {"embedding": row["embedding"]}
        for row in cache
    ])
    (SEMFACT_OUT / "embedding_model.txt").write_text(EMBEDDING_MODEL, encoding="utf-8")
    print(f"Saved {len(cache)} fact embeddings → {rel(EMBEDDINGS_PATH)}")
    return cache


def load_fact_embeddings() -> list[dict]:
    if not EMBEDDINGS_PATH.exists():
        return []
    return [json.loads(line) for line in EMBEDDINGS_PATH.read_text(encoding="utf-8").splitlines() if line.strip()]


# ---------------------------------------------------------------------------
# Semantic retrieval
# ---------------------------------------------------------------------------

def load_neo4j_graph_facts_semantic(
    ticker: str,
    case_id: str,
    years: list[int],
    question: str,
    client: Any,
    fact_embeddings: list[dict],
    top_k: int = TOP_K,
) -> tuple[list[dict], bool, int]:
    """Returns (facts, semantic_applied, n_candidates)."""
    # Case prefix for matching (e.g., "round3_test_016" from "round3_test_016_707dc83f")
    parts = case_id.split("_")
    case_prefix = "_".join(parts[:4]) if len(parts) >= 4 else case_id  # e.g. "round3_test_016_707dc83f"

    # Filter by ticker + year; prefer case-specific facts
    candidate_facts = [
        f for f in fact_embeddings
        if f["ticker"] == ticker and int(f["year"]) in [int(y) for y in years]
        and (f.get("case_id", "") == case_id or f.get("case_id", "").startswith(case_prefix[:18]) or f.get("case_id", "") == "")
    ]

    if not candidate_facts:
        # Fallback: any fact for this ticker + year
        candidate_facts = [
            f for f in fact_embeddings
            if f["ticker"] == ticker and int(f["year"]) in [int(y) for y in years]
        ]

    n_candidates = len(candidate_facts)
    semantic_applied = False

    if n_candidates <= top_k:
        # No selection needed — return all
        return _facts_to_dicts(candidate_facts), semantic_applied, n_candidates

    # Need to select: embed question and pick top-K by cosine similarity
    semantic_applied = True
    resp = client.embeddings.create(model=EMBEDDING_MODEL, input=[question])
    q_emb = resp.data[0].embedding
    scored = [(cosine_similarity(q_emb, f["embedding"]), f) for f in candidate_facts]
    scored.sort(key=lambda x: x[0], reverse=True)
    top_facts = [f for _, f in scored[:top_k]]
    return _facts_to_dicts(top_facts), semantic_applied, n_candidates


def _facts_to_dicts(facts: list[dict]) -> list[dict]:
    return [
        {
            "fact_id": f["obs_id"],
            "metric_canonical": f["metric_canonical"],
            "metric_raw": f["metric_canonical"],
            "value": f["value"],
            "year": int(f["year"]),
            "unit": f.get("unit") or "",
            "company": f["ticker"],
            "ticker": f["ticker"],
            "evidence_quote_exact": f.get("evidence_quote") or "",
            "fact_role": "component",
            "source_fact": True,
            "derived_answer_value": False,
        }
        for f in facts
    ]


# ---------------------------------------------------------------------------
# Prompt builder (mirrors round6_eval.py, extended for semfact methods)
# ---------------------------------------------------------------------------

def build_prompt_semfact(
    case: dict,
    method: str,
    facts: list[dict],
    model_visible_contract: dict,
) -> dict[str, str]:
    pdir = r5.prompt_dir()
    system = (pdir / "prompt_v3_2_system.md").read_text(encoding="utf-8")
    answer_format = (pdir / "answer_format_spec_v3_2.md").read_text(encoding="utf-8")
    rounding = (pdir / "rounding_and_tolerance_rules_v3_2.md").read_text(encoding="utf-8")
    evidence = str(case.get("evidence_text", ""))
    if method == "graph_neo4j_v6_semfact":
        context = f"GRAPH_FACTS_TABLE\n{r5.fact_table(facts)}"
    elif method == "hybrid_neo4j_v6_semfact":
        context = f"TEXT_CONTEXT\n{evidence}\n\nGRAPH_FACTS_TABLE\n{r5.fact_table(facts)}"
    else:
        raise RuntimeError(f"unknown semfact method: {method}")
    user = f"""QUESTION
{case['question']}

{context}
{r5.formula_section(model_visible_contract)}

ANSWER_FORMAT
{answer_format}

ROUNDING_AND_TOLERANCE_RULES
{rounding}

Use temperature=0 behavior. Do not use outside knowledge. Do not mention hidden expected answers or scorer-only target slots.
"""
    return {"system": system, "user": user}


# ---------------------------------------------------------------------------
# Scoring (mirrors round6_eval.py score_result, adapted for semfact)
# ---------------------------------------------------------------------------

def score_result_semfact(
    method: str,
    result: Any | None,
    required_facts: list[dict],
    neo4j_facts: list[dict],
    scorer_contract: dict,
) -> dict:
    if result is None:
        return {
            "required_fact_recall": 0.0,
            "target_numeric_recall": 0.0,
            "numerical_closeness": 0.0,
            "numeric_correctness": 0.0,
            "answer_correctness": 0.0,
            "faithfulness": 0.0,
            "calculation_completeness": 0.0,
            "answer_format_compliance": 0.0,
            "failure_reason": "provider_error",
            "matched_target_slots": "",
            "missing_target_slots": "",
        }
    output = "\n".join([result.final_answer, result.calculation])
    actual = r4.extract_numbers(output)
    slots = scorer_contract.get("target_slots", [])
    matched: list[str] = []
    missing: list[str] = []
    closeness_scores: list[float] = []
    for slot in slots:
        expected = r4.parse_number(str(slot["expected_value"]))
        closeness_scores.append(r5.slot_numerical_closeness(expected, actual))
        if expected and any(r4.close(expected, candidate, slot.get("unit", "")) for candidate in actual):
            matched.append(slot["target_slot_name"])
        else:
            missing.append(slot["target_slot_name"])
    target_recall = round(len(matched) / len(slots), 4) if slots else 0.0
    numerical_closeness = round(sum(closeness_scores) / len(closeness_scores), 4) if closeness_scores else 0.0
    numeric_ok = target_recall >= 0.8 if slots else False
    rfr = r6.required_fact_recall(required_facts, neo4j_facts)
    fmt = bool(result.final_answer and result.calculation)
    calc = bool(result.calculation and any(
        token in result.calculation.lower()
        for token in ["/", "%", "=", "ratio", "rate", "margin", "growth", "change", "formula"]
    ))
    faith = rfr >= 0.8
    ans = numeric_ok and fmt and calc and faith
    failure = "none"
    if not fmt:
        failure = "answer_format_error"
    elif rfr < 0.5:
        failure = "required_fact_missing"
    elif not numeric_ok:
        failure = "formula_target_mismatch"
    elif not ans:
        failure = "scoring_uncertain"
    return {
        "required_fact_recall": rfr,
        "target_numeric_recall": target_recall,
        "numerical_closeness": numerical_closeness,
        "numeric_correctness": 1.0 if numeric_ok else 0.0,
        "answer_correctness": 1.0 if ans else 0.0,
        "faithfulness": 1.0 if faith else 0.0,
        "calculation_completeness": 1.0 if calc else 0.0,
        "answer_format_compliance": 1.0 if fmt else 0.0,
        "failure_reason": failure,
        "matched_target_slots": ";".join(matched),
        "missing_target_slots": ";".join(missing),
    }


# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------

def summarize_semfact(rows: list[dict], split_filter: set[str] | None = None) -> list[dict]:
    selected = [r for r in rows if split_filter is None or r["split"] in split_filter]
    out = []
    for method in NEW_METHODS:
        items = [r for r in selected if r["method"] == method]
        out.append(
            {
                "method": method,
                "avg_ac": avg([r["answer_correctness"] for r in items]),
                "avg_nc": avg([r["numerical_closeness"] for r in items]),
                "avg_rfr": avg([r["required_fact_recall"] for r in items]),
                "avg_facts": avg([r["neo4j_facts_count"] for r in items]),
            }
        )
    return out


def load_r6_basic_test_results() -> dict[str, dict]:
    """Load R06 basic graph+hybrid results for comparison."""
    traces_path = R6_BASIC_RUN / "round6_traces.jsonl"
    if not traces_path.exists():
        return {}
    rows = r5.read_jsonl(traces_path)
    test_rows = [r for r in rows if r.get("split") == "round3_test"]
    out = {}
    for method in ["graph_neo4j_v6", "hybrid_neo4j_v6"]:
        items = [r for r in test_rows if r["method"] == method]
        out[method] = {
            "avg_ac": avg([r["answer_correctness"] for r in items]),
            "avg_nc": avg([r["numerical_closeness"] for r in items]),
            "avg_rfr": avg([r["required_fact_recall"] for r in items]),
            "avg_facts": avg([r["neo4j_facts_count"] for r in items]),
        }
    return out


def write_summary_semfact(run_dir: Path, traces: list[dict]) -> None:
    test_rows = [r for r in traces if r.get("split") == "round3_test"]
    r6_basic = load_r6_basic_test_results()
    semfact_test = summarize_semfact(test_rows)
    semfact_overall = summarize_semfact(traces)

    # Per-case test breakdown
    graph_semfact = {r["case_id"]: r for r in test_rows if r["method"] == "graph_neo4j_v6_semfact"}
    r6_graph_ac = {
        "round3_test_004_b035aeed": 0.0,
        "round3_test_007_4ac62908": 1.0,
        "round3_test_009_3a2f3700": 0.0,
        "round3_test_011_e428c7bc": 1.0,
        "round3_test_012_f9d03e27": 0.0,
        "round3_test_013_bc2fb598": 1.0,
        "round3_test_014_42c9db2b": 0.0,
        "round3_test_016_707dc83f": 1.0,
        "round3_test_017_68bdbbb8": 1.0,
        "round3_test_018_0748ea37": 0.0,
    }
    formula_map = {r["case_id"]: r["formula_type"] for r in test_rows}
    ticker_map = {r["case_id"]: r["ticker"] for r in test_rows}

    lines = [
        "# Round 06 Step A: Semantic Fact Retrieval",
        "",
        f"**Embedding model:** {EMBEDDING_MODEL}",
        f"**Top-K:** {TOP_K}",
        f"**KG batch:** {KG_BATCH}",
        "",
        "## Test split comparison: Round 06 basic vs Step A semantic",
        "",
        "| Method | avg_ac | avg_nc | avg_rfr | avg_facts |",
        "|---|---:|---:|---:|---:|",
    ]
    # Add R06 basic rows first
    for method in ["graph_neo4j_v6", "hybrid_neo4j_v6"]:
        if method in r6_basic:
            r = r6_basic[method]
            lines.append(f"| {method} (R06 basic) | {r['avg_ac']} | {r['avg_nc']} | {r['avg_rfr']} | {r['avg_facts']} |")
    for row in semfact_test:
        lines.append(f"| {row['method']} (Step A) | {row['avg_ac']} | {row['avg_nc']} | {row['avg_rfr']} | {row['avg_facts']} |")

    lines.extend(["", "## Overall (25 cases)", "", "| Method | avg_ac | avg_nc | avg_rfr | avg_facts |", "|---|---:|---:|---:|---:|"])
    for row in semfact_overall:
        lines.append(f"| {row['method']} | {row['avg_ac']} | {row['avg_nc']} | {row['avg_rfr']} | {row['avg_facts']} |")

    lines.extend(["", "## Per-case test breakdown (graph_semfact vs R06 basic graph)", "", "| ticker | formula_type | R06_basic_ac | StepA_semfact_ac | semantic_applied | notes |", "|---|---|---:|---:|---|---|"])
    notes_map = {
        "round3_test_004_b035aeed": "2 facts, selection n/a",
        "round3_test_007_4ac62908": "",
        "round3_test_009_3a2f3700": "royalty_rev ambiguity",
        "round3_test_011_e428c7bc": "",
        "round3_test_012_f9d03e27": "model reasoning issue",
        "round3_test_013_bc2fb598": "",
        "round3_test_014_42c9db2b": "model reasoning issue",
        "round3_test_016_707dc83f": "",
        "round3_test_017_68bdbbb8": "",
        "round3_test_018_0748ea37": "model reasoning issue",
    }
    for cid in sorted(graph_semfact):
        row = graph_semfact[cid]
        basic_ac = r6_graph_ac.get(cid, "?")
        semfact_ac = row.get("answer_correctness", 0.0)
        sem_applied = row.get("semantic_selection_applied", False)
        note = notes_map.get(cid, "")
        ticker = ticker_map.get(cid, row.get("ticker", ""))
        ftype = formula_map.get(cid, row.get("formula_type", ""))
        lines.append(f"| {ticker} | {ftype} | {basic_ac} | {semfact_ac} | {sem_applied} | {note} |")

    # Semantic selection stats
    n_applied = sum(1 for r in traces if r.get("method") == "graph_neo4j_v6_semfact" and r.get("semantic_selection_applied"))
    lines.extend([
        "",
        "## Semantic selection statistics",
        "",
        f"- Cases where semantic selection triggered: {n_applied} / 25",
        f"- Cases where top-K was a no-op (≤{TOP_K} facts): {25 - n_applied} / 25",
    ])

    # Key diagnostic
    semfact_graph_ac = next((r["avg_ac"] for r in semfact_test if r["method"] == "graph_neo4j_v6_semfact"), 0.0)
    r6_basic_graph_ac = r6_basic.get("graph_neo4j_v6", {}).get("avg_ac", 0.5)
    improved = "YES" if semfact_graph_ac > r6_basic_graph_ac else ("SAME" if math.isclose(semfact_graph_ac, r6_basic_graph_ac, abs_tol=0.001) else "NO")
    lines.extend([
        "",
        "## Key diagnostic question",
        "",
        f"Does Step A semantic selection improve test avg_ac vs R06 basic graph? **{improved}**",
        f"- R06 basic graph test avg_ac: {r6_basic_graph_ac}",
        f"- Step A semfact test avg_ac: {semfact_graph_ac}",
        "",
        "## Notes",
        "",
        "- Most primary cases have ≤8 facts → semantic selection is a no-op for those cases.",
        "- Best-effort cases (CARR=21 facts, FOXA=15 facts) may trigger semantic selection.",
        "- AMGN: if royalty_revenue ranked above total_revenue by semantic similarity, result may be worse.",
        "- Model reasoning failures (GM/MU/BXP) are unaffected by semantic selection — rfr=1.0 but formula misinterpretation remains.",
    ])

    r5.write_text(run_dir / "round6_semfact_summary.md", "\n".join(lines))


# ---------------------------------------------------------------------------
# State
# ---------------------------------------------------------------------------

def initial_state(run_dir: Path, n_embedded: int) -> dict:
    return {
        "phase": "running",
        "embedding_model": EMBEDDING_MODEL,
        "facts_embedded": n_embedded,
        "top_k": TOP_K,
        "cases_evaluated": 25,
        "methods": NEW_METHODS,
        "runs_total": 50,
        "runs_completed": 0,
        "runs_failed": [],
        "test_ac_graph_semfact": 0.0,
        "test_ac_hybrid_semfact": 0.0,
        "semantic_selection_triggered_n_cases": 0,
        "run_dir": rel(run_dir) + "/",
        "started_at": utc_now(),
        "completed_at": None,
        "codex_handoff_message": None,
    }


def update_state(state: dict, traces: list[dict]) -> None:
    state["runs_completed"] = len(traces)
    state["runs_failed"] = [
        {"case_id": r["case_id"], "method": r["method"], "failure_reason": r.get("failure_reason")}
        for r in traces if r.get("failure_reason") == "provider_error"
    ]
    test_rows = [r for r in traces if r.get("split") == "round3_test"]
    state["test_ac_graph_semfact"] = avg([r["answer_correctness"] for r in test_rows if r["method"] == "graph_neo4j_v6_semfact"])
    state["test_ac_hybrid_semfact"] = avg([r["answer_correctness"] for r in test_rows if r["method"] == "hybrid_neo4j_v6_semfact"])
    state["semantic_selection_triggered_n_cases"] = sum(
        1 for r in traces if r.get("method") == "graph_neo4j_v6_semfact" and r.get("semantic_selection_applied")
    )
    r5.write_json(STATE_PATH, state)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-dir", default="")
    parser.add_argument("--resume", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--skip-embed", action="store_true", default=False, help="Skip embedding if cache exists")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    SEMFACT_OUT.mkdir(parents=True, exist_ok=True)

    # Build OpenAI client
    import os
    from openai import OpenAI
    client = OpenAI(api_key=os.environ["OPENAI_API_KEY"])

    driver = r5.create_driver()
    try:
        # Phase 1: Build fact embeddings
        if args.skip_embed and EMBEDDINGS_PATH.exists():
            fact_embeddings = load_fact_embeddings()
            print(f"Loaded {len(fact_embeddings)} cached fact embeddings")
        else:
            fact_embeddings = build_fact_embeddings(client, driver)

        n_embedded = len(fact_embeddings)

        # Phase 2: Load evaluation inputs
        cases = r5.load_cases()
        scorer_contracts, visible_contracts = r5.load_all_formula_contracts()
        required_by_case = r5.load_required_facts()
        source_target_fallback_cases = set(r5.fill_source_target_slots(cases, scorer_contracts, required_by_case))

        # Phase 3: Determine run directory
        run_dir = Path(args.run_dir).resolve() if args.run_dir else OUT_ROOT / f"round6_semfact_{ts()}"
        if args.resume and not args.run_dir and STATE_PATH.exists():
            existing = r5.read_json(STATE_PATH)
            if existing.get("phase") == "running" and existing.get("run_dir"):
                run_dir = ROOT / str(existing["run_dir"]).rstrip("/")
        run_dir.mkdir(parents=True, exist_ok=True)

        state = initial_state(run_dir, n_embedded)
        if args.resume and STATE_PATH.exists():
            existing = r5.read_json(STATE_PATH)
            if existing.get("run_dir") == rel(run_dir) + "/" and existing.get("phase") in {"running", "done"}:
                state.update(existing)
                state["phase"] = "running"
                state["completed_at"] = None
                state["codex_handoff_message"] = None
        r5.write_json(STATE_PATH, state)

        traces_path = run_dir / "round6_semfact_traces.jsonl"
        traces = r5.read_jsonl(traces_path) if args.resume else []
        if args.resume:
            traces = [r for r in traces if r.get("failure_reason") != "provider_error"]
            r5.write_jsonl(traces_path, traces)
        completed = {(r["case_id"], r["method"]) for r in traces}
        rows_run = 0

        # Phase 4: Evaluation loop
        for case in cases:
            cid = case["case_id"]
            ticker = str(case["ticker"]).upper()
            years = [int(y) for y in case.get("years", [])]
            question = str(case.get("question", ""))

            for method in NEW_METHODS:
                if (cid, method) in completed:
                    continue
                if args.limit is not None and rows_run >= args.limit:
                    update_state(state, traces)
                    return

                # Semantic retrieval
                graph_facts, semantic_applied, n_candidates = load_neo4j_graph_facts_semantic(
                    ticker, cid, years, question, client, fact_embeddings, TOP_K
                )

                prompt = build_prompt_semfact(case, method, graph_facts, visible_contracts[cid])
                result = None
                raw = None
                usage: dict = {}
                attempt_idx = len(traces) + 1
                base = {
                    "trace_id": f"local_trace_step_a_{attempt_idx:04d}_{cid}__{method}",
                    "case_id": cid,
                    "ticker": ticker,
                    "split": case.get("split", ""),
                    "method": method,
                    "round": ROUND,
                    "track": TRACK,
                    "kg_batch": KG_BATCH,
                    "formula_type": scorer_contracts[cid].get("formula_type") or visible_contracts[cid].get("formula_type", ""),
                    "diagnostic_source_target_fallback": cid in source_target_fallback_cases,
                    "test_split_formula_source": TEST_FORMULA_SOURCE if case.get("split") == "round3_test" else "pre_built",
                    "neo4j_facts_count": len(graph_facts),
                    "semantic_selection_applied": semantic_applied,
                    "semantic_top_k": TOP_K,
                    "candidate_facts_before_selection": n_candidates,
                    "embedding_model": EMBEDDING_MODEL,
                    "provider": "openai",
                    "model": MODEL,
                    "target_slot_count": len(scorer_contracts[cid].get("target_slots", [])),
                    "success": False,
                    "provider_success": False,
                    "error_type": "",
                    "error_message": "",
                }
                try:
                    result, usage, raw = r5.call_openai(prompt)
                    base.update({"success": True, "provider_success": True})
                except r4.ProviderError as exc:
                    base.update({"error_type": exc.error_type, "error_message": str(exc)})
                except Exception as exc:  # noqa: BLE001
                    base.update({"error_type": "scoring_uncertain", "error_message": str(exc)[:300]})

                required_facts = scorer_contracts[cid].get("source_fact_numbers") or required_by_case.get(cid, [])
                scores = score_result_semfact(method, result, required_facts, graph_facts, scorer_contracts[cid])
                base.update(scores)
                if base.get("error_type") in r4.PROVIDER_ERROR_TYPES:
                    base["failure_reason"] = "provider_error"

                row = {
                    **base,
                    "claim_boundary": CLAIM_BOUNDARY,
                    "final_answer": result.final_answer if result else "",
                    "calculation": result.calculation if result else "",
                    "prompt_sha256": r4.sha(prompt["system"] + "\n" + prompt["user"]),
                    "system_prompt": prompt["system"],
                    "user_prompt": prompt["user"],
                    "method_result": asdict(result) if result else None,
                    "raw_result": raw,
                    "usage": usage,
                    "model_api_called": True,
                    "neo4j_write_performed": False,
                }
                traces.append(row)
                completed.add((cid, method))
                rows_run += 1
                r5.write_jsonl(traces_path, traces)
                update_state(state, traces)
                print(
                    json.dumps(
                        {
                            "runs_completed": len(traces),
                            "runs_total": 50,
                            "case_id": cid,
                            "method": method,
                            "neo4j_facts_count": len(graph_facts),
                            "semantic_applied": semantic_applied,
                            "n_candidates": n_candidates,
                            "failure_reason": row.get("failure_reason"),
                        },
                        ensure_ascii=False,
                    ),
                    flush=True,
                )
                time.sleep(0.25)

    finally:
        driver.close()

    write_summary_semfact(run_dir, traces)
    state["phase"] = "done"
    state["completed_at"] = utc_now()
    state["codex_handoff_message"] = "Step A complete. Combine with Round 06 basic results for 6차 report."
    update_state(state, traces)
    print(
        json.dumps(
            {
                "run_dir": rel(run_dir),
                "runs_completed": len(traces),
                "summary": rel(run_dir / "round6_semfact_summary.md"),
                "test_ac_graph_semfact": state["test_ac_graph_semfact"],
                "test_ac_hybrid_semfact": state["test_ac_hybrid_semfact"],
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
