"""Alphas 1..25 of Kakushadze (2016), 101 Formulaic Alphas (arXiv:1601.00991)."""
from __future__ import annotations

import numpy as np
import pandas as pd

import ops
from ops import (clean, rank, delay, delta, correlation, covariance, stddev, ts_sum,
                 product, ts_min, ts_max, ts_argmax, ts_argmin, ts_rank, decay_linear,
                 scale, signedpower, sign, log, abs_, min_, max_, lt, le, gt, ge, eq,
                 or_, and_, not_, where)

FORMULAS = {
    1: "(rank(Ts_ArgMax(SignedPower(((returns < 0) ? stddev(returns, 20) : close), 2.), 5)) - 0.5)",
    2: "(-1 * correlation(rank(delta(log(volume), 2)), rank(((close - open) / open)), 6))",
    3: "(-1 * correlation(rank(open), rank(volume), 10))",
    4: "(-1 * Ts_Rank(rank(low), 9))",
    5: "(rank((open - (sum(vwap, 10) / 10))) * (-1 * abs(rank((close - vwap)))))",
    6: "(-1 * correlation(open, volume, 10))",
    7: "((adv20 < volume) ? ((-1 * ts_rank(abs(delta(close, 7)), 60)) * sign(delta(close, 7))) : (-1 * 1))",
    8: "(-1 * rank(((sum(open, 5) * sum(returns, 5)) - delay((sum(open, 5) * sum(returns, 5)), 10))))",
    9: "((0 < ts_min(delta(close, 1), 5)) ? delta(close, 1) : ((ts_max(delta(close, 1), 5) < 0) ? delta(close, 1) : (-1 * delta(close, 1))))",
    10: "rank(((0 < ts_min(delta(close, 1), 4)) ? delta(close, 1) : ((ts_max(delta(close, 1), 4) < 0) ? delta(close, 1) : (-1 * delta(close, 1)))))",
    11: "((rank(ts_max((vwap - close), 3)) + rank(ts_min((vwap - close), 3))) * rank(delta(volume, 3)))",
    12: "(sign(delta(volume, 1)) * (-1 * delta(close, 1)))",
    13: "(-1 * rank(covariance(rank(close), rank(volume), 5)))",
    14: "((-1 * rank(delta(returns, 3))) * correlation(open, volume, 10))",
    15: "(-1 * sum(rank(correlation(rank(high), rank(volume), 3)), 3))",
    16: "(-1 * rank(covariance(rank(high), rank(volume), 5)))",
    17: "(((-1 * rank(ts_rank(close, 10))) * rank(delta(delta(close, 1), 1))) * rank(ts_rank((volume / adv20), 5)))",
    18: "(-1 * rank(((stddev(abs((close - open)), 5) + (close - open)) + correlation(close, open, 10))))",
    19: "((-1 * sign(((close - delay(close, 7)) + delta(close, 7)))) * (1 + rank((1 + sum(returns, 250)))))",
    20: "(((-1 * rank((open - delay(high, 1)))) * rank((open - delay(close, 1)))) * rank((open - delay(low, 1))))",
    21: "((((sum(close, 8) / 8) + stddev(close, 8)) < (sum(close, 2) / 2)) ? (-1 * 1) : (((sum(close, 2) / 2) < ((sum(close, 8) / 8) - stddev(close, 8))) ? 1 : (((1 < (volume / adv20)) || ((volume / adv20) == 1)) ? 1 : (-1 * 1))))",
    22: "(-1 * (delta(correlation(high, volume, 5), 5) * rank(stddev(close, 20))))",
    23: "(((sum(high, 20) / 20) < high) ? (-1 * delta(high, 2)) : 0)",
    24: "((((delta((sum(close, 100) / 100), 100) / delay(close, 100)) < 0.05) || ((delta((sum(close, 100) / 100), 100) / delay(close, 100)) == 0.05)) ? (-1 * (close - ts_min(close, 100))) : (-1 * delta(close, 3)))",
    25: "rank(((((-1 * returns) * adv20) * vwap) * (high - close)))",
}


def alpha_001(p):
    cond = lt(p.returns, 0.0)
    return rank(ts_argmax(signedpower(where(cond, stddev(p.returns, 20), p.close), 2.0), 5)) - 0.5


def alpha_002(p):
    return clean(-1.0 * correlation(rank(delta(log(p.volume), 2)),
                                    rank(clean((p.close - p.open) / p.open)), 6))


def alpha_003(p):
    return -1.0 * correlation(rank(p.open), rank(p.volume), 10)


def alpha_004(p):
    return -1.0 * ts_rank(rank(p.low), 9)


