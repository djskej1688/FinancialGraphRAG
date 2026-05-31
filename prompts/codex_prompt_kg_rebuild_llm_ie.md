# Codex Task: LLM-Based Knowledge Graph Rebuild

**Last updated by Claude:** 2026-05-28  
**State file:** `outputs/kg_rebuild_llm_ie/state.json`  
**Implementation target:** `scripts/kg_rebuild_llm_ie.py`  
**Verification script (run by Claude after completion):** `scripts/verify_llm_ie_kg.py`

---

## Goal

Build a properly typed financial knowledge graph in Neo4j from 25 evaluation case evidence texts.

**Why:** The existing KG uses flat `KGEntity` nodes with `KG_RELATED` for everything — no typed labels, no semantic relationships. This breaks graph retrieval (no traversal target, no entity resolution). The LLM-based approach extracts real entities (Company, FinancialMetric, Observation) and semantic relationships from the financial text.

**What NOT to touch:**
- `outputs/round3_eval_runs/locked_test_v3_2_track_b_20260528_145253/` — locked test run, read-only
- Existing KGEntity / KG_RELATED nodes in Neo4j — do not delete, do not modify

---

## Input Files

### Primary input: 25 Track B evaluation cases
```
outputs/round3_dual_track_eval_prep/track_b_shadow_overlay/shadow_overlay_eval_ready_cases.jsonl
```
Each line is a JSON object with these fields (relevant ones):
- `case_id` — unique case identifier (e.g., `round3_dev_010_4a66fa95`)
- `ticker` — stock ticker (e.g., `MDLZ`)
- `company` — company name (e.g., `Mondelēz International, Inc`)
- `evidence_text` — raw financial text excerpt from 10-K filing (THIS is what you extract from)
- `required_facts` — array of ground-truth fact objects (use for quality verification, NOT as extraction input)
- `years` — array of fiscal years relevant to this case

### Ground truth for verification: required_facts in shadow overlay
```
outputs/round3_dual_track_eval_prep/track_b_shadow_overlay/shadow_overlay_required_facts.jsonl
```

### Environment / credentials
```
.env  (load with python-dotenv or os.environ)
```
Keys used:
- `NEO4J_URI` from environment or local `.env` only; do not commit the value
- `NEO4J_USERNAME` from environment or local `.env` only
- `NEO4J_PASSWORD` from environment or local `.env` only; do not commit the value
- `NEO4J_DATABASE` from environment or local `.env` only
- `OPENAI_API_KEY` — must be set in environment (NOT in .env file, not in code)

---

## Output: Neo4j Node/Relationship Schema

### New batch identifier
```
KG_BATCH = "kg-llm-ie-v1-20260528"
```
Set `kg_batch` property on every node you create.

### Node types (use proper Neo4j labels, NOT KGEntity)

**(:LLMCompany)**
```
{
  ticker: str,           # e.g. "MDLZ"
  name: str,             # e.g. "Mondelēz International, Inc."
  kg_batch: str,
  created_at: str        # ISO timestamp
}
```
MERGE key: `ticker`

**(:LLMFinancialMetric)**
```
{
  canonical_name: str,   # snake_case, e.g. "net_revenue" / "total_employees"
  display_name: str,     # human-readable, e.g. "Net Revenue" / "Total Employees"
  unit: str,             # "currency_millions" | "count" | "percentage" | "currency_billions" | "ratio"
  kg_batch: str,
  created_at: str
}
```
MERGE key: `canonical_name`  
**Important:** canonical_name must be a real financial metric name, NOT a parsing artifact like `at_the_end_of` or `march` or `generation_x_birth_years_between`.

**(:LLMFiscalYear)**
```
{
  year: int,             # e.g. 2023
  kg_batch: str,
  created_at: str
}
```
MERGE key: `year`

**(:LLMObservation)**
```
{
  obs_id: str,           # "{case_id}___{ticker}_{canonical_name}_{year}"
  ticker: str,
  metric_canonical: str,
  year: int,
  value: float,          # numeric value
  unit: str,
  evidence_quote: str,   # exact substring from evidence_text that contains this value
  case_id: str,
  kg_batch: str,
  created_at: str
}
```
MERGE key: `obs_id`

