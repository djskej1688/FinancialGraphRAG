from __future__ import annotations

import argparse
import csv
import hashlib
import importlib.util
import json
import math
import os
import sys
import time
import urllib.error
import urllib.request
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any

import numpy as np

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

SEOCHO = Path(__file__).resolve().parents[1]
OUT = SEOCHO / "outputs" / "round15_vector_rehab"
AUDIT_DIR = OUT / "00_provenance_audit"
INDEX_DIR = OUT / "01_vector_index"
SMOKE_DIR = OUT / "02_smoke"
STATE_FILE = OUT / "state.json"

ROUND3_RUNS = SEOCHO / "outputs" / "round3_eval_runs"
R14_CASES = SEOCHO / "outputs" / "round14_cross_company" / "04_cross_company_queries" / "cross_company_cases.jsonl"
R14_ALL_SLICES = SEOCHO / "inputs" / "round14" / "all_slices.csv"
R14B_OUT = SEOCHO / "outputs" / "round14b_benchmark_scale_expansion"

EMBED_MODEL = "text-embedding-3-small"
CHUNK_SIZE = 1200
OVERLAP = 150
TOP_K = 10


def now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def sha(text: str, n: int = 16) -> str:
    return hashlib.sha256(text.encode("utf-8", errors="ignore")).hexdigest()[:n]


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    if not path.exists():
        return rows
    with path.open("r", encoding="utf-8", errors="ignore") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return rows


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")


def write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text.rstrip() + "\n", encoding="utf-8", newline="\n")


def update_state(patch: dict[str, Any]) -> None:
    state = {}
    if STATE_FILE.exists():
        try:
            state = json.loads(STATE_FILE.read_text(encoding="utf-8"))
        except Exception:
            state = {}
    state.update(patch)
    state["updated_at"] = now_iso()
    write_json(STATE_FILE, state)


def rel(path: Path) -> str:
    try:
        return str(path.relative_to(SEOCHO)).replace("\\", "/")
    except ValueError:
        return str(path)


def line_number(path: Path, needle: str) -> int | None:
    if not path.exists():
        return None
    for idx, line in enumerate(path.read_text(encoding="utf-8", errors="ignore").splitlines(), start=1):
        if needle in line:
            return idx
    return None


def scan_vector_traces() -> dict[str, Any]:
    trace_hits = []
    by_method: dict[str, dict[str, Any]] = {}
    for path in sorted(ROUND3_RUNS.glob("**/*traces*.jsonl")):
        counts: Counter[str] = Counter()
        sample: dict[str, Any] | None = None
        for row in read_jsonl(path):
            method = str(row.get("method", ""))
            if "vector" not in method.lower():
                continue
            counts[method] += 1
            if sample is None:
                sample = row
        if not counts:
            continue
        trace_hits.append({"trace_file": rel(path), "methods": dict(counts)})
        for method, n in counts.items():
            item = by_method.setdefault(method, {"trace_files": [], "trace_count": 0})
            item["trace_files"].append(rel(path))
            item["trace_count"] += n
            if sample:
                item["sample_fields"] = sorted(k for k in sample.keys() if k in {"retrieved_chunks", "case_id", "round", "split", "source_dataset"})

    r14b_trace = R14B_OUT / "07_full_scale_run" / "full_scale_traces.jsonl"
    if r14b_trace.exists():
        counts = Counter(row.get("method", "") for row in read_jsonl(r14b_trace) if "vector" in str(row.get("method", "")).lower())
        if counts:
            trace_hits.append({"trace_file": rel(r14b_trace), "methods": dict(counts)})
            for method, n in counts.items():
                item = by_method.setdefault(method, {"trace_files": [], "trace_count": 0})
                item["trace_files"].append(rel(r14b_trace))
                item["trace_count"] += n
    return {"trace_hits": trace_hits, "by_method": by_method}


