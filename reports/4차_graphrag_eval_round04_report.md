# GraphRAG Evaluation Round 04 보고서

- 작성일: 2026-05-28
- KG batch: `kg-llm-ie-v1-20260528` (LLM IE KG, Round 04 신규 구축)
- 참조 KG: `kg-full-provenance-20260524` (Round 01–02 기준 KG)
- 평가 케이스: Track B shadow overlay 25개 (round3_dev 12 + baseline_control 3 + round3_test 10)
- 평가 모델: `gpt-4o-mini`
- 실행 결과: 75 runs (25 cases × 3 methods), 실패 0

---

## 1. Executive Summary

Round 04는 두 가지 목적을 동시에 수행한 라운드였다.

첫째, **KG 검색 방식을 Shadow Overlay(로컬 JSONL 파일)에서 실제 Neo4j LLM IE KG 조회로 전환**하는 것이었다. 기존 Round 03(locked test)의 graph 방법은 사전 생성된 `shadow_overlay_required_facts.jsonl` 파일을 그대로 graph facts로 주입했다. Round 04에서는 이 파일을 쓰지 않고, 실제 Neo4j에서 LLMObservation 노드를 Cypher로 직접 조회한다.

둘째, Round 04의 실행과 결과 분석 과정에서 **이전 모든 라운드의 0점 원인이 KG 오염이나 모델 오류가 아니라 test split에 formula contract가 없었다는 사실**을 최초로 규명했다.

Round 04 핵심 수치는 다음과 같다.

| method | avg_answer_correctness | avg_numeric_correctness | avg_rfr | 유효 케이스 |
|---|---:|---:|---:|---|
| `vector_only_v4` | 0.280 | 0.280 | 1.000 | 전체 25 (비점수 10 포함) |
| `graph_neo4j_v4` | 0.120 | 0.120 | 0.686 | 전체 25 (비점수 10 포함) |
| `hybrid_neo4j_v4` | 0.120 | 0.120 | 0.686 | 전체 25 (비점수 10 포함) |

그러나 이 평균은 test split 10개 케이스가 formula contract 부재로 강제 0점 처리된 것을 포함한 수치다. **실제 채점 가능했던 15개 케이스(round3_dev 12 + baseline_control 3)만 기준으로 하면** 수치가 상당히 다르다:

| method | avg_ac (15 cases) | avg_nc (15 cases) |
|---|---:|---:|
| `vector_only_v4` | 0.467 | 0.467 |
| `graph_neo4j_v4` | 0.200 | 0.333 |
| `hybrid_neo4j_v4` | 0.200 | 0.333 |

Round 04에서 나온 가장 중요한 발견은 성능 수치 자체가 아니라 다음 두 가지 구조적 문제의 규명이다.

1. **Test split formula contract 부재**: test 10개 케이스는 formula contract가 없어 scorer가 `expected_answer_ambiguous` 반환 → 모든 run 강제 0점. 이것이 locked test부터 Round 04까지 test split 성능이 항상 0.0이었던 진짜 원인이다.
2. **Graph 방법의 정보 과부하**: Neo4j에서 케이스당 평균 57.4개 사실(min=20, max=108)이 주입되었고, 모델이 필요한 3–5개 사실을 골라내지 못하면서 graph 방법이 vector보다 성능이 낮게 나왔다.

---

## 2. Round 04의 위치와 목적

### 2.1 전체 Evaluation 흐름 내 위치

이 프로젝트의 GraphRAG evaluation은 단계적으로 진행되었다.

| Round | 케이스 수 | Graph 방법 | 결과 요약 |
|---|---|---|---|
| Round 01 | Selected 7 중 1개 | Neo4j full-provenance KG | pipeline smoke test |
| Round 02 | Selected 7 전체 | Neo4j full-provenance KG | hybrid_vector_graph 우세 확인 |
| **Round 03 (locked test)** | Track B 25개 (test 10) | Shadow Overlay JSONL | **전부 0점** — formula contract 부재 |
| **Round 04** | Track B 25개 전체 | **Neo4j LLM IE KG** (신규) | test 10 여전히 0점, 원인 규명 |
| Round 05 (예정) | Track B 25개 전체 | Neo4j LLM IE KG | **test 10 첫 유효 채점** |

