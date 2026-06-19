#!/usr/bin/env python3
"""
Pre-screen — market-conditions & fresh-news engine
==================================================

The *first* stage of the screening pipeline (Pre-screen -> Warren Buffett ->
Magic Elite -> Overall). It reads a dated, sourced snapshot of market
conditions (`data/market_conditions.json`) and produces:

  - an **overall pre-market read**: VIX, index futures, the 10y Treasury, oil,
    a macro 0-100 directional score (50 = neutral) and a risk regime, and
  - a **per-stock near-term news read** (if a ticker is requested): news
    sentiment + sector bias rolled into a 0-100 directional score.

Like the rest of this toolkit the engine is OFFLINE and deterministic — it does
NOT fetch news itself. News is gathered from reliable sources at run time and
stored in market_conditions.json (each entry dated + sourced). Trigger keyword:
**Pre-screen**. Educational only — not investment advice.

    python3 prescreen.py                # overall pre-market conditions
    python3 prescreen.py --ticker ADBE  # + that stock's near-term news read
    python3 prescreen.py --selftest
"""

import argparse
import json
import os

HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_MC = os.path.join(HERE, "data", "market_conditions.json")

SENTIMENT_ADJ = {
    "strong_bullish": 20, "bullish": 12, "neutral": 0, "bearish": -12, "strong_bearish": -20,
}
SECTOR_ADJ = {"bullish": 8, "neutral": 0, "bearish": -8}

# Pre-opening macro-signal tuning (all optional; each contributes only if present
# in the snapshot, so the engine stays backward-compatible with older files).
VVIX_BASE = 90.0          # vol-of-vol baseline: above = stress, below = calm
FG_EXTREME_LO = 20        # CNN Fear & Greed: extreme fear -> contrarian, temper the penalty
FG_EXTREME_HI = 80        # extreme greed -> contrarian, temper the boost
MOVE_BASE = 95.0          # Treasury-vol (MOVE) baseline: above = rates stress, below = calm
HY_OAS_BASE = 3.5         # high-yield OAS (%) baseline: wider = risk-off, tighter = risk-on
PUT_CALL_BASE = 0.70      # equity put/call neutral; low = complacency, high = fear (contrarian)
BREAKEVEN_ANCHOR = 2.3    # 10y breakeven (%) anchor; above = inflation/hawkish headwind


def clamp(v, lo, hi):
    return max(lo, min(hi, v))


def load_market_conditions(path):
    with open(path) as fh:
        return json.load(fh)


