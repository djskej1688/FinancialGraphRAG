# Claude Code Task — Repo Publish v3 (R15-complete, honest packaging)

> 이 파일 + §2 입력 파일들을 **공개 repo 루트**에 두고 Claude Code에게:
> "Read `CLAUDE_CODE_TASK_v3_R15.md` and execute step by step. Confirm the plan before committing."

## 0. 목표 (한 줄)
README는 **이미 최종 완성됨**(`README_FINAL.md`). 이 task는 그것을 repo의 README로 앉히고,
`reports/` + `docs/`를 구성하되 **(1) year-bug superseded 배너 + (2) R15 vector-provenance 재분류 배너**를
달고, **`docs/PROVENANCE.md`를 1급 정직성 산출물**로 추가해 publish-ready로 만든다.

## 1. 절대 규칙 (GUARDRAILS — 위반 금지)
1. **숫자를 새로 계산/추정하지 마라.** §3 canonical 값과 제공 파일만 사용. 어떤 수치도 변경 금지.
2. **`README_FINAL.md`를 다시 쓰지 마라.** 그대로 repo 루트 `README.md`로 복사한다(상대링크 깨짐만 수정 허용).
3. **기존 보고서/스크립트를 삭제·수정하지 마라.** 폐기/재분류 대상은 *상단 배너만* 추가.
4. **scripts/ 로직 수정 금지.** 이 작업은 문서/배치/커밋만.
5. 새 브랜치 `repo-publish-v3`에서 작업. 커밋은 §6 논리 단위로. push 전 사용자 확인.

## 2. 입력 파일 (실행 전 repo에 배치할 것)
seocho 산출물(아래)을 repo에 복사해 둔다:
```
README_FINAL.md                         # -> repo README.md (그대로)
EVALUATION_METRICS.md                   # 채점 정책
cost_ledger.md                          # 비용 원장
README_R15_patch.md                     # §E 가설표 / §F 비용표 원문 참조용
graphrag_portfolio_synthesis_R8_R14.md  # 전체 종합(내부 근거)
05_multijudge/{judge_robustness_verdict.md, kappa_agreement.md, panel_method_scores.csv}
04_reeval/{graph_survival_verdict.md, method_scores.csv}
00_provenance_audit/{vector_arm_audit.md, reclassification_manifest.json}
scripts/round15_portfolio_visuals.py    # R15-correct chart generator (hardcoded numbers)
outputs/portfolio_r15/*.png             # 3 core figures -> place in repo figures/
```
+ 라운드 보고서(로컬 보유): `round04..round14` + `round10_v2_rescore`, `round11_ablation`,
  `round12_kgsrc`, `round13_kg_rebuild`, `round14_summary`, `naive_baseline_v2_comparison` 등.

