#!/usr/bin/env python3
"""
screen_fcf_yield.py — free-cash-flow yield with a profitability guard
====================================================================

The cash-flow arm of the value factor: free cash flow / market cap. FCF yield
is one of the strongest single value metrics in the composite-value literature
(O'Shaughnessy, "What Works on Wall Street"; used as a fundamental feature in
arXiv q-fin work such as 1906.05327).

Screen: rank FCF yield descending among firms with a positive 5-year average
ROIC (so distressed melting-ice-cube cash flows don't top the list).

Required columns: fcf_yield, roic_5y_avg (committed — runs without
extended_input.csv).

Educational tool — not investment advice.
"""

from __future__ import annotations

import pandas as pd

from screen_lib import (finish, normalize_columns, report_dropped,
                        require_columns, standard_main, to_numeric)

NAME = "FCF Yield"
CITATION = "O'Shaughnessy, What Works on Wall Street (4th ed.); arXiv:1906.05327"

THRESHOLDS = {"min_roic_5y": 0.0}


def screen_fcf_yield(df: pd.DataFrame, top: int | None = None,
                     min_roic_5y: float = THRESHOLDS["min_roic_5y"]) -> pd.DataFrame:
    df = normalize_columns(df)
    require_columns(df, ["ticker", "fcf_yield", "roic_5y_avg"], NAME)
    df = to_numeric(df, ["fcf_yield", "roic_5y_avg", "market_cap"])

    before = len(df)
    df = df[df["fcf_yield"].notna() & (df["roic_5y_avg"] > min_roic_5y)].copy()
    report_dropped(before, len(df), NAME, "missing FCF yield or non-positive 5y ROIC")

    return finish(df, ["ticker", "company", "sector", "industry", "market_cap",
                       "fcf_yield", "roic_5y_avg", "pe"],
                  sort_by="fcf_yield", ascending=False, top=top)


if __name__ == "__main__":
    raise SystemExit(standard_main(screen_fcf_yield, NAME))
