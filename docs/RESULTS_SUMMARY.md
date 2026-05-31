# Results Summary

초기 R8-R10 실험에서는 graph가 single-company 벤치마크에서 vector보다 좋아 보였다. R8은 graph 0.46 / vector 0.36, R9C는 graph 0.52 / vector 0.50, R10 원본은 graph 0.610 / vector 0.570이었다. 그러나 이 구간은 pre-v2 year-bug scorer contract와 `vector_only` provenance 문제가 있었으므로, 현재는 방향성 기록으로만 보존한다.

R10 v2 재채점 이후 single-company 결론은 뒤집혔다. corrected single-company 결과는 case_text_only 0.673 > hybrid 0.638 > graph 0.498이다. FinDER에서는 graph 0.177 / case_text 0.469 / hybrid 0.408, FinQA에서는 0.75 / 0.821 / 0.857, TAT-QA에서는 0.923 / 0.954 / 0.908이었다. 이 구간의 핵심 교훈은 graph가 항상 이긴다는 주장이 아니라, single-company text-style 질의에서는 case text가 강하다는 점이다.

R12와 R13은 graph의 약점을 더 좁혀 보았다. R12에서는 source_text_only 0.409가 graph_kgsrc 0.344와 graph_only 0.350보다 높았고, text는 0.575였다. R13에서는 graph_v13 0.215가 graph_v10 0.177보다 나아졌지만, text 0.30과 case_text 0.469에는 못 미쳤다. 다만 단일지표 영역에서는 operating_margin 0.714=0.714, net_margin 0.667=0.667로 parity가 확인되었다.

최종 positive claim은 R14 cross-company structured comparison에 한정된다. R14에서 graph_structured는 0.825, graph_guided는 0.800, source_concat은 0.338, vector_multi는 0.088, vector_single은 0.063이었다. both_found도 graph 1.00 vs vector 0.225/0.125였고, coverage는 323 companies / 34 metrics / 5640 obs / 5246 triples / 0 unreachable / 80/80 structural이었다.

R15는 이 R14 claim을 방어하는 hardening round였다. provenance audit은 R3-R10 `vector_only`가 per_case_evidence_only, R14B가 gold_text, only R14 = real retrieval임을 확정했다. fair chunk retriever 재평가에서도 vector_single_chunk AC 0.163 / vector_multi_chunk 0.113 vs graph 0.825였고, margin은 R14 0.7375에서 fair 0.7125로 유지되었다. 4-vendor judge에서도 graph_structured는 0.60/0.58/0.61/0.49로 모든 vector arm을 모든 judge에서 앞섰고, Fleiss' κ는 0.53이었다.
