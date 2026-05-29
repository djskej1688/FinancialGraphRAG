"""
KG Coverage Check for required_facts.jsonl
Read-only Cypher queries against Neo4j.
Checks:
  1. Company/ticker node exists
  2. Metric node exists (by canonical name proximity)
  3. (Company)-[obs chain]->(Metric, year) observation exists
"""
import os, sys, io, json, re
from pathlib import Path
from collections import defaultdict
from neo4j import GraphDatabase

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

# ── paths ──────────────────────────────────────────────────────────────────────
FACTS_PATH = Path("C:/Users/USER/Documents/graphRAG/seocho/outputs/round3b_recovery/required_facts.jsonl")
REPORT_PATH = Path("C:/Users/USER/Documents/graphRAG/seocho/outputs/round3b_recovery/kg_coverage_report.md")

# ── Neo4j ──────────────────────────────────────────────────────────────────────
NEO4J_URI = os.environ.get("NEO4J_URI", "bolt://localhost:7687")
NEO4J_USER = os.environ.get("NEO4J_USERNAME") or os.environ.get("NEO4J_USER", "neo4j")
NEO4J_PASS = os.environ["NEO4J_PASSWORD"]
NEO4J_DB = os.environ.get("NEO4J_DATABASE", "neo4j")

# ── helpers ───────────────────────────────────────────────────────────────────
def slugify(text: str) -> str:
    """Normalize a metric name to snake_case slug for comparison."""
    return re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_")


def slug_words(slug: str) -> list[str]:
    """Return significant words from a snake_case slug (≥4 chars)."""
    return [w for w in slug.split("_") if len(w) >= 4]


# ── load facts ────────────────────────────────────────────────────────────────
print("Loading required_facts.jsonl ...")
facts = []
with open(FACTS_PATH, encoding="utf-8") as f:
    for line in f:
        line = line.strip()
        if line:
            facts.append(json.loads(line))

print(f"  Loaded {len(facts)} facts")

# unique (ticker, metric_canonical, year) combos
combos: dict[tuple, dict] = {}
for fact in facts:
    ticker = fact.get("ticker") or ""
    company = fact.get("company") or ""
    mc = fact.get("metric_canonical") or ""
    year = fact.get("year")
    key = (ticker, mc, year)
    if key not in combos:
        combos[key] = {"ticker": ticker, "company": company, "metric_canonical": mc, "year": year}

print(f"  Unique (ticker, metric_canonical, year) combos: {len(combos)}")

# unique tickers
tickers_needed = sorted({v["ticker"] for v in combos.values() if v["ticker"]})
print(f"  Unique tickers: {tickers_needed}")

# ── connect Neo4j ─────────────────────────────────────────────────────────────
print("\nConnecting to Neo4j ...")
driver = GraphDatabase.driver(NEO4J_URI, auth=(NEO4J_USER, NEO4J_PASS))

# ── Check 1: company node existence ──────────────────────────────────────────
print("Check 1: Company nodes ...")
company_exists: dict[str, bool] = {}
company_name_map: dict[str, str] = {}   # ticker -> kg name

with driver.session(database=NEO4J_DB) as s:
    for ticker in tickers_needed:
        # primary: exact ticker match
        r = s.run(
            "MATCH (c:KGEntity) WHERE c.label = 'Company' AND c.ticker = $t RETURN c.name AS n, c.ticker AS t LIMIT 1",
            t=ticker,
        )
        rec = r.single()
        if rec:
            company_exists[ticker] = True
            company_name_map[ticker] = rec["n"]
            continue

        # fallback: ticker as part of company name (case-insensitive)
        r = s.run(
            "MATCH (c:KGEntity) WHERE c.label = 'Company' AND toUpper(c.ticker) = $t RETURN c.name AS n, c.ticker AS t LIMIT 1",
            t=ticker.upper(),
        )
        rec = r.single()
        if rec:
            company_exists[ticker] = True
            company_name_map[ticker] = rec["n"]
        else:
            company_exists[ticker] = False
            company_name_map[ticker] = ""
        print(f"  {ticker}: {'FOUND' if company_exists[ticker] else 'NOT FOUND'} | kg_name={company_name_map[ticker][:60]!r}")

# ── Check 2: metric node existence ───────────────────────────────────────────
print("\nCheck 2: Metric nodes ...")
metric_exists: dict[str, bool] = {}
metric_kg_names: dict[str, str] = {}

unique_metrics = sorted({v["metric_canonical"] for v in combos.values() if v["metric_canonical"]})

