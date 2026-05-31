# 11차 GraphRAG Evaluation Round 11 GPT-4o Ablation Report

> 병합 문서. Contract v2로 year-bug를 수정한 `round11_comparison_v2.md`를 우선 배치하고, 원본 `round11_comparison.md`는 비교/추적용으로 뒤에 보존한다.

---

## A. Round 11 v2 — canonical corrected result

# Round 11 Ablation v2: gpt-4o vs gpt-4o-mini (Contract v2 — year-bug fixed)

**Scores use v2 contracts.** FinDER expected_values corrected; FinQA unchanged.

## Overall

| Method | Model | AC | NC |
|---|---|---:|---:|
| graph_neo4j_v11 | gpt-4o      | 0.44  | 0.6304  |
| graph_neo4j_v10 | gpt-4o-mini | 0.48 | 0.7581 |
| **Delta**       | 4o − mini   | **-0.0400** | |

## By Dataset

| Dataset | n | gpt-4o AC | mini AC | Δ AC |
|---|---:|---:|---:|---:|
| FinDER | 25 | 0.12 | 0.24 | -0.1200 |
| FinQA  | 25  | 0.76  | 0.72  | +0.0400  |

## Case-Level
- gpt-4o wins: 5
- mini wins: 7
- both correct: 17
- both wrong: 21

## FinQA Empty Answer (unchanged from v1)
- gpt-4o-mini empty: 4/25 → gpt-4o: 0/25

---

## B. Round 11 original — superseded diagnostic result

# Round 11 Ablation: gpt-4o vs gpt-4o-mini

**Cases:** 50 (FinDER 25 + FinQA 25)
**Method:** graph_neo4j, same KG facts (R10 cache), same prompt v3.4, scorer v9
**Excluded:** ratio_trend, yoy_revenue_change, other (contract bug), TAT-QA (selection bias)

## Overall

| Method | Model | AC | NC |
|---|---|---:|---:|
| graph_neo4j_v11 | gpt-4o | 0.4200 | 0.5426 |
| graph_neo4j_v10 | gpt-4o-mini | 0.5600 | 0.6386 |
| **Delta** | 4o - mini | **-0.1400** | -0.0960 |

## By Dataset

| Dataset | n | gpt-4o AC | mini AC | Delta AC |
|---|---:|---:|---:|---:|
| FinDER | 25 | 0.0800 | 0.4000 | -0.3200 |
| FinQA | 25 | 0.7600 | 0.7200 | +0.0400 |

## By Formula Type

| formula_type | n | gpt-4o AC | mini AC | Delta AC |
|---|---:|---:|---:|---:|
| debt_metrics | 1 | 0.0000 | 0.0000 | +0.0000 |
| effective_tax_rate | 1 | 0.0000 | 1.0000 | -1.0000 |
| finqa_program | 25 | 0.7600 | 0.7200 | +0.0400 |
| gross_margin | 5 | 0.2000 | 0.4000 | -0.2000 |
| income_vs_ops | 3 | 0.0000 | 0.6667 | -0.6667 |
| multi_year_margin | 11 | 0.0909 | 0.3636 | -0.2727 |
| net_margin | 1 | 0.0000 | 0.0000 | +0.0000 |
| operating_margin | 3 | 0.0000 | 0.3333 | -0.3333 |

## Case-Level Breakdown

- gpt-4o wins: 4
- mini wins: 11
- both correct: 17
- both wrong: 18

## FinQA Empty Answer Pattern

- gpt-4o-mini empty final_answer: 4 / 25
- gpt-4o empty final_answer: 0 / 25

## Claim Boundary

```
ablation_model_gpt4o_vs_mini_round11
Allowed claim:
  On the same 50 cases (FinDER 25 + FinQA 25), same KG facts and prompt,
  gpt-4o differs from gpt-4o-mini by the reported AC/NC deltas.
Not allowed:
  Do not generalize to ratio_trend/yoy/TAT-QA or the full benchmark.
```
