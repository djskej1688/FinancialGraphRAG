# Round 10 Multi-Dataset Eval — Codex Execution Spec

**작성일:** 2026-05-29 (R9C 결과 반영 업데이트)  
**전제:** Round 09C 완료  
**목표:** FinDER + FinQA + TAT-QA 세 데이터셋에서 200~300 clean held-out 케이스 평가  
**목표 케이스:** 300 (최소 200 — 단, 200 미달 시 저품질 데이터로 채우지 말고 새 데이터셋 탐색)  
**Claim boundary:** `clean_held_out_round10_three_dataset`  
**총 실행:** 200~300 cases × 3 methods = 600~900 traces  

### R9C에서 확인된 변경 사항 (이 스펙에 반영 완료)

| R9C 발견 | 대응 | 반영 섹션 |
|---|---|---|
| yoy_revenue_change 4/5 실패 (nc~0.05) | Prompt v3.4 — YoY 계산 스텝 명시 | Pre-work 0 |
| eps_dilution 2/2 실패 | 케이스 선택에서 제외 | Pre-work A |
| Neo4j connection reset 발생 | eval retry/resume 로직 필수 명시 | Pre-work F |
| FinQA vector > graph (20케이스, n 작음) | 100케이스에서 모니터링 지표 추가 | state.json |
| formula_type "other" 3.33% 달성 | Round 10 목표 ≤10%로 강화 | Pre-work D |

---

## Pre-work 0: Prompt v3.4 작성 (모델 호출 전 먼저 실행)

**신규 파일:** `outputs/round3_dual_track_eval_prep/prompt_formatter_v3_2/prompt_v3_4_system.md`  
`prompt_v3_3_system.md` 복사 후 아래 두 섹션 추가.

### 0.1 YoY Change 계산 스텝 (신규)

R9C에서 yoy_revenue_change 4/5 케이스가 nc~0.05로 실패함.  
모델이 `(current - prior) / prior × 100` 마지막 나눗셈 또는 ×100을 누락하는 패턴.

추가 지시 (system prompt 중 calculation_steps 섹션 다음에 삽입):

```markdown
## Year-over-Year (YoY) Change Calculation Rule

When computing year-over-year growth or change:

  YoY % = (Current Year Value − Prior Year Value) / Prior Year Value × 100

CRITICAL: Always divide by the PRIOR YEAR value, not the current year value.
CRITICAL: Express as a percentage (multiply by 100 if result is in decimal form).
CRITICAL: Label the result with the year it represents (e.g., "2023 YoY revenue growth").

Example:
  Revenue 2022: $500M, Revenue 2023: $550M
  YoY growth = (550 - 500) / 500 × 100 = 10.0%  ✓
  NOT: (550 - 500) = $50M  ✗  (this is absolute change, not YoY %)
  NOT: 550 / 500 = 1.10  ✗  (this is a ratio, not YoY %)
```

### 0.2 Hybrid 모드 KG-first 지시 (v3.3_kgfirst에서 이동)

`prompt_v3_4_system.md`는 KG-first 지시도 포함 — `prompt_v3_4_kgfirst.md` 별도 파일 불필요.  
v3.4 단일 파일로 통합:
- graph_neo4j_v10: `prompt_v3_4_system.md`
- vector_only_v10: `prompt_v3_4_system.md`
- hybrid_neo4j_v10: `prompt_v3_4_system.md` (KG-first 지시 포함됨)

**검증:** 파일 생성 후 YoY 섹션이 포함됐는지 grep으로 확인.

---

## 0. 불변 규칙

```
OPENAI_API_KEY  환경변수에서만
Neo4j           .env (python-dotenv)
neo4j_write_performed = False 기본; Pre-work E에서만 True

건드리면 안 되는 경로:
  outputs/round8_eval/
  outputs/round9a_sensitivity/
  outputs/round9b_validation/
  outputs/round9c_eval/   (09C 완료 후)

09C 완료 확인 먼저:
  outputs/round9c_eval/state.json → phase=done
```

---

## 1. 데이터 품질 원칙 (최우선)

