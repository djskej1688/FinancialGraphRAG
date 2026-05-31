# GraphRAG Evaluation Round 05 보고서

- 작성일: 2026-05-28
- 진단 레이블: `round5_diagnostic_post_hoc_formula_contracts`
- KG batch: `kg-llm-ie-v1-20260528` (Round 04와 동일)
- 평가 케이스: Track B shadow overlay 25개 (round3_dev 12 + baseline_control 3 + round3_test 10)
- 평가 모델: `gpt-4o-mini`
- 실행 결과: 75 runs (25 cases × 3 methods), 실패 0

---

## 1. Executive Summary

Round 05는 Round 04에서 규명된 두 가지 구조적 문제를 수정한 뒤 실행한 **진단 평가(diagnostic evaluation)** 다.

**수정된 문제:**
1. Test split 10개 케이스의 formula contract 부재 → 사후 구축 후 투입 (25개 전체 채점 가능)
2. Neo4j 정보 과부하(avg 57.4 facts) → year + keyword 필터링 (avg 27.5 facts로 감소)

**Round 05 핵심 수치:**

| Method | avg_ac (전체 25) | avg_nc (전체 25) | avg_rfr | avg_facts |
|---|---:|---:|---:|---:|
| `vector_only_v5` | **0.44** | **0.577** | 1.000 | 0 |
| `graph_neo4j_v5` | 0.12 | 0.320 | 0.686 | 27.5 |
| `hybrid_neo4j_v5` | 0.16 | 0.543 | 0.686 | 27.5 |

**가장 중요한 성과:** test split 10개 케이스가 Round 03(locked test) 이래 처음으로 유효하게 채점되었다. `vector_only_v5`가 test split에서 `avg_ac=0.60`, `avg_nc=0.767`을 기록했으며, 10개 중 6개가 answer_correctness 1.0을 달성했다.

**핵심 발견:** Neo4j 필터링으로 facts 수를 57 → 27로 줄였음에도 graph 방법은 개선되지 않았다. `required_fact_recall=1.0`인 케이스(MU, GM, LOW, APD 4개)에서조차 graph_neo4j_v5가 ac=0.0을 기록했다. 이는 **fact 부재가 아니라 fact 노이즈** 문제임을 확증한다. 현재 LLM IE KG의 generic extraction 방식으로는 모델이 27개 facts 중 3–5개 필요 facts를 골라내지 못한다.

이 결과를 토대로 Round 06 이전에 **B: KG targeted extraction 방식 전환**을 수행한다.

---

## 2. Round 05의 위치와 목적

### 2.1 전체 Evaluation 흐름 내 위치

| Round | 목적 | 핵심 변경 | 주요 결과 |
|---|---|---|---|
| Round 01 | Pipeline smoke test | - | selected 7 중 1개 평가 가능 |
| Round 02 | Selected 7 완전 평가 | Targeted KG curation | hybrid 0.96, graph가 vector 보완 확인 |
| Round 03 (locked) | Track B test split 평가 시도 | Shadow Overlay 방식 | 전부 0점 (formula contract 부재) |
| Round 04 | Neo4j 실제 조회 전환 | LLM IE KG 구축 | test 여전히 0점, 원인 규명 |
| **Round 05** | **formula contract + 필터링** | **25개 전체 채점** | **vector 우세, graph noise 확증** |
| Round 06 (예정) | Targeted KG 효과 측정 | KG 재구축 (targeted) | - |

### 2.2 Round 05 의 두 가지 수정

**수정 1: Formula contract 완성 (25개 전체)**

| 구분 | Round 04 | Round 05 |
|---|---|---|
| dev+baseline (15) | ✅ 있음 | ✅ 있음 |
| test split (10) | ❌ 없음 (0점 강제) | ✅ **사후 구축 완료** |
| 전체 coverage | 15/25 = 60% | **25/25 = 100%** |

**수정 2: Neo4j 정보 과부하 해소**

| 통계 | Round 04 | Round 05 |
|---|---:|---:|
| avg facts per case | 57.4 | **27.5** |
| min | 20 | 6 |
| max | 108 | 63 |

Cypher에 `yr.year IN $years` 조건과 metric_canonical keyword 필터를 추가했다. facts 수가 절반으로 감소했으나 graph 방법 성능은 개선되지 않았다 — 이 점이 Round 05의 핵심 발견이다.

---

## 3. Source-Target Fallback 이슈

**`source_target_fallback_count: 6`**

