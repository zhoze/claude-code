"""Per-finalist technical evaluation (spec §12-15):

1. backtest all 10 strategies on the stock's own history,
2. pick BEST_TECHNICAL_MODEL by out-of-sample robustness,
3. check whether that model has a CURRENTLY valid signal,
4. produce entry zone / invalidation / support / resistance from live ATR.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from .backtest import BacktestResult, robustness_score, run_backtest
from .indicators import atr, sma
from .strategies import STRATEGY_REGISTRY

SIGNAL_CLASSES = ("STRONG_BUY", "BUY", "WATCH", "NEUTRAL", "AVOID")


@dataclass
class TechnicalAssessment:
    ticker: str
    best_strategy: str | None = None
    robustness: float = 0.0
    signal: str = "NEUTRAL"
    signal_age_days: int | None = None
    levels: dict = field(default_factory=dict)
    per_strategy: dict[str, dict] = field(default_factory=dict)
    technical_score: float = 0.0           # 0-100 blend of setup + breadth of agreement
    notes: list[str] = field(default_factory=list)


def evaluate_finalist(ticker: str, px: pd.DataFrame, cfg,
                      bench_close: pd.Series | None = None,
                      sector_close: pd.Series | None = None) -> TechnicalAssessment:
    a = TechnicalAssessment(ticker=ticker)
    if px is None or len(px) < 300:
        a.notes.append("insufficient history for technical evaluation")
        return a
    px = px.tail(cfg.technicals.backtest_days)
    ctx = {"bench_close": bench_close, "sector_close": sector_close}

    results: dict[str, BacktestResult] = {}
    scores: dict[str, float] = {}
    active_now: dict[str, bool] = {}
    for name, fn in STRATEGY_REGISTRY.items():
        try:
            pos = fn(px, ctx)
        except Exception as e:
            a.notes.append(f"{name}: strategy error {e}")
            continue
        res = run_backtest(px, pos, name, cfg.technicals.oos_fraction,
                           cfg.technicals.commission_bps, cfg.technicals.slippage_bps)
        score, reasons = robustness_score(res, cfg.technicals.min_trades)
        results[name] = res
        scores[name] = score
        active_now[name] = bool(pos.iloc[-1] > 0)
        oos = res.metrics_oos.get("PERIOD_METRICS", {})
        a.per_strategy[name] = {
            "robustness": score, "active_now": active_now[name],
            "oos_sharpe": oos.get("SHARPE"), "oos_total_return": oos.get("TOTAL_RETURN"),
            "oos_max_dd": oos.get("MAX_DRAWDOWN"), "oos_profit_factor": oos.get("PROFIT_FACTOR"),
            "oos_cvar": oos.get("CVAR"), "n_trades_oos": res.n_trades_oos,
            "avg_trade_oos": res.avg_trade_oos, "reject_reasons": reasons,
        }

    if not scores:
        return a
    a.best_strategy = max(scores, key=scores.get)
    a.robustness = scores[a.best_strategy]

    # -------- current signal classification (spec §15): the historically best
    # model must be producing a signal NOW; history alone is not a recommendation
    best_active = active_now.get(a.best_strategy, False)
    best_pos = STRATEGY_REGISTRY[a.best_strategy](px, ctx)
    if best_active:
        flips = best_pos.diff().fillna(0)
        recent_entries = flips[flips > 0]
        a.signal_age_days = int((len(px) - 1) - px.index.get_loc(recent_entries.index[-1])) \
            if len(recent_entries) else None
        agreeing = sum(1 for n, act in active_now.items() if act and scores.get(n, 0) >= 40)
        fresh = a.signal_age_days is not None and a.signal_age_days <= 10
        if a.robustness >= 60 and agreeing >= 3 and fresh:
            a.signal = "STRONG_BUY"
        elif a.robustness >= 45 and fresh:
            a.signal = "BUY"
        else:
            a.signal = "WATCH"
    else:
        # nothing valid now — WATCH if any decent strategy is close, else NEUTRAL
        near = any(act and scores.get(n, 0) >= 45 for n, act in active_now.items())
        a.signal = "WATCH" if near else "NEUTRAL"
    dd_now = px["close"].iloc[-1] / px["close"].rolling(63).max().iloc[-1] - 1
    if dd_now < -0.25:
        a.signal = "AVOID"
        a.notes.append("deep recent drawdown — setup rejected")

    # -------- levels (spec §15, §42)
    c = px["close"]
    a_tr = atr(px, cfg.technicals.atr_period)
    last, last_atr = float(c.iloc[-1]), float(a_tr.iloc[-1])
    support = float(px["low"].tail(40).min())
    resistance = float(px["high"].tail(40).max())
    a.levels = {
        "price": last, "ATR": last_atr,
        "entry_zone": [round(last - 0.5 * last_atr, 2), round(last + 0.5 * last_atr, 2)],
        "invalidation": round(max(support, last - 2.0 * last_atr), 2),
        "support": round(support, 2), "resistance": round(resistance, 2),
        "sma50": round(float(sma(c, 50).iloc[-1]), 2),
        "sma200": round(float(sma(c, 200).iloc[-1]), 2) if len(c) >= 200 else None,
        "expected_range_5d": [round(last - last_atr * np.sqrt(5), 2),
                              round(last + last_atr * np.sqrt(5), 2)],
        "expected_range_20d": [round(last - last_atr * np.sqrt(20), 2),
                               round(last + last_atr * np.sqrt(20), 2)],
        "expected_volatility_ann": round(float(c.pct_change().tail(63).std() * np.sqrt(252)), 4),
    }

    # -------- 0-100 technical score: current setup quality + agreement breadth
    sig_pts = {"STRONG_BUY": 90, "BUY": 70, "WATCH": 45, "NEUTRAL": 30, "AVOID": 0}[a.signal]
    breadth = np.mean([1.0 if act else 0.0 for act in active_now.values()])
    a.technical_score = float(np.clip(0.7 * sig_pts + 0.3 * breadth * 100, 0, 100))
    return a
