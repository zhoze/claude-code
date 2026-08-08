#!/usr/bin/env python3
"""
russell1000_screener.py — full, self-contained Russell 1000 value screener
==========================================================================

One file that does the whole pipeline end to end:

  1. DATA  — pull multi-year fundamentals from Financial Modeling Prep (/stable)
             for the largest ~1000 actively-traded US common stocks (a Russell
             1000 proxy), with valuation anchored to the LIVE quote market cap.
  2. SCREENS — run three classic value/quality screens over that universe:
        * Buffett-style Quality Compounder
        * Magic Formula (Joel Greenblatt)
        * Piotroski F-Score (on low price-to-book names)
  3. OUTPUT — print the top-N of each screen plus the cross-screen overlap,
              and optionally save the input/result CSVs.

This is a consolidation of the modules in this directory
(build_screen_inputs.py + screen_buffett_quality.py + screen_magic_formula.py +
screen_piotroski_f_score.py + run_screens.py) into a single script.

Usage
-----
    # Full live run against the ~1000-name universe (needs an FMP key):
    FMP_KEY=your_key  python3 russell1000_screener.py

    # Smoke test on the first 30 discovered symbols:
    FMP_KEY=your_key  python3 russell1000_screener.py --limit 30

    # Explicit universe:
    FMP_KEY=your_key  python3 russell1000_screener.py --universe AAPL,MSFT,KO

    # Offline: re-run the screens on a previously-saved full records CSV
    # (every column produced by build_record), no API key required:
    python3 russell1000_screener.py --input records.csv

    # Save artifacts:
    FMP_KEY=your_key  python3 russell1000_screener.py \
        --save-inputs inputs/ --save-results results/ --top 10

Requirements
------------
    Python 3.9+ and pandas (the screens use pandas). The data pull itself is
    stdlib only (urllib + threads).

The API key is read from FMP_KEY and is NEVER written to disk or committed.
Educational tool — not investment advice.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import re
import sys
import time
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Iterable, Optional

import pandas as pd


BASE = "https://financialmodelingprep.com/stable"

# Names that trade under their own ticker but are NOT common equity — baby bonds,
# notes, debentures, depositary/preferred shares (e.g. CMSD = "CMS Energy ...
# 5.875% Junior Subordinated Notes due 2079"). These slip past the equity screener.
NON_EQUITY_RE = re.compile(
    r"\d%|Notes\s+due|Subordinated|Debentures?|Depositary|\bPfd\b"
    r"|Preferred\s+(?:Stock|Shares|Series)|Cumulative\s+Preferred",
    re.I,
)


# =============================================================================
# PART 1 — DATA: pull fundamentals from Financial Modeling Prep
# =============================================================================

def make_get(key):
    def get(path):
        url = f"{BASE}/{path}{'&' if '?' in path else '?'}apikey={key}"
        for attempt in range(4):
            try:
                with urllib.request.urlopen(url, timeout=30) as r:
                    d = json.loads(r.read().decode())
                if isinstance(d, dict) and ("Error Message" in d or "Restricted" in str(d)[:40]):
                    return None
                return d
            except Exception:
                time.sleep(1.5 * (attempt + 1))
        return None
    return get


def g(row, *keys, default=None):
    """First present, non-None value among keys in a dict."""
    if not isinstance(row, dict):
        return default
    for k in keys:
        v = row.get(k)
        if v is not None:
            return v
    return default


def mean(xs):
    xs = [x for x in xs if x is not None]
    return sum(xs) / len(xs) if xs else None


def cagr(series):
    """CAGR from a newest-first series (series[0]=latest). None if not computable."""
    vals = [x for x in series if x is not None]
    if len(vals) < 2:
        return None
    latest, oldest = vals[0], vals[-1]
    n = len(vals) - 1
    if oldest is None or oldest <= 0 or latest <= 0:
        return None
    return (latest / oldest) ** (1.0 / n) - 1


def discover_universe(get, target=1000):
    """Largest ~`target` US common stocks by market cap (Russell 1000 proxy)."""
    seen = {}
    for exch in ("NASDAQ", "NYSE"):
        q = (f"company-screener?exchange={exch}&country=US&isEtf=false&isFund=false"
             f"&isActivelyTrading=true&marketCapMoreThan=1500000000&limit=3000")
        for row in (get(q) or []):
            sym = row.get("symbol")
            mc = row.get("marketCap") or 0
            if sym and "." not in sym and "-" not in sym:
                seen[sym] = max(mc, seen.get(sym, 0))
    ranked = sorted(seen, key=lambda s: seen[s], reverse=True)
    return ranked[:target]


def pull_symbol(get, sym):
    km = get(f"key-metrics?symbol={sym}&period=annual&limit=5")
    rat = get(f"ratios?symbol={sym}&period=annual&limit=5")
    inc = get(f"income-statement?symbol={sym}&period=annual&limit=5")
    bal = get(f"balance-sheet-statement?symbol={sym}&period=annual&limit=2")
    cf = get(f"cash-flow-statement?symbol={sym}&period=annual&limit=5")
    prof = get(f"profile?symbol={sym}")
    quote = get(f"quote?symbol={sym}")
    if not (km and rat and inc and bal):
        return None
    prof0 = prof[0] if isinstance(prof, list) and prof else {}
    quote0 = quote[0] if isinstance(quote, list) and quote else {}
    return build_record(sym, km, rat, inc, bal, cf or [], prof0, quote0)


def build_record(sym, km, rat, inc, bal, cf, prof, quote=None):
    """Turn raw FMP rows (newest-first) into a flat record for all three screens."""
    k0, r0, i0, b0 = km[0], rat[0], inc[0], bal[0]
    i1 = inc[1] if len(inc) > 1 else {}
    b1 = bal[1] if len(bal) > 1 else {}
    cf0 = cf[0] if cf else {}

    revenues = [g(x, "revenue") for x in inc]
    epss = [g(x, "epsDiluted", "eps") for x in inc]

    def fcf_of(km_row, cf_row):
        """FCF in dollars: cash-flow statement if present, else derived from key-metrics."""
        fc = g(cf_row, "freeCashFlow")
        if fc is not None:
            return fc
        ev_y, mult = g(km_row, "enterpriseValue"), g(km_row, "evToFreeCashFlow")
        if g(km_row, "freeCashFlowToFirm") is not None:
            return g(km_row, "freeCashFlowToFirm")
        return ev_y / mult if (ev_y and mult) else None

    fcf_margins = []
    for idx, ii in enumerate(inc):
        rev = g(ii, "revenue")
        fc = fcf_of(km[idx] if idx < len(km) else {}, cf[idx] if idx < len(cf) else {})
        if rev and fc is not None and rev != 0:
            fcf_margins.append(fc / rev)

    # interest coverage: prefer ratio; else EBIT / interest expense; else high if no interest
    ebit = g(i0, "operatingIncome", "ebit")
    int_exp = g(i0, "interestExpense")
    icov = g(r0, "interestCoverageRatio")
    if not icov:
        if int_exp and int_exp > 0 and ebit is not None:
            icov = ebit / int_exp
        elif ebit and ebit > 0:
            icov = 50.0  # negligible interest expense -> effectively very high coverage

    shares_t = g(i0, "weightedAverageShsOut", "weightedAverageShsOutDil")
    shares_old = g(inc[-1], "weightedAverageShsOut", "weightedAverageShsOutDil")
    shares_change = None
    if shares_t and shares_old and shares_old != 0:
        shares_change = shares_t / shares_old - 1

    quote = quote or {}
    company_name = g(prof, "companyName", "name", default="")
    # Drop non-common-stock securities (baby bonds, notes, preferreds).
    if NON_EQUITY_RE.search(company_name or ""):
        return None

    # Live valuation anchor: trust the current quote's market cap, not a possibly
    # stale annual key-metrics snapshot (which made e.g. KLA look ~10x too cheap).
    mktcap = g(quote, "marketCap")
    if not mktcap or mktcap <= 0:
        mktcap = g(k0, "marketCap") or g(prof, "marketCap", "mktCap")
    if not mktcap or mktcap <= 0:
        return None  # no usable market cap -> can't value it, drop
    price = g(quote, "price") or g(prof, "price")

    # Enterprise value from the live market cap + latest net debt.
    total_debt = g(b0, "totalDebt")
    if total_debt is None:
        total_debt = (g(b0, "longTermDebt") or 0) + (g(b0, "shortTermDebt") or 0)
    cash = g(b0, "cashAndShortTermInvestments", "cashAndCashEquivalents") or 0
    ev = mktcap + (total_debt or 0) - cash

    # Recompute the price-based ratios off the live anchor so a stale snapshot
    # can't manufacture fake "cheapness".
    latest_fcf = fcf_of(k0, cf0)
    fcf_yield = (latest_fcf / mktcap) if (latest_fcf is not None and mktcap) else None
    pe = g(quote, "pe")
    if pe is None or pe <= 0:
        # quote PE is missing on some plans -> derive from live mcap / latest net income
        ni = g(i0, "netIncome")
        eps_ttm = g(quote, "eps")
        if ni and ni > 0:
            pe = mktcap / ni
        elif eps_ttm and eps_ttm > 0 and price:
            pe = price / eps_ttm
    equity = g(b0, "totalStockholdersEquity", "totalEquity")
    price_to_book = (mktcap / equity) if (equity and equity > 0) else g(r0, "priceToBookRatio")

    nwc = None
    if g(b0, "totalCurrentAssets") is not None and g(b0, "totalCurrentLiabilities") is not None:
        nwc = g(b0, "totalCurrentAssets") - g(b0, "totalCurrentLiabilities")
    npe = g(b0, "propertyPlantEquipmentNet")

    def gm(row):
        rev, gp = g(row, "revenue"), g(row, "grossProfit")
        return gp / rev if rev else None

    def at(inc_row, bal_row):
        rev, ta = g(inc_row, "revenue"), g(bal_row, "totalAssets")
        return rev / ta if ta else None

    return {
        "ticker": sym,
        "company": g(prof, "companyName", "name", default=""),
        "sector": g(prof, "sector", default=""),
        "industry": g(prof, "industry", default=""),
        "market_cap": mktcap,
        "price": price,
        # --- Buffett quality (5y averages + latest balance/valuation) ---
        "roic_5y_avg": mean([g(x, "returnOnInvestedCapital") for x in km]),
        "roe_5y_avg": mean([g(x, "returnOnEquity") for x in km]),
        "gross_margin_5y_avg": mean([g(x, "grossProfitMargin") for x in rat]),
        "operating_margin_5y_avg": mean([g(x, "operatingProfitMargin") for x in rat]),
        "fcf_margin_5y_avg": mean(fcf_margins),
        "revenue_cagr_5y": cagr(revenues),
        "eps_cagr_5y": cagr(epss),
        "debt_to_equity": g(r0, "debtToEquityRatio"),
        "interest_coverage": icov,
        "current_ratio": g(r0, "currentRatio"),
        "fcf_yield": fcf_yield,
        "pe": pe,
        "ev_ebit": (ev / ebit) if (ev and ebit and ebit > 0) else None,
        "shares_change_5y_pct": shares_change,
        # --- Magic Formula ---
        "enterprise_value": ev,
        "ebit": ebit,
        "net_working_capital": nwc,
        "net_fixed_assets": npe,
        # --- Piotroski (t / t-1) ---
        "price_to_book": price_to_book,
        "net_income_t": g(i0, "netIncome"),
        "net_income_t_minus_1": g(i1, "netIncome"),
        "total_assets_t": g(b0, "totalAssets"),
        "total_assets_t_minus_1": g(b1, "totalAssets"),
        "operating_cash_flow_t": g(cf0, "netCashProvidedByOperatingActivities", "operatingCashFlow")
            or ((g(k0, "enterpriseValue") / g(k0, "evToOperatingCashFlow"))
                if (g(k0, "enterpriseValue") and g(k0, "evToOperatingCashFlow")) else None),
        "long_term_debt_t": g(b0, "longTermDebt"),
        "long_term_debt_t_minus_1": g(b1, "longTermDebt"),
        "current_assets_t": g(b0, "totalCurrentAssets"),
        "current_liabilities_t": g(b0, "totalCurrentLiabilities"),
        "current_assets_t_minus_1": g(b1, "totalCurrentAssets"),
        "current_liabilities_t_minus_1": g(b1, "totalCurrentLiabilities"),
        "shares_outstanding_t": shares_t,
        "shares_outstanding_t_minus_1": g(i1, "weightedAverageShsOut", "weightedAverageShsOutDil"),
        "gross_margin_t": gm(i0),
        "gross_margin_t_minus_1": gm(i1),
        "asset_turnover_t": at(i0, b0),
        "asset_turnover_t_minus_1": at(i1, b1),
    }


def pull_universe(get, syms, workers=12, progress=True):
    """Pull and build records for every symbol, concurrently."""
    records, done = [], 0
    with ThreadPoolExecutor(max_workers=workers) as ex:
        futs = {ex.submit(pull_symbol, get, s): s for s in syms}
        for fut in as_completed(futs):
            r = fut.result()
            if r:
                records.append(r)
            done += 1
            if progress and done % 100 == 0:
                print("...", done, file=sys.stderr)
    return sorted(records, key=lambda r: r["ticker"])


# Column subsets each input CSV carried in the original per-screen pipeline.
BUFFETT_COLS = ["ticker", "company", "sector", "industry", "market_cap", "roic_5y_avg",
                "roe_5y_avg", "gross_margin_5y_avg", "operating_margin_5y_avg",
                "fcf_margin_5y_avg", "revenue_cagr_5y", "eps_cagr_5y", "debt_to_equity",
                "interest_coverage", "current_ratio", "fcf_yield", "pe", "ev_ebit",
                "shares_change_5y_pct"]
MAGIC_COLS = ["ticker", "company", "sector", "industry", "market_cap",
              "enterprise_value", "ebit", "net_working_capital", "net_fixed_assets", "price"]
PIOTROSKI_COLS = ["ticker", "company", "sector", "industry", "market_cap", "price_to_book",
                  "net_income_t", "net_income_t_minus_1", "total_assets_t", "total_assets_t_minus_1",
                  "operating_cash_flow_t", "long_term_debt_t", "long_term_debt_t_minus_1",
                  "current_assets_t", "current_liabilities_t", "current_assets_t_minus_1",
                  "current_liabilities_t_minus_1", "shares_outstanding_t", "shares_outstanding_t_minus_1",
                  "gross_margin_t", "gross_margin_t_minus_1", "asset_turnover_t", "asset_turnover_t_minus_1"]


def write_records_csv(path, records):
    """Write the full record set (every column) to one CSV."""
    if not records:
        return
    cols = list(records[0].keys())
    os.makedirs(os.path.dirname(os.path.abspath(path)) or ".", exist_ok=True)
    with open(path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(cols)
        for r in records:
            w.writerow(["" if r.get(c) is None else r.get(c) for c in cols])


def write_input_csvs(directory, records):
    """Write the three per-screen input CSVs (parity with build_screen_inputs.py)."""
    os.makedirs(directory, exist_ok=True)
    for name, cols in (("buffett_quality_input.csv", BUFFETT_COLS),
                       ("magic_formula_input.csv", MAGIC_COLS),
                       ("piotroski_input.csv", PIOTROSKI_COLS)):
        with open(os.path.join(directory, name), "w", newline="") as f:
            w = csv.writer(f)
            w.writerow(cols)
            for r in records:
                w.writerow(["" if r.get(c) is None else r.get(c) for c in cols])


# =============================================================================
# PART 2 — SCREENS (pandas). Shared helpers used by all three.
# =============================================================================

def _normalize_columns(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df.columns = [c.strip().lower().replace(" ", "_") for c in df.columns]
    return df


def _require_columns(df: pd.DataFrame, required: Iterable[str]) -> None:
    missing = [c for c in required if c not in df.columns]
    if missing:
        raise ValueError(f"Missing required columns: {', '.join(missing)}")


def _to_numeric(df: pd.DataFrame, cols: Iterable[str]) -> pd.DataFrame:
    df = df.copy()
    for col in cols:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")
    return df


def _score_between(series: pd.Series, low: float, high: float, higher_is_better: bool = True) -> pd.Series:
    """Scale a metric into a 0-100 score using low/high anchors."""
    clipped = series.clip(lower=low, upper=high)
    score = 100 * (clipped - low) / max(high - low, 1e-9)
    return score if higher_is_better else 100 - score


# ----------------------------------------------------------------------------
# 2a. Buffett-style Quality Compounder
# ----------------------------------------------------------------------------

BUFFETT_REQUIRED_COLUMNS = [
    "ticker",
    "roic_5y_avg",
    "roe_5y_avg",
    "gross_margin_5y_avg",
    "operating_margin_5y_avg",
    "fcf_margin_5y_avg",
    "revenue_cagr_5y",
    "eps_cagr_5y",
    "debt_to_equity",
    "interest_coverage",
    "fcf_yield",
    "pe",
]


def calculate_buffett_quality_score(
    df: pd.DataFrame,
    min_market_cap: float = 0,
    top: Optional[int] = None,
) -> pd.DataFrame:
    """Return Buffett-style quality-compounder ranked stocks."""
    df = _normalize_columns(df)
    _require_columns(df, BUFFETT_REQUIRED_COLUMNS)

    numeric_cols = BUFFETT_REQUIRED_COLUMNS + [
        "market_cap",
        "current_ratio",
        "ev_ebit",
        "shares_change_5y_pct",
        "moat_score",
    ]
    df = _to_numeric(df, [c for c in numeric_cols if c != "ticker"])

    if min_market_cap and "market_cap" in df.columns:
        df = df[df["market_cap"] >= min_market_cap]

    df = df.copy()

    # Quality: profitability, cash generation, and business economics.
    df["score_roic"] = _score_between(df["roic_5y_avg"], 0.05, 0.25)
    df["score_roe"] = _score_between(df["roe_5y_avg"], 0.08, 0.30)
    df["score_gross_margin"] = _score_between(df["gross_margin_5y_avg"], 0.20, 0.65)
    df["score_operating_margin"] = _score_between(df["operating_margin_5y_avg"], 0.08, 0.35)
    df["score_fcf_margin"] = _score_between(df["fcf_margin_5y_avg"], 0.05, 0.25)

    # Growth: prefer steady positive growth, but cap the benefit to avoid lottery-ticket growth.
    df["score_revenue_growth"] = _score_between(df["revenue_cagr_5y"], 0.00, 0.12)
    df["score_eps_growth"] = _score_between(df["eps_cagr_5y"], 0.00, 0.15)

    # Balance sheet: lower debt, higher interest coverage.
    df["score_debt"] = _score_between(df["debt_to_equity"], 0.0, 2.0, higher_is_better=False)
    df["score_interest_coverage"] = _score_between(df["interest_coverage"], 2.0, 20.0)

    # Valuation: quality at a reasonable price.
    df["score_fcf_yield"] = _score_between(df["fcf_yield"], 0.02, 0.08)
    df["score_pe"] = _score_between(df["pe"], 10.0, 40.0, higher_is_better=False)

    if "ev_ebit" in df.columns:
        df["score_ev_ebit"] = _score_between(df["ev_ebit"], 8.0, 30.0, higher_is_better=False)
    else:
        df["score_ev_ebit"] = 50

    if "current_ratio" in df.columns:
        df["score_current_ratio"] = _score_between(df["current_ratio"], 1.0, 3.0)
    else:
        df["score_current_ratio"] = 50

    if "shares_change_5y_pct" in df.columns:
        # Negative share change = buybacks; positive = dilution.
        df["score_share_discipline"] = _score_between(
            df["shares_change_5y_pct"], -0.20, 0.20, higher_is_better=False
        )
    else:
        df["score_share_discipline"] = 50

    if "moat_score" in df.columns:
        # Use own moat score if provided on 0-100 scale.
        df["score_moat"] = df["moat_score"].clip(0, 100)
    else:
        df["score_moat"] = (
            0.35 * df["score_roic"]
            + 0.25 * df["score_gross_margin"]
            + 0.25 * df["score_operating_margin"]
            + 0.15 * df["score_fcf_margin"]
        )

    df["quality_score"] = (
        0.24 * df["score_roic"]
        + 0.16 * df["score_roe"]
        + 0.14 * df["score_gross_margin"]
        + 0.14 * df["score_operating_margin"]
        + 0.16 * df["score_fcf_margin"]
        + 0.16 * df["score_moat"]
    )
    df["growth_score"] = 0.50 * df["score_revenue_growth"] + 0.50 * df["score_eps_growth"]
    df["balance_sheet_score"] = (
        0.45 * df["score_debt"]
        + 0.40 * df["score_interest_coverage"]
        + 0.15 * df["score_current_ratio"]
    )
    df["valuation_score"] = (
        0.40 * df["score_fcf_yield"] + 0.35 * df["score_pe"] + 0.25 * df["score_ev_ebit"]
    )
    df["capital_allocation_score"] = df["score_share_discipline"]

    df["buffett_quality_total_score"] = (
        0.40 * df["quality_score"]
        + 0.20 * df["growth_score"]
        + 0.20 * df["balance_sheet_score"]
        + 0.15 * df["valuation_score"]
        + 0.05 * df["capital_allocation_score"]
    )

    # Hard quality filters before final ranking.
    result = df[
        (df["roic_5y_avg"] >= 0.10)
        & (df["roe_5y_avg"] >= 0.12)
        & (df["fcf_margin_5y_avg"] >= 0.05)
        & (df["debt_to_equity"] <= 2.0)
        & (df["interest_coverage"] >= 3.0)
        & (df["fcf_yield"] > 0)
        & (df["pe"] > 0)
    ].copy()

    preferred_cols = [
        "ticker",
        "company",
        "sector",
        "industry",
        "market_cap",
        "buffett_quality_total_score",
        "quality_score",
        "growth_score",
        "balance_sheet_score",
        "valuation_score",
        "capital_allocation_score",
        "roic_5y_avg",
        "roe_5y_avg",
        "gross_margin_5y_avg",
        "operating_margin_5y_avg",
        "fcf_margin_5y_avg",
        "revenue_cagr_5y",
        "eps_cagr_5y",
        "debt_to_equity",
        "interest_coverage",
        "current_ratio",
        "fcf_yield",
        "pe",
        "ev_ebit",
        "shares_change_5y_pct",
        "moat_score",
    ]
    cols = [c for c in preferred_cols if c in result.columns]
    result = result.sort_values(["buffett_quality_total_score", "quality_score"], ascending=False)[cols]
    return result.head(top) if top else result


# ----------------------------------------------------------------------------
# 2b. Magic Formula (Joel Greenblatt)
# ----------------------------------------------------------------------------

DEFAULT_EXCLUDED_SECTORS = {"Financials", "Financial Services", "Banks", "Utilities", "Real Estate"}


def calculate_magic_formula(
    df: pd.DataFrame,
    min_market_cap: float = 0,
    exclude_sectors: bool = True,
    top: Optional[int] = None,
) -> pd.DataFrame:
    """Return Magic Formula ranked stocks from a fundamentals DataFrame."""
    df = _normalize_columns(df)
    _require_columns(df, ["ticker", "enterprise_value", "ebit"])

    numeric_cols = [
        "enterprise_value",
        "ebit",
        "invested_capital",
        "net_working_capital",
        "net_fixed_assets",
        "market_cap",
    ]
    df = _to_numeric(df, numeric_cols)

    if "invested_capital" not in df.columns:
        _require_columns(df, ["net_working_capital", "net_fixed_assets"])
        df["invested_capital"] = df["net_working_capital"] + df["net_fixed_assets"]

    if exclude_sectors and "sector" in df.columns:
        df = df[~df["sector"].astype(str).str.strip().isin(DEFAULT_EXCLUDED_SECTORS)]

    if min_market_cap and "market_cap" in df.columns:
        df = df[df["market_cap"] >= min_market_cap]

    df = df[
        (df["enterprise_value"] > 0)
        & (df["ebit"] > 0)
        & (df["invested_capital"] > 0)
    ].copy()

    df["earnings_yield"] = df["ebit"] / df["enterprise_value"]
    df["return_on_capital"] = df["ebit"] / df["invested_capital"]

    # Higher is better for both, so descending ranks.
    df["earnings_yield_rank"] = df["earnings_yield"].rank(ascending=False, method="min")
    df["return_on_capital_rank"] = df["return_on_capital"].rank(ascending=False, method="min")
    df["magic_formula_rank_sum"] = df["earnings_yield_rank"] + df["return_on_capital_rank"]
    df["magic_formula_score"] = 100 * (
        1 - (df["magic_formula_rank_sum"] - df["magic_formula_rank_sum"].min())
        / max(1, df["magic_formula_rank_sum"].max() - df["magic_formula_rank_sum"].min())
    )

    preferred_cols = [
        "ticker",
        "company",
        "sector",
        "industry",
        "market_cap",
        "enterprise_value",
        "ebit",
        "invested_capital",
        "earnings_yield",
        "return_on_capital",
        "earnings_yield_rank",
        "return_on_capital_rank",
        "magic_formula_rank_sum",
        "magic_formula_score",
    ]
    cols = [c for c in preferred_cols if c in df.columns]
    result = df.sort_values(["magic_formula_rank_sum", "ticker"])[cols]
    return result.head(top) if top else result


# ----------------------------------------------------------------------------
# 2c. Piotroski F-Score (on low price-to-book names)
# ----------------------------------------------------------------------------

PIOTROSKI_REQUIRED_COLUMNS = [
    "ticker",
    "price_to_book",
    "net_income_t",
    "total_assets_t",
    "total_assets_t_minus_1",
    "operating_cash_flow_t",
    "long_term_debt_t",
    "long_term_debt_t_minus_1",
    "current_assets_t",
    "current_liabilities_t",
    "current_assets_t_minus_1",
    "current_liabilities_t_minus_1",
    "shares_outstanding_t",
    "shares_outstanding_t_minus_1",
    "gross_margin_t",
    "gross_margin_t_minus_1",
    "asset_turnover_t",
    "asset_turnover_t_minus_1",
]


def calculate_piotroski_f_score(df: pd.DataFrame) -> pd.DataFrame:
    """Return DataFrame with Piotroski F-Score components and total score."""
    df = _normalize_columns(df)
    _require_columns(df, PIOTROSKI_REQUIRED_COLUMNS)
    df = _to_numeric(df, [c for c in PIOTROSKI_REQUIRED_COLUMNS if c != "ticker"] + ["market_cap"])

    df = df[(df["total_assets_t"] > 0) & (df["total_assets_t_minus_1"] > 0)].copy()
    avg_assets = (df["total_assets_t"] + df["total_assets_t_minus_1"]) / 2

    df["roa_t"] = df["net_income_t"] / avg_assets
    # If prior net income is unavailable, use current total assets base where possible.
    if "net_income_t_minus_1" in df.columns:
        df["net_income_t_minus_1"] = pd.to_numeric(df["net_income_t_minus_1"], errors="coerce")
        df["roa_t_minus_1"] = df["net_income_t_minus_1"] / df["total_assets_t_minus_1"]
    else:
        df["roa_t_minus_1"] = 0

    df["ocf_to_assets"] = df["operating_cash_flow_t"] / avg_assets
    df["debt_to_assets_t"] = df["long_term_debt_t"] / df["total_assets_t"]
    df["debt_to_assets_t_minus_1"] = df["long_term_debt_t_minus_1"] / df["total_assets_t_minus_1"]
    df["current_ratio_t"] = df["current_assets_t"] / df["current_liabilities_t"].replace(0, pd.NA)
    df["current_ratio_t_minus_1"] = df["current_assets_t_minus_1"] / df[
        "current_liabilities_t_minus_1"
    ].replace(0, pd.NA)

    df["f_positive_roa"] = (df["roa_t"] > 0).astype(int)
    df["f_positive_ocf"] = (df["operating_cash_flow_t"] > 0).astype(int)
    df["f_roa_improved"] = (df["roa_t"] > df["roa_t_minus_1"]).astype(int)
    df["f_ocf_gt_net_income"] = (df["operating_cash_flow_t"] > df["net_income_t"]).astype(int)
    df["f_lower_leverage"] = (df["debt_to_assets_t"] < df["debt_to_assets_t_minus_1"]).astype(int)
    df["f_higher_current_ratio"] = (df["current_ratio_t"] > df["current_ratio_t_minus_1"]).astype(int)
    df["f_no_dilution"] = (df["shares_outstanding_t"] <= df["shares_outstanding_t_minus_1"]).astype(int)
    df["f_higher_gross_margin"] = (df["gross_margin_t"] > df["gross_margin_t_minus_1"]).astype(int)
    df["f_higher_asset_turnover"] = (df["asset_turnover_t"] > df["asset_turnover_t_minus_1"]).astype(int)

    score_cols = [c for c in df.columns if c.startswith("f_")]
    df["piotroski_f_score"] = df[score_cols].sum(axis=1)
    return df


def screen_piotroski_value(
    df: pd.DataFrame,
    max_price_to_book_quantile: float = 0.4,
    min_f_score: int = 7,
    min_market_cap: float = 0,
    top: Optional[int] = None,
) -> pd.DataFrame:
    """Filter to low P/B companies with high Piotroski F-Scores."""
    scored = calculate_piotroski_f_score(df)
    scored = scored[(scored["price_to_book"] > 0)].copy()

    if min_market_cap and "market_cap" in scored.columns:
        scored = scored[scored["market_cap"] >= min_market_cap]

    pb_cutoff = scored["price_to_book"].quantile(max_price_to_book_quantile)
    result = scored[
        (scored["price_to_book"] <= pb_cutoff)
        & (scored["piotroski_f_score"] >= min_f_score)
    ].copy()

    preferred_cols = [
        "ticker",
        "company",
        "sector",
        "industry",
        "market_cap",
        "price_to_book",
        "piotroski_f_score",
        "roa_t",
        "ocf_to_assets",
        "debt_to_assets_t",
        "current_ratio_t",
        "gross_margin_t",
        "asset_turnover_t",
        "f_positive_roa",
        "f_positive_ocf",
        "f_roa_improved",
        "f_ocf_gt_net_income",
        "f_lower_leverage",
        "f_higher_current_ratio",
        "f_no_dilution",
        "f_higher_gross_margin",
        "f_higher_asset_turnover",
    ]
    cols = [c for c in preferred_cols if c in result.columns]
    result = result.sort_values(["piotroski_f_score", "price_to_book"], ascending=[False, True])[cols]
    return result.head(top) if top else result


# =============================================================================
# PART 3 — RUNNER: build (or load), run all three screens, report
# =============================================================================

def resolve_universe(get, universe_arg, target, limit):
    if universe_arg:
        if os.path.exists(universe_arg):
            syms = [ln.strip().upper() for ln in open(universe_arg) if ln.strip()]
        else:
            syms = [s.strip().upper() for s in universe_arg.split(",") if s.strip()]
    else:
        print("discovering universe (largest US names by market cap)...", file=sys.stderr)
        syms = discover_universe(get, target=target)
    if limit:
        syms = syms[:limit]
    return syms


def run_all_screens(df: pd.DataFrame, top: int = 10):
    """Run the three screens on one fundamentals DataFrame; return a dict of results."""
    return {
        "buffett": calculate_buffett_quality_score(df, top=top),
        "magic": calculate_magic_formula(df, top=top),
        "piotroski": screen_piotroski_value(df, top=top),
    }


def _print_screen(title, frame, cols):
    print(f"\n===== {title} (top {len(frame)}) =====")
    show = [c for c in cols if c in frame.columns]
    with pd.option_context("display.width", 220, "display.max_columns", None):
        print(frame[show].to_string(index=False))


def report(results, top):
    _print_screen(
        "BUFFETT QUALITY COMPOUNDER", results["buffett"],
        ["ticker", "company", "sector", "buffett_quality_total_score",
         "roic_5y_avg", "fcf_margin_5y_avg", "revenue_cagr_5y", "pe"],
    )
    _print_screen(
        "MAGIC FORMULA", results["magic"],
        ["ticker", "company", "sector", "magic_formula_score",
         "earnings_yield", "return_on_capital"],
    )
    _print_screen(
        "PIOTROSKI F-SCORE (low P/B)", results["piotroski"],
        ["ticker", "company", "sector", "piotroski_f_score", "price_to_book", "roa_t"],
    )
    # Cross-screen overlap.
    sets = {name: set(frame["ticker"]) for name, frame in results.items()}
    counts = {}
    for name, tickers in sets.items():
        for t in tickers:
            counts.setdefault(t, []).append(name)
    overlap = {t: names for t, names in counts.items() if len(names) >= 2}
    print("\n===== CROSS-SCREEN OVERLAP (in 2+ top lists) =====")
    print(overlap if overlap else "none")


def main(argv=None):
    p = argparse.ArgumentParser(
        description="Full Russell 1000 value screener: pull fundamentals from FMP and "
                    "run the Buffett-quality, Magic Formula and Piotroski screens.")
    p.add_argument("--input", help="Offline: load a full records CSV instead of calling FMP")
    p.add_argument("--universe", help="Comma-separated tickers or a file with one ticker per line")
    p.add_argument("--limit", type=int, default=0, help="Only pull the first N symbols (smoke test)")
    p.add_argument("--target", type=int, default=1000, help="Universe size when auto-discovering")
    p.add_argument("--workers", type=int, default=12)
    p.add_argument("--top", type=int, default=10, help="How many to show/save per screen")
    p.add_argument("--save-records", help="Write the full record set (all columns) to this CSV")
    p.add_argument("--save-inputs", help="Write the three per-screen input CSVs to this directory")
    p.add_argument("--save-results", help="Write the three ranked result CSVs to this directory")
    args = p.parse_args(argv)

    # --- obtain fundamentals ---
    if args.input:
        df = pd.read_csv(args.input)
        print(f"loaded {len(df)} rows from {args.input}", file=sys.stderr)
    else:
        key = os.environ.get("FMP_KEY")
        if not key:
            sys.exit("ERROR: set the FMP_KEY environment variable (never hardcode the key), "
                     "or use --input to run offline on a saved records CSV.")
        get = make_get(key)
        syms = resolve_universe(get, args.universe, args.target, args.limit)
        print(f"universe: {len(syms)} symbols", file=sys.stderr)
        records = pull_universe(get, syms, workers=args.workers)
        print(f"built {len(records)} records with usable data", file=sys.stderr)
        if args.save_records:
            write_records_csv(args.save_records, records)
        if args.save_inputs:
            write_input_csvs(args.save_inputs, records)
        df = pd.DataFrame(records)

    if df.empty:
        sys.exit("No usable records — nothing to screen.")

    # --- run screens ---
    results = run_all_screens(df, top=args.top)

    if args.save_results:
        os.makedirs(args.save_results, exist_ok=True)
        results["buffett"].to_csv(os.path.join(args.save_results, "buffett_quality_top.csv"), index=False)
        results["magic"].to_csv(os.path.join(args.save_results, "magic_formula_top.csv"), index=False)
        results["piotroski"].to_csv(os.path.join(args.save_results, "piotroski_top.csv"), index=False)

    report(results, args.top)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
