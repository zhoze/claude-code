#!/usr/bin/env python3
"""
screen_graham_defensive.py — Graham's defensive-investor criteria
=================================================================

Benjamin Graham (1973, "The Intelligent Investor", ch. 14) — the seven
criteria for the defensive investor, applied verbatim with the data horizon
FMP provides (5 annual statements; Graham's original horizons of 10y earnings
stability and 20y dividend record are documented as data-limited to 5y):

    1. Adequate size                    market cap >= $2B (all Russell 1000 pass)
    2. Strong financial condition       current ratio >= 2 AND long-term debt <= net working capital
    3. Earnings stability               positive diluted EPS in each of the last 5 years
    4. Dividend record                  dividends paid in each of the last 5 years
    5. Earnings growth                  EPS CAGR >= 2.9%/yr (Graham: +1/3 over 10 years)
    6. Moderate P/E                     PE <= 15
    7. Moderate price-to-assets         PE x P/B <= 22.5

Survivors are ranked by PE x P/B ascending (Graham's rule-of-thumb multiple).

Requires inputs/extended_input.csv (eps_y1..y5, dividends_paid_y1..y5) —
prints "awaiting data refresh" until build_screen_inputs.py runs with FMP_KEY.

Educational tool — not investment advice.
"""

from __future__ import annotations

import pandas as pd

from screen_lib import (finish, normalize_columns, report_dropped,
                        require_columns, standard_main, to_numeric)

NAME = "Graham Defensive"
CITATION = "Graham (1973), The Intelligent Investor ch. 14; revisited in arXiv q-fin value-screening studies"

THRESHOLDS = {"min_market_cap": 2e9, "min_current_ratio": 2.0,
              "min_eps_cagr": 0.029, "max_pe": 15.0, "max_pe_pb": 22.5}

EPS_COLS = [f"eps_y{i}" for i in range(1, 6)]
DIV_COLS = [f"dividends_paid_y{i}" for i in range(1, 6)]


def screen_graham_defensive(df: pd.DataFrame, top: int | None = None,
                            t: dict = THRESHOLDS) -> pd.DataFrame:
    df = normalize_columns(df)
    base = ["market_cap", "current_ratio", "long_term_debt_t", "current_assets_t",
            "current_liabilities_t", "eps_cagr_5y", "pe", "price_to_book"]
    require_columns(df, ["ticker"] + base + EPS_COLS + DIV_COLS, NAME)
    df = to_numeric(df, base + EPS_COLS + DIV_COLS)

    nwc = df["current_assets_t"] - df["current_liabilities_t"]
    eps_stable = (df[EPS_COLS] > 0).all(axis=1) & df[EPS_COLS].notna().all(axis=1)
    # FMP sign convention: dividends paid are cash outflows (negative values).
    div_record = (df[DIV_COLS] < 0).all(axis=1)

    before = len(df)
    df = df[(df["market_cap"] >= t["min_market_cap"])
            & (df["current_ratio"] >= t["min_current_ratio"])
            & (df["long_term_debt_t"].fillna(0) <= nwc)
            & eps_stable
            & div_record
            & (df["eps_cagr_5y"] >= t["min_eps_cagr"])
            & (df["pe"] > 0) & (df["pe"] <= t["max_pe"])
            & (df["price_to_book"] > 0)
            & (df["pe"] * df["price_to_book"] <= t["max_pe_pb"])].copy()
    report_dropped(before, len(df), NAME, "failed one of Graham's seven criteria")

    df["pe_x_pb"] = df["pe"] * df["price_to_book"]

    return finish(df, ["ticker", "company", "sector", "industry", "market_cap",
                       "pe", "price_to_book", "pe_x_pb", "eps_cagr_5y", "current_ratio"],
                  sort_by="pe_x_pb", ascending=True, top=top)


if __name__ == "__main__":
    raise SystemExit(standard_main(screen_graham_defensive, NAME))
