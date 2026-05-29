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
    # Check ETSY observations via full chain
    r = s.run("""
MATCH (obs:KGEntity)-[:MENTIONS_COMPANY]->(c:KGEntity),
      (obs)-[:OBSERVES_METRIC]->(m:KGEntity),
      (obs)-[:OBSERVED_IN_YEAR]->(yr:KGEntity)
WHERE c.label = 'Company' AND c.ticker = 'ETSY'
RETURN c.ticker AS ticker, m.name AS metric_name, yr.year AS year
LIMIT 10
""")
    count = 0
    for rec in r:
        print(f"ETSY chain: metric={rec['metric_name']} | year={rec['year']}")
        count += 1
    print(f"ETSY full chain results: {count}")

    # Check ETSY observations via Observation node directly
    r = s.run("""
MATCH (obs:KGEntity)-[:MENTIONS_COMPANY]->(c:KGEntity)
WHERE c.label = 'Company' AND c.ticker = 'ETSY'
RETURN obs.metric AS metric, obs.year AS year, obs.value AS val
LIMIT 10
""")
    count = 0
    for rec in r:
        print(f"ETSY obs direct: metric={rec['metric']} | year={rec['year']} | val={rec['val']}")
        count += 1
    print(f"ETSY obs direct results: {count}")

    # Check DUK
    r = s.run("""
MATCH (c:KGEntity)
WHERE c.label = 'Company' AND c.ticker = 'DUK'
RETURN c.ticker, c.name
""")
    count = 0
    for rec in r:
        print(f"DUK company: {rec['c.ticker']} / {rec['c.name']}")
        count += 1
    if count == 0:
        print("DUK: No Company node found")

    # Check WDC
    r = s.run("""
MATCH (c:KGEntity)
WHERE c.label = 'Company' AND c.ticker = 'WDC'
RETURN c.ticker, c.name
""")
    count = 0
    for rec in r:
        print(f"WDC company: {rec['c.ticker']} / {rec['c.name']}")
        count += 1
    if count == 0:
        print("WDC: No Company node found")

    # Check CINC vs CINF
    r = s.run("""
MATCH (c:KGEntity)
WHERE c.label = 'Company' AND (c.ticker IN ['CINC', 'CINF'])
RETURN c.ticker, c.name
""")
    for rec in r:
        print(f"CIN*: {rec['c.ticker']} / {rec['c.name']}")

    # Check DUK by name
    r = s.run("""
MATCH (c:KGEntity)
WHERE c.label = 'Company' AND toLower(c.name) CONTAINS 'duke'
RETURN c.ticker, c.name
""")
    for rec in r:
        print(f"Duke company: {rec['c.ticker']} / {rec['c.name']}")

driver.close()
print("Done")
