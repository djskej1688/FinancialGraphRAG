# Round 3B Recovery Report

Created: 2026-05-28  
Scope: Round 3B recovery planning and candidate-pool diagnosis  
Status: planning/reporting only  

## Executive Summary

Round 3B is not a restart from scratch.

The current diagnosis is:

- The repaired Round 3 subset had fact-level readiness.
- The latest locked Track B test was validity-limited because scorer target slots were missing or ambiguous for most test cases.
- The core missing layer is not evidence recovery, but score-level readiness:
  - formula contract
  - scorer-only target slots
  - oracle scorer sanity checks

The key discovery for Round 3B is that the original `round3_selected_cases.jsonl` pool still contains additional candidates outside the repaired 25-case subset.

However, these remaining candidates are not automatically clean. They were previously excluded from the repaired subset because they had company/ticker or required-fact semantic issues. They may still be salvageable through a targeted repair and formula-contract retrofit pass.

## Source Files Reviewed

Primary source:

```text
outputs/round3_case_factory/round3_selected_cases.jsonl
```

Review/reference files:

```text
outputs/round3_case_factory_review/company_ticker_issues.jsonl
outputs/round3_case_factory_review/required_fact_semantic_issues.jsonl
outputs/round3_case_factory_review/round3_cases_to_fix_or_exclude.jsonl
outputs/round3_case_factory_repaired/eval_ready_cases.jsonl
outputs/round3_eval_harness/formula_contract_v3_2_clean_dev/excluded_formula_contracts.jsonl
outputs/round3_dual_track_eval_prep/dev_rerun_approval_v3_2_clean_dev/v3_2_clean_dev_rerun_case_list.json
outputs/round3_dual_track_eval_prep/track_b_shadow_overlay/shadow_overlay_test_cases.json
```

## Current Pool Accounting

### Repaired Eval-Ready Pool

File:

```text
outputs/round3_case_factory_repaired/eval_ready_cases.jsonl
```

Counts:

- total repaired eval-ready cases: 25
- locked Track B test cases: 10
- v3.2 clean dev/baseline cases: 9
- v3.2 ambiguous excluded cases: 6
- union: 25
- remaining inside repaired pool: 0

Conclusion:

The repaired 25-case subset is fully accounted for. There are no unused repaired cases left.

### Original Selected Pool

File:

```text
outputs/round3_case_factory/round3_selected_cases.jsonl
```

Counts:

- total original selected cases: 50
- cases already consumed or excluded by recent v3.2 flow: 25
- remaining original selected cases: 25

Of the remaining 25:

- baseline_control: 2
- round3_dev: 8
- round3_test: 10
- integration_demo: 5

Because integration demo cases must not be mixed into the scoring benchmark, the practical Round 3B recovery candidate set is:

- baseline_control: 2
- round3_dev: 8
- round3_test: 10
- total practical candidates: 20

## Remaining 20 Practical Candidates

### Baseline

```text
baseline_control_001_7b6ec08b
baseline_control_002_bc20b319
```

### Dev

```text
round3_dev_001_da2a2fad
round3_dev_002_1e2ee4b4
round3_dev_004_11abd756
round3_dev_005_25638981
round3_dev_006_8b9544ef
round3_dev_008_e69805f4
round3_dev_013_6c9047e6
round3_dev_015_df730bd7
```

### Test

```text
round3_test_001_2529e04e
round3_test_002_d3d8efd4
round3_test_003_b8a1383c
round3_test_005_4330b7f9
round3_test_006_08117364
round3_test_008_19b392a0
round3_test_010_6d2cfe43
round3_test_015_536b783d
round3_test_019_3734e04b
round3_test_020_6ee222c0
```

## Why These 20 Were Not In The Repaired Pool

These cases were not merely skipped due to workflow ordering.

They appear in the review outputs as requiring repair or exclusion, usually with:

```text
recommended_action = fix_company_ticker_or_required_facts_then_revalidate
```

Common reasons:

- bad or missing ticker
- company field is actually a section heading
- company field is `Unknown`, `Employees`, `NA`, or similar
- ticker contains metric-like values such as `GPM`, `NP`, or `LT`
- required fact semantic issues

So the Round 3B task is not just:

```text
contract retrofit only
```

It is more accurately:

```text
light identity/fact repair + formula contract retrofit + scorer target slot generation
```

## Candidate Quality Observations

### Likely Red / Drop Candidates

The proposed RED drop list is reasonable:

```text
baseline_control_001_7b6ec08b   company=Long-Term Debt  ticker=LT
round3_dev_005_25638981         company=NA              ticker=NA
round3_test_002_d3d8efd4        company=Unknown         ticker=empty
round3_test_005_4330b7f9        company=Unknown         ticker=empty
```

Reason:

- identity is too weak or unresolvable under a no-inference rule
- these should not enter formula contract generation unless manually repaired from source outside this automated pass

### Higher-Priority Candidates

Cases with no company/ticker issue in the earlier review should be prioritized:

```text
baseline_control_002_bc20b319
round3_dev_004_11abd756
round3_dev_015_df730bd7
round3_test_008_19b392a0
round3_test_020_6ee222c0
```

Note:

- `round3_dev_015_df730bd7` still has a stale ticker correction from `BRCM` to `AVGO`.
- These are not guaranteed score-ready; they are simply less identity-risky.

### Repairable Yellow Candidates

The proposed explicit repair table is a good approach because it avoids open-ended company/ticker inference.

Examples:

```text
round3_dev_001_da2a2fad -> Ameren Corporation / AEE
round3_dev_002_1e2ee4b4 -> Fastenal Company / FAST
round3_dev_008_e69805f4 -> Prudential Financial, Inc. / PRU
round3_dev_013_6c9047e6 -> Western Digital Corporation / WDC
round3_test_003_b8a1383c -> Cboe Global Markets, Inc. / CBOE
round3_test_015_536b783d -> W. R. Berkley Corporation / WRB
round3_test_019_3734e04b -> Ventas, Inc. / VTR
round3_dev_006_8b9544ef -> ConocoPhillips / COP
round3_test_006_08117364 -> FirstEnergy Corp. / FE
round3_dev_015_df730bd7 -> Broadcom Inc. / AVGO
```

## Evaluation Of Proposed Round 3B Prompt

The proposed prompt is directionally strong.

Strong points:

- separates RED drop cases
- repairs company/ticker only through an explicit table
- forbids touching the locked test run directory
- creates gate checkpoints
- separates model-visible formula contract from scorer-only target slots
- forbids expected numeric values in model-visible formula contracts
- requires target slots before numeric scoring
- adds oracle sanity checks before model calls

This is the right correction to the previous gate-ordering failure.

## Required Prompt Improvements Before Execution

The proposed prompt should be slightly hardened before use.

### 1. Explicit Final Processing Set

The prompt says 16 candidates, but GREEN/YELLOW wording can be misread.

Add:

```text
After RED drops, the final processing set is exactly 16 case_ids.
Do not add integration_demo cases.
Do not add locked-test cases.
Do not add v3.2 clean dev/baseline cases.
Do not add v3.2 ambiguous excluded cases.
```

### 2. Add Required Facts As Input

The current prompt names:

```text
round3_selected_cases.jsonl
company_ticker_issues.jsonl
required_fact_semantic_issues.jsonl
```

It should also include:

```text
outputs/round3_case_factory/round3_required_facts.jsonl
```

Reason:

Scorer target slots should not be generated by parsing `evidence_text` alone. Required facts provide structured source values. The safer rule is:

```text
expected_value must be derived from required facts and cross-checked against evidence_text or evidence_quote_exact.
```

### 3. Do Not Let Narrative-Only Cases Into Recovery Test

The prompt allows:

```text
formula_type = narrative_only
target_slots = []
```

This is acceptable for diagnostic or baseline narrative analysis, but dangerous for the recovery test benchmark.

Add:

```text
Recovery test may include numeric cases only.
Narrative-only cases must be written to narrative_diagnostic_cases.jsonl and excluded from scored recovery_test.
```

Otherwise the same "no target slot" problem can re-enter under a legal label.

### 4. Expected Answer Should Be Validation-Only

The prompt currently allows formula generation from:

```text
evidence_text + expected_answer
```

Safer wording:

```text
Use expected_answer only as a secondary consistency check.
Do not infer formula type solely from expected_answer.
Formula type must be supported by question intent + evidence_text + required facts.
```

### 5. Expand Oracle Negative Controls

