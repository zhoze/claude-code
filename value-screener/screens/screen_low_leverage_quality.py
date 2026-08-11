#!/usr/bin/env python3
"""
screen_low_leverage_quality.py — conservative low-leverage quality
==================================================================

The safety arm of quality investing: van Vliet & Blitz (2018) "The
Conservative Formula" and the leverage/safety components of Asness, Frazzini
& Pedersen (2019) "Quality Minus Junk" show that safe, conservatively financed
profitable firms outperform.

Screen (hard filters, then rank by profitability):
    debt/equity      <= 0.5
    interest coverage >= 10x
    current ratio    >= 1.5
    rank survivors by 5-year average ROIC, descending.

Required columns: debt_to_equity, interest_coverage, current_ratio,
roic_5y_avg (committed — runs without extended_input.csv).

Educational tool — not investment advice.
"""

from __future__ import annotations

import pandas as pd

from screen_lib import (finish, normalize_columns, report_dropped,
                        require_columns, standard_main, to_numeric)

NAME = "Conservative Low-Leverage Quality"
CITATION = "van Vliet & Blitz (2018); Asness, Frazzini & Pedersen (2019), Review of Finance"

THRESHOLDS = {"max_debt_to_equity": 0.5, "min_interest_coverage": 10.0, "min_current_ratio": 1.5}


def screen_low_leverage_quality(df: pd.DataFrame, top: int | None = None,
                                max_debt_to_equity: float = THRESHOLDS["max_debt_to_equity"],
                                min_interest_coverage: float = THRESHOLDS["min_interest_coverage"],
                                min_current_ratio: float = THRESHOLDS["min_current_ratio"]) -> pd.DataFrame:
    df = normalize_columns(df)
    cols = ["debt_to_equity", "interest_coverage", "current_ratio", "roic_5y_avg"]
    require_columns(df, ["ticker"] + cols, NAME)
    df = to_numeric(df, cols)

    before = len(df)
    df = df[(df["debt_to_equity"] <= max_debt_to_equity)
            & (df["debt_to_equity"] >= 0)
            & (df["interest_coverage"] >= min_interest_coverage)
            & (df["current_ratio"] >= min_current_ratio)
            & df["roic_5y_avg"].notna()].copy()
    report_dropped(before, len(df), NAME, "failed leverage/coverage/liquidity gates")

    return finish(df, ["ticker", "company", "sector", "industry", "market_cap",
                       "roic_5y_avg", "debt_to_equity", "interest_coverage", "current_ratio"],
                  sort_by="roic_5y_avg", ascending=False, top=top)


if __name__ == "__main__":
    raise SystemExit(standard_main(screen_low_leverage_quality, NAME))