# --------------------------------------------------------------------------- #
# Macro                                                                        #
# --------------------------------------------------------------------------- #
def score_macro(mc):
    """Macro 0-100 directional score (50 neutral, higher = bullish near-term)."""
    m = mc.get("macro", {})
    score = 50.0
    drivers = []

    futs = [m.get(k) for k in ("sp500_futures_pct", "nasdaq_futures_pct", "dow_futures_pct")]
    futs = [f for f in futs if f is not None]
    if futs:
        avg = sum(futs) / len(futs)
        adj = clamp(avg * 5, -20, 20)
        score += adj
        drivers.append((f"Index futures avg {avg:+.2f}%", adj))

    vix = m.get("vix")
    if vix is not None:
        adj = clamp((16 - vix) * 1.2, -20, 12)
        score += adj
        drivers.append((f"VIX {vix} (vs ~16 baseline)", adj))

    dby = m.get("ust10y_change_bps")
    if dby is not None:
        adj = clamp(-dby * 0.4, -10, 10)
        score += adj
        drivers.append((f"10y yield {dby:+d} bps", adj))

    net = 0.0
    for h in m.get("headlines", []):
        w = h.get("weight", 0)
        net += w if h.get("impact") == "bullish" else (-w if h.get("impact") == "bearish" else 0)
    if net:
        adj = clamp(net * 0.8, -15, 15)
        score += adj
        drivers.append((f"Net headline impact {net:+.0f}", adj))

    # --- Volatility-of-volatility (VVIX): elevated = risk-off ---
    vvix = m.get("vvix")
    if vvix is not None:
        adj = clamp((VVIX_BASE - vvix) * 0.15, -8, 6)
        score += adj
        drivers.append((f"VVIX {vvix} (vs ~{VVIX_BASE:.0f} baseline)", adj))

    # --- CNN Fear & Greed: directional, contrarian-tempered at extremes ---
    fg = m.get("fear_greed")
    if fg is not None:
        raw = (fg - 50) * 0.18
        if fg <= FG_EXTREME_LO or fg >= FG_EXTREME_HI:
            raw *= 0.5          # fade the crowd at sentiment extremes
        adj = clamp(raw, -10, 10)
        score += adj
        label = m.get("fear_greed_label", "")
        drivers.append((f"Fear & Greed {fg}{' (' + label + ')' if label else ''}", adj))

    # --- Crude oil: a sharp move is an inflation/risk signal (up = headwind) ---
    oil = m.get("wti_oil_pct")
    if oil is not None:
        adj = clamp(-oil * 0.5, -8, 4)
        score += adj
        drivers.append((f"WTI crude {oil:+.1f}%", adj))

    # --- Safe-haven metals bid (gold/silver rallying) = mild risk-off tilt ---
    metal = [x for x in (m.get("gold_change_pct"), m.get("silver_change_pct")) if x is not None]
    if metal:
        avg = sum(metal) / len(metal)
        adj = clamp(-avg * 0.25, -5, 3)
        score += adj
        drivers.append((f"Precious metals {avg:+.1f}% (safe-haven bid)", adj))

    # --- Gasoline at the pump: higher = consumer/inflation headwind ---
    gas = m.get("gasoline_change_pct")
    if gas is not None:
        adj = clamp(-gas * 0.3, -5, 3)
        score += adj
        drivers.append((f"Gasoline {gas:+.1f}% at the pump", adj))

    # --- US dollar (DXY): a stronger dollar is a mild equity/earnings headwind ---
    dxy = m.get("dxy_change_pct")
    if dxy is not None:
        adj = clamp(-dxy * 0.6, -5, 4)
        score += adj
        drivers.append((f"US dollar (DXY) {dxy:+.1f}%", adj))

    # --- 2y Treasury: front-end rate move (mirrors the 10y treatment) ---
    d2 = m.get("ust2y_change_bps")
    if d2 is not None:
        adj = clamp(-d2 * 0.3, -8, 8)
        score += adj
        drivers.append((f"2y yield {d2:+d} bps", adj))

    # --- Bitcoin: cross-asset risk-appetite proxy (small weight) ---
    btc = m.get("btc_change_pct")
    if btc is not None:
        adj = clamp(btc * 0.15, -5, 5)
        score += adj
        drivers.append((f"Bitcoin {btc:+.1f}%", adj))

    # --- MOVE index: Treasury-market vol; elevated = rates-driven risk-off ---
    move = m.get("move_index")
    if move is not None:
        adj = clamp((MOVE_BASE - move) * 0.06, -8, 5)
        score += adj
        drivers.append((f"MOVE {move} (vs ~{MOVE_BASE:.0f} baseline)", adj))

    # --- High-yield credit spreads (OAS): wider = risk-off, tighter = risk-on ---
    oas = m.get("hy_oas_pct")
    if oas is not None:
        adj = clamp((HY_OAS_BASE - oas) * 6, -12, 6)
        score += adj
        drivers.append((f"HY credit OAS {oas:.2f}% (vs ~{HY_OAS_BASE:.1f}% baseline)", adj))

    # --- Equity put/call: CONTRARIAN - low = complacency (bearish), high = fear (bullish) ---
    pc = m.get("put_call_equity")
    if pc is not None:
        adj = clamp((pc - PUT_CALL_BASE) * 15, -6, 6)
        score += adj
        drivers.append((f"Put/Call {pc:.2f} (contrarian)", adj))

    # --- VIX term structure: contango (VIX < VIX3M) = calm; backwardation = stress ---
    vix3m = m.get("vix3m")
    if vix is not None and vix3m:
        ratio = vix / vix3m
        adj = clamp((1 - ratio) * 30, -8, 6)
        score += adj
        shape = "contango" if ratio < 1 else "backwardation"
        drivers.append((f"VIX term structure {ratio:.2f} ({shape})", adj))

    # --- Overnight global equities (Asia/Europe) set the pre-US-open tide ---
    ge = m.get("global_equities") or {}
    gvals = [v for v in ge.values() if isinstance(v, (int, float))]
    if gvals:
        avg = sum(gvals) / len(gvals)
        adj = clamp(avg * 4, -12, 12)
        score += adj
        drivers.append((f"Global equities avg {avg:+.2f}% (overnight)", adj))

    # --- Inflation expectations (10y breakeven): above anchor = hawkish headwind ---
    be = m.get("breakeven_10y")
    if be is not None:
        adj = clamp(-(be - BREAKEVEN_ANCHOR) * 8, -6, 6)
        score += adj
        drivers.append((f"10y breakeven {be:.2f}% (vs ~{BREAKEVEN_ANCHOR:.1f}% anchor)", adj))

    score = round(clamp(score, 0, 100), 1)

    # Regime: VIX-aware so a calm tape isn't called risk-off on score alone.
    if vix is not None and vix >= 28 or score < 35:
        regime = "Risk-off"
    elif (vix is not None and vix >= 20) or score < 45:
        regime = "Cautious / elevated"
    elif score >= 60 and (vix is None or vix < 18):
        regime = "Risk-on"
    else:
        regime = "Neutral"

    drivers.sort(key=lambda d: -abs(d[1]))
    return {"score": score, "regime": regime, "drivers": drivers}


