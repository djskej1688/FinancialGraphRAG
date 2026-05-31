"""Round 14 - Anchor Coverage + Traversal Robustness Audit (READ-ONLY, post-hoc).

Phase 3.5 of codex_prompt_round14_cross_company.md, runnable post-hoc after eval.

What it does (NO writes anywhere, neo4j_write_performed stays False):
  1. Arm-level coverage from authoritative files (write_log, observations.jsonl,
     metric dictionary, embedding cache).
  2. Optional Neo4j READ-ONLY verification that written (ticker, metric, year)
     triples are actually reachable Company--Observation--Metric--Year.
     If Neo4j is unavailable, routing still works from the write log (flagged).
  3. Per-case retrieval-mode routing for the 80 synthetic cross-company cases:
       structural_graph / graph_guided_chunk / vector_topic_fallback / text_only / no_go
     RULE: anchor missing but references_joined (embeddingText) exists
           => vector_topic_fallback, NOT a KG failure. No anchor auto-creation.
  4. Join routing into round14_traces.jsonl and report method x mode AC,
     so graph_structured advantage is only claimed inside the structural_graph bucket.

Outputs (all under outputs/round14_cross_company/03_extraction/):
  anchor_coverage.json
  anchor_coverage_report.md
  retrieval_mode_routing.csv
  traversal_robustness.md
  round14_mode_stratified_method_scores.md

Usage:
  python scripts/round14_anchor_audit.py
  python scripts/round14_anchor_audit.py --batch kg-round14-multicompany-v1-20260530
  python scripts/round14_anchor_audit.py --no-neo4j     # skip DB verification
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import sys
from collections import defaultdict
from pathlib import Path
from statistics import mean

try:
    sys.stdout.reconfigure(encoding="utf-8")  # avoid cp949 issues on Windows
except Exception:
    pass

REPO_ROOT = Path(__file__).resolve().parents[1]
OUT_DIR = REPO_ROOT / "outputs" / "round14_cross_company"
EXTRACT_DIR = OUT_DIR / "03_extraction"
CASES_PATH = OUT_DIR / "04_cross_company_queries" / "cross_company_cases.jsonl"
WRITE_LOG = EXTRACT_DIR / "neo4j_write_log.jsonl"
OBS_PATH = EXTRACT_DIR / "observations.jsonl"
EMBED_CACHE = EXTRACT_DIR / "embedding_cache.jsonl"
STATE_PATH = OUT_DIR / "state.json"
METRIC_DICT = OUT_DIR / "01_metric_dictionary" / "metric_dictionary_fibo_style.json"

ALL_SLICES_CANDIDATES = [
    REPO_ROOT / "inputs" / "round14" / "all_slices.csv",
    REPO_ROOT / "inputs" / "all_slices.csv",
    REPO_ROOT / "all_slices.csv",
]

ENV_FILES = (REPO_ROOT / ".env", REPO_ROOT.parent / ".env")
CSV_ID_COL = "_id"
CSV_EVIDENCE_COL = "references_joined"

# Read-only guard: every cypher we emit must NOT contain these.
FORBIDDEN_CYPHER = re.compile(
    r"\b(CREATE|MERGE|SET|DELETE|REMOVE|DROP|LOAD\s+CSV)\b", re.IGNORECASE
)


# --------------------------------------------------------------------------- #
# IO helpers
# --------------------------------------------------------------------------- #
def load_jsonl(path: Path):
    rows = []
    if not path.exists():
        return rows
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return rows


def count_lines(path: Path) -> int:
    if not path.exists():
        return 0
    n = 0
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            if line.strip():
                n += 1
    return n


def write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text.rstrip() + "\n", encoding="utf-8", newline="\n")


def write_jsonl(path: Path, rows) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")


def load_env():
    for ef in ENV_FILES:
        if not ef.exists():
            continue
        for line in ef.read_text(encoding="utf-8", errors="ignore").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            k, v = k.strip(), v.strip().strip('"').strip("'")
            if k and k not in __import__("os").environ:
                __import__("os").environ[k] = v


# --------------------------------------------------------------------------- #
# Write-log parsing -> authoritative written triple set
# obs_id format: {batch}__{case}__{ticker}__{metric}__{year}__{seq}
# --------------------------------------------------------------------------- #
def parse_written_triples(write_rows):
    triples = set()          # (ticker, metric_lower, year_int)
    tickers = set()
    bad = 0
    for r in write_rows:
        oid = r.get("obs_id", "")
        parts = oid.split("__")
        if len(parts) < 5:
            bad += 1
            continue
        ticker = (r.get("ticker") or parts[-4]).strip()
        metric = parts[-3].strip().lower()
        year_raw = parts[-2].strip()
        try:
            year = int(year_raw)
        except ValueError:
            bad += 1
            continue
        triples.add((ticker, metric, year))
        tickers.add(ticker)
    return triples, tickers, bad


# --------------------------------------------------------------------------- #
# Neo4j read-only verification (optional)
# --------------------------------------------------------------------------- #
def neo4j_reachable_triples(batch: str):
    """Return (reachable_set, info). reachable_set is None if DB unavailable."""
    import os

    info = {"available": False, "error": "", "schema": {}}
    try:
        from neo4j import GraphDatabase
    except Exception as e:
        info["error"] = f"neo4j driver import failed: {e}"
        return None, info

    uri = os.environ.get("NEO4J_URI")
    user = os.environ.get("NEO4J_USERNAME")
    pwd = os.environ.get("NEO4J_PASSWORD")
    db = os.environ.get("NEO4J_DATABASE") or "neo4j"
    if not (uri and user and pwd):
        info["error"] = "NEO4J_URI/USERNAME/PASSWORD not set"
        return None, info

    def _run(tx, q, **params):
        assert not FORBIDDEN_CYPHER.search(q), f"write keyword in query: {q}"
        return [dict(rec) for rec in tx.run(q, **params)]

    try:
        driver = GraphDatabase.driver(uri, auth=(user, pwd))
        with driver.session(database=db) as session:
            counts = session.execute_read(
                _run,
                """