## 3. CANONICAL 수치 (pinned — 변경 금지, 검증용)
```
R10 v2 (corrected single-company): case_text_only(=옛"vector") 0.673 > hybrid 0.638 > graph 0.498.
  FinDER graph 0.177 / case_text 0.469 / hybrid 0.408. FinQA 0.75/0.821/0.857. TAT-QA 0.923/0.954/0.908.
  (orig superseded: graph 0.610 / vector 0.570.)
Naive v2: graph 0.52 / naive gpt-4o 0.64 / mini 0.54. (orig superseded 0.62/0.56.)
R11 v2: graph 4o 0.44 / mini 0.48. FinDER 0.12/0.24. FinQA 0.76/0.72.
R12 (186): source_text_only 0.409 > graph_kgsrc 0.344 ≈ graph_only 0.350 < text 0.575.
R13 (130): graph_v13 0.215 > graph_v10 0.177, both < text 0.30 < case_text 0.469.
  단일지표 parity: operating_margin 0.714=0.714, net_margin 0.667=0.667.
R14 (80 synthetic cross-company): graph_structured 0.825 (NC 0.965, tokens 2357) /
  graph_guided 0.800 / source_concat 0.338 / vector_multi 0.088 / vector_single 0.063.
  both_found graph 1.00 vs vector 0.225/0.125. failed vector_single missing-company 0.933.
  coverage: 323 companies / 34 metrics / 5640 obs / 5246 triples / 0 unreachable / 80/80 structural.
  by-level graph_structured: L1 0.925 / L2 0.867 / L3 0.300.
R14B (400 FinQA): structured 0.3025 / hybrid 0.255 / gold_text(옛"vector_only_scaled") 0.2025.
  structured-text +0.10 (CI excl 0). 이것은 retrieval 아닌 fact-presentation ablation.
R15 fair re-eval: vector_single_chunk AC 0.163 / vector_multi_chunk 0.113 vs graph 0.825.
  both_found 0.46/0.38. margin R14 0.7375 -> fair 0.7125 (graph survives). graph wins on ¼ prompt tokens
  (1708 vs 6466).
R15 4-vendor judge: gpt-4o/DeepSeek-v4-pro/Kimi(moonshot-v1-128k fallback)/Grok-4.3.
  per-judge graph_structured 0.60/0.58/0.61/0.49; graph > every vector arm under ALL judges (unanimous).
  Fleiss' κ 0.53 (moderate; disagreement on partial boundary, NOT ranking). $4.80.
Cost: OpenAI gen $3.28 + judge panel $4.80 = total ~$8.08 (15 rounds, gpt-4o-mini 위주).
Provenance: R3-R10 vector_only = per_case_evidence_only (case_text); R14B = gold_text; only R14 = real retrieval.
```

## 4. 작업 절차

### A. README
`README_FINAL.md` → repo 루트 `README.md`로 복사(덮어쓰기). 내용 재작성 금지.

### B. reports/ 구성 + 배너 2종
라운드 보고서를 `reports/`로 이동(영문 슬러그 권장: `round10_report.md` 등).

**B-1. SUPERSEDED 배너 (year-bug)** — 다음 상단에 삽입(내용 불변):
대상: `round10_report.md`(R10 원본), `round09c_report.md`, `round09a_report.md`, 원본 naive 보고서.
```markdown
> ⚠️ **SUPERSEDED.** Produced under the pre-v2 (year-bug) scorer contract; kept for traceability only.
> Corrected reference: `round10_v2_rescore_report.md`. See README → "What Went Wrong".
```

**B-2. RECLASSIFIED 배너 (R15 provenance)** — 단일기업 "vector_only" arm을 보고하는 보고서 상단:
대상: `vector_only`를 단일기업 결과로 보고한 모든 보고서(R8/R9C/R10/R12/R13 계열).
```markdown
> ⚠️ **LABEL CORRECTION (R15 audit).** The `vector_only` arm here is `per_case_evidence_only` — the
> case's own evidence text, **not corpus retrieval** — reclassified `case_text_only`. Numbers unchanged;
> only the label is corrected. The only validated retrieval-vs-graph comparison is R14. See docs/PROVENANCE.md.
```

### C. docs/ 생성
- `docs/RESULTS_SUMMARY.md` — 한국어 narrative 3~5문단. README와 **동일 수치**로: 초기 graph 우위 →
  year-bug 발견·역전 → R12/R13 → R14 cross-company 우위 → R15 provenance 감사 + fair vector + 4벤더 judge로
  방어 완성. (새 수치 만들지 말 것.)
- `docs/CLAIM_BOUNDARIES.md` — 라운드별 `claim_boundary` 표(round / allowed / not-allowed).
- `docs/EXPERIMENT_LOG.md` — R4~R15 한 줄 요약 + 상태(diagnostic/pre-v2/corrected/headline/scale/hardening).
- `docs/EVALUATION_METRICS.md` — 입력 `EVALUATION_METRICS.md` 복사(채점 정책: number_overlap/token_f1/judge_score).
- **`docs/PROVENANCE.md` (신규, 1급 산출물)** — `vector_arm_audit.md` + `reclassification_manifest.json`
  기반. 포함: (1) 분류 카운트표(per_case_evidence_only 13 / gold_context 1 / real_retrieval 2),
  (2) arm별 verdict 표, (3) **"single-company 'vector'=case_text; R14B=gold_text; only R14=real
  retrieval"** 명시, (4) fair chunk retriever로 R14 재확인됨(margin 0.7375→0.7125). 새 수치 금지.

