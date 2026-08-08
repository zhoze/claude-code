#!/usr/bin/env python3
"""Elite Magic Trader — full Python port of the screener engine (magic.js).

A faithful *interpretation* of the indicators described in the MetaStock
"Magic Trader(R) Elite" add-on manual (Save Dollar Enterprises, 2019),
reimplemented over the fields available in the bundled dataset.

IMPORTANT: MetaStock's actual Magic Trader formulas are proprietary and not
published. This engine reproduces the manual's *concepts, color scheme, and
decision rules* from price, moving averages, RSI, momentum, volume and beta.
It is a teaching model, not a replica of the closed-source indicators.

Signals implemented (mirrors magic.js one-for-one, including its JS rounding
and the deterministic hash used to synthesize demo volatility fields, so the
Python and JavaScript engines produce identical numbers for identical input):
  - Magic Blue Line territory, Magic Lines gate, 6-zone Magic Trading Zone
  - Health Warning, Volatility Combinator, Directional Lines, Price Spikes
  - Pure Health, HTR Ribbon + elapsed-time risk, Magic Candle colors
  - DSPR Price Preceptors (a..f), Five Ingredients + entry trigger
  - Weekly->Daily multi-timeframe alignment, Magic Score (0..100)
  - Magic Eight Dimensions(SM) weighted risk (0..100), Bond Inversions

Usage:
  magic_screener.py <TICKER>              read one ticker from bundled demo data
  magic_screener.py <TICKER> --live       fetch fresh data from FMP (FMP_API_KEY)
  magic_screener.py <TICKER> --price 204 --sma50 244.9 --sma200 298.3 \
        --rsi 29.4 --beta 1.4 --perf-month -18 --perf-ytd -30 \
        --volume 6857428 --pe 11.67 --roic 59.7
  magic_screener.py --screen [--live] [--csv out.csv] [--sort magic|risk] \
        [--entry-only] [--min-ing N] [--side bull|bear]
  magic_screener.py --list

Educational risk model — gives NO buy/sell signals. Not investment advice.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import os
import re
import sys
import urllib.request

# --------------------------------------------------------------------------- #
# JS-compatible numeric helpers
# --------------------------------------------------------------------------- #

def clamp(v: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, v))


def js_round(v: float, d: int = 0) -> float:
    """JavaScript Math.round semantics: half rounds toward +infinity
    (Python's round() uses banker's rounding, which would drift from magic.js)."""
    scaled = v * 10 ** d
    out = math.floor(scaled + 0.5) / 10 ** d
    return int(out) if d == 0 else out


def js_num(v) -> str:
    """Render a number the way JS stringifies it: whole floats lose the .0."""
    if isinstance(v, float) and v.is_integer():
        return str(int(v))
    return str(v)


def hash_unit(text: str) -> float:
    """Deterministic 0..1 value from a string (FNV-1a, 32-bit, as in magic.js).
    Used only to synthesize the volatility / relative-volume fields the demo
    dataset doesn't carry."""
    h = 2166136261
    for ch in text:
        h ^= ord(ch)
        h = (h * 16777619) & 0xFFFFFFFF  # JS Math.imul + >>>0
    return (h % 100000) / 100000


# --------------------------------------------------------------------------- #
# Master color -> meaning maps (from the manual's color scheme)
# --------------------------------------------------------------------------- #

CANDLE_META = {
    "turquoise": {"label": "Turquoise", "glyph": "▲", "side": "bull", "desc": "Sudden bullish reversal — institutions take the long side"},
    "green":     {"label": "Bull",      "glyph": "▲", "side": "bull", "desc": "Confirmed bullish (Close above both Magic Lines)"},
    "yellow":    {"label": "Yellow",    "glyph": "△", "side": "bull", "desc": "Bull warning / pre-entry — wait for Magic Lines confirmation"},
    "neutral":   {"label": "Neutral",   "glyph": "◇", "side": "neutral", "desc": "Equilibrium — believers exhausted, trend may flip or resume"},
    "indigo":    {"label": "Indigo",    "glyph": "▽", "side": "bear", "desc": "Bear warning / pre-entry — wait for Magic Lines confirmation"},
    "golden":    {"label": "Golden",    "glyph": "▼", "side": "bear", "desc": "Sudden bearish reversal — institutions take the short side"},
    "red":       {"label": "Bear",      "glyph": "▼", "side": "bear", "desc": "Confirmed bearish (Close below both Magic Lines)"},
}

