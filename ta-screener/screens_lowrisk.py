"""Low-risk family screens (5) for the ta-screener 150 catalog.

Betting-Against-Beta, low volatility, low idiosyncratic volatility, MAX-effect
avoidance, and downside beta. All are "less risk = better", so every score is the
negated risk measure (screen contract: higher = better, NaN = cannot evaluate).
"""
from __future__ import annotations

import numpy as np
import pandas as pd

import ops
from panel import Panel, load_config, synthetic_panel
from screen_lib import MissingInputError, ScreenSpec

ANN = 252  # trading days per year (annualization only, not a window length)


def _market_frame(panel: Panel, cfg: dict, like: pd.DataFrame) -> pd.DataFrame:
    """SPY daily returns broadcast to the date x ticker shape."""
    m = panel.market_returns(cfg).reindex(like.index)
    return pd.DataFrame(np.tile(m.to_numpy(dtype=float)[:, None], (1, like.shape[1])),
                        index=like.index, columns=like.columns)


def _rolling_beta(r: pd.DataFrame, mf: pd.DataFrame, d: int) -> pd.DataFrame:
    var_m = ops.stddev(mf, d) ** 2
    return ops.clean(ops.covariance(r, mf, d) / var_m)


def bab_low_beta(panel: Panel, cfg: dict) -> pd.DataFrame:
    lc = cfg["lowrisk"]
    r = panel.returns
    beta = _rolling_beta(r, _market_frame(panel, cfg, r), lc["beta_days"])
    a, b = lc["blume_shrink"]
    return -(a * beta + b)


def low_vol(panel: Panel, cfg: dict) -> pd.DataFrame:
    lc = cfg["lowrisk"]
    return -(ops.stddev(panel.returns, lc["vol_days"]) * np.sqrt(ANN))


def low_idio_vol(panel: Panel, cfg: dict) -> pd.DataFrame:
    """Residual std from the market model: var(resid) = var(r) - beta^2 var(m)."""
    lc = cfg["lowrisk"]
    d = lc["beta_days"]
    r = panel.returns
    mf = _market_frame(panel, cfg, r)
    var_r = ops.stddev(r, d) ** 2
    var_m = ops.stddev(mf, d) ** 2
    beta = ops.clean(ops.covariance(r, mf, d) / var_m)
    var_resid = (var_r - beta ** 2 * var_m).clip(lower=0.0)
    return -(np.sqrt(var_resid) * np.sqrt(ANN))


def max_avoidance(panel: Panel, cfg: dict) -> pd.DataFrame:
    """-(mean of the max_n largest daily returns in the trailing max_days window)."""
    lc = cfg["lowrisk"]
    d, k = lc["max_days"], lc["max_n"]
    r = panel.returns
    a = r.to_numpy(dtype=float)
    out = np.full(a.shape, np.nan)
    if a.shape[0] >= d:
        w = np.lib.stride_tricks.sliding_window_view(a, d, axis=0)
        topk = np.sort(w, axis=-1)[..., -k:].mean(axis=-1)
        topk = np.where(np.isnan(w).any(axis=-1), np.nan, topk)
        out[d - 1:] = topk
    return -pd.DataFrame(out, index=r.index, columns=r.columns)


def downside_beta(panel: Panel, cfg: dict) -> pd.DataFrame:
    """Beta estimated only on trailing days when the market was down (Ang-Chen-Xing).

    Rolling masked moments: cov/var over down-days only, requiring at least
    beta_days/4 usable down-days in the window.
    """
    lc = cfg["lowrisk"]
    d = lc["beta_days"]
    r = panel.returns
    mf = _market_frame(panel, cfg, r)
    mask = (mf < 0) & r.notna() & mf.notna()
    rm = r.where(mask, 0.0)
    mm = mf.where(mask, 0.0)
    n = mask.astype(float).rolling(d, min_periods=d).sum()
    s_r = rm.rolling(d, min_periods=d).sum()
    s_m = mm.rolling(d, min_periods=d).sum()
    s_rm = (rm * mm).rolling(d, min_periods=d).sum()
    s_mm = (mm * mm).rolling(d, min_periods=d).sum()
    n = n.where(n >= d / 4)
    cov = s_rm / n - (s_r / n) * (s_m / n)
    var = s_mm / n - (s_m / n) ** 2
    return -ops.clean(cov / var)


SPECS = [
    ScreenSpec(key="bab_low_beta", family="lowrisk", title="Betting Against Beta (low-beta leg)",
               citation="Frazzini & Pedersen (2014), JFE 111", arxiv=None,
               runner=bab_low_beta, validation=1.0, us_applicability=1.0, persistence=0.7,
               overfit_risk=0.2, perf_bucket=0.6, turnover="low", needs=("benchmarks",),
               notes="Blume-shrunk 252d beta; BAB Sharpe ~0.75 published (1926-2009)."),
    ScreenSpec(key="low_vol", family="lowrisk", title="Low realized volatility",
               citation="Blitz & van Vliet (2007), JPM", arxiv=None,
               runner=low_vol, validation=1.0, us_applicability=1.0, persistence=0.7,
               overfit_risk=0.2, perf_bucket=0.6, turnover="low"),
    ScreenSpec(key="low_idio_vol", family="lowrisk", title="Low idiosyncratic volatility",
               citation="Ang, Hodrick, Xing, Zhang (2006), JF 61", arxiv=None,
               runner=low_idio_vol, validation=1.0, us_applicability=1.0, persistence=0.6,
               overfit_risk=0.2, perf_bucket=0.6, turnover="low", needs=("benchmarks",)),
    ScreenSpec(key="max_avoidance", family="lowrisk", title="MAX-effect (lottery) avoidance",
               citation="Bali, Cakici, Whitelaw (2011), JFE 99", arxiv=None,
               runner=max_avoidance, validation=0.9, us_applicability=1.0, persistence=0.6,
               overfit_risk=0.2, perf_bucket=0.6, turnover="medium"),
    ScreenSpec(key="downside_beta", family="lowrisk", title="Low downside beta",
               citation="Ang, Chen, Xing (2006), RFS 19", arxiv=None,
               runner=downside_beta, validation=0.8, us_applicability=1.0, persistence=0.5,
               overfit_risk=0.3, perf_bucket=0.4, turnover="low", needs=("benchmarks",)),
]


def selftest() -> int:
    panel = synthetic_panel()
    cfg = load_config()
    bad = []
    for spec in SPECS:
        try:
            spec.validate()
            score = spec.runner(panel, cfg)
            assert isinstance(score, pd.DataFrame) and score.shape == panel.close.shape
            cov = score.iloc[-1].notna().mean()
            assert cov >= 0.25, f"last-row coverage {cov:.0%}"
            print(f"  ok {spec.key} (cov {cov:.0%})")
        except Exception as e:  # noqa: BLE001
            bad.append((spec.key, str(e)))
    if bad:
        for k, m in bad:
            print(f"  FAIL {k}: {m}")
        return 1
    print(f"screens_lowrisk.py selftest: OK — {len(SPECS)} low-risk screens")
    return 0


if __name__ == "__main__":
    import sys
    if "--selftest" in sys.argv:
        sys.exit(selftest())
    print(__doc__)
