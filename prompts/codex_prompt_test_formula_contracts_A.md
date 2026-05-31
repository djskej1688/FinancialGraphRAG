# Codex Task: Build Formula Contracts for Test Split — Group A (5 Cases)

**Last updated by Claude:** 2026-05-28  
**State file:** `outputs/round3_eval_harness/formula_contract_v3_2_test_split/state_A.json`  
**Output directory:** `outputs/round3_eval_harness/formula_contract_v3_2_test_split/`

---

## Background

The locked test run (`locked_test_v3_2_track_b_20260528_145253/`) scored 0.0 on all 10 test-split cases because `scorer_only_target_slot_contract` was never built for those cases — the scorer returned `expected_answer_ambiguous` for every trace.

This task builds formula contracts for 5 of the 10 test-split cases where existing `required_facts` are sufficient (with minor supplemental extraction from `evidence_text` for LOW and MPC).

**DO NOT touch:**
- `outputs/round3_eval_runs/locked_test_v3_2_track_b_20260528_145253/` — locked, read-only
- Any existing Neo4j nodes — no writes
- `outputs/round3_eval_harness/formula_contract_v3_2_clean_dev/` — read-only reference

---

## Input Files

### Required facts (read-only)
```
outputs/round3_dual_track_eval_prep/track_b_shadow_overlay/shadow_overlay_required_facts.jsonl
```

### Test cases with evidence_text (read-only)
```
outputs/round3_dual_track_eval_prep/track_b_shadow_overlay/shadow_overlay_test_cases.json
```
Filter to tickers: `NXPI`, `XEL`, `AMGN`, `LOW`, `MPC`

### Reference schema (read existing contracts to understand format)
```
outputs/round3_eval_harness/formula_contract_v3_2_clean_dev/clean_dev_scorer_only_target_slot_contracts.jsonl
outputs/round3_eval_harness/formula_contract_v3_2_clean_dev/clean_dev_model_visible_formula_contracts.jsonl
```

---

## Output Files

Create directory if not exists:
```
outputs/round3_eval_harness/formula_contract_v3_2_test_split/
```

Write:
```
outputs/round3_eval_harness/formula_contract_v3_2_test_split/test_scorer_contracts.jsonl
outputs/round3_eval_harness/formula_contract_v3_2_test_split/test_model_visible_contracts.jsonl
outputs/round3_eval_harness/formula_contract_v3_2_test_split/state_A.json
```

One JSONL row per case in each file.

---

## Cases to Process

### Case 1: NXPI — `round3_test_011_e428c7bc`

**Question:** 2023 op margin trend for NXPI vs. historical cost mgmt.  
**Formula:** `operating_income / revenue * 100`  
**Years:** 2021, 2022, 2023

**Facts from required_facts (use exactly these 6):**
```
fact_01: operating_income  year=2023  value=3661.0   unit=USD_millions
fact_02: operating_income  year=2022  value=3797.0   unit=USD_millions
fact_03: operating_income  year=2021  value=2583.0   unit=USD_millions
fact_04: revenue           year=2023  value=13276.0  unit=USD_millions
fact_05: revenue           year=2022  value=13205.0  unit=USD_millions
fact_06: revenue           year=2021  value=11063.0  unit=USD_millions
```
**SKIP fact_07 and fact_08** — they are cost_of_revenue mislabeled as "revenue" (negative values, not needed).

**Target slots (pre-computed):**
```
operating_margin_2023 = 3661.0 / 13276.0 * 100 = 27.575...  → expected_value: 27.575, tolerance: 0.1
operating_margin_2022 = 3797.0 / 13205.0 * 100 = 28.754...  → expected_value: 28.754, tolerance: 0.1
operating_margin_2021 = 2583.0 / 11063.0 * 100 = 23.348...  → expected_value: 23.348, tolerance: 0.1
unit: "percentage"
acceptable_equivalent_forms: ["percent", "ratio_decimal"]
```

---

### Case 2: XEL — `round3_test_004_b035aeed`

**Question:** female rep ratio mgmt vs overall employees at XEL  
**Formula:** `female_management_percent / female_employee_percent`  
**Year:** 2023

**Facts from required_facts (use exactly these 2):**
```
fact_01: employees   year=2023  value=23.0  unit=employees   (female % in overall employees)
fact_02: management  year=2023  value=26.0  unit=employees   (female % in management)
```
Note: fact_03/04/05 are ethnically diverse metrics — not used for this target.

**Target slots (pre-computed):**
```
female_mgmt_to_employee_ratio_2023 = 26.0 / 23.0 = 1.13043...  → expected_value: 1.13043, tolerance: 0.01
unit: "ratio"
acceptable_equivalent_forms: ["ratio", "times"]
```

---

### Case 3: AMGN — `round3_test_009_3a2f3700`

**Question:** 2023 gross margin for AMGN vs. 2022.  
**Formula:** `(product_sales + other_revenues - cost_of_sales) / total_revenue * 100`  
**Years:** 2022, 2023