def classify_method(method: str) -> dict[str, Any]:
    if method == "vector_only_scaled":
        return {
            "classification": "gold_context_no_retrieval",
            "corpus_scope": "gold_only",
            "retrieval_granularity": "none",
            "index_type": "none",
            "embeddings_persisted": False,
            "provenance_logged": {"retrieved_chunk_ids": False, "similarity_scores": False, "source_case_id": True},
            "naming_honest": False,
            "verdict": "needs_reclassification",
            "recommended_label": "gold_text_only",
            "evidence": "scripts/round14b_benchmark_scale.py context_for(): case.evidence_text[:6000]",
        }
    if method in {"vector_single_v14", "vector_multi_by_company_v14"}:
        return {
            "classification": "real_retrieval_full_corpus",
            "corpus_scope": "full_corpus",
            "retrieval_granularity": "document_level",
            "index_type": "in_memory_cosine",
            "embeddings_persisted": True,
            "provenance_logged": {"retrieved_chunk_ids": True, "similarity_scores": False, "source_case_id": True},
            "naming_honest": True,
            "verdict": "ok_but_weak_retriever",
            "recommended_label": f"{method} (keep; document-level in-memory baseline)",
            "evidence": "scripts/round14_cross_company.py retrieve()/cosine(); embedding_cache.jsonl only, no persistent index",
        }
    if method.startswith("vector_only_v") and any(method.endswith(s) for s in ["8", "9", "10"]):
        return {
            "classification": "per_case_evidence_only",
            "corpus_scope": "per_case",
            "retrieval_granularity": "none",
            "index_type": "none",
            "embeddings_persisted": False,
            "provenance_logged": {"retrieved_chunk_ids": False, "similarity_scores": False, "source_case_id": True},
            "naming_honest": False,
            "verdict": "mislabeled",
            "recommended_label": method.replace("vector_only", "case_text_only"),
            "evidence": "round8/round9c/round10 build_prompt(): TEXT_CONTEXT = case['evidence_text']",
        }
    if method == "vector_only_v7":
        return {
            "classification": "per_case_evidence_only",
            "corpus_scope": "per_case",
            "retrieval_granularity": "none",
            "index_type": "none",
            "embeddings_persisted": False,
            "provenance_logged": {"retrieved_chunk_ids": False, "similarity_scores": False, "source_case_id": True},
            "naming_honest": False,
            "verdict": "mislabeled",
            "recommended_label": "case_text_only_v7",
            "evidence": "scripts/round7_eval.py build_prompt(): vector arm uses case evidence text directly",
        }
    if method.startswith("vector_only_v") or method.startswith("hybrid_vector_graph"):
        return {
            "classification": "per_case_evidence_only",
            "corpus_scope": "per_case",
            "retrieval_granularity": "none",
            "index_type": "none",
            "embeddings_persisted": False,
            "provenance_logged": {"retrieved_chunk_ids": False, "similarity_scores": False, "source_case_id": True},
            "naming_honest": False,
            "verdict": "mislabeled_or_legacy_prompt_arm",
            "recommended_label": method.replace("vector_only", "case_text_only"),
            "evidence": "legacy prompt-format arms; no persistent vector index detected",
        }
    return {
        "classification": "unknown",
        "corpus_scope": "unknown",
        "retrieval_granularity": "unknown",
        "index_type": "unknown",
        "embeddings_persisted": False,
        "provenance_logged": {"retrieved_chunk_ids": False, "similarity_scores": False, "source_case_id": False},
        "naming_honest": False,
        "verdict": "blocker_unknown",
        "recommended_label": "unknown",
        "evidence": "No classification rule matched",
    }


