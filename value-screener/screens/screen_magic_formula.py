#!/usr/bin/env python3
"""
Magic Formula stock screen

Ranks companies by:
1) Earnings yield = EBIT / Enterprise Value
2) Return on capital = EBIT / invested capital

Typical use:
    python screen_magic_formula.py input.csv --top 50 --output magic_formula_results.csv

Required CSV columns:
    ticker, enterprise_value, ebit

Capital columns, choose one approach:
    A) invested_capital
       OR
    B) net_working_capital and net_fixed_assets

Optional CSV columns:
    company, sector, industry, market_cap, price

Notes:
- By default, excludes financials and utilities because their capital structures
  make EBIT/EV and ROC less comparable.
- This is an educational screen, not investment advice.
"""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Iterable

import pandas as pd


DEFAULT_EXCLUDED_SECTORS = {"Financials", "Financial Services", "Banks", "Utilities", "Real Estate"}


def _normalize_columns(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df.columns = [c.strip().lower().replace(" ", "_") for c in df.columns]
    return df


def _require_columns(df: pd.DataFrame, required: Iterable[str]) -> None:
    missing = [c for c in required if c not in df.columns]
    if missing:
        raise ValueError(f"Missing required columns: {', '.join(missing)}")


def _to_numeric(df: pd.DataFrame, cols: Iterable[str]) -> pd.DataFrame:
    df = df.copy()
    for col in cols:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")
    return df


def calculate_magic_formula(
    df: pd.DataFrame,
    min_market_cap: float = 0,
    exclude_sectors: bool = True,
    top: int | None = None,
) -> pd.DataFrame:
    """Return Magic Formula ranked stocks from a fundamentals DataFrame."""
    df = _normalize_columns(df)
    _require_columns(df, ["ticker", "enterprise_value", "ebit"])

    numeric_cols = [
        "enterprise_value",
        "ebit",
        "invested_capital",
        "net_working_capital",
        "net_fixed_assets",
        "market_cap",
    ]
    df = _to_numeric(df, numeric_cols)

    if "invested_capital" not in df.columns:
        _require_columns(df, ["net_working_capital", "net_fixed_assets"])
        df["invested_capital"] = df["net_working_capital"] + df["net_fixed_assets"]

    if exclude_sectors and "sector" in df.columns:
        df = df[~df["sector"].astype(str).str.strip().isin(DEFAULT_EXCLUDED_SECTORS)]

    if min_market_cap and "market_cap" in df.columns:
        df = df[df["market_cap"] >= min_market_cap]

    df = df[
        (df["enterprise_value"] > 0)
        & (df["ebit"] > 0)
        & (df["invested_capital"] > 0)
    ].copy()

    df["earnings_yield"] = df["ebit"] / df["enterprise_value"]
    df["return_on_capital"] = df["ebit"] / df["invested_capital"]

    # Higher is better for both, so descending ranks.
    df["earnings_yield_rank"] = df["earnings_yield"].rank(ascending=False, method="min")
    df["return_on_capital_rank"] = df["return_on_capital"].rank(ascending=False, method="min")
    df["magic_formula_rank_sum"] = df["earnings_yield_rank"] + df["return_on_capital_rank"]
    df["magic_formula_score"] = 100 * (
        1 - (df["magic_formula_rank_sum"] - df["magic_formula_rank_sum"].min())
        / max(1, df["magic_formula_rank_sum"].max() - df["magic_formula_rank_sum"].min())
    )

    preferred_cols = [
        "ticker",
        "company",
        "sector",
        "industry",
        "market_cap",
        "enterprise_value",
        "ebit",
        "invested_capital",
        "earnings_yield",
        "return_on_capital",
        "earnings_yield_rank",
        "return_on_capital_rank",
        "magic_formula_rank_sum",
        "magic_formula_score",
    ]
    cols = [c for c in preferred_cols if c in df.columns]
    result = df.sort_values(["magic_formula_rank_sum", "ticker"])[cols]
    return result.head(top) if top else result


def main() -> None:
    parser = argparse.ArgumentParser(description="Run Magic Formula stock screen on a CSV file.")
    parser.add_argument("input", type=Path, help="Input CSV with fundamental data")
    parser.add_argument("--output", "-o", type=Path, default=Path("magic_formula_results.csv"))
    parser.add_argument("--top", type=int, default=50, help="Number of top results to save")
    parser.add_argument("--min-market-cap", type=float, default=0, help="Minimum market cap filter")
    parser.add_argument(
        "--include-financials-utilities",
        action="store_true",
        help="Do not exclude financials/utilities/real estate",
    )
    args = parser.parse_args()

    df = pd.read_csv(args.input)
    results = calculate_magic_formula(
        df,
        min_market_cap=args.min_market_cap,
        exclude_sectors=not args.include_financials_utilities,
        top=args.top,
    )
    results.to_csv(args.output, index=False)
    print(f"Saved {len(results)} rows to {args.output}")


if __name__ == "__main__":
    main()
