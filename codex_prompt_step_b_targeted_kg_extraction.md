# Step B: KG Targeted Extraction Rebuild — Codex Spec

**Task label:** `step_b_targeted_kg_extraction`
**New KG batch:** `kg-targeted-ie-v1-{YYYYMMDD}` (set date at runtime)
**Precondition:** Round 5 diagnostic complete (`outputs/round5_diagnostic_eval/state.json` → phase=done)
**Next step after this:** Round 06 eval using the new batch

---

## 0. Why This Exists

The existing KG (`kg-llm-ie-v1-20260528`) was built by extracting ALL observations from each company's 10-K evidence_text without case-specific targeting. Round 5 analysis identified two failure modes in the graph method:

1. **Missing required facts (rfr=0.0):** VRSK, MPC, XEL — KG has facts but the scorer's required metric labels are absent
2. **Wrong extracted values (formula_target_mismatch):** LOW, AMGN, GM, MU, APD, BXP — KG has facts under the right label but values don't match the 10-K table

Root cause confirmed for VRSK: KG extracted `revenues ≈ 1548M` (from continuing ops P&L header) vs correct `revenues = 2681.4M` (from total consolidated statement).

**Step B goal:** For each case with a valid scorer contract, extract ONLY the formula-required metrics from the evidence_text using GPT-4o-mini, validate against known source_fact values, and write to a new KG batch. This makes graph retrieval precision-targeted instead of generic.

---

## 1. Security Constraints

- `OPENAI_API_KEY` — read from environment only, never from `.env` file in code
- Neo4j URI, user, password — read from `.env` file at runtime (`python-dotenv`), never hardcoded, never committed
- DO NOT modify any existing Neo4j nodes (nodes with `kg_batch = "kg-llm-ie-v1-20260528"`)
- DO NOT touch directory `outputs/round3_eval_runs/locked_test_v3_2_track_b_20260528_145253/`
- DO NOT commit `.env`

---

## 2. Input Files

| File | Description |
|---|---|
| `outputs/round3_dual_track_eval_prep/track_b_shadow_overlay/shadow_overlay_eval_ready_cases.jsonl` | 25 cases with `evidence_text`, `ticker`, `question`, `years`, `metric_tags`, `split` |
| `outputs/round3_eval_harness/formula_contract_v3_2_clean_dev/clean_dev_scorer_only_target_slot_contracts.jsonl` | 9 valid scorer contracts (6 dev + 3 baseline) with `source_fact_numbers` |
| `outputs/round3_eval_harness/formula_contract_v3_2_test_split/test_scorer_contracts.jsonl` | 10 valid scorer contracts (test split) with `source_fact_numbers` |

**Note on 6 excluded dev cases (KR, LND, MSFT, FOXA, BW, CARR):** These have no valid scorer contracts (excluded with `target_slots=[]`). They are handled as **best-effort extraction** (Section 7) after the 19 primary cases.

---

## 3. Output Files

```
outputs/step_b_targeted_kg/
  state.json                          # Run state (phase, counts, batch_id)
  extraction_trace.jsonl             # Per-fact extraction log
  validation_report.jsonl            # Per-fact validation vs source_fact_numbers
  kg_write_log.jsonl                 # Neo4j write results
  failed_extractions.jsonl           # Facts that failed validation or extraction
  step_b_summary.md                  # Human-readable summary
```

---

## 4. New KG Batch Schema

**Batch ID:** `kg-targeted-ie-v1-{YYYYMMDD}` where YYYYMMDD is the current date (e.g., `kg-targeted-ie-v1-20260529`).

Nodes and relationships to create (same schema as existing KG):

```cypher
// MERGE company (should already exist from kg-llm-ie-v1 build)
MERGE (c:LLMCompany {ticker: $ticker})
ON CREATE SET c.name = $company_name

// MERGE fiscal year (should already exist)
MERGE (yr:LLMFiscalYear {year: $year})

// MERGE metric (may already exist — that's OK; multiple observations can share a metric node)
MERGE (m:LLMFinancialMetric {canonical_name: $canonical_name})
ON CREATE SET m.display_name = $display_name

// CREATE new observation with new batch ID
CREATE (obs:LLMObservation {
    obs_id: $obs_id,
    value: $value,
    unit: $unit,
    evidence_quote: $evidence_quote,
    kg_batch: $batch_id,
    extraction_method: "targeted_gpt4o_mini",
    validation_status: $validation_status,
    source_fact_id: $source_fact_id
})

// Relationships
CREATE (obs)-[:LLM_MENTIONS_COMPANY]->(c)
CREATE (obs)-[:LLM_OBSERVES_METRIC]->(m)
CREATE (obs)-[:LLM_OBSERVED_IN_YEAR]->(yr)
```

