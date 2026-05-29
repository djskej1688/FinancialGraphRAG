# Codex Task: Round 5 Diagnostic Evaluation

**Last updated by Claude:** 2026-05-28  
**State file:** `outputs/round5_diagnostic_eval/state.json`  
**Output directory:** `outputs/round3_eval_runs/round5_diagnostic_{YYYYMMDD_HHMMSS}/`  
**Base this on:** `scripts/round4_eval_llm_ie_kg.py` (read it in full before implementing)

---

## Background and Purpose

Round 4 ran 75 evaluations (25 cases × 3 methods) but all 10 test-split cases scored 0.0 due to missing formula contracts — the scorer returned `expected_answer_ambiguous` for every test-split trace. This was NOT a model failure or KG contamination issue.

Round 5 fixes two problems identified in Round 4:

**Problem 1 (Fixed): Missing formula contracts for test split**  
Formula contracts for all 10 test cases have now been built and saved to:
```
outputs/round3_eval_harness/formula_contract_v3_2_test_split/test_scorer_contracts.jsonl
outputs/round3_eval_harness/formula_contract_v3_2_test_split/test_model_visible_contracts.jsonl
```

**Problem 2 (Fixed): Graph information overload**  
Round 4 returned avg 57.4 Neo4j facts per case (min=20, max=108). The model only needs 3–8 facts per case. Round 5 adds year + keyword filtering to bring this down to ~15 facts per case.

**Result labeling:**  
Because formula contracts for the test split were built post-hoc (after seeing the test cases), Round 5 results MUST be labeled as `"diagnostic evaluation — post-hoc formula contracts"`. They are valid for method comparison but NOT for clean held-out benchmark claims.

**DO NOT touch:**
- `outputs/round3_eval_runs/locked_test_v3_2_track_b_20260528_145253/` — locked, read-only
- Any existing Neo4j KG nodes — read-only queries only
- `outputs/round3_eval_harness/formula_contract_v3_2_clean_dev/` — read-only

---

## Key Change 1: Load Formula Contracts from Both Sources

Merge formula contracts from TWO locations:

```python
def load_all_formula_contracts() -> tuple[dict, dict]:
    """
    Returns: (scorer_contracts, model_visible_contracts)
    Both keyed by case_id.
    Test split contracts take precedence if both have same case_id (shouldn't happen).
    """
    scorer = {}
    visible = {}
    
    # Source 1: dev + baseline (15 cases)
    DEV_SCORER = "outputs/round3_eval_harness/formula_contract_v3_2_clean_dev/clean_dev_scorer_only_target_slot_contracts.jsonl"
    DEV_VISIBLE = "outputs/round3_eval_harness/formula_contract_v3_2_clean_dev/clean_dev_model_visible_formula_contracts.jsonl"
    
    # Source 2: test split (10 cases) — newly built
    TEST_SCORER = "outputs/round3_eval_harness/formula_contract_v3_2_test_split/test_scorer_contracts.jsonl"
    TEST_VISIBLE = "outputs/round3_eval_harness/formula_contract_v3_2_test_split/test_model_visible_contracts.jsonl"
    
    for path in [DEV_SCORER, TEST_SCORER]:
        with open(path, encoding="utf-8") as f:
            for line in f:
                if line.strip():
                    row = json.loads(line)
                    scorer[row["case_id"]] = row["scorer_only_target_slot_contract"]
    
    for path in [DEV_VISIBLE, TEST_VISIBLE]:
        with open(path, encoding="utf-8") as f:
            for line in f:
                if line.strip():
                    row = json.loads(line)
                    visible[row["case_id"]] = row["model_visible_formula_contract"]
    
    return scorer, visible

# Expected counts after loading:
# scorer: 25 entries (15 dev/baseline + 10 test)
# visible: 25 entries
```

**Verify before running:** assert `len(scorer_contracts) == 25` and `len(model_visible_contracts) == 25`. If not 25, print which case_ids are missing and abort.

---

## Key Change 2: Filtered Neo4j Graph Retrieval

Replace the Round 4 `load_neo4j_graph_facts()` function with a filtered version.

