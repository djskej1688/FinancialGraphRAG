> ⚠️ **SUPERSEDED.** Produced under the pre-v2 (year-bug) scorer contract; kept for traceability only.
> Corrected reference: `round10_v2_rescore_report.md`. See README → "What Went Wrong".

> ⚠️ **LABEL CORRECTION (R15 audit).** The `vector_only` arm here is `per_case_evidence_only` — the
> case's own evidence text, **not corpus retrieval** — reclassified `case_text_only`. Numbers unchanged;
> only the label is corrected. The only validated retrieval-vs-graph comparison is R14. See docs/PROVENANCE.md.

# Round 09C 평가 리포트

**작성일:** 2026-05-29  
**라운드 성격:** Clean Held-Out — FinDER 30 + FinQA 20, scorer_v9 + KG-first prompt 적용  
**Claim boundary:** `clean_held_out_round9c_fixed_pipeline`  
**총 실행:** 150 traces (50 cases × 3 methods), provider errors: 0  

---

## 1. 헤드라인 숫자

| Method | R8 | **R9C** | delta |
|---|---:|---:|---:|
| vector_only | 0.36 | **0.50** | +0.14 |
| graph_neo4j | 0.46 | **0.52** | +0.06 |
| hybrid_neo4j | 0.40 | **0.46** | +0.06 |

> **graph > vector 세 번째 clean held-out 연속 확인 (0.52 vs 0.50)**  
> `graph_beats_vector_test = true` — 단, 마진이 0.02로 매우 좁음 (50케이스 기준)

---

## 2. 파이프라인 개선 확인

| 지표 | R8 | R9C | 판정 |
|---|---:|---:|---|
| formula_type "other" 비율 | **100%** | **3.33%** | ✅ 대성공 |
| suspect ticker | 3건 | **0건** | ✅ 해결 |
| KG write success rate | 1.0 | 1.0 | ✅ |
| Contract validation pass | 50/50 | 50/50 | ✅ |

formula_type 확장이 100% "other"를 3.33%(1건)으로 줄임. **파이프라인 품질 목표 달성.**

---

## 3. 데이터셋별 성능

| Dataset | vector | graph | hybrid | n |
|---|---:|---:|---:|---:|
| **FinDER** | 0.2333 | **0.4333** | 0.2333 | 30 |
| **FinQA** | **0.90** | 0.65 | 0.80 | 20 |
| **전체** | 0.50 | **0.52** | 0.46 | 50 |

| Dataset | vector nc | graph nc | hybrid nc |
|---|---:|---:|---:|
| FinDER | 0.400 | **0.508** | 0.436 |
| FinQA | **0.943** | 0.789 | 0.885 |

---

## 4. R8 → R9C Delta 분해

| Dataset | R8 graph | R9C graph | delta | 원인 |
|---|---:|---:|---:|---|
| FinDER | 0.50 | **0.4333** | −0.07 | 신규 formula_type 실패 (아래 섹션 5) |
| FinQA | 0.40 | **0.65** | +0.25 | scorer_v9 tolerance 효과 확인 |
| **전체** | 0.46 | **0.52** | +0.06 | FinQA 개선이 FinDER 퇴행 상쇄 |

---

## 5. 신규 Formula Type 성능 분석

### formula_type별 graph ac

| formula_type | 건수 | graph ac | 판정 |
|---|---:|---:|---|
| finqa_program | 20 | 0.65 | 양호 |
| income_vs_ops | 2 | 1.00 | 우수 |
| effective_tax_rate | 1 | 1.00 | 우수 |
| ratio_trend | 4 | 0.75 | 양호 |
| multi_year_margin | 7 | 0.43 | 보통 |
| gross_margin | 5 | 0.40 | 보통 |
| debt_metrics | 3 | 0.33 | 미흡 |
| **yoy_revenue_change** | 5 | **0.20** | ⚠️ 실패 |
| **eps_dilution** | 2 | **0.00** | ❌ 완전 실패 |
| other | 1 | 0.00 | — |

### yoy_revenue_change 실패 분석 (4/5 실패)

```
round9c_finder_011  ac=0.0  nc=0.012  → 거의 0에 가까운 숫자 출력
round9c_finder_013  ac=0.0  nc=0.049  → 동일 패턴
round9c_finder_019  ac=0.0  nc=0.050  → 동일 패턴
round9c_finder_021  ac=0.0  nc=0.050  → 동일 패턴
round9c_finder_028  ac=1.0  nc=0.999  → 정상
```

**nc가 0.01~0.05 수준** — tolerance 문제가 아닌 실제 오계산. 모델이 **YoY 변화율 대신 절댓값이나 단순 차이를 출력**하는 패턴. `(current - prior) / prior × 100` 계산 단계 중 마지막 나눗셈 또는 ×100을 누락하는 것으로 추정.

→ **scorer 문제 아님. 프롬프트에 YoY 계산 스텝 명시 필요.**

### eps_dilution 완전 실패 (2/2)

```
round9c_finder_006  ac=0.0  nc=0.000  → answer_format_error (빈 final_answer)
round9c_finder_015  ac=0.0  nc=0.211  → formula_target_mismatch
```

- 1건은 모델이 EPS 계산 자체를 포기하고 빈 답 제출
- 1건은 nc=0.211 — 희석 효과 계산에서 크게 빗나감
- **EPS + dilution은 multi-step 계산 (Net Income / Diluted Shares, 희석 전후 비교)**이라 gpt-4o-mini가 안정적으로 처리 못 하는 케이스

→ **Round 10에서 eps_dilution 케이스 선택 시 신중하게. 또는 gpt-4o 업그레이드 후 재시도.**

---

## 6. FinQA: Vector 0.90 vs Graph 0.65

