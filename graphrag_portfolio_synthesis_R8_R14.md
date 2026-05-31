# When Does GraphRAG Beat VectorRAG for Financial QA?
## Portfolio Synthesis — Rounds 8–15

**Scope:** S&P 500 10-K filings, text/table financial QA (FinDER, FinQA, TAT-QA).
**Stack:** DozerDB/Neo4j knowledge graph + LLM (gpt-4o-mini / gpt-4o), `scorer_v9` numeric scorer +
R15 judge layer (`number_overlap`/`token_f1`/`judge_score`), persistent chunk vector index, JSONL traces.
**Reference frame:** arXiv:2502.11371 (RAG vs GraphRAG) — *graph's advantage is relational/comparative/aggregative reasoning, not single-hop recall.*

> ✅ **R15 hardening complete.** The two retrieval-provenance issues and the missing judge layer that
> bounded earlier claims have been **repaired in R15 (§6.5)**: every "vector" arm is now correctly
> classified (real retrieval vs gold-context), a chunk-level persistent retriever was built, and a
> second independent scorer (LLM judge) confirms the ranking. The headline (graph ≫ vector in
> cross-company comparison) **survived** the stronger vector — and graph wins on ¼ the tokens. The cross-company win was then confirmed *unanimously* by a 4-vendor judge panel
> (gpt-4o, DeepSeek, Kimi, Grok), closing the same-vendor concern (§7.2).

---

## 1. Thesis (one paragraph)

For **single-company factoid/lookup** questions, a text baseline is at least as good as the graph —
the KG adds retrieval overhead and distractors without adding reasoning value. For **multi-company
comparison** questions, the graph **decisively** wins, because the task requires retrieving and
aligning facts across *two* entities and then computing over them — exactly where flat top-k
retrieval breaks down and a canonical-metric graph does not. This reproduces the paper's central
claim on our own data and, just as importantly, the project's value is in the **honest correction of
several earlier artifacts** that had falsely shown graph winning everywhere.

---

## 2. Methods Compared

| Family | Method | Context construction |
|---|---|---|
| Vector | `vector_single` | query → top-k passages (cosine) |
| Vector | `vector_multi_by_company` | per-company sub-queries → union of top-k (strong baseline) |
| Graph | `graph_structured` | targeted KG facts `(company, metric, year)` only |
| Graph | `graph_guided_text` | KG identifies cells → serve the matching evidence passages |
| Control | `source_text_concat` | both companies' full evidence text, no retrieval (coverage upper bound) |

All arms share one scorer (`scorer_v9`) and one formula contract per case.

---

## 3. The Honest Reversal — why early "graph wins" was an artifact

The most important methodological content of this project is that **the first signals were wrong**,
and we found out *why*. Four root causes, all traceable to naïve number handling without semantics:

**3.1 Year-bug (contract expected_value = a year).**
FinDER answers are explanatory prose. The contract generator's `parse_number()` grabbed the *first*
number in the answer — frequently a year (`2023`) — and set it as the expected value. 63.8% of FinDER
target slots were affected. Methods that *mentioned* the year (chatty `mini`) false-passed; precise
methods failed. Fixed via `extract_final_answer_from_text()` (last `= X%` / last non-year number) +
contract validation that rejects all-year target slots.

**3.2 Fake KG (header garbage as facts).**
The fallback contract used `all_numbers(evidence)[:8]`, scraping table headers — years, the "31" from
"December 31" — and emitted `source_value_NN` placeholders. 130/130 FinDER cases were polluted. FinQA
survived only because its program operands are real values. Fixed by semantic IE (real metric/value/
year extraction) with validation filters.

**3.3 The gpt-4o paradox (the smoking gun).**
On FinDER, gpt-4o scored *lower* than gpt-4o-mini (0.08 vs 0.40). The stronger model answered
precisely (no year), so it failed the year-matching scorer; the weaker model rambled years and
false-passed. **A more accurate model scoring lower is proof the scorer, not the model, was broken.**

**3.4 KG-as-context interference (R12).**
Putting KG facts *on top of* source text *reduced* accuracy: `source_text_only` (0.409) >
`graph_kgsrc` (0.344). Extra structured context acted as distractor, not signal.

**3.5 Over-retrieval (R13).**
With a *real* KG, graph dumped ~17 facts/case (every metric for the company). Single-metric questions
tied vector; multi-year questions lost to vector because the relevant numbers were buried in
irrelevant ones. Targeted retrieval, not "give the model everything," is the lesson.

**Net correction:** on single-company FinDER, the true ordering is **vector ≥ graph**, not the
reverse we first reported. The naïve-baseline rescore likewise flipped (cheaper-model-with-KG no
longer beats a clean gpt-4o text baseline).

