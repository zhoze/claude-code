#!/usr/bin/env python3
"""
screen_altman_z.py — Altman Z-score safety + value
==================================================

Altman (1968, JF) "Financial Ratios, Discriminant Analysis and the Prediction
of Corporate Bankruptcy" — the original five-variable Z-score:

    Z = 1.2·WC/TA + 1.4·RE/TA + 3.3·EBIT/TA + 0.6·MVE/TL + 1.0·Sales/TA

with WC = working capital, RE = retained earnings, MVE = market value of
equity, TL = total liabilities. Z > 2.99 is Altman's "safe zone".

Screen: exclude financials/utilities/real estate (the model was estimated on
non-financial firms), keep the safe zone, rank survivors by EV/EBIT ascending
— the cheapest bankruptcy-remote names.

Requires inputs/extended_input.csv (retained_earnings_t, total_liabilities_t,
revenue_y1) — prints "awaiting data refresh" until build_screen_inputs.py runs
with FMP_KEY.

Educational tool — not investment advice.
"""

from __future__ import annotations

import pandas as pd

from screen_lib import (exclude_sectors, finish, normalize_columns, report_dropped,
                        require_columns, standard_main, to_numeric)

NAME = "Altman Z Safety + Value"
CITATION = "Altman (1968), Journal of Finance 23(4)"

THRESHOLDS = {"min_z": 2.99}


def screen_altman_z(df: pd.DataFrame, top: int | None = None,
                    min_z: float = THRESHOLDS["min_z"]) -> pd.DataFrame:
    df = normalize_columns(df)
    cols = ["current_assets_t", "current_liabilities_t", "total_assets_t",
            "retained_earnings_t", "ebit", "market_cap", "total_liabilities_t",
            "revenue_y1", "ev_ebit"]
    require_columns(df, ["ticker"] + cols, NAME)
    df = to_numeric(df, cols)
    df = exclude_sectors(df)

    before = len(df)
    df = df[(df["total_assets_t"] > 0) & (df["total_liabilities_t"] > 0)
            & df["retained_earnings_t"].notna() & df["ebit"].notna()
            & (df["revenue_y1"] > 0) & df["current_assets_t"].notna()
            & df["current_liabilities_t"].notna()].copy()
    report_dropped(before, len(df), NAME, "missing Z-score components (or excluded sector)")

    ta = df["total_assets_t"]
    wc = df["current_assets_t"] - df["current_liabilities_t"]
    df["altman_z"] = (1.2 * wc / ta
                      + 1.4 * df["retained_earnings_t"] / ta
                      + 3.3 * df["ebit"] / ta
                      + 0.6 * df["market_cap"] / df["total_liabilities_t"]
                      + 1.0 * df["revenue_y1"] / ta)

    before = len(df)
    df = df[(df["altman_z"] >= min_z) & (df["ev_ebit"] > 0)].copy()
    report_dropped(before, len(df), NAME, f"below safe zone Z<{min_z} or non-positive EV/EBIT")

    return finish(df, ["ticker", "company", "sector", "industry", "market_cap",
                       "altman_z", "ev_ebit", "pe"],
                  sort_by="ev_ebit", ascending=True, top=top)


if __name__ == "__main__":
    raise SystemExit(standard_main(screen_altman_z, NAME))
