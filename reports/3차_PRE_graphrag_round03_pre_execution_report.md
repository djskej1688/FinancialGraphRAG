---
title: "GraphRAG Round 03 사전 실행 보고서"
date: "2026-05-25"
status: "pre-execution / case preparation and quality inspection"
kg_batch: "kg-full-provenance-20260524"
primary_method_candidate: "hybrid_vector_graph"
---

# GraphRAG Round 03 사전 실행 보고서

## 1. Executive Summary

이 보고서는 Round 03 orchestration을 실행하기 직전의 상태를 정리한다. 핵심은 Round 03가 이미 evaluation 결과를 낸 라운드가 아니라, **Round 03 실행 전에 case set을 만들고 case 품질을 검사하는 단계**까지 포함해야 한다는 점이다.

이전 Round 00-02 흐름만 기준으로 보면 Round 03는 “KG missing fact를 추가로 고치는 라운드”가 아니다. Round 02에서 selected 7은 이미 모두 evaluation 가능한 상태가 되었고, targeted curation Round 03 후보도 0으로 정리되었다. 따라서 Round 03의 중심은 다음 세 가지로 잡는 것이 맞다.

1. Round 02 결과를 baseline으로 고정한다.
2. Round 03 실행 전에 사용할 case set과 case quality gate를 확정한다.
3. 이후 orchestration에서 prompt hardening, evaluator sanity check, failure replay, CRWD cross-context graph integration demo를 실행한다.

즉, 2026-05-25 기준 branch 상태는 다음과 같이 해석하는 것이 안전하다.

> Round 03 execution 이전 단계에서는 새로운 benchmark/demo case들을 구성하고, 이 case들이 GraphRAG 평가 또는 CRWD integration demo에 적합한지 품질을 검사하고 있었다. 따라서 Round 03 report에는 “pre-round03 case preparation / quality inspection” 섹션이 별도로 들어가야 한다.

## 2. Source Scope

이 보고서는 현재 접근 가능한 문서와 파일 기준으로 작성되었다.

### 2.1 확인된 기준 문서

- `0차_KG구축_보고서.docx`
- `1차_graphrag_eval_round01_report.md`
- `2차_graphrag_eval_round02_report.md`
- `graphrag_round01_round02_integrated_report.md / korean_fixed.md`
- `finder_selected_indexing_manifest.json`
- `붙여넣은 마크다운(1).md`
- `FINDER_seocho_project_final_report.md`

### 2.2 확인 제한

이 보고서는 실제 git branch의 commit diff, branch-local script 변경분, 2026-05-25 이후 생성된 branch-only 산출물을 직접 확인한 것은 아니다. 다만 접근 가능한 File Library와 사용자가 제공한 맥락을 합치면, Round 03 이전에 case 생성 및 case 품질 검사 작업이 진행 중이었다는 흐름은 기존 Round 00-02 문서만으로는 완전히 반영되어 있지 않다.

따라서 이 문서는 **Round 00-02 결과 + pre-round03 case preparation 맥락을 통합한 사전 실행 보고서**로 보면 된다.

## 3. Round 00-02 상태 요약

### 3.1 Round 00: KG 구축 및 1차 품질 진단

Round 00에서는 FinDER, FinQA, TAT-QA, SEC EFX 데이터를 기반으로 provenance-aware KG를 구축했다.

| 항목 | 값 |
|---|---:|
| KG batch | `kg-full-provenance-20260524` |
| 처리 case 수 | 31,519 |
| 실패 | 0 |
| skip | 0 |
| live LLM 호출 | 0 |
| 주요 저장소 | Neo4j + Opik |

Round 00의 핵심 설계는 다음과 같다.

- 숫자, 연도, 지표, 회사명 등 명확한 정보는 rule/parser 기반으로 추출
- 문맥 판단이 필요한 항목만 LLM 후보로 분류
- Neo4j에는 provenance를 남겨 추적 가능하게 저장
- Opik에는 dataset item과 trace를 남겨 평가 가능하게 구성
- 기존 DB 삭제 없이 `batch_id`와 metadata로 구분