**obs_id format:** `{batch_id}__{case_id}__{metric_canonical}__{year}`

**metric_canonical naming rule:**  
Use the `metric` field from `source_fact_numbers` directly as `canonical_name`. This ensures the round5/round6 retrieval keyword filter can match it against `metric_tags`.

---

## 5. Source Facts Inventory

All 130 facts across 19 valid-contract cases. Codex must load these from the contract files.

### 5.1 Test Split (10 cases, 63 facts)

| case_id | ticker | formula_type | n_facts | source_fact details |
|---|---|---|---|---|
| round3_test_004 | XEL | workforce_ratio | 2 | employees=23 (2023), management=26 (2023) [unit: employees] |
| round3_test_007 | LOW | diluted_eps_and_yoy_change | 5 | diluted_eps_2023=13.2, diluted_eps_2022=10.17; net_earnings_2023=7726, 2022=6437, 2021=8442 [USD_millions/per_share] |
| round3_test_009 | AMGN | gross_margin | 8 | revenue(royalty)_2023=1280, 2022=1522; product_sales_2023=26910, 2022=24801; cost_of_sales_2023=8451, 2022=6406; total_revenue_2023=28190, 2022=26323 |
| round3_test_011 | NXPI | operating_margin | 6 | op_income_2021=2583, 2022=3797, 2023=3661; revenue_2021=11063, 2022=13205, 2023=13276 |
| round3_test_012 | GM | tpo_segment_gross_margin | 10 | net_sales_tpo_2021=21834.5, 2022=27314.3, 2023=33315.5; cost_of_sales_tpo_2021=19092.4, 2022=23291.0, 2023=26894.2; tpo_income_before_tax_2021=1943.2, 2022=3198.8, 2023=4885.7; interest_other_net_2023=520.4 |
| round3_test_013 | VRSK | operating_vs_net_margin | 4 | revenues_2023=2681.4; op_income_2023=1131.7; net_income_attributable_2023=614.6; loss_discontinued_ops_2023=-154.0 |
| round3_test_014 | MU | net_margin_and_nonop_impact | 9 | op_income_2022=9702, 2023=-5745, 2024=1304; revenue_2022=30758, 2023=15540, 2024=25111; net_income_2022=8687, 2023=-5833, 2024=778 |
| round3_test_016 | APD | gross_margin | 6 | sales_2022=12698.6, 2023=12600.0, 2024=12100.6; cost_of_sales_2022=9338.5, 2023=8833.0, 2024=8168.7 |
| round3_test_017 | MPC | continuing_ops_margin | 6 | income_from_cont_ops_2021=2553, 2022=15978, 2023=11172; total_revenues_and_other_income_2021=120930, 2022=179952, 2023=150307 |
| round3_test_018 | BXP | operating_margin | 7 | total_revenue_2022=3108581, 2023=3273569; total_expenses_2022=2050056, 2023=2239227; noncontrolling_interest_2022=-96780, 2023=-22548; net_income_2023=291424 [unit: USD_thousands] |

### 5.2 Dev + Baseline (9 cases, 67 facts)

| case_id | ticker | formula_type | n_facts |
|---|---|---|---|
| baseline_control_003 | LULU | current_ratio | 8 |
| baseline_control_004 | DXCM | gross_margin | 8 |
| baseline_control_005 | VTRS | inventory_turnover | 8 |
| round3_dev_003 | MCO | workforce_ratio | 8 |
| round3_dev_010 | MDLZ | tax_rate_ratio | 8 |
| round3_dev_011 | CMCSA | growth_and_eps_change | 8 |
| round3_dev_016 | BAC | noninterest_expense_ratio | 4 |
| round3_dev_017 | MTB | bank_operating_profit_margin | 7 |
| round3_dev_019 | HCA | tax_rate_ratio | 8 |

