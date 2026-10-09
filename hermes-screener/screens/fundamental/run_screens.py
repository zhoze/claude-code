#!/usr/bin/env python3
"""
run_screens.py — run all 20 fundamental screens + consensus top 10
==================================================================

Reads the input CSVs produced by build_screen_inputs.py and runs every screen
in this directory (see README.md for the full roster and citations). For each
screen it writes results/<key>_top.csv; screens whose exact formula needs
columns from inputs/extended_input.csv are skipped with an "awaiting data
refresh" notice until build_screen_inputs.py has been run with FMP_KEY.

It then aggregates a consensus ranking across every screen that ran — for each
ticker: in how many screen top-Ns it appears and its average cross-sectional
percentile — and writes results/consensus_top10.csv, results/consensus_all.csv
and a regenerated results/SUMMARY.md.

    python3 run_screens.py                 # top 10 each, default input dir
    python3 run_screens.py --top 15
    python3 run_screens.py --only composite_value,garp_peg
    python3 run_screens.py --selftest      # synthetic-frame check of all screens

Educational tool — not investment advice.
"""

import argparse
import os
import time

import pandas as pd

import screen_accruals
import screen_altman_z
import screen_asset_growth
import screen_beneish_m
import screen_composite_value
import screen_dividend_growth
import screen_fcf_yield
import screen_fundamental_signals
import screen_garp_peg
import screen_graham_defensive
import screen_gross_profitability
import screen_low_leverage_quality
import screen_mohanram_g
import screen_ohlson_o
import screen_operating_profitability
import screen_residual_income
import screen_shareholder_yield
from screen_buffett_quality import calculate_buffett_quality_score
from screen_lib import (MissingInputError, drop_non_equity, load_joined,
                        pct_rank, synthetic_frame)
from screen_magic_formula import calculate_magic_formula
from screen_piotroski_f_score import screen_piotroski_value

HERE = os.path.dirname(os.path.abspath(__file__))
INPUTS = os.path.join(HERE, "inputs")
RESULTS = os.path.join(HERE, "results")


def _load(name):
    path = os.path.join(INPUTS, name)
    if not os.path.exists(path):
        raise SystemExit(f"Missing input: {path}\nRun build_screen_inputs.py first.")
    return drop_non_equity(pd.read_csv(path))


# Registry: key, title, runner(joined, opts) -> full ranked DataFrame,
# score column, higher-is-better flag, citation, output CSV name.
# The three original screens keep their own input files and output names.
def _registry():
    legacy = [
        ("buffett_quality", "Buffett Quality Compounder",
         lambda j, o: calculate_buffett_quality_score(_load("buffett_quality_input.csv"), top=None),
         "buffett_quality_total_score", True,
         "Buffett/Hagstrom; quality lineage in Asness, Frazzini & Pedersen (2019)",
         "buffett_quality_top.csv"),
        ("magic_formula", "Magic Formula (Greenblatt)",
         lambda j, o: calculate_magic_formula(_load("magic_formula_input.csv"), top=None),
         "magic_formula_score", True,
         "Greenblatt (2006), The Little Book That Beats the Market; arXiv:1711.04837",
         "magic_formula_top.csv"),
        ("piotroski", "Piotroski F-Score (low P/B value)",
         lambda j, o: screen_piotroski_value(
             _load("piotroski_input.csv"), max_price_to_book_quantile=o["pb_quantile"],
             min_f_score=o["min_f_score"], top=None),
         "piotroski_f_score", True,
         "Piotroski (2000), Journal of Accounting Research 38; arXiv:1906.05327",
         "piotroski_top.csv"),
    ]
    modern = [
        ("graham_defensive", screen_graham_defensive, "screen_graham_defensive", "pe_x_pb", False),
        ("gross_profitability", screen_gross_profitability, "screen_gross_profitability", "gp_score", True),
        ("operating_profitability", screen_operating_profitability, "screen_operating_profitability",
         "operating_profitability", True),
        ("accruals", screen_accruals, "screen_accruals", "accruals_to_assets", False),
        ("fcf_yield", screen_fcf_yield, "screen_fcf_yield", "fcf_yield", True),
        ("shareholder_yield", screen_shareholder_yield, "screen_shareholder_yield",
         "shareholder_yield", True),
        ("altman_z", screen_altman_z, "screen_altman_z", "ev_ebit", False),
        ("ohlson_o", screen_ohlson_o, "screen_ohlson_o", "value_score", True),
        ("beneish_m", screen_beneish_m, "screen_beneish_m", "value_score", True),
        ("mohanram_g", screen_mohanram_g, "screen_mohanram_g", "mohanram_g_score", True),
        ("residual_income", screen_residual_income, "screen_residual_income", "ri_score", True),
        ("composite_value", screen_composite_value, "screen_composite_value", "value_score", True),
        ("dividend_growth", screen_dividend_growth, "screen_dividend_growth", "dividend_cagr_5y", True),
        ("low_leverage_quality", screen_low_leverage_quality, "screen_low_leverage_quality",
         "roic_5y_avg", True),
        ("asset_growth", screen_asset_growth, "screen_asset_growth", "asset_growth_1y", False),
        ("fundamental_signals", screen_fundamental_signals, "screen_fundamental_signals",
         "fundamental_score", True),
        ("garp_peg", screen_garp_peg, "screen_garp_peg", "peg", False),
    ]
    entries = list(legacy)
    for key, mod, fn_name, score, higher in modern:
        fn = getattr(mod, fn_name)
        entries.append((key, mod.NAME,
                        (lambda f: lambda j, o: f(j, top=None))(fn),
                        score, higher, mod.CITATION, f"{key}_top.csv"))
    return entries