**200개를 채우기 위해 저품질 케이스를 넣는 것은 금지.**  
케이스 수가 부족하면 수를 줄이거나, 새 데이터셋을 온라인에서 찾아서 대체.

### 1.1 케이스 품질 기준 (모든 데이터셋 공통)

아래 중 하나라도 해당하면 **해당 케이스 제외**:

| 기준 | 제외 조건 |
|---|---|
| Evidence 텍스트 | 200자 미만이거나 숫자가 2개 이하 |
| 답 | 숫자 파싱 불가, 범위 답, 복수 답 (단일 숫자 답만 허용) |
| 계산 근거 | derivation/program 없거나 단순 lookup (더하기/빼기/나누기 연산 없음) |
| Ticker | GPT가 "UNKNOWN" 반환하거나 is_valid_ticker() 실패 |
| Contract 검증 | validation_failed (재계산 불일치) |
| 중복 | 이미 사용한 source_id/filename/uid |

### 1.2 데이터 부족 시 행동 규칙

```
목표: FinDER 130 + FinQA 100 + TAT-QA 70 = 300

각 데이터셋에서 품질 기준 통과 케이스가 목표치 미달이면:
  → 미달 수량을 기록 (selection_state.json에 shortfall 필드)
  → 다른 데이터셋으로 채우지 말 것 (FinDER로 TAT-QA 부족분 대체 금지)
  → 전체가 200 미만이 될 것 같으면:
       Codex가 STOP하고 아래 후보 데이터셋 중 하나를 탐색
```

### 1.3 200 미달 시 온라인 대체 데이터셋 후보

Codex가 아래 순서로 탐색 및 다운로드 시도:

| 우선순위 | 데이터셋 | GitHub | 특징 |
|---|---|---|---|
| **1순위** | **FinanceBench** | `patronus-ai/financebench` | 전문가 큐레이션, 10-K 기반, 약 150건, 최고 품질 |
| **2순위** | **ConvFinQA** | `czyssrs/ConvFinQA` | FinQA 확장판, 3,892건, 테이블형, 단일 QA만 추출 가능 |
| **3순위** | **MultiHiertt** | `psunlpgroup/MultiHiertt` | 복잡한 다중 테이블 QA, 약 1,200건 |

탐색 순서:
1. GitHub에서 데이터셋 존재 확인 (WebSearch or curl)
2. 다운로드 가능하면 `data/github/` 폴더에 저장
3. 포맷 확인 후 케이스 선택기 작성
4. 위 품질 기준 동일하게 적용

**FinanceBench를 1순위로 추천하는 이유:**  
약 150건이지만 전문가가 직접 큐레이션한 최고 품질 데이터셋.  
10-K 기반이라 우리 파이프라인과 포맷 호환성 높고, company/ticker 필드 이미 포함.

---

## 2. 케이스 목표 수 및 fallback 규칙

| 데이터셋 | 목표 (300) | 최소 (200) | Fallback 조건 |
|---|---:|---:|---|
| FinDER | 130 | 100 | ticker 필터 후 충분히 있음 → 항상 목표치 달성 가능 |
| FinQA | 100 | 80 | 동일 |
| TAT-QA | **70** | **20** | ticker 추출 성공 케이스가 70 미만이면 있는 만큼만 사용 |

**TAT-QA가 최대 병목.** 아래 기준으로 확보 가능한 케이스 수를 먼저 측정하고:
- 70개 이상 → 목표 300 달성
- 20~69개 → TAT-QA는 있는 만큼, FinDER/FinQA로 200 이상 채움
- 20개 미만 → TAT-QA 제외, FinDER 130 + FinQA 80 = 210 (최소 충족)

**최종 케이스 수를 state.json에 `cases_total_target`과 `cases_total_actual`로 분리 기록.**

---

## 2. 사용 금지 Ticker (누적 — 09C 완료 후 업데이트)

