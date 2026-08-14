"""Momentum screens (12) for the ta-screener 150 catalog.

Batch C, part 1: classic price momentum (12-1, 6-1), time-series momentum, 52-week
high, residual momentum, volatility-managed variants, multi-scale MACD, momentum
acceleration, information discreteness (frog-in-the-pan), sector momentum, and a
regime-filtered momentum overlay.

All runners obey the frozen screen contract: runner(panel, cfg) -> date x ticker
score frame shaped like panel.close, higher = better, NaN = cannot evaluate,
strictly causal. Tunables come from cfg["momentum"] (plus benchmark mapping in
cfg["benchmarks"]). Fixed spec constants that config.json carries no key for are
declared as documented module constants below.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

import ops
from panel import Panel, load_config, synthetic_panel
from screen_lib import MissingInputError, ScreenSpec

# Annualization constant (calendar fact, not a tunable).
TRADING_DAYS_PER_YEAR = 252.0

# Baz et al. (2015) MACD-signal normalization windows. Fixed spec constants of the
# published indicator (analogous to the 12/26/9 of classic MACD); the tunable EMA
# scale pairs live in cfg["momentum"]["macd_scales"]. config.json has no key for
# these two windows, so they are pinned here per the prescribed formula.
MACD_PRICE_STD_DAYS = 63
MACD_Z_DAYS = 252

# Prescribed blend weights for sector_momentum (Moskowitz & Grinblatt blend of
# sector-ETF momentum and own momentum). config.json carries no key for them.
SECTOR_ETF_WEIGHT = 0.7
OWN_MOM_WEIGHT = 0.3


# ---------------------------------------------------------------- shared helpers

def _ema(x: pd.DataFrame, n: int) -> pd.DataFrame:
    """EMA per the frozen briefing convention: span=n, adjust=False, min_periods=n."""
    return x.ewm(span=n, adjust=False, min_periods=n).mean()


def _market_frame(panel: Panel, cfg: dict) -> pd.DataFrame:
    """SPY daily returns broadcast to a frame shaped like panel.close."""
    m = panel.market_returns(cfg).reindex(panel.close.index)
    return pd.DataFrame(
        np.repeat(m.to_numpy(dtype=float)[:, None], panel.close.shape[1], axis=1),
        index=panel.close.index, columns=panel.close.columns)


def _mom_12_1_frame(panel: Panel, cfg: dict) -> pd.DataFrame:
    mcfg = cfg["momentum"]
    return ops.clean(panel.adjclose.shift(mcfg["skip_days"])
                     / panel.adjclose.shift(mcfg["lookback_12m"]) - 1.0)


# ---------------------------------------------------------------------- runners

def mom_12_1(panel: Panel, cfg: dict) -> pd.DataFrame:
    """Classic 12-1 momentum: return over the past year skipping the last month."""
    return _mom_12_1_frame(panel, cfg)


def mom_6_1(panel: Panel, cfg: dict) -> pd.DataFrame:
    """Intermediate 6-1 momentum: 6-month return skipping the last month."""
    mcfg = cfg["momentum"]
    return ops.clean(panel.adjclose.shift(mcfg["skip_days"])
                     / panel.adjclose.shift(mcfg["lookback_6m"]) - 1.0)


def ts_momentum(panel: Panel, cfg: dict) -> pd.DataFrame:
    """Time-series momentum: past 12-month total return, no skip."""
    mcfg = cfg["momentum"]
    return ops.clean(panel.adjclose / panel.adjclose.shift(mcfg["lookback_12m"]) - 1.0)


def high_52w(panel: Panel, cfg: dict) -> pd.DataFrame:
    """Proximity to the 52-week high: adjclose / trailing 12-month max."""
    mcfg = cfg["momentum"]
    return ops.clean(panel.adjclose / ops.ts_max(panel.adjclose, mcfg["lookback_12m"]))


def residual_momentum(panel: Panel, cfg: dict) -> pd.DataFrame:
    """Blitz-Huij-Martens residual momentum vs SPY.

    At each date t: closed-form OLS (alpha, beta) of stock returns on SPY over the
    trailing lookback_12m window; OLS residuals are then cumulated over the
    sub-window [t - lookback_12m, t - skip_days] and standardized by the residual
    std over the same sub-window. Sub-window sums/vars come from rolling stats
    shifted by skip_days, so the whole thing is vectorized and strictly causal.
    """
    mcfg = cfg["momentum"]
    L = mcfg["lookback_12m"]
    S = mcfg["skip_days"]
    n_sub = L - S
    r = panel.returns
    mf = _market_frame(panel, cfg)

    var_m = ops.stddev(mf, L) ** 2
    beta = ops.clean(ops.covariance(r, mf, L) / var_m)
    alpha = (r.rolling(L, min_periods=L).mean()
             - beta * mf.rolling(L, min_periods=L).mean())

    sum_r_sub = ops.ts_sum(r, n_sub).shift(S)
    sum_m_sub = ops.ts_sum(mf, n_sub).shift(S)
    cum_resid = sum_r_sub - float(n_sub) * alpha - beta * sum_m_sub

    var_r_sub = ops.stddev(r, n_sub).shift(S) ** 2
    var_m_sub = ops.stddev(mf, n_sub).shift(S) ** 2
    cov_sub = ops.covariance(r, mf, n_sub).shift(S)
    resid_var = (var_r_sub + beta ** 2 * var_m_sub - 2.0 * beta * cov_sub)
    resid_std = resid_var.clip(lower=0.0) ** 0.5

    return ops.clean(cum_resid / resid_std)


def vol_scaled_momentum(panel: Panel, cfg: dict) -> pd.DataFrame:
    """12-1 momentum scaled by annualized realized volatility (DMN-style)."""
    mcfg = cfg["momentum"]
    mom = _mom_12_1_frame(panel, cfg)
    ann_vol = ops.stddev(panel.returns, mcfg["vol_scale_days"]) * np.sqrt(TRADING_DAYS_PER_YEAR)
    return ops.clean(mom / ann_vol)


def sharpe_momentum(panel: Panel, cfg: dict) -> pd.DataFrame:
    """Rolling Sharpe ratio of daily returns over sharpe_days."""
    mcfg = cfg["momentum"]
    d = mcfg["sharpe_days"]
    mean_r = panel.returns.rolling(d, min_periods=d).mean()
    return ops.clean(mean_r / ops.stddev(panel.returns, d))


def macd_multiscale(panel: Panel, cfg: dict) -> pd.DataFrame:
    """Baz et al. (2015) multi-scale MACD signal, mean of three normalized z's.

    For each [S, L] in macd_scales: q = (EMA_S - EMA_L) / stddev(close, 63);
    z = q / stddev(q, 252); score = mean of the per-scale z's (all scales required).
    """
    mcfg = cfg["momentum"]
    px_std = ops.stddev(panel.close, MACD_PRICE_STD_DAYS)
    zs = []
    for s_span, l_span in mcfg["macd_scales"]:
        q = ops.clean((_ema(panel.close, s_span) - _ema(panel.close, l_span)) / px_std)
        zs.append(ops.clean(q / ops.stddev(q, MACD_Z_DAYS)))
    total = zs[0]
    for z in zs[1:]:
        total = total + z
    return ops.clean(total / float(len(zs)))


def momentum_acceleration(panel: Panel, cfg: dict) -> pd.DataFrame:
    """Recent 6-month return minus the prior 6-month return (Sornette acceleration)."""
    mcfg = cfg["momentum"]
    c = panel.adjclose
    L6 = mcfg["lookback_6m"]
    L12 = mcfg["lookback_12m"]
    recent = c / c.shift(L6) - 1.0
    prior = c.shift(L6) / c.shift(L12) - 1.0
    return ops.clean(recent - prior)


def info_discreteness(panel: Panel, cfg: dict) -> pd.DataFrame:
    """Da-Gurun-Warachka frog-in-the-pan: continuous-information winners.

    ID = sign(mom_12_1) * (pct_neg_days - pct_pos_days over id_days). Among
    mom_12_1 > 0 names the score is -ID (continuous information drifts more);
    non-winners are NaN (conditional screen).
    """
    mcfg = cfg["momentum"]
    d = mcfg["id_days"]
    r = panel.returns
    mom = _mom_12_1_frame(panel, cfg)
    pct_neg = ops.clean(ops.ts_sum(ops.lt(r, 0.0), d) / float(d))
    pct_pos = ops.clean(ops.ts_sum(ops.gt(r, 0.0), d) / float(d))
    id_ = ops.sign(mom) * (pct_neg - pct_pos)
    return ops.where(ops.gt(mom, 0.0), -id_, np.nan)


def sector_momentum(panel: Panel, cfg: dict) -> pd.DataFrame:
    """Moskowitz-Grinblatt blend: sector-ETF 6m momentum + own 12-1 momentum.

    score = 0.7 * rank(sector-ETF lookback_6m return, broadcast to members)
          + 0.3 * rank(own mom_12_1). Tickers with no mapped sector ETF get NaN
    on the sector leg (NaN propagates through the sum).
    """
    mcfg = cfg["momentum"]
    L6 = mcfg["lookback_6m"]
    ser = panel.sector_etf_returns(cfg)                    # date x sector-name returns
    sec6 = (ops.product(1.0 + ser, L6) - 1.0).reindex(panel.close.index)
    sectors = panel.sectors()
    mem = pd.DataFrame(np.nan, index=panel.close.index, columns=panel.close.columns)
    for t in panel.close.columns:
        s = sectors.get(t)
        if isinstance(s, str) and s in sec6.columns:
            mem[t] = sec6[s]
    mom = _mom_12_1_frame(panel, cfg)
    return SECTOR_ETF_WEIGHT * ops.rank(mem) + OWN_MOM_WEIGHT * ops.rank(mom)


def regime_filtered_momentum(panel: Panel, cfg: dict) -> pd.DataFrame:
    """12-1 momentum masked to NaN in high-volatility market regimes.

    SPY realized vol over regime_vol_days is compared to its regime_pct quantile
    over the trailing regime_lookback window; dates above the quantile (or where
    the regime cannot be evaluated) are NaN. Conditional screen by construction.
    """
    mcfg = cfg["momentum"]
    m = panel.market_returns(cfg).reindex(panel.close.index)
    v = mcfg["regime_vol_days"]
    lb = mcfg["regime_lookback"]
    vol = m.rolling(v, min_periods=v).std()
    thresh = vol.rolling(lb, min_periods=lb).quantile(mcfg["regime_pct"])
    keep = ((vol <= thresh) & vol.notna() & thresh.notna()).to_numpy()
    mom = _mom_12_1_frame(panel, cfg)
    arr = mom.to_numpy(dtype=float, copy=True)
    arr[~keep, :] = np.nan
    return pd.DataFrame(arr, index=mom.index, columns=mom.columns)


# ------------------------------------------------------------------------ SPECS

SPECS = [
    ScreenSpec(
        key="mom_12_1", family="momentum", title="12-1 price momentum",
        citation="Jegadeesh & Titman (1993 JF)", arxiv=None, runner=mom_12_1,
        validation=1.0, us_applicability=1.0, persistence=0.6, overfit_risk=0.2,
        perf_bucket=0.8, turnover="low", needs=(),
        notes="Past-year return skipping the most recent month (skip_days)."),
    ScreenSpec(
        key="mom_6_1", family="momentum", title="6-1 price momentum",
        citation="Jegadeesh & Titman (1993 JF)", arxiv=None, runner=mom_6_1,
        validation=1.0, us_applicability=1.0, persistence=0.6, overfit_risk=0.2,
        perf_bucket=0.6, turnover="low", needs=(),
        notes="Intermediate-horizon variant: lookback_6m with the same skip."),
    ScreenSpec(
        key="ts_momentum", family="momentum", title="Time-series momentum (12m)",
        citation="Moskowitz, Ooi, Pedersen (2012 JFE)", arxiv=None, runner=ts_momentum,
        validation=1.0, us_applicability=0.7, persistence=0.6, overfit_risk=0.2,
        perf_bucket=0.8, turnover="low", needs=(),
        notes="Past lookback_12m total return, no skip (TSMOM applied cross-sectionally)."),
    ScreenSpec(
        key="high_52w", family="momentum", title="52-week high proximity",
        citation="George & Hwang (2004 JF)", arxiv=None, runner=high_52w,
        validation=1.0, us_applicability=1.0, persistence=0.6, overfit_risk=0.2,
        perf_bucket=0.6, turnover="low", needs=(),
        notes="adjclose / trailing lookback_12m max; nearness to the high, not the return."),
    ScreenSpec(
        key="residual_momentum", family="momentum",
        title="Residual (idiosyncratic) momentum",
        citation="Blitz, Huij, Martens (2011 JEmpFin)", arxiv=None,
        runner=residual_momentum,
        validation=0.9, us_applicability=0.9, persistence=0.7, overfit_risk=0.3,
        perf_bucket=0.8, turnover="low", needs=("benchmarks",),
        notes="Closed-form rolling OLS vs SPY over lookback_12m; residuals cumulated "
              "over [t-lookback_12m, t-skip_days], scaled by sub-window residual std."),
    ScreenSpec(
        key="vol_scaled_momentum", family="momentum", title="Volatility-scaled momentum",
        citation="Lim, Zohren, Roberts (2019), Deep Momentum Networks-inspired",
        arxiv="1904.04912", runner=vol_scaled_momentum,
        validation=0.7, us_applicability=0.6, persistence=0.6, overfit_risk=0.3,
        perf_bucket=0.8, turnover="low", needs=(),
        notes="mom_12_1 / (annualized stddev over vol_scale_days)."),
    ScreenSpec(
        key="sharpe_momentum", family="momentum", title="Rolling Sharpe momentum",
        citation="Sharpe-optimized Deep Momentum Networks family (Lim, Zohren, Roberts 2019)",
        arxiv="1904.04912", runner=sharpe_momentum,
        validation=0.7, us_applicability=0.7, persistence=0.6, overfit_risk=0.3,
        perf_bucket=0.6, turnover="low", needs=(),
        notes="mean/std of daily returns over sharpe_days."),
    ScreenSpec(
        key="macd_multiscale", family="momentum", title="Multi-scale MACD trend",
        citation="Baz, Granger, Harvey, Le Roux, Rattray (2015); used in DMNs",
        arxiv="1904.04912", runner=macd_multiscale,
        validation=0.8, us_applicability=0.7, persistence=0.6, overfit_risk=0.4,
        perf_bucket=0.6, turnover="medium", needs=(),
        notes="Mean of three z-scored MACD signals over macd_scales; normalization "
              "windows (63/252) are fixed spec constants of the published indicator."),
    ScreenSpec(
        key="momentum_acceleration", family="momentum", title="Momentum acceleration",
        citation="Ardila, Forsythe, Sornette (2015)", arxiv=None,
        runner=momentum_acceleration,
        validation=0.5, us_applicability=0.8, persistence=0.5, overfit_risk=0.5,
        perf_bucket=0.4, turnover="medium", needs=(),
        notes="Second difference of trend: recent 6m return minus prior 6m return."),
    ScreenSpec(
        key="info_discreteness", family="momentum",
        title="Frog-in-the-pan information discreteness",
        citation="Da, Gurun, Warachka (2014 RFS)", arxiv=None, runner=info_discreteness,
        validation=0.9, us_applicability=1.0, persistence=0.6, overfit_risk=0.3,
        perf_bucket=0.6, turnover="low", needs=(),
        notes="Conditional screen (winners only) — selftest uses the >=2-names "
              "fallback assertion; continuous-information winners score highest."),
    ScreenSpec(
        key="sector_momentum", family="momentum", title="Sector momentum blend",
        citation="Moskowitz & Grinblatt (1999 JF)", arxiv=None, runner=sector_momentum,
        validation=0.9, us_applicability=1.0, persistence=0.5, overfit_risk=0.3,
        perf_bucket=0.6, turnover="medium", needs=("benchmarks", "sector"),
        notes="0.7*rank(sector-ETF 6m return) + 0.3*rank(own mom_12_1); weights are "
              "prescribed constants (no cfg key)."),
    ScreenSpec(
        key="regime_filtered_momentum", family="momentum",
        title="Regime-filtered momentum",
        citation="Wood, Giegerich, Roberts, Zohren — changepoint/regime idea",
        arxiv="2112.08534", runner=regime_filtered_momentum,
        validation=0.6, us_applicability=0.7, persistence=0.6, overfit_risk=0.4,
        perf_bucket=0.8, turnover="low", needs=("benchmarks",),
        notes="Conditional screen (calm-regime dates only) — selftest uses the "
              ">=2-names fallback assertion; mom_12_1 masked when SPY vol is in "
              "its top (1-regime_pct) tail."),
]


# --------------------------------------------------------------------- selftest

# Conditional screens: coverage on any given date is legitimately sparse (winners
# only / calm-regime dates only), so the briefing's fallback assertion applies:
# >= 2 non-NaN names on SOME recent date instead of >= 25% last-row coverage.
CONDITIONAL_KEYS = {"info_discreteness", "regime_filtered_momentum"}
FALLBACK_RECENT_DAYS = 60  # "recent" horizon for the fallback assertion


def _check(spec: ScreenSpec, score: pd.DataFrame, panel: Panel) -> str:
    assert isinstance(score, pd.DataFrame), f"{spec.key}: runner returned {type(score)}"
    assert score.shape == panel.close.shape, f"{spec.key}: shape {score.shape}"
    assert score.index.equals(panel.close.index), f"{spec.key}: index mismatch"
    assert list(score.columns) == list(panel.close.columns), f"{spec.key}: column mismatch"
    vals = score.to_numpy(dtype=float)
    assert not np.isinf(vals).any(), f"{spec.key}: +-inf leaked (missing ops.clean?)"
    cov_last = float(score.iloc[-1].notna().mean())
    if spec.key in CONDITIONAL_KEYS:
        recent = score.iloc[-FALLBACK_RECENT_DAYS:]
        best = int(recent.notna().sum(axis=1).max())
        assert best >= 2, (f"{spec.key}: fallback failed — fewer than 2 non-NaN names "
                           f"on every date in the last {FALLBACK_RECENT_DAYS} rows")
        return (f"conditional fallback: best recent coverage {best} names; "
                f"last row {cov_last:.0%}")
    assert cov_last >= 0.25, f"{spec.key}: last-row coverage {cov_last:.0%} < 25%"
    return f"last-row coverage {cov_last:.0%}"


def selftest() -> int:
    panel = synthetic_panel()
    cfg = load_config()
    keys = [s.key for s in SPECS]
    assert len(SPECS) == 12, f"expected 12 momentum screens, got {len(SPECS)}"
    assert len(set(keys)) == len(keys), "duplicate screen keys"
    for spec in SPECS:
        spec.validate()
        assert spec.family == "momentum", spec.key
        score = spec.runner(panel, cfg)
        detail = _check(spec, score, panel)
        print(f"ok {spec.key}  ({detail})")
    print(f"screens_momentum.py selftest: OK — {len(SPECS)} momentum screens "
          f"(fallback assertion used by: {sorted(CONDITIONAL_KEYS)})")
    return 0


if __name__ == "__main__":
    import sys
    if "--selftest" in sys.argv:
        sys.exit(selftest())
    print(__doc__)