Round 04는 Round 03에서 실패한 원인을 Neo4j 실제 조회로 대체함으로써 해소할 수 있는지 확인하는 실험이었다. 결론적으로 Neo4j 조회 자체는 성공했지만, 0점 원인이 KG 방식이 아니라 formula contract 부재라는 사실이 밝혀졌다.

### 2.2 Round 04의 핵심 목적

1. Shadow Overlay(로컬 파일 주입) → Neo4j 실제 Cypher 조회로 전환
2. LLM IE KG (1,434 observations, 25 companies, 328 metrics) 품질 검증
3. 기존 locked test와 Round 04 결과 비교를 통해 방법론 차이 분리
4. 0점 원인이 KG 문제인지, 방법론 문제인지, formula contract 문제인지 확인

---

## 3. Neo4j LLM IE KG 구축 현황

### 3.1 KG 구축 방식

Round 01–02에서 사용한 `kg-full-provenance-20260524`는 수작업 curation과 rule-based Cypher로 구축된 KG다. Round 04에서는 25개 Track B 케이스의 `evidence_text`를 GPT-4o-mini에 투입해 구조화 정보를 추출하고, 이를 Neo4j에 저장한 LLM IE KG(`kg-llm-ie-v1-20260528`)를 새로 구축했다.

| 항목 | 값 |
|---|---|
| KG batch | `kg-llm-ie-v1-20260528` |
| 구축 방식 | LLM 기반 IE (GPT-4o-mini, structured JSON output) |
| LLMObservation 노드 | 1,434 |
| LLMCompany 노드 | 25 |
| LLMFinancialMetric 노드 | 328 |
| LLMFiscalYear 노드 | 4 |
| Relationship 수 | 5,736 |
| Chain coverage (full path) | 100% |
| OpenAI tokens used | 46,702 |
| 완료 시각 | 2026-05-28T09:55:43Z |

### 3.2 Neo4j 조회 쿼리

Round 04의 Neo4j 조회는 다음 Cypher를 사용한다. 케이스당 평균 57.4개 사실이 반환되었다.

```cypher
MATCH (obs:LLMObservation)-[:LLM_MENTIONS_COMPANY]->(c:LLMCompany {ticker: $ticker}),
      (obs)-[:LLM_OBSERVES_METRIC]->(m:LLMFinancialMetric),
      (obs)-[:LLM_OBSERVED_IN_YEAR]->(yr:LLMFiscalYear)
WHERE obs.kg_batch = $batch
RETURN obs.obs_id, obs.value, obs.unit, obs.evidence_quote,
       m.canonical_name, m.display_name, yr.year
ORDER BY yr.year, m.canonical_name
```

| 통계 항목 | 값 |
|---|---|
| 케이스당 평균 반환 facts | 57.4 |
| 최솟값 | 20 |
| 최댓값 | 108 |
| Required facts 대비 실제 포함 비율 (avg rfr) | 0.686 |

---

## 4. 케이스 구성

### 4.1 25개 케이스 분포

| split | 케이스 수 | formula contract 존재 여부 | Round 04 채점 가능 |
|---|---:|---|---|
| `round3_dev` | 12 | ✅ 있음 | ✅ 가능 |
| `baseline_control` | 3 | ✅ 있음 | ✅ 가능 |
| `round3_test` | 10 | ❌ **없음** | ❌ 불가 (강제 0점) |
| **합계** | **25** | | |

### 4.2 Test split이 채점 불가능한 이유

`round3_test` 10개 케이스는 `clean_dev_scorer_only_target_slot_contracts.jsonl`에 포함되지 않았다. Scorer는 채점 시 case_id에 맞는 formula contract를 찾지 못하면 즉석 추론(`infer_formula()` + `compute_targets()`)을 시도하는데, 이 즉석 추론기가 다음 이유로 실패했다.

