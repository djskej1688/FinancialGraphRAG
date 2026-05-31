# Round 08 Full Pipeline Expansion — Codex Execution Spec

**작성일:** 2026-05-29  
**목표:** FinDER 신규 케이스 30개 + FinQA 파일럿 20개로 clean held-out benchmark 구축 및 평가  
**Claim boundary:** `clean_held_out_round8_finder_finqa_pilot`  
**총 실행:** 50 cases × 3 methods = 150 traces  

---

## 0. 개요 및 Phase 구조

Round 08은 기존 25케이스(round3_test/dev)와 완전히 독립된 새 테스트셋을 구성한다.  
기존 eval loop(round7_eval.py) 구조를 재사용하되, 케이스 생성·계약 자동생성·KG 추출을 새로 한다.

| Phase | 스크립트 | 출력 |
|---|---|---|
| **A** | `round8_finder_case_selector.py` | 30개 FinDER 케이스 |
| **B** | `round8_finqa_case_selector.py` | 20개 FinQA 케이스 |
| **C** | `round8_formula_contract_gen.py` | scorer + model_visible contracts |
| **D** | `round8_step_b_kg_extraction.py` | Neo4j KG facts (새 배치) |
| **E** | `round8_eval.py` | 150 traces + summary |

실행 순서: A → B → C → D → E (순차 필수)

---

## 1. 전제 조건 및 불변 규칙

```
OPENAI_API_KEY          환경변수에서만 읽기 (절대 .env 하드코드 금지)
Neo4j credentials       .env (python-dotenv)
neo4j_write_performed   False 기본; Pre-work D에서만 True (배치 기록 필수)

건드리면 안 되는 경로:
  outputs/round3_eval_runs/locked_test_v3_2_track_b_20260528_145253/
  outputs/round3_eval_harness/formula_contract_v3_2_test_split/
  outputs/round7_eval/   (기존 round7 결과)

사용 금지 Ticker (기존 test + dev):
  AMGN, APD, BXP, GM, LOW, MPC, MU, NXPI, VRSK, XEL,
  BAC, BW, CARR, CMCSA, FOXA, HCA, KR, LND, MCO, MDLZ, MSFT, MTB
```

---

## 2. 파일/폴더 구조

```
outputs/round8_case_selection/
  finder_candidates.jsonl          # 30개 FinDER 케이스
  finqa_candidates.jsonl           # 20개 FinQA 케이스
  selection_state.json

outputs/round8_formula_contracts/
  round8_scorer_contracts.jsonl    # scorer_only_target_slot_contract
  round8_model_visible_contracts.jsonl  # model_visible_contract
  generation_state.json
  generation_trace.jsonl           # GPT 호출 로그 (FinDER)
  validation_report.jsonl          # 계약 검증 결과

outputs/round8_step_b_kg/
  extraction_trace.jsonl
  kg_write_log.jsonl
  failed_extractions.jsonl
  state.json

outputs/round8_eval/
  state.json

outputs/round3_eval_runs/round8_eval_TIMESTAMP/
  round8_traces.jsonl
  round8_summary.md
```

---

## 3. Pre-work A: FinDER 케이스 선택 (`round8_finder_case_selector.py`)

### 3.1 입력

- `examples/datasets/finder_full.json` (5,703 records)
- 필드: `id, category, reasoning_type, text (evidence), question, expected_answer`

### 3.2 필터 조건 (순서대로 적용)

1. `category == "Financials"`
2. `reasoning_type in ["Calculation", "Compositional", "Subtract", "Subtraction"]`
   - normalize: "Subtract" → "Subtraction"
3. `len(evidence_text) >= 200` (충분한 증거 텍스트)
4. `len(question) >= 20`
5. `ticker 추출 가능` (아래 규칙)
6. `ticker not in EXCLUDED_TICKERS` (섹션 1의 금지 목록)
7. `ticker not in tickers_already_selected` (같은 ticker를 두 번 뽑지 않음 — ticker당 최대 1개)