```python
# 09C 완료 후 아래 경로에서 추가 로드
R9C_FINDER_TICKERS = {c["ticker"] for c in load_jsonl("outputs/round9c_case_selection/finder_candidates.jsonl")}
R9C_FINQA_TICKERS  = {c["ticker"] for c in load_jsonl("outputs/round9c_case_selection/finqa_candidates.jsonl")}

EXCLUDED_TICKERS = {
    # R3 test/dev (22개)
    "AMGN","APD","BXP","GM","LOW","MPC","MU","NXPI","VRSK","XEL",
    "BAC","BW","CARR","CMCSA","FOXA","HCA","KR","LND","MCO","MDLZ","MSFT","MTB",
    # R8 FinDER (30개)
    "DUK","AES","AIG","AXP","BLK","CAGR","CEG","CNP","EQR","EVRG",
    "EXPD","GNRC","LKQ","LVS","MAA","OF","ONEOK","PAYC","PTC","SBA",
    "VMC","WLTW","WMB","ZBH","GLW","EMN","RMD","LOSS","VICI","OI",
    # R8 FinQA (20개)
    "ABMD","ADI","ALLE","AMAT","AMT","ANET","APTV","AWK","CAT","CB",
    "CME","DISCA","DISH","DRE","DVN","ETR","GPN","GS","HIG","HUM",
} | R9C_FINDER_TICKERS | R9C_FINQA_TICKERS   # 09C 완료 후 추가
```

---

## 3. 파일/폴더 구조

```
outputs/round10_case_selection/
  finder_candidates.jsonl
  finqa_candidates.jsonl
  tatqa_candidates.jsonl          # 신규
  tatqa_company_ticker_map.json   # 신규: 회사명→ticker 매핑 결과
  selection_state.json

outputs/round10_formula_contracts/
  round10_scorer_contracts.jsonl
  round10_model_visible_contracts.jsonl
  generation_state.json

outputs/round10_step_b_kg/
  extraction_trace.jsonl
  kg_write_log.jsonl
  state.json
  round10_kg_rollback.cypher

outputs/round10_eval/
  state.json

outputs/round3_eval_runs/round10_eval_TIMESTAMP/
  round10_traces.jsonl
  round10_summary.md
```

---

## 4. Pre-work A: FinDER 케이스 선택 (`round10_finder_case_selector.py`)

`round9c_finder_case_selector.py` 기반. 변경점만:

- `case_id prefix`: `round10_finder`
- `target`: 130 케이스 (fallback: 100)
- `EXCLUDED_TICKERS`: 위 누적 목록
- `kg_batch`: `kg-round10-v1-YYYYMMDD`
- R8/R9C에서 사용한 `source_id`도 제외 (같은 FinDER 레코드 중복 방지)

**R9C 추가: 제외 formula_type**

케이스 선택 후 formula_type 사전 분류(GPT 1회 호출)에서 아래 타입으로 분류되면 제외:

```python
EXCLUDED_FORMULA_TYPES_PREFLIGHT = {
    "eps_dilution",       # R9C 2/2 완전 실패 — gpt-4o-mini 산술 한계
}
```

사전 분류는 contract gen (Pre-work D) 전에 가벼운 GPT 호출로 먼저 확인.  
`eps_dilution`으로 분류된 케이스는 candidate pool에서 제거하고, 다음 순위 케이스로 대체.  
`yoy_revenue_change`는 제외하지 않음 — Prompt v3.4의 YoY 스텝으로 개선 시도.

---

## 5. Pre-work B: FinQA 케이스 선택 (`round10_finqa_case_selector.py`)

`round9c_finqa_case_selector.py` 기반. 변경점만:

- `case_id prefix`: `round10_finqa`
- `target`: 100 케이스 (fallback: 80)
- R8/R9C에서 사용한 `source_filename` 제외

---

## 6. Pre-work C: TAT-QA 케이스 선택 (`round10_tatqa_case_selector.py`) ← 신규

### 6.1 데이터 소스

```
data/github/TAT-QA/TAT-QA-master/dataset_raw/tatqa_dataset_train.json
```

구조: 문서 단위 (table + paragraphs + questions)  
각 question: `uid, question, answer, derivation, answer_type, answer_from, scale`

### 6.2 케이스 필터 조건