The prompt currently tests:

- oracle_correct
- blank
- wrong_formula

Add:

- wrong_year_or_period
- wrong_denominator
- source_fact_only_no_derived_target

Reason:

The previous failure mode allowed high RFR/faithfulness while numeric/answer correctness was invalid. A source-fact-only answer must not receive full answer correctness when the task requires a derived numeric target.

### 6. Split Numeric And Narrative Outputs

Add output files:

```text
numeric_eval_ready_cases.jsonl
narrative_diagnostic_cases.jsonl
```

Final `eval_ready_cases.jsonl` can contain all passable cases, but benchmark-scored recovery test should be selected only from numeric cases.

## Recommended Round 3B Gate Flow

### Phase 0. Drop Red Cases

Output:

```text
outputs/round3b_recovery/dropped_cases.json
```

Hard gate:

- exactly 4 dropped cases

### Phase 1. Company/Ticker Repair

Output:

```text
outputs/round3b_recovery/repaired_cases.jsonl
outputs/round3b_recovery/checkpoints/gate1_report.md
```

Hard gate:

- company not in invalid set
- ticker not in invalid set
- evidence text present
- only explicit table repairs allowed

### Phase 2. Formula Contract Generation

Output:

```text
outputs/round3b_recovery/formula_contracts.jsonl
outputs/round3b_recovery/checkpoints/gate2_report.md
```

Hard gate:

- no ambiguous formula for benchmark candidates
- target years present
- no expected numeric value in model-visible contract

### Phase 3. Scorer Target Slot Generation

Output:

```text
outputs/round3b_recovery/scorer_contracts.jsonl
outputs/round3b_recovery/checkpoints/gate3_report.md
```

Hard gate:

- numeric cases must have target_slot_count > 0
- expected values derived from required facts and cross-checked against evidence
- no null expected values

### Phase 4. Oracle Sanity Check

Output:

```text
outputs/round3b_recovery/checkpoints/gate4_report.md
```

Hard gate:

- oracle_correct pass
- blank fail
- wrong_formula lower/fail
- wrong_year lower/fail
- source_fact_only lower/fail

### Phase 5. Assemble Recovery Pool

Output:

```text
outputs/round3b_recovery/eval_ready_cases.jsonl
outputs/round3b_recovery/numeric_eval_ready_cases.jsonl
outputs/round3b_recovery/narrative_diagnostic_cases.jsonl
outputs/round3b_recovery/recovery_summary.json
```

Hard gate:

- recovery_test numeric cases >= 4
- if fewer than 4, do not create scored recovery test split

## Strategic Interpretation

The path is not:

```text
throw away all Round 3 cases and start from scratch
```

The path is:

```text
reuse original selected pool candidates where possible,
repair identity fields under explicit constraints,
retrofit formula/scorer contracts,
then pass oracle scoring gates before any model call.
```

This is a much better use of existing work.

## Expected Outcomes

Before running the recovery script, plausible outcomes are:

### If 15+ cases pass

Good recovery pool.

Possible split:

- dev: 6-8
- test: 5-6
- baseline: 2-3

### If around 10 cases pass

Still usable.

Recommended:

- run dev/baseline first
- keep test minimal
- do not overclaim

### If fewer than 10 cases pass

Do not force it.

Recommended:

- preserve validated cases
- consider drawing from expanded/max-quality candidate pools
- do not reuse contaminated locked test outputs

## Safety Rules To Preserve

- Do not modify locked test run:
  `outputs/round3_eval_runs/locked_test_v3_2_track_b_20260528_145253/`
- Do not use locked test model outputs to tune prompts/scorers/contracts.
- Do not run model/API during Round 3B recovery pool construction.
- Do not run full eval.
- Do not write to Neo4j.
- Do not apply KG patches.
- Do not include integration demo cases in benchmark scoring.
- Do not put expected numeric values in model-visible formula contracts.

## Recommended Next Action

Run a no-model Round 3B recovery builder over the 16 non-red practical candidates with the strengthened gates above.

The immediate question to answer is:

```text
How many of the 16 candidates survive Gate 1 through Gate 4?
```

That answer determines whether Round 3B can proceed with:

- enough recovery dev/test/baseline cases,
- a minimal recovery test only,
- or an expanded candidate search.