### Round 4 (unfiltered — caused 57.4 avg facts):
```python
# OLD: returned all observations for ticker
MATCH (obs:LLMObservation)-[:LLM_MENTIONS_COMPANY]->(c:LLMCompany {ticker: $ticker})
WHERE obs.kg_batch = $batch
```

### Round 5 (filtered — target ~15 facts per case):
```python
def load_neo4j_graph_facts_filtered(ticker: str, case_id: str, years: list[int], 
                                     metric_tags: list[str], driver) -> list[dict]:
    """
    Query Neo4j with year + keyword filters to reduce fact count.
    
    Args:
        ticker: company ticker
        case_id: for cache keying
        years: list of relevant years from the case (e.g. [2022, 2023])
        metric_tags: list of metric keywords from the case (e.g. ['operating_income', 'revenue'])
        driver: Neo4j driver
    
    Returns: filtered list of fact dicts
    """
    KG_BATCH = "kg-llm-ie-v1-20260528"
    
    with driver.session(database="neo4j") as s:
        r = s.run("""
MATCH (obs:LLMObservation)-[:LLM_MENTIONS_COMPANY]->(c:LLMCompany {ticker: $ticker}),
      (obs)-[:LLM_OBSERVES_METRIC]->(m:LLMFinancialMetric),
      (obs)-[:LLM_OBSERVED_IN_YEAR]->(yr:LLMFiscalYear)
WHERE obs.kg_batch = $batch
  AND yr.year IN $years
RETURN obs.obs_id AS obs_id,
       obs.value AS value,
       obs.unit AS unit,
       obs.evidence_quote AS evidence_quote,
       m.canonical_name AS metric_canonical,
       m.display_name AS metric_display,
       yr.year AS year
ORDER BY yr.year, m.canonical_name
""", ticker=ticker, batch=KG_BATCH, years=years)
        
        all_facts = []
        for rec in r:
            all_facts.append({
                "fact_id": rec["obs_id"],
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
        
        # If metric_tags provided and result is still large (>20), apply keyword filter
        if len(all_facts) > 20 and metric_tags:
            # Keep facts whose metric_canonical contains any tag keyword
            tag_keywords = set()
            for tag in metric_tags:
                # Break compound tags into words (e.g. "operating_income" -> {"operating", "income"})
                tag_keywords.update(tag.lower().replace("_", " ").split())
            
            filtered = [
                f for f in all_facts
                if any(kw in f["metric_canonical"].lower() for kw in tag_keywords)
            ]
            
            # If filter is too aggressive (< 3 facts), revert to year-filtered only
            if len(filtered) >= 3:
                return filtered
        
        return all_facts
```

**Call site:**
```python
# Load case's years and metric_tags from the case JSON
neo4j_facts = load_neo4j_graph_facts_filtered(
    ticker=case["ticker"],
    case_id=case["case_id"],
    years=case.get("years", []),
    metric_tags=case.get("metric_tags", []),
    driver=driver
)
```

Both `years` and `metric_tags` are available in `shadow_overlay_eval_ready_cases.jsonl` for each case.

---

## Key Change 3: Include model_visible_formula_contract in Prompt

In Round 4, formula contracts were only used by the scorer. In Round 5, the `model_visible_formula_contract` should be included in the prompt to guide the model toward the correct formula.

```python
def build_prompt(case: dict, method: str, context: str, 
                 model_visible_contract: dict | None) -> list[dict]:
    """
    Build prompt messages.
    """
    system_prompt = open("outputs/round3_eval_harness/prompts/prompt_v3_2_system.md").read()
    answer_format = open("outputs/round3_eval_harness/prompts/answer_format_spec_v3_2.md").read()
    rounding_rules = open("outputs/round3_eval_harness/prompts/rounding_and_tolerance_rules_v3_2.md").read()
    
    # Build formula hint if available (model_visible only — no expected values)
    formula_section = ""
    if model_visible_contract:
        fc = model_visible_contract
        formula_section = f"""
FORMULA_CONTRACT
formula_type: {fc.get('formula_type', '')}
template: {fc.get('target_formula_template', '')}
target_years: {fc.get('target_years', [])}
required_steps:
{chr(10).join('- ' + s for s in fc.get('required_steps', []))}
rounding: {fc.get('rounding_instruction', 'use v3.2 rounding rules')}
"""
    
    user_content = f"""QUESTION
{case['question']}

{context}
{formula_section}
{answer_format}
{rounding_rules}"""
    
    return [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_content}
    ]
```

