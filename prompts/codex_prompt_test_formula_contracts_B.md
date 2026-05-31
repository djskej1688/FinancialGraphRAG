# Codex Task: Build Formula Contracts for Test Split — Group B (5 Cases)

**Last updated by Claude:** 2026-05-28  
**State file:** `outputs/round3_eval_harness/formula_contract_v3_2_test_split/state_B.json`  
**Output directory:** `outputs/round3_eval_harness/formula_contract_v3_2_test_split/`

---

## Background

The locked test run (`locked_test_v3_2_track_b_20260528_145253/`) scored 0.0 on all 10 test-split cases because `scorer_only_target_slot_contract` was never built. Group B covers the 5 cases where existing `required_facts` are insufficient — the required facts capture the wrong or incomplete metrics, so new facts must be extracted from `evidence_text` using LLM extraction.

**DO NOT touch:**
- `outputs/round3_eval_runs/locked_test_v3_2_track_b_20260528_145253/` — locked, read-only
- Any existing Neo4j nodes — no writes
- `outputs/round3_eval_harness/formula_contract_v3_2_clean_dev/` — read-only reference

---

## Input Files

### Test cases with evidence_text (read-only)
```
outputs/round3_dual_track_eval_prep/track_b_shadow_overlay/shadow_overlay_test_cases.json
```
Filter to tickers: `APD`, `BXP`, `MU`, `VRSK`, `GM`

### Existing required facts (read-only, for reference)
```
outputs/round3_dual_track_eval_prep/track_b_shadow_overlay/shadow_overlay_required_facts.jsonl
```

### Reference schema (read-only)
```
outputs/round3_eval_harness/formula_contract_v3_2_clean_dev/clean_dev_scorer_only_target_slot_contracts.jsonl
outputs/round3_eval_harness/formula_contract_v3_2_clean_dev/clean_dev_model_visible_formula_contracts.jsonl
```

### OpenAI API key
Read from environment variable `OPENAI_API_KEY`. Do NOT read from `.env` file.

---

## Output Files

Append to / create in:
```
outputs/round3_eval_harness/formula_contract_v3_2_test_split/
```

Write:
```
outputs/round3_eval_harness/formula_contract_v3_2_test_split/test_scorer_contracts.jsonl         (append — do not overwrite Group A rows)
outputs/round3_eval_harness/formula_contract_v3_2_test_split/test_model_visible_contracts.jsonl  (append — do not overwrite Group A rows)
outputs/round3_eval_harness/formula_contract_v3_2_test_split/reextracted_facts_B.jsonl           (new file, one row per fact)
outputs/round3_eval_harness/formula_contract_v3_2_test_split/state_B.json
```

---

## Fact Extraction Strategy

For each case, the `evidence_text` is a structured financial table already in the case JSON. Use GPT-4o-mini with a structured extraction prompt to pull specific numeric values. Follow the same OpenAI call pattern from `scripts/kg_rebuild_llm_ie.py`.

### Extraction prompt template (use for all cases):
```python
EXTRACTION_SYSTEM = """You are a financial data extraction assistant.
Extract specific financial metrics from the provided financial statement text.
Return a JSON object with exactly the fields requested. Use null if a value is not found.
Values should be raw numbers (not strings). Use the exact units shown in the table header."""

def extract_facts(evidence_text: str, fields_to_extract: list[dict], model="gpt-4o-mini") -> dict:
    """
    fields_to_extract: list of {"name": str, "description": str, "year": int}
    Returns: {"field_name": numeric_value_or_null, ...}
    """
    fields_json = json.dumps(fields_to_extract, indent=2)
    prompt = f"""Extract the following financial metrics from this text:

{fields_json}

Financial statement:
{evidence_text}

Return JSON with each field name as key and the numeric value as value."""
    
    response = client.chat.completions.create(
        model=model,
        temperature=0,
        response_format={"type": "json_object"},
        messages=[
            {"role": "system", "content": EXTRACTION_SYSTEM},
            {"role": "user", "content": prompt}
        ]
    )
    return json.loads(response.choices[0].message.content)
```

**Important:** All evidence_texts are clean structured tables. The values below are pre-verified from the evidence text — if the extraction returns null or a wrong value, fall back to the hardcoded values provided in this spec.

---

## Cases to Process

### Case 1: APD — `round3_test_016_707dc83f`

