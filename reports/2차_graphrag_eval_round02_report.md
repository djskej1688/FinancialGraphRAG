---
title: "Selected 7 GraphRAG Evaluation Round 02 상세 보고서"
date: "2026-05-24"
---

# Selected 7 GraphRAG Evaluation Round 02 상세 보고서

## 1. Executive Summary

Round 02는 Round 01에서 발견된 coverage 병목을 targeted curation으로 해소한 뒤, selected 7개 전체에 대해 GraphRAG evaluation을 실행한 라운드이다. 이번 라운드에서는 전체 KG 재큐레이션 없이 selected 7 평가에 필요한 missing required facts만 보완했고, 그 결과 `ready_for_eval`이 `1 / 7`에서 `7 / 7`로 개선되었다.

핵심 결과는 다음과 같다.

- KG 기준 상태: `kg-full-provenance-20260524`
- Curation round: `02`
- 평가 모델: `gpt-4o-mini`
- 평가 케이스: selected 7개 전체
- 비교 method: `vector_only`, `graph_facts_only`, `hybrid_vector_graph`, `gold_context`
- 결과 row: `28`개 = 7 cases x 4 methods
- Opik trace: `28 / 28`개 생성
- required trace fields 누락: `0`
- required fact recall: 모든 method 평균 `1.0`
- targeted curation round03 후보: `0`

가장 중요한 관찰은 `hybrid_vector_graph`가 평균 answer correctness `0.9643`, numeric correctness `0.8490`으로 가장 강하게 나왔다는 점이다. `vector_only` 대비 answer correctness는 `+0.2067`, numeric correctness는 `+0.2381`, faithfulness는 `+0.1191` 개선되었다.

## 2. 실험 목적과 위치

이번 프로젝트의 핵심 목적은 다음 두 가지이다.

1. vector 검색보다 graph가 추론 문제에서 나아지는지 확인한다.
2. 서로 다른 맥락의 데이터를 graph로 통합하는 모습을 보여준다.

Round 02는 이 중 첫 번째 목적, 즉 `Financials` 및 `Company overview`의 typed reasoning case에서 graph 기반 구조화 정보가 답변 성능에 도움이 되는지를 검증하는 단계이다. CRWD 중심의 cross-context 통합 데모는 별도 확장 단계로 남겨두고, 이번 라운드는 selected 7개의 정량/구조형 추론 평가에 집중했다.

## 3. Round 02 Targeted Curation 결과

Round 01에서는 selected 7개 중 `e7129c27`만 ready였고, 나머지 6개는 required Metric/Year/Value 중 일부가 KG에서 조회되지 않아 평가 대상에서 제외되었다. Round 02에서는 이 문제를 전체 KG 재처리로 해결하지 않고, selected 7의 missing required facts 11개만 대상으로 patch했다.

### 3.1 Patch Summary

- Target missing facts: `11`
- Applied fixes: `11`
- Unresolved fixes: `0`
- LLM calls: `0`
- Graph delta: `+16 nodes`, `+33 relationships`
- Ready for eval: `1 / 7` -> `7 / 7`
- Missing required fact records: `11` -> `0`

### 3.2 Round 02에서 보완된 핵심 facts

- `b7b8f21b`: Net Income 2022/2023/2024
- `0dc1584f`: Income from Continuing Operations Before Income Tax Expense 2021/2022/2023
- `800ca373`: Weighted Average Number of Basic AEP Common Shares Outstanding 2023
- `8f7b5b57`: Net Sales 2023
- `379644c5`: Dollar Tree Total Associates 2024
- `e6b63fd8`: Reverb Employees After Workforce Reduction 2023, Reverb Workforce Reduction Rate 2023

이 보완은 source fact를 `Observation`으로 추가한 것이며, 최종 계산 결과 percentage를 미리 graph에 넣는 방식이 아니다. 따라서 평가 누수를 줄이고, 모델이 graph facts를 재료로 사용해 계산하도록 설계했다.

## 4. Coverage 결과

Round 02 coverage 결과, selected 7개 전부 DatasetCase, Question, EvidenceText, Answer, canonical Company, required Metric, required Year, required Value 조건을 충족했다.

| case_id   | category         | reasoning_type   |   observation_count |   required_fact_count |   matched_fact_count |   missing_fact_count | ready_for_eval   |
|:----------|:-----------------|:-----------------|--------------------:|----------------------:|---------------------:|---------------------:|:-----------------|
| b7b8f21b  | Financials       | Compositional    |                  39 |                    12 |                   12 |                    0 | True             |
| 0dc1584f  | Financials       | Compositional    |                  36 |                     6 |                    6 |                    0 | True             |
| 800ca373  | Financials       | Addition         |                  34 |                     3 |                    3 |                    0 | True             |
| 8f7b5b57  | Financials       | Division         |                  32 |                     2 |                    2 |                    0 | True             |
| e7129c27  | Company overview | Compositional    |                  36 |                     6 |                    6 |                    0 | True             |
| 379644c5  | Company overview | Division         |                  23 |                     3 |                    3 |                    0 | True             |
| e6b63fd8  | Company overview | Compositional    |                  15 |                     3 |                    3 |                    0 | True             |

