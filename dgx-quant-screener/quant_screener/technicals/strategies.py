"""The 10 technical strategy families (spec §12).

Each strategy maps (px, context) -> desired position Series in {0, 1}
computed from information available at each bar's close; the backtester
executes at the NEXT bar's open (delayed execution, spec §13).

`context` may carry benchmark/sector closes for relative-strength.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from .indicators import adx, atr, bollinger, donchian, ema, macd, obv, rsi, sma


def strat_long_term_trend(px: pd.DataFrame, ctx: dict) -> pd.Series:
    c = px["close"]
    s20, s50, s100, s200 = sma(c, 20), sma(c, 50), sma(c, 100), sma(c, 200)
    aligned = (s20 > s50) & (s50 > s100) & (s100 > s200)
    slope_ok = s200.diff(21) > 0
    return ((c > s50) & aligned & slope_ok).astype(float)


def strat_ema_momentum(px: pd.DataFrame, ctx: dict) -> pd.Series:
    c = px["close"]
    e9, e21, e50 = ema(c, 9), ema(c, 21), ema(c, 50)
    trend = (e21 > e50)
    entry = (e9 > e21) & trend
    return entry.astype(float)


def strat_rsi(px: pd.DataFrame, ctx: dict) -> pd.Series:
    """Trend-adjusted RSI: buy oversold pullbacks inside an uptrend; never short
    merely because RSI > 70 (spec §12.3)."""
    c = px["close"]
    r = rsi(c, 14)
    uptrend = c > sma(c, 100)
    entry = (r < 35) & uptrend
    exit_ = (r > 65) | ~uptrend
    pos = pd.Series(np.nan, index=c.index)
    pos[entry] = 1.0
    pos[exit_] = 0.0
    return pos.ffill().fillna(0.0)


def strat_macd(px: pd.DataFrame, ctx: dict) -> pd.Series:
    c = px["close"]
    line, sig, hist = macd(c)
    return ((line > sig) & (line > 0) & (hist.diff() > 0)).astype(float)


def strat_adx(px: pd.DataFrame, ctx: dict) -> pd.Series:
    a, plus_di, minus_di = adx(px)
    trending = a > 20
    return (trending & (plus_di > minus_di)).astype(float)


def strat_bollinger(px: pd.DataFrame, ctx: dict) -> pd.Series:
    """Squeeze-then-breakout: enter on close above upper band after compression."""
    c = px["close"]
    mid, upper, lower, bw, pct_b = bollinger(c)
    squeeze = bw < bw.rolling(126, min_periods=63).quantile(0.25)
    breakout = (c > upper) & squeeze.shift(1).rolling(5).max().astype(bool)
    exit_ = c < mid
    pos = pd.Series(np.nan, index=c.index)
    pos[breakout] = 1.0
    pos[exit_] = 0.0
    return pos.ffill().fillna(0.0)


def strat_donchian(px: pd.DataFrame, ctx: dict) -> pd.Series:
    c = px["close"]
    hi20, _ = donchian(px, 20)
    _, lo10 = donchian(px, 10)
    entry = c >= hi20.shift(1)               # 20-day breakout, confirmed at close
    exit_ = c <= lo10.shift(1)
    pos = pd.Series(np.nan, index=c.index)
    pos[entry] = 1.0
    pos[exit_] = 0.0
    return pos.ffill().fillna(0.0)


def strat_atr_breakout(px: pd.DataFrame, ctx: dict) -> pd.Series:
    c = px["close"]
    a = atr(px, 14)
    base = sma(c, 20)
    expansion = a > a.rolling(126, min_periods=63).quantile(0.6)
    entry = (c > base + 1.5 * a) & expansion
    exit_ = c < base - 1.0 * a
    pos = pd.Series(np.nan, index=c.index)
    pos[entry] = 1.0
    pos[exit_] = 0.0
    return pos.ffill().fillna(0.0)


def strat_volume_confirmation(px: pd.DataFrame, ctx: dict) -> pd.Series:
    """Price strength only counts when accompanied by accumulation (spec §12.9)."""
    c, v = px["close"], px["volume"]
    rel_vol = v / v.rolling(63).mean()
    ob = obv(px)
    obv_up = ob.diff(21) > 0
    price_up = c > sma(c, 50)
    breakout = (c >= c.rolling(50).max().shift(1)) & (rel_vol > 1.5)
    pos = pd.Series(np.nan, index=c.index)
    pos[breakout & obv_up] = 1.0
    pos[~price_up | ~obv_up] = 0.0
    return pos.ffill().fillna(0.0)


def strat_relative_strength(px: pd.DataFrame, ctx: dict) -> pd.Series:
    c = px["close"]
    bench = ctx.get("bench_close")
    if bench is None:
        return pd.Series(0.0, index=c.index)
    b = bench.reindex(c.index).ffill()
    rs_scores = []
    for n in (21, 63, 126, 252):
        rs_scores.append((c.pct_change(n) - b.pct_change(n)) > 0)
    persistent = sum(s.astype(int) for s in rs_scores) >= 3
    return (persistent & (c > sma(c, 50))).astype(float)


STRATEGY_REGISTRY = {
    "long_term_trend": strat_long_term_trend,
    "ema_momentum": strat_ema_momentum,
    "rsi_pullback": strat_rsi,
    "macd": strat_macd,
    "adx_directional": strat_adx,
    "bollinger_squeeze": strat_bollinger,
    "donchian_breakout": strat_donchian,
    "atr_breakout": strat_atr_breakout,
    "volume_confirmation": strat_volume_confirmation,
    "relative_strength": strat_relative_strength,
}