# --------------------------------------------------------------------------- #
# Per-stock news                                                               #
# --------------------------------------------------------------------------- #
def score_stock_news(mc, ticker, macro_score):
    """Near-term news direction for a stock: macro tide + sector + sentiment."""
    s = mc.get("stocks", {}).get(ticker.upper())
    if not s:
        return None
    sector = s.get("sector") or _lookup_sector(mc, ticker)
    sector_bias = (mc.get("sectors", {}).get(sector, {}) or {}).get("bias", "neutral")
    sentiment = s.get("sentiment", "neutral")

    sector_adj = SECTOR_ADJ.get(sector_bias, 0)
    sent_adj = SENTIMENT_ADJ.get(sentiment, 0)
    news_score = round(clamp(macro_score + sector_adj + sent_adj, 0, 100), 1)

    return {
        "news_score": news_score, "sentiment": sentiment, "sector": sector,
        "sector_bias": sector_bias, "sector_adj": sector_adj, "sentiment_adj": sent_adj,
        "note": s.get("note", ""), "near_term_event": s.get("near_term_event", ""),
        "catalysts": s.get("catalysts", []), "sources": s.get("sources", []),
        "technicals": s.get("technicals", {}),
    }


def _lookup_sector(mc, ticker):
    return (mc.get("stocks", {}).get(ticker.upper(), {}) or {}).get("sector", "")


# --------------------------------------------------------------------------- #
# Rendering / CLI                                                              #
# --------------------------------------------------------------------------- #
def macro_report(mc, macro):
    m = mc.get("macro", {})
    L = [f"===== PRE-SCREEN — market conditions ({mc.get('as_of','n/a')}, {mc.get('session','')}) ====="]
    L.append(f"Regime: {macro['regime'].upper()}   Macro score: {macro['score']}/100 "
             f"(50 = neutral, higher = bullish near-term)")
    bits = []
    if m.get("vix") is not None: bits.append(f"VIX {m['vix']}")
    if m.get("sp500_futures_pct") is not None: bits.append(f"S&P fut {m['sp500_futures_pct']:+.2f}%")
    if m.get("nasdaq_futures_pct") is not None: bits.append(f"Nasdaq fut {m['nasdaq_futures_pct']:+.2f}%")
    if m.get("dow_futures_pct") is not None: bits.append(f"Dow fut {m['dow_futures_pct']:+.2f}%")
    if m.get("ust10y") is not None: bits.append(f"10y {m['ust10y']}%")
    if bits: L.append("  " + " · ".join(bits))
    if m.get("summary"): L.append(f"  {m['summary']}")
    L.append("  Drivers:")
    for label, adj in macro["drivers"]:
        L.append(f"    {adj:+5.1f}  {label}")
    return "\n".join(L)


def stock_news_report(news):
    L = ["", f"--- Stock news read: sentiment {news['sentiment'].upper()} "
            f"| sector {news['sector']} ({news['sector_bias']}) ---"]
    L.append(f"News direction score: {news['news_score']}/100  "
             f"(macro {news['news_score']-news['sector_adj']-news['sentiment_adj']:.1f} "
             f"{news['sector_adj']:+d} sector {news['sentiment_adj']:+d} sentiment)")
    if news.get("near_term_event"): L.append(f"  Near-term event: {news['near_term_event']}")
    if news.get("note"): L.append(f"  {news['note']}")
    for c in news.get("catalysts", []): L.append(f"   - {c}")
    if news.get("sources"):
        L.append("  Sources: " + " · ".join(s.get("title", "link") for s in news["sources"]))
    return "\n".join(L)