Round 00 이후 selected 7 case는 모두 canonical company까지 연결되었고, CompanyAlias, NormalizedMetric, year/value 보완이 일부 수행되었다. 다만 qualitative relation 후보, CRWD/META/Legal/Risk/Governance/Footnotes 관련 후보는 이후 통합 demo 또는 LLM enrichment 후보로 남았다.

### 3.2 Round 01: Coverage gate 및 evaluation smoke test

Round 01은 최종 성능 평가가 아니라, KG freeze 이후 GraphRAG evaluation pipeline과 coverage gate가 정상적으로 작동하는지 확인한 라운드였다.

| 항목 | 결과 |
|---|---:|
| Selected cases | 7 |
| Ready for eval | 1 / 7 |
| Not ready | 6 / 7 |
| Missing required fact records | 11 |
| 실제 평가 case | `e7129c27` |
| 평가 methods | 4 |
| Opik trace rows | 4 |

Round 01의 핵심 의미는 다음과 같다.

- not-ready case를 억지로 평가하지 않았다.
- KG missing fact와 model reasoning failure를 분리했다.
- missing required facts 11건을 Round 02 targeted curation 후보로 넘겼다.
- evaluation trace와 score 기록 pipeline이 정상 작동함을 확인했다.

### 3.3 Round 02: selected 7 전체 평가 완료

Round 02에서는 Round 01에서 발견된 missing required facts 11건만 targeted patch했다. 전체 KG 재큐레이션이나 전체 LLM 재처리는 수행하지 않았다.

| 항목 | 결과 |
|---|---:|
| Target missing facts | 11 |
| Applied fixes | 11 |
| Unresolved fixes | 0 |
| LLM calls | 0 |
| Graph delta | +16 nodes / +33 relationships |
| Ready for eval | 1 / 7 → 7 / 7 |
| Missing required facts | 11 → 0 |

Round 02 evaluation은 selected 7 전체에 대해 4개 method로 수행되었다.

| method | avg_answer_correctness | avg_numeric_correctness | avg_faithfulness | avg_required_fact_recall | failure_count |
|---|---:|---:|---:|---:|---:|
| `vector_only` | 0.7576 | 0.6109 | 0.8054 | 1.0 | 4 |
| `graph_facts_only` | 0.7450 | 0.5663 | 0.7832 | 1.0 | 3 |
| `hybrid_vector_graph` | 0.9643 | 0.8490 | 0.9245 | 1.0 | 1 |
| `gold_context` | 0.8400 | 0.6878 | 0.8439 | 1.0 | 2 |

Round 02의 결론은 다음과 같다.

> selected 7 기준에서는 `hybrid_vector_graph`가 가장 강한 method였다. 남은 실패는 KG missing/wrong fact가 아니라, percentage 계산 생략, trend 비교 미완성, final answer formatting 문제 등 `model_reasoning_error`로 분류된다.

## 4. Pre-Round 03 Case Preparation 포함 여부

### 4.1 이전 요약에 포함되지 않은 부분

이전 Round 00-02 중심 요약은 다음을 충분히 포함하지 않았다.

- Round 03 실행 전 case set을 다시 구성하던 작업
- selected 7 외 CRWD integration용 4개 case 구성
- 단일 case와 조합 dataset indexing 결과
- case quality inspection 또는 indexing manifest 기반 품질 확인
- `combo_reasoning_financials_company_overview`, `combo_crwd_integration_financials_company_footnotes`, `combo_all_selected`의 역할 구분

따라서 2026-05-25 branch 상태를 반영하려면, Round 03 report에 아래의 pre-execution section이 추가되어야 한다.

### 4.2 Round 03 이전 case 구성의 의도

프로젝트 목적은 두 개로 분리된다.