---

## 4. Round Arc (R8 → R14)

| Round | Question | Result |
|---|---|---|
| R8–R10 | Does graph beat vector on FinDER/FinQA? | Apparent yes → traced to year-bug + fake KG |
| R11 | Does a stronger model (gpt-4o) help graph? | Paradox: scored lower → exposed broken scorer |
| R12 | Does KG text *on top of* source help? | No — KG-as-context interferes (H1 of R12) |
| R13 | Rebuild a *real* semantic KG; re-test single-company | graph = vector on single-metric; vector > graph on multi-year (over-retrieval) |
| R14 | **Multi-company comparison** (the untested regime) | **graph ≫ vector (decisive)** |
| R14B | FinQA at scale: does fact-structuring help? | structured > raw text by +0.10 (see §6 caveat) |

---

## 5. R14 — Cross-Company Comparison (the positive result)

**Setup.** 277 fresh held-out S1 (Financials+Compositional) cases → canonical-metric KG (FIBO
hygiene: one canonical node per metric, synonyms as annotations). 5,640 observations, 323 companies,
34 canonical metrics. 80 synthetic cross-company comparison cases (L1 direct / L2 derived margin /
L3 trend), ground truth *derived* from verified observations. 5 methods × 80 = 400 traces.

**Results (mean answer_correctness):**

| Method | AC | both_companies_found |
|---|---:|---:|
| **graph_structured_v14** | **0.825** | 1.00 |
| **graph_guided_text_v14** | **0.800** | 1.00 |
| source_text_concat_v14 | 0.338 | 1.00 |
| vector_multi_by_company_v14 | 0.087 | 0.225 |
| vector_single_v14 | 0.062 | 0.125 |

**Mechanism — a clean three-step causal chain:**
1. **Vector cannot retrieve both companies at once** (both_found 0.12–0.22). A single comparison query
   embeds near one company; decomposing per company (multi) barely helps (0.225). → H5 = **0.933**:
   93% of vector failures are single-company coverage gaps, not reasoning errors.
2. **Coverage alone is not enough.** `source_text_concat` *has* both companies' full text
   (both_found 1.00) yet scores only 0.338 — the model drowns in a wall of text. So the graph win is
   **not** "having both companies," it is **distractor-free targeting** (the R13 over-retrieval lesson,
   mirrored).
3. **Graph supplies both companies as targeted facts** → 1.00 coverage + 0 distractors → 0.80–0.83.

**Hypotheses:** H1 (graph > weak vector) TRUE; **H2 (graph ≥ strong decomposed vector) TRUE** — the
strong claim; H3 (guided ≥ structured) FALSE but tied (0.800 vs 0.825); H4 (coverage) TRUE; H5 0.933.

**Integrity (Neo4j-verified, read-only post-hoc audit).** 5,246 / 5,246 written `(ticker,metric,year)`
triples are reachable `Company→Observation→Metric→Year`; **0 silent write failures**; all 80 cases
route to `structural_graph` (verified in the graph, not just in the write log). An interrupted-run
truncated log line was reconciled against Neo4j as source of truth — KG batch is complete.

---

## 6. R14B — FinQA at Scale (reframed honestly)

400 accepted FinQA cases, 1,200 attempts, 0 provider failures, $0.17.

| Arm | AC | 95% CI |
|---|---:|---|
| `structured_facts_only_scaled` | 0.3025 | [0.258, 0.350] |
| `hybrid_text_structured_scaled` | 0.2550 | [0.213, 0.300] |
| `vector_only_scaled` *(see caveat)* | 0.2025 | [0.165, 0.243] |

Pairwise: structured − text **+0.10**, CI [+0.053, +0.150] (excludes 0 → significant);
hybrid − text +0.053, CI [+0.013, +0.093] (excludes 0).

> **CAVEAT (critical).** `vector_only_scaled` does **not** retrieve. It returns the case's *own* gold
> `evidence_text[:6000]` — there is no corpus, embedding, or top-k. R14B is therefore **not a retrieval
> benchmark**; it is a **fact-presentation ablation on gold context**: "structured operand tables vs raw
> gold text vs both." The honest finding is *"structuring facts aids multi-step FinQA arithmetic by
> +0.10 even when the text is already gold"* — a reasoning-aid result, **not** "structured beats vector
> retrieval." The arm must be renamed `gold_text_only` before publication (§7.1).

FinDER and ConvFinQA accepted 0 here — **by design**: the acceptance gate's HR1/HR2 guards correctly
rejected FinDER's fallback-garbage contracts (357/386 `contract_validation_failed`), and ConvFinQA is
absent locally (not silently substituted).