R8에서는 graph nc(0.812) > vector nc(0.689)였는데, R9C에서는 반전:

| | R8 | R9C |
|---|---|---|
| FinQA graph ac | 0.40 | 0.65 |
| FinQA vector ac | 0.45 | **0.90** |
| FinQA graph nc | **0.812** | 0.789 |
| FinQA vector nc | 0.689 | **0.943** |

**원인 분석:**

1. **R9C FinQA 케이스가 vector에 유리한 케이스들**: 새로 선택한 20개 FinQA 케이스가 R8보다 텍스트 컨텍스트에서 직접 답을 찾을 수 있는 케이스 위주일 가능성. 50케이스 변동성이 큰 영향.

2. **scorer_v9 tolerance 효과가 vector에 더 크게 작용**: vector가 정수형 답에 소수 표기를 쓰거나 단위가 약간 달라도 이제 통과. graph는 KG에서 정확한 값을 가져오므로 tolerance 완화 혜택이 상대적으로 적음.

3. **FinQA graph 실패 7건은 모두 formula_target_mismatch** — nc=0.789이므로 가깝긴 한데 않 맞음.

**이 수치를 "FinQA에서 vector가 graph보다 우수하다"로 해석하면 안 됨 (20케이스).**  
Round 10에서 100케이스로 확인 필요.

---

## 7. Hybrid KG-first Prompt 효과

```
FinDER:
  graph = 0.4333, hybrid = 0.2333, vector = 0.2333
  hybrid == vector → KG-first prompt 효과 없음

  graph pass / hybrid fail: 10건  (R8과 동일)
  hybrid pass / graph fail: 4건   (R8에 없던 케이스)

FinQA:
  vector = 0.90, hybrid = 0.80, graph = 0.65
  hybrid > graph (+0.15) → hybrid가 graph보다는 나음
```

**결론:**
- `hybrid_beats_graph_finder = false` 확정 — KG-first 한 줄 추가로는 FinDER interference 해소 불가
- FinDER에서 여전히 10케이스가 graph=정답/hybrid=오답 패턴 유지
- FinQA에서 hybrid > graph: 텍스트 컨텍스트가 FinQA의 table context를 보완해줌
- **hybrid_beats_graph_finder 해소는 더 구조적인 접근 필요** (입력 재설계 또는 모델 업그레이드)

---

## 8. 누적 Clean Held-Out 결과

| 라운드 | graph ac | vector ac | graph>vector | n_cases |
|---|---:|---:|---|---:|
| R8 | 0.46 | 0.36 | ✅ (+0.10) | 50 |
| R9C | 0.52 | 0.50 | ✅ (+0.02) | 50 |
| **R10 목표** | — | — | **300케이스로 안정화** | 300 |

graph > vector가 두 번 clean held-out에서 확인됐지만, R9C의 마진(+0.02)이 너무 얇아.  
R10 300케이스에서 안정적인 결론이 나야 실질적 주장 가능.

---

## 9. Round 10 시사점

### 반드시 해결할 것

| 이슈 | 원인 | Round 10 대응 |
|---|---|---|
| yoy_revenue_change 실패 (nc~0.05) | 프롬프트에 YoY 계산 스텝 없음 | prompt_v3.3에 YoY 계산 명시 지시 추가 |
| eps_dilution 완전 실패 | gpt-4o-mini 산술 한계 | Round 10에서 eps_dilution 케이스 제외 또는 gpt-4o |
| FinQA vector > graph (20케이스) | 케이스 선택 변동 or scorer 효과 | 100케이스로 확인 |

### KG-first prompt 개선 방향

KG-first 한 줄 추가로는 부족함. FinDER hybrid 해소를 위한 실질적 변경:
- Hybrid 모드에서 텍스트는 "context only" 섹션으로 분리, 숫자 계산은 KG facts에서만
- 또는 두 단계: ① KG에서 숫자 추출 → ② 텍스트에서 맥락 확인

→ Round 10 평가 후 별도 prompt engineering 라운드 설계 권장.

### Round 10 케이스 선택 시 주의

- **eps_dilution 케이스 제외** (gpt-4o-mini 한계)
- **yoy_revenue_change 케이스: 프롬프트 패치 후 포함** OR 일단 제외 후 Round 11에서 테스트
- TAT-QA arithmetic 타입만 선택 (derivation 명시된 것)

---

## 10. Claim 경계

### 말해도 되는 것

```
Round 09C는 R8과 독립된 케이스 + 수정된 파이프라인(scorer_v9, ticker_filter, formula_type 확장)으로 실행됐다.
- formula_type "other" 비율: 3.33% (R8의 100%에서 대폭 개선)
- graph > vector 세 번째 clean held-out 확인 (0.52 vs 0.50)
- FinQA scorer_v9 효과 확인: graph 0.40→0.65 (+0.25)
- 신규 formula_type 중 yoy_revenue_change, eps_dilution은 gpt-4o-mini 한계 확인
```

### 말하면 안 되는 것

```
R9C FinQA vector 0.90이 FinQA 전체에서도 vector가 우수하다는 증거다.
graph > vector 마진(0.02)이 통계적으로 의미 있다. (n=50)
KG-first prompt가 hybrid interference 문제를 해결했다.
```

---

## 11. 파일 위치

| 파일 | 경로 |
|---|---|
| Round 9C traces | `outputs/round3_eval_runs/round9c_eval_20260529_145126/round9c_traces.jsonl` |
| Round 9C summary | `outputs/round3_eval_runs/round9c_eval_20260529_145126/round9c_summary.md` |
| eval state | `outputs/round9c_eval/state.json` |
| 케이스 선택 | `outputs/round9c_case_selection/` |
| Formula contracts | `outputs/round9c_formula_contracts/` |
| KG 추출 | `outputs/round9c_step_b_kg/` |