DSPR_META = {
    "a": {"code": 1, "side": "bull", "desc": "Type a — bullish believers, prices dropping (shakeout / pullback)"},
    "b": {"code": 2, "side": "bull", "desc": "Type b — bullish believers, prices rising (trend fully charged)"},
    "c": {"code": 3, "side": "bull", "desc": "Type c — bullish believers, prices rising (public piling in / extended)"},
    "d": {"code": 4, "side": "bull", "desc": "Type d — bullish believers, prices dropping after a rise (profit-taking near top)"},
    "e": {"code": 5, "side": "bear", "desc": "Type e — bearish believers, prices dropping (downtrend in control)"},
    "f": {"code": 6, "side": "bear", "desc": "Type f — bearish believers, prices rising (counter-bounce / cover)"},
}


# --------------------------------------------------------------------------- #
# Per-stock signal computation (port of magic.js computeMagic)
# --------------------------------------------------------------------------- #

def compute_magic(s: dict) -> dict:
    # --- synthesized demo fields (documented as illustrative) --------------
    h1 = hash_unit(s["ticker"])
    h2 = hash_unit(s["ticker"] + "vol")
    atr_pct = js_round(1.0 + s["beta"] * 1.2 + h1 * 1.6, 2)                  # Pure Volatility %
    rel_vol = js_round(0.7 + h2 * 1.8 + (0.6 if abs(s["perfMonth"]) > 5 else 0), 2)  # vs 20d avg

    # --- geometry vs moving averages ---------------------------------------
    dist_ma50 = (s["price"] - s["sma50"]) / s["sma50"] * 100
    dist_ma200 = (s["price"] - s["sma200"]) / s["sma200"] * 100
    golden = s["sma50"] > s["sma200"]
    above_both = s["price"] > s["sma50"] and s["price"] > s["sma200"]
    below_both = s["price"] < s["sma50"] and s["price"] < s["sma200"]

    # --- 1. Magic Blue Line (territory) ------------------------------------
    territory = "bull" if s["price"] >= s["sma200"] else "bear"
    stair_step = ("up" if golden and territory == "bull"
                  else "down" if not golden and territory == "bear" else "mixed")

    # --- 2. Magic Lines (green = higher line, red = lower) ------------------
    magic_green = max(s["sma50"], s["sma200"])
    magic_red = min(s["sma50"], s["sma200"])
    ml_gate = 1 if above_both else -1 if below_both else 0  # confirmation gate

    # --- 3. Magic Trading Zone (7 lines -> 6 zones) -------------------------
    step = (atr_pct / 100) * s["price"] * 1.5
    zlines = [js_round(s["sma200"] + k * step, 2) for k in (3, 2, 1, 0, -1, -2, -3)]  # MZL-1..7
    zone = 6
    for i in range(len(zlines) - 1):
        if s["price"] >= zlines[i + 1]:
            zone = i + 1
            break
    if s["price"] >= zlines[0]:
        zone = 1  # above the top line

    # --- 4. Health Warning Indicator ----------------------------------------
    health_black = clamp(dist_ma50 * 0.15 + s["perfMonth"] * 0.4 + (1.5 if golden else -1.5), -8, 8)
    health_green = clamp(2 + s["perfMonth"] * 0.2 + (1.5 if above_both else 0), -5, 5)
    health_red = clamp(2 - s["perfMonth"] * 0.2 + (1.5 if below_both else 0), -5, 5)
    health_dir = "bull" if health_green >= health_red else "bear"

    # --- 5. Volatility Combinator (VLL1 hist vs VLL2 blue line) --------------
    vll2 = atr_pct
    vll1 = js_round(atr_pct * (1 + (s["perfMonth"] / 100) * 3), 2)
    vola_bias = "call" if vll1 >= vll2 else "put"  # call bias = bullish
    ex_vola = atr_pct > 4.2
    low_risk = atr_pct < 2.6 and abs(health_black) < 2

    # --- 6. Directional Line -------------------------------------------------
    dir_line = ("green" if s["price"] > s["sma50"] and s["perfMonth"] >= 0
                else "red" if s["price"] < s["sma50"] and s["perfMonth"] < 0 else "none")

    # --- 7. Entry / Price Spike ---------------------------------------------
    spike = ("green" if s["perfMonth"] >= 4 and above_both
             else "red" if s["perfMonth"] <= -4 and below_both else "none")

    # --- 8. Pure Health ------------------------------------------------------
    pure_health = (1 if above_both and s["perfYTD"] > 0 and 45 <= s["rsi14"] <= 72
                   else -1 if below_both and s["perfYTD"] < 0 and s["rsi14"] <= 55 else 0)

    # --- 9. HTR Ribbon + elapsed-time risk number ----------------------------
    ribbon = "black"
    if health_dir == "bull" and above_both:
        ribbon = "green"
    elif health_dir == "bear" and below_both:
        ribbon = "red"
    ribbon_risk = 0  # green: +1..+7; red: -1..-7
    if ribbon == "green":
        ribbon_risk = int(clamp(js_round(1 + dist_ma200 / 3 + max(0, (s["rsi14"] - 50) / 6)), 1, 7))
    elif ribbon == "red":
        ribbon_risk = -int(clamp(js_round(1 + -dist_ma200 / 3 + max(0, (50 - s["rsi14"]) / 6)), 1, 7))

    # --- 10. Magic Candle color ----------------------------------------------
    # Reversal/warning candles take precedence at extremes (they are the
    # manual's pre-entry signals at tops and bottoms); confirmed bull/bear
    # candles apply once price is cleanly inside Magic Lines territory.
    if above_both and s["rsi14"] >= 68 and s["perfMonth"] < 2:
        candle = "golden"       # overbought uptrend stalling -> sudden bear-reversal risk
    elif below_both and s["rsi14"] <= 36:
        candle = "turquoise"    # washed-out downtrend -> sudden bull-reversal setup
    elif above_both and ribbon == "green":
        candle = "green"        # confirmed bull
    elif below_both and ribbon == "red":
        candle = "red"          # confirmed bear
    elif abs(health_black) < 1.4 and not above_both and not below_both:
        candle = "neutral"      # equilibrium between the lines
    elif health_dir == "bull" and not above_both:
        candle = "yellow"       # bull pre-entry warning
    elif health_dir == "bear" and not below_both:
        candle = "indigo"       # bear pre-entry warning
    else:
        candle = "neutral"

    # --- 11. DSPR Price Preceptor --------------------------------------------
    if territory == "bull":
        if s["perfMonth"] >= 0:
            dspr = "c" if s["rsi14"] >= 70 else "b"
        else:
            dspr = "d" if s["rsi14"] >= 60 else "a"
    else:
        dspr = "e" if s["perfMonth"] < 0 else "f"

    # --- 12. Five Ingredients alignment --------------------------------------
    ing_bull = sum([dir_line == "green", health_dir == "bull", spike == "green",
                    pure_health == 1, ribbon == "green"])
    ing_bear = sum([dir_line == "red", health_dir == "bear", spike == "red",
                    pure_health == -1, ribbon == "red"])
    vol_qual = rel_vol >= 1.5           # VQua — unusually high volume
    vol_pass = s["volume"] > 500_000    # base volume filter from the explorations
    bull_candle = candle in ("yellow", "turquoise", "green", "neutral")
    bear_candle = candle in ("indigo", "golden", "red", "neutral")
    entry_trigger = ("bull" if ing_bull == 5 and vol_pass and bull_candle
                     else "bear" if ing_bear == 5 and vol_pass and bear_candle else "none")

    # --- Multi-timeframe: weekly trend (higher TF) -> daily 5 ingredients ----
    # The canonical Magic Trader template works biggest-to-smallest timeframe:
    # confirm the weekly trend first, then take the daily 5-ingredient entry
    # only in that direction. Weekly trend here = the long-horizon Blue Line
    # territory (price vs 200-MA) combined with year-to-date momentum.
    weekly_trend = ("bull" if s["price"] >= s["sma200"] and s["perfYTD"] > 0
                    else "bear" if s["price"] < s["sma200"] and s["perfYTD"] < 0 else "neutral")
    mtf_align = ("long" if weekly_trend == "bull" and ing_bull >= 4 and vol_pass
                 else "short" if weekly_trend == "bear" and ing_bear >= 4 and vol_pass else "none")

    # --- Magic Bull Score: 0..100 directional conviction (50 = neutral) ------
    magic_score = int(clamp(
        js_round(50 + (ing_bull - ing_bear) * 8 + health_black * 2 + (4 if territory == "bull" else -4)),
        0, 100))

    out = dict(s)
    out.update(
        atrPct=atr_pct, relVol=rel_vol,
        distMA50=js_round(dist_ma50, 2), distMA200=js_round(dist_ma200, 2),
        golden=golden, aboveBoth=above_both, belowBoth=below_both,
        territory=territory, stairStep=stair_step,
        magicGreen=js_round(magic_green, 2), magicRed=js_round(magic_red, 2), mlGate=ml_gate,
        zone=zone, zlines=zlines,
        healthBlack=js_round(health_black, 2), healthGreen=js_round(health_green, 2),
        healthRed=js_round(health_red, 2), healthDir=health_dir,
        vll1=vll1, vll2=vll2, volaBias=vola_bias, exVola=ex_vola, lowRisk=low_risk,
        dirLine=dir_line, spike=spike, pureHealth=pure_health,
        ribbon=ribbon, ribbonRisk=ribbon_risk,
        candle=candle,
        dspr=dspr, dsprCode=DSPR_META[dspr]["code"],
        ingBull=ing_bull, ingBear=ing_bear, volQual=vol_qual, volPass=vol_pass,
        entryTrigger=entry_trigger,
        weeklyTrend=weekly_trend, mtfAlign=mtf_align,
        magicScore=magic_score,
    )

    # --- Magic Eight Dimensions(SM) — weighted multi-dimensional risk read ---
    dims, dim_risk = compute_dimensions(out)
    out["dims"] = dims
    out["dimRisk"] = dim_risk
    return out