---

## Input Files

```
outputs/round3_dual_track_eval_prep/track_b_shadow_overlay/shadow_overlay_eval_ready_cases.jsonl
    → 25 cases, each with: case_id, ticker, company, evidence_text, question, split, years, metric_tags

outputs/round3_eval_harness/formula_contract_v3_2_clean_dev/clean_dev_scorer_only_target_slot_contracts.jsonl
outputs/round3_eval_harness/formula_contract_v3_2_clean_dev/clean_dev_model_visible_formula_contracts.jsonl
outputs/round3_eval_harness/formula_contract_v3_2_test_split/test_scorer_contracts.jsonl
outputs/round3_eval_harness/formula_contract_v3_2_test_split/test_model_visible_contracts.jsonl

outputs/round3_dual_track_eval_prep/track_b_shadow_overlay/shadow_overlay_required_facts.jsonl
    → For required_fact_recall computation

outputs/round3_eval_harness/prompts/prompt_v3_2_system.md
outputs/round3_eval_harness/prompts/answer_format_spec_v3_2.md
outputs/round3_eval_harness/prompts/rounding_and_tolerance_rules_v3_2.md
```

---

## Methods (same 3 as Round 4)

| Method | vector_context | graph_facts source |
|---|---|---|
| `vector_only_v5` | evidence_text | (none) |
| `graph_neo4j_v5` | (none) | Neo4j LLM IE KG (filtered) |
| `hybrid_neo4j_v5` | evidence_text | Neo4j LLM IE KG (filtered) |

Note: use `_v5` suffix to distinguish from Round 4 runs.

---

## Scoring

Reuse the same scoring logic from Round 4, with one addition: **now all 25 cases have target_slots**, so `expected_answer_ambiguous` should not appear.

### numeric_correctness
- Load `scorer_only_target_slot_contract` for the case
- For each target_slot, check if model's answer contains expected_value within tolerance
- `numeric_correctness = matched_slots / total_slots`

### answer_correctness
- LLM judge (same as before)

### required_fact_recall
- Compare Neo4j returned facts against ground truth required facts from `shadow_overlay_required_facts.jsonl`
- Match on: ticker + metric_canonical + year + value (within 1% tolerance)
- `rfr = matched / total_required`
- For `vector_only_v5`: rfr = 1.0 (no graph retrieval)

### Diagnostic flag
Add to each trace:
```json
{
  "diagnostic_label": "round5_diagnostic_post_hoc_formula_contracts",
  "test_split_formula_source": "post_hoc",
  "claim_boundary": "method_comparison_valid_absolute_scores_diagnostic"
}
```

---

## Output

### Run directory
```
outputs/round3_eval_runs/round5_diagnostic_{YYYYMMDD_HHMMSS}/
  round5_traces.jsonl              # one row per (case, method) = 75 rows
  round5_summary.md                # aggregate scores by method + split breakdown
  neo4j_facts_cache.jsonl          # Neo4j retrieved facts per case (filtered)
  failure_analysis.jsonl
```

### round5_summary.md format

