# Round 09A No-Model Sensitivity Analysis — Codex Execution Spec

**작성일:** 2026-05-29  
**목표:** R8 traces를 재채점하여 scorer/tolerance/ticker 설계 선택이 결과에 얼마나 영향을 미치는지 분리 측정  
**Claim boundary:** `r8_scorer_sensitivity_no_model_rerun`  
**모델 호출:** 없음 (no-model rescore 전용)  

---

## 0. 불변 규칙

```
모델/API 호출 절대 금지
R8 traces 수정 금지 (read-only)
R8 state.json / round8_summary.md 수정 금지
KG patch 금지
prompt 변경 금지
새 파일로만 저장
```

**R8 원본 경로 (read-only):**
```
outputs/round3_eval_runs/round8_eval_20260529_103625/round8_traces.jsonl
outputs/round8_eval/state.json
outputs/round8_case_selection/finder_candidates.jsonl
outputs/round8_case_selection/finqa_candidates.jsonl
```

---

## 1. 출력 파일 구조

```
outputs/round9a_sensitivity/
  r8_rescored_tolerance_2pct.jsonl
  r8_rescored_tolerance_2pct_unit_norm.jsonl
  r8_rescored_partial_credit_nc90.jsonl
  r8_sensitivity_no_suspect_tickers.jsonl
  format_error_analysis.json
  comparison_table.md
  state.json
```

---

## 2. 스크립트: `scripts/round9a_sensitivity.py`

하나의 스크립트에서 모든 변형을 순서대로 실행.

### 2.1 입력 로딩

```python
R8_TRACES = ROOT / "outputs/round3_eval_runs/round8_eval_20260529_103625/round8_traces.jsonl"
R8_FINDER  = ROOT / "outputs/round8_case_selection/finder_candidates.jsonl"
R8_FINQA   = ROOT / "outputs/round8_case_selection/finqa_candidates.jsonl"
OUT_DIR    = ROOT / "outputs/round9a_sensitivity"

SUSPICIOUS_TICKERS = {"CAGR", "OF", "LOSS"}
```

traces를 method별, case_id별로 인덱싱해두기:
```python
traces: list[dict]           # 모든 150개 trace
by_case: dict[str, dict]     # case_id → {method → trace}
by_method: dict[str, list]   # method → [traces]
```

---

## 3. Variant 1: FinQA Tolerance 2% (`r8_rescored_tolerance_2pct.jsonl`)

### 3.1 적용 대상

`source_dataset == "FinQA"` AND `formula_type == "finqa_program"` 인 trace만 재채점.  
FinDER trace는 변경 없이 원본 그대로.

### 3.2 새 tolerance 계산 공식

```python
def tolerance_2pct(expected_value: float) -> float:
    return max(0.5, abs(expected_value) * 0.02)
```

기존: `max(0.1, abs(expected_value) * 0.005)`  
변경: `max(0.5, abs(expected_value) * 0.02)` (2% 상대오차, 최소 0.5)

### 3.3 재채점 로직

각 FinQA trace에 대해:
1. `target_slot_results`에서 각 slot 읽기
2. `abs(model_value - expected_value) <= new_tolerance` 로 slot match 재계산
3. `numeric_ok = all(slot.match for slot in target_slot_results)`
4. `ans = numeric_ok and fmt` (fmt는 기존 그대로, faith gate 없음)
5. 재계산된 `answer_correctness` 저장

**재채점 불가 케이스 처리:**
- `target_slot_results` 필드 없거나 `model_value`가 null → 원본 ac 유지, `rescore_status: "no_slot_data"`

### 3.4 출력 필드 추가

각 trace에 다음 필드 추가:
```json
{
  "answer_correctness_r8_original": 0.0,
  "answer_correctness_v1_tol2pct": 1.0,
  "rescore_variant": "tolerance_2pct",
  "rescore_applied": true
}
```

---

## 4. Variant 2: FinQA Tolerance 2% + Unit Normalization (`r8_rescored_tolerance_2pct_unit_norm.jsonl`)

Variant 1에 추가로 ratio↔percent 단위 불일치 케이스를 감지해서 재채점.

### 4.1 Unit Normalization 규칙

FinQA `finqa_program` 케이스에서 expected_value와 model_value 사이에 ~100 배 관계가 있으면 단위 불일치로 판정:

```python
def is_unit_mismatch(model_val: float, expected_val: float) -> bool:
    """Check if one value is ~100x the other (ratio vs percentage confusion)."""
    if expected_val == 0 or model_val == 0:
        return False
    ratio = model_val / expected_val
    return 80 <= ratio <= 120 or 0.008 <= ratio <= 0.012
```