### 3.3 Ticker 추출 규칙

`round3_case_factory.py`의 로직을 재사용:
- evidence_text 첫 줄에서 회사명 추출
- 기존 ticker 매핑 테이블 참조 (scripts/round3_case_factory.py에 있는 COMPANY_TO_TICKER 또는 유사 매핑)
- 추출 실패 시 해당 케이스 skip (ticker_extractable=False 로그)

**대안 방법**: question/expected_answer 텍스트에서 "for [TICKER]" 패턴 찾기.  
두 방법 모두 실패 시 skip.

### 3.4 품질 스코어링

`round3_case_factory.py`와 동일한 품질 스코어링 사용:
- `required_fact_count` (evidence에서 추출 가능한 숫자 패턴 수 × 가중치)
- `question_specificity` (숫자 언급, 연도 언급, 비율 언급)
- 중복 내용 패널티

### 3.5 케이스 선택

- 품질 스코어 내림차순 정렬
- 동점 시: `case_id_hash` 기준 (deterministic, anti-cherrypick)
- 상위 30개 선택
- 같은 ticker는 1개만 (ticker당 최고점수 1개)

### 3.6 출력 형식 (`finder_candidates.jsonl`)

각 레코드:
```json
{
  "case_id": "round8_finder_001_XXXXXXXX",
  "split": "round8_test",
  "source_dataset": "FinDER",
  "source_id": "<원래 finder id>",
  "ticker": "AAPL",
  "company": "Apple Inc.",
  "category": "Financials",
  "reasoning_type": "Calculation",
  "evidence_text": "...",
  "question": "...",
  "expected_answer": "...",
  "years": [2022, 2023],
  "quality_score": 15.3,
  "curation_round": "08",
  "kg_batch": "kg-round8-v1-YYYYMMDD",
  "created_at": "...",
  "anti_cherrypick_notes": "Selected by deterministic quality scoring; not selected by observed model outcome."
}
```

`case_id` 생성: `f"round8_finder_{idx:03d}_{hashlib.sha256(source_id.encode()).hexdigest()[:8]}"`

### 3.7 state.json

```json
{
  "phase": "A_done",
  "dataset": "FinDER",
  "total_input": 5703,
  "after_category_filter": ...,
  "after_reasoning_filter": ...,
  "after_ticker_filter": ...,
  "after_dedup_ticker": ...,
  "selected": 30,
  "selected_tickers": [...],
  "output": "outputs/round8_case_selection/finder_candidates.jsonl",
  "completed_at": "..."
}
```

---

## 4. Pre-work B: FinQA 케이스 선택 (`round8_finqa_case_selector.py`)

### 4.1 입력

- `data/github/FinQA/FinQA-main/dataset/train.json` (6,251 records)
- 필드: `pre_text, post_text, table_ori, table, filename, qa`
- `qa` 필드: `question, answer, explanation, program, ops`

### 4.2 필터 조건

1. `qa.answer`가 숫자 파싱 가능 (float or percent string → float)
   - 예: "27.5%", "0.275", "1234.5" 모두 허용
   - "Yes", "No", multi-word answers 제외
2. `qa.program` 존재 및 비어있지 않음 (명시적 연산 단계 필요)
3. `program`이 단순 조회가 아닌 계산 포함 (`divide`, `multiply`, `subtract`, `add` 중 하나 이상)
4. Ticker 추출 가능: `filename.split("/")[0]` → ticker 후보
   - STOP_TICKERS에 포함되면 skip (round3_case_factory.py의 STOP_TICKERS 참조)
5. `ticker not in EXCLUDED_TICKERS`

### 4.3 케이스 품질 스코어

- program complexity (연산 단계 수, 많을수록 고점)
- answer_magnitude_reasonable: 0.001 ~ 1000 범위 (너무 크거나 작은 값 패널티)
- pre_text + post_text 길이 충분 (컨텍스트가 있어야 vector_only도 풀 수 있음)

### 4.4 케이스 선택