1. `answer_type == "arithmetic"` — 계산이 있어야 graph KG가 의미 있음
2. `derivation` 비어있지 않음 (계산 근거 명시)
3. `answer`가 숫자 파싱 가능 (float)
4. `answer_from in ("table", "table-text")` — 표에서 숫자를 읽어야 함
5. `scale in ("", "percent", "million", "billion", "thousand")` — 명확한 단위
6. 회사명 → ticker 추출 가능 (아래 6.3 참조)

### 6.3 회사명 → Ticker 추출 (핵심 신규 작업)

TAT-QA 문서에는 ticker가 없으나 paragraphs에 회사명이 있음.

**Step 1: 회사명 추출**

각 문서의 `paragraphs[0].text` 또는 `table` 헤더에서 회사명 추출:
```python
def extract_company_name(doc: dict) -> str | None:
    # 첫 번째 paragraph 텍스트에서 회사명 패턴 찾기
    for para in doc["paragraphs"]:
        text = para["text"]
        # "ABC Corporation", "XYZ Inc.", "ABC Group" 등의 패턴
        match = re.search(
            r'\b([A-Z][a-zA-Z\s&,\.]+(?:Corporation|Corp\.?|Inc\.?|Ltd\.?|'
            r'Group|Holdings|Company|Co\.?|PLC|SE|AG|SA|NV))\b',
            text
        )
        if match:
            return match.group(1).strip()
    return None
```

**Step 2: 회사명 → Ticker 매핑**

GPT-4o-mini를 사용해 회사명을 ticker로 변환 (케이스당 1회, 전체 unique 회사 수만큼):

```
SYSTEM: You are a financial data assistant. 
Given a company name from a financial report, return the stock ticker symbol.
Return only the ticker (e.g., "AAPL") or "UNKNOWN" if not identifiable.
Do not guess. Only return tickers you are confident about.

USER: Company name: "{company_name}"
```

결과를 `tatqa_company_ticker_map.json`에 저장 (캐싱):
```json
{
  "Apple Inc.": "AAPL",
  "Microsoft Corporation": "MSFT",
  "Unknown Company XYZ": "UNKNOWN"
}
```

**Step 3: ticker 검증**

- `ticker == "UNKNOWN"` → skip
- `not is_valid_ticker(ticker)` → skip (ticker_filter.py 사용)
- `ticker in EXCLUDED_TICKERS` → skip

### 6.4 evidence_text 구성

```python
def build_tatqa_evidence(doc: dict) -> str:
    # 테이블 linearize (TAT-QA table은 list of lists)
    table_text = "\n".join(
        "\t".join(str(cell) for cell in row)
        for row in doc["table"]["table"]
    )
    # 관련 paragraphs
    para_text = "\n".join(p["text"] for p in doc["paragraphs"])
    return f"TABLE\n{table_text}\n\nCONTEXT\n{para_text}"
```

### 6.5 케이스 목표 및 fallback

- **목표: 70케이스**
- **Fallback: 있는 만큼 (최소 20)**
- `ticker == "UNKNOWN"` 비율이 50% 초과 시 → 경고 로그, 있는 만큼만 진행

### 6.6 출력 형식 (`tatqa_candidates.jsonl`)

```json
{
  "case_id": "round10_tatqa_001_XXXXXXXX",
  "split": "round10_test",
  "source_dataset": "TAT-QA",
  "source_uid": "<question uid>",
  "source_doc_uid": "<doc uid>",
  "ticker": "AAPL",
  "company": "Apple Inc.",
  "answer_type": "arithmetic",
  "answer_from": "table-text",
  "derivation": "(100 + 200) / 3",
  "scale": "million",
  "evidence_text": "TABLE\n...\n\nCONTEXT\n...",
  "question": "...",
  "expected_answer": "100.0",
  "expected_answer_numeric": 100.0,
  "years": [],
  "quality_score": 10.2,
  "curation_round": "10",
  "kg_batch": "kg-round10-v1-YYYYMMDD",
  "created_at": "...",
  "anti_cherrypick_notes": "Selected by deterministic scoring; arithmetic answer_type only."
}
```

