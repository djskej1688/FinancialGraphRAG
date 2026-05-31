# Provenance

This document is the publication-facing summary of the R15 vector-arm audit. It keeps the historical outputs frozen and corrects labels by provenance. Numbers are unchanged.

## Core Finding

single-company "vector"=case_text; R14B=gold_text; only R14 = real retrieval.

## Classification Counts

| Classification | Arms |
|---|---:|
| per_case_evidence_only | 13 |
| gold_context | 1 |
| real_retrieval | 2 |

## Arm Verdicts

| Round | Method | Audit classification | Verdict | Publication label |
|---|---|---|---|---|
| 3 | `hybrid_vector_graph_v3` | per_case_evidence_only | mislabeled_or_legacy_prompt_arm | `hybrid_vector_graph_v3` |
| 3_1 | `hybrid_vector_graph_v3_1` | per_case_evidence_only | mislabeled_or_legacy_prompt_arm | `hybrid_vector_graph_v3_1` |
| 3_2 | `hybrid_vector_graph_v3_2` | per_case_evidence_only | mislabeled_or_legacy_prompt_arm | `hybrid_vector_graph_v3_2` |
| 3 | `vector_only_v3` | per_case_evidence_only | mislabeled_or_legacy_prompt_arm | `case_text_only_v3` |
| 3_1 | `vector_only_v3_1` | per_case_evidence_only | mislabeled_or_legacy_prompt_arm | `case_text_only_v3_1` |
| 3_2 | `vector_only_v3_2` | per_case_evidence_only | mislabeled_or_legacy_prompt_arm | `case_text_only_v3_2` |
| 4 | `vector_only_v4` | per_case_evidence_only | mislabeled_or_legacy_prompt_arm | `case_text_only_v4` |
| 5 | `vector_only_v5` | per_case_evidence_only | mislabeled_or_legacy_prompt_arm | `case_text_only_v5` |
| 6 | `vector_only_v6` | per_case_evidence_only | mislabeled_or_legacy_prompt_arm | `case_text_only_v6` |
| 7 | `vector_only_v7` | per_case_evidence_only | mislabeled | `case_text_only_v7` |
| 8 | `vector_only_v8` | per_case_evidence_only | mislabeled | `case_text_only_v8` |
| 9 | `vector_only_v9` | per_case_evidence_only | mislabeled | `case_text_only_v9` |
| 10 | `vector_only_v10` | per_case_evidence_only | mislabeled | `case_text_only_v10` |
| R14 | `vector_single_v14` | real_retrieval | ok_but_weak_retriever | `vector_single_v14` |
| R14 | `vector_multi_by_company_v14` | real_retrieval | ok_but_weak_retriever | `vector_multi_by_company_v14` |
| R14B | `vector_only_scaled` | gold_context | needs_reclassification | `gold_text_only` |

The source audit table is preserved in `reports/evidence/vector_arm_audit.md`.

## Implication

Historical single-company `vector_only` arms were not corpus retrieval. They used the case's own evidence text and are therefore treated as `case_text_only`. R14B's `vector_only_scaled` used gold context and is treated as `gold_text_only`. Only R14's `vector_single_v14` and `vector_multi_by_company_v14` were real retrieval arms.

R15 then rebuilt the R14 vector baseline with a chunk-level, persistent, provenance-logged retriever. The R14 graph margin moved from 0.7375 to 0.7125 under the fair retriever, so the cross-company graph result survives the provenance correction.
