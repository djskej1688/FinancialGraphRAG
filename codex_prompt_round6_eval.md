# Round 06 Evaluation — Codex Spec

**Task label:** `round6_eval`
**KG batch:** `kg-targeted-ie-v1-20260528` (Step B output)
**Precondition:** `outputs/step_b_targeted_kg/state.json` → phase=done
**Baseline:** Round 5 (`outputs/round3_eval_runs/round5_diagnostic_20260528_213524/`)

---

## 0. Context and Goal

Round 5 confirmed: graph method ac=0.0 on ALL test cases.
Root causes identified and fixed in Step B:
- VRSK revenues wrong (1548 → 2681.4) ✓ fixed
- MPC income_from_cont_ops missing ✓ fixed
- BXP, NXPI: partial KG coverage ✓ fixed
- LOW, GM, MU, APD: wrong KG values ✓ fixed
- XEL employees (still problematic: 11311 vs 23, metric ambiguity)
- AMGN royalty_revenue (still wrong, but gross_margin computable from total_revenue + cost_of_sales)

Round 06 tests whether fixing the KG translates to graph ac improvement.

**Expected test outcomes:**
- graph: 0.70–0.90 (from 0.0 in R5)
- vector: ~0.60 (unchanged — no KG change affects vector)
- hybrid: ≥ graph (has vector fallback)

If graph > vector on test split, KG targeted extraction approach is validated.

---

## 1. Security Constraints

Same as Round 5:
- `OPENAI_API_KEY` from environment only, never from `.env` in code
- Neo4j credentials from `.env` (`python-dotenv`)
- DO NOT write to Neo4j (`neo4j_write_performed = False` always)
- DO NOT touch `outputs/round3_eval_runs/locked_test_v3_2_track_b_20260528_145253/`
- DO NOT commit `.env`

---

## 2. Input Files

| File | Description |
|---|---|
| `outputs/round3_dual_track_eval_prep/track_b_shadow_overlay/shadow_overlay_eval_ready_cases.jsonl` | 25 cases (evidence_text, ticker, question, years, metric_tags, split) |
| `outputs/round3_eval_harness/formula_contract_v3_2_clean_dev/clean_dev_scorer_only_target_slot_contracts.jsonl` | 9 dev+baseline scorer contracts |
| `outputs/round3_eval_harness/formula_contract_v3_2_test_split/test_scorer_contracts.jsonl` | 10 test scorer contracts |
| `outputs/round3_eval_harness/formula_contract_v3_2_clean_dev/clean_dev_model_visible_formula_contracts.jsonl` | 9 model-visible contracts |
| `outputs/round3_eval_harness/formula_contract_v3_2_test_split/test_model_visible_contracts.jsonl` | 10 model-visible contracts |

**KG batch:** `kg-targeted-ie-v1-20260528` (Neo4j query parameter)

---

## 3. Output Files

```
outputs/round6_eval/
  state.json

outputs/round3_eval_runs/round6_eval_{YYYYMMDD_HHMMSS}/
  round6_traces.jsonl          # 75 trace rows (25 cases × 3 methods)
  round6_summary.md            # Results + comparison with Round 5
  neo4j_facts_cache.jsonl      # Cached Neo4j retrievals per case
```

Run directory timestamp: set at script start (`datetime.datetime.now().strftime("%Y%m%d_%H%M%S")`).

---

## 4. Methods (unchanged from Round 5)

| Method name | Context source | KG batch |
|---|---|---|
| `vector_only_v6` | evidence_text (vector) only | N/A |
| `graph_neo4j_v6` | Neo4j LLM IE KG only | `kg-targeted-ie-v1-20260528` |
| `hybrid_neo4j_v6` | evidence_text + Neo4j KG | `kg-targeted-ie-v1-20260528` |

Method suffix changed from `_v5` to `_v6` to distinguish Round 06 traces.

---

## 5. Neo4j Graph Retrieval (updated KG batch only)

**Same function as Round 5 `load_neo4j_graph_facts_filtered()` with one change: KG_BATCH.**

```python
def load_neo4j_graph_facts_filtered(ticker: str, case_id: str, years: list[int],
                                     metric_tags: list[str], driver) -> list[dict]:
    KG_BATCH = "kg-targeted-ie-v1-20260528"   # ← CHANGED from Round 5
    
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
        
        all_facts = [...]  # same as Round 5
    
    # Same keyword filter as Round 5 (if > 20 facts and metric_tags provided)
    # The new batch has fewer facts (~7-15 per case), so filter may rarely trigger
    ...
    return all_facts
```

**Note:** The new targeted batch has ~7-15 facts per case (vs ~27.5 in Round 5). The keyword filter `if len(all_facts) > 20` will rarely apply. Revert to year-filtered only for this round.

