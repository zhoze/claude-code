#!/usr/bin/env python3
"""
magic_lite — compact Python port of the Magic Trader Elite directional score
============================================================================

A faithful, self-contained re-implementation of the directional essentials of
the `magic.js` engine (territory, Magic Lines gate, trading zone, candle, Five
Ingredients, 0-100 Magic score) so the Overall pipeline can run all three lenses
in one Python program with no cross-language / cross-branch dependency.

The full browser engine + CLI (with the Magic Eight Dimensions risk model) lives
on the `claude/stock-screener-elite-magic-yHnEC` branch. This is the trading/
technical lens only. Educational — no buy/sell signals, not investment advice.

Required fields on the input dict: price, sma50, sma200, rsi14, beta,
perf_month, perf_ytd, volume (pe/roic/earn_yield optional, for display).
"""

import math


def _clamp(v, lo, hi):
    return max(lo, min(hi, v))


def _hash_unit(s):
    """Deterministic 0..1 from a string — mirrors magic.js hashUnit (FNV-1a)."""
    h = 2166136261
    for ch in s:
        h ^= ord(ch)
        h = (h * 16777619) & 0xFFFFFFFF
    return (h % 100000) / 100000.0


def compute_magic_lite(s):
    g = s.get
    price, sma50, sma200 = g("price"), g("sma50"), g("sma200")
    rsi, beta = g("rsi14", 50), g("beta", 1.0)
    perf_m, perf_y = g("perf_month", 0.0), g("perf_ytd", 0.0)

    dist50 = (price - sma50) / sma50 * 100
    dist200 = (price - sma200) / sma200 * 100
    golden = sma50 > sma200
    above_both = price > sma50 and price > sma200
    below_both = price < sma50 and price < sma200

    territory = "bull" if price >= sma200 else "bear"
    ml_gate = 1 if above_both else (-1 if below_both else 0)

    # Trading zone (7 lines -> 6 zones) using a hash-synthesized volatility, as
    # in magic.js (the demo dataset carries no ATR).
    atr_pct = round(1.0 + beta * 1.2 + _hash_unit(s.get("ticker", "")) * 1.6, 2)
    step = (atr_pct / 100) * price * 1.5
    zlines = [round(sma200 + k * step, 2) for k in (3, 2, 1, 0, -1, -2, -3)]
    zone = 6
    for i in range(len(zlines) - 1):
        if price >= zlines[i + 1]:
            zone = i + 1
            break
    if price >= zlines[0]:
        zone = 1

    health_black = _clamp(dist50 * 0.15 + perf_m * 0.4 + (1.5 if golden else -1.5), -8, 8)
    health_green = _clamp(2 + perf_m * 0.2 + (1.5 if above_both else 0), -5, 5)
    health_red = _clamp(2 - perf_m * 0.2 + (1.5 if below_both else 0), -5, 5)
    health_dir = "bull" if health_green >= health_red else "bear"

    dir_line = "green" if (price > sma50 and perf_m >= 0) else ("red" if (price < sma50 and perf_m < 0) else "none")
    spike = "green" if (perf_m >= 4 and above_both) else ("red" if (perf_m <= -4 and below_both) else "none")
    pure_health = 1 if (above_both and perf_y > 0 and 45 <= rsi <= 72) else (
        -1 if (below_both and perf_y < 0 and rsi <= 55) else 0)

    ribbon = "green" if (health_dir == "bull" and above_both) else (
        "red" if (health_dir == "bear" and below_both) else "black")

    if above_both and rsi >= 68 and perf_m < 2:
        candle = "golden"
    elif below_both and rsi <= 36:
        candle = "turquoise"
    elif above_both and ribbon == "green":
        candle = "green"
    elif below_both and ribbon == "red":
        candle = "red"
    elif abs(health_black) < 1.4 and not above_both and not below_both:
        candle = "neutral"
    elif health_dir == "bull" and not above_both:
        candle = "yellow"
    elif health_dir == "bear" and not below_both:
        candle = "indigo"
    else:
        candle = "neutral"

    ing_bull = sum([dir_line == "green", health_dir == "bull", spike == "green",
                    pure_health == 1, ribbon == "green"])
    ing_bear = sum([dir_line == "red", health_dir == "bear", spike == "red",
                    pure_health == -1, ribbon == "red"])

    magic_score = int(_clamp(round(50 + (ing_bull - ing_bear) * 8 + health_black * 2
                                   + (4 if territory == "bull" else -4)), 0, 100))

    return {
        "magic_score": magic_score, "territory": territory, "gate": ml_gate,
        "zone": zone, "candle": candle, "ribbon": ribbon,
        "ing_bull": ing_bull, "ing_bear": ing_bear,
        "dist200": round(dist200, 2), "health_black": round(health_black, 2),
    }


if __name__ == "__main__":
    # tiny smoke test mirroring the ADBE / AVGO reads
    adbe = {"ticker": "ADBE", "price": 204.02, "sma50": 244.89, "sma200": 298.34,
            "rsi14": 29.38, "beta": 1.40, "perf_month": -18, "perf_ytd": -30, "volume": 6857428}
    print("ADBE", compute_magic_lite(adbe))