# --------------------------------------------------------------------------- #
# Magic Eight Dimensions(SM) of Markets and Securities (per the Overview doc)
#
# The product is "designed to identify risk on a weighted basis" — not to
# emit buy/sell signals. This maps the engine's signals onto the eight
# canonical risk dimensions and rolls them up into a 0-100 weighted risk
# read (higher = more risk), orthogonal to the directional Magic score.
# --------------------------------------------------------------------------- #

MAGIC_DIMENSIONS = [
    {"num": 1, "key": "dspr",        "name": "Dynamic Sectional Price Risk"},
    {"num": 2, "key": "vertical",    "name": "Vertical Risk"},
    {"num": 3, "key": "htr",         "name": "Horizontal Time Risk"},
    {"num": 4, "key": "health",      "name": "Health Risk"},
    {"num": 5, "key": "spot",        "name": "Sudden Spot Risk"},
    {"num": 6, "key": "trendchange", "name": "Trend Change Risk & Warnings"},
    {"num": 7, "key": "special",     "name": "Special Conditional Risks"},
    {"num": 8, "key": "fundamental", "name": "Fundamental Risk"},
]
LEVEL_VAL = {"low": 1, "elevated": 2, "high": 3, "unknown": 2}


def compute_dimensions(r: dict) -> tuple[list[dict], int]:
    dims: list[dict] = []

    def add(key: str, level: str, note: str) -> None:
        meta = next(d for d in MAGIC_DIMENSIONS if d["key"] == key)
        dims.append({"num": meta["num"], "key": key, "name": meta["name"],
                     "level": level, "note": note})

    # 1. Dynamic Sectional Price Risk — the price-preceptor cycle position
    dspr_high = r["dspr"] in ("c", "d", "f")
    add("dspr", "high" if dspr_high else "elevated" if r["dspr"] == "a" else "low",
        f"Type {r['dspr']} ({r['dspr']}={r['dsprCode']})")

    # 2. Vertical Risk — how vertically extended price is within its zones
    add("vertical",
        "high" if r["zone"] in (1, 6) else "elevated" if r["zone"] in (2, 5) else "low",
        f"Zone {r['zone']}; {'+' if r['distMA200'] >= 0 else ''}{js_num(r['distMA200'])}% vs 200-MA")

    # 3. Horizontal Time Risk — elapsed-time risk since the ribbon flipped
    ar = abs(r["ribbonRisk"])
    add("htr",
        "high" if ar >= 6 else "elevated" if ar >= 3 else "elevated" if r["ribbon"] == "black" else "low",
        f"Ribbon {r['ribbon']}" + (f" {'+' if r['ribbonRisk'] > 0 else ''}{r['ribbonRisk']}" if r["ribbonRisk"] else ""))

    # 4. Health Risk — internal health vs. price territory
    aligned = ((r["healthDir"] == "bull" and r["territory"] == "bull")
               or (r["healthDir"] == "bear" and r["territory"] == "bear"))
    add("health",
        "high" if not aligned else "elevated" if abs(r["healthBlack"]) < 2 else "low",
        f"{r['healthDir']} health (black {js_num(r['healthBlack'])})" + ("" if aligned else " — conflicts with territory"))

    # 5. Sudden Spot Risk — a sudden bull/bear reversal spot just printed
    spot = r["candle"] in ("turquoise", "golden")
    add("spot", "high" if spot else "low",
        f"{r['candle']} sudden reversal" if spot else "no sudden spot")

    # 6. Trend Change Risk & Warnings — pre-entry warning / equilibrium candles
    warn = r["candle"] in ("yellow", "indigo", "neutral")
    add("trendchange", "elevated" if warn else "low",
        f"{r['candle']} warning candle" if warn else "trend confirmed")

    # 7. Special Conditional Risks — volatility / liquidity anomalies
    add("special", "high" if r["exVola"] else "low" if r["lowRisk"] else "elevated",
        f"Volatility {js_num(r['atrPct'])}%"
        + (" (excessive)" if r["exVola"] else " (calm)" if r["lowRisk"] else "")
        + (", volume surge" if r["volQual"] else ""))

    # 8. Fundamental Risk — valuation & quality.
    # magic.js distinguishes pe === null (explicitly unprofitable -> HIGH) from
    # pe undefined (field absent -> UNKNOWN); mirror that with a key-presence check.
    if "pe" in r and r["pe"] is None:
        add("fundamental", "high", "unprofitable / no P/E")
    elif r.get("pe") is None or r.get("roic") is None:
        add("fundamental", "unknown", "fundamentals unavailable")
    else:
        fr = 0
        if r["pe"] > 40:
            fr += 2
        elif r["pe"] > 25:
            fr += 1
        if r["roic"] < 10:
            fr += 2
        elif r["roic"] < 18:
            fr += 1
        if r.get("earnYield") is not None and r["earnYield"] < 3:
            fr += 1
        ey = r.get("earnYield")
        add("fundamental", "high" if fr >= 3 else "elevated" if fr >= 1 else "low",
            f"P/E {js_num(r['pe'])}, ROIC {js_num(r['roic'])}%, "
            f"earn yld {js_num(ey) if ey is not None else None}%")

    score = js_round(sum(LEVEL_VAL[d["level"]] for d in dims) / (len(dims) * 3) * 100)
    return dims, int(score)


