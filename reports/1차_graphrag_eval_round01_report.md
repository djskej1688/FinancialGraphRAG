# GraphRAG Evaluation Round 01 현황 보고서

- 작성일: 2026-05-24
- 기준 KG batch: `kg-full-provenance-20260524`
- 기준 curation round: `01`
- 평가 대상: FinDER selected 7 cases
- 평가 모델: `gpt-4o-mini`
- 평가 목적: 현재 KG 상태를 고정한 뒤, selected 7개 기준으로 GraphRAG evaluation round 01을 실행하고 coverage 및 method별 성능을 확인

---

## 1. Executive Summary

Round 01은 최종 성능 평가라기보다 **KG freeze 이후 GraphRAG evaluation 파이프라인이 정상 작동하는지 확인하는 1차 검증 라운드**였다.

핵심 결과는 다음과 같다.

| 항목 | 결과 |
|---|---:|
| Selected cases | 7 |
| Ready for eval | 1 / 7 |
| Not ready | 6 / 7 |
| Missing required fact records | 11 |
| 실제 평가 case | `e7129c27` |
| 평가 methods | 4 |
| Opik trace rows | 4 |
| Opik enabled | True |

Round 01에서 `ready_for_eval=true`로 판정된 case는 `e7129c27` 1개뿐이었다. 나머지 6개 case는 DatasetCase, Question, EvidenceText, Answer, canonical Company 연결은 되어 있었지만, answer 계산에 필요한 required Metric / Year / Value 중 일부가 KG에서 Cypher로 조회되지 않아 not ready로 분류되었다.

따라서 이번 결과는 “GraphRAG 성능 검증 실패”가 아니라, **coverage gate가 정상적으로 작동하여 평가 가능한 case와 보완이 필요한 case를 분리한 결과**로 해석하는 것이 맞다.

---

## 2. Round 01의 목적

이번 라운드의 목적은 전체 KG를 다시 큐레이션하거나 전체 데이터를 LLM에 다시 투입하는 것이 아니었다.

목표는 다음과 같았다.

1. 현재 KG 상태를 고정한다.
2. selected 7개 case에 대해 evaluation 가능 여부를 점검한다.
3. KG에서 required facts가 조회 가능한 ready case만 평가한다.
4. vector-only, graph-facts-only, hybrid, gold-context 조건을 비교한다.
5. 평가 실패 또는 not-ready 원인을 targeted curation round 02 후보로 분류한다.

이번 라운드는 프로젝트의 큰 목적과 연결된다.

| 프로젝트 목적 | Round 01에서 확인한 내용 |
|---|---|
| vector 검색보다 graph가 추론 문제에서 나아지는지 보기 | ready case 1개에서 method별 answer score를 비교 |
| 서로 다른 맥락의 데이터를 graph로 통합하는 모습 보여주기 | 아직 CRWD 통합 데모는 미실행. selected 7 coverage 이후 다음 단계로 분리 필요 |

---

## 3. 기준 상태 Freeze

Round 01의 기준 상태는 다음과 같이 고정되었다.

| 항목 | 값 |
|---|---|
| KG batch | `kg-full-provenance-20260524` |
| Curation round | `01` |
| Coverage generated_at | `2026-05-24T15:31:24Z` |
| Eval generated_at | `2026-05-24T15:31:35Z` |
| Coverage method | Cypher + rule-based checks only |
| 전체 KG 재큐레이션 | 수행하지 않음 |
| 전체 데이터 LLM 재처리 | 수행하지 않음 |

이 기준 상태를 고정한 이유는, 이후 round 02에서 KG를 보완했을 때 어떤 변화가 성능에 영향을 주었는지 비교하기 위해서다.

---

## 4. Selected 7 Coverage 결과

### 4.1 Coverage 요약

| 항목 | 결과 |
|---|---:|
| 전체 selected cases | 7 |
| Ready for eval | 1 |
| Not ready | 6 |
| Missing required fact records | 11 |