단위 불일치가 감지되면 model_value를 정규화해서 재채점:
```python
if is_unit_mismatch(model_val, expected_val):
    # 두 방향 모두 시도, 더 가까운 쪽 사용
    normalized_pct = model_val / 100   # percent → ratio
    normalized_rat = model_val * 100   # ratio → percent
    closer = min(normalized_pct, normalized_rat, key=lambda v: abs(v - expected_val))
    model_val_for_scoring = closer
    unit_normalized = True
else:
    model_val_for_scoring = model_val
    unit_normalized = False
```

새 tolerance도 동일하게 적용 (`max(0.5, abs(expected_value) * 0.02)`).

### 4.2 출력 필드 추가

```json
{
  "answer_correctness_r8_original": 0.0,
  "answer_correctness_v2_tol2pct_unit": 1.0,
  "unit_normalized": true,
  "rescore_variant": "tolerance_2pct_unit_norm",
  "rescore_applied": true
}
```

---

## 5. Variant 3: Partial Credit nc ≥ 0.90 (`r8_rescored_partial_credit_nc90.jsonl`)

**모든 케이스 (FinDER + FinQA)에 적용.**

### 5.1 Partial Credit 규칙

```python
def partial_credit_ac(original_ac: float, nc: float) -> float:
    if original_ac == 1.0:
        return 1.0
    if nc >= 0.90:
        return 0.5   # partial credit
    return 0.0
```

즉, 기존 ac=0.0이지만 nc≥0.90인 케이스를 ac=0.5로 처리.

### 5.2 출력 필드 추가

```json
{
  "answer_correctness_r8_original": 0.0,
  "answer_correctness_v3_partial": 0.5,
  "partial_credit_applied": true,
  "rescore_variant": "partial_credit_nc90"
}
```

### 5.3 avg_ac 계산 방식

partial credit 포함 avg_ac = `sum(v3_ac) / n_cases`  
(ac=0.5를 그대로 평균에 포함)

---

## 6. Variant 4: Suspicious Ticker 제거 Sensitivity (`r8_sensitivity_no_suspect_tickers.jsonl`)

### 6.1 의심 ticker 케이스 식별

```python
SUSPICIOUS_TICKERS = {"CAGR", "OF", "LOSS"}

finder_candidates = load_jsonl(R8_FINDER)
suspect_case_ids = {
    c["case_id"] for c in finder_candidates
    if c.get("ticker", "").upper() in SUSPICIOUS_TICKERS
}
```

### 6.2 재채점 방식

suspect case_id에 해당하는 trace를 **결과 계산에서 제외**하고 avg_ac 재계산.  
(trace를 삭제하는 게 아니라 `excluded_suspect_ticker=true` 플래그 추가 후 집계에서 빼기)

```json
{
  "excluded_suspect_ticker": true,
  "answer_correctness_r8_original": 0.0
}
```

### 6.3 집계 방식

- `r8_original`: 50 cases
- `r8_no_suspect`: 50 - len(suspect_case_ids) cases (최대 3개 제외)
- 두 집계를 나란히 비교

---

## 7. Format Error 분석 (`format_error_analysis.json`)

별도 재채점 variant가 아니라 **분류 리포트**.

### 7.1 대상

```python
format_error_cases = [
    t for t in traces
    if t.get("failure_reason") == "answer_format_error"
       and t.get("method") == "graph_neo4j_v8"
]
```

### 7.2 분석 항목

각 format_error 케이스에 대해:
1. `case_id`, `source_dataset`, `formula_type`
2. `model_raw_answer` (원본 모델 출력, 있으면)
3. `expected_format` (scorer가 기대한 형식)
4. `diagnosis`: 아래 중 하나로 분류
   - `"missing_final_answer_field"` — final_answer JSON 필드 누락
   - `"wrong_unit_label"` — 단위 표기 오류 (% vs ratio)
   - `"extra_explanation_in_slot"` — slot 값에 텍스트 포함
   - `"other"` — 기타

### 7.3 출력

```json
{
  "total_format_errors": 3,
  "by_dataset": {"FinDER": 3, "FinQA": 0},
  "by_formula_type": {"other": 3},
  "by_diagnosis": {
    "missing_final_answer_field": 1,
    "wrong_unit_label": 1,
    "other": 1
  },
  "cases": [...]
}
```

**이 3건은 모델 성능이 아닌 prompt/output-spec 문제이므로 scorer sensitivity와 무관하게 분리.**

---