### D. 증거 산출물 배치
`reports/evidence/`(또는 docs/evidence/)에 복사: `cost_ledger.md`, `graph_survival_verdict.md`,
`kappa_agreement.md`, `panel_method_scores.csv`, `vector_arm_audit.md`. README/문서에서 상대링크로 참조.

### E. 시각화 (R15-correct 차트로 교체)
**옛 PNG는 year-bug 이전 수치(graph>vector single-company)라 README와 모순** → 그대로 publish 금지.
1. `scripts/round15_portfolio_visuals.py`(수치 하드코딩, 데이터파일 불필요)를 repo에 두고
   `python scripts/round15_portfolio_visuals.py` 실행 → **3 core charts**
   (`fig1_r14_headline`, `fig2_both_companies_found`, `fig4_judge_invariance`)를 `figures/`에 생성
   (또는 seocho `outputs/portfolio_r15/*.png`를 `figures/`로 복사).
2. README가 `figures/figN_*.png`를 참조하므로 그 경로에 둘 것.
3. **기존 stale PNG(round_progression/dataset_method_comparison/naive_comparison 등)는
   `figures/_archive_superseded/`로 이동**(삭제 아님)하고, README에서 참조 제거. 옛 차트를 README 본문에
   남기지 말 것(모순 방지). 차트 로직/수치 변경 금지 — 이미 canonical과 일치함.

### F. .gitignore
대용량 원본 trace는 ignore 유지. 단 PNG, `state.json` 요약, 새 `docs/*.md`, `reports/*.md`,
`cost_ledger.{md,json}`는 커밋되게.

## 5. 검증 체크리스트 (커밋 전)
```
- [ ] repo README.md == README_FINAL.md (재작성 흔적 없음)
- [ ] README/문서 모든 수치가 §3 canonical과 일치
- [ ] year-bug 보고서 전부 SUPERSEDED 배너
- [ ] 단일기업 vector 보고서 전부 RECLASSIFIED 배너
- [ ] docs/PROVENANCE.md 존재 + "only R14 = real retrieval" 명시
- [ ] docs/EVALUATION_METRICS.md == 입력본
- [ ] 차트가 README 수치와 모순 없음
- [ ] git log가 논리 단위로 분리
```

## 6. 커밋 플랜
```
1. docs: set finalized R15 README (FINAL)
2. reports: organize R4-R15; add year-bug SUPERSEDED + R15 RECLASSIFIED banners
3. docs: add RESULTS_SUMMARY(KR), CLAIM_BOUNDARIES, EXPERIMENT_LOG, EVALUATION_METRICS, PROVENANCE
4. reports(evidence): add cost ledger, judge κ/robustness, vector arm audit
5. viz: regenerate/caption charts to v2/R15-corrected scores  (해당 시)
6. (push 전 사용자 확인) git push -u origin repo-publish-v3 -> PR
```

## 7. 마지막 보고
변경 파일 목록, 배너 단 파일(2종 각각), 생성한 docs, 재생성/캡션한 차트, 그리고 **수치·라벨이 README와
불일치했던 항목**을 따로 적어 사용자 확인 요청.
```
print:
- README == README_FINAL: yes/no
- superseded banners added: <count>
- reclassified (provenance) banners added: <count>
- docs created: [RESULTS_SUMMARY, CLAIM_BOUNDARIES, EXPERIMENT_LOG, EVALUATION_METRICS, PROVENANCE]
- charts regenerated/captioned/removed:
- numbers altered: none (must be none)
- branch: repo-publish-v3
```