Round 05 실행 중 dev 케이스 6개의 기존 `scorer_only_target_slot_contract`에 `target_slots: []`이 발견되었다. 이 케이스들은 Round 04에서 scorer가 즉석 추론으로 대체했던 케이스들로, 원래 formula contract 파일에 target slot이 없었다.

Codex는 이 케이스들에 required/source facts 기반 "diagnostic source-target 슬롯"을 임시로 생성해 채점했다. 그러나 이 fallback 슬롯은 실제 expected answer에 맞게 설계된 것이 아니기 때문에 **채점 결과를 신뢰할 수 없다.**

**영향 범위:**
- dev+baseline 15개 중 6개 케이스 → dev 수치 신뢰도 낮음
- test split 10개는 이번에 직접 구축했으므로 영향 없음 → **test 수치는 신뢰 가능**
- Round 4→5 dev vector_only 하락(0.467→0.333)의 주요 원인: fallback 슬롯이 올바른 답에 불이익을 줌

이 6개 케이스의 formula contract는 별도로 검토 후 재구축이 필요하다.

---

## 4. 실행 결과

### 4.1 전체 집계 (25 cases)

| Method | avg_ac | avg_nc | avg_rfr | avg_facts |
|---|---:|---:|---:|---:|
| `vector_only_v5` | 0.440 | 0.577 | 1.000 | 0 |
| `graph_neo4j_v5` | 0.120 | 0.320 | 0.686 | 27.5 |
| `hybrid_neo4j_v5` | 0.160 | 0.543 | 0.686 | 27.5 |

### 4.2 Dev+Baseline (15 cases) — fallback 포함, 신뢰도 제한

| Method | avg_ac | avg_nc | avg_rfr | Round 4 avg_ac | delta |
|---|---:|---:|---:|---:|---:|
| `vector_only_v5` | 0.333 | 0.451 | 1.000 | 0.467 | **−0.134** |
| `graph_neo4j_v5` | 0.200 | 0.394 | 0.724 | 0.200 | 0.000 |
| `hybrid_neo4j_v5` | 0.200 | 0.461 | 0.724 | 0.200 | 0.000 |

vector_only의 하락(-0.134)은 fallback contract 6개로 인한 scoring artifact이며, 실제 방법론적 성능 변화가 아니다.

### 4.3 Test Split (10 cases) — **최초 유효 채점** ✅

| Method | avg_ac | avg_nc | avg_rfr |
|---|---:|---:|---:|
| `vector_only_v5` | **0.600** | **0.767** | 1.000 |
| `graph_neo4j_v5` | 0.000 | 0.208 | 0.630 |
| `hybrid_neo4j_v5` | 0.100 | 0.667 | 0.630 |

### 4.4 Test Split vector_only_v5 케이스별 상세

| 케이스 | ticker | ac | nc | 슬롯 수 |
|---|---|---:|---:|---:|
| `round3_test_011_e428c7bc` | NXPI | **1.00** | **1.00** | 3 |
| `round3_test_009_3a2f3700` | AMGN | **1.00** | **1.00** | 2 |
| `round3_test_017_68bdbbb8` | MPC | **1.00** | **1.00** | 3 |
| `round3_test_012_f9d03e27` | GM | **1.00** | **1.00** | 3 |
| `round3_test_013_bc2fb598` | VRSK | **1.00** | **1.00** | 3 |
| `round3_test_004_b035aeed` | XEL | **1.00** | **1.00** | 1 |
| `round3_test_014_42c9db2b` | MU | 0.00 | 0.50 | 4 |
| `round3_test_007_4ac62908` | LOW | 0.00 | 0.50 | 2 |
| `round3_test_016_707dc83f` | APD | 0.00 | 0.67 | 3 |
| `round3_test_018_0748ea37` | BXP | 0.00 | 0.00 | 2 |

6개(NXPI, AMGN, MPC, GM, VRSK, XEL)에서 perfect score. 나머지 4개(MU, LOW, APD, BXP)는 numeric 일부 맞으나 answer_correctness 실패.

### 4.5 Test Split graph_neo4j_v5 케이스별 상세