| 목적 | 실험 축 | 대상 case |
|---|---|---|
| vector 대비 graph가 추론 문제에서 나아지는지 확인 | typed reasoning benchmark | Financials + Company overview selected 7 |
| 서로 다른 맥락의 데이터를 graph로 통합하는 모습 보여주기 | cross-context integration demo | CRWD 중심 Financials + Company overview + Footnotes 4개 |

이 구분이 중요하다. selected 7은 quantitative / typed reasoning 성능 비교용이고, CRWD 4개는 하나의 회사에 대해 Financials, Company overview, Footnotes 맥락을 묶어 graph integration을 보여주기 위한 별도 demo subset이다.

## 5. Case Set 설계

### 5.1 Main reasoning benchmark: selected 7

selected 7은 Round 01-02 evaluation의 기준 subset이다.

| case_id | Category | Type | Company / Ticker | 목적 |
|---|---|---|---|---|
| `b7b8f21b` | Financials | Compositional | ResMed / RMD | FY2022-2024 gross/operating/net margin trend |
| `0dc1584f` | Financials | Compositional | Assurant / AIZ | 2021-2023 operating margin trend |
| `800ca373` | Financials | Addition | AEP / AEP | FY2023 EPS / net income reconciliation |
| `8f7b5b57` | Financials | Division | Super Micro / SMCI | operating expense / net sales ratio |
| `e7129c27` | Company overview | Compositional | Moody’s / MCO | U.S. vs Non-U.S. employee distribution |
| `379644c5` | Company overview | Division | Dollar Tree / DLTR | full-time / part-time workforce ratio |
| `e6b63fd8` | Company overview | Compositional | Etsy / ETSY | Reverb workforce reduction before/after |

이 subset은 Round 02에서 coverage가 7/7로 회복되었고, evaluation trace 28개가 생성되었다. Round 03에서는 이 subset을 baseline replay 및 prompt hardening 검증용으로 사용한다.

### 5.2 CRWD integration demo subset: 4 cases

CRWD integration subset은 하나의 회사 CrowdStrike / CRWD에 대해 서로 다른 문맥을 graph로 연결하는 demo용이다.

| case_id | Category | Type | 질문 축 | 품질상 의미 |
|---|---|---|---|---|
| `b703f322` | Financials | Compositional | FY22-FY23, FY23-FY24 revenue growth | revenue numbers, year comparison, growth formula |
| `d2edc80b` | Company overview | Qualitative | labor disputes / work stoppage risk | workforce headcount, labor union, local labor law |
| `3870d6d8` | Company overview | Qualitative | customer support / incident response / recurring revenue / cash flow | competitive capability와 revenue/cash-flow narrative 연결 |
| `3bd723dd` | Footnotes | Qualitative | subscription vs professional services revenue growth | segment revenue, revenue mix, Footnotes/Financials 연결 |

이 subset의 역할은 selected 7과 다르다. selected 7은 method comparison용이고, CRWD 4개는 **cross-context graph integration demo**용이다.

### 5.3 Combo datasets

사전 indexing manifest는 단일 case뿐 아니라 조합 dataset도 정의한다.

| dataset_id | 구성 | 목적 |
|---|---|---|
| `combo_reasoning_financials_company_overview` | 7 cases = Financials 4 + Company overview 3 | graph vs vector reasoning benchmark |
| `combo_crwd_integration_financials_company_footnotes` | 4 cases = Financials 1 + Company overview 2 + Footnotes 1 | CRWD cross-context graph integration demo |
| `combo_all_selected` | 11 cases = selected 7 + CRWD 4 | 내부 공유 / master selected subset |

## 6. Case Quality Inspection 기준

Round 03 실행 전에 case 품질은 다음 gate로 검사하는 것이 좋다.

### 6.1 Metadata completeness gate

각 case는 최소한 다음 필드를 가져야 한다.