- 품질 내림차순 상위 20개
- ticker당 최대 1개 (EXCLUDED_TICKERS 외에도 round8_finder로 이미 선택된 ticker 제외)
- Deterministic (hash tiebreaker)

### 4.5 출력 형식 (`finqa_candidates.jsonl`)

```json
{
  "case_id": "round8_finqa_001_XXXXXXXX",
  "split": "round8_test",
  "source_dataset": "FinQA",
  "source_filename": "ADI/2009/page_49.pdf",
  "ticker": "ADI",
  "company": "Analog Devices",
  "category": "Financials",
  "reasoning_type": "Calculation",
  "evidence_text": "<linearized_table + pre_text + post_text>",
  "table_ori": [...],
  "question": "...",
  "expected_answer": "<qa.answer as string>",
  "expected_answer_numeric": 27.5,
  "program": "divide(#0, #1)",
  "ops": [...],
  "years": [...],
  "quality_score": 12.1,
  "curation_round": "08",
  "kg_batch": "kg-round8-v1-YYYYMMDD",
  "created_at": "...",
  "anti_cherrypick_notes": "Selected by deterministic quality scoring; not selected by observed model outcome."
}
```

**evidence_text 구성 규칙 (FinQA)**:
```
table_ori 행을 탭 구분 텍스트로 linearize + "\n\n" + pre_text 문장 join + "\n\n" + post_text 문장 join
```

`case_id` 생성: `f"round8_finqa_{idx:03d}_{hashlib.sha256(source_filename.encode()).hexdigest()[:8]}"`

---

## 5. Pre-work C: Formula Contract 자동 생성 (`round8_formula_contract_gen.py`)

### 5.1 목적

각 케이스에 대해 두 가지 계약 생성:
- `scorer_only_target_slot_contract`: 채점기가 사용 (expected_values 포함)
- `model_visible_contract`: 모델이 보는 힌트 (expected_values 제외, 형식/단위만)

### 5.2 FinDER 케이스 계약 생성 (GPT-assisted)

**GPT 프롬프트 (`MODEL = "gpt-4o-mini"`):**

```
SYSTEM:
You are a financial formula contract extractor.
Given a financial question, evidence text, and expected answer, extract:
1. formula_type: one of [gross_margin, operating_margin, net_margin, diluted_eps_and_yoy_change,
   continuing_ops_margin, operating_vs_net_margin, workforce_ratio, tpo_segment_gross_margin,
   net_margin_and_nonop_impact, other]
2. source_fact_numbers: list of {metric, year, value (float), unit}
   - Extract ONLY values explicitly present in evidence_text
   - DO NOT include values from expected_answer
3. target_slots: list of {
     target_slot_name, expected_value (float, computed from source facts), 
     unit (percentage/USD_millions/USD_thousands/USD_per_share/ratio),
     tolerance (reasonable: 0.1 for %, 5.0 for large USD, 0.05 for per-share),
     required_for_answer (true/false),
     acceptable_equivalent_forms,
     derived_or_source ("derived" or "source"),
     years (list of int)
   }

Return JSON only. No explanation.

USER:
QUESTION: {question}
EVIDENCE_TEXT: {evidence_text[:3000]}
EXPECTED_ANSWER: {expected_answer[:1500]}
```

**검증 단계:**
- 각 target_slot의 expected_value를 source_fact_numbers 값으로 재계산
- 재계산값 vs GPT가 제시한 expected_value 비교 → tolerance 이내면 OK
- 재계산 불가능 (formula 모호) → `contract_status: validation_failed`, skip
- 검증 통과 케이스만 최종 포함

**재시도**: 검증 실패 시 1회 retry (temperature=0.1)

### 5.3 FinQA 케이스 계약 생성 (Program Parser)

FinQA는 `program` 필드에 명시적 연산이 있으므로 GPT 불필요.

**Program 파싱 규칙:**

