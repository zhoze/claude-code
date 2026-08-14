"""Alphas 76..101 of Kakushadze (2016), 101 Formulaic Alphas (arXiv:1601.00991).

Convention: the paper's fractional day counts (e.g. 11.8259, 19.383) are rounded to
the nearest integer window, matching the widely used public implementations of the
101 alphas (rolling windows must be integral). The FORMULAS strings keep the paper's
verbatim fractional values for audit.

Documented deviation (paper is silent, see _corr_dz): a correlation window that is
FULLY OBSERVED but has zero variance on either side (pandas 0/0 -> NaN) evaluates
to 0.0 — "no measurable co-movement" — instead of NaN. Cross-sectional ranks of
slow-moving levels (price, adv) hold their rank for many consecutive days on narrow
universes, so short corr windows over them are degenerate most days; the frozen
any-NaN window ops then wipe every downstream nested window (decay_linear/ts_rank/
ts_argmax) to 100% NaN. Missing data still propagates NaN. Applied ONLY where the
wipe-out otherwise leaves an alpha with zero coverage: alphas 92, 96 and 100.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

import ops
from ops import (clean, rank, delay, delta, correlation, covariance, stddev, ts_sum,
                 product, ts_min, ts_max, ts_argmax, ts_argmin, ts_rank, decay_linear,
                 scale, signedpower, sign, log, abs_, min_, max_, lt, le, gt, ge, eq,
                 or_, and_, not_, where)

FORMULAS = {
    76: "(max(rank(decay_linear(delta(vwap, 1.24383), 11.8259)), Ts_Rank(decay_linear(Ts_Rank(correlation(IndNeutralize(low, IndClass.sector), adv81, 8.14941), 19.569), 17.1543), 19.383)) * -1)",
    77: "min(rank(decay_linear(((((high + low) / 2) + high) - (vwap + high)), 20.0451)), rank(decay_linear(correlation(((high + low) / 2), adv40, 3.1614), 5.64125)))",
    78: "(rank(correlation(sum(((low * 0.352233) + (vwap * (1 - 0.352233))), 19.7428), sum(adv40, 19.7428), 6.83313))^rank(correlation(rank(vwap), rank(volume), 5.77492)))",
    79: "(rank(delta(IndNeutralize(((close * 0.60733) + (open * (1 - 0.60733))), IndClass.sector), 1.23438)) < rank(correlation(Ts_Rank(vwap, 3.60973), Ts_Rank(adv150, 9.18637), 14.6644)))",
    80: "((rank(Sign(delta(IndNeutralize(((open * 0.868128) + (high * (1 - 0.868128))), IndClass.industry), 4.04545)))^Ts_Rank(correlation(high, adv10, 5.11456), 5.53756)) * -1)",
    81: "((rank(Log(product(rank((rank(correlation(vwap, sum(adv10, 49.6054), 8.47743))^4)), 14.9655))) < rank(correlation(rank(vwap), rank(volume), 5.07914))) * -1)",
    82: "(min(rank(decay_linear(delta(open, 1.46063), 14.8717)), Ts_Rank(decay_linear(correlation(IndNeutralize(volume, IndClass.sector), ((open * 0.634196) + (open * (1 - 0.634196))), 17.4842), 6.92131), 13.4283)) * -1)",
    83: "((rank(delay(((high - low) / (sum(close, 5) / 5)), 2)) * rank(rank(volume))) / (((high - low) / (sum(close, 5) / 5)) / (vwap - close)))",
    84: "SignedPower(Ts_Rank((vwap - ts_max(vwap, 15.3217)), 20.7127), delta(close, 4.96796))",
    85: "(rank(correlation(((high * 0.876703) + (close * (1 - 0.876703))), adv30, 9.61331))^rank(correlation(Ts_Rank(((high + low) / 2), 3.70596), Ts_Rank(volume, 10.1595), 7.11408)))",
    86: "((Ts_Rank(correlation(close, sum(adv20, 14.7444), 6.00049), 20.4195) < rank(((open + close) - (vwap + open)))) * -1)",
    87: "(max(rank(decay_linear(delta(((close * 0.369701) + (vwap * (1 - 0.369701))), 1.91233), 2.65461)), Ts_Rank(decay_linear(abs(correlation(IndNeutralize(adv81, IndClass.industry), close, 13.4132)), 4.89768), 14.4535)) * -1)",
    88: "min(rank(decay_linear(((rank(open) + rank(low)) - (rank(high) + rank(close))), 8.06882)), Ts_Rank(decay_linear(correlation(Ts_Rank(close, 8.44728), Ts_Rank(adv60, 20.6966), 8.01266), 6.65053), 2.61957))",
    89: "(Ts_Rank(decay_linear(correlation(((low * 0.967285) + (low * (1 - 0.967285))), adv10, 6.94279), 5.51607), 3.79744) - Ts_Rank(decay_linear(delta(IndNeutralize(vwap, IndClass.industry), 3.48158), 10.1466), 15.3012))",
    90: "((rank((close - ts_max(close, 4.66719)))^Ts_Rank(correlation(IndNeutralize(adv40, IndClass.subindustry), low, 5.38375), 3.21856)) * -1)",
    91: "((Ts_Rank(decay_linear(decay_linear(correlation(IndNeutralize(close, IndClass.industry), volume, 9.74928), 16.398), 3.83219), 4.8667) - rank(decay_linear(correlation(vwap, adv30, 4.01303), 2.6809))) * -1)",
    92: "min(Ts_Rank(decay_linear(((((high + low) / 2) + close) < (low + open)), 14.7221), 18.8683), Ts_Rank(decay_linear(correlation(rank(low), rank(adv30), 7.58555), 6.94024), 6.80584))",
    93: "(Ts_Rank(decay_linear(correlation(IndNeutralize(vwap, IndClass.industry), adv81, 17.4193), 19.848), 7.54455) / rank(decay_linear(delta(((close * 0.524434) + (vwap * (1 - 0.524434))), 2.77377), 16.2664)))",
    94: "((rank((vwap - ts_min(vwap, 11.5783)))^Ts_Rank(correlation(Ts_Rank(vwap, 19.6462), Ts_Rank(adv60, 4.02992), 18.0926), 2.70756)) * -1)",
    95: "(rank((open - ts_min(open, 12.4105))) < Ts_Rank((rank(correlation(sum(((high + low) / 2), 19.1351), sum(adv40, 19.1351), 12.8742))^5), 11.7584))",
    96: "(max(Ts_Rank(decay_linear(correlation(rank(vwap), rank(volume), 3.83878), 4.16783), 8.38151), Ts_Rank(decay_linear(Ts_ArgMax(correlation(Ts_Rank(close, 7.45404), Ts_Rank(adv60, 4.13242), 3.65459), 12.6556), 14.0365), 13.4143)) * -1)",
    97: "((rank(decay_linear(delta(IndNeutralize(((low * 0.721001) + (vwap * (1 - 0.721001))), IndClass.industry), 3.3705), 20.4523)) - Ts_Rank(decay_linear(Ts_Rank(correlation(Ts_Rank(low, 7.87871), Ts_Rank(adv60, 17.255), 4.97547), 18.5925), 15.7152), 6.71659)) * -1)",
    98: "(rank(decay_linear(correlation(vwap, sum(adv5, 26.4719), 4.58418), 7.18088)) - rank(decay_linear(Ts_Rank(Ts_ArgMin(correlation(rank(open), rank(adv15), 20.8187), 8.62571), 6.95668), 8.07206)))",
    99: "((rank(correlation(sum(((high + low) / 2), 19.8975), sum(adv60, 19.8975), 8.8136)) < rank(correlation(low, volume, 6.28259))) * -1)",
    100: "(0 - (1 * (((1.5 * scale(indneutralize(indneutralize(rank(((((close - low) - (high - close)) / (high - low)) * volume)), IndClass.subindustry), IndClass.subindustry))) - scale(indneutralize((correlation(close, rank(adv20), 5) - rank(ts_argmin(close, 30))), IndClass.subindustry))) * (volume / adv20))))",
    101: "((close - open) / ((high - low) + .001))",
}


def _corr_dz(x: pd.DataFrame, y: pd.DataFrame, d: int) -> pd.DataFrame:
    """ops.correlation with degenerate (zero-variance) full windows as 0.0, not NaN.

    stddev is frozen at min_periods == window, so it is non-NaN exactly where the
    window is fully observed; 0.0 there marks a constant (degenerate) window. Only
    those cells are remapped — any window containing missing data stays NaN. The
    raw ``== 0.0`` comparisons are mask construction guarded by notna(), not alpha
    branch logic (a NaN stddev is explicitly "not degenerate": corr stays NaN).
    """
    c = correlation(x, y, d)
    sx = stddev(x, d)
    sy = stddev(y, d)
    degenerate = ((sx == 0.0) | (sy == 0.0)) & sx.notna() & sy.notna()
    return c.mask(c.isna() & degenerate, 0.0)


def alpha_076(p):
    p1 = rank(decay_linear(delta(p.vwap, 1), 12))
    p2 = ts_rank(decay_linear(
        ts_rank(correlation(p.ind(p.low), p.adv(81), 8), 20), 17), 19)
    return -1.0 * max_(p1, p2)


def alpha_077(p):
    p1 = rank(decay_linear(((p.high + p.low) / 2.0 + p.high) - (p.vwap + p.high), 20))
    p2 = rank(decay_linear(correlation((p.high + p.low) / 2.0, p.adv(40), 3), 6))
    return clean(min_(p1, p2))


def alpha_078(p):
    w = p.low * 0.352233 + p.vwap * (1.0 - 0.352233)
    p1 = rank(correlation(ts_sum(w, 20), ts_sum(p.adv(40), 20), 7))
    p2 = rank(correlation(rank(p.vwap), rank(p.volume), 6))
    return p1 ** p2


def alpha_079(p):
    w = p.close * 0.60733 + p.open * (1.0 - 0.60733)
    p1 = rank(delta(p.ind(w), 1))
    p2 = rank(correlation(ts_rank(p.vwap, 4), ts_rank(p.adv(150), 9), 15))
    return lt(p1, p2)


def alpha_080(p):
    w = p.open * 0.868128 + p.high * (1.0 - 0.868128)
    p1 = rank(sign(delta(p.ind(w), 4)))
    p2 = ts_rank(correlation(p.high, p.adv(10), 5), 6)
    return -1.0 * p1 ** p2


def alpha_081(p):
    inner = rank(rank(correlation(p.vwap, ts_sum(p.adv(10), 50), 8)) ** 4.0)
    p1 = rank(log(product(inner, 15)))
    p2 = rank(correlation(rank(p.vwap), rank(p.volume), 5))
    return -1.0 * lt(p1, p2)


def alpha_082(p):
    p1 = rank(decay_linear(delta(p.open, 1), 15))
    w = p.open * 0.634196 + p.open * (1.0 - 0.634196)
    p2 = ts_rank(decay_linear(correlation(p.ind(p.volume), w, 17), 7), 13)
    return -1.0 * min_(p1, p2)


def alpha_083(p):
    hl = clean((p.high - p.low) / (ts_sum(p.close, 5) / 5.0))
    num = rank(delay(hl, 2)) * rank(rank(p.volume))
    den = clean(hl / (p.vwap - p.close))
    return clean(num / den)


def alpha_084(p):
    return clean(signedpower(ts_rank(p.vwap - ts_max(p.vwap, 15), 21),
                             delta(p.close, 5)))


def alpha_085(p):
    w = p.high * 0.876703 + p.close * (1.0 - 0.876703)
    p1 = rank(correlation(w, p.adv(30), 10))
    p2 = rank(correlation(ts_rank((p.high + p.low) / 2.0, 4),
                          ts_rank(p.volume, 10), 7))
    return clean(p1 ** p2)


def alpha_086(p):
    p1 = ts_rank(correlation(p.close, ts_sum(p.adv(20), 15), 6), 20)
    p2 = rank((p.open + p.close) - (p.vwap + p.open))
    return -1.0 * lt(p1, p2)


def alpha_087(p):
    w = p.close * 0.369701 + p.vwap * (1.0 - 0.369701)
    p1 = rank(decay_linear(delta(w, 2), 3))
    p2 = ts_rank(decay_linear(
        abs_(correlation(p.ind(p.adv(81)), p.close, 13)), 5), 14)
    return -1.0 * max_(p1, p2)


def alpha_088(p):
    p1 = rank(decay_linear(
        (rank(p.open) + rank(p.low)) - (rank(p.high) + rank(p.close)), 8))
    p2 = ts_rank(decay_linear(
        correlation(ts_rank(p.close, 8), ts_rank(p.adv(60), 21), 8), 7), 3)
    return min_(p1, p2)


def alpha_089(p):
    w = p.low * 0.967285 + p.low * (1.0 - 0.967285)
    p1 = ts_rank(decay_linear(correlation(w, p.adv(10), 7), 6), 4)
    p2 = ts_rank(decay_linear(delta(p.ind(p.vwap), 3), 10), 15)
    return p1 - p2


def alpha_090(p):
    p1 = rank(p.close - ts_max(p.close, 5))
    p2 = ts_rank(correlation(p.ind(p.adv(40)), p.low, 5), 3)
    return -1.0 * p1 ** p2


def alpha_091(p):
    p1 = ts_rank(decay_linear(decay_linear(
        correlation(p.ind(p.close), p.volume, 10), 16), 4), 5)
    p2 = rank(decay_linear(correlation(p.vwap, p.adv(30), 4), 3))
    return -1.0 * (p1 - p2)


def alpha_092(p):
    cond = lt((p.high + p.low) / 2.0 + p.close, p.low + p.open)
    p1 = ts_rank(decay_linear(cond, 15), 19)
    p2 = ts_rank(decay_linear(_corr_dz(rank(p.low), rank(p.adv(30)), 8), 7), 7)
    return clean(min_(p1, p2))


def alpha_093(p):
    p1 = ts_rank(decay_linear(correlation(p.ind(p.vwap), p.adv(81), 17), 20), 8)
    w = p.close * 0.524434 + p.vwap * (1.0 - 0.524434)
    p2 = rank(decay_linear(delta(w, 3), 16))
    return clean(p1 / p2)


def alpha_094(p):
    p1 = rank(p.vwap - ts_min(p.vwap, 12))
    p2 = ts_rank(correlation(ts_rank(p.vwap, 20), ts_rank(p.adv(60), 4), 18), 3)
    return -1.0 * p1 ** p2


def alpha_095(p):
    p1 = rank(p.open - ts_min(p.open, 12))
    p2 = ts_rank(rank(correlation(
        ts_sum((p.high + p.low) / 2.0, 19), ts_sum(p.adv(40), 19), 13)) ** 5.0, 12)
    return clean(lt(p1, p2))


def alpha_096(p):
    p1 = ts_rank(decay_linear(_corr_dz(rank(p.vwap), rank(p.volume), 4), 4), 8)
    p2 = ts_rank(decay_linear(ts_argmax(
        _corr_dz(ts_rank(p.close, 7), ts_rank(p.adv(60), 4), 4), 13), 14), 13)
    return -1.0 * max_(p1, p2)


def alpha_097(p):
    w = p.low * 0.721001 + p.vwap * (1.0 - 0.721001)
    p1 = rank(decay_linear(delta(p.ind(w), 3), 20))
    p2 = ts_rank(decay_linear(ts_rank(
        correlation(ts_rank(p.low, 8), ts_rank(p.adv(60), 17), 5), 19), 16), 7)
    return -1.0 * (p1 - p2)


def alpha_098(p):
    p1 = rank(decay_linear(correlation(p.vwap, ts_sum(p.adv(5), 26), 5), 7))
    p2 = rank(decay_linear(ts_rank(ts_argmin(
        correlation(rank(p.open), rank(p.adv(15)), 21), 9), 7), 8))
    return p1 - p2


def alpha_099(p):
    p1 = rank(correlation(ts_sum((p.high + p.low) / 2.0, 20),
                          ts_sum(p.adv(60), 20), 9))
    p2 = rank(correlation(p.low, p.volume, 6))
    return clean(-1.0 * lt(p1, p2))


def alpha_100(p):
    inner = rank(clean(((p.close - p.low) - (p.high - p.close))
                       / (p.high - p.low)) * p.volume)
    part1 = 1.5 * scale(p.ind(p.ind(inner)))
    part2 = scale(p.ind(_corr_dz(p.close, rank(p.adv(20)), 5)
                        - rank(ts_argmin(p.close, 30))))
    return clean(-1.0 * (part1 - part2) * clean(p.volume / p.adv(20)))


def alpha_101(p):
    return clean((p.close - p.open) / ((p.high - p.low) + 0.001))


ALPHAS = {
    76: alpha_076, 77: alpha_077, 78: alpha_078, 79: alpha_079, 80: alpha_080,
    81: alpha_081, 82: alpha_082, 83: alpha_083, 84: alpha_084, 85: alpha_085,
    86: alpha_086, 87: alpha_087, 88: alpha_088, 89: alpha_089, 90: alpha_090,
    91: alpha_091, 92: alpha_092, 93: alpha_093, 94: alpha_094, 95: alpha_095,
    96: alpha_096, 97: alpha_097, 98: alpha_098, 99: alpha_099, 100: alpha_100,
    101: alpha_101,
}

_DZ_NOTE = ("degenerate (zero-variance) correlation windows evaluate to 0.0, "
            "not NaN — see module docstring (_corr_dz).")

META = {
    76: {"needs": ("vwap",)},
    92: {"notes": _DZ_NOTE},
    100: {"notes": _DZ_NOTE},
    77: {"needs": ("vwap",)},
    78: {"needs": ("vwap",)},
    79: {"needs": ("vwap",)},
    81: {"needs": ("vwap",)},
    83: {"needs": ("vwap",)},
    84: {"needs": ("vwap",)},
    86: {"needs": ("vwap",)},
    87: {"needs": ("vwap",)},
    89: {"needs": ("vwap",)},
    91: {"needs": ("vwap",)},
    93: {"needs": ("vwap",)},
    94: {"needs": ("vwap",)},
    96: {"needs": ("vwap",), "notes": _DZ_NOTE},
    97: {"needs": ("vwap",)},
    98: {"needs": ("vwap",)},
}