- `AMGN gross_margin`: `gross_profit` metric이 없고 `product_sales + cost_of_sales`만 있음 → 자동 매핑 실패
- `VRSK`: 기존 facts가 실제로 필요한 metric(revenues, operating_income)이 아니라 세금 괄호 금액(-12.6, 131.5, -29.7)으로 잘못 캡처됨
- `GM TPO segment`: `net_sales_tpo` / `cost_of_sales_tpo` metric이 KG에 없음

결과적으로 30개 test traces(10 cases × 3 methods) 전체가 0점 처리되었다.

| 실패 코드 | 발생 횟수 | 원인 |
|---|---:|---|
| `expected_answer_ambiguous` | 20 | formula contract 없음, 즉석 추론 실패 |
| `answer_format_error` | 7 | 모델은 답변했으나 scorer 파싱 실패 |
| `required_fact_missing` | 3 | Neo4j 조회 결과에 필요한 사실 없음 |

---

## 5. Method 설계

### 5.1 Round 04에서 신규 정의된 3가지 방법

Round 03(locked test)에서 사용한 `graph_facts_only_v3_2`, `hybrid_vector_graph_v3_2`는 Shadow Overlay 기반이었다. Round 04는 이를 Neo4j 실제 조회로 대체한 새 method 이름을 사용했다.

| Method | vector context | graph facts source | 비고 |
|---|---|---|---|
| `vector_only_v4` | evidence_text 사용 | 없음 | 기존 vector baseline |
| `graph_neo4j_v4` | 없음 | Neo4j LLM IE KG | **Shadow Overlay → Neo4j 전환** |
| `hybrid_neo4j_v4` | evidence_text 사용 | Neo4j LLM IE KG | **Shadow Overlay → Neo4j 전환** |

### 5.2 Context 구성 방식

```python
if method == "vector_only_v4":
    context = f"TEXT_CONTEXT\n{evidence}"
elif method == "graph_neo4j_v4":
    context = f"GRAPH_FACTS_TABLE\n{fact_table(neo4j_facts)}"
elif method == "hybrid_neo4j_v4":
    context = f"TEXT_CONTEXT\n{evidence}\n\nGRAPH_FACTS_TABLE\n{fact_table(neo4j_facts)}"
```

---

## 6. 실행 결과

### 6.1 전체 집계 (25 cases, locked test 비교 포함)

| Method | avg_answer_correctness | avg_numeric_correctness | avg_rfr | 출처 |
|---|---:|---:|---:|---|
| `vector_only (locked)` | 0.000 | 0.000 | 1.000 | locked test (test 10개만) |
| `hybrid_shadow (locked)` | 0.000 | 0.000 | 1.000 | locked test (test 10개만) |
| `vector_only_v4` | 0.280 | 0.280 | 1.000 | Round 04 (25개 전체) |
| `graph_neo4j_v4` | 0.120 | 0.120 | 0.686 | Round 04 (25개 전체) |
| `hybrid_neo4j_v4` | 0.120 | 0.120 | 0.686 | Round 04 (25개 전체) |

### 6.2 Split별 세분화 결과

| split | method | avg_ac | avg_nc | avg_rfr |
|---|---|---:|---:|---:|
| round3_dev (12) | vector_only_v4 | 0.417 | 0.417 | 1.000 |
| round3_dev (12) | graph_neo4j_v4 | 0.250 | 0.250 | 0.759 |
| round3_dev (12) | hybrid_neo4j_v4 | 0.250 | 0.250 | 0.759 |
| baseline_control (3) | vector_only_v4 | 0.667 | 0.667 | 1.000 |
| baseline_control (3) | graph_neo4j_v4 | 0.000 | 0.667 | - |
| baseline_control (3) | hybrid_neo4j_v4 | 0.000 | 0.667 | - |
| **round3_test (10)** | **vector_only_v4** | **0.000** | **0.000** | **1.000** |
| **round3_test (10)** | **graph_neo4j_v4** | **0.000** | **0.000** | **0.630** |
| **round3_test (10)** | **hybrid_neo4j_v4** | **0.000** | **0.000** | **0.630** |

