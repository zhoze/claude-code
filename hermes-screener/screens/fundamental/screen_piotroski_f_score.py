#!/usr/bin/env python3
"""
Piotroski F-Score value stock screen

Finds financially improving companies among low price-to-book stocks.

Typical use:
    python screen_piotroski_f_score.py input.csv --output piotroski_results.csv

Required CSV columns:
    ticker, price_to_book,
    net_income_t, total_assets_t, total_assets_t_minus_1,
    operating_cash_flow_t,
    long_term_debt_t, long_term_debt_t_minus_1,
    current_assets_t, current_liabilities_t,
    current_assets_t_minus_1, current_liabilities_t_minus_1,
    shares_outstanding_t, shares_outstanding_t_minus_1,
    gross_margin_t, gross_margin_t_minus_1,
    asset_turnover_t, asset_turnover_t_minus_1

Optional CSV columns:
    company, sector, industry, market_cap

F-Score criteria:
Profitability:
    1. Positive ROA
    2. Positive operating cash flow
    3. ROA improved year over year
    4. Operating cash flow > net income
Leverage/liquidity/source of funds:
    5. Lower long-term debt/assets
    6. Higher current ratio
    7. No share dilution
Operating efficiency:
    8. Higher gross margin
    9. Higher asset turnover

This is an educational screen, not investment advice.
"""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Iterable

import pandas as pd


REQUIRED_COLUMNS = [
    "ticker",
    "price_to_book",
    "net_income_t",
    "total_assets_t",
    "total_assets_t_minus_1",
    "operating_cash_flow_t",
    "long_term_debt_t",
    "long_term_debt_t_minus_1",
    "current_assets_t",
    "current_liabilities_t",
    "current_assets_t_minus_1",
    "current_liabilities_t_minus_1",
    "shares_outstanding_t",
    "shares_outstanding_t_minus_1",
    "gross_margin_t",
    "gross_margin_t_minus_1",
    "asset_turnover_t",
    "asset_turnover_t_minus_1",
]


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


def calculate_piotroski_f_score(df: pd.DataFrame) -> pd.DataFrame:
    """Return DataFrame with Piotroski F-Score components and total score."""
    df = _normalize_columns(df)
    _require_columns(df, REQUIRED_COLUMNS)
    df = _to_numeric(df, [c for c in REQUIRED_COLUMNS if c != "ticker"] + ["market_cap"])

    df = df[(df["total_assets_t"] > 0) & (df["total_assets_t_minus_1"] > 0)].copy()
    avg_assets = (df["total_assets_t"] + df["total_assets_t_minus_1"]) / 2

    df["roa_t"] = df["net_income_t"] / avg_assets
    # If prior net income is unavailable, use current total assets base where possible.
    if "net_income_t_minus_1" in df.columns:
        df["net_income_t_minus_1"] = pd.to_numeric(df["net_income_t_minus_1"], errors="coerce")
        df["roa_t_minus_1"] = df["net_income_t_minus_1"] / df["total_assets_t_minus_1"]
    else:
        df["roa_t_minus_1"] = 0

    df["ocf_to_assets"] = df["operating_cash_flow_t"] / avg_assets
    df["debt_to_assets_t"] = df["long_term_debt_t"] / df["total_assets_t"]
    df["debt_to_assets_t_minus_1"] = df["long_term_debt_t_minus_1"] / df["total_assets_t_minus_1"]
    df["current_ratio_t"] = df["current_assets_t"] / df["current_liabilities_t"].replace(0, pd.NA)
    df["current_ratio_t_minus_1"] = df["current_assets_t_minus_1"] / df[
        "current_liabilities_t_minus_1"
    ].replace(0, pd.NA)

    df["f_positive_roa"] = (df["roa_t"] > 0).astype(int)
    df["f_positive_ocf"] = (df["operating_cash_flow_t"] > 0).astype(int)
    df["f_roa_improved"] = (df["roa_t"] > df["roa_t_minus_1"]).astype(int)
    df["f_ocf_gt_net_income"] = (df["operating_cash_flow_t"] > df["net_income_t"]).astype(int)
    df["f_lower_leverage"] = (df["debt_to_assets_t"] < df["debt_to_assets_t_minus_1"]).astype(int)
    df["f_higher_current_ratio"] = (df["current_ratio_t"] > df["current_ratio_t_minus_1"]).astype(int)
    df["f_no_dilution"] = (df["shares_outstanding_t"] <= df["shares_outstanding_t_minus_1"]).astype(int)
    df["f_higher_gross_margin"] = (df["gross_margin_t"] > df["gross_margin_t_minus_1"]).astype(int)
    df["f_higher_asset_turnover"] = (df["asset_turnover_t"] > df["asset_turnover_t_minus_1"]).astype(int)

    score_cols = [c for c in df.columns if c.startswith("f_")]
    df["piotroski_f_score"] = df[score_cols].sum(axis=1)
    return df


