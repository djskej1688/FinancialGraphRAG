# Step A: Semantic Fact Retrieval Layer — Codex Spec

**Task label:** `step_a_semantic_retrieval`
**Part of:** Round 06 evaluation (결과는 6차 report에 포함)
**Precondition:** `outputs/round6_eval/state.json` → phase=done
**Baseline:** Round 06 basic graph results (`outputs/round3_eval_runs/round6_eval_20260528_233753/`)

---

## 0. Context and Goal

Round 06 basic eval에서 graph test ac=0.50. 나머지 5개 실패 케이스 분류:
- **모델 추론 오류** (GM/MU/BXP): rfr=1.0인데 formula 오해석 → Step A로 해결 불가
- **허용오차 경계** (AMGN): 0.019pp 차이 → Step A로 미미한 개선 가능성
- **KG 메트릭명 모호성** (XEL): employees=11311 vs 23 (2개 팩트뿐이라 semantic selection 무의미)

**Step A의 실제 가치:**
1. Semantic selection 인프라 구축 (향후 대형 KG에서 활용)
2. 현재 targeted KG (case당 2-21 팩트)에서 keyword filter vs semantic selection 실증 비교
3. best-effort 케이스(CARR=21팩트, FOXA=15팩트)에서 semantic selection 효과 확인

**Expected outcome:** test ac 변화 미미 (대부분 케이스 팩트 수가 K=8 이하). 인프라 검증이 주목적.

---

## 1. Security Constraints

Round 06와 동일:
- `OPENAI_API_KEY` 환경변수에서만 (임베딩 API 호출용)
- Neo4j credentials `.env`에서 (read-only 쿼리만)
- `neo4j_write_performed = False` 항상
- locked test directory 접근 금지

---

## 2. Output Files

```
outputs/step_a_semantic_retrieval/
  fact_embeddings.jsonl          # 181개 팩트 임베딩 캐시
  embedding_model.txt            # 사용한 임베딩 모델명
  state.json                     # 전체 상태

outputs/round3_eval_runs/round6_semfact_{YYYYMMDD_HHMMSS}/
  round6_semfact_traces.jsonl    # 50 traces (25 cases × 2 new methods)
  round6_semfact_summary.md      # Step A 결과 + Round 06 basic 비교
```

---

## 3. New Methods

| Method | Context | Fact selection |
|---|---|---|
| `graph_neo4j_v6_semfact` | Semantic KG only | Cosine similarity top-K |
| `hybrid_neo4j_v6_semfact` | evidence_text + Semantic KG | Cosine similarity top-K |

**K=8** (기본값). 팩트 수가 K 이하인 케이스는 전체 반환.

---

## 4. Embedding Pre-computation

### 4.1 Embedding Text Format

각 `LLMObservation` 팩트의 임베딩 텍스트:

```python
def build_fact_embedding_text(fact: dict) -> str:
    """
    fact 딕셔너리: {obs_id, metric_canonical, year, value, unit, evidence_quote, ticker, case_id}
    """
    metric = fact.get("metric_canonical", "")
    year = fact.get("year", "")
    value = fact.get("value", "")
    unit = fact.get("unit", "")
    quote = fact.get("evidence_quote", "")
    ticker = fact.get("ticker", "")
    
    # Human-readable description for embedding
    text = f"{ticker} {metric} {year}: {value} {unit}"
    if quote:
        text += f" | {quote[:80]}"
    return text
```

예시:
- `"VRSK revenues 2023: 2681.4 USD_millions | Revenues $ 2,681.4"`
- `"XEL employees 2023: 11311.0 employees | 11,311 full-time employees"`
- `"LOW diluted_earnings_per_common_share 2023: 13.2 USD_per_share | Diluted EPS $ 13.20"`

### 4.2 Pre-computation Script