**(:LLMDatasetCase)**
```
{
  case_id: str,
  ticker: str,
  split: str,            # e.g. "round3_dev"
  kg_batch: str,
  created_at: str
}
```
MERGE key: `case_id`

### Relationship types

```
(obs:LLMObservation)-[:LLM_MENTIONS_COMPANY]->(company:LLMCompany)
(obs:LLMObservation)-[:LLM_OBSERVES_METRIC]->(metric:LLMFinancialMetric)
(obs:LLMObservation)-[:LLM_OBSERVED_IN_YEAR]->(year:LLMFiscalYear)
(case:LLMDatasetCase)-[:LLM_HAS_OBSERVATION]->(obs:LLMObservation)
```

---

## Implementation: `scripts/kg_rebuild_llm_ie.py`

### Required packages (install if missing)
```
pip install python-dotenv neo4j openai
```

### High-level algorithm

```python
for each case in shadow_overlay_eval_ready_cases.jsonl:
    1. Call OpenAI to extract entities from case["evidence_text"]
    2. Parse the structured output
    3. Validate: skip observations where metric looks like a parsing artifact
    4. Write nodes + relationships to Neo4j using MERGE
    5. Update state.json with progress
```

### OpenAI extraction call

Use `gpt-4o-mini` model (cheap, fast). Use JSON structured output mode.

**System prompt:**
```
You are a financial information extraction expert. Given a financial text excerpt from a 10-K SEC filing, extract structured financial observations.

For each numeric value mentioned, extract:
- The exact metric it represents (use standard financial terminology)
- The fiscal year it applies to
- The numeric value
- The unit (currency_millions, count, percentage, ratio)
- The exact quote from the text containing this value

Rules:
- Only extract metrics with actual numeric values
- metric_canonical must be a real financial metric name in snake_case (e.g., net_revenue, total_employees, gross_profit_margin)
- Do NOT create metric names from sentence fragments like "at_the_end_of", "during_the_first_quarter_of", etc.
- year must be an integer (e.g., 2023) — if the text says "fiscal year ended June 28, 2024", use 2024
- If the same metric appears across multiple years in a table, extract one observation per year
- evidence_quote must be a substring that actually appears in the input text
- Maximum 20 observations per case
```

**JSON schema for response:**
```json
{
  "type": "object",
  "properties": {
    "observations": {
      "type": "array",
      "items": {
        "type": "object",
        "required": ["metric_canonical", "metric_display", "year", "value", "unit", "evidence_quote"],
        "properties": {
          "metric_canonical": {"type": "string"},
          "metric_display": {"type": "string"},
          "year": {"type": "integer"},
          "value": {"type": "number"},
          "unit": {"type": "string", "enum": ["currency_millions", "currency_billions", "count", "percentage", "ratio", "currency_per_share"]},
          "evidence_quote": {"type": "string"}
        }
      }
    }
  },
  "required": ["observations"]
}
```

### Validation rules (filter out before Neo4j write)

Skip an observation if ANY of these is true:
- `metric_canonical` contains any of: `["at_the_", "during_the_", "_of_", "march_", "june_", "december_", "generation_x", "birth_year"]`
- `metric_canonical` length < 4 characters
- `year` < 2000 or `year` > 2030
- `value` is None or infinite
- `evidence_quote` is empty

### Neo4j write pattern (use MERGE, not CREATE)

```cypher
// Company
MERGE (c:LLMCompany {ticker: $ticker})
ON CREATE SET c.name = $name, c.kg_batch = $kg_batch, c.created_at = $created_at

// Metric
MERGE (m:LLMFinancialMetric {canonical_name: $canonical_name})
ON CREATE SET m.display_name = $display_name, m.unit = $unit, m.kg_batch = $kg_batch, m.created_at = $created_at

// Fiscal Year
MERGE (yr:LLMFiscalYear {year: $year})
ON CREATE SET yr.kg_batch = $kg_batch, yr.created_at = $created_at

// Observation
MERGE (obs:LLMObservation {obs_id: $obs_id})
ON CREATE SET obs.ticker = $ticker, obs.metric_canonical = $canonical_name,
              obs.year = $year, obs.value = $value, obs.unit = $unit,
              obs.evidence_quote = $evidence_quote, obs.case_id = $case_id,
              obs.kg_batch = $kg_batch, obs.created_at = $created_at

// Dataset Case
MERGE (dc:LLMDatasetCase {case_id: $case_id})
ON CREATE SET dc.ticker = $ticker, dc.split = $split, dc.kg_batch = $kg_batch, dc.created_at = $created_at

// Relationships
MERGE (obs)-[:LLM_MENTIONS_COMPANY]->(c)
MERGE (obs)-[:LLM_OBSERVES_METRIC]->(m)
MERGE (obs)-[:LLM_OBSERVED_IN_YEAR]->(yr)
MERGE (dc)-[:LLM_HAS_OBSERVATION]->(obs)
```