def _as_of():
    stamp = os.path.join(INPUTS, "as_of.txt")
    if os.path.exists(stamp):
        return open(stamp).read().strip()
    return "2026-07-27 (last committed data refresh; run build_screen_inputs.py to update)"


def run(top=10, min_f_score=7, pb_quantile=0.4, only=None):
    os.makedirs(RESULTS, exist_ok=True)
    joined = load_joined(INPUTS)
    opts = {"min_f_score": min_f_score, "pb_quantile": pb_quantile}

    results = []
    for key, title, runner, score, higher, citation, outfile in _registry():
        if only and key not in only:
            continue
        entry = {"key": key, "title": title, "score": score, "higher": higher,
                 "citation": citation, "outfile": outfile, "full": None, "skip": None}
        try:
            full = runner(joined, opts)
            entry["full"] = full
            full.head(top).to_csv(os.path.join(RESULTS, outfile), index=False)
        except MissingInputError as e:
            entry["skip"] = str(e)
        results.append(entry)
    return results, joined


def consensus(results, joined, top=10):
    """Cross-screen aggregate: top-N appearances + average percentile."""
    ran = [r for r in results if r["full"] is not None and len(r["full"]) > 0]
    if not ran:
        return None, None
    rows = {}
    for r in ran:
        full = r["full"].drop_duplicates("ticker").set_index("ticker")
        pct = pct_rank(full[r["score"]], higher_is_better=r["higher"])
        top_set = set(r["full"].head(top)["ticker"])
        for t, p in pct.items():
            d = rows.setdefault(t, {"pcts": [], "top_hits": []})
            d["pcts"].append(p)
            if t in top_set:
                d["top_hits"].append(r["key"])

    meta = joined.drop_duplicates("ticker").set_index("ticker")
    out = pd.DataFrame([
        {"ticker": t,
         "company": meta["company"].get(t, ""),
         "sector": meta["sector"].get(t, ""),
         "screens_eligible": len(d["pcts"]),
         "top10_count": len(d["top_hits"]),
         "avg_percentile": sum(d["pcts"]) / len(d["pcts"]),
         "screens": ", ".join(sorted(d["top_hits"]))}
        for t, d in rows.items()])
    # Require eligibility in at least half of the screens that ran, so a name
    # that only survives one niche filter can't top the consensus.
    out = out[out["screens_eligible"] >= len(ran) / 2]
    out = out.sort_values(["top10_count", "avg_percentile", "ticker"],
                          ascending=[False, False, True]).reset_index(drop=True)
    out.index += 1
    out.insert(0, "rank", out.index)
    return out, len(ran)


def _show(title, df, cols):
    print(f"\n{'=' * 78}\n{title}  (top {len(df)})\n{'=' * 78}")
    have = [c for c in cols if c in df.columns]
    with pd.option_context("display.max_columns", None, "display.width", 200,
                           "display.float_format", lambda x: f"{x:,.3f}"):
        print(df[have].to_string(index=False))


def _md_table(df, cols, floatfmt="{:,.3f}"):
    have = [c for c in cols if c in df.columns]
    lines = ["| " + " | ".join(have) + " |",
             "|" + "|".join("---" for _ in have) + "|"]
    for _, row in df.iterrows():
        cells = []
        for c in have:
            v = row[c]
            cells.append(floatfmt.format(v) if isinstance(v, float) else str(v))
        lines.append("| " + " | ".join(cells) + " |")
    return "\n".join(lines)