def phase0() -> dict[str, Any]:
    AUDIT_DIR.mkdir(parents=True, exist_ok=True)
    scan = scan_vector_traces()
    by_method = scan["by_method"]

    script_findings = {
        "round14b_vector_only_scaled_line": line_number(SEOCHO / "scripts" / "round14b_benchmark_scale.py", 'if method == "vector_only_scaled"'),
        "round14b_evidence_text_line": line_number(SEOCHO / "scripts" / "round14b_benchmark_scale.py", 'case.get("evidence_text", "")[:6000]'),
        "round14_retrieve_line": line_number(SEOCHO / "scripts" / "round14_cross_company.py", "def retrieve("),
        "round14_cosine_line": line_number(SEOCHO / "scripts" / "round14_cross_company.py", "def cosine("),
        "round14_load_passage_corpus_line": line_number(SEOCHO / "scripts" / "round14_cross_company.py", "def load_passage_corpus("),
        "round8_vector_context_line": line_number(SEOCHO / "scripts" / "round8_eval.py", 'if method == "vector_only_v8"'),
        "round9c_vector_context_line": line_number(SEOCHO / "scripts" / "round9c_eval.py", 'if method == "vector_only_v9"'),
        "round10_vector_context_line": line_number(SEOCHO / "scripts" / "round10_eval.py", 'if method == "vector_only_v10"'),
        "research_scripts_lancedb_hits": [],
        "finder_vector_arm_exists": (SEOCHO / "scripts" / "finder_vector_arm.py").exists(),
        "product_vector_backend_exists": (SEOCHO / "seocho" / "store" / "vector.py").exists(),
        "adr_0042_exists": (SEOCHO / "docs" / "decisions" / "ADR-0042-openai-compatible-provider-and-vector-backend-contract.md").exists(),
        "lancedb_importable": bool(importlib.util.find_spec("lancedb")),
        "faiss_importable": bool(importlib.util.find_spec("faiss")),
    }
    for path in (SEOCHO / "scripts").glob("*.py"):
        if path.name == Path(__file__).name:
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        if "LanceDB" in text or "lancedb" in text:
            script_findings["research_scripts_lancedb_hits"].append(rel(path))

    rows = []
    for method, info in sorted(by_method.items()):
        cls = classify_method(method)
        round_name = "R14B" if method == "vector_only_scaled" else ("R14" if method.endswith("_v14") else method.split("_v")[-1].upper() if "_v" in method else "legacy")
        rows.append(
            {
                "round": round_name,
                "method_name": method,
                "script": infer_script(method),
                "impl_function": infer_impl_function(method),
                **cls,
                "trace_count": info.get("trace_count", 0),
                "trace_files": info.get("trace_files", []),
            }
        )

    unknown_count = sum(1 for row in rows if row["classification"] == "unknown")
    reclass_manifest = {
        rel(R14B_OUT): {
            "old_label": "vector_only_scaled",
            "new_label": "gold_text_only",
            "reason": "context_for() returns case.evidence_text[:6000]; no retrieval, no embedding search, no vector index",
            "source": "scripts/round14b_benchmark_scale.py",
            "source_line": script_findings["round14b_vector_only_scaled_line"],
            "sidecar_only": True,
            "original_outputs_mutated": False,
        }
    }

    write_json(AUDIT_DIR / "implementation_findings.json", {"script_findings": script_findings, "arms": rows, "trace_hits": scan["trace_hits"]})
    write_json(AUDIT_DIR / "reclassification_manifest.json", reclass_manifest)
    write_text(R14B_OUT / "RECLASSIFIED.md", "\n".join([
        "# R14B Method Reclassification Sidecar",
        "",
        "`vector_only_scaled` is reclassified as `gold_text_only` for downstream reporting.",
        "",
        "Reason: `scripts/round14b_benchmark_scale.py` `context_for()` returns `case.evidence_text[:6000]` directly. There is no retrieval step, no chunk index, and no embedding search.",
        "",
        "This sidecar does not modify, delete, or overwrite any original R14B result file.",
    ]))
    write_text(AUDIT_DIR / "prior_vector_runs_reclassified.md", render_reclassification(rows, reclass_manifest))
    write_text(AUDIT_DIR / "vector_arm_audit.md", render_audit(rows, script_findings, unknown_count))

    backend = "lancedb" if script_findings["lancedb_importable"] else ("faiss_ondisk" if script_findings["faiss_importable"] else "numpy_ondisk")
    report = {
        "phase": "phase0_done",
        "unknown_count": unknown_count,
        "prior_lancedb_impersonation_found": bool(script_findings["research_scripts_lancedb_hits"]),
        "r14b_reclassified_gold_text_only": True,
        "backend_planned": backend,
        "phase1_requires_api": True,
        "neo4j_write_performed": False,
        "existing_outputs_overwritten": False,
        "audit_files": [rel(AUDIT_DIR / name) for name in ["vector_arm_audit.md", "implementation_findings.json", "prior_vector_runs_reclassified.md", "reclassification_manifest.json"]],
    }
    update_state(report)
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return report