# --------------------------------------------------------------------------- #
# Bond Inversions — yield-curve macro risk
# --------------------------------------------------------------------------- #
# Pairs are (shorter maturity, longer maturity); inverted when the longer
# yield is below the shorter yield, per the manual's inversion definition.

BOND_INVERSION_PAIRS = [
    ("3M", "1Y"), ("6M", "1Y"), ("6M", "2Y"), ("6M", "3Y"), ("6M", "5Y"),
    ("1Y", "3Y"), ("1Y", "5Y"), ("1Y", "7Y"), ("1Y", "10Y"),
    ("2Y", "5Y"), ("2Y", "10Y"), ("2Y", "30Y"),
    ("3Y", "5Y"), ("5Y", "10Y"), ("10Y", "30Y"),
]


def compute_inversions(yields: list[dict]) -> dict:
    by_key = {y["maturity"]: y["yield"] for y in yields}
    active = [(sh, lo) for sh, lo in BOND_INVERSION_PAIRS
              if by_key.get(sh) is not None and by_key.get(lo) is not None
              and by_key[lo] < by_key[sh]]  # longer yield below shorter -> inverted
    return {
        "pairs": BOND_INVERSION_PAIRS,
        "active": active,
        "count": len(active),
        "total": len(BOND_INVERSION_PAIRS),
        "pct": js_round(len(active) / len(BOND_INVERSION_PAIRS) * 100, 1),
    }