`case_id`: `f"round10_tatqa_{idx:03d}_{hashlib.sha256(source_uid.encode()).hexdigest()[:8]}"`

---

## 7. Pre-work D: Formula Contract 생성 (`round10_formula_contract_gen.py`)

`round9c_formula_contract_gen.py` 기반. TAT-QA 처리 추가.

### 7.1 FinDER/FinQA

09C와 동일 — `round9c_formula_contract_gen.py` 로직 재사용.

### 7.2 TAT-QA 계약 생성 (신규)

FinQA의 `finqa_program` 파서와 유사하게 `derivation` 필드 파싱:

```python
def build_tatqa_contract(case: dict) -> dict | None:
    """
    TAT-QA arithmetic 케이스에서 scorer + model_visible contract 생성.
    derivation 예시: "(100 + 200) / 3", "100 - 50 + 20"
    """
    derivation = case.get("derivation", "").strip()
    expected = case.get("expected_answer_numeric")
    scale = case.get("scale", "")
    
    if not derivation or expected is None:
        return None
    
    # scale 단위 결정
    unit_map = {
        "percent": "percentage",
        "million": "USD_millions",
        "billion": "USD_billions",
        "thousand": "USD_thousands",
        "": "amount",
    }
    unit = unit_map.get(scale, "amount")
    
    # derivation에서 숫자 추출 → source_fact_numbers
    numbers = re.findall(r'\d+\.?\d*', derivation)
    source_facts = [
        {"metric": f"operand_{i}", "value": float(n), "unit": unit}
        for i, n in enumerate(numbers)
    ]
    
    # target_slot: 최종 답 1개
    tolerance = max(0.5, abs(expected) * 0.02)   # scorer_v9 기준 (2%)
    
    return {
        "case_id": case["case_id"],
        "formula_type": "tatqa_arithmetic",    # 신규 formula_type
        "source_fact_numbers": source_facts,
        "target_slots": [{
            "target_slot_name": "final_result",
            "expected_value": expected,
            "unit": unit,
            "tolerance": tolerance,
            "required_for_answer": True,
            "derived_or_source": "derived",
        }],
        "contract_status": "valid",
    }
```

### 7.3 scorer_v9 업데이트: tatqa_arithmetic 추가

`scorer_v9.py`의 `compute_tolerance`에 타입 추가:

```python
def compute_tolerance(expected_value: float, formula_type: str = "") -> float:
    if formula_type in ("finqa_program", "tatqa_arithmetic"):
        return max(0.5, abs(expected_value) * 0.02)
    return max(0.1, abs(expected_value) * 0.005)
```

### 7.4 formula_type="other" 목표 비율

- FinDER: `other` 비율 **≤ 10%** (R9C 3.33% 달성 → R10 목표 강화)
- FinQA: `finqa_program` 100% (변경 없음)
- TAT-QA: `tatqa_arithmetic` 100% (변경 없음)
- FinDER `other` 비율 10% 초과 시: 경고 로그 기록, eval은 진행하되 summary에 명시

---

## 8. Pre-work E: Step B KG 추출 (`round10_step_b_kg_extraction.py`)

`round9c_step_b_kg_extraction.py`와 동일한 패턴.

- `BATCH_ID = f"kg-round10-v1-{TODAY}"`
- 입력: round10 scorer contracts + 세 데이터셋 candidates
- 케이스 수가 300으로 늘어 KG write 볼륨 증가 (~1,800 facts 예상)
- rollback Cypher 생성 필수

**R9C 추가: Neo4j 연결 재시도 로직 필수**

R9C에서 eval 중 Neo4j connection reset 발생 (한 번 중단 후 resume).  
KG 추출 단계에서 아래 패턴 반드시 적용:

```python
MAX_RETRIES = 3
RETRY_DELAY = 5  # seconds

for attempt in range(MAX_RETRIES):
    try:
        result = session.run(query, **params)
        break
    except (ServiceUnavailable, SessionExpired) as e:
        if attempt == MAX_RETRIES - 1:
            raise
        time.sleep(RETRY_DELAY * (attempt + 1))
        # 드라이버 재연결
        driver = GraphDatabase.driver(NEO4J_URI, auth=(NEO4J_USER, NEO4J_PASS))
```