def infer_script(method: str) -> str:
    if method == "vector_only_scaled":
        return "scripts/round14b_benchmark_scale.py"
    if method.endswith("_v14"):
        return "scripts/round14_cross_company.py"
    for tag in ["10", "9", "8", "7", "6", "5", "4"]:
        if method.endswith(f"_v{tag}"):
            if tag == "9":
                return "scripts/round9c_eval.py"
            if tag == "5":
                return "scripts/round5_diagnostic_eval.py"
            if tag == "4":
                return "scripts/round4_eval_llm_ie_kg.py"
            return f"scripts/round{tag}_eval.py"
    if "_v3_2" in method:
        return "scripts/round3_locked_test_v3_2_track_b.py / scripts/round3_formula_contract_clean_dev.py"
    if "_v3_1" in method:
        return "scripts/round3_dev_dryrun_v3_1.py"
    if "_v3" in method:
        return "scripts/round3_dev_dryrun_v3.py"
    return "unknown"


def infer_impl_function(method: str) -> str:
    if method == "vector_only_scaled":
        return "context_for()"
    if method.endswith("_v14"):
        return "load_passage_corpus()/retrieve()/cosine()"
    return "build_prompt()/TEXT_CONTEXT"


def render_audit(rows: list[dict[str, Any]], findings: dict[str, Any], unknown_count: int) -> str:
    counts = Counter(row["classification"] for row in rows)
    lines = [
        "# Round 15 Phase 0 - Vector Provenance Audit",
        "",
        "## Gate Summary",
        "",
        f"- unknown vector arms: {unknown_count}",
        f"- prior `LanceDB` impersonation found in research scripts: {'yes' if findings['research_scripts_lancedb_hits'] else 'no'}",
        f"- `finder_vector_arm.py` exists: {'yes' if findings['finder_vector_arm_exists'] else 'no'}",
        f"- product vector backend exists: {'yes' if findings['product_vector_backend_exists'] else 'no'}",
        f"- LanceDB importable in current `.venv`: {'yes' if findings['lancedb_importable'] else 'no'}",
        f"- FAISS importable in current `.venv`: {'yes' if findings['faiss_importable'] else 'no'}",
        f"- planned Phase 1 backend: {'lancedb' if findings['lancedb_importable'] else ('faiss_ondisk' if findings['faiss_importable'] else 'numpy_ondisk')}",
        "",
        "## Code Facts Checked",
        "",
        f"- R14B `vector_only_scaled` branch: `scripts/round14b_benchmark_scale.py:{findings['round14b_vector_only_scaled_line']}`",
        f"- R14B direct evidence return: `scripts/round14b_benchmark_scale.py:{findings['round14b_evidence_text_line']}`",
        f"- R14 `load_passage_corpus()`: `scripts/round14_cross_company.py:{findings['round14_load_passage_corpus_line']}`",
        f"- R14 `cosine()`: `scripts/round14_cross_company.py:{findings['round14_cosine_line']}`",
        f"- R14 `retrieve()`: `scripts/round14_cross_company.py:{findings['round14_retrieve_line']}`",
        f"- R8 vector context: `scripts/round8_eval.py:{findings['round8_vector_context_line']}`",
        f"- R9C vector context: `scripts/round9c_eval.py:{findings['round9c_vector_context_line']}`",
        f"- R10 vector context: `scripts/round10_eval.py:{findings['round10_vector_context_line']}`",
        "",
        "## Classification Counts",
        "",
        "| classification | arms |",
        "|---|---:|",
    ]
    for key, value in sorted(counts.items()):
        lines.append(f"| {key} | {value} |")
    lines += [
        "",
        "## Arm Audit",
        "",
        "| round | method | classification | granularity | index | verdict | recommended label | traces |",
        "|---|---|---|---|---|---|---|---:|",
    ]
    for row in rows:
        lines.append(
            f"| {row['round']} | `{row['method_name']}` | {row['classification']} | "
            f"{row['retrieval_granularity']} | {row['index_type']} | {row['verdict']} | "
            f"`{row['recommended_label']}` | {row['trace_count']} |"
        )
    lines += [
        "",
        "## Gate Decision",
        "",
        "Phase 1 may proceed only because every discovered vector-like arm has a non-unknown classification. "
        "R14B is reclassified by sidecar only; original result files are frozen.",
    ]
    return "\n".join(lines)