### State file updates

After processing each case, write updated state to `outputs/kg_rebuild_llm_ie/state.json`:
```json
{
  "phase": "extracting",
  "kg_batch": "kg-llm-ie-v1-20260528",
  "cases_total": 25,
  "cases_processed": 5,
  "cases_succeeded": ["round3_dev_010_4a66fa95", ...],
  "cases_failed": [],
  "nodes_created": {"companies": 12, "metrics": 34, "observations": 67, "years": 8, "dataset_cases": 5},
  "relationships_created": 201,
  "openai_calls": 5,
  "openai_tokens_used": 12500,
  "started_at": "2026-05-28T15:00:00Z",
  "completed_at": null,
  "last_error": null
}
```

When all cases are done (or all failed), set:
- `"phase": "done"` if at least 20/25 cases succeeded
- `"phase": "partial"` if 10-19 succeeded  
- `"phase": "failed"` if fewer than 10 succeeded
- `"completed_at": "<ISO timestamp>"`

### Error handling

- On OpenAI error for a single case: log to `cases_failed`, continue to next case
- On Neo4j write error: retry once, then log and continue
- On JSON parse error from OpenAI: log case_id + raw response to `outputs/kg_rebuild_llm_ie/extraction_errors.jsonl`, continue
- Write per-case extraction results to `outputs/kg_rebuild_llm_ie/extraction_results/` as `{case_id}.json`

---

## Output Directory Structure

```
outputs/kg_rebuild_llm_ie/
  state.json                          # live progress (Codex writes, Claude reads)
  extraction_results/
    round3_dev_010_4a66fa95.json      # raw LLM output per case
    round3_dev_019_a71c9a61.json
    ...
  extraction_errors.jsonl             # cases where extraction failed
  neo4j_write_log.jsonl               # one line per Neo4j MERGE operation
```

---

## Codex Handoff Signal

When done, write to `state.json`:
```json
{
  "phase": "done",
  "codex_handoff_message": "KG rebuild complete. {N} cases processed, {M} observations extracted. Run scripts/verify_llm_ie_kg.py to verify."
}
```

Claude will then run `scripts/verify_llm_ie_kg.py` to validate the output.

---

## What Claude Will Verify (do NOT implement this, it's for Claude)

Claude will run `scripts/verify_llm_ie_kg.py` which checks:
1. Node count by type (LLMCompany, LLMFinancialMetric, LLMObservation, LLMFiscalYear)
2. Relationship distribution
3. Sample metric names — are they real financial metric names?
4. Coverage: for each ticker in shadow_overlay_eval_ready_cases.jsonl, at least 1 LLMObservation exists
5. Year distribution — are extracted years in valid range (2019–2025)?
6. Cross-check against required_facts.jsonl: do key required metrics appear in the LLM-extracted graph?
7. Entity resolution: same ticker → same LLMCompany node (no duplicates)

---

## Checklist for Codex

- [ ] Read `shadow_overlay_eval_ready_cases.jsonl` (25 cases)
- [ ] Set up OpenAI client using `OPENAI_API_KEY` from environment
- [ ] Set up Neo4j driver from .env
- [ ] Process each case: LLM extract → validate → Neo4j write → state update
- [ ] Write `outputs/kg_rebuild_llm_ie/state.json` after each case
- [ ] Write per-case extraction JSON to `outputs/kg_rebuild_llm_ie/extraction_results/`
- [ ] Set `state.json["phase"] = "done"` and `completed_at` when finished
- [ ] Do NOT modify any existing KGEntity nodes
- [ ] Do NOT touch `outputs/round3_eval_runs/locked_test_v3_2_track_b_20260528_145253/`
