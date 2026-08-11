#!/usr/bin/env python3
"""
screen_accruals.py — Sloan low-accruals earnings quality
========================================================

Sloan (1996, The Accounting Review): the cash component of earnings is more
persistent than the accrual component; firms with the lowest accruals earn
higher subsequent returns. Cash-flow-statement definition:

    accruals / assets = (net income - operating cash flow) / average total assets

Screen: rank ascending (most negative accruals = highest earnings quality),
requiring positive operating cash flow.

Required columns: net_income_t, operating_cash_flow_t, total_assets_t,
total_assets_t_minus_1 (all committed — runs without extended_input.csv).

Educational tool — not investment advice.
"""

from __future__ import annotations

import pandas as pd

from screen_lib import (finish, normalize_columns, report_dropped,
                        require_columns, standard_main, to_numeric)

NAME = "Low Accruals (Sloan)"
CITATION = "Sloan (1996), The Accounting Review 71(3)"


def screen_accruals(df: pd.DataFrame, top: int | None = None) -> pd.DataFrame:
    df = normalize_columns(df)
    cols = ["net_income_t", "operating_cash_flow_t", "total_assets_t", "total_assets_t_minus_1"]
    require_columns(df, ["ticker"] + cols, NAME)
    df = to_numeric(df, cols)

    before = len(df)
    df = df[(df["operating_cash_flow_t"] > 0) & (df["total_assets_t"] > 0)
            & (df["total_assets_t_minus_1"] > 0) & df["net_income_t"].notna()].copy()
    report_dropped(before, len(df), NAME, "missing data or non-positive OCF/assets")

    avg_assets = (df["total_assets_t"] + df["total_assets_t_minus_1"]) / 2
    df["accruals_to_assets"] = (df["net_income_t"] - df["operating_cash_flow_t"]) / avg_assets

    return finish(df, ["ticker", "company", "sector", "industry", "market_cap",
                       "accruals_to_assets", "net_income_t", "operating_cash_flow_t"],
                  sort_by="accruals_to_assets", ascending=True, top=top)


if __name__ == "__main__":
    raise SystemExit(standard_main(screen_accruals, NAME))
