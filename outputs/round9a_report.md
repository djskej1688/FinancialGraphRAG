# Round 09A 평가 리포트

**작성일:** 2026-05-29  
**라운드 성격:** No-Model Sensitivity Analysis — R8 traces 재채점 (모델 호출 없음)  
**Claim boundary:** `r8_scorer_sensitivity_no_model_rerun`  
**model_calls:** 0  

---

## 1. 헤드라인: 4개 Variant 결과

| Variant | graph ac | vector ac | hybrid ac | 변화 (graph) |
|---|---:|---:|---:|---:|
| **R8_original** | **0.46** | 0.36 | 0.40 | baseline |
| V1: tol_2pct | 0.48 | 0.38 | 0.40 | +0.02 |
| V2: tol_2pct + unit_norm | 0.48 | 0.40 | 0.40 | +0.02 |
| V3: partial_credit_nc90 | 0.52 | 0.40 | 0.44 | +0.06 |
| V4: no_suspect_tickers | 0.4894 | 0.383 | 0.4255 | +0.029 (분모 축소) |

> **전반적 해석:** R8 점수는 scorer 설계에 적당히 민감하다. 단순 tolerance 완화(+0.02)는 소폭, partial credit 도입(+0.06)은 중간 수준. 어느 variant도 결과를 뒤집는 수준(+0.20 이상)은 아님.

---

## 2. 데이터셋별 세부 결과

| Variant | FinDER graph | FinQA graph | FinDER vector | FinQA vector |
|---|---:|---:|---:|---:|
| R8_original | 0.50 | 0.40 | 0.30 | 0.45 |
| V1: tol_2pct | **0.50** | **0.45** | 0.30 | 0.50 |
| V2: tol_2pct_unit | **0.50** | **0.45** | 0.30 | **0.55** |
| V3: partial_nc90 | **0.50** | **0.55** | 0.3167 | 0.525 |
| V4: no_suspect | **0.5556** | 0.40 | 0.3333 | 0.45 |

---

## 3. Variant별 해석

### V1: FinQA Tolerance 2% 완화

**FinQA graph: 0.40 → 0.45 (+0.05)**  
**FinQA vector: 0.45 → 0.50 (+0.05)**  
FinDER: 변화 없음 (FinQA 전용 변경)

**해석:** tolerance를 0.5%→2%로 완화했을 때 FinQA에서 각 method당 1개 케이스씩 추가 통과.  
이는 모델이 숫자를 거의 맞혔지만 좁은 tolerance 때문에 0점 처리된 케이스가 실제로 존재했음을 확인.  
**→ 09B scorer에 반영할 근거 있음.** 단, 효과가 크지 않으므로(+0.05) 이것만으로 성능 문제가 해결되지는 않음.

### V2: V1 + Ratio↔Percent Unit Normalization

**FinQA graph: V1과 동일 (0.45, delta=0)**  
**FinQA vector: 0.50 → 0.55 (+0.05 추가)**  

**중요 발견:**
- **graph**: unit normalization이 추가 기여 없음 → graph KG facts에서 가져온 값은 이미 단위가 일관됨
- **vector**: unit normalization이 1케이스 추가 통과 → vector 모드(텍스트에서 직접 읽기)에서 모델이 ratio로 계산한 것을 percent로 기대하는 경우 발생

**해석:** 단위 불일치는 vector-only 모드에서만 간헐적으로 발생.  
graph가 KG facts에서 단위를 명시적으로 가져오는 게 단위 일관성에 유리함을 보여주는 패턴.  
**→ unit normalization은 vector scorer에 우선 반영 권장. graph는 필요성 낮음.**

### V3: Partial Credit nc ≥ 0.90 → ac = 0.5

**전체 graph: 0.46 → 0.52 (+0.06)**  
**FinQA graph: 0.40 → 0.55 (+0.15)** ← 가장 큰 delta  
**FinDER graph: 변화 없음 (0.50)**  

**세부 해석:**

FinQA graph에서 partial credit 효과가 +0.15인 이유: R8 분석에서 이미 확인했듯, FinQA graph 실패 케이스의 다수(8/12)가 nc≥0.74 이상이었음. partial credit은 이 중 nc≥0.90인 케이스를 0→0.5로 올림.

FinDER graph에 변화 없는 이유: FinDER 실패 케이스들은 nc가 낮은 편(formula_target_mismatch에서 실질적 오류) → nc≥0.90 threshold를 넘지 못함.