**Question:** Air Products and Chemicals, Inc. (APD) 2024 GPM trend analysis vs historical.  
**Formula:** `(Sales - Cost_of_sales) / Sales * 100`  
**Years:** 2022, 2023, 2024

**Expected facts to extract from evidence_text:**

| Field name | Description | Year | Expected value | Unit |
|---|---|---|---|---|
| `sales_2024` | Sales | 2024 | 12100.6 | USD_millions |
| `sales_2023` | Sales | 2023 | 12600.0 | USD_millions |
| `sales_2022` | Sales | 2022 | 12698.6 | USD_millions |
| `cost_of_sales_2024` | Cost of sales | 2024 | 8168.7 | USD_millions |
| `cost_of_sales_2023` | Cost of sales | 2023 | 8833.0 | USD_millions |
| `cost_of_sales_2022` | Cost of sales | 2022 | 9338.5 | USD_millions |

**Assign fact_ids:**
```
round3_test_016_707dc83f_fact_B01  → sales            year=2024  value=12100.6
round3_test_016_707dc83f_fact_B02  → sales            year=2023  value=12600.0
round3_test_016_707dc83f_fact_B03  → sales            year=2022  value=12698.6
round3_test_016_707dc83f_fact_B04  → cost_of_sales    year=2024  value=8168.7
round3_test_016_707dc83f_fact_B05  → cost_of_sales    year=2023  value=8833.0
round3_test_016_707dc83f_fact_B06  → cost_of_sales    year=2022  value=9338.5
```
All unit=`USD_millions`.

**Target slots (pre-computed):**
```
gross_margin_2024 = (12100.6 - 8168.7) / 12100.6 * 100 = 3931.9 / 12100.6 * 100 = 32.493...
  → expected_value: 32.493, tolerance: 0.1, unit: "percentage"

gross_margin_2023 = (12600.0 - 8833.0) / 12600.0 * 100 = 3767.0 / 12600.0 * 100 = 29.897...
  → expected_value: 29.897, tolerance: 0.1, unit: "percentage"

gross_margin_2022 = (12698.6 - 9338.5) / 12698.6 * 100 = 3360.1 / 12698.6 * 100 = 26.461...
  → expected_value: 26.461, tolerance: 0.1, unit: "percentage"

acceptable_equivalent_forms: ["percent", "ratio_decimal"]
```

Note: The EXISTING required_facts for APD (fact_07, fact_08: net_income) are NOT needed for the target slots. Use only the newly extracted B facts in `source_fact_numbers`.

---

### Case 2: BXP — `round3_test_018_0748ea37`

**Question:** Operating margin for BXP in 2023 vs 2022.  
**Formula:** `(total_revenue - total_expenses) / total_revenue * 100`  
**Years:** 2022, 2023

**Facts already in required_facts (KEEP these):**
```
fact_03: total_expenses  year=2023  value=2239227.0  unit=USD_thousands
fact_04: total_expenses  year=2022  value=2050056.0  unit=USD_thousands
```
Note: facts fact_05, fact_06, fact_08 (noncontrolling_interest, net_income) are NOT needed for target slots but keep them in source_fact_numbers for completeness.

**Expected facts to extract from evidence_text:**

| Field name | Description | Year | Expected value | Unit |
|---|---|---|---|---|
| `total_revenue_2023` | Total revenue | 2023 | 3273569.0 | USD_thousands |
| `total_revenue_2022` | Total revenue | 2022 | 3108581.0 | USD_thousands |

**Assign fact_ids:**
```
round3_test_018_0748ea37_fact_B01  → total_revenue  year=2023  value=3273569.0  unit=USD_thousands
round3_test_018_0748ea37_fact_B02  → total_revenue  year=2022  value=3108581.0  unit=USD_thousands
```

**Target slots (pre-computed):**
```
operating_margin_2023 = (3273569.0 - 2239227.0) / 3273569.0 * 100
                      = 1034342.0 / 3273569.0 * 100 = 31.597...
  → expected_value: 31.597, tolerance: 0.1, unit: "percentage"

operating_margin_2022 = (3108581.0 - 2050056.0) / 3108581.0 * 100
                      = 1058525.0 / 3108581.0 * 100 = 34.050...
  → expected_value: 34.050, tolerance: 0.1, unit: "percentage"

acceptable_equivalent_forms: ["percent", "ratio_decimal"]
```

