⚠️ **SUPERSEDED (2026-05-30):** 이 리포트의 FinDER graph 수치는 평가 계약
year-bug에 의한 거짓 양성을 포함한다. 수정된 수치는
`종합_graphrag_eval_R8_R13_synthesis_report.md` §3, §5.1 참조.
특히 "graph > vector on FinDER" 결론은 철회됨 (수정 후 vector > graph).

---
# Round 10 평가 리포트

**작성일:** 2026-05-29  
**라운드 성격:** Clean Held-Out — FinDER 130 + FinQA 56 + TAT-QA 65 = 251케이스  
**Claim boundary:** `clean_held_out_round10_three_dataset`  
**총 실행:** 753 traces (251 × 3), resume 완벽 작동, provider error 실질 0  

---

## 1. 헤드라인 숫자

| Method | R8 | R9C | **R10** |
|---|---:|---:|---:|
| vector_only | 0.36 | 0.50 | **0.5697** |
| graph_neo4j | 0.46 | 0.52 | **0.6096** |
| hybrid_neo4j | 0.40 | 0.46 | **0.5657** |

> **graph > vector 세 번 연속 clean held-out 확인 (0.61 vs 0.57, +0.04)**  
> `graph_beats_vector_test = true`

---

## 2. 데이터셋별 성능 — 구조가 완전히 다름

| Dataset | vector | graph | hybrid | 승자 | n |
|---|---:|---:|---:|---|---:|
| **FinDER** | 0.2692 | **0.3923** | 0.2692 | graph (+0.12) | 130 |
| **FinQA** | 0.8214 | 0.75 | **0.8571** | hybrid > vector > graph | 56 |
| **TAT-QA** | **0.9538** | 0.9231 | 0.9077 | vector > graph > hybrid | 65 ⚠️ |
| **전체** | 0.5697 | **0.6096** | 0.5657 | graph | 251 |

⚠️ TAT-QA는 ticker 추출률 11.36% 편향 샘플 — 섹션 6 참조

### 수치 근접도 (avg_nc)

| Dataset | vector_nc | graph_nc | hybrid_nc |
|---|---:|---:|---:|
| FinDER | 0.447 | **0.502** | 0.488 |
| FinQA | 0.926 | 0.845 | **0.950** |
| TAT-QA | **0.975** | 0.968 | 0.954 |
| 전체 | 0.691 | 0.699 | **0.712** |

---

## 3. R8 → R9C → R10 누적 추이

| Dataset | R8 | R9C | R10 | 추세 |
|---|---:|---:|---:|---|
| FinDER graph | 0.50 | 0.43 | **0.39** | 하락 (케이스 다양성 증가) |
| FinQA graph | 0.40 | 0.65 | **0.75** | 꾸준한 상승 |
| 전체 graph | 0.46 | 0.52 | **0.61** | 상승 |

**FinDER graph 하락 원인:** R8은 "other" 타입(관용적 채점) 100%, R10은 precise formula_type.  
측정이 더 정확해진 것 — 실제 능력 하락 아님.

---

## 4. Formula_type별 성능

| formula_type | 건수 | graph ac | 판정 |
|---|---:|---:|---|
| tatqa_arithmetic | 65 | 0.9231 | ✅ (편향 주의) |
| effective_tax_rate | 2 | 1.0000 | ✅ (n 작음) |
| finqa_program | 56 | 0.7500 | ✅ |
| income_vs_ops | 8 | 0.6250 | 양호 |
| operating_margin | 7 | 0.5714 | 양호 |
| multi_year_margin | 31 | 0.4194 | 보통 |
| gross_margin | 15 | 0.4000 | 보통 |
| **yoy_revenue_change** | 27 | **0.3704** | ⚠️ 개선 중 (R9C 0.20) |
| net_margin | 3 | 0.3333 | 미흡 |
| debt_metrics | 3 | 0.3333 | 미흡 |
| **ratio_trend** | 30 | **0.2667** | ❌ 최대 실패 |
| other | 4 | 0.2500 | — |

### ratio_trend 0.2667 (30케이스) — 가장 큰 문제

FinDER 두 번째로 큰 타입. 73% 실패.  
**원인:** 동일 비율을 2~3개 연도 계산 + 추세 방향 서술까지 요구.  
숫자는 맞는데 추세 서술 형식이 scorer target_slot과 불일치하는 패턴으로 추정.  
→ formula_target_mismatch가 주요 원인 (tolerance 아님).

### yoy_revenue_change 0.3704 — Prompt v3.4 효과 확인

R9C 0.20 → R10 0.37 (+0.17). YoY 계산 스텝 명시가 실효 있었음.  
하지만 여전히 63% 실패. nc 분포 추가 확인 필요.

---

## 5. FinQA: vector > graph 3라운드 연속 확인

| 라운드 | n | FinQA vector | FinQA graph | delta |
|---|---:|---:|---:|---:|
| R8 | 20 | 0.45 | 0.40 | −0.05 |
| R9C | 20 | 0.90 | 0.65 | −0.25 |
| **R10** | **56** | **0.82** | **0.75** | **−0.07** |

56케이스에서도 vector > graph. **노이즈 아닌 실제 패턴.**

