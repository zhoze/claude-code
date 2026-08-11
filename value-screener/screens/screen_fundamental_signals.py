#!/usr/bin/env python3
"""
screen_fundamental_signals.py — Lev-Thiagarajan fundamental signals
===================================================================

Lev & Thiagarajan (1993, JAR) "Fundamental Information Analysis", as
operationalized by Abarbanell & Bushee (1998, TAR): year-over-year fundamental
signals that predict future earnings. One point per bullish signal:

    S1  %ΔInventory   <= %ΔSales      (inventory not outpacing demand)
    S2  %ΔReceivables <= %ΔSales      (no channel stuffing)
    S3  %ΔGross profit >= %ΔSales     (margins expanding)
    S4  %ΔSG&A        <= %ΔSales      (cost discipline)
    S5  effective tax rate not falling (earnings not flattered by tax items)
    S6  capex growth >= sector median  (investing at least in line with peers;
        sector is the documented stand-in for the paper's industry grouping)

The paper's LIFO-earnings, audit-qualification and labor-force signals are
not derivable from FMP data and are omitted (documented — never invented).
Rows must have at least 5 of the 6 signals computable; the score is the sum of
bullish signals. Rank descending, ROA tie-break.

Requires inputs/extended_input.csv (inventory, receivables, SG&A, capex, tax
histories) — prints "awaiting data refresh" until build_screen_inputs.py runs
with FMP_KEY.

Educational tool — not investment advice.
"""

from __future__ import annotations

import pandas as pd

from screen_lib import (finish, normalize_columns, report_dropped,
                        require_columns, standard_main, to_numeric)

NAME = "Fundamental Signals (Lev-Thiagarajan)"
CITATION = "Lev & Thiagarajan (1993), JAR 31(2); Abarbanell & Bushee (1998), The Accounting Review"

THRESHOLDS = {"min_signals_available": 5}


def _pct_change(now: pd.Series, then: pd.Series) -> pd.Series:
    return (now - then) / then.abs()


def screen_fundamental_signals(df: pd.DataFrame, top: int | None = None,
                               min_signals_available: int = THRESHOLDS["min_signals_available"]) -> pd.DataFrame:
    df = normalize_columns(df)
    cols = ["inventory_t", "inventory_t_minus_1", "receivables_t", "receivables_t_minus_1",
            "revenue_y1", "revenue_y2", "gross_margin_t", "gross_margin_t_minus_1",
            "sga_t", "sga_t_minus_1", "income_tax_expense_t", "income_tax_expense_t_minus_1",
            "pretax_income_t", "pretax_income_t_minus_1", "capex_t", "capex_t_minus_1",
            "net_income_t", "total_assets_t", "sector"]
    require_columns(df, ["ticker"] + cols, NAME)
    df = to_numeric(df, [c for c in cols if c != "sector"])

    before = len(df)
    df = df[(df["revenue_y1"] > 0) & (df["revenue_y2"] > 0)].copy()
    report_dropped(before, len(df), NAME, "missing revenue history")

    sales_g = _pct_change(df["revenue_y1"], df["revenue_y2"])
    gp_g = _pct_change(df["gross_margin_t"] * df["revenue_y1"],
                       df["gross_margin_t_minus_1"] * df["revenue_y2"])
    etr_t = df["income_tax_expense_t"] / df["pretax_income_t"]
    etr_1 = df["income_tax_expense_t_minus_1"] / df["pretax_income_t_minus_1"]
    etr_valid = (df["pretax_income_t"] > 0) & (df["pretax_income_t_minus_1"] > 0)
    capex_g = _pct_change(df["capex_t"].abs(), df["capex_t_minus_1"].abs())
    capex_med = capex_g.groupby(df["sector"]).transform("median")

    signals = pd.DataFrame(index=df.index)
    signals["s1_inventory"] = _pct_change(df["inventory_t"], df["inventory_t_minus_1"]) <= sales_g
    signals["s2_receivables"] = _pct_change(df["receivables_t"], df["receivables_t_minus_1"]) <= sales_g
    signals["s3_gross_margin"] = gp_g >= sales_g
    signals["s4_sga"] = _pct_change(df["sga_t"], df["sga_t_minus_1"]) <= sales_g
    signals["s5_tax_rate"] = (etr_t >= etr_1).where(etr_valid)
    signals["s6_capex"] = capex_g >= capex_med

    # A signal is only counted where its inputs exist; rows must have most signals.
    computable = signals.notna().sum(axis=1)
    df["fundamental_score"] = signals.fillna(False).astype(int).sum(axis=1)
    df["signals_available"] = computable
    df["roa"] = df["net_income_t"] / df["total_assets_t"]

    before = len(df)
    df = df[computable >= min_signals_available].copy()
    report_dropped(before, len(df), NAME,
                   f"fewer than {min_signals_available} of 6 signals computable")

    df = df.sort_values(["fundamental_score", "roa", "ticker"], ascending=[False, False, True])
    return finish(df, ["ticker", "company", "sector", "industry", "market_cap",
                       "fundamental_score", "signals_available", "roa"],
                  sort_by="fundamental_score", ascending=False, top=top)


if __name__ == "__main__":
    raise SystemExit(standard_main(screen_fundamental_signals, NAME))
