# Naive Baseline Eval — Codex Execution Spec

**작성일:** 2026-05-29  
**목표:** 포트폴리오용 비교 baseline — 구조 없는 GPT 호출 vs GraphRAG  
**Claim boundary:** `portfolio_naive_baseline_comparison`  
**모델:** gpt-4o-mini (naive_v1) + gpt-4o (naive_v2)  
**총 실행:** 50케이스 × 2 methods = 100 traces  

---

## 0. 불변 규칙

```
기존 R10 traces/contracts/KG 수정 금지
새 파일로만 저장
OPENAI_API_KEY 환경변수에서만
```

---

## 1. 목적

현재 시스템(graph_neo4j_v10)과 비교할 두 가지 naive 방법 추가:

| Method | 설명 | 핵심 차이 |
|---|---|---|
| `naive_gpt4omini` | gpt-4o-mini, 전체 텍스트, 단순 프롬프트 | 구조화 프롬프트 없음, KG 없음 |
| `naive_gpt4o` | gpt-4o, 전체 텍스트, 단순 프롬프트 | 모델 업그레이드 효과 측정 |

비교 목적:
1. "구조화 GraphRAG" vs "그냥 GPT에 문서 넣기" 성능 차이
2. gpt-4o-mini → gpt-4o 모델 업그레이드 효과
3. 포트폴리오에서 "왜 KG + 구조화 프롬프트가 필요한가" 근거 제공

---

## 2. 케이스 선택

R10 케이스 중 50개 무작위 선택 (deterministic seed=42):

```python
import random
random.seed(42)

# 각 데이터셋에서 비례 선택
finder_cases = load_jsonl("outputs/round10_case_selection/finder_candidates.jsonl")
finqa_cases  = load_jsonl("outputs/round10_case_selection/finqa_candidates.jsonl")
tatqa_cases  = load_jsonl("outputs/round10_case_selection/tatqa_candidates.jsonl")

selected = (
    random.sample(finder_cases, 26) +   # 130 × 0.20 ≈ 26
    random.sample(finqa_cases,  11) +   # 56 × 0.20 ≈ 11
    random.sample(tatqa_cases,  13)     # 65 × 0.20 ≈ 13
)
# 총 50케이스
```

scorer_contracts는 R10 것 그대로 사용:
```
outputs/round10_formula_contracts/round10_scorer_contracts.jsonl
```

---

## 3. Naive 프롬프트

### System Prompt (두 method 동일)

```
You are a financial analyst assistant.
Given a financial document and a question, compute the answer and return it in JSON format.
Use only the information provided in the document. Do not use outside knowledge.

Return exactly this JSON structure:
{
  "final_answer": "<numeric result with unit, e.g. '42.3%' or '$1.2 billion'>",
  "calculation": "<brief explanation of how you calculated it>"
}
```

**의도적으로 제거된 것들:**
- 공식 타입 힌트 (formula_type)
- 반올림 규칙
- Multi-year 완성 요구사항
- YoY 계산 스텝 지시
- Arithmetic verification
- KG facts 우선 규칙

이것이 "naive" baseline의 핵심 — 구조 없이 GPT 능력만으로 풀기.

### User Prompt

```
FINANCIAL DOCUMENT:
{evidence_text}

QUESTION:
{question}
```

---

## 4. Scorer

R10과 동일한 `scorer_v9.py` 사용.  

**Naive 모델 출력 파싱 추가:**
scorer_v9의 `extract_model_answer`에 naive format 처리 추가:

```python
def extract_model_answer_naive(raw_response: str) -> dict:
    """
    Naive 모델은 {"final_answer": "...", "calculation": "..."} 형태 출력.
    기존 extract_model_answer와 달리 final_answer 단일 필드 파싱.
    """
    try:
        data = json.loads(raw_response)
        return {
            "final_answer": data.get("final_answer", ""),
            "calculation_steps": [data.get("calculation", "")],
        }
    except json.JSONDecodeError:
        # JSON 파싱 실패 시 raw text에서 숫자 추출 시도
        return {"final_answer": raw_response.strip()[:200], "calculation_steps": []}
```

---

## 5. 파일 구조

```
outputs/naive_baseline/
  case_sample.jsonl          # 선택된 50케이스 목록
  naive_gpt4omini_traces.jsonl
  naive_gpt4o_traces.jsonl
  comparison_summary.md      # 핵심 비교표
  state.json
```

---

## 6. comparison_summary.md 형식

```markdown
# Naive Baseline Comparison

## 50케이스 기준 전체

| Method | avg_ac | avg_nc | model | prompt |
|---|---:|---:|---|---|
| graph_neo4j_v10 (R10) | ? | ? | gpt-4o-mini | structured v3.4 + KG |
| naive_gpt4omini | ? | ? | gpt-4o-mini | naive |
| naive_gpt4o | ? | ? | gpt-4o | naive |

## 데이터셋별

| Dataset | graph_v10 | naive_mini | naive_4o |
|---|---:|---:|---:|
| FinDER (26) | ? | ? | ? |
| FinQA (11) | ? | ? | ? |
| TAT-QA (13) | ? | ? | ? |

## 핵심 질문 답변

1. graph_neo4j > naive_gpt4omini? → {True/False} (+{delta})
   → "KG + 구조화 프롬프트"가 "단순 GPT"보다 {X}% 포인트 높음

2. naive_gpt4o > graph_neo4j? → {True/False}
   → gpt-4o로 업그레이드해도 graph 구조 없이는 {비교}

3. naive_gpt4o > naive_gpt4omini? → +{delta}
   → 모델 업그레이드 단독 효과
```

---

## 7. state.json

```json
{
  "phase": "done",
  "round": "naive_baseline",
  "n_cases": 50,
  "model_naive_mini": "gpt-4o-mini",
  "model_naive_4o": "gpt-4o",
  "scorer_version": "v9",
  "r10_graph_ac_on_subset": ...,
  "naive_mini_ac": ...,
  "naive_4o_ac": ...,
  "graph_beats_naive_mini": ...,
  "graph_beats_naive_4o": ...,
  "model_calls": 100
}
```

---

## 8. 체크리스트

```
[ ] 50케이스 seed=42 샘플링 (FinDER 26 + FinQA 11 + TAT-QA 13)
[ ] R10 scorer_contracts에서 해당 케이스 계약 로드
[ ] naive_gpt4omini 100 traces (50케이스 × 1, gpt-4o-mini)
[ ] naive_gpt4o 50 traces (50케이스 × 1, gpt-4o)
[ ] R10 traces에서 동일 50케이스의 graph_neo4j_v10 결과 추출 (API 호출 없음)
[ ] comparison_summary.md 생성
[ ] state.json graph_beats_naive_mini, graph_beats_naive_4o 기록
```

---

## 9. 파일 위치

| 파일 | 경로 |
|---|---|
| 이 스펙 | `codex_prompt_naive_baseline.md` |
| R10 cases | `outputs/round10_case_selection/` |
| R10 scorer contracts | `outputs/round10_formula_contracts/round10_scorer_contracts.jsonl` |
| R10 traces (비교용) | `outputs/round3_eval_runs/round10_eval_20260529_170409/round10_traces.jsonl` |
| scorer v9 | `scripts/scorer_v9.py` |