`ready_for_eval=true` 판정 기준은 다음 조건을 모두 만족하는 것이다.

- DatasetCase 존재
- Question 연결
- EvidenceText 연결
- Answer 연결
- canonical Company 연결
- expected_answer 계산에 필요한 required Metric / Year / Value가 KG에서 조회 가능

### 4.2 Case별 Coverage Table

| case_id | Category | Reasoning Type | DatasetCase | Question | Evidence | Answer | Company | Metric | Year | Value | Obs | Missing | Ready |
|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| `b7b8f21b` | Financials | Compositional | Y | Y | Y | Y | Y | N | N | N | 36 | 3 | N |
| `0dc1584f` | Financials | Compositional | Y | Y | Y | Y | Y | N | N | N | 33 | 3 | N |
| `800ca373` | Financials | Addition | Y | Y | Y | Y | Y | N | N | N | 33 | 1 | N |
| `8f7b5b57` | Financials | Division | Y | Y | Y | Y | Y | Y | Y | N | 31 | 1 | N |
| `e7129c27` | Company overview | Compositional | Y | Y | Y | Y | Y | Y | Y | Y | 36 | 0 | Y |
| `379644c5` | Company overview | Division | Y | Y | Y | Y | Y | Y | Y | N | 22 | 1 | N |
| `e6b63fd8` | Company overview | Compositional | Y | Y | Y | Y | Y | Y | Y | N | 13 | 2 | N |

### 4.3 Missing Required Facts

| case_id | Missing fact | Required year | Required value | 상태 해석 |
|---|---|---:|---:|---|
| `b7b8f21b` | `net_income_2024` | 2024 | 1,020,951 | metric/year/value 모두 조회 실패 |
| `b7b8f21b` | `net_income_2023` | 2023 | 897,556 | metric/year/value 모두 조회 실패 |
| `b7b8f21b` | `net_income_2022` | 2022 | 779,437 | metric/year/value 모두 조회 실패 |
| `0dc1584f` | `pretax_income_2023` | 2023 | 806.8 | metric/year/value 모두 조회 실패 |
| `0dc1584f` | `pretax_income_2022` | 2022 | 349.9 | metric/year/value 모두 조회 실패 |
| `0dc1584f` | `pretax_income_2021` | 2021 | 771.3 | metric/year/value 모두 조회 실패 |
| `800ca373` | `basic_shares_2023` | 2023 | 518,903,682 | metric/year/value 모두 조회 실패 |
| `8f7b5b57` | `net_sales_2023` | 2023 | 7,123,482 | metric/year는 있음, value 조회 실패 |
| `379644c5` | `dollar_tree_total_associates_2024` | 2024 | 131,521 | metric/year는 있음, value 조회 실패 |
| `e6b63fd8` | `reverb_employees_post_reduction_2023` | 2023 | 240 | metric/year는 있음, value 조회 실패 |
| `e6b63fd8` | `reverb_reduction_rate_2023` | 2023 | 13 | metric/year는 있음, value 조회 실패 |

### 4.4 Coverage 해석

Coverage 결과에서 중요한 점은 다음과 같다.

첫째, selected 7개 모두 기본 구조는 연결되어 있다. DatasetCase, Question, EvidenceText, Answer, canonical Company 연결은 모두 통과했다.

둘째, not-ready의 핵심 원인은 case 자체가 없거나 evidence가 없기 때문이 아니다. 대부분 **required source fact가 Observation 속성 또는 canonical metric/year/value 형태로 조회되지 않는 문제**다.

셋째, not-ready 6개 중 일부는 metric과 year는 연결되어 있으나 value만 누락되어 있다. 이 경우는 targeted patch로 비교적 빠르게 보완 가능성이 높다.

---

## 5. GraphRAG Evaluation Round 01 결과

### 5.1 평가 대상

Coverage gate를 통과한 case는 `e7129c27` 1개였으므로, Round 01 evaluation은 해당 case만 대상으로 수행되었다.

