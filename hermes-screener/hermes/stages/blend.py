"""Blend screen (momentum + golden cross) — a separate parallel output, not part of the funnel.

Runs the vendored blend_screen_decile10.py (default) or blend_screen_top5.py signal
code unmodified. Only the data source changes: the vendored scripts download Nasdaq
bars; here the bars come from the nightly Massive snapshot (split-adjusted OHLC,
SPY split-adjusted closes for beta). The universe is the largest `universe_size`
snapshot names by market cap, as in the original.
"""
from __future__ import annotations

import os

import pandas as pd

from .. import vendored

DECILE = {"decile10": 0.10, "top5": 0.05}


def _bars(g: pd.DataFrame) -> list[tuple]:
    f = (g["split_close"] / g["close"]).where(g["close"] > 0)
    out = []
    for d, o, h, lo, c, v, k in zip(g["date"], g["open"], g["high"], g["low"],
                                    g["split_close"], g["volume"], f):
        vals = (o * k, h * k, lo * k, c, v / k if k else float("nan"))
        if any(pd.isna(x) for x in vals) or min(vals[:4]) <= 0 or vals[4] < 0:
            continue
        hi_, lo_ = vals[1], vals[2]
        tol = max(1e-10, abs(c) * 1e-10)
        if hi_ + tol < max(vals[0], c, lo_) or lo_ - tol > min(vals[0], c, hi_):
            continue
        out.append((str(d), *vals))
    return out


def run_blend(cfg, snap_dir: str) -> dict:
    bc = cfg.strategy["blend"]
    mod = vendored.blend(bc["variant"])
    meta = pd.read_csv(os.path.join(snap_dir, "ta", "universe.csv"))
    meta["market_cap"] = pd.to_numeric(meta["market_cap"], errors="coerce")
    universe = (meta.dropna(subset=["market_cap"])
                .sort_values(["market_cap", "ticker"], ascending=[False, True])
                .head(int(bc["universe_size"]))["ticker"].tolist())
    prices = pd.read_csv(os.path.join(snap_dir, "ta", "prices.csv.gz"))
    prices = prices[prices["ticker"].isin(universe)].sort_values(["ticker", "date"])
    market = pd.read_csv(os.path.join(snap_dir, "market.csv.gz"))
    spy = market[market["symbol"] == mod.BENCHMARK]
    col = "split_close" if "split_close" in spy.columns else "adjclose"
    bench = dict(zip(spy["date"].astype(str), spy[col].astype(float)))
    if not bench:
        return {"status": "skipped", "reason": f"{mod.BENCHMARK} history missing", "ranked": []}

    latest, illiquid, no_cross = {}, 0, 0
    for sym, g in prices.groupby("ticker", sort=True):
        bars = _bars(g)
        if len(bars) < mod.WARMUP + 5:
            continue
        sig = mod.signals(bars, bench)
        if not sig:
            continue
        if sig[-1]["dollarVolume"] < float(bc["min_dollar_volume"]):
            illiquid += 1
            continue
        if sig[-1]["golden_cross"] is None:
            no_cross += 1
        latest[sym] = sig[-1]
    legs = mod.LEGS_BY_HOLD[int(bc["hold"])]
    ranked = mod.rank_composite(latest, legs)
    if not ranked:
        return {"status": "skipped", "reason": "fewer than 20 rankable names", "ranked": []}
    cutoff = max(1, int(len(ranked) * DECILE[bc["variant"]]))
    wts = mod.rank_weights(cutoff)
    asof = max(r["date"] for r in latest.values())
    rows = []
    for k, (sym, score) in enumerate(ranked, 1):
        r = latest[sym]
        rows.append({"rank": k, "ticker": sym, "score": round(score, 6),
                     "weight": round(wts[k - 1], 6) if k <= cutoff else 0.0,
                     "in_buy_list": k <= cutoff, "as_of": r["date"],
                     "imom252_21": r["imom252_21"], "ma_1_200": r["ma_1_200"],
                     "golden_cross": r["golden_cross"], "beta": r["beta"],
                     "close": r["close"], "dollar_volume_20d": r["dollarVolume"]})
    return {"status": "ran", "variant": bc["variant"], "hold": int(bc["hold"]),
            "legs": list(legs), "as_of": asof, "eligible": len(ranked), "cutoff": cutoff,
            "dropped_illiquid": illiquid, "not_in_golden_cross": no_cross,
            "stale": sum(1 for r in latest.values() if r["date"] != asof),
            "ranked": rows, "held_out_note": mod.HELD_OUT[int(bc["hold"])]}
