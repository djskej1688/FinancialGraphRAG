"""
Claude's verification script for the LLM-based KG rebuild.
Run this after Codex sets state.json["phase"] = "done".
Read-only against Neo4j.
"""
import os, sys, io, json, re
from pathlib import Path
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

from neo4j import GraphDatabase

STATE_PATH = Path("C:/Users/USER/Documents/graphRAG/seocho/outputs/kg_rebuild_llm_ie/state.json")
CASES_PATH = Path("C:/Users/USER/Documents/graphRAG/seocho/outputs/round3_dual_track_eval_prep/track_b_shadow_overlay/shadow_overlay_eval_ready_cases.jsonl")
FACTS_PATH = Path("C:/Users/USER/Documents/graphRAG/seocho/outputs/round3_dual_track_eval_prep/track_b_shadow_overlay/shadow_overlay_required_facts.jsonl")

NEO4J_URI = os.environ.get("NEO4J_URI", "bolt://localhost:7687")
NEO4J_USER = os.environ.get("NEO4J_USERNAME") or os.environ.get("NEO4J_USER", "neo4j")
NEO4J_PASS = os.environ["NEO4J_PASSWORD"]
NEO4J_DB = os.environ.get("NEO4J_DATABASE", "neo4j")
KG_BATCH   = "kg-llm-ie-v1-20260528"

JUNK_METRIC_PATTERNS = re.compile(
    r"(^at_the_|^during_|^in_the_|^for_the_|^as_of_|^march_|^june_|^december_|^january_|"
    r"^generation_x|birth_year|expiration|^the_|^and_|^or_|^of_the_)"
)

# ── load state ────────────────────────────────────────────────────────────────
print("=" * 60)
print("Loading state.json ...")
state = json.loads(STATE_PATH.read_text(encoding="utf-8"))
print(f"  phase: {state['phase']}")
print(f"  cases_processed: {state.get('cases_processed', '?')}/{state.get('cases_total', '?')}")
print(f"  cases_failed: {state.get('cases_failed', [])}")
if state["phase"] not in ("done", "partial"):
    print("  WARNING: phase is not 'done' — Codex may not have finished")

# ── load case list ────────────────────────────────────────────────────────────
cases = [json.loads(l) for l in CASES_PATH.read_text(encoding="utf-8").splitlines() if l.strip()]
tickers_expected = {c["ticker"] for c in cases}
case_ids_expected = {c["case_id"] for c in cases}
print(f"\nExpected tickers ({len(tickers_expected)}): {sorted(tickers_expected)}")

# ── connect Neo4j ─────────────────────────────────────────────────────────────
driver = GraphDatabase.driver(NEO4J_URI, auth=(NEO4J_USER, NEO4J_PASS))
issues = []