**→ Partial credit 도입은 FinQA에 특히 유효. 단, scoring philosophy 변경이므로 claim 표현에 주의 필요.**  
"V3 ac=0.52"를 단순히 "성능 향상"으로 보고하면 안 됨. 채점 방식 자체가 바뀐 것.

### V4: Suspicious Ticker 3개 제거 (CAGR, OF, LOSS)

**FinDER graph: 0.50 → 0.5556 (+0.056)**  
FinQA: 변화 없음 (FinDER-only 변경)

**해석:** 제거된 3케이스  
(`round8_finder_006`, `round8_finder_016`, `round8_finder_028`)  
의 graph ac가 0/3이었던 것으로 추정 → 빼면 분모가 27로 줄어 평균이 오름.

**그러나 이는 성능 개선이 아니라 분모 축소 효과.**  
진짜 의미는: "ticker 오추출된 3케이스의 ac가 모두 0이었다"는 것.  
즉, 잘못된 ticker로 KG를 구성하면 답을 맞힐 수 없다는 게 확인됨.  
**→ 09B에서 ticker whitelist 필터를 반드시 추가해야 하는 근거.**

---

## 4. Format Error 분석 결과

| 항목 | 값 |
|---|---|
| 총 건수 | 3건 |
| 데이터셋 | FinDER 100% |
| formula_type | "other" 100% |
| 진단 | `missing_final_answer_field` 100% |

**3건 모두 동일한 증상:** 모델이 올바른 JSON 구조를 생성했지만 `final_answer` 필드를 빈 문자열(`""`)로 제출.

```json
{
  "brief_interpretation": "",
  "calculation_steps": [],
  "cited_source_facts_used": [],
  "final_answer": "",       ← 여기가 비어있음
  "rounding_statement": "",
  "uncertainty_or_missing_information": []
}
```

**원인:** `formula_type="other"`에 대한 `model_visible_contract`가 `required_outputs`를 명시하지 않아, 모델이 무엇을 `final_answer`에 넣어야 하는지 알 수 없었음. `prompt_v3_3_system.md`의 answer format spec이 standard formula_type들을 기준으로 작성됐고, "other" 타입 처리 지시가 없음.

**→ 모델 능력 문제가 아닌 prompt/contract specification 결여 문제. 09B에서 수정 가능.**  
수정 방향: `model_visible_contract`에 formula_type="other"일 때 `final_answer`에 숫자 결과(값+단위)를 반드시 포함하라는 fallback 지시 추가.

---

## 5. 3개 변경의 성격 분류

| Variant | 성격 분류 | 근거 | 09B 반영 여부 |
|---|---|---|---|
| V1 tolerance 완화 | **Scorer calibration** (측정 오류 수정에 가까움) | 맞은 답을 tolerance 부족으로 틀리게 채점한 케이스 수정 | ✅ 반영 |
| V2 unit normalization | **Scorer calibration** (단위 불일치 보정) | 모델이 ratio/percent 혼용 → 정규화로 교정 | ✅ vector에 반영, graph는 낮은 우선순위 |
| V3 partial credit | **New scoring philosophy** (binary → partial) | 본질적으로 다른 채점 방식 | ⚠️ 독립 지표로 관리, binary ac와 분리 |

---

## 6. R8 결과의 Scorer 민감도 요약

| 질문 | 답 |
|---|---|
| R8 graph 0.46이 tolerance 때문에 낮게 잡혔나? | 부분적으로 (V1 기준 +0.02, FinQA만 +0.05) |
| Unit normalization이 주요 원인이었나? | graph는 아님, vector에서 소폭 기여 |
| nc가 높은데 ac가 낮은 이유가 scorer인가? | FinQA는 맞음 (nc≥0.90 partial credit 시 +0.15) |
| Ticker 오류가 성능을 크게 낮췄나? | 3건이었고 모두 ac=0 → 제거 시 +0.056 (분모 효과) |
| Format error가 성능에 영향 줬나? | 3건 / 30 FinDER (10%) — 미미하지만 prompt 수정으로 제거 가능 |

**결론:** R8 0.46은 scorer 설계로 크게 부풀려지거나 억제된 결과가 아님.  
tolerance 완화·unit normalization을 모두 적용해도 +0.02(graph) 수준이고,  
나머지는 실질적인 모델/KG 품질 문제임.

