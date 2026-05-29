# Naive Baseline Comparison

## Overall on 50-Case Subset

| Method | avg_ac | avg_nc | model | prompt |
|---|---:|---:|---|---|
| graph_neo4j_v10 (R10) | 0.6200 | 0.7217 | gpt-4o-mini | structured v3.4 + KG |
| naive_gpt4omini | 0.4600 | 0.6091 | gpt-4o-mini | naive |
| naive_gpt4o | 0.5600 | 0.6732 | gpt-4o | naive |

## By Dataset

| Dataset | graph_v10 | naive_mini | naive_4o |
|---|---:|---:|---:|
| FinDER (26) | 0.4615 | 0.3077 | 0.3846 |
| FinQA (11) | 0.5455 | 0.4545 | 0.6364 |
| TAT-QA (13) | 1.0000 | 0.7692 | 0.8462 |

## Diagnostic Questions

1. graph_neo4j > naive_gpt4omini? True (+0.1600)
2. naive_gpt4o > graph_neo4j? False (-0.0600)
3. naive_gpt4o > naive_gpt4omini? True (+0.1000)
