#!/usr/bin/env python3
"""
screen_asset_growth.py — low asset growth (conservative investment)
===================================================================

Cooper, Gulen & Schill (2008, JF) "Asset Growth and the Cross-Section of
Stock Returns": firms with the lowest one-year total-asset growth strongly
outperform aggressive asset growers — the "investment" factor later formalized
as CMA in Fama & French (2015).

    asset growth = total_assets_t / total_assets_t-1 − 1

Screen: rank ascending (lowest growth first) among currently profitable firms
(net income > 0, so shrinking-because-dying firms are excluded).

Required columns: total_assets_t, total_assets_t_minus_1, net_income_t
(committed — runs without extended_input.csv).

Educational tool — not investment advice.
"""

from __future__ import annotations

import pandas as pd

from screen_lib import (finish, normalize_columns, report_dropped,
                        require_columns, standard_main, to_numeric)

NAME = "Low Asset Growth (Cooper-Gulen-Schill)"
CITATION = "Cooper, Gulen & Schill (2008), Journal of Finance 63(4); Fama & French (2015) CMA"


def screen_asset_growth(df: pd.DataFrame, top: int | None = None) -> pd.DataFrame:
    df = normalize_columns(df)
    cols = ["total_assets_t", "total_assets_t_minus_1", "net_income_t"]
    require_columns(df, ["ticker"] + cols, NAME)
    df = to_numeric(df, cols)

    before = len(df)
    df = df[(df["total_assets_t"] > 0) & (df["total_assets_t_minus_1"] > 0)
            & (df["net_income_t"] > 0)].copy()
    report_dropped(before, len(df), NAME, "missing assets or negative net income")

    df["asset_growth_1y"] = df["total_assets_t"] / df["total_assets_t_minus_1"] - 1

    return finish(df, ["ticker", "company", "sector", "industry", "market_cap",
                       "asset_growth_1y", "net_income_t", "total_assets_t"],
                  sort_by="asset_growth_1y", ascending=True, top=top)


if __name__ == "__main__":
    raise SystemExit(standard_main(screen_asset_growth, NAME))
