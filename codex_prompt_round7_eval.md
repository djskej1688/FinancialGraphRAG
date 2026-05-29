# Round 07 Evaluation — Codex Spec

**Task label:** `round7_eval`
**라운드 성격:** **Targeted Diagnostic Rerun** — Round 06 test 실패 케이스를 분석한 뒤 scorer/prompt/KG를 수정하는 구조. Clean held-out benchmark 아님. 결과는 diagnostic improvement 주장에만 사용 가능.
**선행 작업:** Step 0~4 → 5-case sanity run → 75-run 본 평가
**Precondition:** `outputs/round6_eval/state.json` → phase=done, graph_beats_vector_test=true
**Baseline:** Round 06 basic (`outputs/round3_eval_runs/round6_eval_20260528_233753/`)

---

## 0. Context and Goal

Round 06 결과 test ac: vector=0.40, graph=0.50. 잔존 5개 실패 케이스 정밀 분류:

| Ticker | 실패 유형 | 근거 | Round 07 수정 |
|---|---|---|---|
| AMGN | **스코어링 버그** | tnr=1.0 (정답) but rfr=0.75<0.8 → faith=False → ac=0.0 | Pre-work A: scorer fix |
| GM | **모델 추론** | final_answer에 pp_change만, 2021·2023 절댓값 누락 (tnr=0.33) | Pre-work B: Prompt v3.3 |
| MU | **모델 추론** | 2024만 계산, 2022·2023 스킵 (tnr=0.5) | Pre-work B: Prompt v3.3 |
| BXP | **모델 추론** | 2023 arithmetic 오류 (2022=34.2% 맞음, 2023=10.4% 틀림) (tnr=0.5) | Pre-work B: Prompt v3.3 |
| XEL | **KG 메트릭 오류** | KG: employees=11311 (total), 필요: female_employee_pct=23 | Pre-work C: KG patch |

**목표:** test ac 0.50 → **0.60~0.80** (현실적), 0.80~0.90 (최상)

**Claim boundary:** `targeted_diagnostic_rerun_r06_failure_cases` — 이 결과로 "GraphRAG > VectorRAG 일반 우위"를 주장하면 안 됨. Round 06 실패 케이스에 맞춘 개선이므로.

---

---

## 0-bis. 실행 순서 (GPT 검토 반영)

```
Step 0  : 라운드 성격 선언 (targeted diagnostic, not benchmark)
Step 1  : R6 raw traces → scorer v7 no-model rescore → R6_rescored baseline 생성
Step 2  : Pre-work A 구현 (scorer fix)  +  unit test
Step 3  : Pre-work B 구현 (prompt v3.3) +  prompt hash 저장
Step 4  : Pre-work C 준비 (XEL patch)
           - evidence exact quote 확인
           - before snapshot 저장
           - rollback Cypher 생성
           - approval file 생성 → patch 실행은 approval 후만
Step 5  : 5-case sanity run (AMGN/GM/MU/BXP/XEL × 3 methods = 15 runs)
           - 기대 검증 항목 확인 후 진행
Step 6  : 75-run full diagnostic rerun
Step 7  : Report (R6_original / R6_rescored / R7 / per-case delta / claim boundary)
```

---

## 1. Security Constraints

Round 06와 동일:
- `OPENAI_API_KEY` 환경변수에서만
- Neo4j credentials `.env`에서
- `neo4j_write_performed = False` (Pre-work C에서만 예외. approval file + before snapshot + rollback Cypher 준비 후 write 허용)
- locked test directory 접근 금지
- DO NOT commit `.env`

---

## 1-bis. Step 1: R6 No-Model Rescore (R6_rescored baseline)

scorer v7 (faith gate 제거)를 Round 06 raw traces에 적용해 baseline 생성.
이 결과는 "scorer 변경 단독 효과"를 격리하는 데 사용됨.

