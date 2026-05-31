# Round 09B Pipeline Fix — Codex Execution Spec

**작성일:** 2026-05-29  
**목표:** 09A sensitivity 분석에서 확정된 4개 파이프라인 수정 사항 구현 (모델 eval 없음)  
**Claim boundary:** `pipeline_fix_no_model_eval`  
**모델/API 호출:** 계약 생성 validation용 소량 GPT 호출만 허용 (eval 재실행 없음)  

---

## 0. 불변 규칙

```
eval 재실행 금지 (150 traces 재생성 금지)
R8 원본 파일 수정 금지
R9A 원본 파일 수정 금지
KG patch 금지
새 KG 배치 생성 금지 (09C에서 함)
```

---

## 1. 09B 작업 목록

| 번호 | 작업 | 파일 | 복잡도 |
|---|---|---|---|
| B1 | Scorer v9 모듈 생성 | `scripts/scorer_v9.py` | 낮음 |
| B2 | Ticker whitelist/denylist | `scripts/ticker_filter.py` | 낮음 |
| B3 | "other" formula fallback output spec | `scripts/round9c_formula_contract_gen.py` 의 base | 낮음 |
| B4 | Hybrid KG-first prompt 지시 | `outputs/round3_dual_track_eval_prep/prompt_formatter_v3_2/prompt_v3_3_kgfirst.md` | 낮음 |
| B5 | Validation — scorer_v9을 R8 traces에 적용 | `scripts/round9b_validation.py` | 낮음 |

---

## 2. B1: Scorer v9 (`scripts/scorer_v9.py`)

Round 08까지 사용하던 scorer (v7, no faith gate)에서 두 가지 변경.

### 2.1 Tolerance 변경

```python
# v7 (기존)
def compute_tolerance(expected_value: float) -> float:
    return max(0.1, abs(expected_value) * 0.005)

# v9 (신규)
def compute_tolerance(expected_value: float, formula_type: str = "") -> float:
    if formula_type == "finqa_program":
        return max(0.5, abs(expected_value) * 0.02)   # FinQA: 2% 상대오차
    return max(0.1, abs(expected_value) * 0.005)       # 기타: 기존 유지
```

**이유:** FinQA만 완화. FinDER 표준 타입은 기존 tolerance 유지 (09A V1 근거).

### 2.2 Unit Normalization (vector 전용)

```python
def normalize_unit(model_val: float, expected_val: float) -> tuple[float, bool]:
    """
    ratio↔percent 불일치 감지 및 정규화.
    Returns (normalized_model_val, was_normalized).
    """
    if expected_val == 0 or model_val == 0:
        return model_val, False
    ratio = model_val / expected_val
    if 80 <= ratio <= 120:           # model이 percent, expected가 ratio
        return model_val / 100, True
    if 0.008 <= ratio <= 0.012:      # model이 ratio, expected가 percent
        return model_val * 100, True
    return model_val, False
```

**적용 대상:** `method in ("vector_only_v*", "hybrid_neo4j_v*")` 일 때만 적용.  
graph_neo4j는 KG facts에서 단위를 가져오므로 unit normalization 불필요 (09A V2 근거).

### 2.3 Scorer 메인 함수

```python
def score_trace(
    trace: dict,
    scorer_contract: dict,
    method: str,
) -> dict:
    """
    Returns updated trace dict with scoring fields.
    scorer_version = "v9"
    """
    formula_type = scorer_contract.get("formula_type", "")
    target_slots = scorer_contract.get("target_slots", [])
    
    # model answer 파싱 (기존 로직 그대로)
    model_answer = extract_model_answer(trace)
    
    slot_results = []
    for slot in target_slots:
        expected = slot["expected_value"]
        tolerance = compute_tolerance(expected, formula_type)
        model_val = extract_slot_value(model_answer, slot["target_slot_name"])
        
        # Unit normalization (vector/hybrid only)
        if method not in ("graph_neo4j_v8", "graph_neo4j_v9"):
            model_val, unit_norm = normalize_unit(model_val, expected)
        else:
            unit_norm = False
        
        match = model_val is not None and abs(model_val - expected) <= tolerance
        slot_results.append({
            "slot_name": slot["target_slot_name"],
            "expected_value": expected,
            "model_value": model_val,
            "tolerance": tolerance,
            "match": match,
            "unit_normalized": unit_norm,
        })
    
    numeric_ok = all(s["match"] for s in slot_results)
    fmt = check_format(model_answer)           # 기존 format check
    calc = check_calculation(model_answer)     # 기존 calc check
    # faith gate 없음 (v7부터 제거됨)
    
    ac = 1.0 if (numeric_ok and fmt and calc) else 0.0
    nc = compute_numerical_closeness(slot_results)
    rfr = compute_rfr(trace, scorer_contract)
    
    return {
        **trace,
        "answer_correctness": ac,
        "numerical_closeness": nc,
        "required_fact_recall": rfr,
        "target_slot_results": slot_results,
        "scorer_version": "v9",
        "failure_reason": classify_failure(ac, numeric_ok, fmt, calc, slot_results),
    }
```

