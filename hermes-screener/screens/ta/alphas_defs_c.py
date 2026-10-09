"""Alphas 51..75 of Kakushadze (2016), 101 Formulaic Alphas (arXiv:1601.00991).

Convention: the paper's fractional day counts (e.g. 3.92795, 17.9282) are rounded to
the nearest integer window, matching the widely used public implementations of the
101 alphas (rolling windows must be integral). The FORMULAS strings keep the paper's
verbatim fractional values for audit.

Documented deviation (alphas 68 and 75 only): correlation windows that are fully
observed but degenerate (zero variance on either side, pandas 0/0 -> NaN) evaluate
to 0.0 via alphas_defs_d._corr_dz — see that module's docstring for the full
rationale. Both alphas correlate cross-sectional ranks of level series (rank(high),
rank(low), rank(adv15), rank(adv50)); on narrow universes those pct-ranks are
quantized to k/N and stay exactly constant over the 9/12-day correlation windows for
most names, so plain rolling Pearson correlation is 0/0 -> NaN and the frozen
any-NaN window ops wipe the nested ts_rank/rank chain to near-zero coverage
(alpha 68 reaches 0% on the 400-day synthetic fixture and still fails the 600-day
fallback). Missing data still propagates NaN.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

import ops
from ops import (clean, rank, delay, delta, correlation, covariance, stddev, ts_sum,
                 product, ts_min, ts_max, ts_argmax, ts_argmin, ts_rank, decay_linear,
                 scale, signedpower, sign, log, abs_, min_, max_, lt, le, gt, ge, eq,
                 or_, and_, not_, where)
from alphas_defs_d import _corr_dz

FORMULAS = {
    51: "(((((delay(close, 20) - delay(close, 10)) / 10) - ((delay(close, 10) - close) / 10)) < (-1 * 0.05)) ? 1 : ((-1 * 1) * (close - delay(close, 1))))",
    52: "((((-1 * ts_min(low, 5)) + delay(ts_min(low, 5), 5)) * rank(((sum(returns, 240) - sum(returns, 20)) / 220))) * ts_rank(volume, 5))",
    53: "(-1 * delta((((close - low) - (high - close)) / (close - low)), 9))",
    54: "((-1 * ((low - close) * (open^5))) / ((low - high) * (close^5)))",
    55: "(-1 * correlation(rank(((close - ts_min(low, 12)) / (ts_max(high, 12) - ts_min(low, 12)))), rank(volume), 6))",
    56: "(0 - (1 * (rank((sum(returns, 10) / sum(sum(returns, 2), 3))) * rank((returns * cap)))))",
    57: "(0 - (1 * ((close - vwap) / decay_linear(rank(ts_argmax(close, 30)), 2))))",
    58: "(-1 * Ts_Rank(decay_linear(correlation(IndNeutralize(vwap, IndClass.sector), volume, 3.92795), 7.89291), 5.50322))",
    59: "(-1 * Ts_Rank(decay_linear(correlation(IndNeutralize(((vwap * 0.728317) + (vwap * (1 - 0.728317))), IndClass.industry), volume, 4.25197), 16.2289), 8.19648))",
    60: "(0 - (1 * ((2 * scale(rank(((((close - low) - (high - close)) / (high - low)) * volume)))) - scale(rank(ts_argmax(close, 10))))))",
    61: "(rank((vwap - ts_min(vwap, 16.1219))) < rank(correlation(vwap, adv180, 17.9282)))",
    62: "((rank(correlation(vwap, sum(adv20, 22.4101), 9.91009)) < rank(((rank(open) + rank(open)) < (rank(((high + low) / 2)) + rank(high))))) * -1)",
    63: "((rank(decay_linear(delta(IndNeutralize(close, IndClass.industry), 2.25164), 8.22237)) - rank(decay_linear(correlation(((vwap * 0.318108) + (open * (1 - 0.318108))), sum(adv180, 37.2467), 13.557), 12.2883))) * -1)",
    64: "((rank(correlation(sum(((open * 0.178404) + (low * (1 - 0.178404))), 12.7054), sum(adv120, 12.7054), 16.6208)) < rank(delta(((((high + low) / 2) * 0.178404) + (vwap * (1 - 0.178404))), 3.69741))) * -1)",
    65: "((rank(correlation(((open * 0.00817205) + (vwap * (1 - 0.00817205))), sum(adv60, 8.6911), 6.40374)) < rank((open - ts_min(open, 13.635)))) * -1)",
    66: "((rank(decay_linear(delta(vwap, 3.51013), 7.23052)) + Ts_Rank(decay_linear(((((low * 0.96633) + (low * (1 - 0.96633))) - vwap) / (open - ((high + low) / 2))), 11.4157), 6.72611)) * -1)",
    67: "((rank((high - ts_min(high, 2.14593)))^rank(correlation(IndNeutralize(vwap, IndClass.sector), IndNeutralize(adv20, IndClass.subindustry), 6.02936))) * -1)",
    68: "((Ts_Rank(correlation(rank(high), rank(adv15), 8.91644), 13.9333) < rank(delta(((close * 0.518371) + (low * (1 - 0.518371))), 1.06157))) * -1)",
    69: "((rank(ts_max(delta(IndNeutralize(vwap, IndClass.industry), 2.72412), 4.79344))^Ts_Rank(correlation(((close * 0.490655) + (vwap * (1 - 0.490655))), adv20, 4.92416), 9.0615)) * -1)",
    70: "((rank(delta(vwap, 1.29456))^Ts_Rank(correlation(IndNeutralize(close, IndClass.industry), adv50, 17.8256), 17.9171)) * -1)",
    71: "max(Ts_Rank(decay_linear(correlation(Ts_Rank(close, 3.43976), Ts_Rank(adv180, 12.0647), 18.0175), 4.20501), 15.6948), Ts_Rank(decay_linear((rank(((low + open) - (vwap + vwap)))^2), 16.4662), 4.4388))",
    72: "(rank(decay_linear(correlation(((high + low) / 2), adv40, 8.93345), 10.1519)) / rank(decay_linear(correlation(Ts_Rank(vwap, 3.72469), Ts_Rank(volume, 18.5188), 6.86671), 2.95011)))",
    73: "(max(rank(decay_linear(delta(vwap, 4.72775), 2.91864)), Ts_Rank(decay_linear(((delta(((open * 0.147155) + (low * (1 - 0.147155))), 2.03608) / ((open * 0.147155) + (low * (1 - 0.147155)))) * -1), 3.33829), 16.7411)) * -1)",
    74: "((rank(correlation(close, sum(adv30, 37.4843), 15.1365)) < rank(correlation(rank(((high * 0.0261661) + (vwap * (1 - 0.0261661)))), rank(volume), 11.4791))) * -1)",
    75: "(rank(correlation(vwap, volume, 4.24304)) < rank(correlation(rank(low), rank(adv50), 12.4413)))",
}


def alpha_051(p):
    m = clean((delay(p.close, 20) - delay(p.close, 10)) / 10.0
              - (delay(p.close, 10) - p.close) / 10.0)
    return clean(where(lt(m, -0.05), 1.0, -1.0 * (p.close - delay(p.close, 1))))


def alpha_052(p):
    part1 = -1.0 * ts_min(p.low, 5) + delay(ts_min(p.low, 5), 5)
    part2 = rank(clean((ts_sum(p.returns, 240) - ts_sum(p.returns, 20)) / 220.0))
    return clean(part1 * part2 * ts_rank(p.volume, 5))


def alpha_053(p):
    inner = clean(((p.close - p.low) - (p.high - p.close)) / (p.close - p.low))
    return clean(-1.0 * delta(inner, 9))


def alpha_054(p):
    return clean((-1.0 * (p.low - p.close) * p.open ** 5.0)
                 / ((p.low - p.high) * p.close ** 5.0))


def alpha_055(p):
    stoch = clean((p.close - ts_min(p.low, 12))
                  / (ts_max(p.high, 12) - ts_min(p.low, 12)))
    return clean(-1.0 * correlation(rank(stoch), rank(p.volume), 6))


def alpha_056(p):
    r1 = rank(clean(ts_sum(p.returns, 10) / ts_sum(ts_sum(p.returns, 2), 3)))
    return clean(-1.0 * r1 * rank(p.returns * p.cap_frame()))


def alpha_057(p):
    return clean(-1.0 * (p.close - p.vwap)
                 / decay_linear(rank(ts_argmax(p.close, 30)), 2))


def alpha_058(p):
    return -1.0 * ts_rank(decay_linear(correlation(p.ind(p.vwap), p.volume, 4), 8), 6)


def alpha_059(p):
    w = p.vwap * 0.728317 + p.vwap * (1.0 - 0.728317)
    return -1.0 * ts_rank(decay_linear(correlation(p.ind(w), p.volume, 4), 16), 8)


def alpha_060(p):
    inner = clean(((p.close - p.low) - (p.high - p.close)) / (p.high - p.low)) * p.volume
    return clean(-1.0 * (2.0 * scale(rank(inner))
                         - scale(rank(ts_argmax(p.close, 10)))))


def alpha_061(p):
    return lt(rank(p.vwap - ts_min(p.vwap, 16)),
              rank(correlation(p.vwap, p.adv(180), 18)))


def alpha_062(p):
    p1 = rank(correlation(p.vwap, ts_sum(p.adv(20), 22), 10))
    inner = lt(rank(p.open) + rank(p.open),
               rank((p.high + p.low) / 2.0) + rank(p.high))
    return clean(-1.0 * lt(p1, rank(inner)))


def alpha_063(p):
    p1 = rank(decay_linear(delta(p.ind(p.close), 2), 8))
    w = p.vwap * 0.318108 + p.open * (1.0 - 0.318108)
    p2 = rank(decay_linear(correlation(w, ts_sum(p.adv(180), 37), 14), 12))
    return -1.0 * (p1 - p2)


def alpha_064(p):
    w1 = p.open * 0.178404 + p.low * (1.0 - 0.178404)
    p1 = rank(correlation(ts_sum(w1, 13), ts_sum(p.adv(120), 13), 17))
    w2 = ((p.high + p.low) / 2.0) * 0.178404 + p.vwap * (1.0 - 0.178404)
    p2 = rank(delta(w2, 4))
    return clean(-1.0 * lt(p1, p2))


def alpha_065(p):
    w = p.open * 0.00817205 + p.vwap * (1.0 - 0.00817205)
    p1 = rank(correlation(w, ts_sum(p.adv(60), 9), 6))
    p2 = rank(p.open - ts_min(p.open, 14))
    return -1.0 * lt(p1, p2)


def alpha_066(p):
    p1 = rank(decay_linear(delta(p.vwap, 4), 7))
    inner = clean(((p.low * 0.96633 + p.low * (1.0 - 0.96633)) - p.vwap)
                  / (p.open - (p.high + p.low) / 2.0))
    p2 = ts_rank(decay_linear(inner, 11), 7)
    return clean(-1.0 * (p1 + p2))


def alpha_067(p):
    p1 = rank(p.high - ts_min(p.high, 2))
    p2 = rank(correlation(p.ind(p.vwap), p.ind(p.adv(20)), 6))
    return -1.0 * p1 ** p2


def alpha_068(p):
    p1 = ts_rank(_corr_dz(rank(p.high), rank(p.adv(15)), 9), 14)
    p2 = rank(delta(p.close * 0.518371 + p.low * (1.0 - 0.518371), 1))
    return -1.0 * lt(p1, p2)


def alpha_069(p):
    p1 = rank(ts_max(delta(p.ind(p.vwap), 3), 5))
    w = p.close * 0.490655 + p.vwap * (1.0 - 0.490655)
    p2 = ts_rank(correlation(w, p.adv(20), 5), 9)
    return -1.0 * p1 ** p2


def alpha_070(p):
    p1 = rank(delta(p.vwap, 1))
    p2 = ts_rank(correlation(p.ind(p.close), p.adv(50), 18), 18)
    return -1.0 * p1 ** p2


def alpha_071(p):
    p1 = ts_rank(decay_linear(
        correlation(ts_rank(p.close, 3), ts_rank(p.adv(180), 12), 18), 4), 16)
    p2 = ts_rank(decay_linear(
        rank((p.low + p.open) - (p.vwap + p.vwap)) ** 2.0, 16), 4)
    return max_(p1, p2)


def alpha_072(p):
    num = rank(decay_linear(correlation((p.high + p.low) / 2.0, p.adv(40), 9), 10))
    den = rank(decay_linear(
        correlation(ts_rank(p.vwap, 4), ts_rank(p.volume, 19), 7), 3))
    return clean(num / den)


def alpha_073(p):
    p1 = rank(decay_linear(delta(p.vwap, 5), 3))
    w = p.open * 0.147155 + p.low * (1.0 - 0.147155)
    p2 = ts_rank(decay_linear(clean(delta(w, 2) / w) * -1.0, 3), 17)
    return clean(-1.0 * max_(p1, p2))


def alpha_074(p):
    p1 = rank(correlation(p.close, ts_sum(p.adv(30), 37), 15))
    w = p.high * 0.0261661 + p.vwap * (1.0 - 0.0261661)
    p2 = rank(correlation(rank(w), rank(p.volume), 11))
    return -1.0 * lt(p1, p2)


def alpha_075(p):
    return lt(rank(correlation(p.vwap, p.volume, 4)),
              rank(_corr_dz(rank(p.low), rank(p.adv(50)), 12)))


ALPHAS = {
    51: alpha_051, 52: alpha_052, 53: alpha_053, 54: alpha_054, 55: alpha_055,
    56: alpha_056, 57: alpha_057, 58: alpha_058, 59: alpha_059, 60: alpha_060,
    61: alpha_061, 62: alpha_062, 63: alpha_063, 64: alpha_064, 65: alpha_065,
    66: alpha_066, 67: alpha_067, 68: alpha_068, 69: alpha_069, 70: alpha_070,
    71: alpha_071, 72: alpha_072, 73: alpha_073, 74: alpha_074, 75: alpha_075,
}

META = {
    57: {"needs": ("vwap",)},
    58: {"needs": ("vwap",)},
    59: {"needs": ("vwap",)},
    61: {"needs": ("vwap",)},
    62: {"needs": ("vwap",)},
    63: {"needs": ("vwap",)},
    64: {"needs": ("vwap",)},
    65: {"needs": ("vwap",)},
    66: {"needs": ("vwap",)},
    67: {"needs": ("vwap",)},
    68: {"notes": "degenerate (zero-variance) correlation windows evaluate to 0.0, "
                  "not NaN — see alphas_defs_d._corr_dz and module docstrings."},
    69: {"needs": ("vwap",)},
    70: {"needs": ("vwap",)},
    71: {"needs": ("vwap",)},
    72: {"needs": ("vwap",)},
    73: {"needs": ("vwap",)},
    74: {"needs": ("vwap",)},
    75: {"needs": ("vwap",),
         "notes": "degenerate (zero-variance) correlation windows evaluate to 0.0, "
                  "not NaN — see alphas_defs_d._corr_dz and module docstrings."},
}