Load exact values from `clean_dev_scorer_only_target_slot_contracts.jsonl`.

---

## 6. Extraction Pipeline

### 6.1 Setup

```python
import os, json, re, datetime
from pathlib import Path
from dotenv import load_dotenv
from neo4j import GraphDatabase
from openai import OpenAI

load_dotenv()
NEO4J_URI = os.getenv("NEO4J_URI", "bolt://localhost:7687")
NEO4J_USER = os.getenv("NEO4J_USER", "neo4j")
NEO4J_PASSWORD = os.getenv("NEO4J_PASSWORD")
OPENAI_API_KEY = os.environ["OPENAI_API_KEY"]  # Must be in environment, not .env

TODAY = datetime.date.today().strftime("%Y%m%d")
KG_BATCH_NEW = f"kg-targeted-ie-v1-{TODAY}"
OUT_DIR = Path("outputs/step_b_targeted_kg")
OUT_DIR.mkdir(parents=True, exist_ok=True)

client = OpenAI(api_key=OPENAI_API_KEY)
driver = GraphDatabase.driver(NEO4J_URI, auth=(NEO4J_USER, NEO4J_PASSWORD))
```

### 6.2 Load Inputs

```python
def load_cases() -> dict:
    """Load shadow overlay cases, keyed by case_id."""
    cases = {}
    with open("outputs/round3_dual_track_eval_prep/track_b_shadow_overlay/shadow_overlay_eval_ready_cases.jsonl", encoding="utf-8") as f:
        for line in f:
            c = json.loads(line)
            cases[c["case_id"]] = c
    return cases

def load_scorer_contracts() -> dict:
    """Load all scorer contracts (dev+baseline + test), keyed by case_id."""
    contracts = {}
    for path in [
        "outputs/round3_eval_harness/formula_contract_v3_2_clean_dev/clean_dev_scorer_only_target_slot_contracts.jsonl",
        "outputs/round3_eval_harness/formula_contract_v3_2_test_split/test_scorer_contracts.jsonl",
    ]:
        with open(path, encoding="utf-8") as f:
            for line in f:
                c = json.loads(line)
                contracts[c["case_id"]] = c
    return contracts
```

Note: case_id in scorer contracts includes full hash suffix (e.g., `round3_test_016_707dc83f`). Match to shadow overlay using the same full case_id.

### 6.3 Targeted Extraction via GPT-4o-mini

For each case with a valid scorer contract:

1. Build extraction targets from `source_fact_numbers` (metric name + year, WITHOUT expected value)
2. Call GPT-4o-mini to extract from `evidence_text`
3. Parse response

```python
def build_extraction_prompt(ticker: str, evidence_text: str, extraction_targets: list[dict]) -> str:
    """
    extraction_targets: list of dicts with {metric, year, unit_hint}
    """
    targets_str = "\n".join(
        f"  - metric: {t['metric']}, year: {t['year']}, unit expected: {t['unit_hint']}"
        for t in extraction_targets
    )
    
    return f"""You are a precise financial data extractor. Extract the following specific metrics from the 10-K text for company {ticker}.

METRICS TO EXTRACT:
{targets_str}

RULES:
- Extract ONLY the metrics listed above for ONLY the years listed.
- Use the exact numeric value as it appears in the financial statements (do not round).
- If a metric appears in different formats (e.g., both "revenues" and "total revenues"), extract the most prominent standalone line item.
- Provide a short evidence_quote (verbatim substring from the text, max 80 chars).
- If a metric is genuinely not found in the text, set value to null and note it.
- Return JSON only. No explanation outside JSON.

OUTPUT FORMAT:
{{
  "extracted_facts": [
    {{
      "metric": "<metric_name_exactly_as_requested>",
      "year": <int>,
      "value": <float or null>,
      "unit": "<unit_string>",
      "evidence_quote": "<short verbatim quote>"
    }}
  ]
}}

10-K TEXT:
{evidence_text}
"""

def extract_facts_for_case(case: dict, source_facts: list[dict], client: OpenAI) -> list[dict]:
    """
    source_facts: list from scorer contract source_fact_numbers
    Returns: list of extracted fact dicts
    """
    ticker = case["ticker"]
    evidence_text = case.get("evidence_text", "")
    
    # Build extraction targets (NO expected values — avoid leakage)
    targets = []
    for sf in source_facts:
        targets.append({
            "metric": sf["metric"],
            "year": sf["year"],
            "unit_hint": sf.get("unit", "unknown"),
        })
    
    # Deduplicate by (metric, year)
    seen = set()
    deduped = []
    for t in targets:
        key = (t["metric"], t["year"])
        if key not in seen:
            seen.add(key)
            deduped.append(t)
    
    prompt = build_extraction_prompt(ticker, evidence_text, deduped)
    
    response = client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[{"role": "user", "content": prompt}],
        temperature=0.0,
        response_format={"type": "json_object"},
    )
    
    raw = json.loads(response.choices[0].message.content)
    return raw.get("extracted_facts", [])
```

