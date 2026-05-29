"""Deep audit of Neo4j KG structure to identify construction issues."""
import os
import sys, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
from neo4j import GraphDatabase

NEO4J_URI = os.environ.get("NEO4J_URI", "bolt://localhost:7687")
NEO4J_USERNAME = os.environ.get("NEO4J_USERNAME") or os.environ.get("NEO4J_USER", "neo4j")
NEO4J_PASSWORD = os.environ["NEO4J_PASSWORD"]
NEO4J_DATABASE = os.environ.get("NEO4J_DATABASE", "neo4j")

driver = GraphDatabase.driver(NEO4J_URI, auth=(NEO4J_USERNAME, NEO4J_PASSWORD))
with driver.session(database=NEO4J_DATABASE) as s:

    print("=" * 60)
    print("1. KGEntity label distribution")
    print("=" * 60)
    r = s.run("MATCH (n:KGEntity) RETURN n.label AS lbl, count(*) AS cnt ORDER BY cnt DESC")
    for rec in r:
        print(f"  {rec['lbl']}: {rec['cnt']}")

    print("\n" + "=" * 60)
    print("2. Relationship type distribution")
    print("=" * 60)
    r = s.run("MATCH ()-[r]->() RETURN type(r) AS t, count(*) AS cnt ORDER BY cnt DESC LIMIT 20")
    for rec in r:
        print(f"  {rec['t']}: {rec['cnt']}")

    print("\n" + "=" * 60)
    print("3. Company nodes — all tickers and names (first 50)")
    print("=" * 60)
    r = s.run("""
MATCH (c:KGEntity) WHERE c.label = 'Company'
RETURN c.ticker AS ticker, c.name AS name, c.cik AS cik
ORDER BY c.ticker
LIMIT 50
""")
    for rec in r:
        try:
            print(f"  ticker={rec['ticker']!r:12} | name={str(rec['name'])[:60]!r}")
        except Exception:
            print(f"  ticker={rec['ticker']!r:12} | [encode error]")

    print("\n" + "=" * 60)
    print("4. DatasetCase nodes — structure")
    print("=" * 60)
    r = s.run("MATCH (n:KGEntity) WHERE n.label = 'DatasetCase' RETURN keys(n) AS props LIMIT 1")
    for rec in r:
        print(f"  DatasetCase props: {rec['props']}")
    r = s.run("""
MATCH (n:KGEntity) WHERE n.label = 'DatasetCase'
RETURN n.case_id AS case_id, n.source_dataset AS src, n.ticker AS ticker
LIMIT 10
""")
    for rec in r:
        print(f"  case_id={rec['case_id']!r} | dataset={rec['src']!r} | ticker={rec['ticker']!r}")

    print("\n" + "=" * 60)
    print("5. DatasetCase→Company chain (HAS_OBSERVATION, MENTIONS_COMPANY)")
    print("=" * 60)
    r = s.run("""
MATCH (dc:KGEntity)-[r1]->(obs:KGEntity)-[r2]->(c:KGEntity)
WHERE dc.label = 'DatasetCase' AND c.label = 'Company'
RETURN type(r1) AS rel1, type(r2) AS rel2, dc.case_id AS case, c.ticker AS ticker
LIMIT 10
""")
    count = 0
    for rec in r:
        print(f"  DatasetCase({rec['case']!r})-[{rec['rel1']}]->(Obs)-[{rec['rel2']}]->Company({rec['ticker']!r})")
        count += 1
    if count == 0:
        print("  No DatasetCase→Obs→Company path found")

    print("\n" + "=" * 60)
    print("6. How DatasetCases connect to anything")
    print("=" * 60)
    r = s.run("""
MATCH (dc:KGEntity)-[r]->(n:KGEntity)
WHERE dc.label = 'DatasetCase'
RETURN type(r) AS rel_type, n.label AS to_label, count(*) AS cnt
ORDER BY cnt DESC LIMIT 15
""")
    for rec in r:
        print(f"  DatasetCase -[{rec['rel_type']}]-> {rec['to_label']}: {rec['cnt']}")

    print("\n" + "=" * 60)
    print("7. Observation→Company (MENTIONS_COMPANY) sample — with company details")
    print("=" * 60)
    r = s.run("""
MATCH (obs:KGEntity)-[:MENTIONS_COMPANY]->(c:KGEntity)
WHERE c.label = 'Company' AND c.ticker IS NOT NULL AND c.ticker <> ''
RETURN obs.case_id AS case_id, obs.batch_id AS batch, c.ticker AS ticker, c.name AS cname
LIMIT 10
""")
    count = 0
    for rec in r:
        try:
            print(f"  obs.case_id={rec['case_id']!r} | obs.batch={rec['batch']!r} | ticker={rec['ticker']!r} | cname={str(rec['cname'])[:50]!r}")
        except Exception:
            print(f"  [encode error]")
        count += 1
    if count == 0:
        print("  No MENTIONS_COMPANY with non-empty ticker found")

    print("\n" + "=" * 60)
    print("8. Do any Observations have metric+year data?")
    print("=" * 60)
    r = s.run("""
MATCH (obs:KGEntity)-[:OBSERVES_METRIC]->(m:KGEntity),
      (obs)-[:OBSERVED_IN_YEAR]->(yr:KGEntity)
WHERE obs.label = 'Observation'
RETURN obs.case_id AS case_id, m.name AS metric, yr.year AS year, obs.value AS val
LIMIT 10
""")
    count = 0
    for rec in r:
        try:
            print(f"  case_id={rec['case_id']!r} | metric={str(rec['metric'])[:40]!r} | year={rec['year']!r} | val={rec['val']!r}")
        except Exception:
            print(f"  [encode error]")
        count += 1
    if count == 0:
        print("  No Observation with both OBSERVES_METRIC and OBSERVED_IN_YEAR")

    print("\n" + "=" * 60)
    print("9. Selected 7 cases: are they in the KG?")
    print("=" * 60)
    selected_7 = ["b7b8f21b", "0dc1584f", "800ca373", "8f7b5b57", "e7129c27", "379644c5", "e6b63fd8"]
    for cid in selected_7:
        r = s.run(
            "MATCH (n:KGEntity) WHERE n.label = 'DatasetCase' AND n.case_id CONTAINS $cid RETURN n.case_id AS cid LIMIT 1",
            cid=cid
        )
        rec = r.single()
        print(f"  {cid}: {'FOUND' if rec else 'NOT FOUND'} in KG")

    print("\n" + "=" * 60)
    print("10. Company nodes — are the selected 7 companies in there?")
    print("=" * 60)
    companies = [("RMD","ResMed"), ("AIZ","Assurant"), ("AEP","American Electric Power"),
                 ("SMCI","Super Micro"), ("MCO","Moody's"), ("DLTR","Dollar Tree"), ("ETSY","Etsy")]
    for ticker, name in companies:
        r = s.run(
            "MATCH (c:KGEntity) WHERE c.label = 'Company' AND c.ticker = $t RETURN c.name AS n LIMIT 1",
            t=ticker
        )
        rec = r.single()
        if rec:
            try:
                print(f"  {ticker}: FOUND | name={str(rec['n'])[:60]!r}")
            except Exception:
                print(f"  {ticker}: FOUND | [encode error]")
        else:
            print(f"  {ticker}: NOT FOUND")

    print("\n" + "=" * 60)
    print("11. Round 1 ready case: e7129c27 — full KG path")
    print("=" * 60)
    r = s.run("""
MATCH (dc:KGEntity)
WHERE dc.label = 'DatasetCase' AND dc.case_id CONTAINS 'e7129c27'
RETURN keys(dc) AS props, dc
LIMIT 1
""")
    for rec in r:
        d = dict(rec['dc'])
        print(f"  DatasetCase props: {rec['props']}")
        for k in ['case_id', 'ticker', 'source_dataset', 'batch_id']:
            print(f"    {k}: {d.get(k)!r}")

    print("\n" + "=" * 60)
    print("12. Metric node: how does it connect to Company?")
    print("=" * 60)
    r = s.run("""
MATCH (c:KGEntity)-[r1*1..4]->(m:KGEntity)
WHERE c.label = 'Company' AND c.ticker = 'MCO' AND m.label = 'Metric'
RETURN [rel in r1 | type(rel)] AS path, m.name AS metric
LIMIT 5
""")
    count = 0
    for rec in r:
        try:
            print(f"  path={rec['path']} | metric={str(rec['metric'])[:50]!r}")
        except Exception:
            print(f"  [encode error]")
        count += 1
    if count == 0:
        print("  No Company→Metric path for MCO")

    print("\n" + "=" * 60)
    print("13. NormalizedMetric nodes")
    print("=" * 60)
    r = s.run("""
MATCH (n:KGEntity) WHERE n.label = 'NormalizedMetric'
RETURN keys(n) AS props, n.name AS name, n.kg_id AS kid
LIMIT 10
""")
    for rec in r:
        try:
            print(f"  name={str(rec['name'])[:60]!r} | kid={rec['kid']!r} | props={rec['props']}")
        except Exception:
            print(f"  [encode error]")

    print("\n" + "=" * 60)
    print("14. CompanyAlias nodes")
    print("=" * 60)
    r = s.run("""
MATCH (n:KGEntity) WHERE n.label = 'CompanyAlias'
RETURN keys(n) AS props, n.name AS name, n.kg_id AS kid
LIMIT 10
""")
    for rec in r:
        try:
            print(f"  name={str(rec['name'])[:60]!r} | kid={rec['kid']!r}")
        except Exception:
            print(f"  [encode error]")

driver.close()
print("\nDone")
