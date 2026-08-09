"""Future outcome tracking (spec §32): score matured predictions at
1/5/10/20/60 trading days — return, MAE, MFE, volatility, drawdown,
benchmark-relative return.
"""

from __future__ import annotations

import datetime as dt
import json
import logging
import math

import numpy as np
import pandas as pd

from ..calendar_utils import next_trading_days
from ..data.prices import PriceLibrary
from ..data.store import Store

log = logging.getLogger(__name__)


def _window_metrics(px: pd.DataFrame, start: dt.date, horizon_days: int,
                    bench: pd.DataFrame | None) -> dict | None:
    fut = px[px.index.date > start].head(horizon_days)
    base_rows = px[px.index.date <= start]
    if base_rows.empty or len(fut) < horizon_days:
        return None  # not matured yet or data gap — do not fabricate
    base = float(base_rows["close"].iloc[-1])
    closes = fut["close"]
    ret = float(closes.iloc[-1] / base - 1)
    path = closes / base - 1
    metrics = {
        "return": ret,
        "mae": float(fut["low"].min() / base - 1),
        "mfe": float(fut["high"].max() / base - 1),
        "vol": float(closes.pct_change().std() * math.sqrt(252)) if horizon_days > 2 else np.nan,
        "drawdown": float((path - path.cummax()).min()),
        "bench_rel_ret": np.nan,
    }
    if bench is not None and len(bench):
        b_base_rows = bench[bench.index.date <= start]
        b_fut = bench[bench.index.date > start].head(horizon_days)
        if len(b_base_rows) and len(b_fut) >= horizon_days:
            b_ret = float(b_fut["close"].iloc[-1] / b_base_rows["close"].iloc[-1] - 1)
            metrics["bench_rel_ret"] = ret - b_ret
    return metrics


def score_matured_predictions(store: Store, price_lib: PriceLibrary, cfg,
                              as_of: dt.date) -> int:
    """Fill outcomes for all predictions whose horizon has fully elapsed."""
    bench = price_lib.get("IWM", as_of)
    scored = 0
    for horizon in cfg.learning.outcome_horizons_days:
        pending = store.unscored_predictions(horizon, as_of)
        for _, row in pending.iterrows():
            run_date = dt.date.fromisoformat(row["run_date"])
            maturity = next_trading_days(run_date, horizon)[-1]
            if maturity > as_of:
                continue
            px = price_lib.get(row["ticker"], as_of)
            if px is None or px.empty:
                continue
            m = _window_metrics(px, run_date, horizon, bench)
            if m is None:
                continue
            store.save_outcome(int(row["prediction_id"]), horizon, m)
            scored += 1
    if scored:
        log.info("scored %d prediction outcomes", scored)
    return scored


def system_performance(store: Store, windows=(20, 50, 100)) -> dict:
    """Rolling system stats for the report (spec §45)."""
    hist = store.prediction_history()
    sel = hist[(hist["selected"] == 1) & hist["ret"].notna()]
    out: dict = {}
    for label, sub in {"all": sel, **{f"last_{n}": sel.head(n * 5) for n in windows}}.items():
        by5 = sub[sub["horizon_days"] == 5]
        if len(by5) < 3:
            continue
        r = by5["ret"]
        rel = by5["bench_rel_ret"].dropna()
        out[label] = {
            "n": int(len(by5)),
            "WIN_RATE": float((r > 0).mean()),
            "AVERAGE_RETURN": float(r.mean()),
            "MEDIAN_RETURN": float(r.median()),
            "ALPHA_VS_IWM": float(rel.mean()) if len(rel) else None,
            "SHARPE": float(r.mean() / (r.std() + 1e-12) * math.sqrt(252 / 5)),
            "MAX_DRAWDOWN": float(((1 + r).cumprod() / (1 + r).cumprod().cummax() - 1).min()),
            "CVAR_95": float(r[r <= r.quantile(0.05)].mean()) if len(r) >= 20 else None,
        }
    return out
