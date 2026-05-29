"""Final targeted KG checks for the audit report."""
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

    print("=== A. HOW MANY Company nodes have a valid ticker vs empty? ===")
    r = s.run("""
MATCH (c:KGEntity) WHERE c.label = 'Company'
RETURN
  sum(CASE WHEN c.ticker IS NOT NULL AND c.ticker <> '' THEN 1 ELSE 0 END) AS has_ticker,
  sum(CASE WHEN c.ticker IS NULL OR c.ticker = '' THEN 1 ELSE 0 END) AS no_ticker,
  count(*) AS total
""")
    for rec in r:
        print(f"  has_ticker={rec['has_ticker']} | no_ticker={rec['no_ticker']} | total={rec['total']}")

    print("\n=== B. Company nodes WITH ticker — are names proper company names? ===")
    r = s.run("""
MATCH (c:KGEntity) WHERE c.label = 'Company' AND c.ticker IS NOT NULL AND c.ticker <> ''
RETURN c.ticker AS ticker, c.name AS name
ORDER BY c.ticker
LIMIT 30
""")
    for rec in r:
        try:
            print(f"  {rec['ticker']:8} | {str(rec['name'])[:70]}")
        except Exception:
            print(f"  {rec['ticker']:8} | [encode error]")

    print("\n=== C. What are the 17 selected cases with typed relationships? ===")
    r = s.run("""
MATCH (dc:KGEntity)-[:MENTIONS_COMPANY]->(c:KGEntity)
WHERE dc.label = 'DatasetCase' AND c.label = 'Company'
RETURN dc.case_id AS case_id, c.ticker AS ticker, c.name AS cname
""")
    for rec in r:
        try:
            print(f"  {rec['case_id']!r:30} | {rec['ticker']!r:8} | {str(rec['cname'])[:50]!r}")
        except Exception:
            print(f"  [encode error]")

    print("\n=== D. KG_RELATED: what does it actually connect? (sample) ===")
    r = s.run("""
MATCH (a:KGEntity)-[:KG_RELATED]->(b:KGEntity)
RETURN a.label AS from_lbl, b.label AS to_lbl, count(*) AS cnt
ORDER BY cnt DESC LIMIT 10
""")
    for rec in r:
        print(f"  {rec['from_lbl']} -[KG_RELATED]-> {rec['to_lbl']}: {rec['cnt']}")

    print("\n=== E. Do any round3b cases exist as DatasetCase nodes? ===")
    round3b_ids = [
        "baseline_control_002_bc20b319", "round3_dev_001_da2a2fad", "round3_dev_002_1e2ee4b4",
        "round3_dev_004_11abd756", "round3_dev_006_8b9544ef", "round3_dev_008_e69805f4",
        "round3_dev_013_6c9047e6", "round3_dev_015_df730bd7", "round3_test_001_2529e04e",
        "round3_test_003_b8a1383c", "round3_test_006_08117364", "round3_test_008_19b392a0",
        "round3_test_010_6d2cfe43", "round3_test_015_536b783d", "round3_test_019_3734e04b",
        "round3_test_020_6ee222c0",
    ]
    found = 0
    for cid in round3b_ids:
        r = s.run(
            "MATCH (n:KGEntity) WHERE n.label = 'DatasetCase' AND n.case_id = $cid RETURN n.case_id LIMIT 1",
            cid=cid
        )
        if r.single():
            found += 1
            print(f"  FOUND: {cid}")
    if found == 0:
        print(f"  NONE of {len(round3b_ids)} round3b case_ids found as DatasetCase nodes in KG")

    print("\n=== F. Observation value quality (sample) ===")
    r = s.run("""
MATCH (obs:KGEntity)-[:OBSERVES_METRIC]->(m:KGEntity),
      (obs)-[:OBSERVED_IN_YEAR]->(yr:KGEntity),
      (obs)-[:MENTIONS_COMPANY]->(c:KGEntity)
WHERE c.label = 'Company' AND c.ticker = 'MCO'
RETURN m.name AS metric, yr.year AS year, obs.value AS val
LIMIT 10
""")
    count = 0
    for rec in r:
        try:
            print(f"  MCO | metric={str(rec['metric'])[:40]!r} | year={rec['year']!r} | val={rec['val']!r}")
        except Exception:
            print(f"  [encode error]")
        count += 1
    if count == 0:
        print("  No MCO observations with full chain found")

    print("\n=== G. Does any Observation node have numeric_value populated? ===")
    r = s.run("""
MATCH (obs:KGEntity) WHERE obs.label = 'Observation' AND obs.numeric_value IS NOT NULL
RETURN obs.numeric_value AS nv, obs.metric AS m, obs.year AS yr
LIMIT 10
""")
    count = 0
    for rec in r:
        try:
            print(f"  numeric_value={rec['nv']!r} | metric={str(rec['m'])[:40]!r} | year={rec['yr']!r}")
        except Exception:
            print(f"  [encode error]")
        count += 1
    if count == 0:
        print("  No Observation with numeric_value populated")

    print("\n=== H. Airport/geography data — what is it? ===")
    r = s.run("MATCH (a:Airport) RETURN keys(a) AS props LIMIT 1")
    for rec in r:
        print(f"  Airport props: {rec['props']}")
    r = s.run("MATCH (c:Country) RETURN c.name AS n LIMIT 5")
    for rec in r:
        print(f"  Country: {rec['n']!r}")

driver.close()
print("\nDone")
