> ⚠️ **SUPERSEDED.** Produced under the pre-v2 (year-bug) scorer contract; kept for traceability only.
> Corrected reference: `round10_v2_rescore_report.md`. See README → "What Went Wrong".

> ⚠️ **LABEL CORRECTION (R15 audit).** The `vector_only` arm here is `per_case_evidence_only` — the
> case's own evidence text, **not corpus retrieval** — reclassified `case_text_only`. Numbers unchanged;
> only the label is corrected. The only validated retrieval-vs-graph comparison is R14. See docs/PROVENANCE.md.

# Round 10 평가 리포트

**작성일:** 2026-05-29  
**라운드 성격:** Clean Held-Out — FinDER 130 + FinQA 56 + TAT-QA 65 = 251케이스, 3개 데이터셋  
**Claim boundary:** `clean_held_out_round10_three_dataset`  
**총 실행:** 753 traces (251 × 3), provider errors: 1 (tatqa_009/graph, ac=0 처리)  
**무결성:** 중복 0, ac=None 0 — WiFi 중단/resume 데이터 오염 없음 ✅  

---

## 1. 헤드라인 숫자

| Method | R8 | R9C | **R10** |
|---|---:|---:|---:|
| vector_only | 0.36 | 0.50 | **0.5697** |
| graph_neo4j | 0.46 | 0.52 | **0.6096** |
| hybrid_neo4j | 0.40 | 0.46 | **0.5657** |

> **graph > vector 세 번의 clean held-out 모두 확인 (R8, R9C, R10)**  
> `graph_beats_vector_test = true` (0.6096 vs 0.5697, +0.04)  
> 250케이스 이상에서 확인된 첫 번째 결과

---

## 2. 데이터셋별 성능

| Dataset | vector | graph | hybrid | n |
|---|---:|---:|---:|---:|
| **FinDER** | 0.2692 | **0.3923** | 0.2692 | 130 |
| **FinQA** | 0.8214 | 0.7500 | **0.8571** | 56 |
| **TAT-QA** | **0.9538** | 0.9231 | 0.9077 | 65 |
| **전체** | 0.5697 | **0.6096** | 0.5657 | 251 |

| Dataset | vector nc | graph nc | hybrid nc |
|---|---:|---:|---:|
| FinDER | 0.447 | **0.502** | 0.488 |
| FinQA | **0.926** | 0.845 | 0.950 |
| TAT-QA | **0.975** | 0.968 | 0.954 |

---

## 3. Clean Held-Out 3라운드 추이 (graph)

| Dataset | R8 (50) | R9C (50) | R10 (251) | 해석 |
|---|---:|---:|---:|---|
| FinDER | 0.50 | 0.4333 | **0.3923** | 더 다양한 formula_type → 실제 난이도 노출 |
| FinQA | 0.40 | 0.65 | **0.75** | 꾸준히 개선 (scorer, 더 많은 케이스) |
| TAT-QA | — | — | **0.9231** | R10 첫 측정 (편향 주의, 아래 참조) |
| **전체** | 0.46 | 0.52 | **0.6096** | 케이스 수 증가 따라 안정화 중 |

---

## 4. 주요 발견 5가지

### 발견 1. Prompt v3.4 YoY 패치 효과 확인

| | R9C | R10 | delta |
|---|---:|---:|---:|
| yoy_revenue_change graph ac | 0.20 | **0.37** | +0.17 |
| yoy_revenue_change graph nc | ~0.05 | **0.527** | +0.477 |

R9C에서 nc~0.05로 거의 0에 가깝던 yoy 케이스들이 R10에서 10/27 통과.  
`(current - prior) / prior × 100` 스텝 명시 지시가 실질적으로 작동함.  
**→ Prompt v3.4 YoY 패치 효과 확정.**

### 발견 2. ratio_trend — FinDER 최대 병목

