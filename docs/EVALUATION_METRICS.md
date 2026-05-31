# Round 15 Evaluation Metrics

This research-track policy defines a uniform scoring layer for R15.

## Three-Layer Evaluation

1. `number_overlap`: diagnostic numeric recall against canonical gold numbers.
2. `token_f1`: diagnostic lexical overlap against canonical gold text.
3. `judge_score`: headline semantic correctness from one fixed judge prompt.

`judge_score` is the headline metric. `number_overlap` and `token_f1` are diagnostic only.

## Gold Answer Source

For R14 cross-company cases, canonical gold comes from each case's
`scorer_only_target_slot_contract`: `company_a_value`, `company_b_value`,
`difference`, and the case-level `winner`. These are derived target values,
not model-visible answers.

## Uniformity

All methods use the same metric functions and the same judge prompt. `scorer_v9`
is retained as a parallel numeric/formula diagnostic and is not silently replaced.

## Judge Bias Disclosure

Prefer cross-vendor judging. If unavailable, use `gpt-4o` while generation uses
`gpt-4o-mini`, and disclose same-vendor bias in metadata.

## Parse Failures

Judge parsing failures are recorded as `verdict=scorer_uncertain` and
`score=null`; no guessed score is allowed.
