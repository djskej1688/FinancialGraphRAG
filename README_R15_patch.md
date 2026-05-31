# README R15 Patch — corrections + new section for the public repo

Your current repo README (the attached `README.md`) was written **before** the R15 provenance audit.
It is strong, but it (a) repeats the vector-provenance mislabel R15 just caught, and (b) is missing the
R15 fair-vector + judge story that actually makes the repo *more* rigorous than the draft. Apply the
changes below **before publishing**. No number is invented here — all come from R15 outputs.

---

## A. CRITICAL CORRECTION — "vector" was case-text in single-company rounds

R15 Phase 0 audit (`outputs/round15_vector_rehab/00_provenance_audit/vector_arm_audit.md`) found that
**`vector_only_v10` (and all R3–R10 `vector_only_*`) were `per_case_evidence_only`** — they fed the
model the case's *own* evidence text, **not retrieval**. They are reclassified `case_text_only_*`.

So in the README:
- The TL;DR row **"Single-company text QA (FinDER) — vector ≥ graph"** → relabel the winner column
  **"case-text ≥ graph"** (or **"text ≥ graph"**), and add a footnote: *"the single-company 'vector'
  arms were case-text (the case's own evidence), not corpus retrieval — see Provenance note. The only
  real retrieval-vs-graph comparison in this repo is R14."*
- The **"Single-Company Results, Corrected (R10 v2)"** table: rename the `vector_only` column to
  `case_text_only` (keep the 0.673 number — it's the same arm, just honestly named), and add one line:
  *"'vector_only' here is case-text, not retrieval (R15 audit). The number is unchanged; only the label
  is corrected."*
- Anywhere the prose says *"vector is the stronger general method"* → *"raw case text is the stronger
  general method"* for single-company.

This is the single most important change. Publishing "vector_only 0.673" as retrieval would repeat the
exact mislabel the project is otherwise famous for catching.

---

## B. NEW SECTION — paste after "Headline Result (R14)"

```markdown
## Round 15 — Did the Graph Win Only Because the Vector Baseline Was Weak? (No.)

R14's vector arms were *real* retrievers, but document-level (whole-filing passages) and in-memory —
a weak retriever that could have inflated the graph margin. R15 rebuilt the vector baseline the right
way and re-checked the result with a second, independent scorer.

**What changed**
- **Fair retriever:** chunk-level (1,200-char chunks), persistent on-disk index, every retrieval logs
  `chunk_id` / `source_case_id` / similarity score. (`text-embedding-3-small`; backend `numpy_ondisk` —
  honestly labeled, since LanceDB/FAISS were not in the env.)
- **Independent judge:** added `number_overlap`, `token_f1`, and an LLM `judge_score` (strict-JSON
  verdict), applied uniformly to all five arms alongside the existing numeric scorer.

**Result — graph survives the fair vector, on a quarter of the tokens**

| Method | scorer AC | judge_score | both companies found | avg prompt tokens |
|---|---:|---:|---:|---:|
| **graph_structured** | **0.825** | **0.600** | 1.00 | **1,708** |
| graph_guided_text | 0.800 | 0.581 | 1.00 | 2,923 |
| source_text_concat | 0.338 | 0.256 | 1.00 | 2,796 |
| vector_single_chunk (fair) | 0.163 | 0.088 | 0.46 | 6,466 |
| vector_multi_by_company_chunk (fair) | 0.113 | 0.081 | 0.38 | 7,130 |

- Chunking **tripled** fair-vector coverage (both-found 0.125 → 0.46) and AC (0.063 → 0.163) — yet
  graph still wins **~5× on AC and ~7× on the judge**. The head-to-head margin barely moved
  (0.7375 → 0.7125).
- **Graph wins on ¼ the tokens** (1.7k vs 6.5k). The advantage is not "more/cleaner context" — it is
  structural: similarity search still fails to assemble a two-company comparison context even with
  chunking, query decomposition, and 4× the token budget (both-found stays < 0.46).
- The **numeric scorer and the independent LLM judge agree** on the ranking.

**Cross-vendor judge panel (4 vendors).** The five arms were re-judged by **gpt-4o, DeepSeek-v4-pro,
Kimi (Moonshot), and Grok-4.3** under one fixed prompt. **Graph beats every vector arm under all four
judges — the ranking is judge-invariant.** Inter-judge agreement is Fleiss' κ = 0.53 (moderate), but the
disagreement is on the *partial*-vs-correct/incorrect boundary (Grok scores nearly binary; gpt-4o uses
*partial* heavily), **not** on the graph-vs-vector ordering — whose margin (≥0.35 under every judge)
dwarfs the inter-judge noise. The headline rests on **ranking agreement (unanimous)**, which κ (a
per-item metric) understates. The same-vendor concern is closed.

| Arm (mean judge_score) | gpt-4o | DeepSeek | Kimi | Grok |
|---|---:|---:|---:|---:|
| graph_structured | 0.60 | 0.58 | 0.61 | 0.49 |
| graph_guided_text | 0.58 | 0.58 | 0.57 | 0.54 |
| source_text_concat | 0.26 | 0.22 | 0.25 | 0.12 |
| vector_single_chunk | 0.09 | 0.14 | 0.13 | 0.04 |
| vector_multi_chunk | 0.08 | 0.09 | 0.12 | 0.03 |

**Provenance note (honesty).** An R15 audit reclassified every "vector" arm by implementation: the
single-company `vector_only_*` arms (R3–R10) were *case-text* (the case's own evidence), not retrieval;
R14B's `vector_only_scaled` was gold context. Only R14 used real retrieval. Originals are frozen and
relabeled by sidecar, never deleted. **The only genuine retrieval-vs-graph comparison in this repo is
R14 — and it now holds against a fair, persistent, chunk-level retriever.**

*Note: gpt-4o shares a vendor with the generator, so the 3 cross-vendor judges above were added; the
graph>vector ranking holds under all four. Kimi `kimi-k2.6` failed strict-JSON smoke and fell back to
`moonshot-v1-128k` (disclosed).*
```

---

## C. Canonical numbers to add to `CLAUDE_CODE_TASK.md` §3 (pinned)

```
R15 fair-vector re-eval (80 cross-company cases, judge=gpt-4o, $1.41):
  graph_structured AC 0.825 / judge 0.600 / tokens 1708
  graph_guided_text AC 0.800 / judge 0.581 / tokens 2923
  source_text_concat AC 0.338 / judge 0.256 / tokens 2796
  vector_single_chunk_v15 AC 0.163 / judge 0.088 / both_found 0.46 / tokens 6466
  vector_multi_by_company_chunk_v15 AC 0.113 / judge 0.081 / both_found 0.38 / tokens 7130
  margin R14 0.7375 -> fair 0.7125 (graph survives). Provenance: R3-R10 vector_only = case_text_only;
  R14B vector_only_scaled = gold_text_only; only R14 = real retrieval.

R15 Phase 2.5 (4-vendor judge panel, $4.80, 0 generation calls, 1598 valid judgments):
  graph_structured mean judge_score: gpt-4o 0.60 / deepseek 0.58 / kimi 0.61 / grok 0.49
  graph > every vector arm under ALL 4 judges (unanimous). Fleiss' kappa 0.53 (moderate; disagreement
  on partial-vs-correct boundary, NOT on ranking). Kimi k2.6 -> moonshot-v1-128k fallback (strict-JSON).
  same-vendor caveat closed.
```

---

## D. Reclassification banner — add to superseded single-company "vector" reports

```markdown
> ⚠️ **LABEL CORRECTION (R15 audit).** The `vector_only` arm in this report is `per_case_evidence_only`
> = the case's own evidence text, **not corpus retrieval**. It is reclassified `case_text_only`.
> The numbers are unchanged; only the label is corrected. The only real retrieval-vs-graph comparison
> in this project is R14 (cross-company), re-confirmed against a fair chunk retriever in R15.
```

---

## E. Hypothesis Validation Table (paste into README — piso7-style, honest PASS/FAIL)

Show the FAILs, don't hide them — that is the trust signal.

```markdown
## Hypotheses — What Held and What Didn't

| ID | Hypothesis | Verdict | Evidence |
|---|---|---|---|
| H1 | graph_structured > weak vector (single query) | ✅ PASS | 0.825 vs 0.062 AC; holds under all 4 judges |
| H2 | graph_structured ≥ strong vector (per-company decomposition) | ✅ PASS | 0.825 vs 0.087; vs fair chunk retriever 0.113 |
| H3 | graph_guided_text ≥ graph_structured | 🟡 MIXED | ~tied (0.800 vs 0.825); judge-dependent (Grok: guided>structured; Kimi: reverse) |
| H4 | graph retrieves *both* companies more than vector | ✅ PASS | both_companies_found 1.00 vs 0.13–0.46 |
| H5 | vector failure = single-company *coverage* gap, not reasoning | ✅ PASS | 93.3% of failed vector_single cases are missing-company |
| H6 | graph≫vector ranking is judge-invariant | ✅ PASS | unanimous across gpt-4o / DeepSeek / Kimi / Grok (Fleiss' κ 0.53) |
| H7 | graph win is not a context-budget artifact | ✅ PASS | graph wins on ¼ the tokens (1.7k vs 6.5k) |
| L3 | graph handles multi-year **trend** queries | 🔴 FAIL | graph L3 = 0.30 (hard for everyone; vector L3 = 0.00) |
| SC | single-company: graph ≥ text | 🔴 FAIL | text/case-text ≥ graph (FinDER 0.47 vs 0.18); graph's value there is coverage, not reasoning |

**Net:** graph's advantage is real and robust **for cross-company structural comparison** (H1–H2, H4–H7),
*tied* on structured-vs-guided (H3), and **does not hold** for single-company QA (SC) or multi-year trend
(L3). We report the two FAILs as first-class results, not footnotes.
```

---

## F. Cost & Compute ledger (paste into README — what is and isn't tracked)

```markdown
## Cost & Compute

All generation ran on **gpt-4o-mini** (gpt-4o only for the R11 ablation + naive baseline); judges add
gpt-4o + 3 cross-vendor models; embeddings use `text-embedding-3-small`. Per-round cost was
**reconstructed offline** by summing `usage` tokens across every trace JSONL (deduped by `trace_id`) at
standard OpenAI rates — script: `scripts/round15_cost_ledger.py`.

| Round | Calls | Est. cost |
|---|---:|---:|
| R3 dev / locked-test | 228 | $0.18 |
| R4-R7 (infra / diagnostic) | 350 | $0.38 |
| R8 / R9C (clean held-out) | 299 | $0.23 |
| R10 (+ v2 rescore, shared traces) | 752 | $0.55 |
| R11 gpt-4o ablation | 50 | $0.60 |
| naive baseline (4o + mini) | 100 | $0.16 |
| R12 / R13 (controls) | 502 | $0.37 |
| **R14 cross-company (headline)** | 160 | $0.20 |
| R14B FinQA scale | 1,260 | $0.18 |
| R15 fair re-eval | 400 | $0.43 |
| **OpenAI generation subtotal** | **~4,100** | **$3.28** |
| R15 cross-vendor judge panel | ~1,200 | $4.80 |
| **Grand total** | | **~$8.08** |

*gpt-4o-mini throughout keeps the whole 15-round program under ~$8. Pricing assumes standard OpenAI
rates (adjust `PRICE` in the script for actuals); R10 original and v2-rescore share trace_ids (deduped,
counted once); embeddings negligible, not itemized.*
```
