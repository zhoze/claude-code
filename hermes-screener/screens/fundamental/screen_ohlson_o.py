#!/usr/bin/env python3
"""
screen_ohlson_o.py — Ohlson O-score low-distress value
======================================================

Ohlson (1980, JAR) "Financial Ratios and the Probabilistic Prediction of
Bankruptcy" — the nine-variable Model 1 logit:

    O = −1.32 − 0.407·log(TA) + 6.03·TL/TA − 1.43·WC/TA + 0.0757·CL/CA
        − 1.72·OENEG − 2.37·NI/TA − 1.83·FFO/TL + 0.285·INTWO − 0.521·CHIN

    OENEG = 1 if TL > TA;  INTWO = 1 if NI < 0 in both of the last two years;
    CHIN  = (NI_t − NI_t-1)/(|NI_t| + |NI_t-1|)

Implementation conventions (documented, standard in replications): TA in
$ millions with the 1968-dollar GNP deflator omitted; FFO taken as operating
cash flow.

Screen: exclude financials/utilities/real estate, keep the lowest-distress
quintile of O, rank survivors by the composite-value score descending.

Requires inputs/extended_input.csv (total_liabilities t/t-1) — prints
"awaiting data refresh" until build_screen_inputs.py runs with FMP_KEY.

Educational tool — not investment advice.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from screen_lib import (composite_value_score, exclude_sectors, finish,
                        normalize_columns, report_dropped, require_columns,
                        standard_main, to_numeric)

NAME = "Ohlson O-Score Low-Distress Value"
CITATION = "Ohlson (1980), Journal of Accounting Research 18(1)"

THRESHOLDS = {"keep_quantile": 0.20}


def screen_ohlson_o(df: pd.DataFrame, top: int | None = None,
                    keep_quantile: float = THRESHOLDS["keep_quantile"]) -> pd.DataFrame:
    df = normalize_columns(df)
    cols = ["total_assets_t", "total_liabilities_t", "current_assets_t",
            "current_liabilities_t", "net_income_t", "net_income_t_minus_1",
            "operating_cash_flow_t", "pe", "ev_ebit", "price_to_book", "fcf_yield"]
    require_columns(df, ["ticker"] + cols, NAME)
    df = to_numeric(df, cols)
    df = exclude_sectors(df)

    before = len(df)
    df = df[(df["total_assets_t"] > 0) & (df["total_liabilities_t"] > 0)
            & (df["current_assets_t"] > 0) & df["current_liabilities_t"].notna()
            & df["net_income_t"].notna() & df["net_income_t_minus_1"].notna()
            & df["operating_cash_flow_t"].notna()].copy()
    report_dropped(before, len(df), NAME, "missing O-score components (or excluded sector)")

    ta, tl = df["total_assets_t"], df["total_liabilities_t"]
    wc = df["current_assets_t"] - df["current_liabilities_t"]
    ni, ni1 = df["net_income_t"], df["net_income_t_minus_1"]
    oeneg = (tl > ta).astype(float)
    intwo = ((ni < 0) & (ni1 < 0)).astype(float)
    chin = (ni - ni1) / (ni.abs() + ni1.abs())

    df["ohlson_o"] = (-1.32
                      - 0.407 * np.log(ta / 1e6)
                      + 6.03 * tl / ta
                      - 1.43 * wc / ta
                      + 0.0757 * df["current_liabilities_t"] / df["current_assets_t"]
                      - 1.72 * oeneg
                      - 2.37 * ni / ta
                      - 1.83 * df["operating_cash_flow_t"] / tl
                      + 0.285 * intwo
                      - 0.521 * chin)

    cutoff = df["ohlson_o"].quantile(keep_quantile)
    df = df[df["ohlson_o"] <= cutoff].copy()
    df["value_score"] = composite_value_score(df)
    before = len(df)
    df = df[df["value_score"].notna()].copy()
    report_dropped(before, len(df), NAME, "negative earnings/EBIT/book value in value blend")

    return finish(df, ["ticker", "company", "sector", "industry", "market_cap",
                       "ohlson_o", "value_score", "pe", "ev_ebit"],
                  sort_by="value_score", ascending=False, top=top)


if __name__ == "__main__":
    raise SystemExit(standard_main(screen_ohlson_o, NAME))