# --------------------------------------------------------------------------- #
# Bundled demo data — parsed from the sibling data.js so the Python and JS
# screeners share one source of truth.
# --------------------------------------------------------------------------- #

DATA_JS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data.js")


def _js_array_to_json(src: str, const_name: str) -> list:
    m = re.search(rf"const\s+{const_name}\s*=\s*(\[.*?\n\]);", src, re.S)
    if not m:
        raise ValueError(f"{const_name} not found in data.js")
    body = m.group(1)
    body = re.sub(r"//[^\n]*", "", body)                    # line comments
    body = re.sub(r"(?<=\d)_(?=\d)", "", body)              # 54_000_000 -> 54000000
    # Quote bare object keys. Keys in data.js are plain identifiers and no
    # string value contains `word:`, so this transform is safe for this file.
    body = re.sub(r"([{,]\s*)([A-Za-z_]\w*)\s*:", r'\1"\2":', body)
    body = re.sub(r",(\s*[}\]])", r"\1", body)              # trailing commas
    return json.loads(body)


def load_demo() -> tuple[list[dict], list[dict]]:
    with open(DATA_JS, encoding="utf-8") as f:
        src = f.read()
    src = re.sub(r"/\*.*?\*/", "", src, flags=re.S)         # block comments
    return _js_array_to_json(src, "STOCK_UNIVERSE"), _js_array_to_json(src, "BOND_YIELDS")


# --------------------------------------------------------------------------- #
# Optional live data (Financial Modeling Prep) — mirrors cli.js / app.js
# --------------------------------------------------------------------------- #

FMP_BASE = "https://financialmodelingprep.com/api/v3"


def _fmp_key() -> str:
    key = os.environ.get("FMP_API_KEY", "")
    if not key:
        raise SystemExit("--live needs an FMP key: export FMP_API_KEY=... "
                         "(or pass numbers via flags)")
    return key


