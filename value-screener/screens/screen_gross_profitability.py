#!/usr/bin/env python3
"""
screen_gross_profitability.py — Novy-Marx gross profitability + value
=====================================================================

Novy-Marx (2013, JFE) "The Other Side of Value: The Gross Profitability
Premium": gross profits / total assets is the cleanest accounting measure of
true economic profitability and predicts returns as well as book-to-market.

    GP/A = gross profit / total assets = gross_margin_t x asset_turnover_t

Ranking (as in the paper's double sort): equal blend of the GP/A percentile
and the cheapness (1 / price-to-book) percentile.

Required columns: gross_margin_t, asset_turnover_t, price_to_book (all in the
committed inputs — runs without extended_input.csv).

Educational tool — not investment advice.
"""

from __future__ import annotations

import pandas as pd

from screen_lib import (finish, normalize_columns, pct_rank, report_dropped,
                        require_columns, standard_main, to_numeric)

NAME = "Gross Profitability (Novy-Marx)"
CITATION = "Novy-Marx (2013), Journal of Financial Economics 108(1)"


def screen_gross_profitability(df: pd.DataFrame, top: int | None = None) -> pd.DataFrame:
    df = normalize_columns(df)
    require_columns(df, ["ticker", "gross_margin_t", "asset_turnover_t", "price_to_book"], NAME)
    df = to_numeric(df, ["gross_margin_t", "asset_turnover_t", "price_to_book"])

    before = len(df)
    df = df[df["gross_margin_t"].notna() & df["asset_turnover_t"].notna()
            & (df["price_to_book"] > 0)].copy()
    report_dropped(before, len(df), NAME, "missing gross margin/turnover or non-positive P/B")

    df["gp_to_assets"] = df["gross_margin_t"] * df["asset_turnover_t"]
    df["gp_score"] = (pct_rank(df["gp_to_assets"], higher_is_better=True)
                      + pct_rank(df["price_to_book"], higher_is_better=False)) / 2

    return finish(df, ["ticker", "company", "sector", "industry", "market_cap",
                       "gp_to_assets", "price_to_book", "gp_score"],
                  sort_by="gp_score", ascending=False, top=top)


if __name__ == "__main__":
    raise SystemExit(standard_main(screen_gross_profitability, NAME))