---

## 6.5 R15 — Hardening: fair vector retriever + independent judge (the defensibility round)

R15 was **not a new experiment** — it closed the provenance/scoring gaps that bounded R14's claim, so
the result can be published without an asterisk.

**Provenance audit (R15 Phase 0).** Every "vector" arm across the project's history was classified by
*implementation*, not by name. Finding: **13 of the historical `vector_only_*` arms (R3–R10, incl.
`vector_only_v10`) were `per_case_evidence_only`** — they fed the model the case's *own* evidence text,
**not retrieval** — and were relabeled `case_text_only_*`. R14B's `vector_only_scaled` was relabeled
`gold_text_only`. **Only R14's two arms were ever real retrievers.** Implication: the single-company
"vector ≥ graph" result is really *"case text ≥ graph facts,"* not a retrieval comparison (originals
frozen; reclassified via sidecar, never deleted).

**Fair retriever (R15 Phase 1).** Built a chunk-level, persistent, provenance-logged index
(`text-embedding-3-small`, 1200-char chunks, on-disk `numpy_ondisk` backend — LanceDB/FAISS not
installed in the env; label is honest). Every retrieval logs `chunk_id`, `source_case_id`, score.

**Independent judge (R15 Phase 2).** Authored `EVALUATION_METRICS.md` and added three uniform metrics
across all arms: `number_overlap`, `token_f1`, and an LLM `judge_score` (strict-JSON verdict). Judge =
gpt-4o; **same vendor as the gpt-4o-mini generator → bias disclosed**, but the bias is *symmetric*
across all five arms (all mini-generated), so the comparison stands; only absolute judge calibration is
affected.

**Re-evaluation (R15 Phase 3) — graph survives the fair vector:**

| Method | scorer_v9 AC | judge_score | both_found | mean prompt tokens |
|---|---:|---:|---:|---:|
| **graph_structured_v14** | **0.825** | **0.600** | 1.00 | **1,708** |
| graph_guided_text_v14 | 0.800 | 0.581 | 1.00 | 2,923 |
| source_text_concat_v14 | 0.338 | 0.256 | 1.00 | 2,796 |
| vector_single_chunk_v15 *(fair)* | 0.163 | 0.088 | 0.46 | 6,466 |
| vector_multi_by_company_chunk_v15 *(fair)* | 0.113 | 0.081 | 0.38 | 7,130 |

- **The chunk retriever improved vector** (single 0.063→0.163, both_found 0.125→0.46) but **graph still
  wins ~5× on AC and ~7× on judge.** Head-to-head margin barely moved (R14 0.7375 → fair 0.7125).
- **Graph wins on ¼ the tokens** (1,708 vs 6,466). So "graph got more/cleaner context" is *false* —
  graph is both more accurate *and* more token-efficient.
- **both_found stays < 0.46** even with chunk-level retrieval + query decomposition + 4× the token
  budget → the bottleneck is **structural** (similarity search cannot assemble a multi-entity
  comparison context), not retriever quality. This is the paper's thesis, now isolated cleanly.
- **scorer_v9 and the independent judge agree** on the ranking → robust to scorer choice.

**Publishable conclusion:** *R14's vector baseline was weak, but after correcting with R15's chunk-level
persistent retriever and a second independent scorer, the graph advantage in cross-company structural
queries persists — and graph achieves it with ¼ the tokens of vector.*

---

**Multi-vendor judge panel (R15 Phase 2.5).** To remove the same-vendor concern, all five arms were
re-judged by a **4-vendor panel** — gpt-4o, DeepSeek (v4-pro), Kimi (Moonshot), Grok (4.3) — same prompt,
different models, 1,598 valid judgments, $4.80, 0 generation calls.

| Arm | gpt-4o | DeepSeek | Kimi | Grok |
|---|---:|---:|---:|---:|
| graph_structured | 0.60 | 0.58 | 0.61 | 0.49 |
| graph_guided_text | 0.58 | 0.58 | 0.57 | 0.54 |
| source_text_concat | 0.26 | 0.22 | 0.25 | 0.12 |
| vector_single_chunk | 0.09 | 0.14 | 0.13 | 0.04 |
| vector_multi_chunk | 0.08 | 0.09 | 0.12 | 0.03 |

**graph ≫ every vector arm under all four judges — the arm ranking is judge-invariant** (graph >
source_concat > vector, unanimous). Inter-judge agreement Fleiss' κ = 0.53 (moderate); critically, the
disagreement is on the *partial*-vs-correct/incorrect boundary (Grok scores nearly binary, gpt-4o uses
*partial* heavily), **not** on the graph-vs-vector ordering — and the graph-vs-vector margin (≥0.35
under every judge) dwarfs the inter-judge noise. **The headline rests on ranking agreement (unanimous),
which κ — a per-item verdict metric — understates.** Same-vendor caveat closed. *(Kimi `kimi-k2.6`
failed strict-JSON smoke → fell back to `moonshot-v1-128k`, disclosed.)*