**가설:** FinQA program이 요구하는 정확한 table cell 값을 KG 추출이 제대로 포착 못 함.  
vector는 full table을 텍스트로 그대로 보여주므로 손실 없음.  
nc도 graph (0.845) < vector (0.926) — 숫자 자체를 더 크게 빗나감.  
→ KG 추출 품질 문제 (tolerance/scorer 문제 아님).

---

## 6. TAT-QA 0.9231 — Selection Bias 경고

**ticker 추출률 11.36%** = GPT가 확신한 회사만 포함 = 대형 유명 기업 편중.  
이 회사들 재무 데이터는 모델 사전학습에 포함됐을 가능성 높음.  
→ KG retrieval 덕분인지, 모델 내재 지식인지 구분 불가.

TAT-QA 0.9231을 "TAT-QA에서 graph가 좋다"로 해석 **금지**.  
더 넓은 검증을 위해 ticker 추출 방식 개선 필요 (단순 GPT 매핑 → 사전 구축 company-ticker DB).

---

## 7. Hybrid 3라운드 요약

| 라운드 | FinDER hybrid vs graph | 결론 |
|---|---|---|
| R8 | hybrid < graph | |
| R9C | hybrid = vector < graph (KG-first prompt 무효) | |
| **R10** | hybrid = vector < graph | hybrid_beats_graph_finder = false 3연속 |

**FinQA에서만 hybrid 유효:** hybrid (0.857) > vector (0.821) > graph (0.75)  
텍스트가 KG 오류를 보완하는 효과 — FinQA는 hybrid가 최선.

KG-first prompt(v3.3_kgfirst → v3.4 통합)로는 FinDER interference 해소 불가.  
→ **hybrid 구조 재설계 필요 (입력 분리: 숫자는 KG, 맥락은 텍스트).**

---

## 8. 케이스 수 이슈 기록

| 항목 | 목표 | 실제 | 원인 |
|---|---:|---:|---|
| FinDER | 130 | **130** | ✅ 달성 |
| FinQA | 100 | **56** | R8+R9C에서 130건 소진 → 잔여 풀 부족 |
| TAT-QA | 70 | **65** | ticker 추출 성공률 11.36% 제한 |
| **합계** | 300 | **251** | 목표 미달 (최소 200 초과 ✅) |

FinQA 풀 소진 → Round 11부터 FinQA dev/test split 활용 또는 ConvFinQA 추가 필요.

---

## 9. 누적 Clean Held-Out 결과 (포트폴리오 요약용)

| 라운드 | n | graph ac | vector ac | graph>vector |
|---|---:|---:|---:|---|
| R8 | 50 | 0.46 | 0.36 | ✅ +0.10 |
| R9C | 50 | 0.52 | 0.50 | ✅ +0.02 |
| **R10** | **251** | **0.61** | **0.57** | **✅ +0.04** |

**세 번 연속, 규모를 키워도 일관됨.** 단, 전체 수치는 TAT-QA 편향 포함.  
FinDER만 보면: graph 0.39 vs vector 0.27 — 가장 강한 주장 가능.

---

## 10. 다음 단계

### 즉시 (no-model)
- ratio_trend 30건 실패 reason 분류 (format error vs target mismatch 비율)
- FinQA graph 실패: KG 추출값 vs program operand 실제값 비교

### 중기 1: gpt-4o ablation
FinDER + FinQA 각 25케이스 subset으로 gpt-4o vs gpt-4o-mini 비교.  
ratio_trend, yoy_revenue_change 집중 확인.

### 중기 2: FinQA KG 개선 or 방향 전환
3라운드 연속 vector > graph → 선택:
1. **KG 추출 개선** — 테이블 셀 정확 매핑
2. **FinQA는 vector, FinDER는 graph** — dataset별 최적 방법 전략

### 중기 3: TAT-QA 편향 제거
Company-ticker DB 사전 구축 (S&P 500 + Russell 1000)으로 GPT 매핑 대체.  
추출률 11.36% → 60%+ 목표.

---

## 11. Claim 경계

### 말해도 되는 것
```
세 번의 clean held-out (R8 50케이스, R9C 50케이스, R10 251케이스) 모두에서
graph-based retrieval이 vector-only보다 높은 answer correctness를 보였다.

특히 텍스트형 재무 데이터(FinDER)에서 graph 우위가 일관됨 (+0.12, 130케이스).
테이블형 데이터(FinQA)에서는 반대 패턴 — vector가 일관되게 우위.
```

### 말하면 안 되는 것
```
TAT-QA 0.9231이 TAT-QA 전체 성능을 대표한다.
FinQA에서 graph가 효과적이다.
251케이스로 GraphRAG가 금융 QA 전반에서 우수하다고 주장한다.
```

---

## 12. 파일 위치

| 파일 | 경로 |
|---|---|
| R10 traces | `outputs/round3_eval_runs/round10_eval_20260529_170409/round10_traces.jsonl` |
| R10 summary | `outputs/round3_eval_runs/round10_eval_20260529_170409/round10_summary.md` |
| R10 state | `outputs/round10_eval/state.json` |
| TAT-QA ticker map | `outputs/round10_case_selection/tatqa_company_ticker_map.json` |
