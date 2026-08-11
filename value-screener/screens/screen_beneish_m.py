#!/usr/bin/env python3
"""
screen_beneish_m.py — Beneish M-score earnings-quality filter + value
=====================================================================

Beneish (1999, FAJ) "The Detection of Earnings Manipulation" — the
eight-variable M-score:

    M = −4.84 + 0.920·DSRI + 0.528·GMI + 0.404·AQI + 0.892·SGI + 0.115·DEPI
        − 0.172·SGAI + 4.679·TATA − 0.327·LVGI

    DSRI  days-sales-in-receivables index      GMI   gross-margin index
    AQI   asset-quality index                  SGI   sales-growth index
    DEPI  depreciation index                   SGAI  SG&A index
    TATA  total accruals to assets             LVGI  leverage index

M > −1.78 flags a likely earnings manipulator. TATA follows the common
post-SFAS-95 implementation (net income − operating cash flow)/total assets;
leverage = (long-term debt + current liabilities)/total assets, both years.

Screen: exclude financials/utilities/real estate, drop flagged names, rank
the remainder by the composite-value score descending — cheap stocks whose
reported earnings look clean.

Requires inputs/extended_input.csv (receivables, SG&A, D&A, PPE, revenue
histories) — prints "awaiting data refresh" until build_screen_inputs.py runs
with FMP_KEY.

Educational tool — not investment advice.
"""

from __future__ import annotations

import pandas as pd

from screen_lib import (composite_value_score, exclude_sectors, finish,
                        normalize_columns, report_dropped, require_columns,
                        standard_main, to_numeric)

NAME = "Beneish M-Score Filter + Value"
CITATION = "Beneish (1999), Financial Analysts Journal 55(5)"

THRESHOLDS = {"manipulator_cutoff": -1.78}


def screen_beneish_m(df: pd.DataFrame, top: int | None = None,
                     manipulator_cutoff: float = THRESHOLDS["manipulator_cutoff"]) -> pd.DataFrame:
    df = normalize_columns(df)
    cols = ["receivables_t", "receivables_t_minus_1", "revenue_y1", "revenue_y2",
            "gross_margin_t", "gross_margin_t_minus_1", "current_assets_t",
            "current_assets_t_minus_1", "ppe_net_t", "ppe_net_t_minus_1",
            "total_assets_t", "total_assets_t_minus_1", "dep_amort_t",
            "dep_amort_t_minus_1", "sga_t", "sga_t_minus_1", "long_term_debt_t",
            "long_term_debt_t_minus_1", "current_liabilities_t",
            "current_liabilities_t_minus_1", "net_income_t", "operating_cash_flow_t",
            "pe", "ev_ebit", "price_to_book", "fcf_yield"]
    require_columns(df, ["ticker"] + cols, NAME)
    df = to_numeric(df, cols)
    df = exclude_sectors(df)

    before = len(df)
    needed_positive = ["revenue_y1", "revenue_y2", "total_assets_t", "total_assets_t_minus_1"]
    df = df[df[cols[:22]].notna().all(axis=1)
            & (df[needed_positive] > 0).all(axis=1)
            & (df["gross_margin_t"] > 0) & (df["gross_margin_t_minus_1"] > 0)].copy()
    report_dropped(before, len(df), NAME, "missing M-score components (or excluded sector)")

    s_t, s_1 = df["revenue_y1"], df["revenue_y2"]
    ta_t, ta_1 = df["total_assets_t"], df["total_assets_t_minus_1"]

    dsri = (df["receivables_t"] / s_t) / (df["receivables_t_minus_1"] / s_1)
    gmi = df["gross_margin_t_minus_1"] / df["gross_margin_t"]
    aq_t = 1 - (df["current_assets_t"] + df["ppe_net_t"]) / ta_t
    aq_1 = 1 - (df["current_assets_t_minus_1"] + df["ppe_net_t_minus_1"]) / ta_1
    aqi = aq_t / aq_1
    sgi = s_t / s_1
    dep_rate_t = df["dep_amort_t"] / (df["dep_amort_t"] + df["ppe_net_t"])
    dep_rate_1 = df["dep_amort_t_minus_1"] / (df["dep_amort_t_minus_1"] + df["ppe_net_t_minus_1"])
    depi = dep_rate_1 / dep_rate_t
    sgai = (df["sga_t"] / s_t) / (df["sga_t_minus_1"] / s_1)
    tata = (df["net_income_t"] - df["operating_cash_flow_t"]) / ta_t
    lev_t = (df["long_term_debt_t"] + df["current_liabilities_t"]) / ta_t
    lev_1 = (df["long_term_debt_t_minus_1"] + df["current_liabilities_t_minus_1"]) / ta_1
    lvgi = lev_t / lev_1

    df["beneish_m"] = (-4.84 + 0.920 * dsri + 0.528 * gmi + 0.404 * aqi + 0.892 * sgi
                       + 0.115 * depi - 0.172 * sgai + 4.679 * tata - 0.327 * lvgi)

    before = len(df)
    df = df[df["beneish_m"].notna() & (df["beneish_m"] <= manipulator_cutoff)].copy()
    report_dropped(before, len(df), NAME, f"flagged as likely manipulator (M > {manipulator_cutoff})")

    df["value_score"] = composite_value_score(df)
    df = df[df["value_score"].notna()].copy()

    return finish(df, ["ticker", "company", "sector", "industry", "market_cap",
                       "beneish_m", "value_score", "pe", "ev_ebit"],
                  sort_by="value_score", ascending=False, top=top)


if __name__ == "__main__":
    raise SystemExit(standard_main(screen_beneish_m, NAME))