| 항목 | 값 |
|---|---|
| 평가 case | `e7129c27` |
| Category | Company overview |
| Reasoning type | Compositional |
| Question | 2023 vs 2022 distribution of MCO US/non-US employees. |
| Ready for eval | True |
| Methods | `vector_only`, `graph_facts_only`, `hybrid_vector_graph`, `gold_context` |

`e7129c27`은 Moody’s의 2023년과 2022년 U.S. / Non-U.S. employee distribution을 비교하는 문제다. answer 계산에는 2023/2022년의 U.S. employees, Non-U.S. employees, Total employees source fact가 필요하다.

### 5.2 Method별 평가 결과

| case_id | method | numeric_correctness | answer_correctness | faithfulness | required_fact_recall | graph_fact_count | retrieved_context_count | failure_reasons |
|---|---|---:|---:|---:|---:|---:|---:|---|
| `e7129c27` | `vector_only` | 0.6429 | 0.75 | 0.8214 | 1.0 | 0 | 2 | `model_reasoning_error` |
| `e7129c27` | `graph_facts_only` | 0.7143 | 1.00 | 0.8571 | 1.0 | 6 | 0 |  |
| `e7129c27` | `hybrid_vector_graph` | 0.6429 | 0.75 | 0.8214 | 1.0 | 6 | 2 | `model_reasoning_error` |
| `e7129c27` | `gold_context` | 0.7143 | 1.00 | 0.8571 | 1.0 | 0 | 1 |  |

### 5.3 Opik Trace IDs

| method | opik_trace_id |
|---|---|
| `vector_only` | `019e5a9d-1914-78b6-85fe-73d258a34bbe` |
| `graph_facts_only` | `019e5a9d-430e-7f6d-a51b-40626fabdef0` |
| `hybrid_vector_graph` | `019e5a9d-568e-7c86-b487-ce782739213b` |
| `gold_context` | `019e5a9d-86f1-7e2a-93dc-781487daed4f` |

---

## 6. 평가 결과 해석

### 6.1 핵심 관찰

Round 01에서 평가 가능한 단일 case인 `e7129c27`에서는 다음 패턴이 관찰되었다.

| method | 결과 해석 |
|---|---|
| `graph_facts_only` | structured graph facts만 사용했음에도 answer_correctness 1.0 달성 |
| `gold_context` | upper-bound baseline으로 answer_correctness 1.0 달성 |
| `vector_only` | required fact recall은 1.0이었지만 일부 계산/percentage 누락으로 answer_correctness 0.75 |
| `hybrid_vector_graph` | required fact recall은 1.0이었지만 일부 계산/percentage 누락으로 answer_correctness 0.75 |

즉, 이 case에서는 graph facts가 필요한 숫자들을 구조화해서 제공했고, 모델이 정답 형태에 더 가깝게 계산하도록 도운 것으로 해석할 수 있다.

다만 평가 case가 1개뿐이므로, 이 결과만으로 “GraphRAG가 Vector RAG보다 전반적으로 우수하다”고 결론내릴 수는 없다.

현재 말할 수 있는 결론은 다음 수준이다.

> Round 01에서는 evaluation pipeline과 Opik trace/score 기록이 정상 작동했고, ready case 1개에서는 `graph_facts_only`가 `vector_only`보다 높은 answer correctness를 보였다. 다만 selected 7 전체에 대한 결론을 위해서는 round 02에서 ready case 수를 늘려야 한다.

### 6.2 `gold_context`의 의미

`gold_context`는 실제 운영 방식의 경쟁 method가 아니라 upper-bound baseline이다.

즉, gold_context는 다음 질문에 답하기 위한 기준이다.

> “정답 근거가 완벽하게 주어졌을 때 모델은 어느 정도까지 답할 수 있는가?”

따라서 실제 비교 중심은 다음 세 method다.

- `vector_only`
- `graph_facts_only`
- `hybrid_vector_graph`

### 6.3 `hybrid_vector_graph` 결과 해석