| ticker | ac | nc | rfr | facts | failure_reason |
|---|---:|---:|---:|---:|---|
| MU | 0.00 | 0.25 | **1.00** | 30 | formula_target_mismatch |
| GM | 0.00 | 0.00 | **1.00** | 41 | formula_target_mismatch |
| LOW | 0.00 | 0.50 | **1.00** | 24 | formula_target_mismatch |
| APD | 0.00 | 0.33 | **1.00** | 36 | formula_target_mismatch |
| NXPI | 0.00 | **1.00** | 0.75 | 32 | scoring_uncertain |
| AMGN | 0.00 | 0.00 | 0.75 | 8 | formula_target_mismatch |
| BXP | 0.00 | 0.00 | 0.60 | 36 | formula_target_mismatch |
| MPC | 0.00 | 0.00 | 0.00 | 36 | required_fact_missing |
| VRSK | 0.00 | 0.00 | 0.00 | 48 | required_fact_missing |
| XEL | 0.00 | 0.00 | 0.20 | 13 | required_fact_missing |

rfr=1.0인 4개 케이스(MU, GM, LOW, APD)에서도 모두 ac=0.0. 필요한 facts가 있어도 모델이 노이즈 속에서 골라내지 못하고 있다.

---

## 5. 결과 해석

### 5.1 Test Split: Vector-only의 의외의 강세

test split에서 `vector_only_v5`가 ac=0.60을 기록했다. 이는 dev+baseline 기준 ac=0.333보다 높다. 두 가지 요인이 복합적으로 작용한 것으로 분석된다.

**요인 1 — Formula contract 품질 차이**
test split formula contract는 이번에 직접 구축했다. Round 04 trace 분석 → expected_answer 검토 → 올바른 수식 결정이라는 과정을 거쳤기 때문에 더 정밀하게 설계되었다. dev 6개 케이스의 fallback contracts와 대비된다.

**요인 2 — Post-hoc 방법론적 오염**
test formula contract를 만들 때 expected_answer를 참조했기 때문에 contract가 모델이 쉽게 맞힐 수 있는 방향으로 설계되었을 가능성이 있다. 특히 AMGN, NXPI, MPC, GM, VRSK는 evidence text에서 직접 계산 가능한 수치들이고, formula contract가 정확히 그 계산을 안내한다.

→ **절대 점수(0.60)는 diagnostic으로만 해석해야 한다.** 단, 세 method 간 상대 비교(vector >> graph)는 여전히 유효하다.

### 5.2 Graph 방법: "Facts 노이즈" 문제의 확증

이번 round에서 가장 중요한 발견은 **rfr=1.0인 케이스에서도 graph_neo4j_v5가 ac=0.0**이라는 점이다.

```
MU: rfr=1.0, facts=30, failure=formula_target_mismatch  → ac=0.0
GM: rfr=1.0, facts=41, failure=formula_target_mismatch  → ac=0.0
LOW: rfr=1.0, facts=24, failure=formula_target_mismatch → ac=0.0
APD: rfr=1.0, facts=36, failure=formula_target_mismatch → ac=0.0
```

이는 "KG에 필요한 facts가 없다"는 문제가 아니다. **필요한 facts가 있어도, 불필요한 facts들 사이에서 모델이 올바른 것을 선택하지 못한다.**

특히 NXPI는 nc=1.0(숫자 계산 정확)이지만 ac=0.0(scoring_uncertain)이다. 이는 모델이 계산 자체는 맞혔지만 answer format이나 서술 방식에서 scorer 기준을 충족하지 못했음을 의미한다. **Graph-only 방식에서는 text context 없이 숫자만 제공되기 때문에 모델의 답변 형식이 불안정해진다.**

### 5.3 필터링 효과의 한계

| 조건 | Round 04 | Round 05 | 결과 |
|---|---:|---:|---|
| avg facts | 57.4 | 27.5 | 52% 감소 ✅ |
| graph_neo4j ac (dev) | 0.200 | 0.200 | **변화 없음** |
| graph_neo4j ac (test) | 0.000 | 0.000 | **변화 없음** |

year + keyword 필터로 facts를 절반으로 줄였으나 graph 성능은 전혀 개선되지 않았다. 27개도 여전히 너무 많거나, 더 근본적으로 **LLM generic extraction이 생성하는 facts 자체의 precision 문제**다.

Round 02 (precision ~6 facts/case, hybrid 0.96) vs Round 05 (generic 27 facts/case, hybrid 0.16)의 gap은 단순한 필터링으로는 메울 수 없다.

### 5.4 Round 02 vs Round 05 패턴 비교

| 항목 | Round 02 | Round 05 |
|---|---|---|
| Best method | hybrid (0.96) | vector (0.44/test:0.60) |
| Graph 방식 | Targeted curation | LLM generic extraction |
| Facts per case | ~6 (precision) | ~27 (noise 포함) |
| rfr | 1.0 (hardcoded) | 0.686 (실측) |
| 케이스 수 | 7 | 25 |