def _get_json(url: str):
    with urllib.request.urlopen(url, timeout=30) as r:
        return json.loads(r.read().decode())


def fetch_live_one(ticker: str) -> dict:
    """Single-ticker live snapshot: quote + key metrics + 1M change + RSI-14."""
    key = _fmp_key()
    quote = _get_json(f"{FMP_BASE}/quote/{ticker}?apikey={key}")
    if not quote:
        raise SystemExit(f"no quote for {ticker}")
    q = quote[0]
    try:
        km = (_get_json(f"{FMP_BASE}/key-metrics-ttm/{ticker}?apikey={key}") or [{}])[0]
    except Exception:
        km = {}
    try:
        chg = _get_json(f"{FMP_BASE}/stock-price-change/{ticker}?apikey={key}")
        c = chg[0] if isinstance(chg, list) and chg else chg if isinstance(chg, dict) else {}
    except Exception:
        c = {}
    try:
        rsi_arr = _get_json(f"{FMP_BASE}/technical_indicator/1day/{ticker}"
                            f"?type=rsi&period=14&apikey={key}")
    except Exception:
        rsi_arr = []
    pe = q.get("pe")
    roic = km.get("roicTTM")
    earn_yield = km.get("earningsYieldTTM")
    return {
        "ticker": ticker, "name": q.get("name") or ticker, "sector": "",
        "price": q["price"], "sma50": q["priceAvg50"], "sma200": q["priceAvg200"],
        "volume": q.get("volume"), "pe": pe, "beta": km.get("betaTTM") or 1.0,
        "rsi14": (rsi_arr[0].get("rsi") if rsi_arr else None) or 50,
        "perfMonth": c.get("1M") or c.get("oneMonth") or 0,
        "perfYTD": c.get("ytd") or 0,
        "roic": round(roic * 100, 2) if roic is not None else None,
        "earnYield": (round(earn_yield * 100, 2) if earn_yield is not None
                      else round(100 / pe, 2) if pe else None),
    }


def fetch_live_universe(universe: list[dict]) -> list[dict]:
    """Batch refresh of the demo universe: quote + stock-price-change, merged
    over the demo baseline exactly like app.js tryLiveData()."""
    key = _fmp_key()
    symbols = ",".join(s["ticker"].replace(".", "-") for s in universe)

    def index_by_symbol(arr):
        return {row["symbol"].replace("-", "."): row
                for row in (arr if isinstance(arr, list) else [])
                if isinstance(row, dict) and isinstance(row.get("symbol"), str)}

    quotes = _get_json(f"{FMP_BASE}/quote/{symbols}?apikey={key}")
    if not isinstance(quotes, list):
        raise SystemExit("Unexpected quote payload")
    try:  # best-effort: a price-change failure must not abort the rebuild
        changes = _get_json(f"{FMP_BASE}/stock-price-change/{symbols}?apikey={key}")
    except Exception:
        changes = None
    by_ticker = index_by_symbol(quotes)
    change_by_ticker = index_by_symbol(changes)

    merged_universe = []
    for base in universe:
        q = by_ticker.get(base["ticker"])
        merged = dict(base)
        if q:
            merged["price"] = q.get("price") or base["price"]
            merged["marketCap"] = q.get("marketCap") or base["marketCap"]
            # A null/0 live P/E means unprofitable — keep it None so the engine
            # flags Fundamental Risk HIGH rather than falling back to demo P/E.
            pe = q.get("pe")
            merged["pe"] = None if pe is None or pe <= 0 else pe
            merged["sma50"] = q.get("priceAvg50") or base["sma50"]
            merged["sma200"] = q.get("priceAvg200") or base["sma200"]
            merged["volume"] = q.get("avgVolume") or base["volume"]
            chg = change_by_ticker.get(base["ticker"], {})
            one_month = chg.get("1M")
            merged["perfMonth"] = one_month if one_month is not None else base["perfMonth"]
        merged_universe.append(merged)
    return merged_universe


# --------------------------------------------------------------------------- #
# Output formatting
# --------------------------------------------------------------------------- #

CSV_COLS = ["ticker", "name", "sector", "price", "perfMonth", "weeklyTrend",
            "territory", "mtfAlign", "mlGate", "zone", "candle", "dspr", "dsprCode",
            "atrPct", "ribbon", "ribbonRisk", "ingBull", "ingBear", "entryTrigger",
            "magicScore", "dimRisk"]

DISCLAIMER = "Educational risk model — no buy/sell signals. Not investment advice."