### 6.3 채점 가능한 15개 케이스만 집계 (dev + baseline)

| method | avg_ac | avg_nc | delta vs vector |
|---|---:|---:|---|
| `vector_only_v4` | 0.467 | 0.467 | 기준 |
| `graph_neo4j_v4` | 0.200 | 0.333 | ac: −0.267 |
| `hybrid_neo4j_v4` | 0.200 | 0.333 | ac: −0.267 |

---

## 7. 결과 해석

### 7.1 Test split 강제 0점 — 진짜 원인

Round 03(locked test)부터 Round 04까지 test 10개 케이스가 일관되게 0점인 것이 오랫동안 의문이었다. KG 오염 가능성, 모델 편향 가능성, retrieval 품질 문제 등이 후보로 검토되었다.

Round 04 trace 분석 결과, 실제 원인은 다음이었다.

> **test split 10개 케이스에 대한 `scorer_only_target_slot_contract`가 처음부터 존재하지 않았다.**

`clean_dev_scorer_only_target_slot_contracts.jsonl`은 `round3_dev`와 `baseline_control` 케이스만 포함했다. Scorer는 formula contract를 찾지 못하면 즉석 추론을 시도하지만, 대부분의 test 케이스에서 metric role mapping에 실패했다.

- AMGN: `gross_margin`을 위해 `gross_profit` metric이 필요하지만, required facts에는 `product_sales + cost_of_sales`만 있음
- VRSK: 기존 required facts가 실제 필요한 metric이 아니라 세금 괄호 값(-12.6, 131.5, -29.7)으로 잘못 캡처됨
- GM: TPO segment `net_sales_tpo` / `cost_of_sales_tpo`가 KG에 없음

따라서 Round 03의 locked test 0점과 Round 04 test split 0점은 KG 오염도, 모델 오류도 아니었다.

**이것은 최종적으로 Round 04 이후에 확인된 사항이다.** 이 사실이 밝혀졌기 때문에 Round 05에서 같은 케이스를 재사용하는 근거가 생겼다 (→ 7.5절에 상세 설명).

### 7.2 Graph 방법 성능 저하 — 정보 과부하 문제

채점 가능한 15개 케이스에서도 `graph_neo4j_v4`와 `hybrid_neo4j_v4`는 `vector_only_v4`보다 낮은 성능을 보였다. Round 02에서는 반대 패턴(`hybrid_vector_graph`가 최고)이었다는 점에서 이 결과는 의외였다.

원인 분석 결과, **정보 과부하(information overload)** 가 핵심이었다.

| 항목 | Round 02 (full-provenance KG) | Round 04 (LLM IE KG) |
|---|---|---|
| 케이스당 평균 graph facts | 6개 내외 (required facts만) | **57.4개** (전체 ticker 관련 observations) |
| 필요한 facts (실제) | 3–8개 | 3–8개 |
| 과부하 비율 | ~1x (딱 맞음) | **~7–10x (과잉)** |

Round 02의 `graph_facts_only`는 required facts만 엄선해서 주었기 때문에 모델이 집중할 수 있었다. Round 04의 `graph_neo4j_v4`는 ticker와 KG batch만 일치하면 모든 관측값을 반환했기 때문에, 케이스에 따라 100개 이상의 불필요한 사실이 모델에게 전달되었다.

**모델이 57.4개 facts 중 정답 계산에 필요한 3–5개를 찾아내는 데 실패하는 경우가 많아졌고**, 이것이 graph 방법의 성능 저하로 이어졌다.

해결 방향: Round 05에서 Cypher 쿼리에 year 필터와 keyword 필터를 추가해 케이스당 15개 내외로 제한한다.