- `case_id`
- `category`
- `reasoning`
- `type`
- `query`
- `ticker_candidates`
- `company_candidates`
- `metric_tags`
- `years`
- `reference_count`
- `evidence_chars`
- `evidence_number_candidates`
- `answer_chars`
- `selection_note`

### 6.2 Graph-indexing readiness gate

각 case는 사전 manifest 수준에서 다음 node/relationship 후보가 생성되어야 한다.

- `Case`
- `Question`
- `EvidenceChunk`
- `Category`
- `ReasoningType`
- `Company`
- `CompanyTicker`
- `MetricTag`
- `Year`
- `NumberCandidate`
- `HAS_QUESTION`
- `HAS_EVIDENCE`
- `IN_CATEGORY`
- `HAS_REASONING_TYPE`
- `ABOUT_COMPANY`
- `ABOUT_TICKER`
- `NEEDS_METRIC`
- `MENTIONS_YEAR`
- `HAS_NUMBER_CANDIDATE`

### 6.3 Numeric reasoning quality gate

계산형 case는 다음 기준을 충족해야 한다.

- evidence 안에 충분한 number candidate가 있어야 한다.
- 질문이 요구하는 연도와 evidence의 연도가 일치해야 한다.
- expected answer가 최종 계산값만 주는 것이 아니라, 사용된 source fact와 계산식을 포함해야 한다.
- final derived answer를 graph에 미리 넣지 않아야 한다.
- graph에는 source fact만 들어가고, 모델이 계산하도록 유지해야 한다.

### 6.4 Integration demo quality gate

CRWD integration case는 다음 기준을 충족해야 한다.

- 모든 case가 동일 company/ticker, 즉 CrowdStrike / CRWD로 연결되어야 한다.
- Financials, Company overview, Footnotes가 최소 1개 이상 포함되어야 한다.
- 동일 회사에 대해 revenue growth, workforce/labor, customer support, subscription/professional services revenue mix가 서로 다른 category에서 연결되어야 한다.
- qualitative case는 numeric benchmark 점수 비교용이 아니라 graph integration narrative demo용으로 분리해야 한다.

### 6.5 Leakage / contamination gate

- Graph facts에는 final answer percentage를 직접 넣지 않는다.
- `gold_context`는 운영 method가 아니라 retrieval upper-bound로만 둔다.
- evaluation prompt에는 source facts와 text evidence의 역할을 분리해 명시한다.
- evaluator는 retrieval failure와 reasoning failure를 분리해서 기록한다.

## 7. Round 03 Orchestration 계획

### 7.1 Baseline lock

Round 03 시작 전 다음 상태를 고정한다.

| 항목 | 고정값 |
|---|---|
| KG batch | `kg-full-provenance-20260524` |
| Curation round | `02` |
| Selected 7 coverage | `7 / 7` |
| Missing required facts | `0` |
| Primary method | `hybrid_vector_graph` |
| Main replay subset | selected 7 |
| Integration demo subset | CRWD 4 |

### 7.2 Prompt hardening

Round 02에서 남은 failure가 model reasoning / formatting 문제였으므로 Round 03 prompt는 다음을 강제해야 한다.

- graph facts를 authoritative source로 사용
- text context는 supporting evidence로 사용
- formula 명시
- numerator / denominator 대입
- unit 명시
- rounding rule 명시
- final answer format 고정
- trend statement 누락 방지

### 7.3 Failure replay

Round 02에서 실패 또는 부분 실패가 있던 row를 우선 replay한다.

- `e7129c27`: hybrid가 항상 best가 아님을 보여준 case
- `8f7b5b57`: vector_only가 숫자 선택에서 약했던 case
- `379644c5`: workforce graph facts가 강하게 작동한 case
- `e6b63fd8`: hybrid만 완전 성공한 case
- `b7b8f21b`, `0dc1584f`: graph_facts_only가 trend/margin narrative에서 약했던 case

### 7.4 Evaluator sanity check

Round 03에서는 evaluator도 같이 점검해야 한다.