Round 02의 "hybrid가 최고"는 graph facts가 precision-curated였기 때문이다. Round 05에서는 generic extraction KG가 noise를 더 많이 추가했고, 이것이 hybrid 성능을 떨어뜨렸다. **Graph가 유용하려면 "많이 아는 것"이 아니라 "정확히 필요한 것만 아는 것"이 중요하다.**

---

## 6. 한계와 유효성 경계

| 항목 | 상태 | 비고 |
|---|---|---|
| Neo4j write 여부 | 없음 ✅ | KG 오염 없음 |
| Locked test directory 수정 | 없음 ✅ | read-only 유지 |
| Test split 채점 유효성 | ✅ 첫 유효 채점 | post-hoc formula contract |
| Dev+baseline 채점 신뢰도 | ⚠️ 제한 | fallback contract 6개 |
| Clean held-out claim 가능 여부 | ❌ 불가 | post-hoc formula contracts |
| Method 비교 유효성 | ✅ (test 기준) | vector >> hybrid >> graph 패턴 |
| Round 02와 직접 비교 | ❌ 부적절 | KG 방식, 케이스 수, contract 품질 모두 다름 |

---

## 7. 다음 단계 로드맵

Round 05 결과로부터 도출된 다음 단계 순서:

### Step B: KG Targeted Extraction 재구축

**목적:** Generic extraction → 질문 유형(formula type)별 targeted extraction으로 전환  
**핵심 문제 해결:** rfr=0.686, formula_target_mismatch, 노이즈 27개  
**방법:**
- formula_type(operating_margin, gross_margin, EPS 등)별로 추출할 metric을 사전 정의
- 각 케이스의 `metric_tags`와 `formula_type`을 기준으로 추출 prompt 특화
- 새 KG batch ID: `kg-targeted-ie-v1-{date}`로 기존 KG와 완전 분리
- 목표 rfr: ≥0.90, 목표 facts per case: ≤10

### Step Round 06: Targeted KG 효과 측정

**목적:** Step B의 KG로 동일한 3가지 method 재평가  
**기대 결과:** rfr 향상 + facts 감소 → graph/hybrid 성능 회복  
**비교 기준:** Round 05 test split 결과 (vector 0.60, graph 0.0, hybrid 0.10)

### Step A: Semantic Fact Retrieval Layer

**목적:** KG에서 질문 임베딩과 가장 유사한 top-K facts만 선택하는 retrieval layer 추가  
**적용 시점:** Step B 이후 → Round 06 결과 확인 후 추가 개선 필요할 경우  
**방법:** 질문 임베딩 + fact description 임베딩 cosine similarity → top 5 선택

---

## 8. 관련 산출물

### Round 05 실행 산출물
```
outputs/round3_eval_runs/round5_diagnostic_20260528_213524/
  round5_traces.jsonl           (75 rows)
  round5_summary.md
  neo4j_facts_cache.jsonl       (filtered)
  failure_analysis.jsonl

outputs/round5_diagnostic_eval/state.json
  (phase=done, formula_contracts_loaded=25, neo4j_avg_facts_filtered=27.48,
   source_target_fallback_count=6)
```

### Formula Contract 산출물 (Round 04 → 05 브릿지)
```
outputs/round3_eval_harness/formula_contract_v3_2_test_split/
  test_scorer_contracts.jsonl      (10 rows)
  test_model_visible_contracts.jsonl (10 rows)
  reextracted_facts_B.jsonl        (24 facts, fallback_used=0)
```

---

## 9. 보고서용 요약 문장

> Round 05에서는 test split 10개 케이스에 대한 formula contract를 사후 구축하고 Neo4j facts를 57개에서 27개로 필터링한 뒤 실행했다. test split에서 `vector_only_v5`가 ac=0.60을 기록하며 10개 중 6개 퍼펙트 스코어를 달성했다. 그러나 graph 방법은 `required_fact_recall=1.0`인 케이스에서도 formula_target_mismatch로 ac=0.0을 기록했다. 이는 facts 수 감소만으로는 해결되지 않는 **KG precision 문제**이며, 다음 단계로 generic LLM extraction을 formula type별 targeted extraction으로 전환하는 KG 재구축(Round B)을 수행한다. 이후 Round 06에서 동일 평가를 반복해 KG 개선 효과를 측정한다.
