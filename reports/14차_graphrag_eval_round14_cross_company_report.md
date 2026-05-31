# Round 14 Summary: Multi-Company Semantic KG Cross-Company Evaluation

**KG batch:** `kg-round14-multicompany-v1-20260530`  
**Claim boundary:** `cross_company_graph_advantage_round14`  
**Method:** `graph_structured_v14` / `graph_guided_text_v14` vs vector baselines / source text concat  
**Cases:** 80 synthetic cross-company cases, 5 methods = 400 traces  
**Model:** `gpt-4o-mini`  
**Neo4j write:** performed in KG build phase, Phase 3.5 audit is read-only  

## Executive Summary

Round 14는 R8~R13에서 반복적으로 확인된 "일반 재무 QA에서는 vector/text가 graph보다 강하다"는 결론을 뒤집는 실험이 아니라, **질의가 KG 온톨로지의 구조와 정확히 맞을 때 graph가 어디에서 강해지는지**를 분리해 측정한 라운드다.

핵심 결과는 명확하다. `Company -> Observation -> Metric -> Year` 구조로 anchor가 걸린 cross-company 비교/계산 질의에서는 `graph_structured_v14`가 `0.825` AC를 기록했고, `vector_single_v14`는 `0.0625`, `vector_multi_by_company_v14`는 `0.0875`에 머물렀다. 특히 vector 실패의 주된 원인은 답변 계산 능력 이전에 **두 회사 중 하나를 검색하지 못하는 coverage failure**였다.

다만 이 결과는 "GraphRAG가 항상 VectorRAG보다 좋다"는 주장이 아니다. Phase 3.5 routing audit 결과, 80개 case 전부가 `structural_graph` 버킷으로 들어갔다. 따라서 허용 가능한 주장은 더 좁다.

> **허용 주장:** `Company-Metric-Year` anchor와 traversal이 가능한 cross-company structured query에서는 graph_structured가 vector retrieval보다 크게 우위였다.  
> **금지 주장:** 일반 single-company QA, FinDER/FinQA/TAT-QA 전체, 또는 fallback/text-only 버킷까지 graph가 우월하다고 일반화하면 안 된다.

## Overall Results

| Method | n | AC | NC | both companies found | RFR | avg tokens |
|---|---:|---:|---:|---:|---:|---:|
| vector_single_v14 | 80 | 0.0625 | 0.6886 | 0.1250 | 0.4250 | 5744.8 |
| vector_multi_by_company_v14 | 80 | 0.0875 | 0.6869 | 0.2250 | 0.4625 | 6436.0 |
| graph_structured_v14 | 80 | **0.8250** | 0.9649 | 1.0000 | 1.0000 | 2356.5 |
| graph_guided_text_v14 | 80 | 0.8000 | **0.9680** | 1.0000 | 1.0000 | 3593.5 |
| source_text_concat_v14 | 80 | 0.3375 | 0.8622 | 1.0000 | 1.0000 | 3549.2 |

`graph_structured_v14`는 정확도뿐 아니라 token 효율에서도 가장 좋았다. 평균 token은 2,356.5로, `vector_single_v14`의 5,744.8보다 훨씬 낮다. 이는 graph가 더 많은 텍스트를 넣어서 이긴 것이 아니라, 필요한 `(ticker, metric, year, value)` 구조를 직접 찾아 넣었기 때문에 이긴 결과로 해석된다.

## By Difficulty Level

| Level | n/cases | vector_single | vector_multi | graph_structured | graph_guided_text | source_text_concat |
|---|---:|---:|---:|---:|---:|---:|
| L1_direct | 40 | 0.0500 | 0.0500 | **0.9250** | **0.9250** | 0.2750 |
| L2_derived | 30 | 0.1000 | 0.1667 | **0.8667** | **0.8667** | 0.4000 |
| L3_trend | 10 | 0.0000 | 0.0000 | 0.3000 | 0.1000 | **0.4000** |

L1/L2에서는 graph가 압도적으로 강했다. 반면 L3 trend에서는 `source_text_concat_v14`가 0.4000으로 가장 높았고, `graph_structured_v14`는 0.3000에 그쳤다. 즉 구조화 KG는 cross-company direct/derived metric 비교에는 강하지만, trend 해석이나 multi-year narrative 판단에서는 여전히 텍스트 기반 context가 필요하다.