```
program 예시: "divide(2.5, 1.6), divide(#0, 1.6)"

ops 필드 사용 (있으면): 각 operation의 operands와 result
없으면 직접 파싱:
  - table_ori에서 숫자값 추출 (행/열 인덱스로 참조)
  - pre_text/post_text에서 언급된 숫자 추출
  - 최종 answer = program evaluation 결과
```

**Contract 구성:**
- `formula_type = "finqa_program"` (새 타입)
- `source_fact_numbers`: program의 leaf operands (숫자 리터럴 or table 셀 값)
- `target_slots`: 최종 answer 1개
  - `expected_value = float(qa.answer)` (% 표기면 그대로 float 변환)
  - `unit`: "percentage" if answer ends with "%" else "amount"
  - `tolerance = max(0.1, abs(expected_value) * 0.005)` (0.5% 상대 오차)

### 5.4 model_visible_contract 생성

scorer_only_contract에서 expected_value 제거:
```json
{
  "case_id": "...",
  "formula_type": "gross_margin",
  "required_outputs": ["gross_margin_2023", "gross_margin_2022"],
  "output_units": {"gross_margin_2023": "percentage", "gross_margin_2022": "percentage"},
  "output_format_hints": "Express as percentage to 1 decimal place."
}
```

### 5.5 실패 처리

- `contract_status: validation_failed` 케이스는 eval에서 제외
- 최소 통과 기준: FinDER 25/30, FinQA 17/20 이상
- 미달 시 selection 풀을 더 넓혀서 재선택 필요 (Codex가 판단)

### 5.6 generation_state.json

```json
{
  "phase": "C_done",
  "finder_total": 30,
  "finder_contract_ok": 27,
  "finder_validation_failed": 3,
  "finqa_total": 20,
  "finqa_contract_ok": 19,
  "finqa_validation_failed": 1,
  "total_eval_ready": 46,
  "scorer_contracts": "outputs/round8_formula_contracts/round8_scorer_contracts.jsonl",
  "model_visible_contracts": "outputs/round8_formula_contracts/round8_model_visible_contracts.jsonl",
  "completed_at": "..."
}
```

---

## 6. Pre-work D: Step B KG 추출 (`round8_step_b_kg_extraction.py`)

`step_b_targeted_kg_extraction.py`와 동일한 패턴. 기존 코드 상당 부분 재사용 가능.

### 6.1 입력

- `outputs/round8_formula_contracts/round8_scorer_contracts.jsonl`
- `outputs/round8_case_selection/finder_candidates.jsonl`
- `outputs/round8_case_selection/finqa_candidates.jsonl`

### 6.2 배치 ID

```python
TODAY = date.today().strftime("%Y%m%d")
BATCH_ID = f"kg-round8-v1-{TODAY}"
```

### 6.3 추출 로직

각 케이스의 `scorer_contract.source_fact_numbers` 리스트를 기준으로:
- 각 fact: `{metric, year, value, unit}`
- evidence_text에서 해당 값이 실제로 있는지 확인 (exact quote 검증)
- Neo4j에 `LLMObservation` 노드 생성:
  ```
  (t:Ticker {symbol: ticker})-[:HAS_OBSERVATION]->(obs:LLMObservation {
    batch_id: BATCH_ID,
    case_id: case_id,
    metric: metric,
    year: year,
    value: value,
    unit: unit,
    validation_status: "pending",
    evidence_source: evidence_quote (50자 이내 exact match)
  })
  ```

### 6.4 안전 절차 (neo4j_write_performed = True)

- write 전: batch_id로 기존 동일 배치 존재 여부 확인 (있으면 skip)
- write 후: `kg_write_log.jsonl`에 각 obs 기록
- rollback Cypher 파일 생성: `round8_kg_rollback.cypher`
  ```cypher
  MATCH (obs:LLMObservation {batch_id: 'kg-round8-v1-YYYYMMDD'})
  DETACH DELETE obs;
  ```
- 실패 obs는 `failed_extractions.jsonl`에 기록
- 전체 실패율 > 20%이면 eval 진행 중단하고 오류 리포트

### 6.5 상태 추적

