# Codex Task: Round 4 Eval — Real Neo4j LLM IE KG Retrieval

**Last updated by Claude:** 2026-05-28  
**State file:** `outputs/round4_neo4j_eval/state.json`  
**Output directory:** `outputs/round3_eval_runs/round4_llm_ie_kg_{YYYYMMDD_HHMMSS}/`  
**Base this on:** `scripts/round3_dev_dryrun_v3_2_clean.py` (read it in full before implementing)

---

## Goal

Run a new evaluation (Round 4) that retrieves graph facts directly from the Neo4j LLM IE KG,  
instead of from the local shadow overlay files (`shadow_overlay_required_facts.jsonl`).

**Why:** The locked test used shadow overlay (local JSONL file as "graph facts"). This tests whether  
the actual Neo4j graph retrieval pipeline produces useful structured facts for the model.

**Do NOT touch:**
- `outputs/round3_eval_runs/locked_test_v3_2_track_b_20260528_145253/` — locked, read-only
- Existing `KGEntity` / `KG_RELATED` nodes in Neo4j — do not modify
- `LLMObservation` / `LLMCompany` nodes — read-only queries only

---

## Input Files

### Cases (all 25 Track B shadow overlay cases)
```
outputs/round3_dual_track_eval_prep/track_b_shadow_overlay/shadow_overlay_eval_ready_cases.jsonl
```
Each case has: `case_id`, `ticker`, `company`, `evidence_text`, `question`, `split`, `years`

### Formula contracts (for scoring)
```
outputs/round3_eval_harness/formula_contract_v3_2_clean_dev/clean_dev_scorer_only_target_slot_contracts.jsonl
outputs/round3_eval_harness/formula_contract_v3_2_clean_dev/clean_dev_model_visible_formula_contracts.jsonl
```
And for round3b cases:
```
outputs/round3b_recovery/scorer_contracts.jsonl
outputs/round3b_recovery/repaired_cases.jsonl  (for model_visible_formula_contract field)
```

### Prompts (reuse existing)
```
outputs/round3_eval_harness/prompts/prompt_v3_2_system.md
outputs/round3_eval_harness/prompts/answer_format_spec_v3_2.md
outputs/round3_eval_harness/prompts/rounding_and_tolerance_rules_v3_2.md
```

### Reference: shadow overlay facts (for comparison baseline, NOT used as graph input in Round 4)
```
outputs/round3_dual_track_eval_prep/track_b_shadow_overlay/shadow_overlay_required_facts.jsonl
```

---

## New: Neo4j Graph Retrieval Function

Add this function to retrieve graph facts from the LLM IE KG:

```python
def load_neo4j_graph_facts(ticker: str, case_id: str, driver) -> list[dict]:
    """
    Query LLMObservation nodes for a given ticker from the LLM IE KG.
    Returns a list of fact dicts compatible with fact_table().
    """
    KG_BATCH = "kg-llm-ie-v1-20260528"
    with driver.session(database="neo4j") as s:
        r = s.run("""
MATCH (obs:LLMObservation)-[:LLM_MENTIONS_COMPANY]->(c:LLMCompany {ticker: $ticker}),
      (obs)-[:LLM_OBSERVES_METRIC]->(m:LLMFinancialMetric),
      (obs)-[:LLM_OBSERVED_IN_YEAR]->(yr:LLMFiscalYear)
WHERE obs.kg_batch = $batch
RETURN obs.obs_id AS obs_id,
       obs.value AS value,
       obs.unit AS unit,
       obs.evidence_quote AS evidence_quote,
       m.canonical_name AS metric_canonical,
       m.display_name AS metric_display,
       yr.year AS year
ORDER BY yr.year, m.canonical_name
""", ticker=ticker, batch=KG_BATCH)
        facts = []
        for rec in r:
            facts.append({
                "fact_id": rec["obs_id"],            # obs_id used as fact_id
                "metric_canonical": rec["metric_canonical"],
                "metric_raw": rec["metric_display"],
                "value": rec["value"],
                "year": rec["year"],
                "unit": rec["unit"] or "",
                "company": ticker,
                "ticker": ticker,
                "evidence_quote_exact": rec["evidence_quote"] or "",
                "fact_role": "component",
                "source_fact": True,
                "derived_answer_value": False,
            })
    return facts
```

---

## Methods for Round 4

Run these 3 methods for each case:

| Method name | vector_context | graph_facts source |
|---|---|---|
| `vector_only_v4` | evidence_text | (none) |
| `graph_neo4j_v4` | (none) | Neo4j LLM IE KG |
| `hybrid_neo4j_v4` | evidence_text | Neo4j LLM IE KG |