---

## 6. Scoring

**Identical to Round 5.** Copy the scoring functions verbatim:
- `score_answer()` with `scorer_only_target_slot_contract`
- `required_fact_recall()` checking source_fact values in retrieved context
- `numerical_closeness()` (continuous, fixed in Round 5 — use the fixed version)
- `numeric_correctness()` (binary slot fraction — separate from numerical_closeness)
- `target_numeric_recall()` (fraction of target slots the model attempted)

The Round 5 bug fixes must carry forward:
- `numerical_closeness` must be a continuous float (not None)
- `numeric_correctness` ≠ `target_numeric_recall` (different metrics)

---

## 7. Trace Schema

Same as Round 5 with additions:

```python
trace = {
    # Identity
    "trace_id": f"local_trace_round6_{i:04d}_{case_id}__{method}",
    "case_id": case_id,
    "ticker": ticker,
    "split": split,
    "method": method,  # vector_only_v6 / graph_neo4j_v6 / hybrid_neo4j_v6
    
    # Round 06 metadata
    "round": "round6",
    "kg_batch": "kg-targeted-ie-v1-20260528",  # or "N/A" for vector_only
    "step_b_batch": "kg-targeted-ie-v1-20260528",
    
    # Formula
    "formula_type": formula_type,
    "diagnostic_source_target_fallback": bool,  # True for 6 excluded dev cases
    "test_split_formula_source": "post_hoc",  # for test cases
    
    # Retrieval
    "neo4j_facts_count": int,  # 0 for vector_only
    "neo4j_write_performed": False,
    
    # Scoring (ALL must be computed)
    "answer_correctness": float,       # 0.0 or 1.0 (binary, all slots must match)
    "numerical_closeness": float,      # continuous 0-1 (FIXED)
    "numeric_correctness": float,      # binary slot fraction
    "target_numeric_recall": float,    # fraction of slots attempted (separate from above)
    "required_fact_recall": float,     # fraction of source_facts found in context
    "target_slot_count": int,
    "matched_target_slots": str,       # semicolon-separated
    "missing_target_slots": str,       # semicolon-separated
    "failure_reason": str,             # none / required_fact_missing / formula_target_mismatch / scoring_uncertain / expected_answer_ambiguous / answer_format_error
    
    # Model
    "model": "gpt-4o-mini",
    "model_api_called": bool,
    "usage": {...},
    "final_answer": str,
    "calculation": str,
}
```

---

## 8. Summary Report

`outputs/round3_eval_runs/round6_eval_{timestamp}/round6_summary.md`:

### 8.1 Overall (25 cases)

| Method | avg_ac | avg_nc | avg_rfr | avg_facts |
|---|---:|---:|---:|---:|
| vector_only_v6 | | | | |
| graph_neo4j_v6 | | | | |
| hybrid_neo4j_v6 | | | | |

### 8.2 Dev/Baseline (15 cases)

| Method | avg_ac | avg_nc | avg_rfr |
|---|---:|---:|---:|
| vector_only_v6 | | | |
| graph_neo4j_v6 | | | |
| hybrid_neo4j_v6 | | | |

### 8.3 Test split (10 cases) — primary comparison

| Method | avg_ac | avg_nc | avg_rfr |
|---|---:|---:|---:|
| vector_only_v6 | | | |
| graph_neo4j_v6 | | | |
| hybrid_neo4j_v6 | | | |

### 8.4 Round 5 → Round 6 delta (test split)

| Method | R5 avg_ac | R6 avg_ac | delta_ac | R5 avg_nc | R6 avg_nc | delta_nc |
|---|---:|---:|---:|---:|---:|---:|
| vector_only | 0.60 | | | | | |
| graph_neo4j | 0.00 | | | | | |
| hybrid_neo4j | 0.10 | | | | | |

(R5 nc values: vector=0.9045, graph=0.6187, hybrid=0.9244 — use fixed numerical_closeness)

### 8.5 Per-case test split breakdown

