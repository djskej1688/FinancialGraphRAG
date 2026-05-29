from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Any

import round8_common as r8


ROOT = r8.ROOT
TODAY = date.today().strftime("%Y%m%d")
ROUND = "round9c"
ROUND9C_BATCH = f"kg-round9c-v1-{TODAY}"
CLAIM_BOUNDARY = "clean_held_out_round9c_fixed_pipeline"
MODEL = r8.MODEL
METHODS = ["vector_only_v9", "graph_neo4j_v9", "hybrid_neo4j_v9"]
PROMPT_VERSION = "v3.3"
KGFIRST_PROMPT_VERSION = "v3.3_kgfirst"
SCORING_VERSION = "v9"

CASE_DIR = ROOT / "outputs" / "round9c_case_selection"
CONTRACT_DIR = ROOT / "outputs" / "round9c_formula_contracts"
KG_DIR = ROOT / "outputs" / "round9c_step_b_kg"
EVAL_DIR = ROOT / "outputs" / "round9c_eval"
EVAL_STATE = EVAL_DIR / "state.json"
OUT_ROOT = ROOT / "outputs" / "round3_eval_runs"

FINDER_CANDIDATES = CASE_DIR / "finder_candidates.jsonl"
FINQA_CANDIDATES = CASE_DIR / "finqa_candidates.jsonl"
SELECTION_STATE = CASE_DIR / "selection_state.json"
SCORER_CONTRACTS = CONTRACT_DIR / "round9c_scorer_contracts.jsonl"
VISIBLE_CONTRACTS = CONTRACT_DIR / "round9c_model_visible_contracts.jsonl"
GEN_STATE = CONTRACT_DIR / "generation_state.json"
GEN_TRACE = CONTRACT_DIR / "generation_trace.jsonl"
VALIDATION_REPORT = CONTRACT_DIR / "validation_report.jsonl"

R8_FINDER_CANDIDATES = ROOT / "outputs" / "round8_case_selection" / "finder_candidates.jsonl"
R8_FINQA_CANDIDATES = ROOT / "outputs" / "round8_case_selection" / "finqa_candidates.jsonl"
R8_TRACES = ROOT / "outputs" / "round3_eval_runs" / "round8_eval_20260529_103625" / "round8_traces.jsonl"
R9B_STATE = ROOT / "outputs" / "round9b_validation" / "state.json"

EXCLUDED_TICKERS = {
    "AMGN", "APD", "BXP", "GM", "LOW", "MPC", "MU", "NXPI", "VRSK", "XEL",
    "BAC", "BW", "CARR", "CMCSA", "FOXA", "HCA", "KR", "LND", "MCO", "MDLZ", "MSFT", "MTB",
    "DUK", "AES", "AIG", "AXP", "BLK", "CAGR", "CEG", "CNP", "EQR", "EVRG",
    "EXPD", "GNRC", "LKQ", "LVS", "MAA", "OF", "ONEOK", "PAYC", "PTC", "SBA",
    "VMC", "WLTW", "WMB", "ZBH", "GLW", "EMN", "RMD", "LOSS", "VICI", "OI",
    "ABMD", "ADI", "ALLE", "AMAT", "AMT", "ANET", "APTV", "AWK", "CAT", "CB",
    "CME", "DISCA", "DISH", "DRE", "DVN", "ETR", "GPN", "GS", "HIG", "HUM",
}

FORMULA_TYPES = [
    "gross_margin",
    "operating_margin",
    "net_margin",
    "diluted_eps_and_yoy_change",
    "continuing_ops_margin",
    "operating_vs_net_margin",
    "workforce_ratio",
    "tpo_segment_gross_margin",
    "net_margin_and_nonop_impact",
    "yoy_revenue_change",
    "multi_year_margin",
    "segment_comparison",
    "eps_dilution",
    "ratio_trend",
    "income_vs_ops",
    "effective_tax_rate",
    "capex_intensity",
    "debt_metrics",
    "finqa_program",
    "other",
]


def assert_round9b_ready() -> None:
    state = r8.read_json(R9B_STATE)
    if state.get("phase") != "9b_done" or not state.get("validation_passed"):
        raise RuntimeError("Round 09B state is not ready: expected phase=9b_done and validation_passed=true")


def read_json(path: Path) -> Any:
    return r8.read_json(path)


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    return r8.read_jsonl(path)


def write_json(path: Path, data: Any) -> None:
    r8.write_json(path, data)


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    r8.write_jsonl(path, rows)


def append_jsonl(path: Path, row: dict[str, Any]) -> None:
    r8.append_jsonl(path, row)


def write_text(path: Path, text: str) -> None:
    r8.write_text(path, text)


def rel(path: Path) -> str:
    return r8.rel(path)


def utc_now() -> str:
    return r8.utc_now()


def ts() -> str:
    return r8.ts()


def sha(text: str) -> str:
    return r8.sha(text)


def load_all_round9c_cases() -> list[dict[str, Any]]:
    return read_jsonl(FINDER_CANDIDATES) + read_jsonl(FINQA_CANDIDATES)


def load_contract_maps() -> tuple[dict[str, Any], dict[str, Any]]:
    scorer = {row["case_id"]: row["scorer_only_target_slot_contract"] for row in read_jsonl(SCORER_CONTRACTS)}
    visible = {row["case_id"]: row["model_visible_formula_contract"] for row in read_jsonl(VISIBLE_CONTRACTS)}
    return scorer, visible