```json
{
  "phase": "D_done",
  "batch_id": "kg-round8-v1-20260529",
  "cases_total": 46,
  "facts_targeted": 280,
  "facts_written": 270,
  "facts_failed": 10,
  "write_success_rate": 0.964,
  "rollback_file": "outputs/round8_step_b_kg/round8_kg_rollback.cypher",
  "completed_at": "..."
}
```

---

## 7. Main Eval: `round8_eval.py`

### 7.1 기반

`round7_eval.py`를 기반으로 수정. 거의 동일한 구조.

### 7.2 상수

```python
METHODS = ["vector_only_v8", "graph_neo4j_v8", "hybrid_neo4j_v8"]
MODEL = "gpt-4o-mini"
ROUND = "round8"
KG_BATCH = "kg-round8-v1-YYYYMMDD"  # step_b_kg_state에서 읽음
PROMPT_VERSION = "v3.3"  # prompt_v3_3_system.md 재사용
SCORING_VERSION = "v7_no_faith_gate"
CLAIM_BOUNDARY = "clean_held_out_round8_finder_finqa_pilot"
```

### 7.3 케이스 로딩

```python
# finder + finqa 합쳐서 eval_ready 케이스만 로드
finder_cases = [c for c in load_jsonl(FINDER_CANDIDATES) 
                if case_has_valid_contract(c["case_id"], scorer_contracts)]
finqa_cases  = [c for c in load_jsonl(FINQA_CANDIDATES)
                if case_has_valid_contract(c["case_id"], scorer_contracts)]
all_cases = finder_cases + finqa_cases  # 예: 27 + 19 = 46
```

### 7.4 Scorer 로직

`round7_eval.py`의 scorer v7 그대로:
- `ans = numeric_ok and fmt and calc` (faith gate 없음)
- `rfr` (required_fact_recall)은 standalone metric으로 기록만
- FinQA 케이스는 `formula_type="finqa_program"` — target_slots 1개이므로 기존 로직 그대로 작동

### 7.5 Prompt 구성

`round7_eval.py`의 `build_prompt` 재사용:
- `vector_only_v8`: TEXT_CONTEXT (evidence_text)
- `graph_neo4j_v8`: GRAPH_FACTS_TABLE (KG facts from Neo4j)
- `hybrid_neo4j_v8`: TEXT_CONTEXT + GRAPH_FACTS_TABLE
- system prompt: `prompt_v3_3_system.md` 그대로

### 7.6 Trace 형식

기존 round7 trace 형식 + 추가 필드:
```json
{
  "source_dataset": "FinDER",   // or "FinQA"
  "formula_type": "gross_margin",
  ...기존 필드...
}
```

### 7.7 출력

- `outputs/round3_eval_runs/round8_eval_TIMESTAMP/round8_traces.jsonl`
- `outputs/round3_eval_runs/round8_eval_TIMESTAMP/round8_summary.md`
- `outputs/round8_eval/state.json`

### 7.8 Summary 형식

```markdown
# Round 08 Summary

## Overall (all cases)
| Method | avg_ac | n_cases |
...

## By Dataset
| Dataset | Method | avg_ac | n_cases |
| FinDER  | graph_neo4j_v8 | ... | 27 |
| FinQA   | graph_neo4j_v8 | ... | 19 |
...

## Claim Limit
Round 08 is a clean held-out benchmark:
- Cases selected from unused FinDER + FinQA records (no test-time cherry-picking)
- Formula contracts auto-generated and validated (not hand-tuned)
- KG extraction performed on new batch (kg-round8-v1-*)
- graph_beats_vector: {True/False}
```

---

## 8. Round 08 state.json 최종 형식

```json
{
  "round": "round8",
  "phase": "done",
  "kg_batch": "kg-round8-v1-YYYYMMDD",
  "prompt_version": "v3.3",
  "scoring_version": "v7_no_faith_gate",
  "claim_boundary": "clean_held_out_round8_finder_finqa_pilot",
  "cases_finder": 27,
  "cases_finqa": 19,
  "cases_total": 46,
  "runs_total": 138,
  "runs_completed": 138,
  "runs_failed": [],
  "test_ac_vector": ...,
  "test_ac_graph": ...,
  "test_ac_hybrid": ...,
  "test_ac_graph_finder": ...,
  "test_ac_graph_finqa": ...,
  "graph_beats_vector_test": ...,
  "started_at": "...",
  "completed_at": "..."
}
```