## 5. Evaluation 설계

### 5.1 비교 methods

| method | 정의 | 목적 |
|---|---|---|
| `vector_only` | text evidence/context 기반 retrieval만 사용, graph facts 미사용 | 기존 vector 기반 RAG baseline |
| `graph_facts_only` | Neo4j에서 조회한 structured graph facts만 사용, 원문 evidence 미사용 | 구조화 KG fact의 독립적 유용성 확인 |
| `hybrid_vector_graph` | text context와 graph facts를 함께 사용 | GraphRAG의 실제 권장 방식 |
| `gold_context` | 원본 selected case의 gold evidence를 그대로 사용 | 이상적 context 조건에 가까운 upper-bound baseline |

### 5.2 평가 지표

| metric | 의미 |
|---|---|
| `numeric_correctness` | 숫자 계산, 비율, 단위, 반올림 등이 expected answer와 얼마나 맞는지 |
| `answer_correctness` | 전체 답변의 의미와 결론이 expected answer와 얼마나 맞는지 |
| `faithfulness` | 답변이 제공된 context 또는 graph facts에 얼마나 충실한지 |
| `required_fact_recall` | 정답 계산에 필요한 source fact가 입력에 포함되었는지 |

이번 라운드에서는 모든 method의 평균 `required_fact_recall`이 `1.0`이었다. 즉, Round 02의 실패 원인은 KG missing/wrong fact가 아니라, 주로 모델의 계산/서술 수행 문제로 분류된다.

## 6. Method 평균 결과

| method              |   case_count |   avg_answer_correctness |   avg_numeric_correctness |   avg_faithfulness |   avg_required_fact_recall |   failure_count |   delta_answer_vs_vector |   delta_numeric_vs_vector |
|:--------------------|-------------:|-------------------------:|--------------------------:|-------------------:|---------------------------:|----------------:|-------------------------:|--------------------------:|
| vector_only         |            7 |                   0.7576 |                    0.6109 |             0.8054 |                          1 |               4 |                   0      |                    0      |
| graph_facts_only    |            7 |                   0.745  |                    0.5663 |             0.7832 |                          1 |               3 |                  -0.0126 |                   -0.0446 |
| hybrid_vector_graph |            7 |                   0.9643 |                    0.849  |             0.9245 |                          1 |               1 |                   0.2067 |                    0.2381 |
| gold_context        |            7 |                   0.84   |                    0.6878 |             0.8439 |                          1 |               2 |                   0.0824 |                    0.0769 |


### 6.1 핵심 해석

- `hybrid_vector_graph`가 answer correctness와 numeric correctness에서 모두 1위이다.
- `vector_only`와 `graph_facts_only`는 평균적으로 비슷하지만, case별로 강점이 다르다.
- `graph_facts_only`는 구조화된 숫자를 정확히 제공하지만, 원문 설명 맥락이 부족한 경우 모델이 계산이나 trend 설명을 생략할 수 있다.
- `hybrid_vector_graph`는 text context와 graph facts를 함께 제공하기 때문에 source selection과 reasoning이 동시에 안정화된 것으로 해석된다.
- `gold_context`는 이상적인 context 조건이지만, 모델이 percentage 계산이나 answer format을 일부 생략하면 hybrid보다 낮게 나올 수 있다. 따라서 gold_context는 retrieval upper-bound이지 generation upper-bound라고 단정하면 안 된다.

## 7. Case별 상세 결과

### 7.1 Answer correctness matrix

| case_id   |   vector_only |   graph_facts_only |   hybrid_vector_graph |   gold_context |
|:----------|--------------:|-------------------:|----------------------:|---------------:|
| b7b8f21b  |        1      |              0.615 |                  1    |           1    |
| 0dc1584f  |        1      |              0.3   |                  1    |           1    |
| 800ca373  |        1      |              1     |                  1    |           1    |
| 8f7b5b57  |        0.5333 |              1     |                  1    |           1    |
| e7129c27  |        0.75   |              1     |                  0.75 |           1    |
| 379644c5  |        0.58   |              1     |                  1    |           0.3  |
| e6b63fd8  |        0.44   |              0.3   |                  1    |           0.58 |

### 7.2 Numeric correctness matrix

| case_id   |   vector_only |   graph_facts_only |   hybrid_vector_graph |   gold_context |
|:----------|--------------:|-------------------:|----------------------:|---------------:|
| b7b8f21b  |        0.7    |             0.45   |                0.7    |         0.7    |
| 0dc1584f  |        1      |             0      |                1      |         1      |
| 800ca373  |        1      |             1      |                1      |         1      |
| 8f7b5b57  |        0.3333 |             1      |                1      |         1      |
| e7129c27  |        0.6429 |             0.7143 |                0.6429 |         0.7143 |
| 379644c5  |        0.4    |             0.8    |                0.8    |         0      |
| e6b63fd8  |        0.2    |             0      |                0.8    |         0.4    |