with driver.session(database=NEO4J_DB) as s:
    for mc in unique_metrics:
        # Try exact slug match against normalized KG metric name
        r = s.run(
            """
MATCH (m:KGEntity) WHERE m.label = 'Metric'
  AND toLower(replace(replace(m.name, ' ', '_'), '-', '_')) = $mc
RETURN m.name AS n LIMIT 1
""",
            mc=mc,
        )
        rec = r.single()
        if rec:
            metric_exists[mc] = True
            metric_kg_names[mc] = rec["n"]
            continue

        # Try word-based partial match using significant words
        words = slug_words(mc)
        if not words:
            metric_exists[mc] = False
            metric_kg_names[mc] = ""
            continue

        # Build case-insensitive contains check for top 2 words
        w1 = words[0]
        w2 = words[1] if len(words) > 1 else words[0]
        r = s.run(
            """
MATCH (m:KGEntity) WHERE m.label = 'Metric'
  AND toLower(m.name) CONTAINS $w1
  AND toLower(m.name) CONTAINS $w2
RETURN m.name AS n LIMIT 1
""",
            w1=w1, w2=w2,
        )
        rec = r.single()
        if rec:
            metric_exists[mc] = True
            metric_kg_names[mc] = rec["n"]
        else:
            metric_exists[mc] = False
            metric_kg_names[mc] = ""

found_m = sum(1 for v in metric_exists.values() if v)
print(f"  Metrics found: {found_m}/{len(unique_metrics)}")

# ── Check 3: Company-Metric-Year observation chain ───────────────────────────
print("\nCheck 3: Company-Metric-Year observation chain ...")
# Pattern:
#   (obs)-[:MENTIONS_COMPANY]->(company {ticker})
#   (obs)-[:OBSERVES_METRIC]->(metric)  where metric.name fuzzy matches mc
#   (obs)-[:OBSERVED_IN_YEAR]->(year {year: Y})

obs_chain_exists: dict[tuple, bool] = {}

with driver.session(database=NEO4J_DB) as s:
    for key, combo in combos.items():
        ticker, mc, year = combo["ticker"], combo["metric_canonical"], combo["year"]
        if not ticker or year is None:
            obs_chain_exists[key] = False
            continue

        yr_str = str(int(year))

        # Try fuzzy metric match
        words = slug_words(mc)
        if not words:
            obs_chain_exists[key] = False
            continue

        w1 = words[0]
        w2 = words[1] if len(words) > 1 else words[0]

        r = s.run(
            """
MATCH (obs:KGEntity)-[:MENTIONS_COMPANY]->(c:KGEntity),
      (obs)-[:OBSERVES_METRIC]->(m:KGEntity),
      (obs)-[:OBSERVED_IN_YEAR]->(yr:KGEntity)
WHERE c.label = 'Company' AND c.ticker = $ticker
  AND m.label = 'Metric'
  AND toLower(m.name) CONTAINS $w1
  AND toLower(m.name) CONTAINS $w2
  AND yr.year = $year
RETURN obs.name AS obs_name LIMIT 1
""",
            ticker=ticker, w1=w1, w2=w2, year=yr_str,
        )
        rec = r.single()
        obs_chain_exists[key] = rec is not None

chains_found = sum(1 for v in obs_chain_exists.values() if v)
print(f"  Chains found: {chains_found}/{len(obs_chain_exists)}")

driver.close()

# ── Build report ──────────────────────────────────────────────────────────────
print("\nBuilding coverage report ...")

total = len(combos)
full_coverage = []
partial_coverage = []  # company exists but metric/year missing
no_coverage = []       # company not found

missing_details = []   # (ticker, mc, year) where full coverage fails

for key, combo in sorted(combos.items(), key=lambda x: (x[0][0] or "", x[0][1] or "", x[0][2] if x[0][2] is not None else -1)):
    ticker, mc, year = combo["ticker"], combo["metric_canonical"], combo["year"]
    c_ok = company_exists.get(ticker, False)
    m_ok = metric_exists.get(mc, False)
    o_ok = obs_chain_exists.get(key, False)

    all_pass = c_ok and m_ok and o_ok

    if all_pass:
        full_coverage.append(key)
    elif not c_ok:
        no_coverage.append(key)
    else:
        partial_coverage.append(key)
        missing_details.append({
            "ticker": ticker,
            "metric_canonical": mc,
            "year": year,
            "company_found": c_ok,
            "metric_found": m_ok,
            "obs_chain_found": o_ok,
        })

# ── Write markdown report ─────────────────────────────────────────────────────
lines = []
lines.append("# KG Coverage Report — Round 3B Recovery")
lines.append("")
lines.append(f"**Source:** `outputs/round3b_recovery/required_facts.jsonl`")
lines.append(f"**Total facts:** {len(facts)}")
lines.append(f"**Unique (ticker, metric_canonical, year) combos checked:** {total}")
lines.append("")
lines.append("---")
lines.append("")
lines.append("## Summary")
lines.append("")
lines.append(f"| Check | Count | % |")
lines.append(f"|---|---|---|")
lines.append(f"| Full KG coverage (all 3 checks pass) | {len(full_coverage)} | {len(full_coverage)/total*100:.1f}% |")
lines.append(f"| Partial coverage (company exists, metric/year missing) | {len(partial_coverage)} | {len(partial_coverage)/total*100:.1f}% |")
lines.append(f"| No coverage (company node not found) | {len(no_coverage)} | {len(no_coverage)/total*100:.1f}% |")
lines.append("")
lines.append("---")
lines.append("")
lines.append("## Check 1 — Company Node Existence")
lines.append("")
lines.append("Cypher pattern: `MATCH (c:KGEntity {label:'Company'}) WHERE c.ticker = $ticker`")
lines.append("")
lines.append("| Ticker | Found | KG Name (first 80 chars) |")
lines.append("|---|---|---|")
for t in tickers_needed:
    found = "✓" if company_exists.get(t) else "✗"
    kg_name = (company_name_map.get(t) or "")[:80]
    lines.append(f"| {t} | {found} | {kg_name} |")