```python
# scripts/r6_rescore_v7.py
# Input:  outputs/round3_eval_runs/round6_eval_20260528_233753/round6_traces.jsonl
# Output: outputs/round6_eval/r6_rescored_v7.jsonl
#         outputs/round6_eval/r6_rescore_v7_summary.md

# 각 trace에서 기존 final_answer, calculation, neo4j_facts를 재사용
# 새 scorer (faith gate 없음) 적용
# model API 호출 없음 (no-model rescore)
# trace에 scoring_version="v7_no_faith_gate" 추가

# 출력 summary:
# | Method | R6_original_ac | R6_rescored_ac | delta_scorer |
# AMGN에서 delta가 있어야 함 (0.0 → 1.0 예상)
# 다른 케이스는 delta=0 이어야 함 (scorer fix는 AMGN만 영향)
```

**State file 추가:** `outputs/round6_eval/state.json`에 아래 필드 append:
```json
{
  "r6_rescored_v7": "outputs/round6_eval/r6_rescored_v7.jsonl",
  "r6_rescored_test_ac_graph": 0.0,   // 채점 후 기입
  "r6_rescored_test_ac_vector": 0.0,
  "r6_rescored_test_ac_hybrid": 0.0
}
```

**예상 결과:**
- AMGN graph_neo4j_v6 rescored ac: 0.0 → 1.0 (faith gate만 제거해도 tnr=1.0이므로)
- 나머지 24개 케이스: R6_original과 동일
- R6_rescored test ac graph: 0.50 → **0.60** (AMGN +0.10)
- 이 0.60이 Round 07과 비교할 공정한 baseline

---

## 2. Pre-work A: Scorer Fix (rfr → ac gate 분리)

**문제:** `score_result()`의 `ans = numeric_ok and fmt and calc and faith`에서 `faith = (rfr >= 0.8)`.
AMGN은 tnr=1.0 (두 target slot 모두 정답)인데 rfr=0.75 < 0.8이라 ac=0.0.

rfr은 retrieval 품질 지표이지 answer 품질 지표가 아님. source_fact_numbers에 KG에 없는 팩트(royalty_revenue)가 포함돼 있어서 AMGN graph는 구조적으로 rfr=1.0 불가능.

**수정:**

```python
# Round 07 score_result()에서:
# BEFORE (Round 06):
faith = rfr >= 0.8
ans = numeric_ok and fmt and calc and faith

# AFTER (Round 07):
# rfr은 standalone metric으로 유지, ac gate에서 제거
ans = numeric_ok and fmt and calc
# faith는 계산은 하되 ans에 영향 없음
faith = rfr >= 0.8  # 기록용만
```

`failure_reason` 로직도 업데이트:
```python
failure = "none"
if not fmt:
    failure = "answer_format_error"
elif not numeric_ok:
    failure = "formula_target_mismatch"
elif not ans:
    failure = "scoring_uncertain"
# rfr 관련 failure는 rfr 자체가 낮아도 ans=True면 "none"
```

---

## 3. Pre-work B: Prompt v3.3

### 3.1 새 파일 생성

`outputs/round3_dual_track_eval_prep/prompt_formatter_v3_2/prompt_v3_3_system.md`:

```markdown
# Prompt v3.3 System

You answer multi-fact financial reasoning questions using only the context provided for the selected method.

Rules that apply to every method:
- Use the same answer format, rounding rules, and scoring expectations.
- Do not use outside knowledge.
- Do not infer missing source facts from the expected answer.
- Do not invent fact ids, citations, companies, tickers, metrics, years, or values.
- Keep source facts separate from derived calculations.
- Return JSON only.

## Multi-year requirement (NEW in v3.3)
When the formula contract specifies multiple target_years or comparison_periods, you MUST:
1. Compute the required formula for EACH year separately.
2. Include EACH year's result as a distinct value in `final_answer`.
3. Do NOT substitute individual year values with only a summary (e.g., "change of X%").
   Include both the absolute values per year AND the summary change when both are required.
4. Verify: before writing `final_answer`, count the required outputs in the formula contract
   and confirm every required year/metric appears explicitly.

## Answer completeness check (NEW in v3.3)
Before writing `final_answer`:
- List all required outputs from the formula contract (e.g., margin_2021, margin_2023, delta_pp).
- Confirm each appears numerically in `final_answer`.
- If any required output is missing, add a calculation step for it before finalizing.

## Arithmetic verification (NEW in v3.3)
For each calculation step:
- State the formula.
- Plug in the exact source values.
- Show the intermediate result.
- If computing (A - B) / A, verify A > B when physically expected (e.g., revenue > expenses).
  If A < B unexpectedly, flag in `uncertainty_or_missing_information`.

Formula Target Contract v3.2:
- Every method receives the same model-visible formula contract.
- Use the formula contract to determine the target formula and ALL required output slots.
- Do not treat source fact numbers as final answer targets unless the contract asks for them.

Method isolation is mandatory. The only difference between methods is the allowed context source.
```

### 3.2 스크립트에서 prompt_v3_3_system.md 로드

```python
# round7_eval.py build_prompt()에서:
system = (pdir / "prompt_v3_3_system.md").read_text(encoding="utf-8")
```

---

## 4. Pre-work C: XEL KG Patch

### 4.1 현재 XEL KG 상태

```
KG (kg-targeted-ie-v1-20260528) XEL 팩트:
- metric_canonical="employees"  year=2023  value=11311  unit="employees"
- metric_canonical="management" year=2023  value=26     unit="%"
```

### 4.2 필요한 값

scorer contract 기준:
- `female_employee_pct` = 23.0 (%)   ← employees=11311은 total headcount, 불필요
- `female_management_pct` = 26.0 (%) ← management=26은 이미 올바른 값, metric명만 수정

모델 visible contract:
- formula: `female_management_percent / female_employee_percent`
- numerator: `female_management_pct`
- denominator: `female_employee_pct`

### 4.3 패치 방법

Neo4j에서 기존 XEL 관측값 업데이트 또는 신규 추가:

```python
# option A: metric_canonical 업데이트
# UPDATE employees → female_employee_pct (value=23.0)
# UPDATE management → female_management_pct (value=26.0, 그대로)

# option B: deprecated 처리 후 신규 추가 (권장)
# 기존 2개 obs: validation_status="deprecated_r7_patch"
# 신규 추가: female_employee_pct=23.0, female_management_pct=26.0
# kg_batch: "kg-targeted-ie-v1-20260528"  (동일 batch, patch 태그만 추가)
# obs_id에 "_r7patch" suffix
```

**Option B를 권장.** 기존 obs 변경 없이 신규 팩트 추가, validation_status로 구분.

### 4.4 XEL patch 실행 전 필수 조건 (GPT 검토 반영)

아래 조건 없이는 patch 실행 금지:

```
1. evidence_text에서 23%, 26% exact quote 확인 및 저장
2. before snapshot 저장:
   MATCH (obs:LLMObservation) WHERE obs.case_id='round3_test_004_b035aeed'
   RETURN obs.obs_id, obs.metric_canonical, obs.value, obs.validation_status
   → outputs/round7_eval/xel_patch_before_snapshot.json
3. rollback Cypher 생성 (추가한 obs_id 리스트 포함)
   → outputs/round7_eval/xel_patch_rollback.cypher
4. approval file 생성:
   → outputs/round7_eval/xel_patch_approval.json
   {
     "approved_by": "user",
     "approved_at": "...",
     "evidence_quotes_verified": true,
     "female_employee_pct_quote": "...",
     "female_management_pct_quote": "...",
     "patch_scope": "round3_test_004_b035aeed only",
     "existing_obs_action": "deprecated_flag_only_no_delete"
   }
5. 신규 obs만 추가, 기존 obs 삭제 금지
6. after snapshot 저장 및 before와 비교
7. write query log 저장
```