### 7.3 Method별 failure count

| method              |   failure_count |
|:--------------------|----------------:|
| vector_only         |               4 |
| graph_facts_only    |               3 |
| hybrid_vector_graph |               1 |
| gold_context        |               2 |

### 7.4 주요 case 관찰

- `b7b8f21b` RMD margin trend: vector, hybrid, gold는 answer correctness `1.0`이지만 graph_facts_only는 `0.615`로 낮았다. structured facts만으로는 gross/operating/net margin trend를 충분히 서술하지 못한 것으로 보인다.
- `0dc1584f` AIZ operating margin trend: vector, hybrid, gold는 모두 `1.0`, graph_facts_only는 `0.3`으로 낮았다. 필요한 facts는 있었지만 모델이 structured facts만 보고 연도별 trend를 제대로 계산/정리하지 못했다.
- `800ca373` AEP EPS reconciliation: 모든 method가 `1.0`으로 성공했다.
- `8f7b5b57` SMCI operating expense ratio: graph_facts_only, hybrid, gold는 `1.0`, vector_only는 `0.5333`이었다. 이 case는 graph facts가 숫자 선택에 분명히 도움이 된 사례이다.
- `e7129c27` MCO employee distribution: graph_facts_only와 gold는 `1.0`, vector_only와 hybrid는 `0.75`였다. hybrid가 항상 best는 아니며, prompt/context 조합에 따른 model reasoning error가 남아 있음을 보여준다.
- `379644c5` Dollar Tree FT/PT ratio: graph_facts_only와 hybrid는 `1.0`, vector_only는 `0.58`, gold_context는 `0.3`이었다. workforce 구조화 facts가 강하게 작동한 사례이다.
- `e6b63fd8` Etsy workforce reduction: hybrid만 `1.0`이고 나머지는 낮았다. text context와 graph facts가 함께 있을 때 감원 전/후 계산이 안정화된 사례이다.

## 8. 실패 분석

Round 02에서 targeted curation round03 후보가 `0`인 이유는, 실패가 KG missing/wrong fact로 분류되지 않았기 때문이다. 모든 failure는 `model_reasoning_error`로 분류되었다.

실패 해석은 다음과 같다.

- KG에는 required fact가 존재했고, required_fact_recall도 `1.0`이었다.
- 실패는 대부분 모델이 percentage 계산을 생략하거나 trend 비교를 완성하지 못한 경우이다.
- 따라서 다음 보완 대상은 KG curation이 아니라 prompt/evaluator/model reasoning 제어이다.

## 9. 결론

Round 02는 selected 7 전체에 대한 첫 완전 평가 라운드이다. 결과적으로 `hybrid_vector_graph`가 가장 강한 method로 나타났으며, 이는 graph facts가 text retrieval을 대체하기보다는 보완할 때 가장 유효하다는 해석을 지지한다.

보고서나 발표에서는 다음과 같이 말하는 것이 안전하다.

> Selected 7 reasoning benchmark에서 KG source-fact coverage를 100%로 만든 뒤 평가한 결과, `hybrid_vector_graph`가 `vector_only`보다 answer correctness와 numeric correctness 모두에서 높은 평균 점수를 보였다. 실패는 KG missing fact가 아니라 모델 계산/서술 오류로 분류되어, 현재 병목은 graph curation이 아니라 reasoning prompt 및 answer formatting 단계로 이동했다.

## 10. 한계와 다음 단계

### 10.1 한계

- selected 7개만 평가했기 때문에 통계적 일반화에는 한계가 있다.
- 모델은 `gpt-4o-mini` 하나만 사용했다.
- scoring은 프로젝트 내부 지표이며, 더 넓은 benchmark에서는 별도 judge/evaluator 검증이 필요하다.
- `gold_context`가 항상 가장 높게 나오지 않은 점은 prompt와 answer formatting 민감도를 의미한다.
- 이번 라운드는 CRWD cross-category integration demo가 아니라 Financials/Company overview reasoning comparison에 초점을 둔다.

### 10.2 다음 작업 제안

1. Round 02 결과를 발표/보고서용 핵심 성과로 고정한다.
2. `hybrid_vector_graph`를 primary GraphRAG method로 채택한다.
3. model_reasoning_error를 줄이기 위해 prompt에 계산식, 단위, final answer format을 더 강하게 요구한다.
4. `graph_facts_only`에는 단순 fact list가 아니라 formula hint 또는 metric grouping을 추가하는 실험을 진행한다.
5. 동일 selected 7에 대해 다른 모델 또는 반복 실행을 통해 안정성을 확인한다.
6. 다음 확장으로 CRWD 4개 case를 활용해 cross-context graph integration demo를 만든다.

## 11. Source Artifacts

- `selected7_eval_report(1).md`
- `selected7_eval_results(1).csv`
- `method_comparison_summary.csv`
- `case_coverage_report_after_round02(1).md`
- `case_coverage_report_after_round02(1).csv`
- `curation_round02_report(1).md`
