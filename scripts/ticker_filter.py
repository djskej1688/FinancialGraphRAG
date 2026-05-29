from __future__ import annotations

import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]

TICKER_DENYLIST = {
    "CAGR",
    "OF",
    "LOSS",
    "GAIN",
    "EPS",
    "EBITDA",
    "EBIT",
    "GAAP",
    "NON",
    "TAX",
    "NET",
    "GROSS",
    "INC",
    "LLC",
    "LTD",
    "AND",
    "THE",
    "FOR",
    "SEC",
    "ROI",
    "CEO",
    "CFO",
    "FY",
    "USD",
    "US",
    "UK",
    "EU",
    "YOY",
    "QOQ",
    "R&D",
    "CAPEX",
    "COGS",
    "FCF",
    "GPM",
    "NII",
    "NIM",
    "NPM",
    "ROA",
    "ROE",
    "SGA",
    "SRT",
    "TIE",
    "AF",
    "AMGEN",
    "ATC",
    "BROWN",
    "CINC",
    "CMN",
    "CO",
    "COSTS",
    "CSTOR",
    "DUKE",
    "EBT",
    "EPT",
    "FS",
    "FTHR",
    "GP",
    "IBT",
    "LINDE",
    "NT",
    "OM",
    "PLC",
    "RALPH",
    "SA",
    "TE",
    "TPO",
    "TR",
    "UR",
    "VISA",
    "XCEL",
    "YEARS",
    "AO",
    "APTIV",
    "BRWN",
    "DR",
    "ENDED",
    "ESSEX",
    "GROUP",
    "PART",
    "SALES",
    "TARGA",
    "TOWER",
    "TRUCK",
    "YEAR",
    "COCA",
    "CORP",
    "ITEM",
    "PARTS",
    "TRUST",
    "BUNGE",
    "COLA",
    "OTHER",
}

COMPANY_TO_TICKER: dict[str, str] = {}


def _load_sp500_allowlist() -> set[str]:
    path = ROOT / "data" / "sp500_tickers.txt"
    if not path.exists():
        return set()
    return {
        line.strip().upper()
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.strip().startswith("#")
    }


SP500_ALLOWLIST = _load_sp500_allowlist()


def normalize_ticker(ticker: str) -> str:
    return str(ticker or "").strip().upper().replace(".", "")


def is_valid_ticker(ticker: str) -> bool:
    ticker = normalize_ticker(ticker)
    if not ticker:
        return False
    if ticker in TICKER_DENYLIST:
        return False
    if not re.match(r"^[A-Z]{1,5}$", ticker):
        return False
    if SP500_ALLOWLIST and ticker not in SP500_ALLOWLIST:
        return False
    return True


def filter_ticker(ticker: str) -> str | None:
    ticker = normalize_ticker(ticker)
    if is_valid_ticker(ticker):
        return ticker
    return None


def ticker_for_company(company: str) -> str | None:
    mapped = COMPANY_TO_TICKER.get(str(company or "").strip().lower())
    return filter_ticker(mapped or "")
