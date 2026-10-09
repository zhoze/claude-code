#!/usr/bin/env python3
"""
screen_residual_income.py — residual-income spread (economic profit)
====================================================================

Residual-income valuation (Ohlson 1995; Lee, Myers & Swaminathan 1999): a firm
creates value only when its return on invested capital exceeds the cost of
capital. The spread ROIC - r drives the residual-income term of intrinsic
value.

Screen: among firms with a positive spread, blend the spread percentile with
the EV/EBIT cheapness percentile — economic profit at a reasonable price.
The cost of capital is a flat assumption (THRESHOLDS["cost_of_capital"],
default 8%), applied uniformly across the cross-section.

Required columns: roic_5y_avg, ev_ebit (committed — runs without
extended_input.csv).

Educational tool — not investment advice.
"""

from __future__ import annotations

import pandas as pd

from screen_lib import (finish, normalize_columns, pct_rank, report_dropped,
                        require_columns, standard_main, to_numeric)

NAME = "Residual Income Spread"
CITATION = "Ohlson (1995), Contemporary Accounting Research; Lee, Myers & Swaminathan (1999), JF"

THRESHOLDS = {"cost_of_capital": 0.08}


def screen_residual_income(df: pd.DataFrame, top: int | None = None,
                           cost_of_capital: float = THRESHOLDS["cost_of_capital"]) -> pd.DataFrame:
    df = normalize_columns(df)
    require_columns(df, ["ticker", "roic_5y_avg", "ev_ebit"], NAME)
    df = to_numeric(df, ["roic_5y_avg", "ev_ebit"])

    df["roic_spread"] = df["roic_5y_avg"] - cost_of_capital
    before = len(df)
    df = df[(df["roic_spread"] > 0) & (df["ev_ebit"] > 0)].copy()
    report_dropped(before, len(df), NAME, "ROIC below cost of capital or non-positive EV/EBIT")

    df["ri_score"] = (pct_rank(df["roic_spread"], higher_is_better=True)
                      + pct_rank(df["ev_ebit"], higher_is_better=False)) / 2

    return finish(df, ["ticker", "company", "sector", "industry", "market_cap",
                       "roic_5y_avg", "roic_spread", "ev_ebit", "ri_score"],
                  sort_by="ri_score", ascending=False, top=top)


if __name__ == "__main__":
    raise SystemExit(standard_main(screen_residual_income, NAME))