def write_summary(results, cons, n_ran, universe_size, top=10):
    ran = [r for r in results if r["full"] is not None]
    skipped = [r for r in results if r["skip"]]
    lines = [
        "# Russell 1000 — 20 fundamental screens, summary",
        "",
        f"*Data as of: {_as_of()}. Universe: {universe_size} companies "
        "(largest ~1000 US common stocks by market cap — Russell 1000 proxy).*",
        "",
        "Generated by `run_screens.py`. Each screen implements its literature",
        "formula exactly (no proxies); screens whose inputs are not yet in the",
        "committed data are listed under *Awaiting data refresh* and activate",
        "after `build_screen_inputs.py` runs with `FMP_KEY`.",
        "",
        "## Consensus top 10",
        "",
        f"Aggregated across the {n_ran} screens that ran: `top10_count` = how many",
        f"screen top-{top}s the name appears in; `avg_percentile` = its mean",
        "cross-sectional percentile over the screens where it was eligible",
        "(names eligible in fewer than half the screens are excluded).",
        "",
        _md_table(cons.head(top), ["rank", "ticker", "company", "sector",
                                   "top10_count", "avg_percentile", "screens"]),
        "",
        "Caveat: several screens share valuation inputs (PE, P/B, EV/EBIT, FCF",
        "yield), so the consensus tilts toward value; financials are excluded",
        "from the Magic Formula / Altman / Ohlson / Beneish screens and are",
        "therefore under-represented.",
        "",
        f"## Per-screen top {top}",
        "",
    ]
    for r in ran:
        head = r["full"].head(top)
        lines += [f"### {r['title']}",
                  "",
                  f"*{r['citation']}* — full results: `results/{r['outfile']}`",
                  "",
                  _md_table(head, ["ticker", "company", "sector", r["score"]]),
                  ""]
    if skipped:
        lines += ["## Awaiting data refresh", "",
                  "These exact-formula screens need `inputs/extended_input.csv`",
                  "(run `build_screen_inputs.py` with `FMP_KEY`, e.g. via the",
                  "`russell1000-screens` GitHub Actions workflow):", ""]
        lines += [f"- **{r['title']}** — {r['skip']}" for r in skipped]
        lines.append("")
    lines += ["---", "",
              f"*Regenerated {time.strftime('%Y-%m-%d')} by run_screens.py. "
              "Educational tool — not investment advice.*", ""]
    with open(os.path.join(RESULTS, "SUMMARY.md"), "w") as f:
        f.write("\n".join(lines))


def selftest():
    """Run every screen on the deterministic synthetic frame."""
    frame = synthetic_frame()
    checks = [
        ("buffett_quality", lambda: calculate_buffett_quality_score(frame, top=5)),
        ("magic_formula", lambda: calculate_magic_formula(frame, top=5)),
        ("piotroski", lambda: screen_piotroski_value(
            frame, max_price_to_book_quantile=1.0, min_f_score=0, top=5)),
    ]
    for key, title, runner, score, higher, citation, outfile in _registry()[3:]:
        checks.append((key, lambda r=runner: r(frame, None)))
    failures = []
    for key, fn in checks:
        try:
            out = fn()
            assert len(out) > 0 and "ticker" in out.columns
            print(f"  ok  {key:24s} rows={len(out):2d} best={out.iloc[0]['ticker']}")
        except Exception as e:  # noqa: BLE001 — report every failing screen
            failures.append((key, e))
            print(f" FAIL {key:24s} {e}")
    if failures:
        raise SystemExit(f"selftest: {len(failures)} screen(s) failed")
    print(f"selftest OK — {len(checks)} screens")


def main(argv=None):
    p = argparse.ArgumentParser(description="Run all 20 screens; report top N + consensus.")
    p.add_argument("--top", type=int, default=10)
    p.add_argument("--min-f-score", type=int, default=7)
    p.add_argument("--pb-quantile", type=float, default=0.4)
    p.add_argument("--only", help="comma-separated screen keys (debugging)")
    p.add_argument("--selftest", action="store_true")
    args = p.parse_args(argv)

    if args.selftest:
        selftest()
        return 0

    only = set(args.only.split(",")) if args.only else None
    results, joined = run(args.top, args.min_f_score, args.pb_quantile, only)

    for i, r in enumerate(results, 1):
        if r["skip"]:
            print(f"\n{i}. {r['title']}: SKIPPED — {r['skip']}")
            continue
        _show(f"{i}. {r['title']}", r["full"].head(args.top),
              ["ticker", "company", "sector", r["score"], "market_cap"])

    cons, n_ran = consensus(results, joined, args.top)
    if cons is not None:
        cons.head(args.top).to_csv(os.path.join(RESULTS, "consensus_top10.csv"), index=False)
        cons.to_csv(os.path.join(RESULTS, "consensus_all.csv"), index=False)
        _show(f"CONSENSUS across {n_ran} screens", cons.head(args.top),
              ["rank", "ticker", "company", "sector", "top10_count",
               "avg_percentile", "screens"])
        if not only:
            write_summary(results, cons, n_ran, universe_size=len(joined), top=args.top)
            print(f"\nWrote results/SUMMARY.md, consensus_top10.csv, consensus_all.csv "
                  f"and per-screen CSVs to {RESULTS}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