### 6.4 Validation

```python
def validate_extracted_fact(extracted: dict, source_facts: list[dict]) -> dict:
    """
    Compare extracted value against the corresponding source_fact expected value.
    Returns: {match: bool, tolerance_pct: float, expected: float, extracted: float, status: str}
    """
    metric = extracted.get("metric")
    year = extracted.get("year")
    value = extracted.get("value")
    
    # Find matching source fact
    matching = [
        sf for sf in source_facts
        if sf["metric"] == metric and sf["year"] == year
    ]
    
    if not matching:
        return {"status": "no_contract_fact", "match": None}
    
    expected = matching[0]["value"]
    
    if value is None:
        return {"status": "extraction_failed", "match": False, "expected": expected, "extracted": None}
    
    # Compute tolerance: 1% for large values, absolute 0.01 for small (e.g., EPS, ratios)
    if abs(expected) > 1.0:
        tolerance_pct = abs(value - expected) / abs(expected) * 100
        match = tolerance_pct <= 1.0  # within 1%
    else:
        # Small values (EPS, percentages)
        tolerance_abs = abs(value - expected)
        tolerance_pct = tolerance_abs
        match = tolerance_abs <= 0.05
    
    return {
        "status": "matched" if match else "mismatch",
        "match": match,
        "expected": expected,
        "extracted": value,
        "tolerance_pct": round(tolerance_pct, 4),
    }
```

### 6.5 Neo4j Write

```python
def write_facts_to_neo4j(driver, case_id: str, ticker: str, extracted_facts: list[dict], 
                          batch_id: str, validation_results: dict) -> int:
    """
    Write validated (and failed) facts to Neo4j.
    Returns: count of nodes written.
    """
    written = 0
    
    with driver.session(database="neo4j") as session:
        for fact in extracted_facts:
            metric = fact.get("metric")
            year = fact.get("year")
            value = fact.get("value")
            unit = fact.get("unit", "")
            evidence_quote = fact.get("evidence_quote", "")
            
            if value is None:
                continue  # Skip nulls
            
            # Convert value to float
            try:
                float_value = float(value)
            except (TypeError, ValueError):
                continue
            
            validation = validation_results.get(f"{metric}_{year}", {})
            val_status = validation.get("status", "no_contract")
            
            obs_id = f"{batch_id}__{case_id}__{metric}__{year}"
            canonical_name = metric  # Use metric name directly as canonical_name
            
            session.run("""
MERGE (c:LLMCompany {ticker: $ticker})
MERGE (yr:LLMFiscalYear {year: $year})
MERGE (m:LLMFinancialMetric {canonical_name: $canonical_name})
  ON CREATE SET m.display_name = $canonical_name
CREATE (obs:LLMObservation {
    obs_id: $obs_id,
    value: $value,
    unit: $unit,
    evidence_quote: $evidence_quote,
    kg_batch: $batch_id,
    extraction_method: 'targeted_gpt4o_mini',
    validation_status: $val_status,
    case_id: $case_id
})
CREATE (obs)-[:LLM_MENTIONS_COMPANY]->(c)
CREATE (obs)-[:LLM_OBSERVES_METRIC]->(m)
CREATE (obs)-[:LLM_OBSERVED_IN_YEAR]->(yr)
""",
                ticker=ticker,
                year=year,
                canonical_name=canonical_name,
                obs_id=obs_id,
                value=float_value,
                unit=unit,
                evidence_quote=evidence_quote,
                batch_id=batch_id,
                val_status=val_status,
                case_id=case_id,
            )
            written += 1
    
    return written
```

