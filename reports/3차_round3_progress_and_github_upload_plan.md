# GraphRAG / FinDER 프로젝트 진행 현황 보고서 및 GitHub 업로드 계획

- 작성일: 2026-05-27
- 작성 목적: 지금까지의 Round 1–3 진행 상황을 한 문서로 정리하고, GitHub에 올릴 파일과 올리면 안 되는 파일을 구분한다.
- 현재 기준 상태: Round 3는 **test/full evaluation 전 단계**이며, v3.1 dev/baseline dry-run까지 완료되었다. 현재 다음 액션은 **no-model root-cause audit**이다.

---

## 1. 한 줄 요약

현재 프로젝트는 **FinDER 기반 GraphRAG 평가 파이프라인을 구축하고, selected 7 기준 Round 2 평가까지는 의미 있는 결과를 확보한 상태**다. Round 3는 고품질 케이스 확장과 dual-track 평가 구조까지 만들었지만, 아직 locked test/full evaluation으로 넘어가면 안 된다. 현재 blocker는 모델 API 실패가 아니라 **scoring / context assembly / prompt contract mismatch 가능성**이다.

---

## 2. 프로젝트 목표

이 프로젝트의 핵심 목적은 두 가지다.

1. **vector 검색보다 graph 기반 구조화 정보가 multi-fact financial reasoning에서 더 나은지 확인한다.**
2. **서로 다른 맥락의 금융 정보를 graph로 통합하는 모습을 보여준다.**

초기에는 live Neo4j KG 기반 평가를 목표로 했지만, Round 3에서 live KG coverage와 schema mismatch 문제가 드러났다. 그래서 현재는 다음 두 track으로 분리되었다.

| Track | 의미 | 현재 상태 | claim boundary |
|---|---|---|---|
| Track A | Live Neo4j KG 기반 coverage-first diagnostic | 6 cases, dev/test/baseline = 3/2/1 | live KG diagnostic only |
| Track B | exact-quote verified facts 기반 shadow overlay graph | 25 cases, dev/test/baseline = 12/10/3 | shadow overlay scoped evaluation only |

---

## 3. Round 1–2 진행 요약

### 3.1 Round 1: Coverage gate / pipeline smoke test

Round 1에서는 현재 KG 상태를 freeze하고 selected 7개 case가 실제 GraphRAG evaluation에 들어갈 수 있는지 coverage를 확인했다.

- 기준 KG batch: `kg-full-provenance-20260524`
- curation round: `01`
- selected cases: 7개
- ready_for_eval: `1 / 7`
- 실제 평가 가능 case: `e7129c27`
- 나머지 6개: missing required facts로 not-ready

Round 1의 의미는 성능 비교가 아니라, **coverage gate가 제대로 작동하는지 확인한 것**이다. 즉, graph에 필요한 source facts가 없으면 평가를 억지로 돌리지 않고 차단했다.

### 3.2 Round 2: Targeted curation + selected 7 full evaluation

Round 2에서는 Round 1에서 막힌 selected 7의 missing facts 11개를 targeted curation으로 보완했다.

- applied fixes: `11`
- unresolved: `0`
- ready_for_eval: `1 / 7` → `7 / 7`
- missing_required_facts: `11` → `0`
- Graph delta: `+16 nodes`, `+33 relationships`
- LLM 호출: `0`

그 후 selected 7 전체에 대해 4개 method로 evaluation을 실행했다.

| Method | Case count | Avg numeric correctness | Avg answer correctness | Avg faithfulness | Avg required fact recall | Failure count |
|---|---:|---:|---:|---:|---:|---:|
| vector_only | 7 | 0.6109 | 0.7576 | 0.8054 | 1.0 | 4 |
| graph_facts_only | 7 | 0.5663 | 0.7450 | 0.7832 | 1.0 | 3 |
| hybrid_vector_graph | 7 | **0.8490** | **0.9643** | **0.9245** | 1.0 | **1** |
| gold_context | 7 | 0.6878 | 0.8400 | 0.8439 | 1.0 | 2 |

Round 2의 핵심 해석은 다음과 같다.

> selected 7 기준에서는 `hybrid_vector_graph`가 가장 강하게 나왔다. 다만 이는 작은 curated subset 기준의 결과이므로, 전체 FinDER 또는 전체 live KG 성능으로 일반화하면 안 된다.

---

## 4. Round 3 진행 요약