**Do NOT re-run** `graph_facts_only_v3_2` or `hybrid_vector_graph_v3_2` (they're in the locked test already).

---

## Prompt Building

Reuse the `build_prompt()` logic from `round3_dev_dryrun_v3_2_clean.py` exactly.  
Map new method names to context building:
```python
if method == "vector_only_v4":
    context = f"TEXT_CONTEXT\n{evidence}"
elif method == "graph_neo4j_v4":
    context = f"GRAPH_FACTS_TABLE\n{fact_table(neo4j_facts)}"
elif method == "hybrid_neo4j_v4":
    context = f"TEXT_CONTEXT\n{evidence}\n\nGRAPH_FACTS_TABLE\n{fact_table(neo4j_facts)}"
```

---

## Scoring

Reuse the same scoring logic from `round3_dev_dryrun_v3_2_clean.py`:
- `numeric_correctness` — check target slots
- `answer_correctness` — LLM judge (same as before)
- `required_fact_recall` — for neo4j methods, compute ACTUAL recall:
  - Load `shadow_overlay_required_facts.jsonl` as ground truth
  - Check if each required fact's value (ticker + metric + year + value) appears in the Neo4j-retrieved facts
  - `rfr = matched_required_facts / total_required_facts` (actual, not hardcoded 1.0)

**Important:** Do NOT hardcode `required_fact_recall = 1.0` for neo4j methods. Compute it properly.

---

## Formula Contracts

For each case, load the formula contract:
1. First try `clean_dev_model_visible_formula_contracts.jsonl` (for dev/baseline cases)
2. If not found, try `repaired_cases.jsonl` (round3b cases have `model_visible_formula_contract` field)
3. Scorer contracts similarly from `clean_dev_scorer_only_target_slot_contracts.jsonl` or `scorer_contracts.jsonl`

---

## Output

### Run directory
```
outputs/round3_eval_runs/round4_llm_ie_kg_{YYYYMMDD_HHMMSS}/
  round4_traces.jsonl         # one row per (case, method) run
  round4_summary.md           # aggregate scores by method
  neo4j_facts_cache.jsonl     # Neo4j-retrieved facts per case (for debugging)
  failure_analysis.jsonl      # failed cases with error details
```

### Trace row schema (JSONL, one per case×method)
```json
{
  "case_id": "round3_dev_010_4a66fa95",
  "ticker": "MDLZ",
  "split": "round3_dev",
  "method": "hybrid_neo4j_v4",
  "track": "track_b_neo4j_llm_ie",
  "neo4j_facts_count": 47,
  "formula_type": "gross_profit_margin",
  "target_slot_count": 3,
  "target_numeric_recall": 0.67,
  "required_fact_recall": 0.75,
  "numeric_correctness": 0.5,
  "answer_correctness": 0.8,
  "success": true,
  "failure_reason": null,
  "model": "gpt-4o-mini",
  "final_answer": "...",
  "calculation": "..."
}
```

### Summary markdown
Compare Round 4 vs locked test (from `locked_test_v3_2_track_b_20260528_145253/`):

| Method | avg_answer_correctness | avg_numeric_correctness | avg_rfr | notes |
|---|---|---|---|---|
| vector_only (locked) | ... | ... | ... | from locked test |
| hybrid_shadow (locked) | ... | ... | ... | from locked test |
| vector_only_v4 | ... | ... | ... | Round 4 |
| graph_neo4j_v4 | ... | ... | ... | Round 4 NEW |
| hybrid_neo4j_v4 | ... | ... | ... | Round 4 NEW |

---

## State File: `outputs/round4_neo4j_eval/state.json`

Update after each case×method run:
```json
{
  "phase": "running",
  "cases_total": 25,
  "methods": ["vector_only_v4", "graph_neo4j_v4", "hybrid_neo4j_v4"],
  "runs_total": 75,
  "runs_completed": 12,
  "runs_failed": [],
  "started_at": "...",
  "completed_at": null,
  "run_dir": "outputs/round3_eval_runs/round4_llm_ie_kg_20260528_160000/",
  "codex_handoff_message": null
}
```

When done: `"phase": "done"`, `"completed_at": "<timestamp>"`, `"codex_handoff_message": "Round 4 complete. Run scripts/verify_round4_results.py"`

---

## Checklist

- [ ] Read `round3_dev_dryrun_v3_2_clean.py` in full (reuse its structure and scoring logic)
- [ ] Implement `load_neo4j_graph_facts(ticker, case_id, driver)` 
- [ ] Load 25 cases from `shadow_overlay_eval_ready_cases.jsonl`
- [ ] Load formula contracts (dev/baseline from clean_dev, round3b from repaired_cases)
- [ ] Run 3 methods × 25 cases = 75 LLM calls (gpt-4o-mini, temperature=0)
- [ ] For neo4j methods: compute `required_fact_recall` properly (not hardcoded 1.0)
- [ ] Write traces to `round4_traces.jsonl`
- [ ] Write comparison summary markdown
- [ ] Cache Neo4j facts per case to `neo4j_facts_cache.jsonl`
- [ ] Update `state.json` throughout
- [ ] Do NOT modify locked test directory
- [ ] Do NOT write to Neo4j (read-only queries only)