`extract_model_answer`, `check_format`, `check_calculation`, `compute_rfr`, `classify_failure`, `compute_numerical_closeness` 등은 `round7_eval.py` / `round8_eval.py`에서 임포트하거나 복사.

---

## 3. B2: Ticker Filter (`scripts/ticker_filter.py`)

### 3.1 Denylist

```python
TICKER_DENYLIST = {
    # 금융 용어 (단어로 추출되는 오류 케이스)
    "CAGR", "OF", "LOSS", "GAIN", "EPS", "EBITDA", "EBIT",
    "GAAP", "NON", "TAX", "NET", "GROSS", "INC", "LLC", "LTD",
    "AND", "THE", "FOR", "SEC", "ROI", "CEO", "CFO", "FY",
    "USD", "US", "UK", "EU", "YOY", "QOQ",
    # 기존 stop tickers (round3_case_factory.py에서 가져옴)
    "THE", "AND", "FOR", "INC", "LLC", "LTD", "SEC", "ROI",
    "EPS", "CEO", "CFO", "FY", "GAAP", "R&D", "USD", "US", "UK",
}
```

### 3.2 Allowlist (S&P 500 기반)

`data/sp500_tickers.txt` 파일 없으면 아래 방식으로 보완:

```python
def is_valid_ticker(ticker: str) -> bool:
    """
    True if ticker looks like a real equity ticker.
    Rules:
    1. Length 1-5 characters
    2. All uppercase letters (no digits, no special chars except . for class shares)
    3. Not in TICKER_DENYLIST
    4. At least 2 chars (single-letter tickers 'A', 'T' etc. are real but rare — allow)
    """
    if not ticker:
        return False
    ticker = ticker.strip().upper()
    if ticker in TICKER_DENYLIST:
        return False
    if not re.match(r'^[A-Z]{1,5}$', ticker):  # 숫자, 특수문자 제외
        return False
    return True
```

### 3.3 Company → Ticker 매핑 (선택적 강화)

`round3_case_factory.py`의 기존 COMPANY_TO_TICKER 매핑을 `ticker_filter.py`로 이동하여 중앙화.  
매핑 없이 규칙으로만 추출된 ticker는 `is_valid_ticker()` 통과 여부로 최종 결정.

### 3.4 Export

```python
def filter_ticker(ticker: str) -> str | None:
    """Returns ticker if valid, None if should be rejected."""
    if is_valid_ticker(ticker):
        return ticker.upper()
    return None
```

---

## 4. B3: "other" Formula Type Output Spec Fallback

### 4.1 문제

현재 `model_visible_contract`는 standard formula_type일 때 `required_outputs` 필드에 구체적인 슬롯명을 제공함.  
`formula_type="other"`일 때 이 필드가 비어있어 모델이 `final_answer`에 무엇을 넣을지 모름.

### 4.2 수정

`round8_formula_contract_gen.py`를 복사하여 `round9c_formula_contract_gen.py` 기반 코드에 아래 추가:

```python
def build_model_visible_contract(scorer_contract: dict) -> dict:
    formula_type = scorer_contract.get("formula_type", "other")
    target_slots = scorer_contract.get("target_slots", [])
    
    # 기존 로직
    required_outputs = [s["target_slot_name"] for s in target_slots]
    output_units = {s["target_slot_name"]: s.get("unit", "") for s in target_slots}
    
    # "other" 타입 fallback 추가
    if formula_type == "other":
        output_format_hints = (
            "Compute the requested financial metric(s) from the evidence. "
            "Place the final numeric result(s) in the 'final_answer' field as a string "
            "in the format '<value> <unit>' (e.g., '42.3%', '$1.2 billion', '0.85x'). "
            "If multiple values are required, list them each on a new line with labels."
        )
    elif formula_type == "finqa_program":
        output_format_hints = (
            "Compute the result of the described financial calculation. "
            "Place the single numeric result in 'final_answer' with appropriate unit."
        )
    else:
        output_format_hints = f"Express as {output_units} to 1 decimal place."
    
    return {
        "case_id": scorer_contract["case_id"],
        "formula_type": formula_type,
        "required_outputs": required_outputs,
        "output_units": output_units,
        "output_format_hints": output_format_hints,
    }
```