### 6.6 Main Loop

```python
def run_step_b():
    cases = load_cases()
    contracts = load_scorer_contracts()
    
    state = {
        "phase": "running",
        "batch_id": KG_BATCH_NEW,
        "cases_total": 19,
        "cases_processed": 0,
        "facts_extracted": 0,
        "facts_validated_ok": 0,
        "facts_validation_failed": 0,
        "facts_written_to_neo4j": 0,
        "started_at": datetime.datetime.utcnow().isoformat() + "Z",
    }
    save_state(state)
    
    extraction_trace = []
    validation_report = []
    kg_write_log = []
    failed_extractions = []
    
    # Process 19 cases with valid contracts
    for case_id, contract in contracts.items():
        if case_id not in cases:
            # Try to find by prefix match
            matching_case_id = next(
                (cid for cid in cases if cid.startswith('_'.join(case_id.split('_')[:3]))), 
                None
            )
            if not matching_case_id:
                print(f"WARN: case {case_id} not in shadow overlay, skipping")
                continue
            case = cases[matching_case_id]
        else:
            case = cases[case_id]
        
        ticker = case["ticker"]
        sc = contract.get("scorer_only_target_slot_contract", {})
        source_facts = sc.get("source_fact_numbers", [])
        formula_type = sc.get("formula_type", "unknown")
        
        print(f"Processing {case_id} ({ticker}) - {formula_type}: {len(source_facts)} target facts")
        
        # Extract
        try:
            extracted = extract_facts_for_case(case, source_facts, client)
        except Exception as e:
            print(f"  ERROR extracting {case_id}: {e}")
            failed_extractions.append({"case_id": case_id, "error": str(e)})
            continue
        
        # Validate
        val_results = {}
        n_ok = 0
        n_fail = 0
        for ef in extracted:
            metric = ef.get("metric")
            year = ef.get("year")
            vr = validate_extracted_fact(ef, source_facts)
            key = f"{metric}_{year}"
            val_results[key] = vr
            
            if vr.get("match") is True:
                n_ok += 1
            elif vr.get("match") is False:
                n_fail += 1
            
            validation_report.append({
                "case_id": case_id,
                "ticker": ticker,
                "formula_type": formula_type,
                "metric": metric,
                "year": year,
                "extracted_value": ef.get("value"),
                **vr,
            })
        
        # Log extraction
        extraction_trace.append({
            "case_id": case_id,
            "ticker": ticker,
            "formula_type": formula_type,
            "n_target_facts": len(source_facts),
            "n_extracted": len(extracted),
            "n_validated_ok": n_ok,
            "n_validated_fail": n_fail,
        })
        
        # Write to Neo4j
        n_written = write_facts_to_neo4j(driver, case_id, ticker, extracted, KG_BATCH_NEW, val_results)
        kg_write_log.append({"case_id": case_id, "ticker": ticker, "n_written": n_written})
        
        # Update state
        state["cases_processed"] += 1
        state["facts_extracted"] += len(extracted)
        state["facts_validated_ok"] += n_ok
        state["facts_validation_failed"] += n_fail
        state["facts_written_to_neo4j"] += n_written
        save_state(state)
        
        print(f"  -> extracted={len(extracted)}, ok={n_ok}, fail={n_fail}, written={n_written}")
    
    # Save outputs
    save_jsonl(OUT_DIR / "extraction_trace.jsonl", extraction_trace)
    save_jsonl(OUT_DIR / "validation_report.jsonl", validation_report)
    save_jsonl(OUT_DIR / "kg_write_log.jsonl", kg_write_log)
    save_jsonl(OUT_DIR / "failed_extractions.jsonl", failed_extractions)
    
    state["phase"] = "done_primary"
    save_state(state)
    
    return state
```

---

## 7. Best-Effort Extraction for 6 Excluded Dev Cases

After the 19 primary cases, also extract facts for the 6 excluded dev cases (no scorer contract). Use only formula_type heuristics.

**Excluded cases:**

