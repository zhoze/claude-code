#!/usr/bin/env python3
"""
screen_shareholder_yield.py — total shareholder yield
=====================================================

Boudoukh, Michaely, Richardson & Roberts (2007, JF) "On the Importance of
Measuring Payout Yield": total payout (dividends + net repurchases) predicts
returns better than dividend yield alone. The practitioner extension adds net
debt paydown ("shareholder yield", Priest/Faber).

    shareholder yield = (dividends + net buybacks + net debt paydown) / market cap

All cash-flow inputs keep the FMP sign convention (outflows negative,
issuance positive), so with raw values:

    payout = −dividends_paid − (stock_repurchased + stock_issued) − debt_flow

Screen: rank shareholder yield descending.

Requires inputs/extended_input.csv (dividends_paid_y1, stock_repurchased_t,
common_stock_issued_t, debt_flow_t) — prints "awaiting data refresh" until
build_screen_inputs.py runs with FMP_KEY.

Educational tool — not investment advice.
"""

from __future__ import annotations

import pandas as pd

from screen_lib import (finish, normalize_columns, report_dropped,
                        require_columns, standard_main, to_numeric)

NAME = "Shareholder Yield"
CITATION = "Boudoukh, Michaely, Richardson & Roberts (2007), Journal of Finance 62(2)"


def screen_shareholder_yield(df: pd.DataFrame, top: int | None = None) -> pd.DataFrame:
    df = normalize_columns(df)
    cols = ["market_cap", "dividends_paid_y1", "stock_repurchased_t",
            "common_stock_issued_t", "debt_flow_t"]
    require_columns(df, ["ticker"] + cols, NAME)
    df = to_numeric(df, cols)

    before = len(df)
    df = df[(df["market_cap"] > 0) & df["dividends_paid_y1"].notna()
            & df["stock_repurchased_t"].notna()].copy()
    report_dropped(before, len(df), NAME, "missing payout cash-flow data")

    payout = (-df["dividends_paid_y1"]
              - (df["stock_repurchased_t"] + df["common_stock_issued_t"].fillna(0))
              - df["debt_flow_t"].fillna(0))
    df["shareholder_yield"] = payout / df["market_cap"]
    df["dividend_yield"] = -df["dividends_paid_y1"] / df["market_cap"]

    return finish(df, ["ticker", "company", "sector", "industry", "market_cap",
                       "shareholder_yield", "dividend_yield"],
                  sort_by="shareholder_yield", ascending=False, top=top)


if __name__ == "__main__":
    raise SystemExit(standard_main(screen_shareholder_yield, NAME))