```
formula_type  |  count  |  graph ac  |  graph nc
ratio_trend   |    30   |   0.267    |   0.323
```

30케이스(FinDER 23%)에서 ac=0.267, nc=0.323.  
nc가 낮아 **tolerance 문제가 아닌 실제 오계산**.  

실패 원인: formula_target_mismatch 15건 + answer_format_error 7건  

ratio_trend 예시: "operating leverage ratio", "debt coverage trend", "return on equity 3-year trend" 등 — 동일 비율을 3개 연도에 걸쳐 계산하고 추세를 분석하는 복합 케이스. gpt-4o-mini가 multi-step + multi-year를 동시에 처리하는 데 한계.

**→ Round 11에서 ratio_trend 전용 프롬프트 지시 추가 필요.**

### 발견 3. FinQA: vector > graph 패턴 56케이스에서 재확인

| | R8 (20) | R9C (20) | R10 (56) |
|---|---:|---:|---:|
| FinQA graph ac | 0.40 | 0.65 | 0.75 |
| FinQA vector ac | 0.45 | 0.90 | 0.82 |
| FinQA vector > graph? | ✓ | ✓ | ✓ |

세 라운드 모두 FinQA에서 vector ≥ graph.  
nc도 vector(0.926) > graph(0.845) → 모델이 숫자 자체를 더 정확하게 읽음.

**해석:** FinQA의 structured table format에서는 evidence_text 안에 답이 직접 있음.  
KG 추출을 거치면 오히려 정보가 손실될 수 있음.  
→ **"KG가 텍스트에 이미 있는 구조 정보를 해치는" 케이스** — FinDER(텍스트 분산)과 반대 패턴.

**→ 데이터셋 유형별로 최적 방법이 다름:**
- 텍스트 기반 비정형 QA (FinDER) → graph 유리
- 구조화 테이블 QA (FinQA) → vector/hybrid 유리
- 명시적 arithmetic QA (TAT-QA) → 방법 무관하게 높음

### 발견 4. Hybrid interference 최악 — FinDER에서 29케이스

```
R8:   graph pass / hybrid fail = 10건
R9C:  graph pass / hybrid fail = 10건
R10:  graph pass / hybrid fail = 29건  ← 대폭 증가
      hybrid pass / graph fail = 13건
      순 손실: -16케이스
```

케이스 수가 늘어나면서 interference 절대 건수도 증가.  
KG-first prompt(v3.3_kgfirst → v3.4에 통합)는 여전히 29건의 텍스트-KG 충돌을 막지 못함.

**→ 프롬프트 수정만으로는 한계. 입력 구조 자체 변경 필요 (Round 11 핵심 과제).**

### 발견 5. TAT-QA 0.92 — 편향 주의

TAT-QA graph ac=0.9231은 매우 높지만:
- ticker 추출 성공률: **11.36%** (3,020 질문 중 136개 회사만 ticker 확인)
- 즉, ticker가 명확하게 식별되는 대형 기업 + 단순한 케이스만 선택됨
- `tatqa_unknown_rate = 0.8864` — 88.6%가 UNKNOWN으로 걸러짐

**TAT-QA 0.92를 "TAT-QA 전반에서 그래프가 좋다"로 해석하면 안 됨.**  
선택된 65케이스 자체가 TAT-QA에서 쉬운 subset.

---

## 5. Formula Type 전체 성능

| formula_type | 건수 | graph ac | 평가 |
|---|---:|---:|---|
| tatqa_arithmetic | 65 | 0.9231 | ✅ 우수 |
| effective_tax_rate | 2 | 1.0000 | ✅ 우수 |
| finqa_program | 56 | 0.7500 | ✅ 양호 |
| income_vs_ops | 8 | 0.6250 | 양호 |
| operating_margin | 7 | 0.5714 | 양호 |
| multi_year_margin | 31 | 0.4194 | 보통 |
| gross_margin | 15 | 0.4000 | 보통 |
| yoy_revenue_change | 27 | 0.3704 | 개선 중 (R9C 0.20→) |
| debt_metrics | 3 | 0.3333 | 미흡 |
| net_margin | 3 | 0.3333 | 미흡 |
| **ratio_trend** | **30** | **0.2667** | ❌ 최대 병목 |
| other | 4 | 0.2500 | — |

