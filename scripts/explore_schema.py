import os

from neo4j import GraphDatabase

NEO4J_URI = os.environ.get("NEO4J_URI", "bolt://localhost:7687")
NEO4J_USERNAME = os.environ.get("NEO4J_USERNAME") or os.environ.get("NEO4J_USER", "neo4j")
NEO4J_PASSWORD = os.environ["NEO4J_PASSWORD"]
NEO4J_DATABASE = os.environ.get("NEO4J_DATABASE", "neo4j")

driver = GraphDatabase.driver(NEO4J_URI, auth=(NEO4J_USERNAME, NEO4J_PASSWORD))
with driver.session(database=NEO4J_DATABASE) as s:
    # Company properties
    r = s.run("MATCH (n:KGEntity) WHERE n.label = 'Company' RETURN keys(n) AS props LIMIT 1")
    for rec in r:
        print("Company props:", rec["props"])

    r = s.run("MATCH (n:KGEntity) WHERE n.label = 'Company' RETURN n.name AS name, n.kg_id AS kg_id LIMIT 10")
    for rec in r:
        print("Company:", dict(rec))

    # Metric properties
    r = s.run("MATCH (n:KGEntity) WHERE n.label = 'Metric' RETURN keys(n) AS props LIMIT 1")
    for rec in r:
        print("Metric props:", rec["props"])

    r = s.run("MATCH (n:KGEntity) WHERE n.label = 'Metric' RETURN n.name AS name, n.kg_id AS kg_id LIMIT 5")
    for rec in r:
        print("Metric:", dict(rec))

    # Observation properties
    r = s.run("MATCH (n:KGEntity) WHERE n.label = 'Observation' RETURN keys(n) AS props LIMIT 1")
    for rec in r:
        print("Obs props:", rec["props"])

    r = s.run("MATCH (n:KGEntity) WHERE n.label = 'Observation' RETURN n.name AS name, n.year AS year, n.ticker AS ticker, n.metric_canonical AS mc LIMIT 5")
    for rec in r:
        print("Obs:", dict(rec))

    # OBSERVES_METRIC pattern
    r = s.run("MATCH (a)-[r:OBSERVES_METRIC]->(b) RETURN a.label AS from_lbl, a.name AS from_name, b.label AS to_lbl, b.name AS to_name LIMIT 3")
    for rec in r:
        print("OBSERVES_METRIC:", dict(rec))

    # OBSERVED_IN_YEAR pattern
    r = s.run("MATCH (a)-[r:OBSERVED_IN_YEAR]->(b) RETURN a.label AS from_lbl, a.name AS from_name, b.label AS to_lbl, b.name AS to_name, b.year AS yr LIMIT 3")
    for rec in r:
        print("OBSERVED_IN_YEAR:", dict(rec))

    # Full path from Company to Metric
    r = s.run("MATCH (c:KGEntity)-[*1..4]-(m:KGEntity) WHERE c.label = 'Company' AND m.label = 'Metric' RETURN c.name AS company, m.name AS metric LIMIT 5")
    for rec in r:
        print("Company->Metric:", dict(rec))

driver.close()
print("Done")
