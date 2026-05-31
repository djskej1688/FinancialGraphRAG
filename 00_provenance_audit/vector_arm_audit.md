# Round 15 Phase 0 - Vector Provenance Audit

## Gate Summary

- unknown vector arms: 0
- prior `LanceDB` impersonation found in research scripts: no
- `finder_vector_arm.py` exists: no
- product vector backend exists: yes
- LanceDB importable in current `.venv`: no
- FAISS importable in current `.venv`: no
- planned Phase 1 backend: numpy_ondisk

## Code Facts Checked

- R14B `vector_only_scaled` branch: `scripts/round14b_benchmark_scale.py:577`
- R14B direct evidence return: `scripts/round14b_benchmark_scale.py:578`
- R14 `load_passage_corpus()`: `scripts/round14_cross_company.py:1221`
- R14 `cosine()`: `scripts/round14_cross_company.py:1263`
- R14 `retrieve()`: `scripts/round14_cross_company.py:1270`
- R8 vector context: `scripts/round8_eval.py:24`
- R9C vector context: `scripts/round9c_eval.py:39`
- R10 vector context: `scripts/round10_eval.py:29`

## Classification Counts

| classification | arms |
|---|---:|
| gold_context_no_retrieval | 1 |
| per_case_evidence_only | 13 |
| real_retrieval_full_corpus | 2 |

## Arm Audit

| round | method | classification | granularity | index | verdict | recommended label | traces |
|---|---|---|---|---|---|---|---:|
| 3 | `hybrid_vector_graph_v3` | per_case_evidence_only | none | none | mislabeled_or_legacy_prompt_arm | `hybrid_vector_graph_v3` | 19 |
| 3_1 | `hybrid_vector_graph_v3_1` | per_case_evidence_only | none | none | mislabeled_or_legacy_prompt_arm | `hybrid_vector_graph_v3_1` | 19 |
| 3_2 | `hybrid_vector_graph_v3_2` | per_case_evidence_only | none | none | mislabeled_or_legacy_prompt_arm | `hybrid_vector_graph_v3_2` | 19 |
| R14 | `vector_multi_by_company_v14` | real_retrieval_full_corpus | document_level | in_memory_cosine | ok_but_weak_retriever | `vector_multi_by_company_v14 (keep; document-level in-memory baseline)` | 152 |
| R14B | `vector_only_scaled` | gold_context_no_retrieval | none | none | needs_reclassification | `gold_text_only` | 400 |
| 10 | `vector_only_v10` | per_case_evidence_only | none | none | mislabeled | `case_text_only_v10` | 251 |
| 3 | `vector_only_v3` | per_case_evidence_only | none | none | mislabeled_or_legacy_prompt_arm | `case_text_only_v3` | 19 |
| 3_1 | `vector_only_v3_1` | per_case_evidence_only | none | none | mislabeled_or_legacy_prompt_arm | `case_text_only_v3_1` | 19 |
| 3_2 | `vector_only_v3_2` | per_case_evidence_only | none | none | mislabeled_or_legacy_prompt_arm | `case_text_only_v3_2` | 19 |
| 4 | `vector_only_v4` | per_case_evidence_only | none | none | mislabeled_or_legacy_prompt_arm | `case_text_only_v4` | 25 |
| 5 | `vector_only_v5` | per_case_evidence_only | none | none | mislabeled_or_legacy_prompt_arm | `case_text_only_v5` | 25 |
| 6 | `vector_only_v6` | per_case_evidence_only | none | none | mislabeled_or_legacy_prompt_arm | `case_text_only_v6` | 25 |
| 7 | `vector_only_v7` | per_case_evidence_only | none | none | mislabeled | `case_text_only_v7` | 25 |
| 8 | `vector_only_v8` | per_case_evidence_only | none | none | mislabeled | `case_text_only_v8` | 50 |
| 9 | `vector_only_v9` | per_case_evidence_only | none | none | mislabeled | `case_text_only_v9` | 50 |
| R14 | `vector_single_v14` | real_retrieval_full_corpus | document_level | in_memory_cosine | ok_but_weak_retriever | `vector_single_v14 (keep; document-level in-memory baseline)` | 152 |

## Gate Decision

Phase 1 may proceed only because every discovered vector-like arm has a non-unknown classification. R14B is reclassified by sidecar only; original result files are frozen.
