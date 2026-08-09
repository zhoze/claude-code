"""Portfolio stress testing against historical crisis analogs (spec §11)."""

from __future__ import annotations

import math

import numpy as np
import pandas as pd

from .scenarios import CRISIS_WINDOWS

STRESS_LABELS = {
    ("2008-09-01", "2009-03-31"): "GFC / recessionary + liquidity stress",
    ("2011-07-01", "2011-10-31"): "US downgrade / sovereign stress",
    ("2015-08-01", "2016-02-29"): "China deval / commodity shock",
    ("2018-10-01", "2018-12-31"): "rate-shock + growth sell-off",
    ("2020-02-15", "2020-04-30"): "COVID volatility spike",
    ("2022-01-01", "2022-10-31"): "inflation + rising-rate bear",
}


def portfolio_metrics(port_rets: pd.Series, rf_daily: float = 0.0) -> dict:
    r = port_rets.dropna()
    if len(r) < 20:
        return {}
    ann = math.sqrt(252)
    vol = float(r.std() * ann)
    downside = r[r < 0]
    cum = (1 + r).cumprod()
    dd = (cum / cum.cummax() - 1).min()
    var95 = float(r.quantile(0.05))
    tail = r[r <= var95]
    wins = r[r > 0]
    losses = r[r < 0]
    years = len(r) / 252
    return {
        "MAX_DRAWDOWN": float(dd),
        "VOLATILITY": vol,
        "VAR": var95,
        "CVAR": float(tail.mean()) if len(tail) else np.nan,
        "EXPECTED_SHORTFALL": float(tail.mean()) if len(tail) else np.nan,
        "SHARPE": float((r.mean() - rf_daily) / (r.std() + 1e-12) * ann),
        "SORTINO": float((r.mean() - rf_daily) / (downside.std() + 1e-12) * ann)
            if len(downside) else np.nan,
        "CALMAR": float((cum.iloc[-1] ** (1 / max(years, 1e-9)) - 1) / abs(dd))
            if dd < 0 else np.nan,
        "WIN_RATE": float((r > 0).mean()),
        "PROFIT_FACTOR": float(wins.sum() / abs(losses.sum())) if len(losses) and losses.sum() != 0 else np.nan,
        "TOTAL_RETURN": float(cum.iloc[-1] - 1),
        "CAGR": float(cum.iloc[-1] ** (1 / max(years, 1e-9)) - 1),
    }


def stress_test(weights: pd.Series, prices: dict[str, pd.DataFrame]) -> pd.DataFrame:
    """Apply today's weights to each historical crisis window. Names without
    history in a window are reported as reduced coverage, not backfilled."""
    rows = []
    rets = pd.DataFrame({t: prices[t]["close"].pct_change()
                         for t in weights.index if t in prices and len(prices[t])})
    for window in CRISIS_WINDOWS:
        lo, hi = window
        sub = rets.loc[lo:hi]
        if sub.empty:
            continue
        available = [t for t in weights.index if t in sub.columns and sub[t].notna().mean() > 0.7]
        if not available:
            continue
        w = weights[available]
        w = w / w.sum() if w.sum() > 0 else w
        port = (sub[available] * w).sum(axis=1)
        m = portfolio_metrics(port)
        rows.append({"scenario": STRESS_LABELS.get(window, f"{lo}..{hi}"),
                     "window": f"{lo}..{hi}", "coverage": len(available) / len(weights),
                     "total_return": m.get("TOTAL_RETURN"), "max_drawdown": m.get("MAX_DRAWDOWN"),
                     "cvar": m.get("CVAR"), "volatility": m.get("VOLATILITY")})
    return pd.DataFrame(rows)
