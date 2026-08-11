#!/usr/bin/env python3
"""
screen_dividend_growth.py — dividend growth and safety
======================================================

The dividend-growth ("aristocrat") style, formalized by the payout component
of Asness, Frazzini & Pedersen (2019) "Quality Minus Junk": firms that pay,
grow and comfortably cover their dividend. Criteria over the 5 annual
cash-flow statements FMP provides:

    1. Dividend record    dividends paid in each of the last 5 years
    2. Dividend growth    5-year dividend CAGR > 0
    3. Sustainable payout dividends / net income < 60%
    4. Cash coverage      operating cash flow / dividends >= 2x

Survivors are ranked by 5-year dividend CAGR descending (current yield is
reported alongside).

Dividend inputs keep the FMP sign convention (cash outflows negative).

Requires inputs/extended_input.csv (dividends_paid_y1..y5) — prints
"awaiting data refresh" until build_screen_inputs.py runs with FMP_KEY.

Educational tool — not investment advice.
"""

from __future__ import annotations

import pandas as pd

from screen_lib import (finish, normalize_columns, report_dropped,
                        require_columns, standard_main, to_numeric)

NAME = "Dividend Growth & Safety"
CITATION = "Asness, Frazzini & Pedersen (2019) payout quality; practitioner dividend-growth literature"

THRESHOLDS = {"max_payout_ratio": 0.60, "min_ocf_coverage": 2.0}

DIV_COLS = [f"dividends_paid_y{i}" for i in range(1, 6)]


def screen_dividend_growth(df: pd.DataFrame, top: int | None = None,
                           max_payout_ratio: float = THRESHOLDS["max_payout_ratio"],
                           min_ocf_coverage: float = THRESHOLDS["min_ocf_coverage"]) -> pd.DataFrame:
    df = normalize_columns(df)
    base = ["market_cap", "net_income_t", "operating_cash_flow_t"]
    require_columns(df, ["ticker"] + base + DIV_COLS, NAME)
    df = to_numeric(df, base + DIV_COLS)

    paid_now = -df["dividends_paid_y1"]   # outflows are negative in FMP data
    paid_old = -df["dividends_paid_y5"]
    df["dividend_cagr_5y"] = (paid_now / paid_old) ** 0.25 - 1
    df["dividend_yield"] = paid_now / df["market_cap"]
    df["payout_ratio"] = paid_now / df["net_income_t"]
    df["ocf_coverage"] = df["operating_cash_flow_t"] / paid_now

    before = len(df)
    df = df[(df[DIV_COLS] < 0).all(axis=1)
            & (df["dividend_cagr_5y"] > 0)
            & (df["net_income_t"] > 0)
            & (df["payout_ratio"] < max_payout_ratio)
            & (df["ocf_coverage"] >= min_ocf_coverage)].copy()
    report_dropped(before, len(df), NAME, "failed dividend record/growth/payout/coverage gates")

    return finish(df, ["ticker", "company", "sector", "industry", "market_cap",
                       "dividend_cagr_5y", "dividend_yield", "payout_ratio", "ocf_coverage"],
                  sort_by="dividend_cagr_5y", ascending=False, top=top)


if __name__ == "__main__":
    raise SystemExit(standard_main(screen_dividend_growth, NAME))
