#!/usr/bin/env python3
"""
run_screens.py — run the three screens and report top 10 of each + overlaps
===========================================================================

Reads the input CSVs produced by build_screen_inputs.py and runs:
    1. Buffett Quality Compounder   (screen_buffett_quality.py)
    2. Magic Formula                (screen_magic_formula.py)
    3. Piotroski F-Score value      (screen_piotroski_f_score.py)

Writes per-screen top-N CSVs to screens/results/ and prints a console report,
including the names that appear in more than one screen (the overlap set).

    python3 run_screens.py                 # top 10 each, default input dir
    python3 run_screens.py --top 15

Educational tool — not investment advice.
"""

import argparse
import os

import pandas as pd

from screen_buffett_quality import calculate_buffett_quality_score
from screen_magic_formula import calculate_magic_formula
from screen_piotroski_f_score import screen_piotroski_value

HERE = os.path.dirname(os.path.abspath(__file__))
INPUTS = os.path.join(HERE, "inputs")
RESULTS = os.path.join(HERE, "results")


def _load(name):
    path = os.path.join(INPUTS, name)
    if not os.path.exists(path):
        raise SystemExit(f"Missing input: {path}\nRun build_screen_inputs.py first.")
    return pd.read_csv(path)


def run(top=10, min_f_score=7, pb_quantile=0.4):
    os.makedirs(RESULTS, exist_ok=True)

    buffett = calculate_buffett_quality_score(_load("buffett_quality_input.csv"), top=top)
    magic = calculate_magic_formula(_load("magic_formula_input.csv"), top=top)
    piotroski = screen_piotroski_value(
        _load("piotroski_input.csv"), max_price_to_book_quantile=pb_quantile,
        min_f_score=min_f_score, top=top)

    buffett.to_csv(os.path.join(RESULTS, "buffett_quality_top.csv"), index=False)
    magic.to_csv(os.path.join(RESULTS, "magic_formula_top.csv"), index=False)
    piotroski.to_csv(os.path.join(RESULTS, "piotroski_top.csv"), index=False)
    return buffett, magic, piotroski


def _show(title, df, cols):
    print(f"\n{'=' * 78}\n{title}  (top {len(df)})\n{'=' * 78}")
    have = [c for c in cols if c in df.columns]
    with pd.option_context("display.max_columns", None, "display.width", 200,
                           "display.float_format", lambda x: f"{x:,.3f}"):
        print(df[have].to_string(index=False))


def overlaps(buffett, magic, piotroski):
    sets = {"Buffett Quality": set(buffett["ticker"]),
            "Magic Formula": set(magic["ticker"]),
            "Piotroski": set(piotroski["ticker"])}
    counts = {}
    for name, s in sets.items():
        for t in s:
            counts.setdefault(t, []).append(name)
    return {t: who for t, who in counts.items() if len(who) >= 2}, sets


def main(argv=None):
    p = argparse.ArgumentParser(description="Run the three screens; report top N and overlaps.")
    p.add_argument("--top", type=int, default=10)
    p.add_argument("--min-f-score", type=int, default=7)
    p.add_argument("--pb-quantile", type=float, default=0.4)
    args = p.parse_args(argv)

    buffett, magic, piotroski = run(args.top, args.min_f_score, args.pb_quantile)

    _show("1. BUFFETT QUALITY COMPOUNDER", buffett,
          ["ticker", "company", "sector", "buffett_quality_total_score", "quality_score",
           "growth_score", "balance_sheet_score", "valuation_score", "roic_5y_avg",
           "roe_5y_avg", "fcf_margin_5y_avg", "pe"])
    _show("2. MAGIC FORMULA (Greenblatt)", magic,
          ["ticker", "company", "sector", "magic_formula_score", "earnings_yield",
           "return_on_capital", "earnings_yield_rank", "return_on_capital_rank"])
    _show("3. PIOTROSKI F-SCORE (low P/B value)", piotroski,
          ["ticker", "company", "sector", "piotroski_f_score", "price_to_book",
           "roa_t", "ocf_to_assets", "asset_turnover_t"])

    ov, _ = overlaps(buffett, magic, piotroski)
    print(f"\n{'=' * 78}\nOVERLAP — names in 2+ screens\n{'=' * 78}")
    if ov:
        for t, who in sorted(ov.items(), key=lambda kv: (-len(kv[1]), kv[0])):
            print(f"  {t:6s} — {', '.join(who)}")
    else:
        print("  (no overlap in this universe/top-N)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
