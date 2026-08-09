"""Per-stock macro sensitivity via multivariate regression (spec §28).

RETURN(stock) = b1*Russell + b2*Sector + b3*dVIX + b4*dRates + b5*Oil + b6*USD + eps

Betas are estimated on daily data (2y window); combined with the CURRENT macro
impulses to classify the environment as POSITIVE / NEUTRAL / NEGATIVE for the
candidate.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np
import pandas as pd


@dataclass
class MacroSensitivity:
    ticker: str
    betas: dict[str, float] = field(default_factory=dict)
    r_squared: float = np.nan
    current_impulse: dict[str, float] = field(default_factory=dict)
    expected_macro_drag: float = np.nan      # expected daily return from current impulses
    assessment: str = "NEUTRAL"              # POSITIVE | NEUTRAL | NEGATIVE
    macro_score: float = 50.0                # 0-100 for the composite
    notes: list[str] = field(default_factory=list)


FACTORS = ["russell", "sector", "dvix", "drates", "oil", "usd"]


def estimate_sensitivity(ticker: str, px: pd.DataFrame,
                         macro_series: dict[str, pd.Series],
                         sector_close: pd.Series | None,
                         window: int = 504) -> MacroSensitivity:
    ms = MacroSensitivity(ticker=ticker)
    r = px["close"].pct_change()

    def rets(name):
        s = macro_series.get(name)
        return s.reindex(px.index).ffill().pct_change() if s is not None else None

    rus = rets("russell2000") if "russell2000" in macro_series else rets("sp500")
    sec = sector_close.reindex(px.index).ffill().pct_change() if sector_close is not None else None
    vix = macro_series.get("vix")
    dvix = vix.reindex(px.index).ffill().diff() / 10 if vix is not None else None
    tnx = macro_series.get("ust10y")
    drates = tnx.reindex(px.index).ffill().diff() if tnx is not None else None
    oil = rets("wti")
    usd = rets("dxy")

    X_parts = {"russell": rus, "sector": sec, "dvix": dvix,
               "drates": drates, "oil": oil, "usd": usd}
    X_parts = {k: v for k, v in X_parts.items() if v is not None}
    if len(X_parts) < 3:
        ms.notes.append("insufficient macro series for regression")
        return ms
    df = pd.DataFrame({"y": r, **X_parts}).dropna().tail(window)
    if len(df) < 120:
        ms.notes.append("insufficient overlapping history")
        return ms
    X = df[list(X_parts)].to_numpy()
    y = df["y"].to_numpy()
    X1 = np.column_stack([np.ones(len(X)), X])
    beta, *_ = np.linalg.lstsq(X1, y, rcond=None)
    resid = y - X1 @ beta
    ss_res, ss_tot = float((resid ** 2).sum()), float(((y - y.mean()) ** 2).sum())
    ms.r_squared = 1 - ss_res / ss_tot if ss_tot > 0 else np.nan
    ms.betas = {name: float(b) for name, b in zip(X_parts, beta[1:])}

    # current impulses = today's factor moves (last observation)
    drag = 0.0
    for name in X_parts:
        impulse = float(df[name].iloc[-1])
        ms.current_impulse[name] = impulse
        drag += ms.betas[name] * impulse
    ms.expected_macro_drag = drag
    if not math.isfinite(drag) or (math.isfinite(ms.r_squared) and ms.r_squared < 0.05):
        ms.assessment = "NEUTRAL"
        ms.macro_score = 50.0
        if math.isfinite(ms.r_squared) and ms.r_squared < 0.05:
            ms.notes.append("macro regression explains <5% of variance — treated as neutral")
        return ms
    if drag > 0.002:
        ms.assessment = "POSITIVE"
    elif drag < -0.002:
        ms.assessment = "NEGATIVE"
    ms.macro_score = float(np.clip(50 + drag * 10000, 0, 100))
    return ms
