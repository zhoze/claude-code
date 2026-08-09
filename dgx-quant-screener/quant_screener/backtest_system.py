"""Full-pipeline walk-forward backtest (spec §29-30).

Replays the complete decision pipeline (universe -> screens -> overlap -> ML ->
Mean-CVaR -> technicals -> selection) at historical decision dates using only
point-in-time data, then measures forward returns vs Russell/IWM/SPY and an
equal-weight portfolio.

NOTE: without a point-in-time membership CSV and publication-dated fundamentals
the replay inherits survivorship/restatement bias from the fallback data
sources; results are stamped with those warnings rather than presented clean.
"""

from __future__ import annotations

import datetime as dt
import logging

import numpy as np
import pandas as pd

from .config import Config
from .pipeline import run_daily
from .portfolio.stress import portfolio_metrics

log = logging.getLogger(__name__)

FORWARD_HORIZONS = (1, 5, 10, 20)


def run_system_backtest(cfg: Config, start: dt.date, end: dt.date,
                        step_days: int = 21, max_universe: int | None = 300) -> dict:
    """Monthly decision dates between start and end. Each decision reuses the
    live pipeline in as-of mode; forward returns are read from the price cache
    afterwards (allowed: those dates are in the past relative to *us*, but the
    pipeline itself only saw data up to each decision date)."""
    from .data.prices import PriceLibrary
    from .data.providers import build_providers
    from .data.store import Store
    from .config import fmp_api_key

    decision_dates = pd.date_range(start, end, freq=f"{step_days}D").date
    picks: list[dict] = []
    for d in decision_dates:
        try:
            result = run_daily(cfg, as_of=d, dry_run=True, max_universe=max_universe)
        except Exception as e:
            log.warning("backtest run %s failed: %s", d, e)
            continue
        for t in result.get("selections", []):
            picks.append({"date": d, "ticker": t})
        if not result.get("selections"):
            picks.append({"date": d, "ticker": None})

    store = Store(cfg["storage_root"])
    provider, _ = build_providers(cfg, fmp_api_key(cfg))
    lib = PriceLibrary(provider, store, cfg.data.price_cache_days)
    today = dt.date.today()

    rows = []
    for p in picks:
        if p["ticker"] is None:
            rows.append({**p, **{f"fwd_{h}d": 0.0 for h in FORWARD_HORIZONS},
                         "in_market": False})
            continue
        px = lib.get(p["ticker"], today)
        if px is None or px.empty:
            continue
        base_rows = px[px.index.date <= p["date"]]
        if base_rows.empty:
            continue
        base = float(base_rows["close"].iloc[-1])
        fut = px[px.index.date > p["date"]]
        row = {**p, "in_market": True}
        for h in FORWARD_HORIZONS:
            row[f"fwd_{h}d"] = float(fut["close"].iloc[h - 1] / base - 1) if len(fut) >= h else np.nan
        rows.append(row)
    df = pd.DataFrame(rows)

    benches = {}
    for name, sym in (("IWM", "IWM"), ("SPY", "SPY")):
        bx = lib.get(sym, today)
        benches[name] = bx["close"] if len(bx) else None

    summary: dict = {"n_decisions": len(decision_dates),
                     "n_picks": int(df["in_market"].sum()) if len(df) else 0,
                     "abstention_rate": float((~df["in_market"]).mean()) if len(df) else None}
    if len(df) and df["in_market"].any():
        traded = df[df["in_market"]]
        for h in FORWARD_HORIZONS:
            r = traded[f"fwd_{h}d"].dropna()
            if not len(r):
                continue
            entry = {"mean": float(r.mean()), "median": float(r.median()),
                     "win_rate": float((r > 0).mean()), "n": int(len(r))}
            for name, close in benches.items():
                if close is None:
                    continue
                rels = []
                for _, row in traded.iterrows():
                    b0 = close[close.index.date <= row["date"]]
                    bf = close[close.index.date > row["date"]]
                    if len(b0) and len(bf) >= h and np.isfinite(row[f"fwd_{h}d"]):
                        rels.append(row[f"fwd_{h}d"] - (float(bf.iloc[h - 1] / b0.iloc[-1]) - 1))
                if rels:
                    entry[f"alpha_vs_{name}"] = float(np.mean(rels))
            summary[f"forward_{h}d"] = entry
        # simple equal-weight daily curve at 5d holding for portfolio metrics
        r5 = traded["fwd_5d"].dropna() / 5
        summary["portfolio_metrics_5d_scaled"] = portfolio_metrics(
            pd.Series(r5.values, index=pd.RangeIndex(len(r5))))
    summary["warnings"] = [
        "survivorship bias present unless universe.membership_csv supplied",
        "fundamental publication lag approximated unless FMP filing dates available",
    ]
    store.close()
    return summary