Round 3의 목표는 selected 7을 넘어서 더 큰 고품질 평가셋을 만드는 것이었다. 그러나 이 과정에서 case 품질, KG coverage, schema mismatch, scoring contract 문제가 단계별로 드러났다.

### 4.1 Case factory v0

처음에는 자동 case factory로 다음과 같은 결과가 생성되었다.

- longlist: 5,696
- selected total: 50
- round3_dev: 20
- round3_test: 20
- baseline_control: 5
- integration_demo: 5
- required facts: 400
- initial ready_for_eval: 50 true / 0 false

하지만 preflight validation에서 다음 문제가 드러났다.

- evidence_quote 400개가 모두 원문 exact excerpt가 아니라 parser-generated synthetic row string이었다.
- company/ticker issue: 23 cases
- suspicious parser artifact: 96 facts
- Neo4j coverage: not_checked_no_neo4j_config
- eval-ready subset: 0

이 단계의 교훈은 다음이다.

> candidate generation과 eval-ready certification을 분리해야 한다. `selected`라는 표현을 너무 일찍 쓰면 안 된다.

### 4.2 Repair / local eval-ready subset

그 후 exact quote, company/ticker, parser artifact를 보완하여 local 기준 subset을 만들었다.

- eval-ready local cases: 25
- eval-ready required facts: 139
- split: round3_dev 12, round3_test 10, baseline_control 3
- category: Financials 23, Company Overview 2
- exact evidence quote coverage: 100%
- required fact semantic pass: 100%
- derived leakage: 0
- duplicate case_id: 0
- eval-ready company/ticker unresolved issue: 0
- Neo4j coverage: not_checked_no_neo4j_config
- decision: conditional_go

이 subset은 **local_eval_ready**로는 유효하지만, Neo4j coverage가 없어 **final_eval_ready**는 아니었다.

### 4.3 Patch path 검토와 중단

Round 3 repaired subset 중 live KG coverage가 부족한 backlog를 patch로 살리려는 시도가 있었다.

- ready partial eval cases: 6
- backlog: 19 cases / 81 missing required facts
- B0 consolidated package 생성
- B1/B2/B2a/B2b/B2c read-only validation 진행
- Neo4j write: 없음
- KG patch: 없음
- model/API call: 없음
- full eval: 없음

B2c 최종 read-only disambiguation 결과:

- approved_candidate_ready: none
- abandon_patch_path:
  - `pg_001_lin_ticker`
  - `pg_002_mdlz_alias`
  - `pg_004_bac_obs`
- defer_test_informed:
  - `pg_003_apd_fiscal`

결론:

> Patch path는 안전하게 진행할 수 없으므로 종료했다. live KG를 억지로 수정하지 않고, dual-track 평가 구조로 전환했다.

---

## 5. Dual-track Round 3 평가 준비

Patch path가 중단된 뒤, Round 3는 다음 dual-track 구조로 전환되었다.

### 5.1 Track A: Live KG coverage-first diagnostic

- live KG cases: 6
- dev/test/baseline: 3 / 2 / 1
- status: partial_only
- claim boundary: live Neo4j KG diagnostic only

Track A는 실제 Neo4j KG에 이미 존재하는 facts만 사용하므로 가장 보수적이다. 하지만 case 수가 작아 성능 주장의 메인 근거로 쓰기에는 부족하다.

### 5.2 Track B: Shadow overlay scoped evaluation

- shadow overlay cases: 25
- dev/test/baseline: 12 / 10 / 3
- status: ready_for_approval_scoped_eval
- claim boundary: shadow overlay only, not live Neo4j KG

Track B는 exact-quote verified local facts를 immutable shadow graph / overlay graph로 사용한다. live Neo4j에 patch를 적용하지 않지만, structured graph facts가 reasoning에 주는 효과를 테스트할 수 있다.

---

## 6. v3 / v3.1 dev dry-run 현황

### 6.1 v3 dev dry-run

- Track A dev/baseline attempts: 16
- Track B dev/baseline attempts: 60
- total attempts: 76
- provider failures: 0
- test split rows: 0
- model: `gpt-4.1-mini`
- temperature: 0
- Opik traces: 0, not_configured
- decision: not_ready_needs_dev_rerun

v3 결과는 실행 안정성은 좋았지만, prompt/formatter/scoring 문제로 test-ready가 아니었다.

### 6.2 v3.1 prompt/formatter/scoring patch