Use fact_03, fact_04, fact_B01, fact_B02 in source_fact_numbers.

---

### Case 3: MU — `round3_test_014_42c9db2b`

**Question:** Impact of non-op items on MU fiscal net margin for Aug 2024 vs. prior yrs.  
**Formula:** `net_income / revenue * 100` (margin); `net_income - operating_income` (non-op impact)  
**Years:** 2022 (FY2022=Sep2022), 2023 (FY2023=Aug2023), 2024 (FY2024=Aug2024)

**IMPORTANT — fiscal year convention:** Micron's fiscal years end in August/September, labeled by the END date year. In the evidence_text, columns are "August 29, 2024" / "August 31, 2023" / "September 1, 2022". Store as year=2024/2023/2022 respectively.

**Facts already in required_facts (KEEP these):**
```
fact_06: operating_income  year=2024  value=1304.0   unit=USD_millions
fact_07: operating_income  year=2022  value=9702.0   unit=USD_millions
fact_08: net_income        year=2024  value=778.0    unit=USD_millions
```
Skip facts fact_01 (operating_income year=2024 value=-31 — this is a mislabeled non-op item), fact_02/03/04/05 (income_tax and other_op items — secondary).

**Expected facts to extract from evidence_text:**

| Field name | Description | Year | Expected value | Unit |
|---|---|---|---|---|
| `revenue_2024` | Revenue | 2024 | 25111.0 | USD_millions |
| `revenue_2023` | Revenue | 2023 | 15540.0 | USD_millions |
| `revenue_2022` | Revenue | 2022 | 30758.0 | USD_millions |
| `net_income_2023` | Net income (loss) | 2023 | -5833.0 | USD_millions |
| `net_income_2022` | Net income | 2022 | 8687.0 | USD_millions |
| `operating_income_2023` | Operating income (loss) | 2023 | -5745.0 | USD_millions |

**Assign fact_ids:**
```
round3_test_014_42c9db2b_fact_B01  → revenue          year=2024  value=25111.0   unit=USD_millions
round3_test_014_42c9db2b_fact_B02  → revenue          year=2023  value=15540.0   unit=USD_millions
round3_test_014_42c9db2b_fact_B03  → revenue          year=2022  value=30758.0   unit=USD_millions
round3_test_014_42c9db2b_fact_B04  → net_income       year=2023  value=-5833.0   unit=USD_millions
round3_test_014_42c9db2b_fact_B05  → net_income       year=2022  value=8687.0    unit=USD_millions
round3_test_014_42c9db2b_fact_B06  → operating_income year=2023  value=-5745.0   unit=USD_millions
```

**Target slots (pre-computed):**
```
net_margin_2024 = 778.0 / 25111.0 * 100 = 3.097...
  → expected_value: 3.097, tolerance: 0.1, unit: "percentage"

non_op_impact_2024 = 778.0 - 1304.0 = -526.0
  → expected_value: -526.0, tolerance: 5.0, unit: "USD_millions"
  → derived_or_source: "derived"
  → acceptable_equivalent_forms: ["USD_millions", "amount"]

net_margin_2022 = 8687.0 / 30758.0 * 100 = 28.24...
  → expected_value: 28.240, tolerance: 0.2, unit: "percentage"

net_margin_2023 = -5833.0 / 15540.0 * 100 = -37.53...
  → expected_value: -37.534, tolerance: 0.2, unit: "percentage"
  → note: negative margin (net loss year)
```

Use fact_06, fact_07, fact_08, fact_B01 through fact_B06 in source_fact_numbers.

---

### Case 4: VRSK — `round3_test_013_bc2fb598`

**Question:** Net vs operating margin impact of discontinued ops for VRSK 2023.  
**Formula:** operating_margin = `operating_income / revenues * 100`; net_margin = `net_income_attributable / revenues * 100`  
**Year:** 2023

**Note on existing required_facts:** The 3 existing facts for VRSK (fact_01, 02, 03) capture the PARENTHETICAL tax expense amounts from discontinued operations (values: -12.6, 131.5, -29.7), NOT the actual income/loss from discontinued operations (-154.0, -87.8, 59.2). These facts are NOT useful for the target formula. Do NOT include them in source_fact_numbers for the target slots.

**Expected facts to extract from evidence_text:**