이번 case에서 `hybrid_vector_graph`는 `graph_facts_only`보다 낮은 answer_correctness를 보였다. 이는 graph facts가 있었음에도 text context와 함께 들어가면서 모델이 일부 percentage 계산 또는 비교 설명을 생략했기 때문으로 분류되었다.

이 결과는 hybrid 방식 자체가 나쁘다는 뜻은 아니다. 오히려 다음 개선 포인트를 보여준다.

- hybrid prompt에서 graph facts를 authoritative source로 명시할 필요가 있음
- text context는 설명 보조용으로 제한할 필요가 있음
- 계산형 case에서는 final answer format을 더 강하게 요구할 필요가 있음

---

## 7. Round 01의 의미

Round 01의 가장 중요한 성과는 성능 수치 자체가 아니라, **GraphRAG evaluation을 하기 위한 gate와 trace 구조가 실제로 작동했다는 점**이다.

이번 라운드에서 확인된 사항은 다음과 같다.

1. KG freeze 기준이 산출물에 남았다.
2. selected 7 coverage check가 수행되었다.
3. not-ready case를 억지로 평가하지 않고 차단했다.
4. ready case만 method별로 평가했다.
5. Opik trace와 local CSV/JSONL 결과가 생성되었다.
6. 실패 원인이 `model_reasoning_error` 또는 missing required facts로 분리되었다.
7. round 02에서 무엇을 고쳐야 하는지 명확해졌다.

즉, 이번 결과는 “1개밖에 못 돌린 실패”가 아니라, **정상적인 smoke/evaluation gating 결과**로 보는 것이 맞다.

---

## 8. 한계

Round 01의 한계는 명확하다.

1. 평가 case가 1개뿐이다.
2. selected 7 전체에 대한 method별 성능 비교는 아직 불가능하다.
3. Financials case 4개는 모두 not ready로 남았다.
4. Company overview case 중 `e7129c27`만 평가되었다.
5. CRWD 통합 데모는 아직 이 라운드에서 평가되지 않았다.
6. `vector_only`와 `hybrid_vector_graph`의 실패가 retrieval 문제인지 prompt/format 문제인지 더 세부 분석이 필요하다.

따라서 Round 01 결과는 최종 benchmark가 아니라 다음 단계로 넘어가기 위한 진단 결과다.

---

## 9. Targeted Curation Round 02 후보

Round 02에서는 전체 KG를 다시 큐레이션하지 않고, selected 7의 missing required facts만 대상으로 보완하는 것이 적절하다.

### 9.1 1순위: value만 누락된 case

다음 case들은 metric/year는 조회되지만 value가 조회되지 않았다. 따라서 patch 우선순위가 높다.

| case_id | Missing fact | Required value | 예상 보완 방식 |
|---|---|---:|---|
| `8f7b5b57` | `net_sales_2023` | 7,123,482 | Observation value patch |
| `379644c5` | `dollar_tree_total_associates_2024` | 131,521 | Observation value patch |
| `e6b63fd8` | `reverb_employees_post_reduction_2023` | 240 | Observation value patch |
| `e6b63fd8` | `reverb_reduction_rate_2023` | 13 | Observation value patch |

### 9.2 2순위: metric normalization / canonical mapping이 필요한 case

다음 case들은 metric/year/value가 모두 조회 실패했다. metric normalization 또는 canonical fact mapping이 필요할 가능성이 높다.

| case_id | Missing facts | 예상 보완 방식 |
|---|---|---|
| `b7b8f21b` | `net_income_2024`, `net_income_2023`, `net_income_2022` | `Net income` canonical metric mapping |
| `0dc1584f` | `pretax_income_2023`, `pretax_income_2022`, `pretax_income_2021` | `Income from continuing operations before income tax expense` → `pretax_income` mapping |
| `800ca373` | `basic_shares_2023` | `Weighted average number of basic shares outstanding` → `basic_shares` mapping |

---

## 10. Round 02 권장 작업

