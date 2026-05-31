# Experiment Log

| Round | Status | One-line result |
|---|---|---|
| R4-R7 | diagnostic | Built and debugged the KG/prompt/scorer path; no final public claim. |
| R8 | pre-v2 | First clean held-out run: graph 0.46 / vector 0.36, later treated as directional. |
| R9A | pre-v2 sensitivity | No-model sensitivity analysis over R8 traces; did not resolve the scorer contract issue. |
| R9C | pre-v2 | Pipeline fixes plus scorer v9: graph 0.52 / vector 0.50, later superseded. |
| R10 original | pre-v2 | Three-dataset run: graph 0.610 / vector 0.570, superseded by corrected scoring. |
| R10 v2 | corrected | Corrected single-company result: case_text_only 0.673 > hybrid 0.638 > graph 0.498. |
| Naive original | pre-v2 | Historical comparison: graph 0.62 / naive gpt-4o 0.56, superseded by Naive v2. |
| Naive v2 | corrected | Corrected comparison: graph 0.52 / naive gpt-4o 0.64 / mini 0.54. |
| R11 v2 | corrected | Graph with 4o/mini: graph 4o 0.44 / mini 0.48. |
| R12 | diagnostic | Source text beat graph variants: source_text_only 0.409 > graph_kgsrc 0.344 ≈ graph_only 0.350. |
| R13 | diagnostic | Graph rebuild improved graph_v13 0.215 over graph_v10 0.177 but did not beat text 0.30 or case_text 0.469. |
| R14 | headline | Cross-company structured comparison: graph_structured 0.825 vs vector_multi 0.088 / vector_single 0.063. |
| R14B | scale | FinQA fact-presentation ablation: structured 0.3025 / hybrid 0.255 / gold_text 0.2025. |
| R15 | hardening | Provenance audit, fair chunk retriever, and 4-vendor judge panel defend the R14 claim. |
