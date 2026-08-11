#!/usr/bin/env python3
"""
screen_composite_value.py — multi-ratio composite value
=======================================================

Composite value ranks beat any single ratio out of sample (O'Shaughnessy,
"What Works on Wall Street"; the same ratio set is used as the fundamental
feature block in arXiv q-fin stock-selection work such as 1906.05327 and
1711.04837).

    score = mean percentile of { 1/PE, 1/(EV/EBIT), 1/(P/B), FCF yield }

Each ratio is winsorized at the 1st/99th percentile; firms must have positive
earnings, EBIT and book value to be scored.

Required columns: pe, ev_ebit, price_to_book, fcf_yield (committed — runs
without extended_input.csv).

Educational tool — not investment advice.
"""

from __future__ import annotations

import pandas as pd

from screen_lib import (composite_value_score, finish, normalize_columns,
                        report_dropped, require_columns, standard_main, to_numeric)

NAME = "Composite Value"
CITATION = "O'Shaughnessy, What Works on Wall Street; arXiv:1906.05327; arXiv:1711.04837"


def screen_composite_value(df: pd.DataFrame, top: int | None = None) -> pd.DataFrame:
    df = normalize_columns(df)
    require_columns(df, ["ticker", "pe", "ev_ebit", "price_to_book", "fcf_yield"], NAME)
    df = to_numeric(df, ["pe", "ev_ebit", "price_to_book", "fcf_yield"])

    df["value_score"] = composite_value_score(df)
    before = len(df)
    df = df[df["value_score"].notna()].copy()
    report_dropped(before, len(df), NAME, "negative earnings/EBIT/book value")

    return finish(df, ["ticker", "company", "sector", "industry", "market_cap",
                       "pe", "ev_ebit", "price_to_book", "fcf_yield", "value_score"],
                  sort_by="value_score", ascending=False, top=top)


if __name__ == "__main__":
    raise SystemExit(standard_main(screen_composite_value, NAME))