def screen_piotroski_value(
    df: pd.DataFrame,
    max_price_to_book_quantile: float = 0.4,
    min_f_score: int = 7,
    min_market_cap: float = 0,
    top: int | None = None,
) -> pd.DataFrame:
    """Filter to low P/B companies with high Piotroski F-Scores."""
    scored = calculate_piotroski_f_score(df)
    scored = scored[(scored["price_to_book"] > 0)].copy()

    if min_market_cap and "market_cap" in scored.columns:
        scored = scored[scored["market_cap"] >= min_market_cap]

    pb_cutoff = scored["price_to_book"].quantile(max_price_to_book_quantile)
    result = scored[
        (scored["price_to_book"] <= pb_cutoff)
        & (scored["piotroski_f_score"] >= min_f_score)
    ].copy()

    preferred_cols = [
        "ticker",
        "company",
        "sector",
        "industry",
        "market_cap",
        "price_to_book",
        "piotroski_f_score",
        "roa_t",
        "ocf_to_assets",
        "debt_to_assets_t",
        "current_ratio_t",
        "gross_margin_t",
        "asset_turnover_t",
        "f_positive_roa",
        "f_positive_ocf",
        "f_roa_improved",
        "f_ocf_gt_net_income",
        "f_lower_leverage",
        "f_higher_current_ratio",
        "f_no_dilution",
        "f_higher_gross_margin",
        "f_higher_asset_turnover",
    ]
    cols = [c for c in preferred_cols if c in result.columns]
    result = result.sort_values(["piotroski_f_score", "price_to_book"], ascending=[False, True])[cols]
    return result.head(top) if top else result


def main() -> None:
    parser = argparse.ArgumentParser(description="Run Piotroski F-Score value screen on a CSV file.")
    parser.add_argument("input", type=Path, help="Input CSV with fundamental data")
    parser.add_argument("--output", "-o", type=Path, default=Path("piotroski_results.csv"))
    parser.add_argument("--top", type=int, default=50, help="Number of top results to save")
    parser.add_argument("--min-f-score", type=int, default=7, help="Minimum Piotroski F-Score")
    parser.add_argument(
        "--pb-quantile",
        type=float,
        default=0.4,
        help="Keep stocks in lowest price-to-book quantile, e.g. 0.2 or 0.4",
    )
    parser.add_argument("--min-market-cap", type=float, default=0, help="Minimum market cap filter")
    args = parser.parse_args()

    df = pd.read_csv(args.input)
    results = screen_piotroski_value(
        df,
        max_price_to_book_quantile=args.pb_quantile,
        min_f_score=args.min_f_score,
        min_market_cap=args.min_market_cap,
        top=args.top,
    )
    results.to_csv(args.output, index=False)
    print(f"Saved {len(results)} rows to {args.output}")


if __name__ == "__main__":
    main()