## Hypothesis Results

| Hypothesis | Result | Detail |
|---|---|---|
| H1: graph_structured > vector_single | TRUE | 0.8250 vs 0.0625 |
| H2: graph_structured >= vector_multi_by_company | TRUE | 0.8250 vs 0.0875 |
| H3: graph_guided_text >= graph_structured | FALSE | 0.8000 vs 0.8250 |
| H4: graph both-company coverage > vector_single | TRUE | 1.0000 vs 0.1250 |
| H5: vector_single failure missing-company rate | TRUE signal | failed vector_single traces 중 missing-company rate = 0.9333 |

H3가 false인 점이 중요하다. KG facts에 text를 추가한 `graph_guided_text_v14`가 `graph_structured_v14`보다 높지 않았다. 이번 질의군에서는 텍스트 보강보다 **정확한 구조화 traversal 자체**가 핵심 성능 원인이다.

## KG Build and Smoke Gate

| Field | Value |
|---|---:|
| observations written | 5,640 |
| companies | 323 |
| canonical metrics | 34 |
| comparable metric-year cells | 131 |
| smoke sample cases | 25 |
| canonical mapped ratio | 0.894 |
| evidence quote verified ratio | 0.9461 |
| duplicate consistency ratio | 1.000 |
| placeholder count | 0 |
| value-as-year count | 0 |
| smoke gate pass | true |

Round 13의 핵심 교훈은 "KG 품질이 낮으면 graph는 text/vector보다 약하다"였다. Round 14는 그 다음 단계로, multi-company KG를 만들고 cross-company 질의가 가능한 형태로 온톨로지를 확장했다. smoke gate 기준에서는 placeholder metric과 value-as-year 문제가 사라졌고, evidence quote 검증도 94.61%로 충분히 높았다.

## Phase 3.5 Anchor Coverage + Traversal Robustness

Phase 3.5는 "write log에는 썼다고 되어 있는데 Neo4j에서 실제 Company-Observation-Metric-Year traversal로 reachable하지 않은 silent fail"을 잡기 위한 audit다.

| Check | Value |
|---|---:|
| Neo4j verification | PASS |
| written triples | 5,246 |
| written triples reachable in Neo4j | 5,246 |
| written triples NOT reachable | 0 |
| reachable triples in Neo4j | 5,253 |
| company anchor nodes | 323 |
| canonical metric nodes | 34 |
| observation rows | 5,640 |
| observations with source chunk | 5,640 |
| evidence verified observations | 4,887 |
| chunks with embeddingText | 973 |
| controlled-vocab metrics total | 35 |
| controlled-vocab orphan metrics | 1 (`ebitda`) |
| comparable cells >=2 companies | 131 |

결론: **Neo4j에 쓰인 triple은 모두 실제 traversal로 reachable하다.** 따라서 이번 graph 우위는 "파일에는 있었지만 DB 관계가 끊긴 상태"에서 나온 artifact가 아니다.

## Retrieval Mode Routing

80개 synthetic case에 대해 routing logic을 적용하고, 결과를 `round14_traces.jsonl` 400개 trace에 join했다.

| Retrieval mode | cases |
|---|---:|
| structural_graph | 80 |
| graph_guided_chunk | 0 |
| vector_topic_fallback | 0 |
| text_only | 0 |
| no_go | 0 |

Mode-stratified method score는 다음과 같다.

| Method | structural_graph AC | n |
|---|---:|---:|
| vector_single_v14 | 0.062 | 80 |
| vector_multi_by_company_v14 | 0.087 | 80 |
| graph_structured_v14 | **0.825** | 80 |
| graph_guided_text_v14 | 0.800 | 80 |
| source_text_concat_v14 | 0.338 | 80 |

**해석:** 이번 R14의 graph 우위는 `structural_graph` 버킷 안에서만 claim해야 한다. 다만 이번 completed run에서는 80개 case 전부가 structural bucket에 속하므로, structural bucket 결과가 full 400 trace 재집계 결과와 동일하다.

## Why Graph Wins Here

R8~R13에서는 graph가 자주 약했다. 이유는 KG가 질문에 필요한 metric/year/value를 정확히 담지 못하거나, text evidence를 생략하면서 모델이 계산에 필요한 맥락을 잃었기 때문이다. R14에서 결과가 달라진 이유는 retrieval 문제가 다음 세 가지 조건을 만족했기 때문이다.

