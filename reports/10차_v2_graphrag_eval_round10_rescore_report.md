# Round 10 Re-scored Summary (Contract v2 — FinDER year-bug fixed)

**Change:** FinDER expected_values corrected (year → actual metric %).
FinQA and TAT-QA scores unchanged.

## Overall

| Method | AC (v2) | NC (v2) | AC (orig) | Δ AC |
|---|---:|---:|---:|---:|
| graph_neo4j | 0.498 | 0.7432 | 0.6096 | -0.1116 |
| vector_only | 0.6733 | 0.8321 | 0.5697 | +0.1036 |
| hybrid_neo4j | 0.6375 | 0.8511 | 0.5657 | +0.0718 |

## By Dataset × Method

| Dataset | Method | AC (v2) | AC (orig) | Δ AC |
|---|---|---:|---:|---:|
| FinDER | graph | 0.1769 | 0.3923 | -0.2154 |
| FinDER | vector | 0.4692 | 0.2692 | +0.2000 |
| FinDER | hybrid | 0.4077 | 0.2692 | +0.1385 |
| FinQA | graph | 0.75 | 0.75 | +0.0000 |
| FinQA | vector | 0.8214 | 0.8214 | +0.0000 |
| FinQA | hybrid | 0.8571 | 0.8571 | +0.0000 |
| TAT-QA | graph | 0.9231 | 0.9231 | +0.0000 |
| TAT-QA | vector | 0.9538 | 0.9538 | +0.0000 |
| TAT-QA | hybrid | 0.9077 | 0.9077 | +0.0000 |

## FinDER Rescore Breakdown
- Rescored traces: 390
- AC improved: 49  dropped: 33  unchanged: 308