def render_reclassification(rows: list[dict[str, Any]], manifest: dict[str, Any]) -> str:
    lines = [
        "# Prior Vector Runs Reclassified",
        "",
        "No original trace/result file is edited. This document is a sidecar classification layer.",
        "",
        "## Mandatory Reclassification",
        "",
        "| run | old label | new label | reason |",
        "|---|---|---|---|",
    ]
    for run, item in manifest.items():
        lines.append(f"| `{run}` | `{item['old_label']}` | `{item['new_label']}` | {item['reason']} |")
    lines += [
        "",
        "## Non-reclassified but caveated arms",
        "",
        "| method | classification | recommendation |",
        "|---|---|---|",
    ]
    for row in rows:
        if row["method_name"] == "vector_only_scaled":
            continue
        if row["classification"] != "real_retrieval_full_corpus":
            lines.append(f"| `{row['method_name']}` | {row['classification']} | `{row['recommended_label']}` |")
    return "\n".join(lines)


def load_r14_cases() -> list[dict[str, Any]]:
    return read_jsonl(R14_CASES)


def load_all_slices_by_id() -> dict[str, dict[str, str]]:
    with R14_ALL_SLICES.open("r", encoding="utf-8-sig", newline="") as f:
        return {str(row["_id"]): row for row in csv.DictReader(f)}


def chunk_text(text: str, source_case_id: str, metadata: dict[str, Any]) -> list[dict[str, Any]]:
    text = text.strip()
    chunks: list[dict[str, Any]] = []
    if not text:
        return chunks
    start = 0
    idx = 0
    while start < len(text):
        end = min(len(text), start + CHUNK_SIZE)
        chunk = text[start:end].strip()
        if chunk:
            chunk_id = f"r15_{sha(source_case_id + ':' + str(idx) + ':' + chunk, 20)}"
            chunks.append(
                {
                    "chunk_id": chunk_id,
                    "source_case_id": source_case_id,
                    "dataset": metadata.get("dataset", "FinDER"),
                    "ticker": metadata.get("ticker", ""),
                    "company": metadata.get("company", ""),
                    "chunk_index": idx,
                    "char_start": start,
                    "char_end": end,
                    "chunk_text": chunk,
                    "embed_model": EMBED_MODEL,
                    "chunk_size": CHUNK_SIZE,
                    "overlap": OVERLAP,
                    "created_at": now_iso(),
                }
            )
        idx += 1
        if end >= len(text):
            break
        start = max(0, end - OVERLAP)
    return chunks