def fmt_read(r: dict) -> str:
    c = CANDLE_META[r["candle"]]
    gate = ("LONG-OK (above both)" if r["mlGate"] == 1
            else "SHORT-OK (below both)" if r["mlGate"] == -1 else "NO-TRADE (between)")
    zone_note = ("(washed-out / bottom)" if r["zone"] >= 5
                 else "(rich / top)" if r["zone"] <= 2 else "(mid)")
    rr = f" {'+' if r['ribbonRisk'] > 0 else ''}{r['ribbonRisk']}" if r["ribbonRisk"] else ""
    lines = [
        f"===== ELITE MAGIC TRADER — {r['ticker']}" + (f" ({r['name']})" if r.get("name") else "") + " =====",
        f"Price ${js_num(r['price'])}  | SMA50 ${js_num(r['sma50'])}  SMA200 ${js_num(r['sma200'])}"
        f"  | RSI14 {js_num(r['rsi14'])}  beta {js_num(r['beta'])}",
        "",
        "DIRECTIONAL READ",
        f"  Blue Line territory : {r['territory'].upper()}  (stair-step {r['stairStep']})",
        f"  Magic Lines gate    : {gate}  [green ${js_num(r['magicGreen'])} / red ${js_num(r['magicRed'])}]",
        f"  Magic Trading Zone  : Zone {r['zone']}/6  {zone_note}  | {js_num(r['distMA200'])}% vs 200-MA",
        f"  Magic Candle        : {c['glyph']} {c['label']} — {c['desc']}",
        f"  HTR Ribbon          : {r['ribbon']}{rr}  (elapsed-time risk)",
        f"  Health              : {r['healthDir']} (black {js_num(r['healthBlack'])})  | Dir-line {r['dirLine']} | Spike {r['spike']} | PureHealth {r['pureHealth']}",
        f"  Volatility Combinator: bias {r['volaBias']}  vol {js_num(r['atrPct'])}% "
        + ("(EXCESSIVE)" if r["exVola"] else "(calm)" if r["lowRisk"] else ""),
        f"  DSPR preceptor      : Type {r['dspr']} ({r['dsprCode']}) — {DSPR_META[r['dspr']]['desc']}",
        "",
        f"FIVE INGREDIENTS  Bull {r['ingBull']}/5  Bear {r['ingBear']}/5  vol>500k {str(r['volPass']).lower()}  | Entry trigger: {r['entryTrigger'].upper()}",
        f"Weekly trend {r['weeklyTrend']} → Wk→Daily alignment: {r['mtfAlign'].upper()}",
        "",
        f"MAGIC SCORE (0 bear · 50 neutral · 100 bull): {r['magicScore']}",
        f"8D WEIGHTED RISK (higher = more risk): {r['dimRisk']}/100",
    ]
    for d in r["dims"]:
        lines.append(f"   {d['num']}. {d['name']:<30} {d['level'].upper():<9} {d['note']}")
    return "\n".join(lines)


def screen_table(scored: list[dict]) -> str:
    out = [f"{'TICK':<6}{'PRICE':>9}  {'TERR':<5}{'ZN':>3}  {'CANDLE':<10}"
           f"{'RIBBON':<10}{'B/B':<5}{'MAGIC':>6}{'8D':>5}  ENTRY"]
    for r in scored:
        c = CANDLE_META[r["candle"]]
        rr = f" {'+' if r['ribbonRisk'] > 0 else ''}{r['ribbonRisk']}" if r["ribbonRisk"] else ""
        out.append(f"{r['ticker']:<6}{'$' + js_num(r['price']):>9}  {r['territory']:<5}"
                   f"{r['zone']:>3}  {c['glyph'] + ' ' + c['label']:<10}"
                   f"{r['ribbon'] + rr:<10}{str(r['ingBull']) + '/' + str(r['ingBear']):<5}"
                   f"{r['magicScore']:>6}{r['dimRisk']:>5}  {r['entryTrigger']}")
    return "\n".join(out)


def write_csv(scored: list[dict], path: str) -> None:
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)  # RFC 4180 quoting/escaping, same as the web app's export
        w.writerow(CSV_COLS)
        for r in scored:
            w.writerow(["" if r.get(k) is None else js_num(r[k]) if isinstance(r[k], float)
                        else r[k] for k in CSV_COLS])


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #

NUM_FLAGS = {"price": "price", "sma50": "sma50", "sma200": "sma200", "rsi": "rsi14",
             "beta": "beta", "perf_month": "perfMonth", "perf_ytd": "perfYTD",
             "volume": "volume", "pe": "pe", "roic": "roic", "earn_yield": "earnYield",
             "pb": "pb", "div_yield": "divYield", "market_cap": "marketCap"}