**Facts from required_facts (use all 8):**
```
fact_01: revenue          year=2023  value=1280.0   unit=USD_millions  (other_revenues)
fact_02: revenue          year=2022  value=1522.0   unit=USD_millions  (other_revenues)
fact_03: product_sales    year=2023  value=26910.0  unit=USD_millions
fact_04: product_sales    year=2022  value=24801.0  unit=USD_millions
fact_05: cost_of_sales    year=2023  value=8451.0   unit=USD_millions
fact_06: cost_of_sales    year=2022  value=6406.0   unit=USD_millions
fact_07: total_revenue    year=2023  value=28190.0  unit=USD_millions
fact_08: total_revenue    year=2022  value=26323.0  unit=USD_millions
```

Note: fact_01 and fact_02 are stored as metric_canonical="revenue" but represent `other_revenues` per the income statement. In source_fact_numbers, use metric_canonical as stored.

**Target slots (pre-computed):**
```
gross_margin_2023 = (26910.0 + 1280.0 - 8451.0) / 28190.0 * 100
                  = 19739.0 / 28190.0 * 100 = 70.019...
  → expected_value: 70.019, tolerance: 0.1, unit: "percentage"

gross_margin_2022 = (24801.0 + 1522.0 - 6406.0) / 26323.0 * 100
                  = 19917.0 / 26323.0 * 100 = 75.659...
  → expected_value: 75.659, tolerance: 0.1, unit: "percentage"

acceptable_equivalent_forms: ["percent", "ratio_decimal"]
```

---

### Case 4: LOW — `round3_test_007_4ac62908`

**Question:** Lowe's (LOW) diluted EPS for the current yr and YoY % change.  
**Formula:** EPS direct + `(eps_current - eps_prior) / eps_prior * 100`  
**Years:** 2022 (prior), 2023 (current)

**Facts from required_facts:**
```
fact_04: diluted_earnings_per_common_share  year=2023  value=13.2  unit=USD_millions
fact_01: net_earnings_attributable_to_lowe_s_companies_inc  year=2023  value=7726.0  unit=USD_millions
fact_05: net_earnings_attributable_to_lowe_s_companies_inc  year=2022  value=6437.0  unit=USD_millions
fact_06: net_earnings_attributable_to_lowe_s_companies_inc  year=2021  value=8442.0  unit=USD_millions
```

**Supplemental fact extraction required:**  
Read `evidence_text` for LOW from `shadow_overlay_test_cases.json`. Extract:
- `diluted_eps_2022 = 10.17` (from "Diluted earnings per common share: $13.20 [FY2024], $10.17 [FY2023]" in the EPS table)
- Assign fact_id: `round3_test_007_4ac62908_fact_S1` (S = supplemental)
- metric_canonical: `diluted_earnings_per_common_share`, year: 2022, value: 10.17, unit: `USD_per_share`

Write supplemental facts to:
```
outputs/round3_eval_harness/formula_contract_v3_2_test_split/supplemental_facts_A.jsonl
```

**Target slots (pre-computed):**
```
diluted_eps_2023 = 13.20
  → expected_value: 13.20, tolerance: 0.05, unit: "USD_per_share"
  → derived_or_source: "source"
  → acceptable_equivalent_forms: ["dollar_amount", "per_share"]

diluted_eps_yoy_pct_change = (13.20 - 10.17) / 10.17 * 100 = 3.03 / 10.17 * 100 = 29.793...
  → expected_value: 29.793, tolerance: 0.2, unit: "percentage"
  → derived_or_source: "derived"
  → acceptable_equivalent_forms: ["percent"]
```

Use both fact_04 and fact_S1 in source_fact_numbers for the scorer contract.

---

### Case 5: MPC — `round3_test_017_68bdbbb8`

**Question:** MPC's tax-adjusted continuing income consistency & recurring profitability risk impact with no discontinued ops.  
**Formula:** `income_from_continuing_operations_net_of_tax / total_revenues_and_other_income * 100`  
**Years:** 2021, 2022, 2023

**Facts from required_facts:**
```
fact_01: income_from_continuing_operations_net_of_tax  year=2023  value=11172.0  unit=USD_millions
fact_02: income_from_continuing_operations_net_of_tax  year=2022  value=15978.0  unit=USD_millions
fact_03: income_from_continuing_operations_net_of_tax  year=2021  value=2553.0   unit=USD_millions
```

**Supplemental fact extraction required:**  
Read `evidence_text` for MPC from `shadow_overlay_test_cases.json`. Extract total revenues:
- `total_revenues_2023 = 150307.0` (unit=USD_millions)
- `total_revenues_2022 = 179952.0` (unit=USD_millions)
- `total_revenues_2021 = 120930.0` (unit=USD_millions)
- Assign fact_ids: `round3_test_017_68bdbbb8_fact_S1`, `_fact_S2`, `_fact_S3`
- metric_canonical: `total_revenues_and_other_income`

