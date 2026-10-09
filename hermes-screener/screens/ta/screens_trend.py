"""Trend / classical-indicator screens (10) for the ta-screener 150 catalog.

The indicator set follows the feature-selection evidence of Moodi &
Jahangard-Rafsanjani (arXiv:2310.09903) — PPO, Bollinger, Squeeze, Ichimoku, OBV —
plus the trainable-MACD idea of Lu's Technical Indicator Networks (arXiv:2507.20202)
as a causal correlation-weighted MACD blend. Conditional screens (breakouts, squeeze
fires, ADX filter) return NaN when their setup is off — that is "no signal", not zero.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

import ops
from panel import Panel, load_config, synthetic_panel
from screen_lib import MissingInputError, ScreenSpec


def _ema(x: pd.DataFrame, span: int) -> pd.DataFrame:
    return x.ewm(span=span, adjust=False, min_periods=span).mean()


def _wilder(x: pd.DataFrame, n: int) -> pd.DataFrame:
    return x.ewm(alpha=1.0 / n, adjust=False, min_periods=n).mean()


def _days_since(event: pd.DataFrame) -> pd.DataFrame:
    """Trading days since the last True in each column (NaN before the first)."""
    idx = pd.DataFrame(
        np.tile(np.arange(len(event.index), dtype=float)[:, None], (1, event.shape[1])),
        index=event.index, columns=event.columns)
    last = idx.where(event > 0).ffill()
    return idx - last


def ppo(panel: Panel, cfg: dict) -> pd.DataFrame:
    tc = cfg["trend"]
    c = panel.adjclose
    fast, slow = _ema(c, tc["ppo_fast"]), _ema(c, tc["ppo_slow"])
    return ops.clean((fast - slow) / slow)


def _macd_parts(c: pd.DataFrame, f: int, s: int, sig: int):
    macd = _ema(c, f) - _ema(c, s)
    signal = _ema(macd, sig)
    return macd, signal


def macd_cross(panel: Panel, cfg: dict) -> pd.DataFrame:
    """Freshness of the current bullish MACD(12,26,9) cross; NaN while bearish."""
    c = panel.adjclose
    macd, signal = _macd_parts(c, 12, 26, 9)
    bull = ops.gt(macd, signal)
    crossed_up = ops.and_(bull, ops.not_(ops.delay(bull, 1)))
    days = _days_since(crossed_up)
    return ops.where(bull, ops.clean(1.0 / (1.0 + days)), float("nan"))


def macd_tin(panel: Panel, cfg: dict) -> pd.DataFrame:
    """Causal correlation-weighted blend of three MACD histograms (TIN-inspired).

    Each histogram is normalized by 63d price vol; its weight at date t is the
    trailing 126d correlation between the *lagged* histogram and same-day returns
    (i.e. h_{tau-1} vs r_tau), so weights use only information available at t.
    """
    c = panel.adjclose
    r = panel.returns
    norm = ops.stddev(c, 63)
    blended_num = None
    blended_den = None
    for f, s, sig in ((8, 17, 9), (12, 26, 9), (19, 39, 9)):
        macd, signal = _macd_parts(c, f, s, sig)
        h = ops.clean((macd - signal) / norm)
        w = ops.correlation(ops.delay(h, 1), r, 126)
        term = w * h
        blended_num = term if blended_num is None else blended_num + term
        aw = abs(w)
        blended_den = aw if blended_den is None else blended_den + aw
    return ops.clean(blended_num / blended_den)


def ichimoku_position(panel: Panel, cfg: dict) -> pd.DataFrame:
    t9, t26, t52 = cfg["trend"]["ichimoku"]
    h, low, c = panel.high, panel.low, panel.adjclose
    tenkan = (ops.ts_max(h, t9) + ops.ts_min(low, t9)) / 2.0
    kijun = (ops.ts_max(h, t26) + ops.ts_min(low, t26)) / 2.0
    senkou_a = ops.delay((tenkan + kijun) / 2.0, t26)
    senkou_b = ops.delay((ops.ts_max(h, t52) + ops.ts_min(low, t52)) / 2.0, t26)
    cloud_top = ops.max_(senkou_a, senkou_b)
    return (1.0 * ops.gt(c, cloud_top)
            + 0.5 * ops.gt(tenkan, kijun)
            + 0.25 * ops.gt(c, kijun))


def obv_slope(panel: Panel, cfg: dict) -> pd.DataFrame:
    """20d OLS slope of On-Balance Volume, normalized by 20d average dollar volume."""
    d = cfg["trend"]["obv_days"]
    c, v = panel.adjclose, panel.volume
    step = (ops.sign(ops.delta(c, 1)) * v).fillna(0.0)
    obv = step.cumsum().mask(c.isna())
    tf = pd.DataFrame(
        np.tile(np.arange(len(c.index), dtype=float)[:, None], (1, c.shape[1])),
        index=c.index, columns=c.columns)
    slope = ops.covariance(obv, tf, d) / ((d ** 2 - 1) / 12.0)
    return ops.clean(slope / panel.adv(cfg["adv_days"]))


def _atr(panel: Panel, n: int) -> pd.DataFrame:
    h, low, c = panel.high, panel.low, panel.adjclose
    pc = ops.delay(c, 1)
    tr = ops.max_(h - low, ops.max_(abs(h - pc), abs(low - pc)))
    return _wilder(tr, n)


def ttm_squeeze(panel: Panel, cfg: dict) -> pd.DataFrame:
    """Squeeze (BB inside Keltner >= 5 days) firing off: direction x bandwidth pop."""
    tc = cfg["trend"]
    d, sig, km = tc["bb_days"], tc["bb_sigma"], tc["keltner_mult"]
    c = panel.adjclose
    mid = c.rolling(d, min_periods=d).mean()
    sd = ops.stddev(c, d)
    atr = _atr(panel, d)
    ema_mid = _ema(c, d)
    on = ops.and_(ops.lt(mid + sig * sd, ema_mid + km * atr),
                  ops.gt(mid - sig * sd, ema_mid - km * atr))
    run = on.rolling(5, min_periods=5).sum()
    fired = ops.and_(ops.ge(ops.delay(run, 1), 5.0), ops.not_(on))
    bw = ops.clean(2.0 * sig * sd / mid)
    pop = abs(ops.delta(bw, 1))
    direction = ops.sign(ops.ts_sum(panel.returns, 5))
    return ops.where(fired, pop * direction, float("nan"))


def bollinger_breakout(panel: Panel, cfg: dict) -> pd.DataFrame:
    tc = cfg["trend"]
    d, sig = tc["bb_days"], tc["bb_sigma"]
    c = panel.adjclose
    mid = c.rolling(d, min_periods=d).mean()
    sd = ops.stddev(c, d)
    upper = mid + sig * sd
    return ops.where(ops.gt(c, upper), ops.clean((c - upper) / sd), float("nan"))


def donchian_55(panel: Panel, cfg: dict) -> pd.DataFrame:
    d = cfg["trend"]["donchian_days"]
    c = panel.adjclose
    prior_high = ops.delay(ops.ts_max(c, d), 1)
    brk = ops.clean(c / prior_high) - 1.0
    return ops.where(ops.ge(c, prior_high), ops.max_(brk, 0.0), float("nan"))


def adx_trend(panel: Panel, cfg: dict) -> pd.DataFrame:
    tc = cfg["trend"]
    n = tc["adx_days"]
    h, low = panel.high, panel.low
    up = ops.delta(h, 1)
    dn = -ops.delta(low, 1)
    plus_dm = ops.where(ops.and_(ops.gt(up, dn), ops.gt(up, 0.0)), up, 0.0)
    minus_dm = ops.where(ops.and_(ops.gt(dn, up), ops.gt(dn, 0.0)), dn, 0.0)
    atr = _atr(panel, n)
    plus_di = ops.clean(100.0 * _wilder(plus_dm, n) / atr)
    minus_di = ops.clean(100.0 * _wilder(minus_dm, n) / atr)
    dx = ops.clean(100.0 * abs(plus_di - minus_di) / (plus_di + minus_di))
    adx = _wilder(dx, n)
    return ops.where(ops.gt(adx, float(tc["adx_min"])),
                     adx * ops.sign(plus_di - minus_di), float("nan"))


def golden_cross(panel: Panel, cfg: dict) -> pd.DataFrame:
    tc = cfg["trend"]
    c = panel.adjclose
    fast = c.rolling(tc["sma_fast"], min_periods=tc["sma_fast"]).mean()
    slow = c.rolling(tc["sma_slow"], min_periods=tc["sma_slow"]).mean()
    bull = ops.gt(fast, slow)
    crossed_up = ops.and_(bull, ops.not_(ops.delay(bull, 1)))
    days = _days_since(crossed_up)
    spread = ops.clean(fast / slow) - 1.0
    return ops.where(bull, spread + ops.clean(1.0 / (1.0 + days)), float("nan"))


_RUBRIC = dict(us_applicability=0.7, persistence=0.4, perf_bucket=0.4, turnover="medium")

SPECS = [
    ScreenSpec(key="ppo", family="trend", title="Percentage Price Oscillator",
               citation="PPO; top-ranked feature in Moodi & Jahangard-Rafsanjani",
               arxiv="2310.09903", runner=ppo, validation=0.6, us_applicability=0.7,
               persistence=0.5, overfit_risk=0.5, perf_bucket=0.4, turnover="medium"),
    ScreenSpec(key="macd_cross", family="trend", title="MACD bullish-cross freshness",
               citation="Appel; classical baseline in Lu (2025) TIN", arxiv="2507.20202",
               runner=macd_cross, validation=0.5, overfit_risk=0.4, **_RUBRIC),
    ScreenSpec(key="macd_tin", family="trend", title="MACD-TIN causal weighted blend",
               citation="Lu (2025), Technical Indicator Networks", arxiv="2507.20202",
               runner=macd_tin, validation=0.5, us_applicability=0.7, persistence=0.5,
               overfit_risk=0.5, perf_bucket=0.6, turnover="medium"),
    ScreenSpec(key="ichimoku_position", family="trend", title="Ichimoku cloud position",
               citation="Ichimoku; selected in Moodi & Jahangard-Rafsanjani",
               arxiv="2310.09903", runner=ichimoku_position, validation=0.4,
               overfit_risk=0.4, **_RUBRIC),
    ScreenSpec(key="obv_slope", family="trend", title="On-Balance-Volume slope",
               citation="Granville; Archer OBV in Moodi & Jahangard-Rafsanjani",
               arxiv="2310.09903", runner=obv_slope, validation=0.5, us_applicability=0.7,
               persistence=0.5, overfit_risk=0.4, perf_bucket=0.4, turnover="medium"),
    ScreenSpec(key="ttm_squeeze", family="trend", title="TTM Squeeze breakout",
               citation="Carter; Squeeze/Squeeze_pro top features in arXiv:2310.09903",
               arxiv="2310.09903", runner=ttm_squeeze, validation=0.4, overfit_risk=0.5,
               **_RUBRIC, notes="conditional: fires only when a squeeze releases."),
    ScreenSpec(key="bollinger_breakout", family="trend", title="Bollinger upper-band breakout",
               citation="Bollinger; Moodi & Jahangard-Rafsanjani feature set",
               arxiv="2310.09903", runner=bollinger_breakout, validation=0.5,
               overfit_risk=0.3, **_RUBRIC, notes="conditional: close above upper band."),
    ScreenSpec(key="donchian_55", family="trend", title="Donchian 55-day breakout",
               citation="Donchian; Turtle trading rules", arxiv=None,
               runner=donchian_55, validation=0.5, us_applicability=0.7, persistence=0.5,
               overfit_risk=0.3, perf_bucket=0.4, turnover="medium",
               notes="conditional: new 55d high."),
    ScreenSpec(key="adx_trend", family="trend", title="ADX-filtered directional trend",
               citation="Wilder (1978); Moodi & Jahangard-Rafsanjani feature set",
               arxiv="2310.09903", runner=adx_trend, validation=0.5, overfit_risk=0.4,
               **_RUBRIC, notes="conditional: ADX above threshold."),
    ScreenSpec(key="golden_cross", family="trend", title="Golden cross (50/200 SMA)",
               citation="classical; ML-validation literature context", arxiv=None,
               runner=golden_cross, validation=0.5, us_applicability=0.8, persistence=0.4,
               overfit_risk=0.2, perf_bucket=0.4, turnover="low",
               notes="conditional: fast SMA above slow."),
]

CONDITIONAL = {"macd_cross", "ttm_squeeze", "bollinger_breakout", "donchian_55",
               "adx_trend", "golden_cross"}


def selftest() -> int:
    panel = synthetic_panel()
    cfg = load_config()
    bad, fallback = [], []
    for spec in SPECS:
        try:
            spec.validate()
            score = spec.runner(panel, cfg)
            assert isinstance(score, pd.DataFrame) and score.shape == panel.close.shape
            cov = score.iloc[-1].notna().mean()
            if cov >= 0.25:
                print(f"  ok {spec.key} (cov {cov:.0%})")
            else:
                recent = score.iloc[-60:].notna().sum(axis=1).max()
                assert spec.key in CONDITIONAL and recent >= 2, \
                    f"coverage {cov:.0%} and best recent {recent} names"
                fallback.append(spec.key)
                print(f"  ok {spec.key} (conditional fallback: best recent {recent} names)")
        except Exception as e:  # noqa: BLE001
            bad.append((spec.key, str(e)))
    if bad:
        for k, m in bad:
            print(f"  FAIL {k}: {m}")
        return 1
    print(f"screens_trend.py selftest: OK — {len(SPECS)} screens; fallback: {fallback}")
    return 0


if __name__ == "__main__":
    import sys
    if "--selftest" in sys.argv:
        sys.exit(selftest())
    print(__doc__)