def selftest():
    mc = {
        "as_of": "test", "session": "test",
        "macro": {"vix": 14, "sp500_futures_pct": 0.5, "nasdaq_futures_pct": 0.6,
                  "dow_futures_pct": 0.4, "ust10y_change_bps": -5,
                  "headlines": [{"impact": "bullish", "weight": 8}]},
        "sectors": {"Tech": {"bias": "bullish"}},
        "stocks": {"GOOD": {"sector": "Tech", "sentiment": "bullish"},
                   "BADN": {"sector": "Tech", "sentiment": "strong_bearish"}},
    }
    macro = score_macro(mc)
    assert macro["score"] > 60 and macro["regime"] == "Risk-on", macro
    good = score_stock_news(mc, "GOOD", macro["score"])
    bad = score_stock_news(mc, "BADN", macro["score"])
    assert good["news_score"] > bad["news_score"], (good, bad)
    assert good["news_score"] > macro["score"], good          # bullish stock beats the tape
    assert bad["news_score"] < macro["score"], bad            # bearish stock lags it

    # Risk-off regime when VIX spikes and headlines turn negative.
    mc2 = {"macro": {"vix": 30, "sp500_futures_pct": -1.5, "nasdaq_futures_pct": -2.0,
                     "dow_futures_pct": -1.2, "ust10y_change_bps": 10,
                     "headlines": [{"impact": "bearish", "weight": 10}]}}
    m2 = score_macro(mc2)
    assert m2["regime"] == "Risk-off" and m2["score"] < 40, m2

    # Pre-opening signals: each present indicator contributes and surfaces as a driver.
    mc3 = {"macro": {"vix": 15, "vvix": 105, "fear_greed": 18, "fear_greed_label": "Extreme Fear",
                     "wti_oil_pct": 8.0, "gold_change_pct": 3.0, "silver_change_pct": 2.0,
                     "gasoline_change_pct": 5.0, "dxy_change_pct": 1.5, "ust2y_change_bps": 12,
                     "btc_change_pct": -6.0}}
    mc3["macro"].update({"move_index": 130, "hy_oas_pct": 6.5, "put_call_equity": 0.45,
                         "vix3m": 14, "breakeven_10y": 2.9,
                         "global_equities": {"nikkei_pct": -2.0, "dax_pct": -1.5}})
    m3 = score_macro(mc3)
    labels = " ".join(l for l, _ in m3["drivers"])
    for tok in ("VVIX", "Fear & Greed", "WTI", "metals", "Gasoline", "DXY", "2y yield", "Bitcoin",
                "MOVE", "HY credit OAS", "Put/Call", "term structure", "Global equities", "breakeven"):
        assert tok in labels, (tok, labels)
    assert m3["score"] < 50, m3          # this cluster of headwinds is net-bearish

    # Calm "plumbing" (low MOVE, tight HY, contango, benign breakevens) lifts the score.
    calm = {"macro": {"vix": 15, "move_index": 65, "hy_oas_pct": 2.6, "vix3m": 19,
                      "breakeven_10y": 2.2, "global_equities": {"nikkei_pct": 0.8, "dax_pct": 0.5}}}
    assert score_macro(calm)["score"] > 55, score_macro(calm)

    print("prescreen selftest: all assertions passed")
    print(f"  risk-on macro {macro['score']} {macro['regime']}; GOOD {good['news_score']} vs BADN {bad['news_score']}")
    print(f"  risk-off macro {m2['score']} {m2['regime']}")
    return 0


def main(argv=None):
    p = argparse.ArgumentParser(description="Pre-screen market-conditions & news engine.")
    p.add_argument("--ticker", help="Also show this stock's near-term news read")
    p.add_argument("--market-conditions", default=DEFAULT_MC)
    p.add_argument("--selftest", action="store_true")
    args = p.parse_args(argv)
    if args.selftest:
        return selftest()

    mc = load_market_conditions(args.market_conditions)
    macro = score_macro(mc)
    print(macro_report(mc, macro))
    if args.ticker:
        news = score_stock_news(mc, args.ticker, macro["score"])
        if news:
            print(stock_news_report(news))
        else:
            print(f"\n(no news entry for {args.ticker.upper()} in {os.path.basename(args.market_conditions)} "
                  f"— refresh it with fresh sources)")
    print("\nEducational risk model — not investment advice. News as of "
          f"{mc.get('as_of','n/a')}; refresh before relying on it.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
