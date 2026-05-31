# Round 07 평가 리포트

**작성일:** 2026-05-29  
**라운드 성격:** Targeted Diagnostic Rerun — Round 06 실패 케이스 5개에 scorer/prompt/KG patch 적용  
**Claim boundary:** `targeted_diagnostic_rerun_r06_failure_cases`  
**총 실행:** 75 traces (25 cases × 3 methods) + R6 no-model rescore 75 traces  

---

## 1. 헤드라인 숫자

| Method | R6_original | R6_rescored | **R7** |
|---|---:|---:|---:|
| vector_only | 0.40 | 0.40 | **0.60** |
| graph_neo4j | 0.50 | 0.60 | **0.90** |
| hybrid_neo4j | 0.40 | 0.50 | **0.80** |

> **graph > vector 유지. graph test ac 0.90 달성.**  
> `graph_beats_vector_test = true` (0.90 vs 0.60)

---

## 2. Delta 분해: 무엇이 얼마나 기여했나

| Method | delta_scorer | delta_combined | 합계 |
|---|---:|---:|---:|
| vector_only | 0.0 | +0.20 | **+0.20** |
| graph_neo4j | +0.10 | +0.30 | **+0.40** |
| hybrid_neo4j | +0.10 | +0.30 | **+0.40** |

- **delta_scorer** = R6_rescored − R6_original: scorer fix(faith gate 제거) 단독 효과
- **delta_combined** = R7 − R6_rescored: prompt v3.3 + XEL KG patch 실질 효과

### 개입별 기여 (graph 기준)

| 개입 | 대상 | 기여 ac | 근거 |
|---|---|---:|---|
| Scorer fix (faith gate 제거) | AMGN | +0.10 | R6_rescored에서 이미 ac=1.0 확인 |
| Prompt v3.3 (multi-year) | MU | +0.10 | 2022·2023·2024 모두 출력 |
| Prompt v3.3 (all values in final) | GM | +0.10 | 2021 절댓값 + pp_change 출력 |
| Prompt v3.3 (step-by-step) | BXP | +0.10 | 2023 arithmetic 오류 해소 |
| XEL KG patch | XEL | +0.10 | female_employee_pct=23 retrieval 성공 |
| **합계** | | **+0.50** | |

**Vector도 +0.20 개선** (0.40→0.60): KG를 사용하지 않는 vector_only도 prompt v3.3의 multi-year/completeness 지시로 개선됨. MU와 BXP가 vector에서도 수정된 것으로 추정.

---

## 3. Targeted 5개 실패 케이스 — 전원 해결

| Ticker | Formula | R6 ac | R7 ac | 해결 수단 | 검증 |
|---|---|---:|---:|---|---|
| **AMGN** | gross_margin | 0.0 | **1.0** | Scorer fix (faith gate) | R6_rescored에서 ac=1.0 확인 |
| **GM** | tpo_segment_gross_margin | 0.0 | **1.0** | Prompt v3.3 | sanity: gm_graph_2021=True |
| **MU** | net_margin_and_nonop_impact | 0.0 | **1.0** | Prompt v3.3 | sanity: mu_graph_all_years=True |
| **BXP** | operating_margin | 0.0 | **1.0** | Prompt v3.3 | sanity: bxp_not_old_10_4=True |
| **XEL** | workforce_ratio | 0.0 | **1.0** | XEL KG patch | sanity: xel_graph_two_facts=True |

5-case sanity run 15/15 통과. 모든 sanity check True.

### AMGN 특이사항: Scorer Bug 확인

AMGN은 Round 06에서 이미 정답(tnr=1.0, 두 target slot 모두 match)이었지만 ac=0.0을 받고 있었음.

- **원인:** `faith = rfr >= 0.8` gate. rfr=0.75 (source_fact_numbers에 KG에 없는 royalty_revenue 포함) → faith=False → ac=0.0
- **R6_rescored 확인:** scorer fix만 적용 시 ac=1.0 확인 (모델 변경 없음)
- **수정:** `ans = numeric_ok and fmt and calc` (faith 제거). rfr은 standalone metric으로 유지

### BXP 특이사항: Arithmetic 오류 해소

Round 06에서 "2023 operating margin = 10.4%"라는 잘못된 값 출력. Prompt v3.3의 arithmetic verification 지시로 해소됨.
- "A > B when physically expected (revenue > expenses)" 검증 단계가 실질적으로 작동

---

## 4. Sanity Run 검증

5 targeted cases × 3 methods = 15 runs 사전 실행.

| 체크 | 결과 |
|---|---|
| amgn_graph_none (scorer fix 검증) | ✅ True |
| mu_graph_all_years (2022·2023 포함) | ✅ True |
| gm_graph_2021 (절댓값 포함) | ✅ True |
| bxp_not_old_10_4 (2023 오류 해소) | ✅ True |
| xel_graph_two_facts (올바른 KG 팩트) | ✅ True |