FinDER의 57/130 케이스(44%)가 ratio_trend + yoy의 두 타입 → FinDER 성능의 핵심 변수.

---

## 6. 누적 Clean Held-Out 요약

| 라운드 | n | graph | vector | graph>vector | 특이사항 |
|---|---:|---:|---:|---|---|
| R8 | 50 | 0.46 | 0.36 | +0.10 | 첫 clean held-out |
| R9C | 50 | 0.52 | 0.50 | +0.02 | 파이프라인 수정 |
| **R10** | **251** | **0.61** | **0.57** | **+0.04** | **3개 데이터셋, 가장 신뢰도 높음** |

**graph > vector가 50→251 케이스에서 일관되게 유지됨.**  
마진이 R8(+0.10) → R9C(+0.02) → R10(+0.04)로 등락하지만 방향은 일정.

---

## 7. 다음 단계 (Round 11 후보)

### 즉시 효과 예상 (저비용)

| 작업 | 기대 효과 | 근거 |
|---|---|---|
| ratio_trend 프롬프트 패치 | FinDER +0.03~0.05 | 30케이스 × ac 0.267 → 개선 여지 큼 |
| FinQA answer_format_error 수정 | FinQA graph +0.05 | 6/14 실패가 format 문제 |
| Naive baseline 추가 (포트폴리오) | 비교 근거 생성 | gpt-4o-mini 전체 텍스트 vs graph |

### 구조적 과제

| 작업 | 내용 |
|---|---|
| **Hybrid 입력 구조 재설계** | 텍스트-KG 충돌 29건 해소 → FinDER hybrid ≥ graph 목표 |
| **FinQA KG 추출 방식 재검토** | 왜 vector가 graph보다 nc도 높은지 — 테이블 linearization이 충분한가? |
| **TAT-QA ticker 추출 개선** | 11% → 50%+ 목표 → 대표성 있는 TAT-QA 결과 |
| **gpt-4o ablation** | 포트폴리오용 model upgrade 효과 측정 |

---

## 8. Claim 경계

### 말해도 되는 것

```
Round 10은 FinDER/FinQA/TAT-QA 세 데이터셋 251케이스에서
graph-based retrieval이 vector-only보다 높은 answer correctness를 보인
세 번째 연속 clean held-out 결과다 (0.61 vs 0.57).

데이터셋별 패턴:
- FinDER(텍스트 기반): graph 명확 우위 (0.39 vs 0.27)
- FinQA(구조화 테이블): vector ≥ graph — 세 라운드 일관 패턴
- TAT-QA: 모든 방법 0.90+ (편향 선택 주의)

Prompt v3.4 YoY 패치 효과 확인: 0.20 → 0.37 (+0.17)
```

### 말하면 안 되는 것

```
TAT-QA 0.92가 TAT-QA 전반을 대표한다. (ticker 추출 11% 선택 편향)
FinQA에서 graph가 vector보다 좋다. (세 라운드 모두 vector ≥ graph)
graph > vector 마진 +0.04가 통계적으로 강하게 유의미하다. (251케이스)
```

---

## 9. 파일 위치

| 파일 | 경로 |
|---|---|
| Round 10 traces | `outputs/round3_eval_runs/round10_eval_20260529_170409/round10_traces.jsonl` |
| Round 10 summary | `outputs/round3_eval_runs/round10_eval_20260529_170409/round10_summary.md` |
| eval state | `outputs/round10_eval/state.json` |
| KG rollback | `outputs/round10_step_b_kg/round10_kg_rollback.cypher` |