---

## 7. Methodological Limitations & Provenance (the part reviewers will probe)

**7.1 Vector-baseline provenance — [RESOLVED in R15, §6.5].** Every "vector" arm is now classified by
implementation. Historical `vector_only_*` (R3–R10) were `case_text_only` (gold context, no retrieval);
R14B was `gold_text_only`; only R14 used real retrieval. R14's document-level in-memory retriever was
*replaced/cross-checked* by a fair chunk-level persistent retriever (`vector_*_chunk_v15`); the graph
margin held (0.7375 → 0.7125). We never labeled anything "LanceDB" (the env had no LanceDB/FAISS; the
on-disk backend is honestly labeled `numpy_ondisk`). **Single-company claims are stated as "case text ≥
graph," not "vector retrieval ≥ graph."** Residual: the *literal* persistent store is numpy, not
LanceDB — swapping is a one-line `pip install lancedb` + rebuild if symmetry-with-DozerDB is wanted.

**7.2 Judge layer — [ADDED in R15, §6.5], now cross-vendor-confirmed (R15 Phase 2.5).** `number_overlap` + `token_f1` +
LLM `judge_score` now score all arms uniformly, and agree with `scorer_v9` on the ranking. **Caveat:
the judge (gpt-4o) shares a vendor with the generator (gpt-4o-mini)** — no cross-vendor key was
available. The self-preference bias is *symmetric across arms* (all answers are mini-generated), so it
does not change the graph-vs-vector comparison, R15 Phase 2.5 then ran exactly that — a **4-vendor panel**
(gpt-4o + DeepSeek + Kimi + Grok) ranks graph > every vector arm *unanimously* (Fleiss' κ = 0.53; the
disagreement is on the partial-vs-correct boundary, **not** the ranking). **The same-vendor caveat is
closed.**

**7.3 Synthetic cross-company queries.** R14 questions are programmatically generated from comparable
KG cells, not sampled from real user queries. The claim is bounded to this distribution.

**7.4 Coverage ≠ retrieval-win.** R14 `structural_ratio = 1.0` *by construction* — synthesis only
selected metric-year cells that ≥2 companies share. So R14 measures *"given the KG can answer, graph
retrieval beats vector retrieval,"* which is **separate** from *"how often the KG can answer"*
(coverage breadth: 323 companies, 131 comparable cells). Both must be reported separately.

---

## 8. Decision Guidance — when to use which

| Question shape | Use | Why |
|---|---|---|
| Single passage / single fact lookup | **Text/vector** | KG adds retrieval cost + distractors, no reasoning gain (note: our single-company evidence was case-text, not retrieval — §6.5) |
| Single company, multi-year aggregation | **Targeted** retrieval | flat top-k OK if facts co-located; avoid graph over-retrieval |
| **Multi-company comparison / cross-entity aggregation** | **Graph (structured or guided)** | structurally retrieves *all* entities + distractor-free targeting; wins on ¼ the tokens (§6.5) |
| Arithmetic over known operands | Structured facts > raw text | pre-extracted operands aid multi-step reasoning (R14B) |

---

## 9. Status & Remaining Work

**Done in R15 (§6.5):** ✅ vector baseline rehabilitated (chunk-level persistent retriever);
✅ all historical vector arms audited & honestly reclassified (case_text vs retrieval); ✅ R14B renamed
`gold_text_only`; ✅ judge layer added (`number_overlap`/`token_f1`/`judge_score`); ✅ R14 graph margin
re-confirmed against the fair vector — **it survived, on ¼ the tokens.**

**Remaining (nice-to-have, not blocking publication):**
1. ✅ **[DONE — R15 Phase 2.5; Fleiss' κ 0.53, ranking unanimous]** Cross-vendor judge (gpt-4o/DeepSeek/Kimi/Grok) to remove the same-vendor caveat (§7.2) — and to report
   inter-judge agreement (Cohen's κ), the way a polished diagnosis repo does.
2. **Literal LanceDB** backend (`pip install lancedb` + rebuild) if persistent-store symmetry with
   DozerDB is wanted for the narrative.
3. **Attribution analysis** (e.g. ContextCite) to show *which* chunks vector retrieved and confirm the
   "missing-company" failure mode at the document level, not just via `both_companies_found`.

*The headline result is now defensible; these items raise polish/rigor, not direction.*