```python
import os, json
from pathlib import Path
from openai import OpenAI
from neo4j import GraphDatabase

EMBEDDING_MODEL = "text-embedding-3-small"
KG_BATCH = "kg-targeted-ie-v1-20260528"
OUT_DIR = Path("outputs/step_a_semantic_retrieval")
OUT_DIR.mkdir(parents=True, exist_ok=True)

client = OpenAI(api_key=os.environ["OPENAI_API_KEY"])
driver = GraphDatabase.driver(NEO4J_URI, auth=(NEO4J_USER, NEO4J_PASSWORD))

def fetch_all_targeted_facts(driver) -> list[dict]:
    """Fetch all 181 facts from targeted KG."""
    with driver.session(database="neo4j") as s:
        r = s.run("""
MATCH (obs:LLMObservation)-[:LLM_MENTIONS_COMPANY]->(c:LLMCompany),
      (obs)-[:LLM_OBSERVES_METRIC]->(m:LLMFinancialMetric),
      (obs)-[:LLM_OBSERVED_IN_YEAR]->(yr:LLMFiscalYear)
WHERE obs.kg_batch = $batch
RETURN obs.obs_id AS obs_id,
       obs.value AS value,
       obs.unit AS unit,
       obs.evidence_quote AS evidence_quote,
       obs.case_id AS case_id,
       obs.validation_status AS validation_status,
       m.canonical_name AS metric_canonical,
       yr.year AS year,
       c.ticker AS ticker
""", batch=KG_BATCH)
        return [dict(rec) for rec in r]

def embed_texts_batch(texts: list[str], client: OpenAI) -> list[list[float]]:
    """Embed texts in batches of 100."""
    embeddings = []
    batch_size = 100
    for i in range(0, len(texts), batch_size):
        batch = texts[i:i+batch_size]
        resp = client.embeddings.create(
            model=EMBEDDING_MODEL,
            input=batch,
        )
        embeddings.extend([e.embedding for e in resp.data])
    return embeddings

# Fetch all facts
all_facts = fetch_all_targeted_facts(driver)
print(f"Fetched {len(all_facts)} facts from targeted KG")

# Build embedding texts
texts = [build_fact_embedding_text(f) for f in all_facts]

# Embed
embeddings = embed_texts_batch(texts, client)

# Save cache
cache = []
for fact, emb in zip(all_facts, embeddings):
    cache.append({
        "obs_id": fact["obs_id"],
        "metric_canonical": fact["metric_canonical"],
        "year": fact["year"],
        "value": fact["value"],
        "unit": fact["unit"],
        "ticker": fact["ticker"],
        "case_id": fact.get("case_id", ""),
        "validation_status": fact.get("validation_status", ""),
        "embedding_text": build_fact_embedding_text(fact),
        "embedding": emb,  # list of floats
    })

with open(OUT_DIR / "fact_embeddings.jsonl", "w", encoding="utf-8") as f:
    for row in cache:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")

with open(OUT_DIR / "embedding_model.txt", "w") as f:
    f.write(EMBEDDING_MODEL)

print(f"Saved {len(cache)} fact embeddings")
```

---

## 5. Semantic Retrieval Function