v3 review 후 다음을 개선한 v3.1 package가 생성되었다.

- method isolation rules
- graph fact table formatting
- JSON-only answer format
- reasoning type templates
- rounding and tolerance rules
- scoring rubric
- prompt hashes
- test lock notice
- Opik gap notice

### 6.3 v3.1 dev/baseline rerun

v3.1 dev/baseline rerun 결과:

- Track A dev/baseline attempts: 16
- Track B dev/baseline attempts: 60
- provider failures: 0
- test split rows: 0
- Opik traces created: 0
- Opik status: not_configured
- model/API called: yes
- Neo4j write: no
- KG patch: no
- full eval: no
- test eval: no
- current gate: not_ready_needs_dev_rerun

Method summary는 다음과 같다.

| Track | Method | Attempts | Avg required fact recall | Avg numeric correctness | Avg answer correctness | Avg faithfulness | Avg calculation completeness | Avg answer format compliance |
|---|---|---:|---:|---:|---:|---:|---:|---:|
| Track A live KG diagnostic | gold_context_v3_1 | 4 | 0.125 | 0.25 | 0.0 | 0.0 | 1.0 | 1.0 |
| Track A live KG diagnostic | graph_facts_only_v3_1 | 4 | 1.0 | 0.0 | 0.0 | 0.5 | 1.0 | 1.0 |
| Track A live KG diagnostic | hybrid_vector_graph_v3_1 | 4 | 1.0 | 0.0 | 0.0 | 0.75 | 1.0 | 1.0 |
| Track A live KG diagnostic | vector_only_v3_1 | 4 | 0.25 | 0.25 | 0.0 | 0.25 | 1.0 | 1.0 |
| Track B shadow overlay | gold_context_v3_1 | 15 | 0.05 | 0.5333 | 0.0 | 0.0 | 1.0 | 1.0 |
| Track B shadow overlay | graph_facts_only_v3_1 | 15 | 1.0 | 0.2 | 0.2 | 0.6 | 1.0 | 1.0 |
| Track B shadow overlay | hybrid_vector_graph_v3_1 | 15 | 1.0 | 0.5333 | 0.5333 | 0.7333 | 1.0 | 1.0 |
| Track B shadow overlay | vector_only_v3_1 | 15 | 0.0857 | 0.4667 | 0.0 | 0.0 | 1.0 | 1.0 |

현재 해석:

- provider/API 문제는 아니다.
- test contamination도 아니다.
- 안전 gate는 잘 지켜지고 있다.
- 하지만 numeric correctness와 answer correctness가 낮아 test로 갈 수 없다.
- 특히 gold_context가 비정상적으로 약하게 나와 scoring/context assembly 문제가 의심된다.

---

## 7. 현재 blocker와 다음 액션

현재 blocker는 다음이다.

1. **Scoring / answer parser 문제 가능성**
   - answer_format_compliance와 calculation_completeness는 높지만 answer/numeric correctness는 낮다.
   - 모델이 낸 답을 scorer가 제대로 읽지 못했을 가능성이 있다.

2. **Gold context anomaly**
   - gold_context는 원래 상한선 역할을 해야 하는데, Track B gold_context answer correctness가 0.0이다.
   - gold_context assembly 또는 fact recall metric 설계에 문제가 있을 수 있다.

3. **Required fact recall metric 설계 문제**
   - graph/hybrid는 fact id를 받을 수 있지만 vector/gold는 text 기반이다.
   - fact-id recall을 cross-method primary metric으로 쓰면 graph method에 유리하고 text method에 불리할 수 있다.

4. **Opik not configured**
   - dev rerun은 local-only waiver로 가능했지만 locked test 전에는 Opik 설정 복구 또는 명시적 local-only waiver가 필요하다.

### 즉시 다음 단계

다음은 **v3.2 prompt patch가 아니라 no-model root-cause audit**이다.

권장 작업명:

```text
v3.1 Dev Dry-Run Root-Cause Audit
```

목표:

- 기존 76개 v3.1 traces만 분석한다.
- 모델/API를 다시 호출하지 않는다.
- scoring issue인지, context assembly issue인지, prompt issue인지 분리한다.
- test split은 계속 잠근다.

권장 출력:

- `root_cause_audit_summary.md`
- `representative_failed_trace_sample.csv`
- `representative_failed_trace_audit.jsonl`
- `scorer_vs_model_error_matrix.csv`
- `gold_context_anomaly_audit.md`
- `answer_parser_audit.md`
- `unit_scale_rounding_audit.md`
- `context_assembly_audit.md`
- `required_fact_recall_metric_audit.md`
- `case_level_root_causes.csv`
- `rescore_candidates.jsonl`
- `prompt_patch_candidates.jsonl`
- `context_patch_candidates.jsonl`
- `case_exclusion_candidates.jsonl`
- `recommended_next_action.md`

Possible decisions:

| Decision | 의미 |
|---|---|
| `repair_scorer_only_then_rescore_no_model` | 모델 재호출 없이 기존 trace 재채점 |
| `repair_context_assembly_then_dev_rerun` | context assembly 수정 후 dev 재실행 |
| `repair_prompt_formatter_then_dev_rerun` | prompt/formatter 수정 후 dev 재실행 |
| `combined_scoring_context_prompt_patch_then_dev_rerun` | scoring/context/prompt 복합 수정 후 dev 재실행 |
| `exclude_bad_dev_cases_then_dev_rerun` | 품질 낮은 dev case 제외 후 재실행 |
| `no_go_abandon_round3_eval` | Round 3 evaluation 포기 |

---

## 8. GitHub에 올릴 것과 올리면 안 되는 것

### 8.1 GitHub에 올려야 하는 것

#### A. 코드 / 스크립트

다음은 재현성과 협업을 위해 GitHub에 올리는 것이 좋다.

```text
scripts/round3_case_factory.py
scripts/round3_preflight_validation.py
scripts/round3_repair_eval_ready.py
scripts/round3_generate_backlog_remediation.py
scripts/round3_b1_b5_file_orchestration.py
scripts/round3_dev_dryrun_v3_1.py
scripts/round3_review_dev_dryrun_v3_1.py
```

그리고 실제 repo에 존재한다면 다음도 포함한다.

```text
scripts/merge_multi_agent_reviews.py
scripts/round3_orchestrate.py
scripts/round3_operator_loop.py
scripts/round3_operator_finalize.py
scripts/round3_eval_loop.py
seocho/eval/round3/
seocho/tests/test_round3_*.py
```

#### B. Prompt / formatter / scoring spec

이 파일들은 실험 재현에 중요하므로 commit 권장.

```text
outputs/round3_dual_track_eval_prep/prompt_formatter_v3_1/prompt_v3_1_system.md
outputs/round3_dual_track_eval_prep/prompt_formatter_v3_1/prompt_v3_1_user_templates.md
outputs/round3_dual_track_eval_prep/prompt_formatter_v3_1/graph_fact_formatter_v3_1.md
outputs/round3_dual_track_eval_prep/prompt_formatter_v3_1/reasoning_type_templates_v3_1.md
outputs/round3_dual_track_eval_prep/prompt_formatter_v3_1/answer_format_spec_v3_1.md
outputs/round3_dual_track_eval_prep/prompt_formatter_v3_1/scoring_rubric_v3_1.md
outputs/round3_dual_track_eval_prep/prompt_formatter_v3_1/method_isolation_rules_v3_1.md
outputs/round3_dual_track_eval_prep/prompt_formatter_v3_1/rounding_and_tolerance_rules_v3_1.md
outputs/round3_dual_track_eval_prep/prompt_formatter_v3_1/v3_to_v3_1_change_log.md
outputs/round3_dual_track_eval_prep/prompt_formatter_v3_1/v3_1_risk_review.md
outputs/round3_dual_track_eval_prep/prompt_formatter_v3_1/v3_1_go_no_go_for_dev_rerun.md
```

권장 위치는 `experiments/round3/prompt_formatter_v3_1/` 또는 `docs/round3/prompt_formatter_v3_1/`이다.

#### C. 보고서 / 의사결정 문서

다음은 GitHub에 올려도 좋다.

```text
docs/reports/graphrag_round01_round02_integrated_report_korean_fixed.md
docs/reports/graphrag_round01_round02_integrated_report_korean_fixed.pdf
docs/reports/graphrag_eval_round02_detailed_report.md
docs/reports/round3_progress_and_github_upload_plan.md
```

Round 3 관련으로는 다음도 권장한다.