```markdown
# Round 5 Diagnostic Evaluation

**Diagnostic label:** post-hoc formula contracts for test split
**Claim boundary:** method comparison is valid; absolute scores are diagnostic

## Overall (25 cases)

| Method | avg_ac | avg_nc | avg_rfr | avg_facts |
|---|---:|---:|---:|---:|
| vector_only_v5 | ... | ... | 1.0 | 0 |
| graph_neo4j_v5 | ... | ... | ... | ... |
| hybrid_neo4j_v5 | ... | ... | ... | ... |

## Dev/Baseline only (15 cases) — formula contracts pre-built

| Method | avg_ac | avg_nc | avg_rfr |
|---|---:|---:|---:|
| vector_only_v5 | ... | ... | 1.0 |
| graph_neo4j_v5 | ... | ... | ... |
| hybrid_neo4j_v5 | ... | ... | ... |

## Test split only (10 cases) — FIRST VALID SCORING

| Method | avg_ac | avg_nc | avg_rfr |
|---|---:|---:|---:|
| vector_only_v5 | ... | ... | 1.0 |
| graph_neo4j_v5 | ... | ... | ... |
| hybrid_neo4j_v5 | ... | ... | ... |

## Comparison: Round 4 vs Round 5 (dev+baseline 15 cases only)

| Method | Round 4 avg_ac | Round 5 avg_ac | delta |
|---|---:|---:|---:|
| vector_only | 0.467 | ... | ... |
| graph_neo4j | 0.200 | ... | ... |
| hybrid_neo4j | 0.200 | ... | ... |

## Neo4j Facts Count (Round 5 filtered)

| Stat | Round 4 | Round 5 |
|---|---:|---:|
| Average | 57.4 | ... |
| Min | 20 | ... |
| Max | 108 | ... |
```

### Trace row schema (75 rows)
```json
{
  "case_id": "round3_test_011_e428c7bc",
  "ticker": "NXPI",
  "split": "round3_test",
  "method": "hybrid_neo4j_v5",
  "track": "track_b_neo4j_llm_ie",
  "neo4j_facts_count": 12,
  "formula_type": "operating_margin",
  "target_slot_count": 3,
  "required_fact_recall": 0.85,
  "numeric_correctness": 0.67,
  "answer_correctness": 0.80,
  "success": true,
  "failure_reason": null,
  "model": "gpt-4o-mini",
  "final_answer": "...",
  "calculation": "...",
  "diagnostic_label": "round5_diagnostic_post_hoc_formula_contracts",
  "test_split_formula_source": "post_hoc",
  "claim_boundary": "method_comparison_valid_absolute_scores_diagnostic"
}
```

---

## State File: `outputs/round5_diagnostic_eval/state.json`

```json
{
  "phase": "running",
  "diagnostic_label": "round5_diagnostic_post_hoc_formula_contracts",
  "cases_total": 25,
  "methods": ["vector_only_v5", "graph_neo4j_v5", "hybrid_neo4j_v5"],
  "runs_total": 75,
  "runs_completed": 0,
  "runs_failed": [],
  "formula_contracts_loaded": 0,
  "started_at": null,
  "completed_at": null,
  "run_dir": null,
  "codex_handoff_message": null
}
```

Update after each case×method. When done:
```json
{
  "phase": "done",
  "runs_completed": 75,
  "formula_contracts_loaded": 25,
  "neo4j_avg_facts_filtered": "...",
  "completed_at": "...",
  "codex_handoff_message": "Round 5 diagnostic complete. Check round5_summary.md for results."
}
```

---

## Checklist

- [ ] Read `scripts/round4_eval_llm_ie_kg.py` in full before implementing
- [ ] Load and merge formula contracts from BOTH sources (verify 25 total)
- [ ] Load 25 cases from `shadow_overlay_eval_ready_cases.jsonl`
- [ ] Implement `load_neo4j_graph_facts_filtered()` with year + keyword filtering
- [ ] Implement updated `build_prompt()` with formula_contract section
- [ ] Verify Neo4j connection (read-only, no writes)
- [ ] Run 3 methods × 25 cases = 75 LLM calls (gpt-4o-mini, temperature=0)
- [ ] Confirm target_slot_count > 0 for ALL 25 cases before scoring
- [ ] If any case has target_slot_count = 0, stop and report which case
- [ ] Add `diagnostic_label`, `test_split_formula_source`, `claim_boundary` to every trace
- [ ] Write round5_traces.jsonl (75 rows)
- [ ] Write round5_summary.md with ALL THREE breakdowns: overall, dev-only, test-only
- [ ] Include Round 4 vs Round 5 comparison table in summary (dev+baseline 15 only)
- [ ] Report avg Neo4j facts count (filtered) vs Round 4 unfiltered (57.4)
- [ ] DO NOT modify locked test directory
- [ ] DO NOT write to Neo4j
- [ ] Update state.json to phase=done
