#!/usr/bin/env python3
"""
screen_garp_peg.py — GARP / PEG (growth at a reasonable price)
==============================================================

Peter Lynch (1989, "One Up on Wall Street"): "The P/E ratio of any company
that's fairly priced will equal its growth rate" — i.e. PEG ≈ 1 is fair,
PEG < 1 is attractive.

    PEG = PE / (100 × EPS growth)

The growth input is the trailing 5-year diluted-EPS CAGR (the committed data
is historical; Lynch's original uses expected growth — the trailing CAGR is
the documented, reproducible stand-in).

Screen: require EPS CAGR >= 10%, positive PE, 5y average ROE >= 10% (Lynch's
quality check); rank by PEG ascending.

Required columns: pe, eps_cagr_5y, roe_5y_avg (committed — runs without
extended_input.csv).

Educational tool — not investment advice.
"""

from __future__ import annotations

import pandas as pd

from screen_lib import (finish, normalize_columns, report_dropped,
                        require_columns, standard_main, to_numeric)

NAME = "GARP / PEG (Lynch)"
CITATION = "Lynch (1989), One Up on Wall Street; growth-forecast motivation in arXiv:1711.04837"

THRESHOLDS = {"min_eps_cagr": 0.10, "min_roe_5y": 0.10}


def screen_garp_peg(df: pd.DataFrame, top: int | None = None,
                    min_eps_cagr: float = THRESHOLDS["min_eps_cagr"],
                    min_roe_5y: float = THRESHOLDS["min_roe_5y"]) -> pd.DataFrame:
    df = normalize_columns(df)
    require_columns(df, ["ticker", "pe", "eps_cagr_5y", "roe_5y_avg"], NAME)
    df = to_numeric(df, ["pe", "eps_cagr_5y", "roe_5y_avg"])

    before = len(df)
    df = df[(df["pe"] > 0) & (df["eps_cagr_5y"] >= min_eps_cagr)
            & (df["roe_5y_avg"] >= min_roe_5y)].copy()
    report_dropped(before, len(df), NAME, "failed PE>0 / EPS-growth / ROE gates")

    df["peg"] = df["pe"] / (df["eps_cagr_5y"] * 100)

    return finish(df, ["ticker", "company", "sector", "industry", "market_cap",
                       "pe", "eps_cagr_5y", "roe_5y_avg", "peg"],
                  sort_by="peg", ascending=True, top=top)


if __name__ == "__main__":
    raise SystemExit(standard_main(screen_garp_peg, NAME))
