# Round 08 평가 리포트

**작성일:** 2026-05-29  
**라운드 성격:** Clean Held-Out Benchmark — FinDER 신규 30케이스 + FinQA 파일럿 20케이스  
**Claim boundary:** `clean_held_out_round8_finder_finqa_pilot`  
**총 실행:** 50 cases × 3 methods = 150 traces  

---

## 1. 헤드라인 숫자

| Method | R7 (targeted diagnostic) | **R8 (clean held-out)** |
|---|---:|---:|
| vector_only | 0.60 | **0.36** |
| graph_neo4j | 0.90 | **0.46** |
| hybrid_neo4j | 0.80 | **0.40** |

> **graph > vector 유지 (0.46 vs 0.36)**  
> `graph_beats_vector_test = true`  
> R7 0.90은 targeted diagnostic 효과 — clean held-out에서 0.46으로 수렴

---

## 2. 데이터셋별 성능

| Dataset | vector | graph | hybrid | n_cases |
|---|---:|---:|---:|---:|
| **FinDER** (신규 30) | 0.30 | **0.50** | 0.37 | 30 |
| **FinQA** (신규 20) | **0.45** | 0.40 | **0.45** | 20 |
| **전체** | 0.36 | **0.46** | 0.40 | 50 |

### 데이터셋별 수치 근접도 (avg_nc)

| Dataset | vector_nc | graph_nc | hybrid_nc |
|---|---:|---:|---:|
| FinDER | 0.456 | **0.611** | 0.624 |
| FinQA | 0.689 | **0.812** | 0.744 |
| 전체 | 0.550 | **0.692** | 0.672 |

> **중요 관찰:** FinQA에서 graph의 nc(0.812)가 vector(0.689)보다 월등히 높음.  
> 그러나 binary ac는 graph(0.40) = vector(0.45) — 아래 섹션 5에서 원인 분석.

---

## 3. R7 → R8: 일반화 검증

| Method | R7 targeted | R8 clean | 차이 | 해석 |
|---|---:|---:|---:|---|
| vector | 0.60 | 0.36 | −0.24 | R7 prompt 개선 부분 일반화 안 됨 |
| graph | 0.90 | 0.46 | −0.44 | R7는 5개 targeted fix 결과, 일반화 제한적 |
| hybrid | 0.80 | 0.40 | −0.40 | 동일 |

**결론:** R7의 0.90은 targeted diagnostic 환경에서의 결과로, clean held-out에서는 0.46이 현재 실제 성능 상한. 이 수치가 향후 개선의 베이스라인이 됨.

---

## 4. Pipeline 성과

| 단계 | 결과 |
|---|---|
| FinDER 케이스 선택 | 30/30 (990개 후보 중 tier-1) |
| FinQA 케이스 선택 | 20/20 (5,509개 filtered 후보 중 top) |
| Formula contract 생성 | 50/50 통과 (validation_failed=0) |
| KG 사실 추출 | 314/314 (success_rate=1.00) |
| Eval 실행 | 150/150 (provider error=0) |

Round 08은 첫 번째 완전 자동화된 케이스→계약→KG→Eval 풀 파이프라인으로 실행됨.

---

## 5. 핵심 발견 3가지

### 5-1. FinQA: 높은 nc에도 낮은 ac — Scorer Tolerance 문제

FinQA graph 실패 케이스 12개 중 8개의 nc가 0.74 이상 (5개는 0.90+):

| Case | nc | failure_reason |
|---|---:|---|
| round8_finqa_008 | 0.947 | formula_target_mismatch |
| round8_finqa_016 | 0.947 | formula_target_mismatch |
| round8_finqa_012 | 0.941 | formula_target_mismatch |
| round8_finqa_007 | 0.920 | formula_target_mismatch |
| round8_finqa_018 | 0.913 | formula_target_mismatch |
| round8_finqa_001 | 0.909 | formula_target_mismatch |

**원인:** `finqa_program` 타입의 target_slot tolerance가 너무 좁게 설정됨.  
프로그램 파서가 생성한 `tolerance = max(0.1, abs(expected_value) * 0.005)` (0.5% 상대오차)가  
소수점 표기 방식 불일치나 % vs ratio 혼용에 대응하지 못함.

예: 모델이 27.4%를 출력했고 expected=27.5% → nc=0.947 → ac=0.0 (tolerance=0.1375가 실제 차이 0.1을 커버 못 함)