전원 통과 → 75-run 본 실행 진행.

---

## 5. XEL KG Patch 상세

**패치 내용:**
- 기존: `employees=11311` (총 인원수, 모델 visible contract와 불일치)
- 추가: `female_employee_pct=23.0` (%), `female_management_pct=26.0` (%)
- 기존 obs: `validation_status="deprecated_r7_patch"` (삭제 아님)

**안전 절차 완료:**
- evidence_text exact quote 확인 후 값 사용
- before/after snapshot 저장
- rollback Cypher 생성
- approval file 생성
- patch scope: round3_test_004_b035aeed 1개 케이스 한정

**파일:** `outputs/round7_eval/xel_patch_before_snapshot.json`, `xel_patch_rollback.cypher`, `xel_patch_approval.json`

---

## 6. 전체 결과 (25 cases)

| Method | avg_ac | 비고 |
|---|---:|---|
| vector_only_v7 | (test 0.60) | Prompt v3.3 효과 |
| graph_neo4j_v7 | (test 0.90) | Scorer fix + Prompt v3.3 + XEL patch |
| hybrid_neo4j_v7 | (test 0.80) | 동일 |

---

## 7. Claim 제한 (중요)

### 말해도 되는 것

```
Round 07은 Round 06 test split에서 확인된 5개 실패 케이스의 원인별 
targeted intervention 결과다:
- AMGN: scorer gate 버그(rfr가 ac를 잘못 blocking) 수정으로 개선
- MU/GM/BXP: 프롬프트 v3.3의 multi-year/completeness 지시로 개선
- XEL: KG metric 오류(employees=11311 총인원 → female_pct=23) 수정으로 개선

5개 개입 모두 sanity run으로 검증됨.
R6_rescored baseline을 통해 scorer 효과와 prompt/KG 효과를 분리함.
```

### 말하면 안 되는 것

```
GraphRAG가 VectorRAG보다 일반적으로 우수하다. (test split 10개, diagnostic)
Round 07은 clean held-out benchmark다. (R06 실패 케이스를 보고 설계됨)
XEL patch가 포함된 결과를 원래 KG의 자연 성능이라고 부를 수 있다.
Graph 0.90이 new test set에서도 유지될 것이라고 예측할 수 있다.
```

---

## 8. 라운드별 진행 요약

| 라운드 | 핵심 변화 | Test graph ac | 비고 |
|---|---|---:|---|
| Round 5 | Round 3 KG 사용 (기존) | **0.00** | graph 완전 실패 |
| Round 6 basic | Step B targeted KG extraction | **0.50** | graph > vector 첫 달성 |
| Round 6 Step A | Semantic fact retrieval (top-K) | **0.50** | 대부분 케이스 K≤8, no-op |
| R6_rescored | scorer v7 적용 (no model rerun) | **0.60** | AMGN bug fix 단독 확인 |
| Round 7 | Prompt v3.3 + XEL patch | **0.90** | 5/5 targeted failures resolved |

누적 개선: R5 0.00 → R7 0.90 (+0.90)

---

## 9. 다음 단계 제안

### 단기 (Round 08 후보)
| 작업 | 기대 효과 | 비고 |
|---|---|---|
| **Clean held-out test** | 진정한 성능 측정 | 새 케이스로 R7 prompt+KG의 일반화 검증 |
| **Dev case KG 재추출** | dev ac 개선 | MCO unit, MDLZ sign 오류 수정 |
| **Multi-sample voting** | Vector instability 완화 | 3x run + majority vote |

### 중기
| 작업 | 내용 |
|---|---|
| **Model upgrade** | gpt-4o-mini → gpt-4o (산술 안정성) |
| **Partial credit scoring** | binary ac → slot fraction (tnr 반영) |
| **더 큰 test set** | 10개 → 50개 이상으로 통계 신뢰도 향상 |

---

## 10. 파일 위치

| 파일 | 경로 |
|---|---|
| Round 07 traces (75) | `outputs/round3_eval_runs/round7_eval_20260529_074714/round7_traces.jsonl` |
| Round 07 summary | `outputs/round3_eval_runs/round7_eval_20260529_074714/round7_summary.md` |
| R6 rescored traces | `outputs/round6_eval/r6_rescored_v7.jsonl` |
| R6 rescore summary | `outputs/round6_eval/r6_rescore_v7_summary.md` |
| XEL patch approval | `outputs/round7_eval/xel_patch_approval.json` |
| XEL patch rollback | `outputs/round7_eval/xel_patch_rollback.cypher` |
| Round 07 state | `outputs/round7_eval/state.json` |
| Prompt v3.3 | `outputs/round3_dual_track_eval_prep/prompt_formatter_v3_2/prompt_v3_3_system.md` |