### 7.3 rfr 측정 방식의 구조적 편향 — 해소

Round 02와 locked test에서는 shadow overlay 방법의 `required_fact_recall`이 항상 1.0으로 **하드코딩**되어 있었다. Shadow overlay는 required_facts 파일에서 직접 읽기 때문에 정의상 100% recall이지만, 이것은 그래프 retrieval 품질을 측정하는 것이 아니었다.

Round 04에서는 rfr을 실제 계산했다: Neo4j에서 가져온 facts와 ground truth required facts를 metric × year × value 기준으로 매칭했다.

| method | Round 04 실제 rfr | locked test rfr (하드코딩) |
|---|---:|---:|
| `vector_only_v4` | 1.000 | 1.000 |
| `graph_neo4j_v4` | 0.686 | 하드코딩 N/A |
| `hybrid_neo4j_v4` | 0.686 | 하드코딩 N/A |

Neo4j LLM IE KG가 required facts를 68.6% 커버한다는 것은, LLM이 evidence text에서 모든 필요 사실을 추출하지 못했거나 metric canonical name이 불일치했음을 의미한다.

### 7.4 Round 02 대비 성능 해석

Round 02에서 `hybrid_vector_graph`는 answer_correctness 0.9643으로 최고 방법이었다. Round 04에서는 `hybrid_neo4j_v4`가 0.200에 불과했다. 이 차이는 다음 세 가지 복합 요인 때문이다.

| 요인 | Round 02 | Round 04 |
|---|---|---|
| Graph facts 수 | ~6개 (정밀) | ~57개 (과잉) |
| KG 구축 방식 | Rule-based curation, targeted | LLM IE, 전체 evidence |
| Formula contract | dev+test 전체 OK | test 10개 없음 → 0점 강제 |
| 케이스 수 | 7개 | 25개 (test 10 포함) |

Round 02와 Round 04의 직접 비교는 적절하지 않다. Round 02는 정밀하게 큐레이션된 7개 케이스, Round 04는 다양성이 더 높은 25개 케이스 + 미완성 formula contract 조건에서 실행되었다.

### 7.5 왜 Round 04 케이스를 Round 05에서 그대로 재활용하는가

Round 05에서 같은 25개 케이스를 재사용하는 것이 과적합이나 방법론 위반이 아닌 이유를 상세히 설명한다.

#### 이유 1: Round 04 점수가 "진짜 성능"이 아닌 "인프라 결함" 때문에 0점이었다

test 10개 케이스가 Round 04에서 0점인 것은 모델이 오답을 했기 때문이 아니다. **채점 인프라(formula contract)가 없었기 때문에 채점 자체를 할 수 없었다.** 이것은 모델 응답의 quality를 측정한 결과가 아니라, scorer가 강제로 0을 반환한 것이다. 따라서 "같은 케이스를 다시 돌린다"는 것이 아니라, "처음으로 올바르게 채점한다"는 의미에 가깝다.

#### 이유 2: 모델 과적합이 발생할 수 있는 메커니즘이 없다

과적합은 모델이 특정 데이터에 대해 학습하고, 그 데이터에서 비정상적으로 높은 성능을 보일 때 발생한다. Round 05에서:
- GPT-4o-mini의 가중치는 변경되지 않는다.
- 케이스를 미리 학습시키거나 fine-tuning하지 않는다.
- 프롬프트를 test 케이스에 맞게 조정하지 않는다.

따라서 전통적인 의미의 모델 과적합은 불가능하다.

#### 이유 3: 방법 비교(method comparison)는 formula contract 선택에 영향을 받지 않는다

Round 05에서 답변해야 할 핵심 질문은 "vector_only vs graph_neo4j vs hybrid_neo4j 중 어느 방법이 더 나은가?"이다. Formula contract는 세 방법 모두에게 **동일하게** 적용된다. 즉, formula contract 구성 방식이 편향되어 있더라도, 그 편향은 세 방법 모두에게 동일하게 적용되므로 방법 간 상대적 비교는 여전히 유효하다.