def alpha_005(p):
    return clean(rank(p.open - ts_sum(p.vwap, 10) / 10.0)
                 * (-1.0 * abs_(rank(p.close - p.vwap))))


def alpha_006(p):
    return -1.0 * correlation(p.open, p.volume, 10)


def alpha_007(p):
    d7 = delta(p.close, 7)
    return where(lt(p.adv(20), p.volume),
                 (-1.0 * ts_rank(abs_(d7), 60)) * sign(d7),
                 -1.0)


def alpha_008(p):
    inner = ts_sum(p.open, 5) * ts_sum(p.returns, 5)
    return -1.0 * rank(inner - delay(inner, 10))


def alpha_009(p):
    d1 = delta(p.close, 1)
    return where(lt(0.0, ts_min(d1, 5)), d1,
                 where(lt(ts_max(d1, 5), 0.0), d1, -1.0 * d1))


def alpha_010(p):
    d1 = delta(p.close, 1)
    return rank(where(lt(0.0, ts_min(d1, 4)), d1,
                      where(lt(ts_max(d1, 4), 0.0), d1, -1.0 * d1)))


def alpha_011(p):
    vc = p.vwap - p.close
    return (rank(ts_max(vc, 3)) + rank(ts_min(vc, 3))) * rank(delta(p.volume, 3))


def alpha_012(p):
    return sign(delta(p.volume, 1)) * (-1.0 * delta(p.close, 1))


def alpha_013(p):
    return -1.0 * rank(covariance(rank(p.close), rank(p.volume), 5))


def alpha_014(p):
    return (-1.0 * rank(delta(p.returns, 3))) * correlation(p.open, p.volume, 10)


def alpha_015(p):
    return -1.0 * ts_sum(rank(correlation(rank(p.high), rank(p.volume), 3)), 3)


def alpha_016(p):
    return -1.0 * rank(covariance(rank(p.high), rank(p.volume), 5))


def alpha_017(p):
    return clean(((-1.0 * rank(ts_rank(p.close, 10)))
                  * rank(delta(delta(p.close, 1), 1)))
                 * rank(ts_rank(clean(p.volume / p.adv(20)), 5)))


def alpha_018(p):
    co = p.close - p.open
    return -1.0 * rank((stddev(abs_(co), 5) + co) + correlation(p.close, p.open, 10))


def alpha_019(p):
    return ((-1.0 * sign((p.close - delay(p.close, 7)) + delta(p.close, 7)))
            * (1.0 + rank(1.0 + ts_sum(p.returns, 250))))


def alpha_020(p):
    return ((-1.0 * rank(p.open - delay(p.high, 1)))
            * rank(p.open - delay(p.close, 1))
            * rank(p.open - delay(p.low, 1)))


def alpha_021(p):
    m8 = ts_sum(p.close, 8) / 8.0
    s8 = stddev(p.close, 8)
    m2 = ts_sum(p.close, 2) / 2.0
    vr = clean(p.volume / p.adv(20))
    inner = where(or_(lt(1.0, vr), eq(vr, 1.0)), 1.0, -1.0)
    return clean(where(lt(m8 + s8, m2), -1.0,
                       where(lt(m2, m8 - s8), 1.0, inner)))


def alpha_022(p):
    return -1.0 * (delta(correlation(p.high, p.volume, 5), 5) * rank(stddev(p.close, 20)))


def alpha_023(p):
    return clean(where(lt(ts_sum(p.high, 20) / 20.0, p.high),
                       -1.0 * delta(p.high, 2), 0.0))


def alpha_024(p):
    ratio = clean(delta(ts_sum(p.close, 100) / 100.0, 100) / delay(p.close, 100))
    cond = or_(lt(ratio, 0.05), eq(ratio, 0.05))
    return clean(where(cond,
                       -1.0 * (p.close - ts_min(p.close, 100)),
                       -1.0 * delta(p.close, 3)))


def alpha_025(p):
    return rank(((-1.0 * p.returns) * p.adv(20)) * p.vwap * (p.high - p.close))


ALPHAS = {
    1: alpha_001, 2: alpha_002, 3: alpha_003, 4: alpha_004, 5: alpha_005,
    6: alpha_006, 7: alpha_007, 8: alpha_008, 9: alpha_009, 10: alpha_010,
    11: alpha_011, 12: alpha_012, 13: alpha_013, 14: alpha_014, 15: alpha_015,
    16: alpha_016, 17: alpha_017, 18: alpha_018, 19: alpha_019, 20: alpha_020,
    21: alpha_021, 22: alpha_022, 23: alpha_023, 24: alpha_024, 25: alpha_025,
}

META = {
    5: {"needs": ("vwap",)},
    11: {"needs": ("vwap",)},
    25: {"needs": ("vwap",)},
}
