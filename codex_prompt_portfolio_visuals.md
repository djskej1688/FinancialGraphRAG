# Portfolio Visualizations — Codex Execution Spec

**작성일:** 2026-05-29  
**목표:** 포트폴리오용 시각화 4종 생성 (모델/API 호출 없음)  
**전제:** Round 10 + Naive Baseline 완료 후 실행  
**출력:** `outputs/portfolio/` 폴더에 PNG 파일들  

---

## 0. 불변 규칙

```
API 호출 없음
기존 결과 파일 수정 없음
matplotlib / seaborn / pandas 사용
한글 폰트 없으면 영어로 (폰트 오류 방지)
```

---

## 1. 스크립트: `scripts/generate_portfolio_visuals.py`

---

## 2. 시각화 1: 라운드별 성능 추이 (`round_progression.png`)

### 데이터 소스

```python
ROUND_DATA = [
    # round, n_cases, graph_ac, vector_ac, hybrid_ac
    ("R5",   25, 0.00, None, None),
    ("R6",   25, 0.50, 0.40, 0.40),
    ("R7*",  25, 0.90, 0.60, 0.80),   # asterisk = targeted diagnostic
    ("R8",   50, 0.46, 0.36, 0.40),
    ("R9C",  50, 0.52, 0.50, 0.46),
    ("R10", 251, 0.61, 0.57, 0.57),
]
```

R7에 `*` 표시와 footnote: "R7: targeted diagnostic rerun (not clean held-out)"

### 차트 형식

- Line chart, x축: Round, y축: Answer Correctness (0~1.0)
- 3개 라인: graph (blue, 굵게), vector (orange), hybrid (green)
- R8/R9C/R10에 회색 배경 박스 "Clean Held-Out" 표시
- R7에 점선 구분선 + 주석
- y축 0.0~1.0, 그리드 on
- 범례 우하단
- 제목: "GraphRAG vs Vector RAG — Answer Correctness by Round"

---

## 3. 시각화 2: 데이터셋 × Method 비교 (`dataset_method_comparison.png`)

### 데이터 소스 (R10)

```python
DATASET_DATA = {
    "FinDER\n(130)": {"vector": 0.2692, "graph": 0.3923, "hybrid": 0.2692},
    "FinQA\n(56)":   {"vector": 0.8214, "graph": 0.7500, "hybrid": 0.8571},
    "TAT-QA\n(65)":  {"vector": 0.9538, "graph": 0.9231, "hybrid": 0.9077},
    "Overall\n(251)": {"vector": 0.5697, "graph": 0.6096, "hybrid": 0.5657},
}
```

### 차트 형식

- Grouped bar chart, 3개 그룹 per dataset (vector/graph/hybrid)
- graph 바: 파란색 + 약간 굵은 테두리 (강조)
- 각 바 위에 값 레이블 (소수점 2자리)
- FinQA에 작은 주석: "vector≥graph (table format)"
- TAT-QA에 작은 주석: "11% selection bias"
- 제목: "Round 10: Performance by Dataset and Method"

---

## 4. 시각화 3: Formula Type 성능 히트맵 (`formula_type_heatmap.png`)

### 데이터 소스

R10 traces에서 formula_type × method 조합별 avg_ac 계산:

```python
traces_path = "outputs/round3_eval_runs/round10_eval_20260529_170409/round10_traces.jsonl"
# formula_type × method 피벗 테이블 생성
```

### 차트 형식

- Heatmap, x축: method (vector/graph/hybrid), y축: formula_type
- 색상: 0.0(빨강) → 0.5(노랑) → 1.0(초록), cmap='RdYlGn'
- 각 셀에 ac 값 표시 (소수점 2자리)
- 건수(n)를 y축 레이블에 포함: "ratio_trend (30)"
- 제목: "Round 10: Answer Correctness by Formula Type"

---

## 5. 시각화 4: Naive Baseline 비교 (`naive_comparison.png`)

### 데이터 소스

```python
# naive baseline 결과 로드
state = load_json("outputs/naive_baseline/state.json")

NAIVE_DATA = {
    "Naive\ngpt-4o-mini": state["naive_mini_ac"],
    "Naive\ngpt-4o": state["naive_4o_ac"],
    "GraphRAG\n(ours)": state["r10_graph_ac_on_subset"],
}
```

### 차트 형식

- 수평 막대 차트 (3개 바)
- GraphRAG 바: 파란색 + 굵은 테두리 (강조)
- Naive 바: 회색
- 각 바 오른쪽에 ac 값 레이블
- 제목: "GraphRAG vs Naive LLM Baseline (50-case subset)"
- 부제: "Same 50 cases, same scorer — only retrieval method differs"
- GraphRAG > Naive인 경우 화살표 + "+{delta:.1%} improvement" 주석

---

## 6. 출력 파일

```
outputs/portfolio/
  round_progression.png          # 시각화 1
  dataset_method_comparison.png  # 시각화 2
  formula_type_heatmap.png       # 시각화 3
  naive_comparison.png           # 시각화 4
  visuals_state.json             # 생성 메타데이터
```

### visuals_state.json

```json
{
  "generated_at": "...",
  "files": ["round_progression.png", ...],
  "r10_graph_overall": 0.6096,
  "r10_vector_overall": 0.5697,
  "naive_mini_ac": ...,
  "naive_4o_ac": ...,
  "all_generated": true
}
```

---

## 7. 의존성 확인

```python
# 스크립트 시작 시 확인
import matplotlib
import seaborn
import pandas
import numpy
# 없으면: pip install matplotlib seaborn pandas numpy
```

---

## 8. 체크리스트

```
[ ] naive_baseline/state.json 존재 확인 (naive baseline 선행 완료)
[ ] R10 traces 로드 확인 (753 lines)
[ ] 시각화 1: round_progression.png 생성
[ ] 시각화 2: dataset_method_comparison.png 생성
[ ] 시각화 3: formula_type_heatmap.png 생성
[ ] 시각화 4: naive_comparison.png 생성
[ ] visuals_state.json all_generated=true 확인
[ ] 각 PNG 파일 크기 > 10KB 확인 (정상 생성 여부)
```

---

## 9. 파일 위치

| 파일 | 경로 |
|---|---|
| 이 스펙 | `codex_prompt_portfolio_visuals.md` |
| 시각화 스크립트 | `scripts/generate_portfolio_visuals.py` |
| Naive baseline 결과 (선행) | `outputs/naive_baseline/state.json` |
| R10 traces | `outputs/round3_eval_runs/round10_eval_20260529_170409/round10_traces.jsonl` |
| 출력 폴더 | `outputs/portfolio/` |