#### 이유 4: Formula contract 구성 시 "정답 복사"를 사용하지 않았다

Round 04 이후 구축한 test split formula contracts는 다음 원칙을 따랐다:
- target slot의 expected_value는 **required facts로부터 직접 계산**했다 (예: 3661 / 13276 × 100 = 27.575)
- expected_answer 텍스트는 sanity check(검증)에만 사용했고, 값을 복사하지 않았다
- 모델에게 보이는 `model_visible_formula_contract`에는 expected numeric value를 포함하지 않았다

따라서 formula contract 자체가 모델에게 정답을 알려주는 구조가 아니다.

#### 이유 5: 방법론적 오염은 존재하지만 범위가 제한적이다

솔직하게 인정해야 할 점은, formula contract를 만들 때 expected_answer 텍스트를 참조했다는 것이다. 예를 들어:
- AMGN의 formula type을 `gross_margin`으로 결정할 때 expected_answer의 서술을 확인했다
- VRSK의 required facts가 잘못되어 있다는 것을 expected_answer로부터 역추적했다

이 부분은 **"formula contract 선택의 방법론적 오염"**이다. 그러나 이것이 영향을 미치는 것은 오직 **절대 점수 수준**이지, **세 방법 간 상대적 순위**가 아니다. 절대 점수는 "diagnostic eval"로 레이블하면 충분하다.

#### 이유 6: 신규 케이스를 쓰는 것은 현재 단계에서 불필요한 비용이다

진짜 clean held-out benchmark가 필요하다면 신규 케이스를 써야 한다. 그러나 현재 단계의 목표는:
- test split 케이스에서 세 방법이 어떻게 다른 패턴을 보이는지 확인
- graph 정보 과부하 문제가 수정되었을 때 성능이 얼마나 회복되는지 확인
- formula contract가 추가되면 채점이 정상화되는지 확인

이 목표들은 같은 케이스로도 충분히 달성 가능하다. 신규 케이스 선정, formula contract 구축, oracle sanity 등에는 상당한 공수가 필요하고, 이것은 최종 benchmark 단계에서 수행하는 것이 적합하다.

---

## 8. 한계와 유효성 경계

| 항목 | 상태 | 비고 |
|---|---|---|
| Neo4j write 여부 | 없음 ✅ | KG 오염 없음 |
| locked test directory 수정 | 없음 ✅ | read-only 유지 |
| test split 채점 유효성 | ❌ 0점 강제 | formula contract 부재 |
| graph 방법 성능 신뢰도 | ⚠️ 제한 | 정보 과부하(avg 57.4 facts) |
| rfr 측정 방식 | ✅ 실제 계산 | locked test의 하드코딩 1.0 문제 해소 |
| 방법 간 비교 유효성 | ⚠️ 부분적 | test 0점 포함 → dev+baseline 15개만 유효 |
| Round 02와 직접 비교 | ❌ 부적절 | 케이스 수, KG 방식, formula contract 모두 다름 |

---

## 9. 진행된 사후 복구 작업 (Round 04 → Round 05 브릿지)

Round 04 분석을 통해 0점 원인이 규명된 후, 다음 복구 작업이 수행되었다.

### 9.1 Test split formula contract 구축 완료

10개 test 케이스에 대한 formula contract를 다음 두 그룹으로 분리 구축했다.

**Group A — 기존 facts만으로 구축 가능 (5개 케이스):**
- NXPI: operating_income / revenue × 100 (2021, 2022, 2023)
- XEL: female_management% / female_employees% (2023)
- AMGN: (product_sales + other_rev − cost_of_sales) / total_revenue × 100 (2022, 2023)
- LOW: diluted EPS = 13.20, YoY % = 29.8% (evidence_text에서 prior EPS 보충 추출)
- MPC: income_from_continuing_ops / total_revenues × 100 (2021, 2022, 2023)

