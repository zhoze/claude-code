"""Quantitative market-regime detection (spec §16, §35).

Classification uses measurable evidence only: VIX level & term structure,
index trend, breadth proxy, credit proxy, rate impulse. Output feeds the macro
score, the CVaR expected-return tilt, and regime-specific learning keys.
"""

from __future__ import annotations

import datetime as dt
import math
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

REGIMES = ("RISK_ON", "NEUTRAL", "RISK_OFF", "HIGH_STRESS")


@dataclass
class RegimeAssessment:
    regime: str = "NEUTRAL"
    score: float = 0.0                      # -100 (stress) .. +100 (risk-on)
    components: dict = field(default_factory=dict)
    sub_regimes: dict = field(default_factory=dict)   # vol/rate/trend/credit labels
    tilt: float = 0.0                       # -1..+1 expected-return tilt for CVaR blend
    notes: list[str] = field(default_factory=list)


def detect_regime(macro_series: dict[str, pd.Series], as_of: dt.date) -> RegimeAssessment:
    a = RegimeAssessment()
    comp = a.components

    def last(name, default=math.nan):
        s = macro_series.get(name)
        if s is None or not len(s):
            return default
        return float(s.iloc[-1])

    def chg(name, n, default=math.nan):
        s = macro_series.get(name)
        if s is None or len(s) <= n:
            return default
        return float(s.iloc[-1] / s.iloc[-1 - n] - 1)

    pts = 0.0
    weight_used = 0.0

    vix = last("vix")
    if math.isfinite(vix):
        comp["vix"] = vix
        pts += np.interp(vix, [12, 18, 25, 35], [25, 10, -15, -35]); weight_used += 1
        a.sub_regimes["volatility"] = "HIGH_VOL" if vix > 22 else "LOW_VOL"

    vix3m = last("vix3m")
    if math.isfinite(vix) and math.isfinite(vix3m) and vix3m > 0:
        ratio = vix / vix3m
        comp["vix_term_ratio"] = ratio
        pts += 15 if ratio < 0.9 else -25 if ratio > 1.0 else 0; weight_used += 1

    spx = macro_series.get("sp500")
    if spx is not None and len(spx) > 200:
        above200 = float(spx.iloc[-1]) > float(spx.rolling(200).mean().iloc[-1])
        mom63 = chg("sp500", 63)
        comp["spx_above_200dma"] = above200
        comp["spx_mom_63"] = mom63
        pts += (15 if above200 else -15); weight_used += 1
        if math.isfinite(mom63):
            pts += float(np.clip(mom63 * 200, -15, 15)); weight_used += 1
        a.sub_regimes["trend"] = "BULL" if above200 and (mom63 or 0) > 0 else \
            "BEAR" if not above200 and (mom63 or 0) < -0.05 else "MIXED"

    # small-cap breadth proxy: Russell 2000 vs S&P relative 21d
    r2k, spx_c = chg("russell2000", 21), chg("sp500", 21)
    if math.isfinite(r2k) and math.isfinite(spx_c):
        comp["breadth_r2k_vs_spx_21d"] = r2k - spx_c
        pts += float(np.clip((r2k - spx_c) * 300, -10, 10)); weight_used += 1

    # credit proxy: HYG vs LQD 21d
    hyg, lqd = chg("hyg", 21), chg("lqd", 21)
    if math.isfinite(hyg) and math.isfinite(lqd):
        comp["credit_hy_vs_ig_21d"] = hyg - lqd
        pts += float(np.clip((hyg - lqd) * 400, -15, 15)); weight_used += 1
        a.sub_regimes["credit"] = "STRESS" if hyg - lqd < -0.02 else "NORMAL"

    # rate impulse: 10y yield change over 21d (yield indexes are in %*10 for ^TNX)
    tnx = macro_series.get("ust10y")
    if tnx is not None and len(tnx) > 21:
        d21 = float(tnx.iloc[-1] - tnx.iloc[-22])
        comp["ust10y_chg_21d"] = d21
        pts += float(np.clip(-abs(d21) * 10 + 3, -10, 3)); weight_used += 1
        a.sub_regimes["rates"] = "RISING" if d21 > 0.1 else "FALLING" if d21 < -0.1 else "STABLE"

    a.score = float(np.clip(pts, -100, 100))
    if weight_used < 3:
        a.notes.append("regime evidence thin — defaulting toward NEUTRAL")
        a.score *= 0.5

    stress = (math.isfinite(vix) and vix > 30) or \
             (comp.get("vix_term_ratio", 0) > 1.05) or \
             (comp.get("credit_hy_vs_ig_21d", 0) < -0.03)
    if stress:
        a.regime = "HIGH_STRESS"
    elif a.score >= 20:
        a.regime = "RISK_ON"
    elif a.score <= -20:
        a.regime = "RISK_OFF"
    else:
        a.regime = "NEUTRAL"
    a.tilt = {"RISK_ON": 0.5, "NEUTRAL": 0.0, "RISK_OFF": -0.5, "HIGH_STRESS": -1.0}[a.regime]
    return a