새 obs:
```
obs_id: "kg-targeted-ie-v1-20260528__round3_test_004_b035aeed__female_employee_pct__2023__r7patch"
metric_canonical: "female_employee_pct"
value: 23.0
unit: "%"
year: 2023
ticker: "XEL"
case_id: "round3_test_004_b035aeed"
kg_batch: "kg-targeted-ie-v1-20260528"
validation_status: "r7_patch_ok"
evidence_quote: "23% of our employees are female"  (또는 실제 evidence에서 추출)

obs_id: "kg-targeted-ie-v1-20260528__round3_test_004_b035aeed__female_management_pct__2023__r7patch"
metric_canonical: "female_management_pct"
value: 26.0
unit: "%"
year: 2023
ticker: "XEL"
case_id: "round3_test_004_b035aeed"
kg_batch: "kg-targeted-ie-v1-20260528"
validation_status: "r7_patch_ok"
evidence_quote: "26% of our senior management are female"  (실제 evidence 확인 필요)
```

**evidence_text에서 실제 값 검증 필수** — evidence_text에서 XEL 케이스의 female workforce % 직접 확인 후 값 사용.

shadow_overlay_eval_ready_cases.jsonl에서 case_id="round3_test_004_b035aeed" 케이스의 evidence_text를 읽고 female management %, female employee %를 직접 추출.

### 4.4 Neo4j write 허용 범위

이 pre-work에서만 `neo4j_write_performed = True` 허용.
Round 07 eval 본 실행에서는 `neo4j_write_performed = False` 유지.

---

## 5. Input Files (unchanged from Round 06)

| File | Description |
|---|---|
| `outputs/round3_dual_track_eval_prep/track_b_shadow_overlay/shadow_overlay_eval_ready_cases.jsonl` | 25 cases |
| `outputs/round3_eval_harness/formula_contract_v3_2_clean_dev/clean_dev_scorer_only_target_slot_contracts.jsonl` | 9 dev scorer contracts |
| `outputs/round3_eval_harness/formula_contract_v3_2_test_split/test_scorer_contracts.jsonl` | 10 test scorer contracts |
| `outputs/round3_eval_harness/formula_contract_v3_2_clean_dev/clean_dev_model_visible_formula_contracts.jsonl` | 9 dev model-visible |
| `outputs/round3_eval_harness/formula_contract_v3_2_test_split/test_model_visible_contracts.jsonl` | 10 test model-visible |

---

## 6. Output Files

```
outputs/round7_eval/
  state.json

outputs/round3_eval_runs/round7_eval_{YYYYMMDD_HHMMSS}/
  round7_traces.jsonl          # 75 traces (25 × 3 methods)
  round7_summary.md
  neo4j_facts_cache.jsonl
```

---

## 7. Methods

| Method | Context | 변경사항 |
|---|---|---|
| `vector_only_v7` | evidence_text only | 프롬프트 v3.3 |
| `graph_neo4j_v7` | Neo4j KG only | 프롬프트 v3.3 + scorer fix + XEL patch |
| `hybrid_neo4j_v7` | evidence_text + Neo4j KG | 프롬프트 v3.3 + scorer fix + XEL patch |

**KG batch:** `kg-targeted-ie-v1-20260528` (XEL patch 포함)

---

## 8. Scoring Changes (vs Round 06)

```python
# Round 07 score_result():
ans = numeric_ok and fmt and calc   # faith 제거
# rfr은 그대로 계산·기록하되 ans gate에서 제거

# failure_reason 로직:
failure = "none"
if not fmt:
    failure = "answer_format_error"
elif not numeric_ok:
    failure = "formula_target_mismatch"
elif not ans:
    failure = "scoring_uncertain"
# "required_fact_missing" 제거 (rfr이 낮아도 ans에 영향 없으므로 별도 failure category 불필요)
# rfr은 trace에 기록, required_fact_recall 필드 그대로 유지
```