```text
outputs/round3_eval_runs/dev_dryrun_v3_1_20260528_005749/dev_dryrun_v3_1_report.md
outputs/round3_eval_runs/dev_dryrun_v3_1_20260528_005749/claim_boundary_after_dev_dryrun_v3_1.md
outputs/round3_eval_runs/dev_dryrun_v3_1_20260528_005749/go_no_go_for_test_eval_v3_1.md
outputs/round3_eval_runs/dev_dryrun_v3_1_20260528_005749/review/test_eval_readiness_decision.md
outputs/round3_eval_runs/dev_dryrun_v3_1_20260528_005749/review/recommended_next_action.md
```

단, 문서 안의 Windows 절대경로는 상대경로로 치환하는 것이 좋다.

#### D. 요약 CSV / JSON

다음은 비교적 lightweight이고 재현성에 유용하므로 commit 가능하다.

```text
method_summary_by_track.csv
method_summary_by_split.csv
case_level_scores.csv
```

다만 raw traces와 달리, summary 파일만 우선 올리는 것을 권장한다.

---

### 8.2 GitHub에 직접 올리지 말아야 하는 것

#### A. 비밀키 / 환경 설정

절대 commit 금지.

```text
.env
*.env
.env.local
openai_api_key*
opik_api_key*
neo4j_password*
credentials.json
secrets.*
```

`.env.example`만 만들어서 필요한 변수 이름만 공유한다.

예:

```text
OPENAI_API_KEY=
OPIK_API_KEY=
OPIK_WORKSPACE=
OPIK_PROJECT_NAME=
NEO4J_URI=
NEO4J_USER=
NEO4J_PASSWORD=
NEO4J_DATABASE=
```

#### B. 실행 가능한 dangerous Cypher

다음은 commit하지 않거나, 반드시 `.disabled.cypher` + 전체 주석 처리 후에만 올린다.

```text
dangerous_write_patch_preview.cypher
b2_candidate_patch_preview.disabled.cypher
b3_write_query_log.cypher
rollback_plan.cypher
```

이번 프로젝트에서는 patch path가 abandon되었으므로, write preview는 GitHub에 올리지 않는 편이 더 안전하다.

#### C. Raw traces / 큰 JSONL / 모델 출력 전체

다음은 Git repo를 크게 만들고, 원문 evidence나 모델 출력이 과도하게 들어갈 수 있으므로 기본 commit은 비추천한다.

```text
*_traces.jsonl
dev_dryrun_v3_1_traces.jsonl
opik_trace_ids.jsonl
raw_model_outputs*.jsonl
provider_raw*.jsonl
```

필요하면 Git LFS, release artifact, 또는 별도 drive/archive로 관리한다.

#### D. 대형 후보 pool / rejected cases

다음은 크기가 크고 noise가 많아 기본 repo에는 비추천한다.

```text
round3_case_candidates_longlist.csv
round3_rejected_cases.jsonl
round3_required_facts.jsonl
round3_selected_cases.jsonl
required_fact_semantic_issues.jsonl
suspicious_parser_artifacts.jsonl
```

단, 재현을 위해 필요하면 `data/README.md`에 생성 스크립트와 seed/config만 남기고, 원본 대형 산출물은 release artifact로 분리한다.

---

## 9. 권장 GitHub 디렉터리 구조

현재 산출물이 `outputs/` 아래에 흩어져 있으므로, GitHub에는 다음처럼 정리해서 올리는 것을 추천한다.

```text
.
├── README.md
├── .gitignore
├── .env.example
├── scripts/
│   ├── round3_case_factory.py
│   ├── round3_preflight_validation.py
│   ├── round3_repair_eval_ready.py
│   ├── round3_dev_dryrun_v3_1.py
│   └── round3_review_dev_dryrun_v3_1.py
├── seocho/
│   └── eval/
│       └── round3/
├── tests/
│   └── test_round3_*.py
├── docs/
│   ├── reports/
│   │   ├── graphrag_round01_round02_integrated_report_korean_fixed.md
│   │   ├── graphrag_round01_round02_integrated_report_korean_fixed.pdf
│   │   └── round3_progress_and_github_upload_plan.md
│   └── round3/
│       ├── claim_boundaries/
│       ├── decisions/
│       └── prompt_formatter_v3_1/
├── experiments/
│   └── round3/
│       ├── summaries/
│       ├── eval_plans/
│       └── review_reports/
└── data/
    └── README.md
```

---

## 10. 권장 `.gitignore`

아래를 `.gitignore`에 추가하는 것을 추천한다.