Write supplemental facts to:
```
outputs/round3_eval_harness/formula_contract_v3_2_test_split/supplemental_facts_A.jsonl
```
(Append to same file as LOW supplemental facts.)

**Target slots (pre-computed):**
```
cont_ops_margin_2023 = 11172.0 / 150307.0 * 100 = 7.433...
  → expected_value: 7.433, tolerance: 0.1, unit: "percentage"

cont_ops_margin_2022 = 15978.0 / 179952.0 * 100 = 8.879...
  → expected_value: 8.879, tolerance: 0.1, unit: "percentage"

cont_ops_margin_2021 = 2553.0 / 120930.0 * 100 = 2.111...
  → expected_value: 2.111, tolerance: 0.1, unit: "percentage"

acceptable_equivalent_forms: ["percent", "ratio_decimal"]
```

---

## Output Schema

### `test_scorer_contracts.jsonl` — one row per case
```json
{
  "case_id": "round3_test_011_e428c7bc",
  "split": "round3_test",
  "scorer_only_target_slot_contract": {
    "formula_type": "operating_margin",
    "final_target_numbers": ["operating_margin_2021", "operating_margin_2022", "operating_margin_2023"],
    "intermediate_numbers": [],
    "non_target_numbers": ["case_id", "fact_id", "trace_id", "source_id", "prompt_hash", "metric IDs", "evidence IDs"],
    "source_fact_numbers": [
      {"fact_id": "round3_test_011_e428c7bc_fact_01", "metric": "operating_income", "unit": "USD_millions", "value": 3661.0, "year": 2023},
      ...
    ],
    "target_slots": [
      {
        "target_slot_name": "operating_margin_2023",
        "expected_value": 27.575,
        "tolerance": 0.1,
        "unit": "percentage",
        "years": [2023],
        "derived_or_source": "derived",
        "required_for_answer": true,
        "acceptable_equivalent_forms": ["percent", "ratio_decimal"]
      },
      ...
    ]
  }
}
```

### `test_model_visible_contracts.jsonl` — one row per case
```json
{
  "case_id": "round3_test_011_e428c7bc",
  "split": "round3_test",
  "leakage_guard": {
    "contains_expected_answer_text": false,
    "contains_expected_numeric_final_answers": false,
    "contains_scorer_target_values": false
  },
  "model_visible_formula_contract": {
    "formula_type": "operating_margin",
    "target_formula_template": "operating_income / revenue * 100",
    "numerator_metric_role": "operating_income",
    "denominator_metric_role": "revenue",
    "target_years": [2021, 2022, 2023],
    "comparison_periods": ["2021", "2022", "2023"],
    "expected_output_type": "percentage",
    "required_comparison": "compare operating margin across 2021-2023",
    "required_steps": [
      "identify operating income and revenue for each year",
      "compute operating_income / revenue * 100",
      "identify trend: 2021 → 2022 → 2023"
    ],
    "rounding_instruction": "use v3.2 rounding rules",
    "do_not_use_as_targets": ["source fact ids", "case ids", "trace ids", "citation ids", "raw source-only numbers not requested as final targets"]
  }
}
```

---

## State File

Initialize:
```json
{
  "phase": "running",
  "task": "A",
  "cases": ["round3_test_011_e428c7bc", "round3_test_004_b035aeed", "round3_test_009_3a2f3700", "round3_test_007_4ac62908", "round3_test_017_68bdbbb8"],
  "cases_total": 5,
  "cases_completed": 0,
  "cases_failed": [],
  "started_at": null,
  "completed_at": null,
  "output_dir": "outputs/round3_eval_harness/formula_contract_v3_2_test_split/"
}
```

Update `cases_completed` after each case. When done: `"phase": "done"`, `"completed_at": "<timestamp>"`.

---

## Checklist

- [ ] Read `clean_dev_scorer_only_target_slot_contracts.jsonl` (first 3 rows) to confirm output schema
- [ ] Read `shadow_overlay_required_facts.jsonl` — filter to tickers NXPI, XEL, AMGN, LOW, MPC
- [ ] Read `shadow_overlay_test_cases.json` — need `evidence_text` for LOW and MPC
- [ ] Create output directory `outputs/round3_eval_harness/formula_contract_v3_2_test_split/`
- [ ] Process NXPI — compute 3 operating margin target slots, write to both output files
- [ ] Process XEL — compute 1 ratio target slot, write to both output files
- [ ] Process AMGN — compute 2 gross margin target slots, write to both output files
- [ ] Process LOW — extract diluted_eps_2022=10.17 from evidence_text, compute 2 target slots
- [ ] Process MPC — extract total_revenues (3 years) from evidence_text, compute 3 margin target slots
- [ ] Write supplemental_facts_A.jsonl (for LOW and MPC supplemental extractions)
- [ ] Verify: 5 rows in test_scorer_contracts.jsonl, 5 rows in test_model_visible_contracts.jsonl
- [ ] No OpenAI calls needed — all computations are deterministic from provided values above
- [ ] Update state_A.json to phase=done
