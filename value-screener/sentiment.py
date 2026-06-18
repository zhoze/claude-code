#!/usr/bin/env python3
"""
Sentiment — the 4th lens: "most-liked / highly-rated" signal
============================================================

Runs after Magic Elite in the pipeline. Blends three popularity/quality signals,
each weighted equally (~1/3), into a 0-100 directional score (50 = neutral):

  1. Retail social   — Reddit most-mentioned + StockTwits bullish % (crowd buzz)
  2. Analyst consensus — Strong Buy/Buy/Hold/Sell + average price-target upside
  3. Insider buying  — recent net insider purchases ("skin in the game")

**Hype temper (flag & temper):** retail favorites skew high-beta and narrative-
driven, so extreme *bullish* social sentiment on a high-beta name is partly
discounted (contrarian-aware) and flagged `hype_risk` — froth doesn't fully
inflate the score. Only over-enthusiasm is tempered; bearish/neutral isn't.

Offline & deterministic: signals are gathered from forums/portals/analyst data
at run time and stored, dated and sourced, in data/sentiment.json (it does NOT
fetch them itself). Educational only — not investment advice.
"""

import json
import os

HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_SENTIMENT = os.path.join(HERE, "data", "sentiment.json")

SOCIAL = {"very_bullish": 85, "bullish": 70, "neutral": 50, "bearish": 30, "very_bearish": 15}
ANALYST_BASE = {"strong_buy": 80, "buy": 65, "hold": 50, "sell": 30, "strong_sell": 18}
INSIDER = {"buying": 70, "neutral": 50, "selling": 30}


def clamp(v, lo, hi):
    return max(lo, min(hi, v))


def load_sentiment(path):
    if not path or not os.path.exists(path):
        return {}
    try:
        with open(path) as fh:
            return {k.upper(): v for k, v in json.load(fh).get("stocks", {}).items()}
    except (ValueError, OSError):
        return {}


def compute_sentiment(entry, beta=None):
    """entry: {social, analyst, analyst_upside_pct, insider}. Returns dict or None."""
    if not entry:
        return None
    comps = {}
    if entry.get("social") in SOCIAL:
        comps["social"] = float(SOCIAL[entry["social"]])
    if entry.get("analyst") in ANALYST_BASE:
        a = ANALYST_BASE[entry["analyst"]]
        up = entry.get("analyst_upside_pct")
        if up is not None:
            a += clamp(up * 0.3, -15, 15)
        comps["analyst"] = clamp(a, 0, 100)
    if entry.get("insider") in INSIDER:
        comps["insider"] = float(INSIDER[entry["insider"]])
    if not comps:
        return None

    raw = sum(comps.values()) / len(comps)          # equal weight (~1/3 each when all present)

    # Hype temper: only bullish social froth on a high-beta name is discounted.
    social_val = comps.get("social", 50)
    social_extreme = clamp((social_val - 65) / 35.0, 0, 1)        # >0 only for bullish/very_bullish
    beta_factor = clamp(((beta or 1.0) - 1.5) / 1.5, 0, 1)        # 0 at beta<=1.5, 1 at beta>=3
    hype = social_extreme * beta_factor
    hype_penalty = hype * 0.5
    tempered = 50 + (raw - 50) * (1 - hype_penalty)

    return {
        "score": round(clamp(tempered, 0, 100), 1),
        "raw": round(raw, 1),
        "components": {k: round(v, 1) for k, v in comps.items()},
        "hype_risk": hype > 0.3,
        "hype_penalty": round(hype_penalty, 2),
        "sources": entry.get("sources", []),
    }


if __name__ == "__main__":
    # smoke test: high-beta very-bullish name gets tempered + flagged
    froth = {"social": "very_bullish", "analyst": "buy", "analyst_upside_pct": 20, "insider": "neutral"}
    print("beta 1.0:", compute_sentiment(froth, 1.0))
    print("beta 3.0:", compute_sentiment(froth, 3.0))
