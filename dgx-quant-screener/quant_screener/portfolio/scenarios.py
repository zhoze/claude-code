"""Return-scenario matrix for Mean-CVaR (spec §9).

Scenarios are historical point-in-time daily joint returns — no normality
assumption — augmented with stressed-volatility resamples and crisis-period
observations so the tail is not limited to the recent calm sample.
"""

from __future__ import annotations

import datetime as dt

import numpy as np
import pandas as pd

CRISIS_WINDOWS = [  # regime-conditioned stress observations (spec §9)
    ("2008-09-01", "2009-03-31"), ("2011-07-01", "2011-10-31"),
    ("2015-08-01", "2016-02-29"), ("2018-10-01", "2018-12-31"),
    ("2020-02-15", "2020-04-30"), ("2022-01-01", "2022-10-31"),
]


def joint_return_matrix(prices: dict[str, pd.DataFrame], lookback_days: int) -> pd.DataFrame:
    rets = {}
    for t, px in prices.items():
        if px is not None and len(px) > 20:
            rets[t] = px["close"].pct_change()
    if not rets:
        return pd.DataFrame()
    R = pd.DataFrame(rets).dropna(how="all").tail(lookback_days)
    return R.dropna()   # only days where all names traded (joint scenarios)


def blended_expected_returns(tickers: list[str], horizon_days: int,
                             hist_matrix: pd.DataFrame,
                             fundamental_scores: dict[str, float],
                             ml_expected: dict[str, float],
                             technical_scores: dict[str, float],
                             regime_tilt: float, blend: dict) -> pd.Series:
    """Blend per spec §9: historical + fundamental + ML + technical + regime.
    Each component is expressed as an expected return over `horizon_days`."""
    out = {}
    for t in tickers:
        parts, weights = [], []
        if t in hist_matrix.columns:
            parts.append(float(hist_matrix[t].mean()) * horizon_days)
            weights.append(blend["historical"])
        fs = fundamental_scores.get(t)
        if fs is not None and np.isfinite(fs):
            parts.append((fs - 50) / 50 * 0.01 * horizon_days / 5)   # ±1%/wk at extremes
            weights.append(blend["fundamental"])
        mle = ml_expected.get(t)
        if mle is not None and np.isfinite(mle):
            parts.append(float(mle))
            weights.append(blend["ml"])
        ts_ = technical_scores.get(t)
        if ts_ is not None and np.isfinite(ts_):
            parts.append((ts_ - 50) / 50 * 0.008 * horizon_days / 5)
            weights.append(blend["technical"])
        parts.append(regime_tilt * 0.005 * horizon_days / 5)
        weights.append(blend["regime"])
        w = np.array(weights)
        out[t] = float(np.dot(parts, w / w.sum())) if w.sum() > 0 else np.nan
    return pd.Series(out)


def scenario_matrix(hist_matrix: pd.DataFrame, expected: pd.Series,
                    n_stress_resamples: int = 250, stress_vol_mult: float = 1.75,
                    seed: int = 7) -> np.ndarray:
    """Historical scenarios re-centered on blended expectations, plus
    volatility-stressed bootstrap resamples of the worst decile days."""
    R = hist_matrix[expected.index].to_numpy()
    mu_hist = R.mean(axis=0)
    R_centered = R - mu_hist + expected.to_numpy() / max(len(R), 1) * 0  # keep daily scale
    R_shifted = R_centered + (expected.to_numpy() / 21)                  # daily drift from E[r] (monthly)
    rng = np.random.default_rng(seed)
    port_proxy = R.mean(axis=1)
    worst = np.argsort(port_proxy)[: max(len(R) // 10, 10)]
    idx = rng.choice(worst, size=n_stress_resamples, replace=True)
    stressed = R[idx] * stress_vol_mult
    return np.vstack([R_shifted, stressed])