Round 02의 목표는 전체 KG 품질을 완벽하게 만드는 것이 아니라, selected 7의 evaluation coverage를 늘리는 것이다.

권장 목표는 다음과 같다.

| 목표 | 기준 |
|---|---|
| 최소 성공 기준 | ready_for_eval 5 / 7 이상 |
| 이상적 목표 | ready_for_eval 7 / 7 |
| 평가 trace 목표 | ready case 수 × 4 methods |
| 유지 원칙 | 전체 KG 재큐레이션 금지, 전체 LLM 재처리 금지 |

권장 산출물은 다음과 같다.

```text
outputs/kg_build/curation_round_02/
- targeted_fixes.jsonl
- metric_normalization_patch.json
- observation_value_patch.jsonl
- case_coverage_report_after_round02.csv
- case_coverage_report_after_round02.md
- curation_round02_report.md
```

그다음 다시 evaluation을 실행한다.

```text
outputs/kg_build/eval_round02/
- selected7_eval_summary.json
- selected7_eval_report.md
- selected7_eval_results.csv
- selected7_eval_traces.jsonl
- targeted_curation_round03_candidates.jsonl
```

---

## 11. 현재 상태에 대한 최종 판단

Round 01은 최종 성능 benchmark로는 부족하지만, 실험 설계 측면에서는 정상적으로 진행되었다.

현재 상태는 다음과 같이 정리할 수 있다.

```text
완료된 것:
- KG freeze
- selected 7 coverage check
- ready case filtering
- method별 GraphRAG evaluation 실행
- Opik trace/score 기록
- not-ready case의 missing required facts 식별
- round02 targeted curation 후보 분류

아직 남은 것:
- not-ready 6개 case의 missing required facts 보완
- ready case 수 확대
- selected 7 전체 method별 성능 비교
- CRWD 중심 graph 통합 데모
- round02 이후 결과 기반 해석
```

따라서 Round 01의 결론은 다음과 같다.

> 현재 KG 상태에서 selected 7 중 1개 case만 바로 evaluation 가능했다. 해당 case에서는 `graph_facts_only`가 `vector_only`보다 높은 answer correctness를 보였고, Opik trace/score 기록도 정상 작동했다. 그러나 전체 selected 7에 대한 결론을 내리기에는 coverage가 부족하므로, round 02에서는 missing required facts 11개를 targeted patch하여 ready case 수를 늘리는 것이 다음 단계다.

---

## 12. 관련 산출물

### Coverage 산출물

```text
outputs/kg_build/curation_round_01/case_coverage_report.csv
outputs/kg_build/curation_round_01/case_coverage_report.md
outputs/kg_build/curation_round_01/missing_required_facts.jsonl
```

### Evaluation 산출물

```text
outputs/kg_build/eval_round01/selected7_eval_summary.json
outputs/kg_build/eval_round01/selected7_eval_report.md
outputs/kg_build/eval_round01/selected7_eval_results.csv
outputs/kg_build/eval_round01/selected7_eval_traces.jsonl
outputs/kg_build/eval_round01/targeted_curation_round02_candidates.jsonl
```

---

## 13. 보고서용 요약 문장

내부 공유 또는 발표에서는 다음 문장을 사용할 수 있다.

> Round 01에서는 KG를 `kg-full-provenance-20260524 / curation_round=01`로 freeze한 뒤 selected 7개 case의 evaluation readiness를 점검했다. 7개 중 1개 case만 required Metric/Year/Value가 모두 KG에서 조회되어 evaluation을 실행했고, 해당 case에서는 `graph_facts_only`와 `gold_context`가 answer correctness 1.0을 기록했다. 나머지 6개는 missing required facts 11건으로 인해 not ready로 분류되었으며, 이는 targeted curation round 02의 보완 대상으로 정리했다. 따라서 Round 01은 최종 성능 결론이 아니라 coverage gate와 Opik-based GraphRAG evaluation pipeline이 정상 작동함을 확인한 진단 라운드로 해석한다.