def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(
        description="Elite Magic Trader screener (Python port of magic.js). " + DISCLAIMER,
        formatter_class=argparse.ArgumentDefaultsHelpFormatter)
    p.add_argument("ticker", nargs="?", help="ticker for a single-stock read")
    p.add_argument("--live", action="store_true", help="fetch fresh data from FMP (needs FMP_API_KEY)")
    p.add_argument("--screen", action="store_true", help="score the whole universe as a table")
    p.add_argument("--list", action="store_true", help="list bundled demo tickers")
    p.add_argument("--json", metavar="FILE", help="load the stock record from a JSON file")
    p.add_argument("--csv", metavar="FILE", help="with --screen: also write results to CSV")
    p.add_argument("--sort", choices=["magic", "risk"], default="magic",
                   help="screen sort: magic = conviction desc, risk = 8D risk asc")
    p.add_argument("--entry-only", action="store_true", help="only rows with a 5/5 entry trigger")
    p.add_argument("--min-ing", type=int, default=0, metavar="N",
                   help="minimum five-ingredient alignment on the chosen side")
    p.add_argument("--side", choices=["bull", "bear"], default="bull",
                   help="side used by --min-ing / ranking filters")
    for flag in NUM_FLAGS:
        p.add_argument(f"--{flag.replace('_', '-')}", type=float, default=None)
    p.add_argument("--name")
    p.add_argument("--sector")
    args = p.parse_args(argv)

    universe, bond_yields = load_demo()

    if args.list:
        print("  ".join(s["ticker"] for s in universe))
        return

    if args.screen:
        rows = fetch_live_universe(universe) if args.live else universe
        scored = [compute_magic(dict(s)) for s in rows]
        ing_key = "ingBull" if args.side == "bull" else "ingBear"
        scored = [r for r in scored if r[ing_key] >= args.min_ing]
        if args.entry_only:
            scored = [r for r in scored if r["entryTrigger"] != "none"]
        if args.sort == "magic":
            scored.sort(key=lambda r: -r["magicScore"])
        else:
            scored.sort(key=lambda r: (r["dimRisk"], -r["magicScore"]))
        inv = compute_inversions(bond_yields)
        print(f"BOND INVERSIONS (demo curve): {inv['count']}/{inv['total']} pairs "
              f"inverted ({js_num(inv['pct'])}%)  — macro risk gauge")
        print()
        print(screen_table(scored))
        if args.csv:
            write_csv(scored, args.csv)
            print(f"\nCSV -> {args.csv} ({len(scored)} rows)")
        print("\n" + ("LIVE via FMP" if args.live else "bundled DEMO snapshot — may be stale; pass --live for fresh data"))
        print(DISCLAIMER)
        return

    ticker = (args.ticker or "").upper()
    if not ticker:
        p.error("give a TICKER, or use --screen / --list")

    src_note = ""
    if args.json:
        with open(args.json, encoding="utf-8") as f:
            stock = json.load(f)
        src_note = f"(from {args.json})"
    elif args.live:
        stock = fetch_live_one(ticker)
        src_note = "(LIVE via FMP)"
    else:
        demo = next((s for s in universe if s["ticker"].upper() == ticker), None)
        if demo:
            stock = dict(demo)
            src_note = "(bundled DEMO snapshot — may be stale; pass --live or --price/... for fresh data)"
        else:
            stock = {"ticker": ticker, "name": ticker}

    # apply numeric/string overrides
    for flag, prop in NUM_FLAGS.items():
        val = getattr(args, flag)
        if val is not None:
            stock[prop] = val
    if args.name is not None:
        stock["name"] = args.name
    if args.sector is not None:
        stock["sector"] = args.sector
    stock["ticker"] = ticker
    if args.earn_yield is None and stock.get("pe"):
        stock["earnYield"] = round(100 / stock["pe"], 2)

    required = ["price", "sma50", "sma200", "rsi14", "beta", "perfMonth",
                "perfYTD", "volume", "pe"]
    missing = [k for k in required if k not in stock
               or (stock[k] is None and k != "pe")
               or (isinstance(stock.get(k), float) and math.isnan(stock[k]))]
    if missing:
        raise SystemExit(
            f"Missing data for {ticker}: {', '.join(missing)}\n"
            "Pass them as flags, e.g. --price 204.02 --sma50 244.89 ... "
            "(or --live with FMP_API_KEY).")

    print(fmt_read(compute_magic(stock)))
    if src_note:
        print("\n" + src_note)
    print(DISCLAIMER)


if __name__ == "__main__":
    try:  # die quietly when piped into head/less (no SIGPIPE on Windows)
        import signal
        signal.signal(signal.SIGPIPE, signal.SIG_DFL)
    except (ImportError, AttributeError, ValueError):
        pass
    main()
