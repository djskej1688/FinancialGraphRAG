import os
import sys
import io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

from neo4j import GraphDatabase

NEO4J_URI = os.environ.get("NEO4J_URI", "bolt://localhost:7687")
NEO4J_USERNAME = os.environ.get("NEO4J_USERNAME") or os.environ.get("NEO4J_USER", "neo4j")
NEO4J_PASSWORD = os.environ["NEO4J_PASSWORD"]
NEO4J_DATABASE = os.environ.get("NEO4J_DATABASE", "neo4j")

driver = GraphDatabase.driver(NEO4J_URI, auth=(NEO4J_USERNAME, NEO4J_PASSWORD))
with driver.session(database=NEO4J_DATABASE) as s:
    # Company-to-Obs connection pattern via MENTIONS_COMPANY
    r = s.run("""
MATCH (obs:KGEntity)-[:MENTIONS_COMPANY]->(c:KGEntity)
WHERE c.label = 'Company'
RETURN c.ticker AS ticker, c.name AS name, obs.metric AS metric_raw, obs.year AS yr, obs.value AS val
LIMIT 10
""")
    for rec in r:
        print(f"Ticker={rec['ticker']} | metric={rec['metric_raw']} | year={rec['yr']} | val={rec['val']}")

    # Full chain: ticker -> obs -> metric node -> year
    r = s.run("""
MATCH (obs:KGEntity)-[:MENTIONS_COMPANY]->(c:KGEntity),
      (obs)-[:OBSERVES_METRIC]->(m:KGEntity),
      (obs)-[:OBSERVED_IN_YEAR]->(yr:KGEntity)
WHERE c.label = 'Company' AND m.label = 'Metric' AND yr.label = 'Year'
RETURN c.ticker AS ticker, m.name AS metric_name, yr.year AS year
LIMIT 10
""")
    print("--- Full chain ---")
    for rec in r:
        print(f"ticker={rec['ticker']} | metric={rec['metric_name']} | year={rec['year']}")

    # All tickers present in KG (with coverage)
    r = s.run("""
MATCH (c:KGEntity)
WHERE c.label = 'Company' AND c.ticker IS NOT NULL AND c.ticker <> ''
RETURN c.ticker AS ticker, c.name AS name
ORDER BY c.ticker
""")
    print("--- All tickers ---")
    for rec in r:
        try:
            print(f"  {rec['ticker']}: {rec['name']}")
        except Exception:
            print(f"  {rec['ticker']}: [encode error]")

driver.close()
print("Done")