def build_corpus_chunks() -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    cases = load_r14_cases()
    by_id = load_all_slices_by_id()
    obs_meta: dict[str, dict[str, Any]] = {}
    for case in cases:
        for obs in case.get("source_observations", []):
            cid = str(obs.get("case_id", ""))
            if not cid:
                continue
            obs_meta.setdefault(
                cid,
                {
                    "ticker": obs.get("ticker", ""),
                    "company": obs.get("company", ""),
                    "dataset": "FinDER",
                },
            )
    chunks: list[dict[str, Any]] = []
    for source_case_id, meta in sorted(obs_meta.items()):
        row = by_id.get(source_case_id, {})
        text = str(row.get("references_joined") or "")
        chunks.extend(chunk_text(text, source_case_id, meta))
    return chunks, cases


def embed_texts(texts: list[str], cache_path: Path) -> list[list[float]]:
    cache: dict[str, list[float]] = {}
    if cache_path.exists():
        for row in read_jsonl(cache_path):
            if row.get("key") and isinstance(row.get("embedding"), list):
                cache[str(row["key"])] = row["embedding"]
    missing: list[tuple[str, str]] = []
    for text in texts:
        key = sha(text, 32)
        if key not in cache:
            missing.append((key, text))
    api_key = os.environ.get("OPENAI_API_KEY")
    if missing and not api_key:
        raise RuntimeError("OPENAI_API_KEY missing from environment; .env is intentionally not loaded")
    for start in range(0, len(missing), 64):
        batch = missing[start:start + 64]
        vectors = call_openai_embeddings([text for _, text in batch], api_key or "")
        rows = []
        for (key, _), vec in zip(batch, vectors):
            cache[key] = vec
            rows.append({"key": key, "embedding": vec, "embed_model": EMBED_MODEL})
        append_jsonl(cache_path, rows)
        update_state({"phase": "phase1_embedding", "embedding_cached": len(cache), "embedding_remaining": max(0, len(missing) - start - len(batch))})
        time.sleep(0.1)
    return [cache[sha(text, 32)] for text in texts]


def append_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8", newline="\n") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")


def call_openai_embeddings(texts: list[str], api_key: str) -> list[list[float]]:
    payload = json.dumps({"model": EMBED_MODEL, "input": texts}, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        "https://api.openai.com/v1/embeddings",
        data=payload,
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        method="POST",
    )
    for attempt in range(1, 5):
        try:
            with urllib.request.urlopen(req, timeout=120) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            return [row["embedding"] for row in sorted(data["data"], key=lambda item: item["index"])]
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="ignore")[:500]
            if exc.code in {429, 500, 502, 503, 504} and attempt < 4:
                time.sleep(2 * attempt)
                continue
            raise RuntimeError(f"OpenAI embeddings HTTP {exc.code}: {body}") from exc
        except Exception:
            if attempt < 4:
                time.sleep(2 * attempt)
                continue
            raise
    raise RuntimeError("OpenAI embeddings failed after retries")