| case_id | ticker | formula_type | R5_graph_rfr | R6_graph_rfr | R5_graph_ac | R6_graph_ac | delta | notes |
|---|---|---|---:|---:|---:|---:|---:|---|
| round3_test_004 | XEL | workforce_ratio | 0.20 | | 0.0 | | | employees metric ambiguous |
| round3_test_007 | LOW | diluted_eps_and_yoy_change | 1.00 | | 0.0 | | | values fixed |
| round3_test_009 | AMGN | gross_margin | 0.75 | | 0.0 | | | royalty_rev wrong, total_rev ok |
| round3_test_011 | NXPI | operating_margin | 0.75 | | 0.0 | | | all 6 facts match |
| round3_test_012 | GM | tpo_segment_gross_margin | 1.00 | | 0.0 | | | all 10 facts match |
| round3_test_013 | VRSK | operating_vs_net_margin | 0.00 | | 0.0 | | | revenues=2681.4 fixed |
| round3_test_014 | MU | net_margin_and_nonop_impact | 1.00 | | 0.0 | | | all 9 facts match |
| round3_test_016 | APD | gross_margin | 1.00 | | 0.0 | | | all 6 facts match |
| round3_test_017 | MPC | continuing_ops_margin | 0.00 | | 0.0 | | | all 6 facts match |
| round3_test_018 | BXP | operating_margin | 0.60 | | 0.0 | | | all 7 facts match |

Fill in R6 values after eval.

### 8.6 Key diagnostic question

> Does graph_neo4j_v6 test avg_ac **exceed** vector_only_v6 test avg_ac?

Answer: [YES / NO / SAME]

If NO: investigate remaining failure modes (model arithmetic? formula contract interpretation?)

---

## 9. State File

`outputs/round6_eval/state.json`:

```json
{
  "phase": "done",
  "round": "round6",
  "kg_batch": "kg-targeted-ie-v1-20260528",
  "step_b_batch_validated": true,
  "cases_total": 25,
  "runs_total": 75,
  "runs_completed": 75,
  "runs_failed": [],
  "methods": ["vector_only_v6", "graph_neo4j_v6", "hybrid_neo4j_v6"],
  "run_dir": "outputs/round3_eval_runs/round6_eval_{timestamp}/",
  "test_ac_vector": 0.0,
  "test_ac_graph": 0.0,
  "test_ac_hybrid": 0.0,
  "graph_beats_vector_test": false,
  "started_at": "...",
  "completed_at": "...",
  "codex_handoff_message": "Round 6 complete. Check round6_summary.md. Next: Step A (semantic fact retrieval layer) if graph > vector; otherwise diagnose remaining failures."
}
```

Set `graph_beats_vector_test = (test_ac_graph > test_ac_vector)`.

---

## 10. Inherited Behavior (copy from Round 5)

These must be carried forward unchanged:

1. **Formula contract injection**: `model_visible_formula_contract` included in every prompt for all 3 methods
2. **Source-target fallback flag**: `diagnostic_source_target_fallback = True` for 6 excluded dev cases (KR, LND, MSFT, BW, CARR, FOXA)
3. **Claim boundary**: `claim_boundary = "method_comparison_valid_absolute_scores_diagnostic"` on all traces
4. **Prompt**: Prompt v3.1 System (same as Round 5 — do not modify)
5. **Model**: `gpt-4o-mini` (same)
6. **rfr calculation**: Match source_fact values against retrieved context within 1% tolerance for large values, 0.05 absolute for small values

---

## 11. Checklist

- [ ] Load 25 shadow overlay cases
- [ ] Load 19 scorer contracts + 19 model-visible contracts
- [ ] Verify Neo4j batch `kg-targeted-ie-v1-20260528` has observations: `MATCH (obs:LLMObservation {kg_batch: 'kg-targeted-ie-v1-20260528'}) RETURN count(obs)` → expect 181
- [ ] Run 75 evaluations (25 cases × 3 methods)
- [ ] Verify `numerical_closeness` is NOT None for any trace
- [ ] Verify `numeric_correctness` ≠ `target_numeric_recall` for at least some traces (not all equal)
- [ ] Write round6_traces.jsonl (75 rows)
- [ ] Write round6_summary.md with all 4 breakdowns + per-case test table + R5→R6 delta
- [ ] Set `graph_beats_vector_test` in state.json
- [ ] Write state.json phase=done

---

## 12. Notes

- **Do not re-run Round 5.** Round 5 baseline numbers (ac/nc/rfr with fixed metrics) are in the updated traces at `outputs/round3_eval_runs/round5_diagnostic_20260528_213524/round5_traces.jsonl`.
- **avg_facts for graph/hybrid** will be much lower than R5 (~7-15 vs ~27.5). This is intentional — targeted extraction.
- **XEL expected to still fail** (graph): employees=11311 in new KG vs expected=23. This is a scorer contract issue (metric name `employees` is ambiguous). Note this explicitly in the summary.
- **AMGN**: royalty_revenue is wrong in new KG, but gross_margin is computable from total_revenue + cost_of_sales. If ac=1.0 for AMGN graph, note that the formula used a different revenue component than specified in source_facts.
- **6 fallback dev cases**: scores for KR, LND, MSFT, BW, CARR, FOXA remain diagnostic only (no valid target_slots). Mark `diagnostic_source_target_fallback=True` and do not include in the "clean" dev avg_ac.
