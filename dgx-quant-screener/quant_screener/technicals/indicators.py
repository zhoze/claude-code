"""Vectorized technical indicators shared by the 10 strategy families."""

from __future__ import annotations

import numpy as np
import pandas as pd


def sma(s: pd.Series, n: int) -> pd.Series:
    return s.rolling(n).mean()


def ema(s: pd.Series, n: int) -> pd.Series:
    return s.ewm(span=n, adjust=False).mean()


def rsi(close: pd.Series, period: int = 14) -> pd.Series:
    delta = close.diff()
    gain = delta.clip(lower=0).ewm(alpha=1 / period, min_periods=period).mean()
    loss = (-delta.clip(upper=0)).ewm(alpha=1 / period, min_periods=period).mean()
    rs = gain / loss.replace(0, np.nan)
    return 100 - 100 / (1 + rs)


def macd(close: pd.Series, fast: int = 12, slow: int = 26, signal: int = 9):
    line = ema(close, fast) - ema(close, slow)
    sig = ema(line, signal)
    return line, sig, line - sig


def true_range(px: pd.DataFrame) -> pd.Series:
    prev_close = px["close"].shift(1)
    return pd.concat([px["high"] - px["low"],
                      (px["high"] - prev_close).abs(),
                      (px["low"] - prev_close).abs()], axis=1).max(axis=1)


def atr(px: pd.DataFrame, period: int = 14) -> pd.Series:
    return true_range(px).ewm(alpha=1 / period, min_periods=period).mean()


def adx(px: pd.DataFrame, period: int = 14):
    up = px["high"].diff()
    dn = -px["low"].diff()
    plus_dm = ((up > dn) & (up > 0)) * up
    minus_dm = ((dn > up) & (dn > 0)) * dn
    tr = true_range(px).ewm(alpha=1 / period, min_periods=period).mean()
    plus_di = 100 * plus_dm.ewm(alpha=1 / period, min_periods=period).mean() / tr
    minus_di = 100 * minus_dm.ewm(alpha=1 / period, min_periods=period).mean() / tr
    dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, np.nan)
    return dx.ewm(alpha=1 / period, min_periods=period).mean(), plus_di, minus_di


def bollinger(close: pd.Series, n: int = 20, k: float = 2.0):
    mid = sma(close, n)
    sd = close.rolling(n).std()
    upper, lower = mid + k * sd, mid - k * sd
    bandwidth = (upper - lower) / mid
    pct_b = (close - lower) / (upper - lower).replace(0, np.nan)
    return mid, upper, lower, bandwidth, pct_b


def obv(px: pd.DataFrame) -> pd.Series:
    return (np.sign(px["close"].diff()).fillna(0) * px["volume"]).cumsum()


def donchian(px: pd.DataFrame, n: int):
    return px["high"].rolling(n).max(), px["low"].rolling(n).min()