with driver.session(database=NEO4J_DB) as s:

    # ── Check 1: Node counts ──────────────────────────────────────────────────
    print("\n" + "=" * 60)
    print("Check 1: Node counts by type")
    print("=" * 60)
    for label in ["LLMCompany", "LLMFinancialMetric", "LLMObservation", "LLMFiscalYear", "LLMDatasetCase"]:
        r = s.run(f"MATCH (n:{label}) WHERE n.kg_batch = $b RETURN count(n) AS cnt", b=KG_BATCH)
        cnt = r.single()["cnt"]
        print(f"  {label}: {cnt}")
        if label == "LLMObservation" and cnt == 0:
            issues.append("CRITICAL: No LLMObservation nodes found")
        if label == "LLMCompany" and cnt == 0:
            issues.append("CRITICAL: No LLMCompany nodes found")

    # ── Check 2: Relationship counts ─────────────────────────────────────────
    print("\n" + "=" * 60)
    print("Check 2: Relationship type distribution")
    print("=" * 60)
    for rel in ["LLM_MENTIONS_COMPANY", "LLM_OBSERVES_METRIC", "LLM_OBSERVED_IN_YEAR", "LLM_HAS_OBSERVATION"]:
        r = s.run(f"MATCH ()-[r:{rel}]->() RETURN count(r) AS cnt")
        cnt = r.single()["cnt"]
        print(f"  :{rel} — {cnt}")
        if cnt == 0:
            issues.append(f"CRITICAL: No {rel} relationships found")

    # ── Check 3: Company coverage ─────────────────────────────────────────────
    print("\n" + "=" * 60)
    print("Check 3: Company coverage — expected tickers vs found")
    print("=" * 60)
    missing_tickers = []
    for ticker in sorted(tickers_expected):
        r = s.run(
            "MATCH (c:LLMCompany {ticker: $t}) WHERE c.kg_batch = $b RETURN c.name AS n LIMIT 1",
            t=ticker, b=KG_BATCH
        )
        rec = r.single()
        if rec:
            print(f"  {ticker}: FOUND | name={str(rec['n'])[:50]!r}")
        else:
            print(f"  {ticker}: MISSING")
            missing_tickers.append(ticker)
    if missing_tickers:
        issues.append(f"Missing company nodes for: {missing_tickers}")

    # ── Check 4: Metric quality ───────────────────────────────────────────────
    print("\n" + "=" * 60)
    print("Check 4: Sample metric names — checking for junk")
    print("=" * 60)
    r = s.run(
        "MATCH (m:LLMFinancialMetric) WHERE m.kg_batch = $b RETURN m.canonical_name AS cn, m.display_name AS dn LIMIT 40",
        b=KG_BATCH
    )
    junk_metrics = []
    good_count = 0
    for rec in r:
        cn = rec["cn"] or ""
        dn = rec["dn"] or ""
        is_junk = bool(JUNK_METRIC_PATTERNS.match(cn))
        flag = " *** JUNK" if is_junk else ""
        try:
            print(f"  {cn[:50]!r} | {dn[:40]!r}{flag}")
        except Exception:
            print(f"  [encode error]")
        if is_junk:
            junk_metrics.append(cn)
        else:
            good_count += 1
    if len(junk_metrics) > 5:
        issues.append(f"Too many junk metrics ({len(junk_metrics)}): first few = {junk_metrics[:3]}")
    print(f"  Good metrics: {good_count} | Junk: {len(junk_metrics)}")

    # ── Check 5: Year distribution ────────────────────────────────────────────
    print("\n" + "=" * 60)
    print("Check 5: Fiscal year distribution")
    print("=" * 60)
    r = s.run(
        "MATCH (yr:LLMFiscalYear) WHERE yr.kg_batch = $b RETURN yr.year AS y ORDER BY y",
        b=KG_BATCH
    )
    years_found = [rec["y"] for rec in r]
    print(f"  Years extracted: {years_found}")
    out_of_range = [y for y in years_found if y is not None and (y < 2000 or y > 2030)]
    if out_of_range:
        issues.append(f"Out-of-range years found: {out_of_range}")

    # ── Check 6: Observation chain integrity ─────────────────────────────────
    print("\n" + "=" * 60)
    print("Check 6: Full observation chain (obs→company, obs→metric, obs→year)")
    print("=" * 60)
    r = s.run("""
MATCH (obs:LLMObservation)-[:LLM_MENTIONS_COMPANY]->(c:LLMCompany),
      (obs)-[:LLM_OBSERVES_METRIC]->(m:LLMFinancialMetric),
      (obs)-[:LLM_OBSERVED_IN_YEAR]->(yr:LLMFiscalYear)
WHERE obs.kg_batch = $b
RETURN count(obs) AS cnt
""", b=KG_BATCH)
    full_chain_count = r.single()["cnt"]
    print(f"  Observations with full chain (company+metric+year): {full_chain_count}")
    if full_chain_count == 0:
        issues.append("CRITICAL: No observations with full chain")

    r = s.run(
        "MATCH (obs:LLMObservation) WHERE obs.kg_batch = $b RETURN count(obs) AS cnt", b=KG_BATCH
    )
    total_obs = r.single()["cnt"]
    if total_obs > 0:
        pct = full_chain_count / total_obs * 100
        print(f"  Chain coverage: {full_chain_count}/{total_obs} = {pct:.1f}%")
        if pct < 50:
            issues.append(f"Low chain coverage: {pct:.1f}%")

    # ── Check 7: Cross-check against required_facts ───────────────────────────
    print("\n" + "=" * 60)
    print("Check 7: Cross-check key required_facts against extracted observations")
    print("=" * 60)
    facts = [json.loads(l) for l in FACTS_PATH.read_text(encoding="utf-8").splitlines() if l.strip()]
    # Sample: pick first 10 facts with clean metric names
    clean_facts = [
        f for f in facts
        if f.get("metric_canonical") and not JUNK_METRIC_PATTERNS.match(f.get("metric_canonical", ""))
        and f.get("year") and f.get("ticker")
    ][:10]

    hit = 0
    for fact in clean_facts:
        ticker = fact["ticker"]
        mc = fact["metric_canonical"]
        year = int(fact["year"]) if fact["year"] else None
        if not year:
            continue
        words = [w for w in mc.split("_") if len(w) >= 4]
        if not words:
            continue
        w1, w2 = words[0], (words[1] if len(words) > 1 else words[0])
        r = s.run("""
MATCH (obs:LLMObservation)-[:LLM_MENTIONS_COMPANY]->(c:LLMCompany {ticker: $t}),
      (obs)-[:LLM_OBSERVES_METRIC]->(m:LLMFinancialMetric),
      (obs)-[:LLM_OBSERVED_IN_YEAR]->(yr:LLMFiscalYear {year: $yr})
WHERE obs.kg_batch = $b
  AND toLower(m.canonical_name) CONTAINS $w1
  AND toLower(m.canonical_name) CONTAINS $w2
RETURN obs.value AS v, m.canonical_name AS mc LIMIT 1
""", t=ticker, yr=year, b=KG_BATCH, w1=w1, w2=w2)
        rec = r.single()
        status = "HIT" if rec else "MISS"
        if rec:
            hit += 1
        try:
            print(f"  [{status}] {ticker} | {mc[:40]} | year={year}")
        except Exception:
            pass

    print(f"  Required facts cross-check: {hit}/{len(clean_facts)} found")
    if len(clean_facts) > 0 and hit / len(clean_facts) < 0.3:
        issues.append(f"Low required_facts cross-check: only {hit}/{len(clean_facts)} found")

    # ── Check 8: DatasetCase coverage ────────────────────────────────────────
    print("\n" + "=" * 60)
    print("Check 8: LLMDatasetCase coverage")
    print("=" * 60)
    missing_cases = []
    for case_id in sorted(case_ids_expected):
        r = s.run(
            "MATCH (dc:LLMDatasetCase {case_id: $cid}) WHERE dc.kg_batch = $b RETURN dc LIMIT 1",
            cid=case_id, b=KG_BATCH
        )
        if not r.single():
            missing_cases.append(case_id)
    print(f"  Cases found: {len(case_ids_expected) - len(missing_cases)}/{len(case_ids_expected)}")
    if missing_cases:
        print(f"  Missing cases: {missing_cases}")
        if len(missing_cases) > 10:
            issues.append(f"Too many missing LLMDatasetCase nodes: {len(missing_cases)}")

driver.close()

# ── Summary ───────────────────────────────────────────────────────────────────
print("\n" + "=" * 60)
print("VERIFICATION SUMMARY")
print("=" * 60)
if not issues:
    print("  PASS — No issues found. LLM IE KG looks good.")
else:
    print(f"  {len(issues)} issue(s) found:")
    for i, issue in enumerate(issues, 1):
        print(f"  {i}. {issue}")

print("\nDone.")