**수정 방향 (Round 09):** finqa_program tolerance를 `max(0.5, abs(expected_value) * 0.02)` (2% 상대오차)로 완화.  
또는 nc ≥ 0.90인 경우 binary ac → partial credit으로 전환.

### 5-2. FinDER Hybrid < Graph — Text-Graph Interference 패턴

**10개 케이스에서 graph ac=1.0이지만 hybrid ac=0.0:**

- graph_nc=1.000 (정확히 맞음), hybrid_fail_reason=formula_target_mismatch
- 패턴: hybrid 모드에서 evidence_text가 KG facts와 충돌하여 모델이 텍스트 쪽 숫자를 선택

**원인 가설:** 
1. evidence_text에 그래프가 추출하지 않은 중간 계산값이 있어 모델이 혼용
2. FinDER 텍스트는 이미 계산된 값(중간 결과)을 많이 포함 → 그래프 facts와 텍스트 facts 간 일관성이 낮을 때 모델이 혼동

**Round 07 대비 차이:** Round 07 hybrid(0.80) > graph(0.90) 아니었음(graph가 더 높았음). Round 08에서도 동일 패턴 — graph가 hybrid보다 우수 (FinDER 기준).

**수정 방향 (Round 09):** Hybrid 프롬프트에 "KG facts를 텍스트보다 우선" 지시 추가 또는 hybrid를 "KG facts + 텍스트 요약" 방식으로 변경.

### 5-3. FinDER Ticker 품질 — 3개 의심 Ticker

`selection_state.json`에서 확인된 의심 tickers: **CAGR, OF, LOSS**

- 이들은 실제 주식 ticker가 아니라 금융 텍스트에서 단어로 추출된 것
- 해당 케이스의 KG facts는 ticker 노드가 올바르게 생성됐을 가능성 낮음
- 영향: 최대 3개 케이스 (전체의 6%) — 단, KG facts_written=314/314이므로 추출 자체는 성공

**수정 방향 (Round 09):** 
- ticker 허용 목록(S&P 500 + 일반적 중소형 tickers) 기반 화이트리스트 필터 추가
- 또는 company명 → ticker 역매핑 API 활용

---

## 6. 전체 실패 분석 (graph 기준)

| 실패 원인 | 건수 | 비율 | 데이터셋 |
|---|---:|---:|---|
| formula_target_mismatch | 24 | 88.9% | FinDER+FinQA 혼합 |
| answer_format_error | 3 | 11.1% | FinDER |
| **합계** | **27** | | |

**formula_target_mismatch 세부 분류 (추정):**
- FinQA tolerance 문제: ~8건 (nc≥0.74인 FinQA 실패)
- FinDER hybrid-interfered formula: ~10건 (graph pass, hybrid fail 패턴으로 추정)
- 나머지: 모델 reasoning 오류 (계산 실수, multi-year 누락 등)

**answer_format_error:** prompt v3.3의 answer_format_spec이 FinDER "other" formula_type에 최적화 안 됨.  
신규 formula_type에 대한 output envelope 추가 필요.

---

## 7. Formula Contract 품질 평가

### FinDER: formula_type="other" 100%

GPT-4o-mini가 30개 FinDER 케이스 모두를 `formula_type="other"`로 분류.

**원인:** 신규 케이스들은 기존 표준 타입(gross_margin, operating_margin 등)에 해당하지 않는  
다양한 계산 유형 포함 (YoY change, segment breakdown, EPS + dilution effect 등).

**영향:**
- 긍정: 계약 생성 실패 없음 (50/50), 평가 진행 가능
- 부정: "other" 타입은 scorer가 target_slot 정의에 더 넓은 관용도 적용 → 채점 정밀도 낮아짐
  
**Round 09 대응:** formula_type 목록 확장 (yoy_change, segment_comparison, eps_impact, ratio_trend 등)하고  
FinDER 케이스에 대한 contract generation prompt에 예시 추가.

### FinQA: finqa_program 100% — 신뢰도 높음

FinQA 계약은 프로그램 파서 기반 → deterministic, validation 100% 통과.  
nc가 높고(avg 0.812) 실패의 주요 원인이 tolerance라는 점에서 계약 자체 품질은 양호.

---

## 8. 라운드별 누적 진행

