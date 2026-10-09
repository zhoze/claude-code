#!/usr/bin/env python3
"""
screen_operating_profitability.py — Fama-French operating profitability (RMW)
=============================================================================

Fama & French (2015, JFE) "A Five-Factor Asset Pricing Model" — the RMW
(robust-minus-weak) profitability factor:

    OP = (revenue − COGS − SG&A − interest expense) / book equity
       = (gross profit − SG&A − interest expense) / book equity

Book equity is recovered exactly from the committed data as
market_cap / price_to_book (price_to_book in the inputs is market_cap/equity).

Screen: rank OP descending among firms with positive book equity.

Requires inputs/extended_input.csv (sga_t, interest_expense_t, revenue_y1) —
prints "awaiting data refresh" until build_screen_inputs.py runs with FMP_KEY.

Educational tool — not investment advice.
"""

from __future__ import annotations

import pandas as pd

from screen_lib import (finish, normalize_columns, report_dropped,
                        require_columns, standard_main, to_numeric)

NAME = "Operating Profitability (Fama-French RMW)"
CITATION = "Fama & French (2015), Journal of Financial Economics 116(1)"


def screen_operating_profitability(df: pd.DataFrame, top: int | None = None) -> pd.DataFrame:
    df = normalize_columns(df)
    cols = ["market_cap", "price_to_book", "gross_margin_t", "revenue_y1",
            "sga_t", "interest_expense_t"]
    require_columns(df, ["ticker"] + cols, NAME)
    df = to_numeric(df, cols)

    before = len(df)
    df = df[(df["price_to_book"] > 0) & (df["market_cap"] > 0)
            & df["gross_margin_t"].notna() & (df["revenue_y1"] > 0)
            & df["sga_t"].notna() & df["interest_expense_t"].notna()].copy()
    report_dropped(before, len(df), NAME, "missing SG&A/interest or non-positive book value")

    book_equity = df["market_cap"] / df["price_to_book"]
    gross_profit = df["gross_margin_t"] * df["revenue_y1"]
    df["operating_profitability"] = (gross_profit - df["sga_t"] - df["interest_expense_t"]) / book_equity

    return finish(df, ["ticker", "company", "sector", "industry", "market_cap",
                       "operating_profitability", "gross_margin_t", "sga_t", "interest_expense_t"],
                  sort_by="operating_profitability", ascending=False, top=top)


if __name__ == "__main__":
    raise SystemExit(standard_main(screen_operating_profitability, NAME))