```gitignore
# Secrets
.env
.env.*
!.env.example
*api_key*
*password*
credentials.json
secrets.*

# Python
__pycache__/
*.pyc
.venv/
venv/
.ipynb_checkpoints/

# Raw / large generated outputs
outputs/**/dev_dryrun*_traces.jsonl
outputs/**/opik_trace_ids.jsonl
outputs/**/*raw*.jsonl
outputs/**/*provider*.jsonl
outputs/**/*snapshot*.jsonl
outputs/**/round3_case_candidates_longlist.csv
outputs/**/round3_rejected_cases.jsonl
outputs/**/required_fact_semantic_issues.jsonl
outputs/**/suspicious_parser_artifacts.jsonl

# Dangerous write files
outputs/**/*dangerous*.cypher
outputs/**/*write_patch*.cypher
outputs/**/*rollback*.cypher

# Local caches / logs
*.log
*.tmp
.DS_Store
Thumbs.db
```

---

## 11. 권장 commit 순서

### Commit 1: 코드와 테스트

```text
feat(round3): add case factory, repair, dry-run and review scripts
```

포함:

- scripts
- seocho/eval/round3
- tests

### Commit 2: prompt / formatter / scoring spec

```text
feat(round3): add v3.1 prompt formatter and scoring contract
```

포함:

- prompt_formatter_v3_1 files
- method isolation rules
- scoring rubric

### Commit 3: 보고서와 claim boundary

```text
docs(round3): add progress report and claim boundaries
```

포함:

- Round 1–2 integrated report
- Round 3 progress report
- claim boundary docs

### Commit 4: lightweight summaries

```text
chore(round3): add sanitized evaluation summaries
```

포함:

- method summary CSV
- case-level summary CSV
- go/no-go decision docs

Raw traces는 commit하지 말고 필요 시 release artifact로 분리한다.

---

## 12. 지금 GitHub README에 반드시 적어야 할 현재 상태

README나 docs 상단에는 다음 상태를 명확히 써야 한다.

```text
Current status:
- Round 2 selected-7 evaluation completed.
- Round 3 full evaluation is NOT completed.
- Round 3 test split remains locked.
- Round 3 v3.1 dev/baseline dry-run completed locally.
- Opik logging for Round 3 is not configured yet.
- No Neo4j write or KG patch was applied in Round 3 backlog remediation.
- Next step: no-model root-cause audit of v3.1 dev traces.
```

이 문장을 넣어야 검토자가 “Round 3 full eval이 끝났다”고 오해하지 않는다.

---

## 13. 즉시 할 일 체크리스트

### 지금 바로 할 일

- [ ] v3.1 dev traces 대상 no-model root-cause audit 실행
- [ ] scorer / answer parser / context assembly / prompt issue 분리
- [ ] Opik config 복구 여부 확인
- [ ] GitHub 업로드 전 `.env`, raw traces, dangerous Cypher 제외
- [ ] 현재 진행 보고서 commit

### 아직 하지 말아야 할 일

- [ ] Track B test eval 실행 금지
- [ ] Track A test eval 실행 금지
- [ ] full eval 실행 금지
- [ ] Neo4j write 금지
- [ ] KG patch 금지
- [ ] `shadow overlay` 결과를 `live Neo4j KG` 결과로 주장 금지

---

## 14. 최종 판단

현재 프로젝트는 다음 상태다.

```text
Round 2:
- selected 7 기준으로 의미 있는 평가 결과 확보.
- hybrid_vector_graph가 가장 강한 baseline 결과를 보임.

Round 3:
- case 확장, preflight, repair, dual-track 설계, v3/v3.1 dev dry-run까지 완료.
- live KG patch path는 안전하지 않아 종료.
- Track A는 live KG diagnostic only.
- Track B는 shadow overlay scoped evaluation 준비 상태.
- v3.1 dev 결과는 test-ready가 아니며, root-cause audit이 필요.
```

따라서 지금 가장 안전한 전략은 다음이다.

```text
1. Round 2 결과는 presentation-ready baseline으로 보존한다.
2. Round 3는 full eval 완료처럼 말하지 않는다.
3. v3.1 root-cause audit으로 scoring/context/prompt 원인을 분리한다.
4. GitHub에는 코드, prompt spec, 보고서, sanitized summaries만 올린다.
5. secrets, raw traces, dangerous write files, 대형 noisy outputs는 올리지 않는다.
```
