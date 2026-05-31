# Round 13 Summary: FinDER KG Semantic Re-extraction

**KG batch:** `kg-round13-finder-v1-20260530`
**Method:** graph_neo4j_v13 (semantic IE KG) vs graph_v10 / source_text_only / vector
**Cases:** 130 FinDER, scorer v2

## FinDER AC

| Method | AC | NC |
|---|---:|---:|
| graph_v13 (new KG) | 0.2154 | 0.4455 |
| graph_v10 (broken KG) | 0.1769 | 0.5870 |
| source_text_only | 0.3000 | 0.5466 |
| vector | 0.4692 | 0.7203 |

## Hypotheses

| H | Result | Detail |
|---|---|---|
| H1: v13 > v10 | TRUE | 0.2154 vs 0.1769 |
| H2: v13 >= text-only | FALSE | 0.2154 vs 0.3000 |
| H3: v13 >= vector | FALSE | 0.2154 vs 0.4692 |

**Interpretation:** Real KG beats broken KG but still below text/vector. Structured facts alone insufficient for FinDER.

## KG Quality Gate

`{"avg_new_distinct_years": 2.9846, "avg_new_obs": 17.2846, "avg_new_real_metric_obs": 17.2846, "avg_new_year_value_obs": 0.0077, "avg_old_placeholder_metrics": 8.0, "avg_old_year_value_facts": 3.1231, "cases": 130, "new_placeholder_total": 0, "quality_gate_passed": true, "zero_obs_cases": 0}`

## Formula Type

| formula_type | n | graph_v13 | graph_v10 | textonly | vector |
|---|---:|---:|---:|---:|---:|
| debt_metrics | 3 | 0.0000 | 0.3333 | 0.0000 | 0.6667 |
| effective_tax_rate | 2 | 0.0000 | 0.5000 | 0.0000 | 0.5000 |
| gross_margin | 15 | 0.0667 | 0.1333 | 0.1333 | 0.3333 |
| income_vs_ops | 8 | 0.3750 | 0.2500 | 0.3750 | 0.5000 |
| multi_year_margin | 31 | 0.3548 | 0.1935 | 0.4516 | 0.5806 |
| net_margin | 3 | 0.6667 | 0.0000 | 0.6667 | 0.6667 |
| operating_margin | 7 | 0.7143 | 0.1429 | 0.7143 | 0.7143 |
| other | 4 | 0.0000 | 0.2500 | 0.2500 | 0.5000 |
| ratio_trend | 30 | 0.1000 | 0.1333 | 0.2667 | 0.4333 |
| yoy_revenue_change | 27 | 0.1111 | 0.1852 | 0.1481 | 0.3333 |

## Claim Boundary

```
finder_kg_semantic_rebuild_round13
Allowed claim:
  FinDER 130 cases were re-evaluated with a semantic IE KG using real metric names, years, and values.
Not allowed:
  Do not generalize to FinQA/TAT-QA or cross-company/multi-hop query classes.
```
