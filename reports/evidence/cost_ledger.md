# Cost Ledger — OpenAI generation across all rounds (offline reconstruction)

Reconstructed by summing `usage` tokens across 28 trace files, deduped by `trace_id`. Pricing = assumed standard OpenAI rates (gpt-4o-mini 0.15/0.60, gpt-4o 2.50/10.00 per 1M in/out). Adjust `PRICE` in `scripts/round15_cost_ledger.py` for actuals.

| Round | Calls | Prompt tok | Completion tok | Model(s) | Est. cost (USD) |
|---|---:|---:|---:|---|---:|
| R3_dev_dryrun | 188 | 295,390 | 166,824 | gpt-4o-mini | $0.1444 |
| R3_locked_test | 40 | 77,865 | 43,451 | gpt-4o-mini | $0.0378 |
| R4 | 75 | 324,651 | 78,526 | gpt-4o-mini | $0.0958 |
| R5 | 75 | 207,706 | 77,018 | gpt-4o-mini | $0.0774 |
| R6 | 125 | 229,129 | 140,363 | gpt-4o-mini | $0.1186 |
| R7 | 75 | 154,270 | 101,030 | gpt-4o-mini | $0.0838 |
| R8 | 150 | 358,572 | 109,964 | gpt-4o-mini | $0.1198 |
| R9C | 149 | 371,235 | 97,494 | gpt-4o-mini | $0.1142 |
| R10_v2_rescore | 752 | 1,914,813 | 440,452 | gpt-4o-mini | $0.5515 |
| R11_v2 | 50 | 118,433 | 30,569 | gpt-4o | $0.6018 |
| naive_v1 | 100 | 78,868 | 10,194 | gpt-4o,gpt-4o-mini | $0.1625 |
| R12 | 372 | 984,470 | 198,481 | gpt-4o-mini | $0.2668 |
| R13 | 130 | 357,700 | 76,368 | gpt-4o-mini | $0.0995 |
| R14 | 160 | 854,584 | 119,382 | gpt-4o-mini | $0.1998 |
| R14B | 1260 | 1,044,678 | 42,500 | gpt-4o-mini | $0.1822 |
| R15_reeval | 400 | 1,681,817 | 294,974 | gpt-4o-mini | $0.4293 |
| **TOTAL (OpenAI gen, deduped)** | **4101** | | | | **$3.28** |

- Total OpenAI **generation** tokens (deduped): 11,081,771
- **Cross-vendor judge panel (R15 P2.5)** = **$4.80** (DeepSeek/Kimi/Grok + gpt-4o re-judge; recorded separately, not in these generation traces).
- R15 P1 vector index: embeddings (text-embedding-3-small) not in these traces; ~negligible.
- **Grand total (generation $3.28 + judge panel $4.80) ≈ $8.08**
