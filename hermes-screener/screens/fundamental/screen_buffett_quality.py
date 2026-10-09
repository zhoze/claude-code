#!/usr/bin/env python3
"""
Buffett-style Quality Compounder screen

This screen tries to approximate the kind of business characteristics often
associated with high-quality long-term compounders:
- High and persistent returns on capital/equity
- Strong margins and free cash flow generation
- Moderate leverage
- Consistent growth
- Reasonable valuation, not just quality at any price

Typical use:
    python screen_buffett_quality.py input.csv --output buffett_quality_results.csv

Required CSV columns:
    ticker,
    roic_5y_avg, roe_5y_avg,
    gross_margin_5y_avg, operating_margin_5y_avg, fcf_margin_5y_avg,
    revenue_cagr_5y, eps_cagr_5y,
    debt_to_equity, interest_coverage,
    fcf_yield, pe

Optional CSV columns:
    company, sector, industry, market_cap, current_ratio, ev_ebit,
    shares_change_5y_pct, moat_score

This is an educational screen, not investment advice.
"""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Iterable

import pandas as pd


REQUIRED_COLUMNS = [
    "ticker",
    "roic_5y_avg",
    "roe_5y_avg",
    "gross_margin_5y_avg",
    "operating_margin_5y_avg",
    "fcf_margin_5y_avg",
    "revenue_cagr_5y",
    "eps_cagr_5y",
    "debt_to_equity",
    "interest_coverage",
    "fcf_yield",
    "pe",
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


def _score_between(series: pd.Series, low: float, high: float, higher_is_better: bool = True) -> pd.Series:
    """Scale a metric into a 0-100 score using low/high anchors."""
    clipped = series.clip(lower=low, upper=high)
    score = 100 * (clipped - low) / max(high - low, 1e-9)
    return score if higher_is_better else 100 - score


def calculate_buffett_quality_score(
    df: pd.DataFrame,
    min_market_cap: float = 0,
    top: int | None = None,
) -> pd.DataFrame:
    """Return Buffett-style quality-compounder ranked stocks."""
    df = _normalize_columns(df)
    _require_columns(df, REQUIRED_COLUMNS)

    numeric_cols = REQUIRED_COLUMNS + [
        "market_cap",
        "current_ratio",
        "ev_ebit",
        "shares_change_5y_pct",
        "moat_score",
    ]
    df = _to_numeric(df, [c for c in numeric_cols if c != "ticker"])

    if min_market_cap and "market_cap" in df.columns:
        df = df[df["market_cap"] >= min_market_cap]

    df = df.copy()

    # Quality: profitability, cash generation, and business economics.
    df["score_roic"] = _score_between(df["roic_5y_avg"], 0.05, 0.25)
    df["score_roe"] = _score_between(df["roe_5y_avg"], 0.08, 0.30)
    df["score_gross_margin"] = _score_between(df["gross_margin_5y_avg"], 0.20, 0.65)
    df["score_operating_margin"] = _score_between(df["operating_margin_5y_avg"], 0.08, 0.35)
    df["score_fcf_margin"] = _score_between(df["fcf_margin_5y_avg"], 0.05, 0.25)

    # Growth: prefer steady positive growth, but cap the benefit to avoid lottery-ticket growth.
    df["score_revenue_growth"] = _score_between(df["revenue_cagr_5y"], 0.00, 0.12)
    df["score_eps_growth"] = _score_between(df["eps_cagr_5y"], 0.00, 0.15)

    # Balance sheet: lower debt, higher interest coverage.
    df["score_debt"] = _score_between(df["debt_to_equity"], 0.0, 2.0, higher_is_better=False)
    df["score_interest_coverage"] = _score_between(df["interest_coverage"], 2.0, 20.0)

    # Valuation: quality at a reasonable price.
    df["score_fcf_yield"] = _score_between(df["fcf_yield"], 0.02, 0.08)
    df["score_pe"] = _score_between(df["pe"], 10.0, 40.0, higher_is_better=False)

    if "ev_ebit" in df.columns:
        df["score_ev_ebit"] = _score_between(df["ev_ebit"], 8.0, 30.0, higher_is_better=False)
    else:
        df["score_ev_ebit"] = 50

    if "current_ratio" in df.columns:
        df["score_current_ratio"] = _score_between(df["current_ratio"], 1.0, 3.0)
    else:
        df["score_current_ratio"] = 50

    if "shares_change_5y_pct" in df.columns:
        # Negative share change = buybacks; positive = dilution.
        df["score_share_discipline"] = _score_between(
            df["shares_change_5y_pct"], -0.20, 0.20, higher_is_better=False
        )
    else:
        df["score_share_discipline"] = 50

    if "moat_score" in df.columns:
        # Use own moat score if provided on 0-100 scale.
        df["score_moat"] = df["moat_score"].clip(0, 100)
    else:
        df["score_moat"] = (
            0.35 * df["score_roic"]
            + 0.25 * df["score_gross_margin"]
            + 0.25 * df["score_operating_margin"]
            + 0.15 * df["score_fcf_margin"]
        )

    df["quality_score"] = (
        0.24 * df["score_roic"]
        + 0.16 * df["score_roe"]
        + 0.14 * df["score_gross_margin"]
        + 0.14 * df["score_operating_margin"]
        + 0.16 * df["score_fcf_margin"]
        + 0.16 * df["score_moat"]
    )
    df["growth_score"] = 0.50 * df["score_revenue_growth"] + 0.50 * df["score_eps_growth"]
    df["balance_sheet_score"] = (
        0.45 * df["score_debt"]
        + 0.40 * df["score_interest_coverage"]
        + 0.15 * df["score_current_ratio"]
    )
    df["valuation_score"] = (
        0.40 * df["score_fcf_yield"] + 0.35 * df["score_pe"] + 0.25 * df["score_ev_ebit"]
    )
    df["capital_allocation_score"] = df["score_share_discipline"]

    df["buffett_quality_total_score"] = (
        0.40 * df["quality_score"]
        + 0.20 * df["growth_score"]
        + 0.20 * df["balance_sheet_score"]
        + 0.15 * df["valuation_score"]
        + 0.05 * df["capital_allocation_score"]
    )

    # Hard quality filters before final ranking.
    result = df[
        (df["roic_5y_avg"] >= 0.10)
        & (df["roe_5y_avg"] >= 0.12)
        & (df["fcf_margin_5y_avg"] >= 0.05)
        & (df["debt_to_equity"] <= 2.0)
        & (df["interest_coverage"] >= 3.0)
        & (df["fcf_yield"] > 0)
        & (df["pe"] > 0)
    ].copy()

    preferred_cols = [
        "ticker",
        "company",
        "sector",
        "industry",
        "market_cap",
        "buffett_quality_total_score",
        "quality_score",
        "growth_score",
        "balance_sheet_score",
        "valuation_score",
        "capital_allocation_score",
        "roic_5y_avg",
        "roe_5y_avg",
        "gross_margin_5y_avg",
        "operating_margin_5y_avg",
        "fcf_margin_5y_avg",
        "revenue_cagr_5y",
        "eps_cagr_5y",
        "debt_to_equity",
        "interest_coverage",
        "current_ratio",
        "fcf_yield",
        "pe",
        "ev_ebit",
        "shares_change_5y_pct",
        "moat_score",
    ]
    cols = [c for c in preferred_cols if c in result.columns]
    result = result.sort_values(["buffett_quality_total_score", "quality_score"], ascending=False)[cols]
    return result.head(top) if top else result


def main() -> None:
    parser = argparse.ArgumentParser(description="Run Buffett-style Quality Compounder screen on a CSV file.")
    parser.add_argument("input", type=Path, help="Input CSV with fundamental data")
    parser.add_argument("--output", "-o", type=Path, default=Path("buffett_quality_results.csv"))
    parser.add_argument("--top", type=int, default=50, help="Number of top results to save")
    parser.add_argument("--min-market-cap", type=float, default=0, help="Minimum market cap filter")
    args = parser.parse_args()

    df = pd.read_csv(args.input)
    results = calculate_buffett_quality_score(df, min_market_cap=args.min_market_cap, top=args.top)
    results.to_csv(args.output, index=False)
    print(f"Saved {len(results)} rows to {args.output}")


if __name__ == "__main__":
    main()