| Field name | Description | Year | Expected value | Unit |
|---|---|---|---|---|
| `revenues_2023` | Revenues (from continuing operations) | 2023 | 2681.4 | USD_millions |
| `operating_income_2023` | Operating income | 2023 | 1131.7 | USD_millions |
| `net_income_attributable_2023` | Net income attributable to Verisk | 2023 | 614.6 | USD_millions |
| `loss_from_discontinued_ops_2023` | Loss from discontinued operations, net of tax | 2023 | -154.0 | USD_millions |

**Assign fact_ids:**
```
round3_test_013_bc2fb598_fact_B01  → revenues                     year=2023  value=2681.4   unit=USD_millions
round3_test_013_bc2fb598_fact_B02  → operating_income             year=2023  value=1131.7   unit=USD_millions
round3_test_013_bc2fb598_fact_B03  → net_income_attributable      year=2023  value=614.6    unit=USD_millions
round3_test_013_bc2fb598_fact_B04  → loss_from_discontinued_ops   year=2023  value=-154.0   unit=USD_millions
```

**Target slots (pre-computed):**
```
operating_margin_2023 = 1131.7 / 2681.4 * 100 = 42.207...
  → expected_value: 42.207, tolerance: 0.1, unit: "percentage"

net_margin_2023 = 614.6 / 2681.4 * 100 = 22.921...
  → expected_value: 22.921, tolerance: 0.1, unit: "percentage"

margin_gap_due_to_discontinued = operating_margin - net_margin = 42.207 - 22.921 = 19.286
  → expected_value: 19.286, tolerance: 0.3, unit: "percentage_points"
  → derived_or_source: "derived"
  → required_for_answer: false  (secondary diagnostic target)
  → acceptable_equivalent_forms: ["percentage_points", "percent"]
```

Use fact_B01, fact_B02, fact_B03, fact_B04 in source_fact_numbers.

---

### Case 5: GM — `round3_test_012_f9d03e27`

**Question:** PACCAR's GM TPO segment, vs 2021, shows a % change for 2023.  
**Note:** Despite the question referencing PACCAR, the evidence text is GM's Truck, Parts and Other (TPO) segment.  
**Formula:** `(net_sales - cost_of_sales) / net_sales * 100` for TPO segment  
**Years:** 2021, 2023 (comparison); also 2022 for completeness

**Facts already in required_facts (secondary, include in source_fact_numbers):**
```
fact_05: truck_parts_and_other_income_before_income_taxes  year=2023  value=4885.7  unit=USD_millions
fact_06: truck_parts_and_other_income_before_income_taxes  year=2021  value=1943.2  unit=USD_millions
fact_07: truck_parts_and_other_income_before_income_taxes  year=2022  value=3198.8  unit=USD_millions
fact_08: interest_and_other_expenses_income_net            year=2023  value=520.4   unit=USD_millions
```
These are NOT used in the gross margin formula but provide context.

**Expected facts to extract from evidence_text:**

| Field name | Description | Year | Expected value | Unit |
|---|---|---|---|---|
| `net_sales_tpo_2023` | TPO Net sales and revenues | 2023 | 33315.5 | USD_millions |
| `net_sales_tpo_2022` | TPO Net sales and revenues | 2022 | 27314.3 | USD_millions |
| `net_sales_tpo_2021` | TPO Net sales and revenues | 2021 | 21834.5 | USD_millions |
| `cost_of_sales_tpo_2023` | TPO Cost of sales and revenues | 2023 | 26894.2 | USD_millions |
| `cost_of_sales_tpo_2022` | TPO Cost of sales and revenues | 2022 | 23291.0 | USD_millions |
| `cost_of_sales_tpo_2021` | TPO Cost of sales and revenues | 2021 | 19092.4 | USD_millions |

**Assign fact_ids:**
```
round3_test_012_f9d03e27_fact_B01  → net_sales_tpo        year=2023  value=33315.5  unit=USD_millions
round3_test_012_f9d03e27_fact_B02  → net_sales_tpo        year=2022  value=27314.3  unit=USD_millions
round3_test_012_f9d03e27_fact_B03  → net_sales_tpo        year=2021  value=21834.5  unit=USD_millions
round3_test_012_f9d03e27_fact_B04  → cost_of_sales_tpo    year=2023  value=26894.2  unit=USD_millions
round3_test_012_f9d03e27_fact_B05  → cost_of_sales_tpo    year=2022  value=23291.0  unit=USD_millions
round3_test_012_f9d03e27_fact_B06  → cost_of_sales_tpo    year=2021  value=19092.4  unit=USD_millions
```