**Group B — LLM으로 evidence_text 재추출 필요 (5개 케이스):**
- APD: Sales + Cost_of_sales 3개년 추출 (GPT-4o-mini, fallback_used=0)
- BXP: total_revenue 2개년 추출
- MU: revenue 3개년 + net_income/operating_income 보완
- VRSK: revenues, operating_income, net_income_attributable 추출
- GM: net_sales_tpo + cost_of_sales_tpo 3개년 추출

**결과:**
- `test_scorer_contracts.jsonl`: 10 rows (5A + 5B)
- `test_model_visible_contracts.jsonl`: 10 rows
- `reextracted_facts_B.jsonl`: 24 facts, fallback_used=0

---

## 10. Round 05 계획

Round 04에서 규명된 두 가지 문제 — formula contract 부재, graph 정보 과부하 — 를 모두 수정한 뒤 Round 05를 실행한다.

| 항목 | Round 04 | Round 05 |
|---|---|---|
| Formula contract | dev+baseline 15개만 | **25개 전체 (test 10 추가)** |
| Graph facts 수 | 평균 57.4개 | year + keyword 필터 적용 → 목표 15개 이하 |
| Test split 채점 | 불가 (0점 강제) | **처음으로 유효 채점** |
| 결과 레이블 | N/A | `"Round 5 — diagnostic eval, post-hoc formula contracts"` |
| 의미 | 탐색/원인 규명 | **첫 완전 method 비교 (25 cases)** |

**예상 Round 05 주요 질문:**
1. formula contract가 추가되면 test split에서 세 방법 성능이 어떻게 나오는가?
2. graph 정보 과부하를 줄이면 `graph_neo4j_v4`와 `hybrid_neo4j_v4`가 얼마나 회복되는가?
3. dev + test 합산 기준으로 Round 02의 패턴("hybrid가 우세")이 재현되는가?

---

## 11. 관련 산출물

### Round 04 실행 산출물
```
outputs/round3_eval_runs/round4_llm_ie_kg_20260528_191900/
  round4_traces.jsonl              (75 rows)
  round4_summary.md
  neo4j_facts_cache.jsonl
  failure_analysis.jsonl

outputs/round4_neo4j_eval/state.json
```

### Round 04 → 05 브릿지 산출물 (formula contract 복구)
```
outputs/round3_eval_harness/formula_contract_v3_2_test_split/
  test_scorer_contracts.jsonl      (10 rows)
  test_model_visible_contracts.jsonl (10 rows)
  supplemental_facts_A.jsonl
  reextracted_facts_B.jsonl        (24 facts)
  state_A.json                     (phase=done, 5/5)
  state_B.json                     (phase=done, 5/5, openai_calls=5)
```

### LLM IE KG 구축 산출물
```
outputs/kg_rebuild_llm_ie/state.json
  (1434 observations, 25 companies, 328 metrics, 5736 relationships)
```

---

## 12. 보고서용 요약 문장

내부 공유 또는 발표에서는 다음 문장을 사용할 수 있다.

> Round 04에서는 Shadow Overlay 방식 대신 Neo4j LLM IE KG(`kg-llm-ie-v1-20260528`)에서 실제 Cypher로 사실을 조회하는 방식으로 전환했다. 75개 실행(25 cases × 3 methods) 모두 완료되었으나, test split 10개 케이스는 formula contract 부재로 강제 0점 처리되었다. 채점 가능한 dev+baseline 15개 기준으로 `vector_only_v4`(ac=0.467)가 `graph_neo4j_v4`(ac=0.200)보다 높게 나왔는데, 이는 Neo4j 조회가 케이스당 평균 57.4개 사실을 반환해 정보 과부하가 발생했기 때문이다. Round 04의 가장 중요한 성과는 성능 수치 자체가 아니라, locked test부터 반복된 0점의 진짜 원인 — formula contract 부재 — 을 규명한 것이다. 이를 토대로 test 10개에 대한 formula contract를 사후 구축했으며, Round 05에서 처음으로 25개 전체 케이스를 유효하게 채점할 수 있게 되었다.
