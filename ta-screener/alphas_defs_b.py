"""Alphas 26..50 of Kakushadze (2016), 101 Formulaic Alphas (arXiv:1601.00991)."""
from __future__ import annotations

import numpy as np
import pandas as pd

import ops
from ops import (clean, rank, delay, delta, correlation, covariance, stddev, ts_sum,
                 product, ts_min, ts_max, ts_argmax, ts_argmin, ts_rank, decay_linear,
                 scale, signedpower, sign, log, abs_, min_, max_, lt, le, gt, ge, eq,
                 or_, and_, not_, where)

FORMULAS = {
    26: "(-1 * ts_max(correlation(ts_rank(volume, 5), ts_rank(high, 5), 5), 3))",
    27: "((0.5 < rank((sum(correlation(rank(volume), rank(vwap), 6), 2) / 2.0))) ? (-1 * 1) : 1)",
    28: "scale(((correlation(adv20, low, 5) + ((high + low) / 2)) - close))",
    29: "(min(product(rank(rank(scale(log(sum(ts_min(rank(rank((-1 * rank(delta((close - 1), 5))))), 2), 1))))), 1), 5) + ts_rank(delay((-1 * returns), 6), 5))",
    30: "(((1.0 - rank(((sign((close - delay(close, 1))) + sign((delay(close, 1) - delay(close, 2)))) + sign((delay(close, 2) - delay(close, 3)))))) * sum(volume, 5)) / sum(volume, 20))",
    31: "((rank(rank(rank(decay_linear((-1 * rank(rank(delta(close, 10)))), 10)))) + rank((-1 * delta(close, 3)))) + sign(scale(correlation(adv20, low, 12))))",
    32: "(scale(((sum(close, 7) / 7) - close)) + (20 * scale(correlation(vwap, delay(close, 5), 230))))",
    33: "rank((-1 * ((1 - (open / close))^1)))",
    34: "rank(((1 - rank((stddev(returns, 2) / stddev(returns, 5)))) + (1 - rank(delta(close, 1)))))",
    35: "((Ts_Rank(volume, 32) * (1 - Ts_Rank(((close + high) - low), 16))) * (1 - Ts_Rank(returns, 32)))",
    36: "(((((2.21 * rank(correlation((close - open), delay(volume, 1), 15))) + (0.7 * rank((open - close)))) + (0.73 * rank(Ts_Rank(delay((-1 * returns), 6), 5)))) + rank(abs(correlation(vwap, adv20, 6)))) + (0.6 * rank((((sum(close, 200) / 200) - open) * (close - open)))))",
    37: "(rank(correlation(delay((open - close), 1), close, 200)) + rank((open - close)))",
    38: "((-1 * rank(Ts_Rank(close, 10))) * rank((close / open)))",
    39: "((-1 * rank((delta(close, 7) * (1 - rank(decay_linear((volume / adv20), 9)))))) * (1 + rank(sum(returns, 250))))",
    40: "((-1 * rank(stddev(high, 10))) * correlation(high, volume, 10))",
    41: "(((high * low)^0.5) - vwap)",
    42: "(rank((vwap - close)) / rank((vwap + close)))",
    43: "(ts_rank((volume / adv20), 20) * ts_rank((-1 * delta(close, 7)), 8))",
    44: "(-1 * correlation(high, rank(volume), 5))",
    45: "(-1 * ((rank((sum(delay(close, 5), 20) / 20)) * correlation(close, volume, 2)) * rank(correlation(sum(close, 5), sum(close, 20), 2))))",
    46: "((0.25 < (((delay(close, 20) - delay(close, 10)) / 10) - ((delay(close, 10) - close) / 10))) ? (-1 * 1) : (((((delay(close, 20) - delay(close, 10)) / 10) - ((delay(close, 10) - close) / 10)) < 0) ? 1 : ((-1 * 1) * (close - delay(close, 1)))))",
    47: "((((rank((1 / close)) * volume) / adv20) * ((high * rank((high - close))) / (sum(high, 5) / 5))) - rank((vwap - delay(vwap, 5))))",
    48: "(indneutralize(((correlation(delta(close, 1), delta(delay(close, 1), 1), 250) * delta(close, 1)) / close), IndClass.subindustry) / sum(((delta(close, 1) / delay(close, 1))^2), 250))",
    49: "(((((delay(close, 20) - delay(close, 10)) / 10) - ((delay(close, 10) - close) / 10)) < (-1 * 0.1)) ? 1 : ((-1 * 1) * (close - delay(close, 1))))",
    50: "(-1 * ts_max(rank(correlation(rank(volume), rank(vwap), 5)), 5))",
}


def alpha_026(p):
    return -1.0 * ts_max(correlation(ts_rank(p.volume, 5), ts_rank(p.high, 5), 5), 3)


def alpha_027(p):
    inner = rank(clean(ts_sum(correlation(rank(p.volume), rank(p.vwap), 6), 2) / 2.0))
    return where(lt(0.5, inner), -1.0, 1.0)


def alpha_028(p):
    return clean(scale((correlation(p.adv(20), p.low, 5) + (p.high + p.low) / 2.0) - p.close))


def alpha_029(p):
    inner = ts_min(rank(rank(-1.0 * rank(delta(p.close - 1.0, 5)))), 2)
    inner = rank(rank(scale(log(ts_sum(inner, 1)))))
    part1 = ts_min(product(inner, 1), 5)
    part2 = ts_rank(delay(-1.0 * p.returns, 6), 5)
    return part1 + part2