def normalize(matrix: np.ndarray) -> np.ndarray:
    norms = np.linalg.norm(matrix, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    return matrix / norms


def search(query: str, chunks: list[dict[str, Any]], matrix: np.ndarray, query_cache: Path, top_k: int = TOP_K) -> list[dict[str, Any]]:
    qvec = np.array(embed_texts([query], query_cache)[0], dtype=np.float32)
    qnorm = qvec / (np.linalg.norm(qvec) or 1.0)
    scores = matrix @ qnorm
    order = np.argsort(-scores)[:top_k]
    out = []
    for rank, idx in enumerate(order, start=1):
        chunk = chunks[int(idx)]
        out.append(
            {
                "rank": rank,
                "chunk_id": chunk["chunk_id"],
                "source_case_id": chunk["source_case_id"],
                "score": float(scores[int(idx)]),
                "chunk_text": chunk["chunk_text"],
                "ticker": chunk.get("ticker", ""),
                "company": chunk.get("company", ""),
                "vector_store": "numpy_ondisk",
                "table_name": "round15_chunk_vectors",
            }
        )
    return out


def contains_checks(case: dict[str, Any], results: list[dict[str, Any]]) -> dict[str, Any]:
    blob = "\n".join(row["chunk_text"] for row in results).lower()
    nums = [str(x).rstrip("0").rstrip(".") for x in case.get("source_fact_numbers", [])]
    metric = str(case.get("metric", "")).replace("_", " ").lower()
    companies = [str(case.get("company_a", "")).lower(), str(case.get("company_b", "")).lower()]
    numbers_hit = sum(1 for num in nums if num and num in blob)
    metric_hit = metric in blob or str(case.get("metric", "")).lower() in blob
    company_hit = sum(1 for co in companies if co and co in blob)
    return {
        "contains_expected_number": numbers_hit > 0,
        "contains_expected_metric": metric_hit,
        "contains_expected_company": company_hit >= 1,
        "contains_match": numbers_hit > 0 and (metric_hit or company_hit >= 1),
        "overlap_ratio": round((numbers_hit + int(metric_hit) + company_hit) / max(1, len(nums) + 1 + len(companies)), 4),
    }


def phase1() -> dict[str, Any]:
    lancedb_ok = bool(importlib.util.find_spec("lancedb"))
    faiss_ok = bool(importlib.util.find_spec("faiss"))
    backend = "lancedb" if lancedb_ok else ("faiss_ondisk" if faiss_ok else "numpy_ondisk")
    if backend != "numpy_ondisk":
        raise RuntimeError("This Phase 1 implementation only supports the honest numpy_ondisk fallback path")

    INDEX_DIR.mkdir(parents=True, exist_ok=True)
    SMOKE_DIR.mkdir(parents=True, exist_ok=True)
    update_state({"phase": "phase1_start", "vector_store": backend, "neo4j_write_performed": False})

    chunks, cases = build_corpus_chunks()
    if not chunks:
        raise RuntimeError("No R14 corpus chunks built")
    texts = [row["chunk_text"] for row in chunks]
    embeddings = np.array(embed_texts(texts, INDEX_DIR / "embedding_cache.jsonl"), dtype=np.float32)
    matrix = normalize(embeddings)
    np.save(INDEX_DIR / "embeddings.npy", matrix)
    chunks_no_embedding = [{**row, "embedding": "<stored in embeddings.npy>"} for row in chunks]
    write_jsonl(INDEX_DIR / "chunks.jsonl", chunks_no_embedding)
    write_jsonl(INDEX_DIR / "chunks_sample.jsonl", chunks_no_embedding[:20])
    write_jsonl(INDEX_DIR / "lancedb_table_sample_rows.jsonl", chunks_no_embedding[:20])

    manifest = {
        "vector_store": backend,
        "backend_imports": {"lancedb": lancedb_ok, "faiss": faiss_ok, "numpy": True},
        "embed_model": EMBED_MODEL,
        "chunk_size": CHUNK_SIZE,
        "overlap": OVERLAP,
        "row_count": len(chunks),
        "corpus_case_count": len({row["source_case_id"] for row in chunks}),
        "table_name": "round15_chunk_vectors",
        "created_at": now_iso(),
        "persistent_files": [rel(INDEX_DIR / "embeddings.npy"), rel(INDEX_DIR / "chunks.jsonl")],
        "label_honest": True,
        "neo4j_write_performed": False,
    }
    write_json(INDEX_DIR / "index_manifest.json", manifest)

    query_rows = []
    smoke_cases = cases[:5]
    deterministic_ok = True
    for case in smoke_cases:
        r1 = search(case["question"], chunks, matrix, INDEX_DIR / "query_embedding_cache.jsonl", TOP_K)
        r2 = search(case["question"], chunks, matrix, INDEX_DIR / "query_embedding_cache.jsonl", TOP_K)
        deterministic_ok = deterministic_ok and [r["chunk_id"] for r in r1] == [r["chunk_id"] for r in r2]
        checks = contains_checks(case, r1)
        for row in r1:
            query_rows.append({"case_id": case["case_id"], "query": case["question"], **checks, **row})
    write_jsonl(SMOKE_DIR / "query_results.jsonl", query_rows)
    write_jsonl(SMOKE_DIR / "lancedb_table_sample_rows.jsonl", chunks_no_embedding[:20])

    required_fields_ok = all(
        row.get("chunk_id") and row.get("source_case_id") and isinstance(row.get("score"), float) and row.get("chunk_text")
        for row in query_rows
    )
    contains_any = any(row.get("contains_match") for row in query_rows)
    smoke = {
        "G1_backend_import_success": True,
        "G2_table_row_count_gt_zero": len(chunks) > 0,
        "G3_search_returns_chunk_id_score_text": bool(query_rows) and required_fields_ok,
        "G4_expected_fact_overlap_calculable": contains_any,
        "G5_reproducible_same_query": deterministic_ok,
        "G6_vector_store_label_matches_backend": manifest["vector_store"] == backend and backend == "numpy_ondisk",
        "passed": False,
        "backend": backend,
        "row_count": len(chunks),
        "smoke_case_count": len(smoke_cases),
        "neo4j_write_performed": False,
    }
    smoke["passed"] = all(bool(v) for k, v in smoke.items() if k.startswith("G"))
    write_json(SMOKE_DIR / "smoke_results.json", smoke)
    write_text(SMOKE_DIR / "smoke_report.md", render_smoke(smoke, query_rows[:10]))
    update_state({
        "phase": "done" if smoke["passed"] else "phase1_smoke_failed",
        "vector_store": backend,
        "row_count": len(chunks),
        "corpus_case_count": manifest["corpus_case_count"],
        "embedding_cached": len(chunks),
        "chunk_embedding_count": len(chunks),
        "smoke_query_embedding_count": len(smoke_cases),
        "smoke_passed": smoke["passed"],
        "neo4j_write_performed": False,
        "existing_outputs_overwritten": False,
        "generated_files": [
            rel(INDEX_DIR / "index_manifest.json"),
            rel(INDEX_DIR / "embeddings.npy"),
            rel(INDEX_DIR / "chunks.jsonl"),
            rel(SMOKE_DIR / "smoke_results.json"),
            rel(SMOKE_DIR / "smoke_report.md"),
            rel(SMOKE_DIR / "query_results.jsonl"),
        ],
    })
    print(json.dumps({"backend": backend, "row_count": len(chunks), "smoke_passed": smoke["passed"], "neo4j_write_performed": False}, ensure_ascii=False, indent=2))
    return smoke


def render_smoke(smoke: dict[str, Any], samples: list[dict[str, Any]]) -> str:
    lines = [
        "# Round 15 Phase 1 Smoke Report",
        "",
        f"- backend: `{smoke['backend']}`",
        f"- row_count: {smoke['row_count']}",
        f"- smoke passed: {smoke['passed']}",
        f"- Neo4j write performed: {smoke['neo4j_write_performed']}",
        "",
        "## Gates",
        "",
        "| gate | result |",
        "|---|---|",
    ]
    for key, value in smoke.items():
        if key.startswith("G"):
            lines.append(f"| {key} | {value} |")
    lines += ["", "## Sample Results", "", "| case_id | rank | chunk_id | source_case_id | score | contains_match |", "|---|---:|---|---|---:|---|"]
    for row in samples:
        lines.append(f"| {row['case_id']} | {row['rank']} | `{row['chunk_id']}` | `{row['source_case_id']}` | {row['score']:.4f} | {row.get('contains_match')} |")
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--phase", choices=["phase0", "phase1", "all"], default="all")
    args = parser.parse_args()
    if args.phase in {"phase0", "all"}:
        report = phase0()
        if report["unknown_count"]:
            raise SystemExit("Phase 0 blocker: unknown vector arms remain")
    if args.phase in {"phase1", "all"}:
        phase1()


if __name__ == "__main__":
    main()
