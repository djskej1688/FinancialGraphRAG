import os

from neo4j import GraphDatabase

NEO4J_URI = os.environ.get("NEO4J_URI", "bolt://localhost:7687")
NEO4J_USERNAME = os.environ.get("NEO4J_USERNAME") or os.environ.get("NEO4J_USER", "neo4j")
NEO4J_PASSWORD = os.environ["NEO4J_PASSWORD"]
NEO4J_DATABASE = os.environ.get("NEO4J_DATABASE", "neo4j")

driver = GraphDatabase.driver(NEO4J_URI, auth=(NEO4J_USERNAME, NEO4J_PASSWORD))
with driver.session(database=NEO4J_DATABASE) as s:
    # How does Company connect to Observations?
    r = s.run("""
MATCH (c:KGEntity)-[r]-(obs:KGEntity)
WHERE c.label = 'Company' AND obs.label = 'Observation'
RETURN type(r) AS rel_type, startNode(r).label AS from_lbl, endNode(r).label AS to_lbl
LIMIT 10
""")
    for rec in r:
        print("Company<->Obs:", dict(rec))

    # Check MENTIONS_COMPANY direction
    r = s.run("""
MATCH (obs:KGEntity)-[r:MENTIONS_COMPANY]->(c:KGEntity)
WHERE c.label = 'Company'
RETURN c.name AS comp, c.ticker AS tick, obs.metric AS metric, obs.year AS yr
LIMIT 5
""")
    for rec in r:
        print("MENTIONS_COMPANY:", dict(rec))

    # Check specific ticker presence
    r = s.run("""
MATCH (c:KGEntity)
WHERE c.label = 'Company' AND c.ticker IS NOT NULL
RETURN c.ticker AS ticker, c.name AS name
ORDER BY c.ticker
LIMIT 30
""")
    for rec in r:
        print("Ticker:", rec["ticker"], "| Name:", rec["name"])

driver.close()
print("Done")