이미 write된 obs는 `kg_write_log.jsonl`에 기록하여 재시작 시 skip.

---

## 9. Main Eval: `round10_eval.py`

`round9c_eval.py` 기반. 변경점:

```python
METHODS = ["vector_only_v10", "graph_neo4j_v10", "hybrid_neo4j_v10"]
ROUND = "round10"
PROMPT_VERSION = "v3.4"   # prompt_v3_4_system.md (YoY fix 포함)
SCORING_VERSION = "v9"    # scorer_v9 그대로 (tatqa_arithmetic tolerance 추가)
CLAIM_BOUNDARY = "clean_held_out_round10_three_dataset"
```

TAT-QA 케이스도 동일한 3-method eval 적용.

**R9C 추가: eval retry/resume 로직 필수**

R9C에서 Neo4j connection reset 발생 → 동일 run-dir에서 resume 가능하게 설계.

```python
# eval 시작 시 기존 traces 로드
completed_ids = set()
if TRACES_PATH.exists():
    for line in TRACES_PATH.read_text().splitlines():
        t = json.loads(line)
        completed_ids.add((t["case_id"], t["method"]))

# 이미 완료된 (case_id, method) 조합은 skip
for case in cases:
    for method in METHODS:
        if (case["case_id"], method) in completed_ids:
            continue
        # ... run eval
```

Neo4j 쿼리도 KG 추출과 동일한 retry 패턴 적용 (MAX_RETRIES=3).

---

## 10. Round 10 Summary 형식

```markdown
# Round 10 Summary

## Overall (N cases)

| Method | avg_ac | avg_nc | n_cases |
...

## By Dataset

| Dataset | Method | avg_ac | avg_nc | n_cases |
| FinDER  | graph_neo4j_v10 | ... | ... | 130 |
| FinQA   | graph_neo4j_v10 | ... | ... | 100 |
| TAT-QA  | graph_neo4j_v10 | ... | ... | 70 |
...

## formula_type Distribution

| formula_type | count | avg_ac (graph) |
...

## R8 vs R9C vs R10 Graph AC Trend

| Dataset | R8 | R9C | R10 |
...

## TAT-QA: Company→Ticker 추출 품질
- GPT 호출 수: N
- UNKNOWN 비율: X%
- 실제 사용 케이스: N

## Claim Limit
...
```

---

## 11. state.json

```json
{
  "round": "round10",
  "phase": "done",
  "kg_batch": "kg-round10-v1-YYYYMMDD",
  "scoring_version": "v9",
  "claim_boundary": "clean_held_out_round10_three_dataset",
  "cases_target": 300,
  "cases_total_actual": ...,
  "cases_finder": ...,
  "cases_finqa": ...,
  "cases_tatqa": ...,
  "tatqa_ticker_extraction_rate": ...,
  "formula_type_other_pct_finder": ...,
  "runs_total": ...,
  "runs_completed": ...,
  "test_ac_vector": ...,
  "test_ac_graph": ...,
  "test_ac_hybrid": ...,
  "test_ac_graph_finder": ...,
  "test_ac_graph_finqa": ...,
  "test_ac_graph_tatqa": ...,
  "graph_beats_vector_test": ...,
  "hybrid_beats_graph_finder": ...,
  "yoy_fix_applied": true,
  "eps_dilution_excluded": true,
  "finqa_vector_beats_graph": ...,
  "prompt_version": "v3.4",
  "started_at": "...",
  "completed_at": "..."
}
```

---

## 12. 실행 순서 및 체크리스트