---

## 9. Trace Schema

Round 06와 동일, 변경사항:

```python
trace = {
    ...
    "round": "round7",
    "method": "vector_only_v7 / graph_neo4j_v7 / hybrid_neo4j_v7",
    "prompt_version": "v3.3",
    "scoring_version": "v7_no_faith_gate",
    "xel_kg_patched": True,   # Pre-work C 완료 여부
    ...
}
```

---

## 10. Summary Report

`round7_summary.md`:

### 10.1 R6 → R7 delta (test split, 3-way 비교)

| Method | R6_original | R6_rescored | R7 | delta_scorer | delta_combined |
|---|---:|---:|---:|---:|---:|
| vector_only | 0.40 | ~0.40 | | ~0.0 | |
| graph_neo4j | 0.50 | ~0.60 | | ~+0.10 | |
| hybrid_neo4j | 0.40 | ~0.40 | | ~0.0 | |

- `delta_scorer` = R6_rescored - R6_original (scorer definition 변경 단독 효과)
- `delta_combined` = R7 - R6_rescored (prompt v3.3 + XEL patch 실질 효과)
- R7 vs R6_original은 참고용으로만 기재 (혼합 효과라 주요 비교 아님)

### 10.2 Per-case test breakdown

Focus on the 5 previously failing cases:

| ticker | formula_type | R6_graph_ac | R7_graph_ac | delta | resolved_by |
|---|---|---:|---:|---:|---|
| AMGN | gross_margin | 0.0 | | | scorer_fix |
| GM | tpo_segment_gross_margin | 0.0 | | | prompt_v3.3 |
| MU | net_margin_and_nonop_impact | 0.0 | | | prompt_v3.3 |
| BXP | operating_margin | 0.0 | | | prompt_v3.3 |
| XEL | workforce_ratio | 0.0 | | | kg_patch |

### 10.3 Key diagnostic questions

1. AMGN: `tnr=1.0` with scorer fix → ac=1.0? (faith 제거로 즉시 fix 예상)
2. MU: 2022·2023·2024 모두 final_answer에 출력?
3. GM: 2021 절댓값 + 2023 절댓값 + pp_change 모두 출력?
4. BXP: 2023 arithmetic error 해소?
5. XEL: female_employee_pct=23 KG 팩트로 ratio 계산 성공?

---

## 10-bis. 5-Case Sanity Run (Step 5)

75-run 전에 아래 케이스만 먼저 실행:

```
AMGN / GM / MU / BXP / XEL × 3 methods = 15 runs
```

**통과 기준:**

| 케이스 | 확인 항목 | 기대 결과 |
|---|---|---|
| AMGN | failure_reason | "none" (scorer fix 검증) |
| MU | matched_target_slots | net_margin_2022, net_margin_2023, net_margin_2024 모두 포함 |
| GM | matched_target_slots | tpo_gross_margin_2021 포함 |
| BXP | final_answer 2023 값 | 10.4% 아닌 다른 값 (개선 여부) |
| XEL | neo4j_facts_count | 2 (female_employee_pct, female_management_pct) |

**sanity run 실패 시:** 본 75-run 중단. 해당 케이스 원인 재분석.

sanity run output: `outputs/round7_eval/sanity_run_15.jsonl` (별도 저장)

---

## 11. State File

`outputs/round7_eval/state.json`:

```json
{
  "phase": "done",
  "round": "round7",
  "kg_batch": "kg-targeted-ie-v1-20260528",
  "xel_kg_patched": true,
  "prompt_version": "v3.3",
  "scoring_version": "v7_no_faith_gate",
  "cases_total": 25,
  "runs_total": 75,
  "runs_completed": 75,
  "runs_failed": [],
  "methods": ["vector_only_v7", "graph_neo4j_v7", "hybrid_neo4j_v7"],
  "run_dir": "outputs/round3_eval_runs/round7_eval_{timestamp}/",
  "test_ac_vector": 0.0,
  "test_ac_graph": 0.0,
  "test_ac_hybrid": 0.0,
  "graph_beats_vector_test": false,
  "started_at": "...",
  "completed_at": "...",
  "codex_handoff_message": "Round 7 complete. Check round7_summary.md."
}
```

---

## 12. Checklist

### Pre-work A (Scorer fix)
- [ ] `score_result()`: `ans = numeric_ok and fmt and calc` (faith 제거)
- [ ] failure_reason 로직 업데이트 (required_fact_missing 제거)

### Pre-work B (Prompt v3.3)
- [ ] `prompt_v3_3_system.md` 생성 (multi-year, completeness check, arithmetic verification 포함)
- [ ] `build_prompt()`: system = `prompt_v3_3_system.md` 로드

### Pre-work C (XEL KG patch)
- [ ] shadow_overlay evidence_text에서 XEL female workforce % 직접 추출·검증
- [ ] Neo4j에 female_employee_pct=23, female_management_pct=26 추가 (r7patch obs)
- [ ] 기존 employees=11311 obs에 validation_status="deprecated_r7_patch" 업데이트
- [ ] 패치 결과 검증: `MATCH (obs:LLMObservation {kg_batch:'kg-targeted-ie-v1-20260528'}) WHERE obs.case_id='round3_test_004_b035aeed' RETURN obs.metric_canonical, obs.value`

### Round 07 Eval
- [ ] 75 evaluations 실행 (25 × 3)
- [ ] AMGN: failure_reason="none" 확인 (scorer fix 검증)
- [ ] MU: 2022·2023 target slots matched 확인
- [ ] GM: tpo_gross_margin_2021 matched 확인
- [ ] XEL: female_employee_pct 팩트 retrieve 확인
- [ ] round7_summary.md R6→R7 delta 포함
- [ ] state.json phase=done

---

## 13. Notes

### 개선 확실성 (R6_rescored 기준 delta)
- **AMGN**: scorer fix로 R6_rescored에서 이미 ac=1.0. R7 delta는 0 (scorer 효과는 Step 1에서 분리됨).
- **MU fix 확실성 높음** — "each year separately" 명시 시 gpt-4o-mini 준수 가능성 >80%. delta_combined +0.10 기대.
- **GM fix 확실성 중간** — 2021 절댓값 포함 여부. tpo_gross_margin_2021 expected value 확인 필요 (12.0%가 tolerance 내인지). delta_combined +0.00~0.10.
- **BXP fix 확실성 낮음** — arithmetic 오류. gpt-4o-mini 한계. delta_combined +0.00~0.10.
- **XEL fix 확실성 높음** — 올바른 metric으로 KG patch 시 retrieval + 계산 성공 가능. delta_combined +0.10 기대.

### 현실적 기대치 (R6_rescored baseline 기준)
- 보수적: delta_combined +0.10~0.20 → R7 graph test ac: 0.70~0.80
- 최상: delta_combined +0.30 → R7 graph test ac: 0.90
- BXP가 안 고쳐지면: +0.20 → 0.80

### Claim 제한 (GPT 검토 반영)
말해도 되는 것:
```
Round 07은 Round 06 test failure 원인별 targeted intervention 결과다.
- AMGN: scorer gate 버그 수정으로 개선됨 (R6_rescored에서 확인)
- MU/GM: 프롬프트 multi-year 지시 추가로 개선됨
- XEL: KG metric 오류 수정으로 개선됨
```
말하면 안 되는 것:
```
GraphRAG가 VectorRAG보다 일반적으로 우수하다.
Round 07은 clean benchmark다.
XEL patch가 포함된 결과를 원래 KG 자연 성능이라고 부를 수 있다.
```