---

## 9. Sanity Run (선택 권장)

Pre-work E (optional, 권장): 5개 케이스 × 3 methods = 15 traces 사전 실행

선택 기준:
- FinDER 3개 (다양한 formula_type)
- FinQA 2개 (program-based)

Pass 조건:
- 최소 5/15 ac ≥ 1.0 (20% 이상)
- graph_neo4j_v8 ac >= vector_only_v8 ac (5케이스 기준)

실패 시: 계약 생성 오류 또는 KG 추출 오류 진단 후 재실행

---

## 10. 실행 체크리스트

```
[ ] A: finder_candidates.jsonl 생성 완료 (30개)
[ ] B: finqa_candidates.jsonl 생성 완료 (20개)
[ ] C: formula contracts 생성 완료 (FinDER ≥25, FinQA ≥17 pass)
[ ] C: validation_report.jsonl 확인 (실패 케이스 원인 기록)
[ ] D: Neo4j write 전 rollback Cypher 파일 생성 확인
[ ] D: kg_write_log.jsonl 확인 (success_rate ≥ 0.80)
[ ] E (optional): sanity 5케이스 통과
[ ] Main: round8_eval.py 실행 (TIMESTAMP 폴더 생성)
[ ] Main: round8_summary.md 확인
[ ] Main: state.json에 graph_beats_vector_test 기록
```

---

## 11. Claim 경계

### 말해도 되는 것

```
Round 08은 기존 eval 케이스와 완전히 독립된 clean held-out benchmark다.
- FinDER 신규 30 + FinQA 신규 20 케이스
- Formula contracts 자동 생성 (hand-tuning 없음)
- graph_beats_vector: {결과}
- FinDER vs FinQA 성능 차이: {결과}
- 기존 round7(0.90)이 새 test에서 얼마나 일반화되는지 첫 측정
```

### 말하면 안 되는 것

```
Round 08 결과만으로 GraphRAG가 일반적으로 우수하다.
(50케이스 pilot — 통계적 유의성 제한)
FinQA 20케이스가 전체 FinQA 성능을 대표한다.
```

---

## 12. 다음 단계 (Round 09 후보)

| 작업 | 내용 | 전제 |
|---|---|---|
| TAT-QA 통합 | 16,552 Q&A, 다양한 answer_type | ticker 매핑 필요 (company명 → ticker) |
| FinQA 전체 | 1,147 test cases 전수 평가 | Round 08 FinQA 파일럿 성공 후 |
| Model upgrade | gpt-4o-mini → gpt-4o | 산술 안정성 향상 |
| Multi-sample voting | 3× run + majority vote | vector instability 완화 |
| Partial credit scoring | binary ac → slot fraction | tnr 기반 세분화 |

---

## 13. 파일 위치 요약

| 파일 | 경로 |
|---|---|
| 이 스펙 | `codex_prompt_round8_pipeline_expansion.md` |
| FinDER dataset | `examples/datasets/finder_full.json` |
| FinQA train | `data/github/FinQA/FinQA-main/dataset/train.json` |
| 기존 case factory | `scripts/round3_case_factory.py` (ticker 추출 참조) |
| 기존 Step B | `scripts/step_b_targeted_kg_extraction.py` (KG 추출 참조) |
| 기존 eval | `scripts/round7_eval.py` (eval loop 참조) |
| Prompt v3.3 | `outputs/round3_dual_track_eval_prep/prompt_formatter_v3_2/prompt_v3_3_system.md` |
| 기존 scorer contracts | `outputs/round3_eval_harness/formula_contract_v3_2_test_split/test_scorer_contracts.jsonl` |
