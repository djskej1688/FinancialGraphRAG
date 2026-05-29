# Round 09C New Eval — Codex Execution Spec

**작성일:** 2026-05-29  
**전제:** Round 09B 완료 (scorer_v9, ticker_filter, hybrid KG-first prompt 준비 완료)  
**목표:** 09B 파이프라인 수정 + 확장 formula_type 기준으로 새 50케이스 선택 → 완전한 clean held-out eval  
**Claim boundary:** `clean_held_out_round9c_fixed_pipeline`  
**총 실행:** 50 cases × 3 methods = 150 traces  

---

## 0. 불변 규칙

```
OPENAI_API_KEY  환경변수에서만
Neo4j           .env (python-dotenv)
neo4j_write_performed = False 기본; Pre-work D에서만 True

건드리면 안 되는 경로:
  outputs/round3_eval_runs/locked_test_v3_2_track_b_20260528_145253/
  outputs/round8_eval/ (R8 원본)
  outputs/round9a_sensitivity/ (R9A 원본)
  outputs/round9b_validation/ (R9B 원본)

Round 09B 완료 확인 먼저:
  outputs/round9b_validation/state.json → phase=9b_done, validation_passed=true
```

---

## 1. 사용 금지 Ticker (누적)

```python
EXCLUDED_TICKERS = {
    # Round 3 test
    "AMGN", "APD", "BXP", "GM", "LOW", "MPC", "MU", "NXPI", "VRSK", "XEL",
    # Round 3 dev
    "BAC", "BW", "CARR", "CMCSA", "FOXA", "HCA", "KR", "LND", "MCO", "MDLZ", "MSFT", "MTB",
    # Round 8 FinDER (valid + suspect)
    "DUK", "AES", "AIG", "AXP", "BLK", "CAGR", "CEG", "CNP", "EQR", "EVRG",
    "EXPD", "GNRC", "LKQ", "LVS", "MAA", "OF", "ONEOK", "PAYC", "PTC", "SBA",
    "VMC", "WLTW", "WMB", "ZBH", "GLW", "EMN", "RMD", "LOSS", "VICI", "OI",
    # Round 8 FinQA
    "ABMD", "ADI", "ALLE", "AMAT", "AMT", "ANET", "APTV", "AWK", "CAT", "CB",
    "CME", "DISCA", "DISH", "DRE", "DVN", "ETR", "GPN", "GS", "HIG", "HUM",
}
```

---

## 2. 파일/폴더 구조

```
outputs/round9c_case_selection/
  finder_candidates.jsonl
  finqa_candidates.jsonl
  selection_state.json

outputs/round9c_formula_contracts/
  round9c_scorer_contracts.jsonl
  round9c_model_visible_contracts.jsonl
  generation_state.json
  generation_trace.jsonl
  validation_report.jsonl

outputs/round9c_step_b_kg/
  extraction_trace.jsonl
  kg_write_log.jsonl
  failed_extractions.jsonl
  state.json
  round9c_kg_rollback.cypher

outputs/round9c_eval/
  state.json

outputs/round3_eval_runs/round9c_eval_TIMESTAMP/
  round9c_traces.jsonl
  round9c_summary.md
```

---

## 3. Pre-work A: FinDER 케이스 선택 (`round9c_finder_case_selector.py`)

`round8_finder_case_selector.py`를 기반으로 수정. 주요 변경점만 명시.

### 3.1 변경점

1. **ticker_filter.py 사용:** `from ticker_filter import filter_ticker, is_valid_ticker`  
   — TICKER_DENYLIST + 형식 검사 적용
2. **EXCLUDED_TICKERS:** 위 누적 목록 사용
3. **case_id prefix:** `round9c_finder`
4. **kg_batch:** `kg-round9c-v1-YYYYMMDD`

### 3.2 선택 목표: 30케이스

동일한 품질 스코어링 + deterministic 선택 + ticker당 1개.

---

## 4. Pre-work B: FinQA 케이스 선택 (`round9c_finqa_case_selector.py`)

`round8_finqa_case_selector.py`를 기반으로 수정.

### 4.1 변경점

1. **EXCLUDED_TICKERS:** 위 누적 목록 사용
2. **case_id prefix:** `round9c_finqa`
3. **train.json 이미 사용한 케이스 제외:** R8에서 사용한 source_filename 목록 로드 후 제외
   ```python
   r8_finqa_used = {c["source_filename"] for c in load_jsonl(R8_FINQA_CANDIDATES)}
   # 이미 사용한 케이스 건너뛰기
   ```

### 4.2 선택 목표: 20케이스

---

## 5. Pre-work C: Formula Contract 생성 (`round9c_formula_contract_gen.py`)

`round8_formula_contract_gen.py`를 기반으로 수정. 핵심 변경: formula_type 목록 확장 + "other" fallback.

### 5.1 확장된 Formula Type 목록

GPT 분류 프롬프트에 아래 타입 추가:

```python
FORMULA_TYPES = [
    # 기존
    "gross_margin",
    "operating_margin",
    "net_margin",
    "diluted_eps_and_yoy_change",
    "continuing_ops_margin",
    "operating_vs_net_margin",
    "workforce_ratio",
    "tpo_segment_gross_margin",
    "net_margin_and_nonop_impact",
    
    # 신규
    "yoy_revenue_change",        # 전년 대비 매출 증감 (절대값 + %)
    "multi_year_margin",         # 동일 margin을 2년 이상에 걸쳐 계산
    "segment_comparison",        # 사업부문별 동일 지표 비교
    "eps_dilution",              # 희석 EPS + 희석 효과 분석
    "ratio_trend",               # 임의 재무비율의 연도별 추이
    "income_vs_ops",             # 영업이익 vs 순이익 차이 (비영업항목 영향)
    "effective_tax_rate",        # 유효세율 계산
    "capex_intensity",           # CAPEX / Revenue 비율
    "debt_metrics",              # 부채비율, 이자보상배율 등
    
    "finqa_program",             # FinQA 전용
    "other",                     # 위에 해당하지 않는 경우
]
```

### 5.2 GPT 분류 프롬프트 개선

기존 프롬프트에 각 타입 정의 예시 추가:

```
SYSTEM:
You are a financial formula contract extractor.
Given a financial question, evidence text, and expected answer, extract:

1. formula_type: Choose the BEST matching type from this list:
   - gross_margin: (Revenue - COGS) / Revenue
   - operating_margin: Operating Income / Revenue
   - net_margin: Net Income / Revenue
   - yoy_revenue_change: (Current Year Revenue - Prior Year Revenue) / Prior Year Revenue
   - multi_year_margin: Same margin formula across 2+ fiscal years
   - segment_comparison: Same metric across different business segments
   - eps_dilution: Diluted EPS calculation with share count impact
   - ratio_trend: Any financial ratio tracked across 2+ years
   - income_vs_ops: Explains why net income differs from operating income
   - effective_tax_rate: Income Tax Expense / Pre-tax Income
   - capex_intensity: CapEx / Revenue
   - debt_metrics: Debt-to-equity, interest coverage, or similar leverage metric
   - other: None of the above clearly applies

   Pick the MOST SPECIFIC type possible. Only use "other" if genuinely no other type fits.

2. source_fact_numbers: [...]
3. target_slots: [...]

Return JSON only.
```

### 5.3 "other" fallback (B3에서 구현한 내용 포함)

`build_model_visible_contract`에서 formula_type="other"일 때 output spec fallback 적용 (09B B3 참조).

### 5.4 검증 기준

- FinDER: 25/30 이상 통과 (R8의 50/50과 동일 기준이지만 formula_type 분산 체크 추가)
- **신규 기준:** formula_type="other" 비율이 50% 이하여야 함 (R8의 100%를 개선)
  - 50% 초과 시 경고 로그 + 계속 진행 (실패로 중단 안 함)
- FinQA: 17/20 이상 통과

---

## 6. Pre-work D: Step B KG 추출 (`round9c_step_b_kg_extraction.py`)

`round8_step_b_kg_extraction.py`와 동일한 패턴. 변경점:

- `BATCH_ID = f"kg-round9c-v1-{TODAY}"`
- 입력 파일: `round9c_scorer_contracts.jsonl`, `round9c_finder/finqa_candidates.jsonl`
- 출력: `outputs/round9c_step_b_kg/`

안전 절차 동일:
- write 전 rollback Cypher 생성
- write 후 write_log 확인
- success_rate ≥ 0.80 이상이어야 eval 진행

---

## 7. Main Eval: `round9c_eval.py`

`round8_eval.py` 기반. 주요 변경점:

### 7.1 상수

```python
METHODS = ["vector_only_v9", "graph_neo4j_v9", "hybrid_neo4j_v9"]
ROUND = "round9c"
KG_BATCH = "<round9c_step_b kg state에서 읽기>"
PROMPT_VERSION = "v3.3_kgfirst"   # hybrid에만 적용
SCORING_VERSION = "v9"
CLAIM_BOUNDARY = "clean_held_out_round9c_fixed_pipeline"
```

### 7.2 Scorer

```python
from scorer_v9 import score_trace
```

round8_eval.py 내부 scorer 로직 대신 scorer_v9 모듈 사용.

### 7.3 Prompt 선택

```python
def get_system_prompt(method: str) -> str:
    pdir = prompt_dir()
    if method.startswith("hybrid"):
        return (pdir / "prompt_v3_3_kgfirst.md").read_text(encoding="utf-8")
    return (pdir / "prompt_v3_3_system.md").read_text(encoding="utf-8")
```

### 7.4 Trace 추가 필드

```python
{
    "round": "round9c",
    "source_dataset": "FinDER" or "FinQA",
    "formula_type": "...",
    "scorer_version": "v9",
    "prompt_version": "v3.3" or "v3.3_kgfirst",
}
```

### 7.5 Summary 형식 (`round9c_summary.md`)