**Target slots (pre-computed):**
```
tpo_gross_margin_2023 = (33315.5 - 26894.2) / 33315.5 * 100
                      = 6421.3 / 33315.5 * 100 = 19.273...
  → expected_value: 19.273, tolerance: 0.1, unit: "percentage"

tpo_gross_margin_2021 = (21834.5 - 19092.4) / 21834.5 * 100
                      = 2742.1 / 21834.5 * 100 = 12.557...
  → expected_value: 12.557, tolerance: 0.1, unit: "percentage"

tpo_gross_margin_pp_change_2021_to_2023 = 19.273 - 12.557 = 6.716
  → expected_value: 6.716, tolerance: 0.2, unit: "percentage_points"
  → derived_or_source: "derived"
  → acceptable_equivalent_forms: ["percentage_points", "percent"]
```

Use fact_B01 through fact_B06 in source_fact_numbers (plus fact_05 through fact_08 from required_facts).

---

## reextracted_facts_B.jsonl Schema

One row per extracted fact:
```json
{
  "fact_id": "round3_test_016_707dc83f_fact_B01",
  "case_id": "round3_test_016_707dc83f",
  "ticker": "APD",
  "metric_canonical": "sales",
  "metric_raw": "Sales",
  "year": 2024,
  "value": 12100.6,
  "unit": "USD_millions",
  "source": "evidence_text_extraction",
  "extraction_model": "gpt-4o-mini",
  "verified_against_expected": true,
  "fallback_used": false
}
```

Set `fallback_used: true` if the extracted value didn't match the expected value and you used the hardcoded value from this spec.

---

## State File

Initialize at `outputs/round3_eval_harness/formula_contract_v3_2_test_split/state_B.json`:
```json
{
  "phase": "running",
  "task": "B",
  "cases": [
    "round3_test_016_707dc83f",
    "round3_test_018_0748ea37",
    "round3_test_014_42c9db2b",
    "round3_test_013_bc2fb598",
    "round3_test_012_f9d03e27"
  ],
  "cases_total": 5,
  "cases_completed": 0,
  "cases_failed": [],
  "openai_calls_made": 0,
  "started_at": null,
  "completed_at": null,
  "output_dir": "outputs/round3_eval_harness/formula_contract_v3_2_test_split/"
}
```

---

## Checklist

- [ ] Read `shadow_overlay_test_cases.json` — load evidence_text for APD, BXP, MU, VRSK, GM
- [ ] Read `shadow_overlay_required_facts.jsonl` — load existing facts for reference
- [ ] Initialize OpenAI client from env `OPENAI_API_KEY`
- [ ] For each case: run extraction prompt → verify against hardcoded expected values → use fallback if mismatch
- [ ] Write all extracted facts to `reextracted_facts_B.jsonl`
- [ ] Compute target slots using pre-computed values from this spec (do not re-derive)
- [ ] Append 5 rows to `test_scorer_contracts.jsonl` (DO NOT overwrite Group A rows)
- [ ] Append 5 rows to `test_model_visible_contracts.jsonl` (DO NOT overwrite Group A rows)
- [ ] Verify final counts: test_scorer_contracts.jsonl = 10 rows total (5A + 5B)
- [ ] Update state_B.json to phase=done

---

## Final Verification

After both Group A and Group B complete, the test split contract files should contain exactly 10 rows:

| case_id | ticker | formula_type |
|---|---|---|
| round3_test_011_e428c7bc | NXPI | operating_margin |
| round3_test_004_b035aeed | XEL | workforce_ratio |
| round3_test_009_3a2f3700 | AMGN | gross_margin |
| round3_test_007_4ac62908 | LOW | diluted_eps_and_yoy_change |
| round3_test_017_68bdbbb8 | MPC | continuing_ops_margin |
| round3_test_016_707dc83f | APD | gross_margin |
| round3_test_018_0748ea37 | BXP | operating_margin |
| round3_test_014_42c9db2b | MU | net_margin_and_nonop_impact |
| round3_test_013_bc2fb598 | VRSK | operating_vs_net_margin |
| round3_test_012_f9d03e27 | GM | tpo_segment_gross_margin |
