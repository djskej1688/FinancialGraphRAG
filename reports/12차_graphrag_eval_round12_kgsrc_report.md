# Round 12 Summary: Graph + Source Text Hybrid

**Control method:** `source_text_only_v12` (evidence_text only, no KG facts)
**Treatment method:** `graph_kgsrc_v12` (KG facts + evidence_text)
**Baseline:** R10 v2 (graph_only, vector, hybrid)
**Cases:** 186 comparable across all methods

## Overall

| Method | AC | NC | n |
|---|---:|---:|---:|
| source_text_only (CONTROL) | 0.4086 | 0.6325 | 186 |
| graph_kgsrc (TREATMENT) | 0.3441 | 0.6471 | 186 |
| graph_only (R10 v2) | 0.3495 | 0.6647 | 186 |
| vector (R10 v2) | 0.5753 | 0.7822 | 186 |
| hybrid (R10 v2) | 0.5430 | 0.8152 | 186 |

## FinDER

| Method | AC | Delta vs graph | Delta vs textonly |
|---|---:|---:|---:|
| source_text_only (CONTROL) | 0.3000 | +0.1231 | -- |
| graph_kgsrc (TREATMENT) | 0.2231 | +0.0462 | -0.0769 |
| graph_only (R10 v2) | 0.1769 | -- | -0.1231 |
| vector (R10 v2) | 0.4692 | +0.2923 | +0.1692 |
| hybrid (R10 v2) | 0.4077 | +0.2308 | +0.1077 |

## FinQA

| Method | AC | NC |
|---|---:|---:|
| source_text_only (CONTROL) | 0.6607 | 0.8321 |
| graph_kgsrc (TREATMENT) | 0.6250 | 0.9309 |
| graph_only (R10 v2) | 0.7500 | 0.8451 |
| vector (R10 v2) | 0.8214 | 0.9259 |
| hybrid (R10 v2) | 0.8571 | 0.9504 |

## Formula Type (FinDER)

| formula_type | n | textonly | kgsrc | graph | vector | H2? |
|---|---:|---:|---:|---:|---:|---|
| debt_metrics | 3 | 0.0000 | 0.0000 | 0.3333 | 0.6667 | TIE |
| effective_tax_rate | 2 | 0.0000 | 0.0000 | 0.5000 | 0.5000 | TIE |
| gross_margin | 15 | 0.1333 | 0.1333 | 0.1333 | 0.3333 | TIE |
| income_vs_ops | 8 | 0.3750 | 0.2500 | 0.2500 | 0.5000 | NO |
| multi_year_margin | 31 | 0.4516 | 0.3548 | 0.1935 | 0.5806 | NO |
| net_margin | 3 | 0.6667 | 0.6667 | 0.0000 | 0.6667 | TIE |
| operating_margin | 7 | 0.7143 | 0.5714 | 0.1429 | 0.7143 | NO |
| other | 4 | 0.2500 | 0.2500 | 0.2500 | 0.5000 | TIE |
| ratio_trend | 30 | 0.2667 | 0.1333 | 0.1333 | 0.4333 | NO |
| yoy_revenue_change | 27 | 0.1481 | 0.1111 | 0.1852 | 0.3333 | NO |

## Hypothesis Results (FinDER)

| Hypothesis | Result | Detail |
|---|---|---|
| H1: kgsrc > graph_only | TRUE | 0.2231 vs 0.1769 |
| H2: kgsrc > textonly (+2pp) | FALSE | 0.2231 vs 0.3000 |
| H3: gap to vector narrows | FALSE | vector=0.4692 |
| H4: kgsrc >= vector (-2pp) | FALSE | 0.2231 vs 0.4692 |

**Interpretation:** Source text fixes KG coverage loss, but KG facts add little beyond text. Text is the key.

## Token Budget Note

graph_kgsrc has more context than source_text_only because KG facts are added.
Token-matched comparison is deferred to Round 14.
Context summary: `{'source_text_only_v12': {'avg_total_user_msg_chars': 5982.8065, 'avg_kg_fact_text_chars': 0.0, 'avg_source_text_chars': 1977.2957, 'avg_prompt_tokens': 2249.1613}, 'graph_kgsrc_v12': {'avg_total_user_msg_chars': 7973.3548, 'avg_kg_fact_text_chars': 1963.5484, 'avg_source_text_chars': 1977.2957, 'avg_prompt_tokens': 3043.6882}, 'token_gap_kgsrc_minus_textonly': 794.5269}`

## Claim Boundary

```
graph_kgsrc_source_text_hybrid_round12
Allowed claim:
  On the same cases, same scorer v2, and gpt-4o-mini, these methods differ by the reported AC/NC deltas.
Not allowed:
  Do not claim graph_kgsrc is always at or above vector unless H4 is TRUE.
  Do not make token-efficiency claims because graph_kgsrc has more context.
```