def alpha_030(p):
    signs = (sign(p.close - delay(p.close, 1))
             + sign(delay(p.close, 1) - delay(p.close, 2))
             + sign(delay(p.close, 2) - delay(p.close, 3)))
    return clean((1.0 - rank(signs)) * ts_sum(p.volume, 5) / ts_sum(p.volume, 20))


def alpha_031(p):
    p1 = rank(rank(rank(decay_linear(-1.0 * rank(rank(delta(p.close, 10))), 10))))
    p2 = rank(-1.0 * delta(p.close, 3))
    p3 = sign(scale(correlation(p.adv(20), p.low, 12)))
    return p1 + p2 + p3


def alpha_032(p):
    return clean(scale(ts_sum(p.close, 7) / 7.0 - p.close)
                 + 20.0 * scale(correlation(p.vwap, delay(p.close, 5), 230)))


def alpha_033(p):
    return clean(rank(-1.0 * (1.0 - clean(p.open / p.close)) ** 1.0))


def alpha_034(p):
    ratio = rank(clean(stddev(p.returns, 2) / stddev(p.returns, 5)))
    return rank((1.0 - ratio) + (1.0 - rank(delta(p.close, 1))))


def alpha_035(p):
    return (ts_rank(p.volume, 32)
            * (1.0 - ts_rank(p.close + p.high - p.low, 16))
            * (1.0 - ts_rank(p.returns, 32)))


def alpha_036(p):
    p1 = 2.21 * rank(correlation(p.close - p.open, delay(p.volume, 1), 15))
    p2 = 0.7 * rank(p.open - p.close)
    p3 = 0.73 * rank(ts_rank(delay(-1.0 * p.returns, 6), 5))
    p4 = rank(abs_(correlation(p.vwap, p.adv(20), 6)))
    p5 = 0.6 * rank((ts_sum(p.close, 200) / 200.0 - p.open) * (p.close - p.open))
    return clean(p1 + p2 + p3 + p4 + p5)


def alpha_037(p):
    return rank(correlation(delay(p.open - p.close, 1), p.close, 200)) + rank(p.open - p.close)


def alpha_038(p):
    return clean(-1.0 * rank(ts_rank(p.close, 10)) * rank(clean(p.close / p.open)))


def alpha_039(p):
    p1 = -1.0 * rank(delta(p.close, 7)
                     * (1.0 - rank(decay_linear(clean(p.volume / p.adv(20)), 9))))
    return clean(p1 * (1.0 + rank(ts_sum(p.returns, 250))))


def alpha_040(p):
    return -1.0 * rank(stddev(p.high, 10)) * correlation(p.high, p.volume, 10)


def alpha_041(p):
    return (p.high * p.low) ** 0.5 - p.vwap


def alpha_042(p):
    return clean(rank(p.vwap - p.close) / rank(p.vwap + p.close))


def alpha_043(p):
    return clean(ts_rank(clean(p.volume / p.adv(20)), 20)
                 * ts_rank(-1.0 * delta(p.close, 7), 8))


def alpha_044(p):
    return -1.0 * correlation(p.high, rank(p.volume), 5)


def alpha_045(p):
    return clean(-1.0 * (rank(ts_sum(delay(p.close, 5), 20) / 20.0)
                         * correlation(p.close, p.volume, 2)
                         * rank(correlation(ts_sum(p.close, 5), ts_sum(p.close, 20), 2))))


def alpha_046(p):
    m = clean((delay(p.close, 20) - delay(p.close, 10)) / 10.0
              - (delay(p.close, 10) - p.close) / 10.0)
    inner = where(lt(m, 0.0), 1.0, -1.0 * (p.close - delay(p.close, 1)))
    return clean(where(lt(0.25, m), -1.0, inner))


def alpha_047(p):
    part1 = clean(rank(clean(1.0 / p.close)) * p.volume / p.adv(20))
    part2 = clean(p.high * rank(p.high - p.close) / (ts_sum(p.high, 5) / 5.0))
    return clean(part1 * part2 - rank(p.vwap - delay(p.vwap, 5)))


def alpha_048(p):
    num = p.ind(clean(correlation(delta(p.close, 1), delta(delay(p.close, 1), 1), 250)
                      * delta(p.close, 1) / p.close))
    den = ts_sum(clean(delta(p.close, 1) / delay(p.close, 1)) ** 2.0, 250)
    return clean(num / den)


def alpha_049(p):
    m = clean((delay(p.close, 20) - delay(p.close, 10)) / 10.0
              - (delay(p.close, 10) - p.close) / 10.0)
    return clean(where(lt(m, -0.1), 1.0, -1.0 * (p.close - delay(p.close, 1))))


def alpha_050(p):
    return -1.0 * ts_max(rank(correlation(rank(p.volume), rank(p.vwap), 5)), 5)


ALPHAS = {
    26: alpha_026, 27: alpha_027, 28: alpha_028, 29: alpha_029, 30: alpha_030,
    31: alpha_031, 32: alpha_032, 33: alpha_033, 34: alpha_034, 35: alpha_035,
    36: alpha_036, 37: alpha_037, 38: alpha_038, 39: alpha_039, 40: alpha_040,
    41: alpha_041, 42: alpha_042, 43: alpha_043, 44: alpha_044, 45: alpha_045,
    46: alpha_046, 47: alpha_047, 48: alpha_048, 49: alpha_049, 50: alpha_050,
}

META = {
    27: {"needs": ("vwap",)},
    32: {"needs": ("vwap",)},
    36: {"needs": ("vwap",)},
    41: {"needs": ("vwap",)},
    42: {"needs": ("vwap",)},
    47: {"needs": ("vwap",)},
    50: {"needs": ("vwap",)},
}