1. **온톨로지가 질의 구조와 맞았다.**  
   질문이 "회사 A와 회사 B의 같은 metric/year를 비교하라"는 형태이고, KG도 `Company-Metric-Year-Value` 구조로 되어 있다. 이때 graph traversal은 search가 아니라 deterministic lookup에 가까워진다.

2. **양쪽 회사 anchor가 모두 있었다.**  
   graph 계열은 both_companies_found가 1.0이었다. 반면 vector_single은 0.125, vector_multi도 0.225에 그쳤다. vector는 한 회사만 검색하거나 엉뚱한 chunk를 가져오는 문제가 컸다.

3. **필요 fact가 압축되어 들어갔다.**  
   graph_structured는 평균 2,356 tokens로 가장 작다. text를 많이 넣어서 이긴 것이 아니라, 필요한 structured facts만 넣어서 이겼다.

따라서 R14는 "KG 생성에 쓴 온톨로지에 성능이 어떻게 의존하는가"에 대한 강한 증거다. graph는 ontology와 question topology가 맞을 때 강하고, 그렇지 않으면 R12/R13처럼 text/vector보다 약할 수 있다.

## Relation to R12/R13

| Round | Main question | Result |
|---|---|---|
| R12 | KG facts + source text가 text-only보다 좋은가? | No. text-only가 더 강함 |
| R13 | semantic IE KG가 broken KG보다 좋은가? | Yes, but still below vector/text |
| R14 | cross-company structural query에서 graph traversal이 vector보다 좋은가? | Yes, by a large margin |

R12/R13의 결론과 R14의 결론은 충돌하지 않는다. R12/R13은 single-company / FinDER-style QA에서 "KG facts만으로 충분한가"를 본 것이고, R14는 KG ontology가 직접 지원하는 cross-company structural query를 본 것이다.

즉 최종 해석은 다음과 같다.

> GraphRAG는 일반 재무 QA의 보편적 상위 방법이라기보다, **온톨로지에 명시적으로 모델링된 관계를 따라가야 하는 질의에서 강한 specialized retrieval method**다.

## Limitations

1. **Synthetic cross-company benchmark다.**  
   80개 case는 KG 구조가 지원하는 cross-company 비교를 의도적으로 만들었다. 따라서 자연 발생 질문 분포 전체로 일반화하면 안 된다.

2. **모든 case가 structural_graph로 routing되었다.**  
   fallback bucket에서 graph가 어떻게 작동하는지는 이번 라운드에서 측정되지 않았다.

3. **L3 trend는 아직 약하다.**  
   trend 질의에서는 graph_structured가 0.3000이고 source_text_concat이 0.4000이다. multi-year reasoning은 structured values만으로 충분하지 않을 수 있다.

4. **controlled vocab orphan이 1개 있다.**  
   `ebitda`는 정의되어 있었지만 관측되지 않았다. 이번 case에는 치명적이지 않았지만, metric coverage 관리 항목으로 남겨야 한다.

## Claim Boundary

```
cross_company_graph_advantage_round14

Allowed claim:
  In 80 synthetic cross-company financial comparison cases where both companies
  and required metric-year cells are reachable through the Round 14 semantic KG,
  graph_structured_v14 substantially outperformed vector retrieval baselines.

Allowed mechanism claim:
  The gain is attributable to structural Company-Metric-Year anchoring and
  traversal robustness, not to larger context size.

Not allowed:
  Do not claim that GraphRAG is generally better than VectorRAG across FinDER,
  FinQA, TAT-QA, or arbitrary single-company financial QA.
  Do not claim advantage outside the structural_graph routing bucket.
  Do not use this result as evidence that KG+text always beats text-only.
```

## Output Artifacts

| Artifact | Path |
|---|---|
| state | `outputs/round14_cross_company/state.json` |
| summary | `outputs/round14_cross_company/round14_summary.md` |
| traces | `outputs/round3_eval_runs/round14_cross_company_20260530_133644/round14_traces.jsonl` |
| anchor audit | `outputs/round14_cross_company/03_extraction/anchor_coverage_report.md` |
| routing CSV | `outputs/round14_cross_company/03_extraction/retrieval_mode_routing.csv` |
| mode-stratified scores | `outputs/round14_cross_company/03_extraction/round14_mode_stratified_method_scores.md` |


