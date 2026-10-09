#!/usr/bin/env python3
"""
screen_mohanram_g.py — Mohanram G-Score for growth stocks
=========================================================

Mohanram (2005, RAST) "Separating Winners from Losers among Low Book-to-Market
Stocks using Financial Statement Analysis" — the growth-stock counterpart of
the Piotroski F-score. Universe: the lowest book-to-market (= highest P/B)
quintile. Signals, each 1 point when true, measured against the sector median
of that growth universe (sector is the documented stand-in for Mohanram's
2-digit-SIC industry, which FMP does not expose):

    G1  ROA above median
    G2  cash-flow ROA (OCF/TA) above median
    G3  OCF > net income (accrual quality)
    G4  ROA variability (5y std) below median
    G5  sales-growth variability (5y std) below median
    G6  R&D intensity (R&D/TA) above median
    G7  capex intensity (|capex|/TA) above median
    G8  advertising intensity — NOT AVAILABLE from FMP; omitted, so the score
        runs 0-7 (documented deviation, signals are never invented)

Rank by G-score descending, ROA as tie-break.

Requires inputs/extended_input.csv (rd_expense_t, capex_t, roa_y*, revenue_y*)
— prints "awaiting data refresh" until build_screen_inputs.py runs with FMP_KEY.

Educational tool — not investment advice.
"""

from __future__ import annotations

import pandas as pd

from screen_lib import (finish, normalize_columns, report_dropped,
                        require_columns, standard_main, to_numeric)

NAME = "Mohanram G-Score (growth)"
CITATION = "Mohanram (2005), Review of Accounting Studies 10(2-3)"

THRESHOLDS = {"growth_quintile": 0.80}

ROA_COLS = [f"roa_y{i}" for i in range(1, 6)]
REV_COLS = [f"revenue_y{i}" for i in range(1, 6)]


def screen_mohanram_g(df: pd.DataFrame, top: int | None = None,
                      growth_quintile: float = THRESHOLDS["growth_quintile"]) -> pd.DataFrame:
    df = normalize_columns(df)
    base = ["price_to_book", "net_income_t", "operating_cash_flow_t",
            "total_assets_t", "rd_expense_t", "capex_t", "sector"]
    require_columns(df, ["ticker"] + base + ROA_COLS + REV_COLS, NAME)
    df = to_numeric(df, [c for c in base if c != "sector"] + ROA_COLS + REV_COLS)

    before = len(df)
    df = df[(df["price_to_book"] > 0) & (df["total_assets_t"] > 0)
            & df["net_income_t"].notna() & df["operating_cash_flow_t"].notna()].copy()
    df = df[df["price_to_book"] >= df["price_to_book"].quantile(growth_quintile)].copy()
    report_dropped(before, len(df), NAME, "outside the high-P/B growth quintile or missing data")

    ta = df["total_assets_t"]
    df["roa"] = df["net_income_t"] / ta
    df["cf_roa"] = df["operating_cash_flow_t"] / ta
    df["roa_var"] = df[ROA_COLS].std(axis=1)
    growth = df[REV_COLS].apply(pd.to_numeric).pct_change(axis=1, periods=-1)
    df["salesgro_var"] = growth.std(axis=1)
    df["rd_intensity"] = df["rd_expense_t"].fillna(0) / ta
    df["capex_intensity"] = df["capex_t"].abs() / ta

    med = df.groupby("sector")[["roa", "cf_roa", "roa_var", "salesgro_var",
                                "rd_intensity", "capex_intensity"]].transform("median")

    df["mohanram_g_score"] = (
        (df["roa"] > med["roa"]).astype(int)
        + (df["cf_roa"] > med["cf_roa"]).astype(int)
        + (df["operating_cash_flow_t"] > df["net_income_t"]).astype(int)
        + (df["roa_var"] < med["roa_var"]).astype(int)
        + (df["salesgro_var"] < med["salesgro_var"]).astype(int)
        + (df["rd_intensity"] > med["rd_intensity"]).astype(int)
        + (df["capex_intensity"] > med["capex_intensity"]).astype(int))

    df = df.sort_values(["mohanram_g_score", "roa", "ticker"],
                        ascending=[False, False, True])
    return finish(df, ["ticker", "company", "sector", "industry", "market_cap",
                       "mohanram_g_score", "roa", "cf_roa", "price_to_book"],
                  sort_by="mohanram_g_score", ascending=False, top=top)


if __name__ == "__main__":
    raise SystemExit(standard_main(screen_mohanram_g, NAME))