| case_id | ticker | formula_type (from dev_baseline) | years |
|---|---|---|---|
| round3_dev_007 | KR | operating_margin | 2021, 2022, 2023, 2024 |
| round3_dev_009 | LND | operating_margin | 2021, 2022, 2023 |
| round3_dev_012 | MSFT | operating_margin | 2022, 2023, 2024 |
| round3_dev_014 | FOXA | ambiguous_manual_review | 2022, 2023, 2024 |
| round3_dev_018 | BW | operating_margin | 2021, 2022, 2023 |
| round3_dev_020 | CARR | gross_margin | 2021, 2022, 2023 |

**Metrics to extract per formula_type (no validation, best-effort):**

- `operating_margin` → `operating_income`, `revenue` (or `net_sales`, `total_revenues`) for each year
- `gross_margin` → `revenue` (or `net_sales`), `cost_of_sales` (or `cost_of_goods_sold`, `cost_of_products_sold`), for each year
- `ambiguous_manual_review` (FOXA) → extract: `revenue`, `operating_income`, `net_income`, `restructuring_charges`, `depreciation` for years 2022-2024

Load the `years` from the shadow overlay case (`c["years"]`). Mark all as `validation_status = "no_contract"`.

```python
BEST_EFFORT_METRICS = {
    "operating_margin": ["operating_income", "revenue", "net_sales", "total_revenues", "total_net_revenues"],
    "gross_margin": ["revenue", "net_sales", "total_revenues", "cost_of_sales", "cost_of_goods_sold", "cost_of_products_sold", "gross_profit"],
    "ambiguous_manual_review": ["revenue", "operating_income", "net_income", "restructuring_charges", "depreciation_and_amortization"],
}

BEST_EFFORT_CASES = [
    {"case_id_prefix": "round3_dev_007", "ticker": "KR", "formula_type": "operating_margin"},
    {"case_id_prefix": "round3_dev_009", "ticker": "LND", "formula_type": "operating_margin"},
    {"case_id_prefix": "round3_dev_012", "ticker": "MSFT", "formula_type": "operating_margin"},
    {"case_id_prefix": "round3_dev_014", "ticker": "FOXA", "formula_type": "ambiguous_manual_review"},
    {"case_id_prefix": "round3_dev_018", "ticker": "BW", "formula_type": "operating_margin"},
    {"case_id_prefix": "round3_dev_020", "ticker": "CARR", "formula_type": "gross_margin"},
]
```

Build extraction targets from the formula_type metrics × the case's years. No validation step (no expected values). Write to Neo4j with `validation_status = "no_contract"`.

Update state with best-effort counts separately.

---

## 8. Summary Report

After completion, write `outputs/step_b_targeted_kg/step_b_summary.md`:

```markdown
# Step B: KG Targeted Extraction Summary

**Batch ID:** {KG_BATCH_NEW}
**Run date:** {TODAY}
**Cases processed (primary):** {n_primary}
**Cases processed (best-effort):** {n_best_effort}

## Validation Results (19 primary cases)

| Case | Ticker | Formula Type | Target Facts | Extracted | Validated OK | Failed | Match Rate |
|---|---|---|---|---|---|---|---|
...

## Match Rate Summary

| Split | n_cases | avg_match_rate |
|---|---|---|
| test (10) | ... | ... |
| dev+baseline (9) | ... | ... |
| **total (19)** | ... | ... |

## Validation Failures Detail

For each failed fact: {case_id, metric, year, expected, extracted, delta_pct}

## KG Write Summary

- Total nodes written: {total_written}
- With validation_ok: {n_validated_ok_written}
- Best-effort (no contract): {n_best_effort_written}

## Comparison: Round 5 rfr vs Expected Round 6 rfr

| Case | Ticker | R5 rfr (graph) | Expected R6 rfr | Notes |
|---|---|---|---|---|
| round3_test_013 | VRSK | 0.00 | ~1.0 | revenues value fixed |
| round3_test_017 | MPC | 0.00 | ~1.0 | income_from_cont_ops extracted |
| round3_test_004 | XEL | 0.20 | ~1.0 | employee counts extracted |
| round3_test_011 | NXPI | 0.75 | ~1.0 | 6 facts: op_income + revenue |
| round3_test_009 | AMGN | 0.75 | ~1.0 | 8 facts targeted |
| round3_test_018 | BXP | 0.60 | ~1.0 | 7 facts: revenue + expenses |
| round3_test_007 | LOW | 1.00 | 1.0 | values corrected |
| round3_test_012 | GM | 1.00 | 1.0 | values corrected |
| round3_test_014 | MU | 1.00 | 1.0 | values corrected |
| round3_test_016 | APD | 1.00 | 1.0 | values corrected |

## Codex Handoff

Next step: Round 06 eval. Update `codex_prompt_round6_eval.md` with:
- KG_BATCH = "{KG_BATCH_NEW}"
- same 3 methods, same 25 cases as Round 5
- compare against Round 5 baseline (test: vector=0.60, graph=0.0, hybrid=0.10)
```