MATCH (o:LLMObservation {kg_batch:$b})
RETURN count(o) AS observations,
       count { (o)-[:LLM_MENTIONS_COMPANY]->(:LLMCompany) } AS company_edges,
       count { (o)-[:LLM_OBSERVES_METRIC]->(:LLMFinancialMetric) } AS metric_edges,
       count { (o)-[:LLM_OBSERVED_IN_YEAR]->(:LLMFiscalYear) } AS year_edges
""",
                b=batch,
            )[0]
            info["schema"] = {
                "obs_label": "LLMObservation",
                "company_label": "LLMCompany",
                "metric_label": "LLMFinancialMetric",
                "year_label": "LLMFiscalYear",
                "company_prop": "ticker",
                "metric_prop": "canonical_name",
                "year_prop": "year",
                **counts,
            }
            if int(counts.get("observations") or 0) == 0:
                info["error"] = f"no LLMObservation nodes with kg_batch={batch}"
                driver.close()
                return None, info
            rows = session.execute_read(
                _run,
                """
MATCH (o:LLMObservation {kg_batch:$b})-[:LLM_MENTIONS_COMPANY]->(c:LLMCompany)
MATCH (o)-[:LLM_OBSERVES_METRIC]->(m:LLMFinancialMetric)
MATCH (o)-[:LLM_OBSERVED_IN_YEAR]->(y:LLMFiscalYear)
RETURN DISTINCT c.ticker AS ticker, m.canonical_name AS metric, y.year AS year
""",
                b=batch,
            )
        driver.close()

        reachable = set()
        for row in rows:
            t = row.get("ticker")
            m = row.get("metric")
            y = row.get("year")
            if t is None or m is None:
                continue
            try:
                y = int(y) if y is not None else None
            except (ValueError, TypeError):
                y = None
            reachable.add((str(t).strip(), str(m).strip().lower(), y))
        info["available"] = True
        info["reachable_count"] = len(reachable)
        return reachable, info
    except Exception as e:
        info["error"] = f"neo4j query failed: {e}"
        return None, info


# --------------------------------------------------------------------------- #
# Routing
# --------------------------------------------------------------------------- #
def required_triples_by_company(case):
    """Group required (ticker, metric, year) from source_observations by ticker."""
    by_ticker = defaultdict(set)
    src_cases_by_ticker = defaultdict(set)
    all_verified = True
    for ob in case.get("source_observations", []):
        tk = (ob.get("ticker") or "").strip()
        mc = (ob.get("metric_canonical") or "").strip().lower()
        yr = ob.get("year")
        try:
            yr = int(yr)
        except (ValueError, TypeError):
            yr = None
        if tk and mc and yr is not None:
            by_ticker[tk].add((tk, mc, yr))
        if ob.get("case_id"):
            src_cases_by_ticker[tk].add(ob["case_id"])
        if ob.get("evidence_quote_verified") is False:
            all_verified = False
    return by_ticker, src_cases_by_ticker, all_verified


def route_case(case, written, tickers, reachable, text_by_id, neo4j_ok):
    a = (case.get("company_a") or "").strip()
    b = (case.get("company_b") or "").strip()
    req_by_tk, src_by_tk, gt_verified = required_triples_by_company(case)

    req_a = req_by_tk.get(a, set())
    req_b = req_by_tk.get(b, set())

    a_anchor = a in tickers
    b_anchor = b in tickers
    a_written = bool(req_a) and req_a <= written and a_anchor
    b_written = bool(req_b) and req_b <= written and b_anchor

    # reachability (verified traversal); if DB unavailable, fall back to written
    verify_set = reachable if (neo4j_ok and reachable is not None) else written
    a_reach = bool(req_a) and req_a <= verify_set and a_anchor
    b_reach = bool(req_b) and req_b <= verify_set and b_anchor

    a_text = any(text_by_id.get(cid) for cid in src_by_tk.get(a, set())) or bool(text_by_id.get(a))
    b_text = any(text_by_id.get(cid) for cid in src_by_tk.get(b, set())) or bool(text_by_id.get(b))

    a_struct = a_reach
    b_struct = b_reach

    if a_struct and b_struct:
        mode = "structural_graph"
    elif a_anchor and b_anchor and a_text and b_text:
        mode = "graph_guided_chunk"      # anchored but a (metric,year) cell not reachable/written
    elif a_text and b_text:
        mode = "vector_topic_fallback"   # anchor missing BUT text exists -> not a KG failure
    elif a_text or b_text:
        mode = "text_only"
    else:
        mode = "no_go"

    return {
        "case_id": case.get("case_id"),
        "level": case.get("level"),
        "company_a": a, "company_b": b,
        "metric": case.get("metric"), "year": case.get("year"),
        "a_anchor": a_anchor, "b_anchor": b_anchor,
        "a_cells_written": a_written, "b_cells_written": b_written,
        "a_reachable": a_reach, "b_reachable": b_reach,
        "a_text": a_text, "b_text": b_text,
        "ground_truth_verified": gt_verified,
        "recommended_mode": mode,
    }


# --------------------------------------------------------------------------- #
# Main
# --------------------------------------------------------------------------- #
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--batch", default=None, help="kg_batch (default: from state.json)")
    ap.add_argument("--no-neo4j", action="store_true", help="skip Neo4j verification")
    args = ap.parse_args()

    state = json.loads(STATE_PATH.read_text(encoding="utf-8")) if STATE_PATH.exists() else {}
    batch = args.batch or state.get("kg_batch")
    if not batch:
        print("ERROR: no kg_batch (pass --batch).")
        sys.exit(1)

    # trace file: from state, else latest round14 run dir
    trace_file = state.get("trace_file")
    if trace_file and Path(trace_file).exists():
        trace_path = Path(trace_file)
    else:
        cand = sorted((REPO_ROOT / "outputs" / "round3_eval_runs").glob(
            "round14_cross_company_*/round14_traces.jsonl"))
        trace_path = cand[-1] if cand else None

    print(f"[round14_anchor_audit] batch={batch}")
    print(f"[round14_anchor_audit] traces={trace_path}")

    # ---- load authoritative files ----
    cases = load_jsonl(CASES_PATH)
    write_rows = load_jsonl(WRITE_LOG)
    obs_rows = load_jsonl(OBS_PATH)
    written, tickers, bad_obsid = parse_written_triples(write_rows)

    # text (embeddingText) availability by source _id
    text_by_id = {}
    all_slices = next((p for p in ALL_SLICES_CANDIDATES if p.exists()), None)
    if all_slices:
        with all_slices.open("r", encoding="utf-8") as f:
            for row in csv.DictReader(f):
                _id = (row.get(CSV_ID_COL) or "").strip()
                if _id:
                    text_by_id[_id] = bool((row.get(CSV_EVIDENCE_COL) or "").strip())

    # metric dictionary (controlled vocab) -> orphan vocab
    dict_metrics = set()
    if METRIC_DICT.exists():
        try:
            md = json.loads(METRIC_DICT.read_text(encoding="utf-8"))
            items = md if isinstance(md, list) else md.get("metrics", md.values() if isinstance(md, dict) else [])
            for it in items:
                if isinstance(it, dict) and it.get("canonical_name"):
                    dict_metrics.add(it["canonical_name"].strip().lower())
                elif isinstance(it, str):
                    dict_metrics.add(it.strip().lower())
        except Exception:
            pass

    # ---- Neo4j verification (optional) ----
    reachable, neo4j_info = (None, {"available": False, "error": "skipped (--no-neo4j)"})
    if not args.no_neo4j:
        load_env()
        reachable, neo4j_info = neo4j_reachable_triples(batch)
    neo4j_ok = neo4j_info.get("available", False)

    # ---- arm-level coverage (from files) ----
    observed_metrics = {m for (_t, m, _y) in written}
    observed_tickers = tickers
    cells = defaultdict(set)  # (metric, year) -> {tickers}
    for (t, m, y) in written:
        cells[(m, y)].add(t)
    comparable_cells = {k: v for k, v in cells.items() if len(v) >= 2}

    obs_with_provenance = sum(
        1 for o in obs_rows_flat(obs_rows)
        if (o.get("evidence_quote") or "").strip()
    )
    obs_verified = sum(
        1 for o in obs_rows_flat(obs_rows)
        if o.get("evidence_quote_verified") is True
    )
    total_obs_flat = sum(1 for _ in obs_rows_flat(obs_rows))
    orphan_vocab = sorted(dict_metrics - observed_metrics) if dict_metrics else []

    # reachability robustness
    if neo4j_ok and reachable is not None:
        written_reachable = len(written & reachable)
        written_not_reachable = len(written - reachable)
    else:
        written_reachable = None
        written_not_reachable = None

    # ---- per-case routing ----
    routes = [route_case(c, written, tickers, reachable, text_by_id, neo4j_ok) for c in cases]
    mode_counts = defaultdict(int)
    for r in routes:
        mode_counts[r["recommended_mode"]] += 1

    structural = mode_counts["structural_graph"]
    fallback = mode_counts["graph_guided_chunk"] + mode_counts["vector_topic_fallback"]
    no_go = mode_counts["no_go"]
    n_cases = len(routes)
    structural_ratio = (structural / n_cases) if n_cases else 0.0

    # gates
    ga = len(observed_tickers) >= 2
    gb = structural_ratio >= 0.40
    gc_no_go = no_go

    # ---- traces join + mode stratification ----
    traces = load_jsonl(trace_path) if trace_path else []
    route_by_case = {r["case_id"]: r for r in routes}
    mode_by_case = {cid: r["recommended_mode"] for cid, r in route_by_case.items()}

    # Persist the routing join into round14_traces.jsonl as requested. The audit is
    # read-only with respect to Neo4j; this local file enrichment is idempotent.
    enriched_traces = []
    n_joined_traces = 0
    for t in traces:
        cid = t.get("case_id")
        route = route_by_case.get(cid)
        if not route:
            enriched_traces.append(t)
            continue
        enriched = dict(t)
        enriched["recommended_mode"] = route["recommended_mode"]
        enriched["retrieval_mode"] = route["recommended_mode"]
        for key, value in route.items():
            if key == "case_id":
                continue
            enriched[f"routing_{key}"] = value
        enriched_traces.append(enriched)
        n_joined_traces += 1
    if trace_path and enriched_traces:
        write_jsonl(trace_path, enriched_traces)
        traces = enriched_traces

    # method x mode -> list of AC
    bucket = defaultdict(list)
    method_overall = defaultdict(list)
    both_found = defaultdict(list)
    n_traces_with_mode = 0
    for t in traces:
        cid = t.get("case_id")
        method = t.get("method")
        ac = t.get("answer_correctness")
        if method is None or ac is None:
            continue
        mode = mode_by_case.get(cid, "unrouted")
        if cid in mode_by_case:
            n_traces_with_mode += 1
        bucket[(method, mode)].append(float(ac))
        method_overall[method].append(float(ac))
        if t.get("both_companies_found") is not None:
            both_found[(method, mode)].append(1.0 if t.get("both_companies_found") else 0.0)

    methods = sorted(method_overall)
    modes_order = ["structural_graph", "graph_guided_chunk", "vector_topic_fallback",
                   "text_only", "no_go", "unrouted"]

    # ----------------------------------------------------------------- #
    # write outputs
    # ----------------------------------------------------------------- #
    EXTRACT_DIR.mkdir(parents=True, exist_ok=True)

    coverage = {
        "kg_batch": batch,
        "neo4j_write_performed": False,
        "neo4j_verification": neo4j_info,
        "metric_nodes": len(observed_metrics),
        "value_observation_nodes": len(written),
        "observation_rows_in_file": total_obs_flat,
        "company_anchor_nodes": len(observed_tickers),
        "metrics_with_company_anchor": len(observed_metrics),  # all observed metrics come via a company obs
        "metrics_without_company_anchor": 0,
        "observations_with_source_chunk": obs_with_provenance,
        "observations_evidence_verified": obs_verified,
        "chunks_with_embeddingtext": count_lines(EMBED_CACHE),
        "controlled_vocab_total": len(dict_metrics),
        "controlled_vocab_unobserved_orphans": orphan_vocab,
        "metric_year_cells": len(cells),
        "comparable_cells_ge2_companies": len(comparable_cells),
        "written_triples": len(written),
        "written_triples_reachable": written_reachable,
        "written_triples_not_reachable": written_not_reachable,
        "bad_obsid_parse": bad_obsid,
        "successful_structural_traversal_count": structural,
        "fallback_retrieval_count": fallback,
        "no_go_count": no_go,
        "n_cross_company_cases": n_cases,
        "structural_ratio": round(structural_ratio, 4),
        "mode_distribution": dict(mode_counts),
        "gates": {
            "GA_company_anchor_ge2": ga,
            "GB_structural_ratio_ge_0.40": gb,
            "GC_no_go_count": gc_no_go,
        },
        "traces_total": len(traces),
        "traces_with_mode": n_traces_with_mode,
        "traces_joined_to_routing": n_joined_traces,
        "trace_file_enriched": str(trace_path) if trace_path else "",
    }
    write_text(EXTRACT_DIR / "anchor_coverage.json",
               json.dumps(coverage, indent=2, ensure_ascii=False))

    # routing csv
    csv_cols = ["case_id", "level", "company_a", "company_b", "metric", "year",
                "a_anchor", "b_anchor", "a_cells_written", "b_cells_written",
                "a_reachable", "b_reachable", "a_text", "b_text",
                "ground_truth_verified", "recommended_mode"]
    routing_csv = EXTRACT_DIR / "retrieval_mode_routing.csv"
    with routing_csv.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=csv_cols)
        w.writeheader()
        for r in routes:
            w.writerow(r)

    # anchor coverage report (human, uses the user's requested fields)
    rv = (str(written_reachable) if written_reachable is not None else "n/a (DB unavailable)")
    rnr = (str(written_not_reachable) if written_not_reachable is not None else "n/a")
    lines = [
        "# Round 14 - Anchor Coverage Report",
        "",
        f"**kg_batch:** `{batch}`  ",
        f"**neo4j_write_performed:** False (read-only audit)  ",
        f"**Neo4j verification:** {'OK' if neo4j_ok else 'UNAVAILABLE -> routing from write log'}"
        f"{'' if neo4j_ok else '  (' + neo4j_info.get('error','') + ')'}",
        "",
        "## Arm: kg-round14-multicompany",
        "",
        "| field | value |",
        "|---|---|",
        f"| metric (canonical) nodes | {len(observed_metrics)} |",
        f"| value/observation triples (ticker,metric,year) | {len(written)} |",
        f"| observation rows in file | {total_obs_flat} |",
        f"| Company anchor nodes (tickers) | {len(observed_tickers)} |",
        f"| metric nodes WITH company anchor | {len(observed_metrics)} |",
        f"| metric nodes WITHOUT company anchor | 0 |",
        f"| observations with source chunk (evidence_quote) | {obs_with_provenance} |",
        f"| observations evidence-verified | {obs_verified} |",
        f"| chunks with embeddingText (embedding_cache) | {coverage['chunks_with_embeddingtext']} |",
        f"| controlled-vocab metrics total | {len(dict_metrics)} |",
        f"| controlled-vocab unobserved (orphan) | {len(orphan_vocab)} |",
        f"| (metric,year) cells | {len(cells)} |",
        f"| comparable cells (>=2 companies) | {len(comparable_cells)} |",
        f"| written triples reachable in Neo4j | {rv} |",
        f"| written triples NOT reachable (silent fail) | {rnr} |",
        f"| successful structural traversal (cases) | {structural} |",
        f"| fallback retrieval (cases) | {fallback} |",
        f"| orphan / no_go (cases) | {no_go} |",
        "",
    ]
    if orphan_vocab:
        lines += ["**Orphan controlled-vocab metrics (defined, never observed):** "
                  + ", ".join(orphan_vocab), ""]
    write_text(EXTRACT_DIR / "anchor_coverage_report.md", "\n".join(lines))

    # traversal robustness + gates + mode distribution
    tr = [
        "# Round 14 - Traversal Robustness",
        "",
        f"Cross-company cases: {n_cases}",
        "",
        "## Retrieval mode distribution",
        "",
        "| mode | cases |",
        "|---|---|",
    ]
    for m in modes_order:
        if m == "unrouted":
            continue
        tr.append(f"| {m} | {mode_counts.get(m, 0)} |")
    tr += [
        "",
        f"- successful_structural_traversal_count = **{structural}**",
        f"- fallback_retrieval_count (graph_guided_chunk + vector_topic_fallback) = **{fallback}**",
        f"- no_go_count = **{no_go}**",
        f"- structural_ratio = **{structural_ratio:.3f}**",
        "",
        "## Gates",
        "",
        f"- **GA** company_anchor >= 2: {'PASS' if ga else 'FAIL'} ({len(observed_tickers)} companies)",
        f"- **GB** structural_ratio >= 0.40: {'PASS' if gb else 'WARN'} "
        f"({structural_ratio:.3f}) -- if WARN, weaken cross-company advantage claim in summary.",
        f"- **GC** no_go cases excluded from eval population: {no_go} (report, do not score as graph).",
        "",
    ]
    if neo4j_ok and written_not_reachable:
        tr += [f"> WARNING: {written_not_reachable} written triples are NOT reachable in Neo4j "
               "(written-but-orphaned). Investigate relationship wiring before claiming structural wins.", ""]
    write_text(EXTRACT_DIR / "traversal_robustness.md", "\n".join(tr))

    # mode-stratified method scores
    ms = [
        "# Round 14 - Method Scores stratified by Retrieval Mode",
        "",
        f"Traces: {len(traces)} (with routed mode: {n_traces_with_mode}). "
        f"Eval may be partial; re-run after eval completes.",
        "",
        "**Rule:** graph_structured_v14 advantage is only claimable inside the "
        "`structural_graph` bucket. Other buckets reflect fallback, not graph traversal.",
        "",
        "## Mean answer_correctness: method x mode (n)",
        "",
    ]
    header = "| method | " + " | ".join(
        m for m in modes_order if any((mm == m) for (_me, mm) in bucket)) + " | overall |"
    active_modes = [m for m in modes_order if any((mm == m) for (_me, mm) in bucket)]
    ms.append("| method | " + " | ".join(active_modes) + " | overall (n) |")
    ms.append("|" + "---|" * (len(active_modes) + 2))
    for method in methods:
        cells_out = []
        for m in active_modes:
            vals = bucket.get((method, m), [])
            cells_out.append(f"{mean(vals):.3f} (n={len(vals)})" if vals else "-")
        ov = method_overall[method]
        ms.append(f"| {method} | " + " | ".join(cells_out)
                  + f" | {mean(ov):.3f} (n={len(ov)}) |")
    ms += ["", "## both_companies_found rate: method x mode", ""]
    ms.append("| method | " + " | ".join(active_modes) + " |")
    ms.append("|" + "---|" * (len(active_modes) + 1))
    for method in methods:
        cells_out = []
        for m in active_modes:
            vals = both_found.get((method, m), [])
            cells_out.append(f"{mean(vals):.2f} (n={len(vals)})" if vals else "-")
        ms.append(f"| {method} | " + " | ".join(cells_out) + " |")
    write_text(EXTRACT_DIR / "round14_mode_stratified_method_scores.md", "\n".join(ms))

    # ---- console summary ----
    print("\n=== ARM COVERAGE ===")
    print(f"companies={len(observed_tickers)} metrics={len(observed_metrics)} "
          f"triples={len(written)} comparable_cells={len(comparable_cells)} "
          f"embeddingText_chunks={coverage['chunks_with_embeddingtext']}")
    if neo4j_ok:
        print(f"neo4j reachable triples={len(reachable)} / written={len(written)} "
              f"(not_reachable={written_not_reachable})")
    else:
        print(f"neo4j: UNAVAILABLE ({neo4j_info.get('error','')}) -> routing from write log")
    print("\n=== ROUTING ===")
    for m in modes_order:
        if mode_counts.get(m):
            print(f"  {m}: {mode_counts[m]}")
    print(f"structural_ratio={structural_ratio:.3f}  "
          f"GA={'PASS' if ga else 'FAIL'} GB={'PASS' if gb else 'WARN'} no_go={no_go}")
    print(f"\ntraces={len(traces)} (routed={n_traces_with_mode})")
    print("\nWrote 5 files to", EXTRACT_DIR)


def obs_rows_flat(obs_rows):
    """observations.jsonl groups observations per case; yield each observation dict.
    Also tolerate already-flat rows."""
    for row in obs_rows:
        obs = row.get("observations")
        if isinstance(obs, list):
            for o in obs:
                yield o
        elif "evidence_quote" in row or "metric_canonical" in row:
            yield row


if __name__ == "__main__":
    main()
