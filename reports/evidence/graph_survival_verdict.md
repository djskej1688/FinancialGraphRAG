# Round 15 - Graph Survival Verdict

**judge_vendor:** `openai`  
**judge_model:** `gpt-4o`  
**same_vendor_as_generator:** `True`  
**bias disclosure:** No cross-vendor judge key found. Using OpenAI gpt-4o while generation uses gpt-4o-mini; same-vendor bias disclosed.

## Headline

Graph survives the fair chunk-vector rehabilitation under both scorer_v9 AC and judge_score.

| Method | scorer_v9_AC | judge_score | number_overlap | token_f1 | both_found | mean_prompt_tokens |
|---|---:|---:|---:|---:|---:|---:|
| vector_single_chunk_v15 | 0.1625 | 0.0875 | 0.3958 | 0.0585 | 0.4625 | 6466.0 |
| vector_multi_by_company_chunk_v15 | 0.1125 | 0.0813 | 0.3792 | 0.0555 | 0.3750 | 7129.8 |
| graph_structured_v14 | 0.8250 | 0.6000 | 0.9000 | 0.1363 | 1.0000 | 1707.8 |
| graph_guided_text_v14 | 0.8000 | 0.5813 | 0.8875 | 0.1305 | 1.0000 | 2922.9 |
| source_text_concat_v14 | 0.3375 | 0.2562 | 0.5708 | 0.0634 | 1.0000 | 2796.2 |

## Margin

- R14 graph_structured vs original vector_multi AC margin: `0.7375`
- R15 graph_structured vs fair vector_multi AC margin: `0.7125`
- R15 graph_structured beats fair vector by AC: `True`
- R15 graph_structured beats fair vector by judge_score: `True`
- fair vector single both_found: `0.4625`
- fair vector multi both_found: `0.375`

## Publish Interpretation Matrix

- If graph wins: multi-company graph advantage is robust to a fair chunk retriever.
- If margin shrinks but graph still wins: R14 vector baseline was weak, but graph advantage remains after correction.
- If fair vector ties or wins: R14 graph advantage was mostly a weak-retriever artifact; retract broad graph-win framing.