- `gold_context`가 항상 최고가 아닌 이유를 retrieval upper-bound와 generation quality로 분리한다.
- numeric correctness와 answer correctness의 차이를 명확히 한다.
- qualitative CRWD demo에 selected 7용 quantitative scoring을 그대로 적용하지 않는다.
- CRWD demo는 answer correctness보다 evidence coverage, graph path validity, cross-category connection quality를 중점 평가한다.

### 7.5 CRWD cross-context integration demo

CRWD demo의 목표는 “graph가 여러 문맥을 한 회사 중심으로 연결한다”는 것을 보여주는 것이다.

권장 graph narrative는 다음과 같다.

```text
CrowdStrike / CRWD
├─ Financials
│  └─ FY2022-2024 total revenue growth
├─ Company overview
│  ├─ workforce headcount
│  ├─ labor union / collective bargaining / work stoppage risk
│  └─ customer support / incident response / proactive services
└─ Footnotes
   └─ subscription vs professional services revenue growth contribution
```

이 demo에서는 vector-only가 개별 문서를 잘 찾더라도, graph가 다음을 더 명확히 보여줄 수 있다.

- 같은 회사/티커에 속한 서로 다른 category의 evidence 연결
- revenue growth와 서비스 구성 요소의 관계
- workforce/labor risk와 company overview의 qualitative context
- Financials와 Footnotes 사이의 segment revenue 연결

## 8. Round 03 산출물 제안

Round 03 orchestration 이후에는 다음 산출물을 남기는 것이 좋다.

```text
outputs/kg_build/eval_round03/
- round03_pre_execution_case_quality_report.md
- round03_case_registry.csv
- round03_case_quality_results.csv
- round03_prompt_config.md
- round03_orchestration_config.yaml
- selected7_failure_replay_results.csv
- selected7_failure_replay_traces.jsonl
- crwd_integration_demo_report.md
- crwd_graph_paths.jsonl
- crwd_context_merge_traces.jsonl
- round03_eval_report.md
```

## 9. Orchestrator에 같이 보내야 할 자료

Round 00-02 보고서만 보내면 큰 흐름은 이해할 수 있지만, Round 03 orchestration을 정확히 하려면 다음이 추가로 필요하다.

### 9.1 필수

- `selected7_eval_results.csv`
- `selected7_eval_traces.jsonl`
- `method_comparison_summary.csv`
- `case_coverage_report_after_round02.csv/md`
- 현재 prompts: `vector_only`, `graph_facts_only`, `hybrid_vector_graph`, `gold_context`
- evaluator/scoring config
- graph retrieval / Cypher config
- vector retrieval config

### 9.2 Pre-Round 03 case 관련

- `FINDER_selection_indexing_report.md`
- `finder_selected_cases.csv`
- `finder_dataset_indexing_summary.csv`
- `finder_selected_indexing_manifest.json`
- `finder_selected_cases_for_seocho.json`
- case quality inspection script / 결과 CSV
- CRWD 4 case expected answer / evidence / graph path 후보

### 9.3 재현성

- git branch name
- git commit hash
- branch diff summary
- execution command
- `.env` key 이름 목록만 포함한 config template
- Neo4j schema/index/constraints 요약

## 10. 최종 판단

Round 03 report에는 “case를 만들고 case 품질을 검사하던 pre-execution 단계”가 포함되어야 한다. 이전 Round 00-02 중심 요약만으로는 이 부분이 충분히 반영되지 않았다.

따라서 Round 03의 현재 상태는 다음 문장으로 정리할 수 있다.

> Round 03는 아직 본 evaluation을 실행한 단계가 아니라, Round 02 결과를 baseline으로 고정한 뒤 selected 7 replay와 CRWD cross-context integration demo를 위해 case registry와 case quality gate를 준비하는 단계다. selected 7은 method comparison의 baseline이고, CRWD 4개는 데이터 통합 demo용 subset이며, Round 03 orchestration은 이 둘을 분리해서 실행해야 한다.