lines.append("")
lines.append("---")
lines.append("")
lines.append("## Check 2 — Metric Node Existence")
lines.append("")
lines.append("Cypher pattern: `MATCH (m:KGEntity {label:'Metric'}) WHERE toLower(m.name) fuzzy-matches $metric_canonical`")
lines.append("")
lines.append("| metric_canonical | Found | KG Metric Name (first 80 chars) |")
lines.append("|---|---|---|")
for mc in unique_metrics:
    found = "✓" if metric_exists.get(mc) else "✗"
    kg_name = (metric_kg_names.get(mc) or "")[:80]
    lines.append(f"| {mc} | {found} | {kg_name} |")
lines.append("")
lines.append("---")
lines.append("")
lines.append("## Check 3 — Company–Metric–Year Observation Chain")
lines.append("")
lines.append("Cypher pattern:")
lines.append("```cypher")
lines.append("MATCH (obs)-[:MENTIONS_COMPANY]->(c {ticker}),")
lines.append("      (obs)-[:OBSERVES_METRIC]->(m),")
lines.append("      (obs)-[:OBSERVED_IN_YEAR]->(yr {year})")
lines.append("```")
lines.append("")
chains_true = {k for k, v in obs_chain_exists.items() if v}
chains_false = {k for k, v in obs_chain_exists.items() if not v}
lines.append(f"- Chains found: **{len(chains_true)}** / {len(obs_chain_exists)}")
lines.append(f"- Chains missing: **{len(chains_false)}**")
lines.append("")
lines.append("---")
lines.append("")
lines.append("## Missing (ticker, metric_canonical, year) Combinations")
lines.append("")
lines.append("All combos where full coverage fails:")
lines.append("")
lines.append("| ticker | metric_canonical | year | company_found | metric_found | obs_chain_found |")
lines.append("|---|---|---|---|---|---|")

all_failing = partial_coverage + no_coverage
for key in sorted(all_failing, key=lambda k: (k[0] or "", k[1] or "", k[2] if k[2] is not None else -1)):
    ticker, mc, year = key
    c_ok = "✓" if company_exists.get(ticker) else "✗"
    m_ok = "✓" if metric_exists.get(mc) else "✗"
    o_ok = "✓" if obs_chain_exists.get(key) else "✗"
    lines.append(f"| {ticker} | {mc} | {year} | {c_ok} | {m_ok} | {o_ok} |")

lines.append("")
lines.append("---")
lines.append("")
lines.append("## Coverage by Ticker")
lines.append("")
lines.append("| Ticker | Company (fact file) | Company node? | Combos checked | Full coverage | Partial | No coverage |")
lines.append("|---|---|---|---|---|---|---|")

# group combos by ticker
ticker_combos: dict[str, list] = defaultdict(list)
for key, combo in combos.items():
    ticker_combos[combo["ticker"]].append(key)

for ticker in tickers_needed:
    keys = ticker_combos[ticker]
    comp_name = next((v["company"] for v in combos.values() if v["ticker"] == ticker), "")
    c_node = "✓" if company_exists.get(ticker) else "✗"
    full = sum(1 for k in keys if k in full_coverage)
    partial = sum(1 for k in keys if k in partial_coverage)
    no = sum(1 for k in keys if k in no_coverage)
    lines.append(f"| {ticker} | {comp_name[:40]} | {c_node} | {len(keys)} | {full} | {partial} | {no} |")

lines.append("")

report_text = "\n".join(lines)
REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
REPORT_PATH.write_text(report_text, encoding="utf-8")
print(f"Report written to: {REPORT_PATH}")

# ── Console summary ───────────────────────────────────────────────────────────
print("\n" + "=" * 60)
print("COVERAGE SUMMARY")
print("=" * 60)
print(f"Total facts:                  {len(facts)}")
print(f"Unique combos checked:        {total}")
print(f"Full KG coverage:             {len(full_coverage)} ({len(full_coverage)/total*100:.1f}%)")
print(f"Partial coverage:             {len(partial_coverage)} ({len(partial_coverage)/total*100:.1f}%)")
print(f"No coverage (no company):     {len(no_coverage)} ({len(no_coverage)/total*100:.1f}%)")
print(f"Missing combos total:         {len(all_failing)}")
print(f"\nReport: {REPORT_PATH}")
