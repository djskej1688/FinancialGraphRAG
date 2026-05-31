# Claim Boundaries

| Round | Allowed claim | Not allowed |
|---|---|---|
| R4-R7 | Diagnostic development of KG/prompt/scorer pipeline. | No public graph-vs-vector superiority claim. |
| R8 | Clean held-out directional result under pre-v2 contract: graph 0.46 / vector 0.36. | Do not treat as final retrieval comparison; `vector_only` is reclassified case text. |
| R9A | No-model scorer sensitivity over R8 traces. | Do not use as independent model evidence. |
| R9C | Pipeline/scorer v9 directional result under pre-v2 contract: graph 0.52 / vector 0.50. | Do not treat the narrow margin as final proof. |
| R10 original | Historical pre-v2 three-dataset run: graph 0.610 / vector 0.570. | Superseded by R10 v2; do not cite as corrected result. |
| R10 v2 | Corrected single-company result: case_text_only 0.673 > hybrid 0.638 > graph 0.498. | Do not call `case_text_only` corpus retrieval. |
| Naive original | Historical pre-v2 naive comparison: graph 0.62 / naive gpt-4o 0.56. | Superseded by Naive v2: graph 0.52 / naive gpt-4o 0.64 / mini 0.54. |
| R11 v2 | Corrected single-company model comparison: graph 4o 0.44 / mini 0.48. | Do not generalize beyond the corrected single-company setting. |
| R12 | KG-source/text stacking test: source_text_only 0.409 > graph_kgsrc 0.344 ≈ graph_only 0.350 < text 0.575. | Do not claim KG facts stacked on text help in this setting. |
| R13 | Graph rebuild improvement: graph_v13 0.215 > graph_v10 0.177, below text 0.30 and case_text 0.469. | Do not claim single-company graph superiority. |
| R14 | Positive headline: cross-company structured queries favor graph_structured 0.825 over vector_multi 0.088 and vector_single 0.063. | Do not claim this covers open-domain retrieval, unstructured questions, or all finance QA. |
| R14B | Fact-presentation ablation at scale: structured 0.3025 / hybrid 0.255 / gold_text 0.2025. | Not a retrieval benchmark; `gold_text` is not corpus retrieval. |
| R15 | Provenance + fair-vector + multi-judge hardening confirms the R14 claim. | Not a new broad benchmark; it defends the R14 retrieval-vs-graph comparison. |
