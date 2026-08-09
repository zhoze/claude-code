"""Feature matrix construction (spec §6): fundamental, price, volume, macro and
regime features, computed strictly from data available at each row's date.

Rows are (date, forward-return targets) per ticker; every feature uses only
prices/macro up to and including that date, and fundamentals published by it.
"""

from __future__ import annotations

import math

import numpy as np
import pandas as pd

PRICE_FEATURES = [
    "mom_21", "mom_63", "mom_126", "mom_252", "vol_21", "vol_63", "drawdown_63",
    "trend_50_200", "gap_1d", "rel_strength_63", "rsi_14", "dist_52w_high",
]
VOLUME_FEATURES = ["rel_volume_5", "obv_slope_21", "volume_trend_21", "pv_divergence"]
MACRO_FEATURES = ["vix_level", "vix_chg_5", "ust10y_chg_21", "curve_2s10s",
                  "dxy_chg_21", "wti_chg_21", "spx_mom_63", "sector_mom_63"]


def _rsi(close: pd.Series, period: int = 14) -> pd.Series:
    delta = close.diff()
    gain = delta.clip(lower=0).ewm(alpha=1 / period, min_periods=period).mean()
    loss = (-delta.clip(upper=0)).ewm(alpha=1 / period, min_periods=period).mean()
    rs = gain / loss.replace(0, np.nan)
    return 100 - 100 / (1 + rs)


def price_feature_frame(px: pd.DataFrame, bench_close: pd.Series | None = None,
                        sector_close: pd.Series | None = None,
                        macro: dict[str, pd.Series] | None = None) -> pd.DataFrame:
    """All features are as-of each date (shift-free constructions on past windows)."""
    c, v = px["close"], px["volume"]
    f = pd.DataFrame(index=px.index)
    ret = c.pct_change()

    f["mom_21"] = c.pct_change(21)
    f["mom_63"] = c.pct_change(63)
    f["mom_126"] = c.pct_change(126)
    f["mom_252"] = c.pct_change(252)
    f["vol_21"] = ret.rolling(21).std() * math.sqrt(252)
    f["vol_63"] = ret.rolling(63).std() * math.sqrt(252)
    roll_max = c.rolling(63, min_periods=21).max()
    f["drawdown_63"] = c / roll_max - 1
    sma50, sma200 = c.rolling(50).mean(), c.rolling(200).mean()
    f["trend_50_200"] = sma50 / sma200 - 1
    f["gap_1d"] = px["open"] / c.shift(1) - 1
    f["rsi_14"] = _rsi(c)
    f["dist_52w_high"] = c / c.rolling(252, min_periods=126).max() - 1

    if bench_close is not None:
        b = bench_close.reindex(px.index).ffill()
        f["rel_strength_63"] = c.pct_change(63) - b.pct_change(63)
    else:
        f["rel_strength_63"] = np.nan

    f["rel_volume_5"] = v.rolling(5).mean() / v.rolling(63).mean()
    obv = (np.sign(ret).fillna(0) * v).cumsum()
    f["obv_slope_21"] = obv.diff(21) / v.rolling(63).mean().replace(0, np.nan)
    f["volume_trend_21"] = v.rolling(21).mean() / v.rolling(63).mean()
    f["pv_divergence"] = np.sign(c.pct_change(10)) * -np.sign(f["obv_slope_21"])

    if macro:
        def ali(name):
            s = macro.get(name)
            return s.reindex(px.index).ffill() if s is not None else pd.Series(np.nan, index=px.index)
        vix = ali("vix")
        f["vix_level"] = vix
        f["vix_chg_5"] = vix.pct_change(5)
        f["ust10y_chg_21"] = ali("ust10y").diff(21)
        f["curve_2s10s"] = ali("ust10y") - ali("ust2y")
        f["dxy_chg_21"] = ali("dxy").pct_change(21)
        f["wti_chg_21"] = ali("wti").pct_change(21)
        f["spx_mom_63"] = ali("sp500").pct_change(63)
        if sector_close is not None:
            f["sector_mom_63"] = sector_close.reindex(px.index).ffill().pct_change(63)
        else:
            f["sector_mom_63"] = np.nan
    return f


def build_training_frame(px: pd.DataFrame, horizons: list[int],
                         bench_close: pd.Series | None = None,
                         sector_close: pd.Series | None = None,
                         macro: dict[str, pd.Series] | None = None,
                         fundamental_scores: pd.Series | None = None) -> pd.DataFrame:
    """Features + forward-return targets. Target columns use FUTURE data by design —
    they exist only for supervised training and are strictly separated from
    features by the walk-forward splitter."""
    f = price_feature_frame(px, bench_close, sector_close, macro)
    c = px["close"]
    for h in horizons:
        f[f"fwd_ret_{h}"] = c.shift(-h) / c - 1
        f[f"fwd_pos_{h}"] = (f[f"fwd_ret_{h}"] > 0).astype(float)
    if fundamental_scores is not None:
        f["fundamental_composite"] = fundamental_scores.reindex(f.index).ffill()
    return f


def feature_columns(df: pd.DataFrame) -> list[str]:
    return [c for c in df.columns if not c.startswith("fwd_")]