---

## 7. 09B 반영 항목 확정

### 반드시 반영 (저비용, 근거 충분)

| 항목 | 변경 내용 | 근거 |
|---|---|---|
| FinQA tolerance | `max(0.1, 0.005×v)` → `max(0.5, 0.02×v)` | V1: +0.05 FinQA ac |
| Vector unit normalization | ratio↔percent 감지 + 정규화 | V2: +0.05 FinQA vector ac |
| Ticker whitelist | S&P 500 + denylist (CAGR, OF, LOSS 등) | V4: suspect 3건 모두 ac=0 |
| "other" formula_type output spec | model_visible_contract에 fallback final_answer 지시 추가 | format error 3건 원인 |

### 별도 관리 (scoring philosophy 변경)

| 항목 | 내용 | 주의사항 |
|---|---|---|
| Partial credit nc90 | nc≥0.90 → ac=0.5 | binary ac와 분리, `ac_partial` 별도 지표 |

### 구조적 개선 (09B 핵심)

| 항목 | 내용 |
|---|---|
| formula_type 목록 확장 | FinDER 30케이스 "other" 100% 해소 → yoy_change, segment_comparison 등 |
| Hybrid prompt 개선 | KG facts 우선 지시 (FinDER 10케이스 interference 해소) |
| 새 케이스 선택 | ticker whitelist + 확장 formula_type 기준 적용 |

---

## 8. 누적 라운드 진행

| 라운드 | 성격 | graph ac | vector ac |
|---|---|---:|---:|
| Round 5 | Round 3 KG 재사용 | 0.00 | — |
| Round 6 | Step B targeted KG | 0.50 | 0.40 |
| R6_rescored | scorer v7 | 0.60 | 0.40 |
| Round 7 | Prompt v3.3 + XEL patch | 0.90 | 0.60 |
| Round 8 | **Clean held-out pilot** | **0.46** | 0.36 |
| R9A V1 (calibrated) | scorer tolerance 2% | 0.48 | 0.38 |
| R9A V3 (partial) | partial credit nc90 | 0.52 | 0.40 |

> R8 0.46 → V1 교정 후 **0.48이 현재 가장 타당한 기준 성능**으로 볼 수 있음.  
> V3 0.52는 partial credit이 포함된 별도 지표.

---

## 9. Claim 경계

### 말해도 되는 것

```
R8 재채점 no-model sensitivity 결과:
- tolerance 완화 (0.5%→2%)로 FinQA graph +0.05, vector +0.05 — scorer calibration
- unit normalization으로 FinQA vector 추가 +0.05
- partial credit nc≥0.90 도입 시 FinQA graph +0.15, 전체 graph +0.06
- suspect ticker 3개(CAGR/OF/LOSS)는 모두 ac=0 → ticker 품질이 성능에 직결
- format error 3건은 모두 "other" formula_type의 output spec 결여 — 모델 능력 문제 아님
- R8 0.46은 scorer 설계로 크게 부풀려진 결과가 아님 (tolerance 교정 후 0.48)
```

### 말하면 안 되는 것

```
V3 partial credit 0.52를 "Round 9 성능"으로 제시 (채점 방식이 다름)
V4 FinDER 0.5556을 "실제 FinDER 성능 개선"으로 해석 (분모 축소 효과)
R9A 결과를 새 held-out에 일반화 (R8 traces 재채점이므로 새 데이터 아님)
```

---

## 10. 파일 위치

| 파일 | 경로 |
|---|---|
| Round 9A 스크립트 | `scripts/round9a_sensitivity.py` |
| 비교 테이블 | `outputs/round9a_sensitivity/comparison_table.md` |
| Format error 분석 | `outputs/round9a_sensitivity/format_error_analysis.json` |
| V1 rescored traces | `outputs/round9a_sensitivity/r8_rescored_tolerance_2pct.jsonl` |
| V2 rescored traces | `outputs/round9a_sensitivity/r8_rescored_tolerance_2pct_unit_norm.jsonl` |
| V3 rescored traces | `outputs/round9a_sensitivity/r8_rescored_partial_credit_nc90.jsonl` |
| V4 rescored traces | `outputs/round9a_sensitivity/r8_sensitivity_no_suspect_tickers.jsonl` |
| State | `outputs/round9a_sensitivity/state.json` |