```python
import numpy as np

def cosine_similarity(a: list[float], b: list[float]) -> float:
    a, b = np.array(a), np.array(b)
    return float(np.dot(a, b) / (np.linalg.norm(a) * np.linalg.norm(b) + 1e-9))

def load_fact_embeddings(path="outputs/step_a_semantic_retrieval/fact_embeddings.jsonl") -> list[dict]:
    """Load pre-computed fact embeddings."""
    with open(path, encoding="utf-8") as f:
        return [json.loads(l) for l in f]

def load_neo4j_graph_facts_semantic(ticker: str, case_id: str, years: list[int],
                                     question: str, client: OpenAI,
                                     fact_embeddings: list[dict],
                                     top_k: int = 8) -> list[dict]:
    """
    Semantic version of graph fact retrieval.
    1. Filter pre-computed embeddings by ticker + year
    2. Embed question
    3. Cosine similarity → top-K
    """
    # Step 1: Filter by ticker and year
    # case_id prefix match (e.g., "round3_test_016") allows multiple case_id suffixes
    case_prefix = '_'.join(case_id.split('_')[:3]) if case_id.count('_') >= 3 else case_id
    
    candidate_facts = [
        f for f in fact_embeddings
        if f["ticker"] == ticker
        and f["year"] in years
        # Include facts from this case or from best-effort (case_id=None or matching)
        and (f.get("case_id", "").startswith(case_prefix) or f.get("case_id", "") == "")
    ]
    
    if not candidate_facts:
        # Fallback: any fact for this ticker + year in targeted KG
        candidate_facts = [
            f for f in fact_embeddings
            if f["ticker"] == ticker and f["year"] in years
        ]
    
    # If ≤ top_k facts, return all (no selection needed)
    if len(candidate_facts) <= top_k:
        return _facts_to_dicts(candidate_facts)
    
    # Step 2: Embed question
    resp = client.embeddings.create(
        model=EMBEDDING_MODEL,
        input=[question],
    )
    q_emb = resp.data[0].embedding
    
    # Step 3: Compute similarity and select top-K
    scored = []
    for f in candidate_facts:
        sim = cosine_similarity(q_emb, f["embedding"])
        scored.append((sim, f))
    
    scored.sort(key=lambda x: x[0], reverse=True)
    top_facts = [f for _, f in scored[:top_k]]
    
    return _facts_to_dicts(top_facts)

def _facts_to_dicts(facts: list[dict]) -> list[dict]:
    """Convert embedding cache entries to eval-ready fact dicts."""
    return [
        {
            "fact_id": f["obs_id"],
            "metric_canonical": f["metric_canonical"],
            "metric_raw": f["metric_canonical"],
            "value": f["value"],
            "year": f["year"],
            "unit": f["unit"] or "",
            "company": f["ticker"],
            "ticker": f["ticker"],
            "evidence_quote_exact": f.get("evidence_quote", "") or "",
            "fact_role": "component",
            "source_fact": True,
            "derived_answer_value": False,
        }
        for f in facts
    ]
```

---

## 6. Evaluation Loop

**50 runs:** 25 cases × 2 new methods (`graph_neo4j_v6_semfact`, `hybrid_neo4j_v6_semfact`)

```python
NEW_METHODS = ["graph_neo4j_v6_semfact", "hybrid_neo4j_v6_semfact"]
```

For each case + method:

```python
def build_context_semfact(case: dict, method: str, question: str,
                           fact_embeddings: list[dict], client: OpenAI) -> dict:
    ticker = case["ticker"]
    case_id = case["case_id"]
    years = case.get("years", [])
    evidence_text = case.get("evidence_text", "")
    
    if method == "graph_neo4j_v6_semfact":
        graph_facts = load_neo4j_graph_facts_semantic(
            ticker, case_id, years, question, client, fact_embeddings, top_k=8
        )
        return {
            "context_source": "graph_neo4j_semfact",
            "graph_facts": graph_facts,
            "evidence_text": None,
            "neo4j_facts_count": len(graph_facts),
            "semantic_selection_applied": len(graph_facts) < 20,  # True if selection was needed
        }
    elif method == "hybrid_neo4j_v6_semfact":
        graph_facts = load_neo4j_graph_facts_semantic(
            ticker, case_id, years, question, client, fact_embeddings, top_k=8
        )
        return {
            "context_source": "hybrid_semfact",
            "graph_facts": graph_facts,
            "evidence_text": evidence_text,
            "neo4j_facts_count": len(graph_facts),
            "semantic_selection_applied": len(graph_facts) < 20,
        }
```

**Trace extra fields for Step A:**

```python
trace["semantic_selection_applied"] = bool  # True if K-selection was triggered
trace["semantic_top_k"] = 8  # K value used
trace["candidate_facts_before_selection"] = int  # facts before top-K filter
trace["embedding_model"] = EMBEDDING_MODEL
```

**Prompt construction:** same as Round 06 (Graph Facts Table format, formula contract injection, Prompt v3.1 System).

---

## 7. Summary Report

`outputs/round3_eval_runs/round6_semfact_{timestamp}/round6_semfact_summary.md`:

### Test split comparison: Round 06 basic vs Step A semantic