```
[ ] 09C 완료 확인 (outputs/round9c_eval/state.json phase=done)
[ ] Pre-work 0: prompt_v3_4_system.md 작성 (YoY 계산 스텝 포함 확인)
[ ] A: round10_finder_case_selector.py → 목표 130, fallback 100
[ ]   → eps_dilution preflight 분류 실행, 해당 케이스 제외
[ ] B: round10_finqa_case_selector.py  → 목표 100, fallback 80
[ ] C: round10_tatqa_case_selector.py
[ ]   → company→ticker GPT 매핑 실행
[ ]   → tatqa_company_ticker_map.json 저장 (캐싱)
[ ]   → 확보 가능 케이스 수 확인 → 70미만이면 있는 만큼
[ ]   → selection_state.json에 tatqa_available 기록
[ ] D: round10_formula_contract_gen.py
[ ]   → FinDER other% 확인 (≤50% 목표)
[ ]   → FinQA: finqa_program 100%
[ ]   → TAT-QA: tatqa_arithmetic 100%
[ ] E: round10_step_b_kg_extraction.py
[ ]   → rollback Cypher 생성 확인
[ ]   → success_rate ≥ 0.80
[ ] F: round10_eval.py (N×3 traces)
[ ] F: round10_summary.md 확인
[ ] F: state.json cases_total_actual 기록 (200~300 범위 확인)
```

---

## 13. 해석 가이드

| 결과 패턴 | 의미 |
|---|---|
| R10 graph ac > R9C graph ac (전체) | 더 많은 케이스에서도 graph 우위 유지 → 강한 일반화 신호 |
| FinDER graph ac > R9C (0.43) | Prompt v3.4 YoY fix + eps_dilution 제거 효과 |
| FinQA graph ac > vector ac (100케이스) | R9C 역전(vector 0.90)이 n=20 노이즈였음 확인 |
| FinQA vector ac > graph ac (100케이스) | KG가 FinQA에 실제로 도움이 안 됨 → KG 추출 방식 재검토 필요 |
| TAT-QA graph ac > vector ac | 세 번째 데이터셋에서도 KG 접근법 유효 확인 |
| TAT-QA graph ac ≤ vector ac | 테이블 구조 데이터에 KG가 추가 가치 없음 → ticker/테이블 구조 문제 |
| hybrid > graph (FinDER) | v3.4 prompt가 interference 완화 → `hybrid_beats_graph_finder=true` |
| hybrid ≤ graph (FinDER) | interference 근본 원인 미해결 → 입력 구조 재설계 필요 (Round 11) |
| yoy_revenue_change ac > 0.5 | Prompt v3.4 YoY 스텝이 실효 있음 |
| yoy_revenue_change ac ≤ 0.3 | 프롬프트 패치 불충분 → 계산 템플릿 추가 필요 |
| formula_type other% ≤ 10% | contract gen 품질 목표 달성 |

---

## 14. Claim 경계

### 말해도 되는 것

```
Round 10은 3개 데이터셋(FinDER/FinQA/TAT-QA)에서 200~300개 clean held-out 케이스로
실행된 가장 넓은 범위의 벤치마크다.
- 세 데이터셋 모두에서 graph vs vector 비교 가능
- Round 08 → 09C → 10 세 번의 clean held-out에서 graph_beats_vector 패턴 확인
```

### 말하면 안 되는 것

```
300케이스가 전체 FinDER/FinQA/TAT-QA를 대표한다.
TAT-QA ticker 추출 성공 케이스만으로 TAT-QA 전체 성능을 주장한다.
```

---

## 15. 파일 위치

| 파일 | 경로 |
|---|---|
| 이 스펙 | `codex_prompt_round10_300case_eval.md` |
| 선행 스펙 | `codex_prompt_round9c_new_eval.md` |
| TAT-QA 데이터 | `data/github/TAT-QA/TAT-QA-master/dataset_raw/tatqa_dataset_train.json` |
| FinDER | `examples/datasets/finder_full.json` |
| FinQA train | `data/github/FinQA/FinQA-main/dataset/train.json` |
| Scorer v9 | `scripts/scorer_v9.py` |
| Ticker filter | `scripts/ticker_filter.py` |
| Prompt v3.4 (신규, YoY fix) | `outputs/round3_dual_track_eval_prep/prompt_formatter_v3_2/prompt_v3_4_system.md` |
| Hybrid KG-first prompt (v3.3, R9C용) | `outputs/round3_dual_track_eval_prep/prompt_formatter_v3_2/prompt_v3_3_kgfirst.md` |