---

## 9. State Management

```python
def save_state(state: dict):
    with open(OUT_DIR / "state.json", "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2)

def save_jsonl(path: Path, rows: list[dict]):
    with open(path, "w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
```

State fields:
```json
{
  "phase": "done",
  "batch_id": "kg-targeted-ie-v1-20260529",
  "cases_total": 25,
  "cases_processed_primary": 19,
  "cases_processed_best_effort": 6,
  "facts_extracted": 0,
  "facts_validated_ok": 0,
  "facts_validation_failed": 0,
  "facts_written_to_neo4j": 0,
  "primary_match_rate": 0.0,
  "test_match_rate": 0.0,
  "started_at": "...",
  "completed_at": "...",
  "codex_handoff_message": "Step B complete. Check step_b_summary.md. Next: write Round 06 eval spec with KG_BATCH = kg-targeted-ie-v1-YYYYMMDD."
}
```

---

## 10. Checklist

- [ ] Load all 25 shadow overlay cases
- [ ] Load 19 scorer contracts (9 dev+baseline + 10 test)
- [ ] Run targeted extraction for all 19 primary cases
- [ ] Validate all extracted facts against source_fact_numbers
- [ ] Write validated facts to Neo4j (new batch, do not modify existing)
- [ ] Run best-effort extraction for 6 excluded dev cases
- [ ] Write best-effort facts to Neo4j
- [ ] Verify Neo4j write: query `MATCH (obs:LLMObservation {kg_batch: $batch}) RETURN count(obs)` should be > 0
- [ ] Write state.json phase=done
- [ ] Write step_b_summary.md with validation report
- [ ] Final verification: for each test case, the new batch has at least N facts where N = len(source_fact_numbers)

---

## 11. Error Handling

- If GPT-4o-mini returns invalid JSON: retry once, then log to `failed_extractions.jsonl` and continue
- If a specific fact extraction returns `null`: log as extraction_failed, do NOT write to Neo4j
- If Neo4j connection fails: abort with error (do not partial-write)
- If validation match rate for test cases < 50%: log warning in state.json under `"warning"` but still complete
- Rate limit: add `time.sleep(0.1)` between OpenAI calls (not required for gpt-4o-mini at this scale)

---

## 12. Important Notes

1. **Do NOT hardcode expected values into the extraction prompt.** The GPT-4o-mini must extract from evidence_text independently. Expected values are for post-hoc validation only.

2. **metric_canonical naming**: Use the `metric` field from `source_fact_numbers` verbatim (e.g., `"revenues"`, `"operating_income"`, `"income_from_continuing_operations_net_of_tax"`). The Round 06 retrieval filter will match against these.

3. **LULU units**: LULU (baseline_control_003) uses USD_thousands, not millions. Ensure unit is set correctly.

4. **BXP units**: BXP (round3_test_018) uses USD_thousands. All values in thousands.

5. **MPC scale**: MPC revenues are ~$150 billion (150,000 in USD_millions). Normal for a petroleum refiner.

6. **XEL workforce**: Expected `employees=23`, `management=26` — these are likely representation percentages (23% of employees are female, 26% of management are female). The evidence_text for XEL must contain the DEI/ESG section. If not found, log as extraction_failed.

7. **AMGN has two revenue types**: `revenue` (royalties: 1280 and 1522) and `product_sales` (26910 and 24801) and `total_revenue` (28190 and 26323). Extract all three separately as the scorer contract requires all.

8. **GM segment**: `net_sales_tpo` and `cost_of_sales_tpo` are segment-specific (TPO = Truck, Parts and Other). The evidence_text should have the GM segment breakdown table.