## 8. 비교 테이블 (`comparison_table.md`)

### 8.1 전체 비교

```markdown
## R8 Scorer Sensitivity — Overall (50 cases)

| Variant | vector ac | graph ac | hybrid ac | 비고 |
|---|---:|---:|---:|---|
| R8_original | 0.36 | 0.46 | 0.40 | baseline |
| V1: tol_2pct | ? | ? | ? | FinQA tolerance 완화 |
| V2: tol_2pct_unit | ? | ? | ? | V1 + ratio/% 정규화 |
| V3: partial_nc90 | ? | ? | ? | nc≥0.90 → ac=0.5 |
| V4: no_suspect | ? | ? | ? | CAGR/OF/LOSS 제외 |
```

### 8.2 데이터셋별 비교

```markdown
## By Dataset

| Variant | FinDER graph | FinQA graph | FinDER vector | FinQA vector |
|---|---:|---:|---:|---:|
| R8_original | 0.50 | 0.40 | 0.30 | 0.45 |
| V1: tol_2pct | (FinDER 동일) | ? | (동일) | ? |
| V2: tol_2pct_unit | (동일) | ? | (동일) | ? |
| V3: partial_nc90 | ? | ? | ? | ? |
| V4: no_suspect | ? | ? | (동일) | (동일) |
```

### 8.3 해석 섹션

각 variant 결과 아래에 한 줄 해석:
- V1이 V2보다 얼마나 더 올라갔는지 → unit normalization의 기여
- V3가 V1보다 얼마나 더 올라갔는지 → partial credit의 기여
- V4가 R8_original과 거의 같다면 → ticker 이슈는 실제 영향 미미

---

## 9. state.json

```json
{
  "phase": "9a_done",
  "round": "round9a",
  "r8_traces_source": "outputs/round3_eval_runs/round8_eval_20260529_103625/round8_traces.jsonl",
  "r8_original_graph_ac": 0.46,
  "r8_original_vector_ac": 0.36,
  "r8_original_hybrid_ac": 0.40,
  "v1_tol2pct_graph_ac": null,
  "v2_tol2pct_unit_graph_ac": null,
  "v3_partial_nc90_graph_ac": null,
  "v4_no_suspect_graph_ac": null,
  "suspect_cases_removed": null,
  "format_error_cases": 3,
  "model_calls": 0,
  "completed_at": "..."
}
```

`null` 자리에 실제 계산값 채워 넣기.

---

## 10. 실행 체크리스트

```
[ ] R8 traces 로드 (150개) 확인
[ ] R8_original 기준 수치 재확인 (graph 0.46 / vector 0.36 / hybrid 0.40)
[ ] Variant 1 (tol_2pct) 실행 → r8_rescored_tolerance_2pct.jsonl
[ ] Variant 2 (unit_norm) 실행 → r8_rescored_tolerance_2pct_unit_norm.jsonl
[ ] Variant 3 (partial_nc90) 실행 → r8_rescored_partial_credit_nc90.jsonl
[ ] Variant 4 (no_suspect) 실행 → r8_sensitivity_no_suspect_tickers.jsonl
[ ] Format error 분석 → format_error_analysis.json
[ ] comparison_table.md 생성
[ ] state.json 완성 (model_calls=0 확인 필수)
[ ] R8 원본 파일 변경 없음 확인 (git status로)
```

---

## 11. 다음 단계 안내 (09A 완료 후)

09A 결과에 따라 Round 09B 방향 결정:

| 조건 | 결론 |
|---|---|
| V1/V2로 FinQA graph ac가 +0.10 이상 상승 | tolerance 완화를 09B scorer에 반영 |
| V2 > V1 (unit_norm 효과 있음) | finqa_program 계약 생성 시 unit 통일 로직 추가 |
| V3 효과가 V1보다 크다 | partial credit 도입 검토 (단, claim 표현 주의) |
| V4와 R8_original 차이 작다 | ticker 이슈는 품질 문제이지 성능 병목이 아님 확인 |
| format_error 3건 진단 완료 | 09B prompt에서 output envelope 수정 (answer_format_spec 업데이트) |

---

## 12. 파일 위치 요약

| 파일 | 경로 |
|---|---|
| 이 스펙 | `codex_prompt_round9a_no_model_sensitivity.md` |
| R8 traces (read-only) | `outputs/round3_eval_runs/round8_eval_20260529_103625/round8_traces.jsonl` |
| 출력 디렉토리 | `outputs/round9a_sensitivity/` |
| 스크립트 | `scripts/round9a_sensitivity.py` |