| Method | avg_ac | avg_nc | avg_rfr | avg_facts |
|---|---:|---:|---:|---:|
| graph_neo4j_v6 (R06 basic) | 0.50 | 0.8631 | 0.925 | 7.24 |
| graph_neo4j_v6_semfact (Step A) | | | | |
| hybrid_neo4j_v6 (R06 basic) | 0.40 | 0.9809 | 0.925 | 7.24 |
| hybrid_neo4j_v6_semfact (Step A) | | | | |

### Per-case test breakdown (graph methods)

| ticker | formula_type | R06_basic_ac | StepA_semfact_ac | semantic_applied | notes |
|---|---|---|---|---|---|
| XEL | workforce_ratio | 0.0 | | | 2 facts, selection n/a |
| LOW | diluted_eps_and_yoy_change | 1.0 | | | |
| AMGN | gross_margin | 0.0 | | | royalty_rev ambiguity |
| NXPI | operating_margin | 1.0 | | | |
| GM | tpo_segment_gross_margin | 0.0 | | | model reasoning issue |
| VRSK | operating_vs_net_margin | 1.0 | | | |
| MU | net_margin_and_nonop_impact | 0.0 | | | model reasoning issue |
| APD | gross_margin | 1.0 | | | |
| MPC | continuing_ops_margin | 1.0 | | | |
| BXP | operating_margin | 0.0 | | | model reasoning issue |

### Key diagnostic questions

1. Does semantic selection change ac for any test case?
2. For cases where `semantic_selection_applied=True`, what facts were excluded?
3. Does semantic selection help or hurt AMGN (royalty_revenue ambiguity)?

---

## 8. State File

`outputs/step_a_semantic_retrieval/state.json`:

```json
{
  "phase": "done",
  "embedding_model": "text-embedding-3-small",
  "facts_embedded": 181,
  "top_k": 8,
  "cases_evaluated": 25,
  "methods": ["graph_neo4j_v6_semfact", "hybrid_neo4j_v6_semfact"],
  "runs_completed": 50,
  "runs_failed": [],
  "test_ac_graph_semfact": 0.0,
  "test_ac_hybrid_semfact": 0.0,
  "semantic_selection_triggered_n_cases": 0,
  "run_dir": "outputs/round3_eval_runs/round6_semfact_{timestamp}/",
  "completed_at": "...",
  "codex_handoff_message": "Step A complete. Combine with Round 06 basic results for 6차 report."
}
```

---

## 9. Checklist

- [ ] Fetch all 181 facts from targeted KG (`kg-targeted-ie-v1-20260528`)
- [ ] Embed all 181 facts with `text-embedding-3-small`
- [ ] Save `fact_embeddings.jsonl` with obs_id, metric, year, value, embedding
- [ ] Implement `load_neo4j_graph_facts_semantic()` with cosine similarity top-K
- [ ] Run 50 evaluations (25 × 2 new methods)
- [ ] Verify `numerical_closeness` ≠ None for all 50 traces (carry Round 06 fix forward)
- [ ] Note `semantic_selection_applied` per trace
- [ ] Write `round6_semfact_summary.md` with comparison table
- [ ] Write `state.json` phase=done
- [ ] Handoff message: "Step A complete. Ready for 6차 report."

---

## 10. Notes

- **OPENAI_API_KEY**는 임베딩 API와 chat completion 모두에 사용. 환경변수에서만 읽을 것.
- **181개 팩트 임베딩 비용**: text-embedding-3-small 기준 약 0.002 USD (무시할 수준)
- **Question embedding 캐싱**: 25개 케이스의 question은 3가지 방법에서 동일하므로 캐싱 권장. 하지만 선택 사항.
- **semantic_selection_applied=False 케이스**: 팩트 수 ≤ 8인 경우 (대부분의 primary 케이스). 이 케이스들은 Round 06 basic과 동일한 결과 나옴.
- **주의**: AMGN에서 semantic selection이 royalty_revenue를 total_revenue보다 높게 랭킹하면 WORSE해질 수 있음. 명시적으로 기록할 것.
- **Round 06 basic의 3가지 method 결과는 이미 있음** — 재실행 불필요. Step A는 2개 추가 method만.