```markdown
# Round 09C Summary

## Overall (all cases)

| Method | avg_ac | avg_nc | avg_rfr | n_cases |
...

## By Dataset

| Dataset | Method | avg_ac | avg_nc | n_cases |
...

## Formula Type Distribution

| formula_type | count | avg_ac |
...

## R8 vs R9C Comparison (graph method)

| Dataset | R8 graph ac | R9C graph ac | delta |
...

## Claim Limit

Round 09C is a clean held-out benchmark with fixed pipeline:
- New cases not seen in R8 (different tickers, different source records)
- scorer_v9: finqa tolerance 2%, vector unit normalization
- hybrid uses KG-first prompt (v3.3_kgfirst)
- ticker filter applied: 0 suspect tickers expected
- formula_type "other" ratio: X%  (target < 50%)
```

---

## 8. state.json

```json
{
  "round": "round9c",
  "phase": "done",
  "kg_batch": "kg-round9c-v1-YYYYMMDD",
  "prompt_version": "v3.3_kgfirst",
  "scoring_version": "v9",
  "claim_boundary": "clean_held_out_round9c_fixed_pipeline",
  "cases_finder": ...,
  "cases_finqa": ...,
  "cases_total": ...,
  "formula_type_other_pct": ...,
  "runs_total": ...,
  "runs_completed": ...,
  "runs_failed": [],
  "test_ac_vector": ...,
  "test_ac_graph": ...,
  "test_ac_hybrid": ...,
  "test_ac_graph_finder": ...,
  "test_ac_graph_finqa": ...,
  "graph_beats_vector_test": ...,
  "hybrid_beats_graph_finder": ...,
  "started_at": "...",
  "completed_at": "..."
}
```

`hybrid_beats_graph_finder` 추가: FinDER에서 hybrid > graph이면 KG-first prompt 효과 있음.

---

## 9. 실행 체크리스트

```
[ ] 09B state.json validation_passed=true 확인
[ ] A: round9c_finder_case_selector.py 실행 → 30케이스
[ ] B: round9c_finqa_case_selector.py 실행 → 20케이스
[ ] C: round9c_formula_contract_gen.py 실행
[ ]   → FinDER formula_type="other" 비율 확인 (50% 이하 목표)
[ ]   → 검증 통과율 확인 (FinDER ≥25, FinQA ≥17)
[ ] D: round9c_step_b_kg_extraction.py 실행
[ ]   → rollback Cypher 생성 확인
[ ]   → success_rate ≥ 0.80 확인
[ ] E: round9c_eval.py 실행 (150 traces)
[ ] E: round9c_summary.md 확인
[ ] E: state.json graph_beats_vector_test, hybrid_beats_graph_finder 기록
```

---

## 10. R8 → R9C 비교 해석 가이드

| 조건 | 의미 |
|---|---|
| R9C graph ac > R8 graph ac | 파이프라인 수정(ticker/formula_type)이 실제 성능 향상에 기여 |
| R9C hybrid ac > R9C graph ac (FinDER) | KG-first prompt가 interference 문제 해소 |
| R9C hybrid ac < R9C graph ac (FinDER) | KG-first prompt 불충분, 추가 개선 필요 |
| R9C FinQA graph ac > R8 FinQA graph ac | tolerance 2% + scorer_v9 실효성 확인 |
| formula_type="other" 비율 50% 이하 | formula_type 확장이 제대로 작동함 |
| graph_beats_vector_test=true | 3번째 clean held-out에서 graph 우위 재확인 |

---

## 11. Claim 경계

### 말해도 되는 것

```
Round 09C는 R8과 완전히 독립된 새 케이스 + 수정된 파이프라인으로 실행된 clean held-out이다.
R8과 비교해 다음이 변경됨:
- ticker filter (suspect 3개 방지)
- scorer tolerance 완화 (FinQA 2%)
- hybrid KG-first prompt 적용
- formula_type 확장

graph_beats_vector 결과는 R8 + R9C 두 clean held-out에서 일관성을 확인한 것임.
```

### 말하면 안 되는 것

```
R9C graph ac가 R8보다 높다면, 이것이 전부 파이프라인 개선 덕분이다.
(새 케이스 자체가 쉬울 수도 있음 — 50 케이스 비교는 통계적으로 약함)
R9C 결과가 전체 FinDER/FinQA를 대표한다.
```

---

## 12. 파일 위치 요약

| 파일 | 경로 |
|---|---|
| 이 스펙 | `codex_prompt_round9c_new_eval.md` |
| 09B 스펙 (선행) | `codex_prompt_round9b_pipeline_fix.md` |
| 09B 완료 확인 | `outputs/round9b_validation/state.json` |
| FinDER dataset | `examples/datasets/finder_full.json` |
| FinQA train | `data/github/FinQA/FinQA-main/dataset/train.json` |
| R8 사용 케이스 (제외 목록) | `outputs/round8_case_selection/finder_candidates.jsonl` |
| R8 사용 케이스 (제외 목록) | `outputs/round8_case_selection/finqa_candidates.jsonl` |
| Scorer v9 | `scripts/scorer_v9.py` (09B 산출물) |
| Ticker filter | `scripts/ticker_filter.py` (09B 산출물) |
| Hybrid KG-first prompt | `outputs/round3_dual_track_eval_prep/prompt_formatter_v3_2/prompt_v3_3_kgfirst.md` (09B 산출물) |