---

## 5. B4: Hybrid KG-First Prompt (`prompt_v3_3_kgfirst.md`)

**기존 파일:** `outputs/round3_dual_track_eval_prep/prompt_formatter_v3_2/prompt_v3_3_system.md`  
**신규 파일:** `outputs/round3_dual_track_eval_prep/prompt_formatter_v3_2/prompt_v3_3_kgfirst.md`

기존 prompt_v3_3_system.md 내용 복사 후 아래 섹션 추가 (GRAPH_FACTS_TABLE 지시 바로 아래):

```markdown
## Source Priority Rule (Hybrid Mode Only)

When both GRAPH_FACTS_TABLE and TEXT_CONTEXT are provided:

1. **GRAPH_FACTS_TABLE is authoritative for all numeric values.**  
   Use graph facts as the primary source for any calculation.

2. **TEXT_CONTEXT provides background and provenance only.**  
   Do NOT use numeric values from TEXT_CONTEXT if they conflict with GRAPH_FACTS_TABLE.

3. **If a value appears in both sources but differs, always use the GRAPH_FACTS_TABLE value.**

4. If a required fact is missing from GRAPH_FACTS_TABLE, you may use TEXT_CONTEXT as a fallback,
   but flag it in `uncertainty_or_missing_information`.
```

**적용 방식:** `round9c_eval.py`에서 `hybrid_neo4j_v9` method에 `prompt_v3_3_kgfirst.md` 사용,  
`graph_neo4j_v9` / `vector_only_v9`는 기존 `prompt_v3_3_system.md` 유지.

---

## 6. B5: Validation (`scripts/round9b_validation.py`)

09B 코드 변경이 09A sensitivity 결과와 일치하는지 확인. 모델 호출 없음.

### 6.1 검증 항목

```python
# scorer_v9를 R8 traces에 적용
# 결과가 09A V1+V2 combined와 일치해야 함

EXPECTED = {
    "graph_neo4j_v8": {
        "finqa_ac": 0.45,   # V1 결과 (tolerance 2% 적용)
        "finder_ac": 0.50,  # 변화 없어야 함
        "overall_ac": 0.48,
    },
    "vector_only_v8": {
        "finqa_ac": 0.55,   # V2 결과 (tolerance + unit norm)
        "finder_ac": 0.30,  # 변화 없어야 함
        "overall_ac": 0.40,
    },
}
```

### 6.2 검증 성공 기준

모든 method × dataset 조합에서 expected와 ±0.01 이내.

### 6.3 출력

```
outputs/round9b_validation/
  validation_report.json    # pass/fail per check
  state.json               # phase=9b_done, validation_passed=true/false
```

---

## 7. 실행 순서

```
1. scripts/scorer_v9.py 작성
2. scripts/ticker_filter.py 작성
3. prompt_v3_3_kgfirst.md 작성
4. round9b_validation.py 작성
5. round9b_validation.py 실행 → validation_passed=true 확인
6. state.json 완성
```

---

## 8. state.json

```json
{
  "phase": "9b_done",
  "round": "round9b",
  "scorer_version": "v9",
  "changes": [
    "finqa_tolerance_2pct",
    "vector_unit_normalization",
    "other_formula_output_spec",
    "hybrid_kgfirst_prompt",
    "ticker_denylist"
  ],
  "validation_passed": true,
  "model_calls": 0,
  "completed_at": "..."
}
```

---

## 9. 파일 위치

| 파일 | 경로 |
|---|---|
| Scorer v9 | `scripts/scorer_v9.py` |
| Ticker filter | `scripts/ticker_filter.py` |
| Hybrid KG-first prompt | `outputs/round3_dual_track_eval_prep/prompt_formatter_v3_2/prompt_v3_3_kgfirst.md` |
| Validation 스크립트 | `scripts/round9b_validation.py` |
| Validation 결과 | `outputs/round9b_validation/` |