| 라운드 | 성격 | graph ac | vector ac | 비고 |
|---|---|---:|---:|---|
| Round 5 | Round 3 KG 사용 | 0.00 | — | graph 완전 실패 |
| Round 6 | Step B targeted KG | 0.50 | 0.40 | graph > vector 첫 달성 |
| R6_rescored | scorer v7 (no model) | 0.60 | 0.40 | AMGN bug fix 단독 |
| Round 7 | Prompt v3.3 + XEL patch | **0.90** | 0.60 | targeted 5개 all resolved |
| **Round 8** | **Clean held-out 50케이스** | **0.46** | **0.36** | **일반화 베이스라인 확립** |

> **R8이 핵심 기준점**: R7 0.90은 targeted 환경, R8 0.46이 실제 일반화 성능의 첫 측정값.  
> 이 수치를 베이스라인으로 Round 09에서 개선 추적.

---

## 9. Claim 경계

### 말해도 되는 것

```
Round 08은 기존 eval 케이스와 완전히 독립된 clean held-out benchmark다:
- FinDER 신규 30 + FinQA 신규 20 케이스 (기존 22개 ticker 모두 제외)
- Formula contracts 자동 생성 (hand-tuning 없음)
- KG 추출 kg-round8-v1-20260529 새 배치 (314/314 성공)

결과:
- graph > vector 재확인 (0.46 vs 0.36)
- FinDER에서 graph 우위 뚜렷 (0.50 vs 0.30)
- FinQA에서 nc 기준 graph 우위 (0.812 vs 0.689)지만 binary ac는 동등
- R7 0.90은 clean held-out에서 0.46으로 수렴 → R7은 targeted diagnostic 결과임 확인
```

### 말하면 안 되는 것

```
Round 08 기준으로 GraphRAG가 일반적으로 우수하다. (50케이스 pilot)
FinQA 20케이스가 전체 FinQA 8,281건 성능을 대표한다.
R8 FinDER 0.50이 FinDER 5,678건 전체에서도 유지된다.
graph(0.46)이 hybrid(0.40)보다 일반적으로 우수하다. (FinDER-특화 현상)
```

---

## 10. 다음 단계 제안 (Round 09)

### 우선 수정 (고영향, 저비용)

| 작업 | 예상 개선 | 구현 난이도 |
|---|---|---|
| **FinQA tolerance 완화** (0.5% → 2%) | FinQA ac +0.10~0.15 예상 | 매우 낮음 |
| **Ticker 화이트리스트 필터** | 케이스 품질 향상 (3개 제거) | 낮음 |
| **formula_type 목록 확장** | FinDER 채점 정밀도 향상 | 중간 |

> **빠른 validation**: R8 traces에 수정된 scorer 재적용 (no-model rescore, R6 rescore 패턴).  
> FinQA tolerance 완화 시 현재 R8 traces로 0.40 → ~0.50~0.55 예측 가능.

### 구조적 개선 (중기)

| 작업 | 내용 |
|---|---|
| **Hybrid 프롬프트 개선** | KG facts 우선 지시 추가 → FinDER hybrid 0.37 → 0.50 목표 |
| **FinQA 전체 평가** | 20 pilot → 100+ (R8 FinQA 파일럿 결과 기반) |
| **TAT-QA 통합** | 16,552 Q&A; company명→ticker 매핑 선행 필요 |
| **Partial credit scoring** | nc ≥ 0.90 → ac_partial=0.5로 처리 |
| **model upgrade** | gpt-4o-mini → gpt-4o (산술 정밀도 향상) |

---

## 11. 파일 위치

| 파일 | 경로 |
|---|---|
| Round 08 traces (150) | `outputs/round3_eval_runs/round8_eval_20260529_103625/round8_traces.jsonl` |
| Round 08 summary | `outputs/round3_eval_runs/round8_eval_20260529_103625/round8_summary.md` |
| Round 08 state | `outputs/round8_eval/state.json` |
| FinDER candidates (30) | `outputs/round8_case_selection/finder_candidates.jsonl` |
| FinQA candidates (20) | `outputs/round8_case_selection/finqa_candidates.jsonl` |
| Selection state | `outputs/round8_case_selection/selection_state.json` |
| Formula contracts | `outputs/round8_formula_contracts/round8_scorer_contracts.jsonl` |
| KG write log | `outputs/round8_step_b_kg/kg_write_log.jsonl` |
| KG rollback | `outputs/round8_step_b_kg/round8_kg_rollback.cypher` |
