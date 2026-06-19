#!/usr/bin/env python3
"""
preopen.py - US stock-market PRE-OPENING screen
===============================================

A single, scannable read of everything that shapes sentiment and direction
BEFORE the US cash open: index futures, the VIX and VVIX, the CNN "fear gauge",
rates, commodities (crude, natural gas, gold, silver, copper) and fuel/gas at the
pump, the dollar (DXY) and bitcoin - rolled into one 0-100 directional score
(50 = neutral) and a risk regime.

It REUSES the Pre-screen macro engine (prescreen.score_macro) so the score and
"drivers" stay consistent across the toolkit; this module is just the dashboard
on top of it. Like the rest of the toolkit it is OFFLINE and deterministic - it
reads the dated, sourced snapshot in data/market_conditions.json and does NOT
fetch anything itself. Refresh that file before relying on it.

    python3 preopen.py            # full pre-opening screen
    python3 preopen.py --json     # machine-readable snapshot
    python3 preopen.py --selftest # built-in checks

Educational tool - not investment advice.
"""

import argparse
import json
import os

from prescreen import DEFAULT_MC, clamp, load_market_conditions, score_macro


# --------------------------------------------------------------------------- #
# Formatting helpers                                                           #
# --------------------------------------------------------------------------- #
def _num(v, fmt="{:.2f}", dash="n/a"):
    return dash if v is None else fmt.format(v)


def _pct(v, dash="n/a"):
    return dash if v is None else f"{v:+.2f}%"


def _arrow(v):
    if v is None:
        return " "
    return "▲" if v > 0 else ("▼" if v < 0 else "▬")


def _row(label, value, change=None, chg_unit="%"):
    """One indicator line: 'label .... value  (arrow change)'."""
    if change is None:
        chg = ""
    elif chg_unit == "bps":
        chg = f"  {_arrow(change)} {change:+.0f} bps"
    else:
        chg = f"  {_arrow(change)} {_pct(change)}"
    return f"    {label:<26} {value}{chg}"


def _gauge(score):
    """A compact 0-100 bar with the score marked (50 = neutral)."""
    width = 20
    pos = int(round(clamp(score / 100.0, 0, 1) * (width - 1)))
    bar = "".join("|" if i == pos else ("·" if i == width // 2 else "-") for i in range(width))
    return f"[{bar}]"


def _regime_tag(regime):
    return {
        "Risk-on": "\U0001f7e2 RISK-ON",
        "Neutral": "⚪ NEUTRAL",
        "Cautious / elevated": "\U0001f7e1 CAUTIOUS",
        "Risk-off": "\U0001f534 RISK-OFF",
    }.get(regime, regime.upper())


# --------------------------------------------------------------------------- #
# Report                                                                       #
# --------------------------------------------------------------------------- #
def render(mc, macro):
    m = mc.get("macro", {})
    L = []
    L.append("=" * 64)
    L.append("  US STOCK-MARKET PRE-OPENING SCREEN")
    L.append(f"  {mc.get('as_of', 'n/a')}  |  {mc.get('session', '')}")
    L.append("=" * 64)
    L.append(f"  {_regime_tag(macro['regime'])}    Score {macro['score']}/100  {_gauge(macro['score'])}")
    L.append("  (50 = neutral; higher = bullish near-term)")
    if m.get("summary"):
        L.append("")
        L.append("  " + _wrap(m["summary"], 60, "  "))

    # --- Equity futures ---
    L.append("")
    L.append("  EQUITY FUTURES")
    if m.get("futures_closed"):
        L.append(f"    {'S&P / Nasdaq / Dow':<26} CLOSED (holiday)")
        pc = m.get("prev_close", {})
        if pc:
            L.append(_row("Prior close - S&P 500", _pct(pc.get("sp500_pct"))))
            L.append(_row("Prior close - Nasdaq 100", _pct(pc.get("nasdaq100_pct"))))
            dl = pc.get("dow_level")
            dow = _pct(pc.get("dow_pct"))
            if pc.get("dow_points") is not None:
                dow += f" ({pc['dow_points']:+d} to {dl:,})" if dl else f" ({pc['dow_points']:+d})"
            L.append(_row("Prior close - Dow", dow))
            if pc.get("russell2000_pct") is not None:
                L.append(_row("Prior close - Russell 2000", _pct(pc.get("russell2000_pct"))))
    else:
        L.append(_row("S&P 500 futures", "", m.get("sp500_futures_pct")))
        L.append(_row("Nasdaq futures", "", m.get("nasdaq_futures_pct")))
        L.append(_row("Dow futures", "", m.get("dow_futures_pct")))

    # --- Global / overnight equities ---
    ge = m.get("global_equities") or {}
    if ge:
        L.append("")
        L.append("  GLOBAL / OVERNIGHT (sets the pre-open tone)")
        names = [("nikkei_pct", "Nikkei 225 (JP)"), ("hang_seng_pct", "Hang Seng (HK)"),
                 ("euro_stoxx50_pct", "Euro Stoxx 50"), ("dax_pct", "DAX (DE)"),
                 ("ftse100_pct", "FTSE 100 (UK)")]
        for key, label in names:
            if ge.get(key) is not None:
                L.append(_row(label, "", ge[key]))

    # --- Volatility & fear ---
    L.append("")
    L.append("  VOLATILITY & FEAR")
    L.append(_row("VIX (fear gauge)", _num(m.get("vix")), m.get("vix_change_pct")))
    L.append(_row("VVIX (vol-of-vol)", _num(m.get("vvix")), m.get("vvix_change_pct")))
    vix3m = m.get("vix3m")
    if vix3m is not None:
        L.append(_row("VIX3M (3-month)", _num(vix3m)))
        if m.get("vix") is not None:
            ratio = m["vix"] / vix3m
            shape = "contango (calm)" if ratio < 1 else "backwardation (stress)"
            L.append(_row("VIX term structure", f"{ratio:.2f}  {shape}"))
    fg = m.get("fear_greed")
    if fg is not None:
        fgl = m.get("fear_greed_label", "")
        prev = m.get("fear_greed_prev")
        extra = f"  (prev {prev})" if prev is not None else ""
        L.append(_row("CNN Fear & Greed", f"{fg} {fgl}{extra}"))

    # --- Rates ---
    L.append("")
    L.append("  RATES (US Treasuries)")
    L.append(_row("2-year yield", _num(m.get("ust2y"), "{:.2f}%"), m.get("ust2y_change_bps"), "bps"))
    L.append(_row("10-year yield", _num(m.get("ust10y"), "{:.2f}%"), m.get("ust10y_change_bps"), "bps"))
    if m.get("ust2y") is not None and m.get("ust10y") is not None:
        L.append(_row("10y-2y spread", f"{(m['ust10y'] - m['ust2y']) * 100:+.0f} bps"))
    if m.get("breakeven_10y") is not None:
        L.append(_row("10y breakeven inflation", _num(m.get("breakeven_10y"), "{:.2f}%")))

    # --- Credit, rates-vol & positioning ---
    if any(m.get(k) is not None for k in ("move_index", "hy_oas_pct", "put_call_equity")):
        L.append("")
        L.append("  CREDIT, RATES-VOL & POSITIONING")
        L.append(_row("MOVE (Treasury vol)", _num(m.get("move_index")), m.get("move_change_pct")))
        L.append(_row("HY credit OAS", _num(m.get("hy_oas_pct"), "{:.2f}%")))
        L.append(_row("Equity put/call", _num(m.get("put_call_equity"), "{:.2f}")))

    # --- Commodities & fuel ---
    L.append("")
    L.append("  COMMODITIES & FUEL")
    L.append(_row("WTI crude ($/bbl)", _num(m.get("wti_oil")), m.get("wti_oil_pct")))
    L.append(_row("Brent crude ($/bbl)", _num(m.get("brent_oil")), m.get("brent_oil_pct")))
    L.append(_row("Natural gas ($/MMBtu)", _num(m.get("natgas")), m.get("natgas_pct")))
    L.append(_row("Gold ($/oz)", _num(m.get("gold"), "{:,.2f}"), m.get("gold_change_pct")))
    L.append(_row("Silver ($/oz)", _num(m.get("silver")), m.get("silver_change_pct")))
    L.append(_row("Copper ($/lb)", _num(m.get("copper")), m.get("copper_change_pct")))
    L.append(_row("Gasoline (US avg $/gal)", _num(m.get("gasoline_price"), "{:.3f}"),
                  m.get("gasoline_change_pct")))

    # --- FX & crypto ---
    L.append("")
    L.append("  FX & CRYPTO")
    L.append(_row("US dollar (DXY)", _num(m.get("dxy")), m.get("dxy_change_pct")))
    L.append(_row("Bitcoin ($)", _num(m.get("btc"), "{:,.0f}"), m.get("btc_change_pct")))

    # --- Top drivers ---
    L.append("")
    L.append("  TOP DRIVERS (signed contribution to the score)")
    for label, adj in macro["drivers"][:8]:
        L.append(f"    {adj:+5.1f}  {label}")

    # --- Headlines ---
    heads = [h for h in m.get("headlines", []) if h.get("text")]
    if heads:
        L.append("")
        L.append("  KEY HEADLINES")
        for h in heads:
            mark = {"bullish": "+", "bearish": "-"}.get(h.get("impact"), "·")
            L.append(f"    [{mark}] {h['text']}")

    # --- Economic calendar ---
    events = m.get("events", [])
    if events:
        L.append("")
        L.append("  ECONOMIC CALENDAR (upcoming catalysts)")
        sev = {"high": "!!", "medium": " !", "low": "  "}
        for e in events:
            tag = sev.get(e.get("importance", "low"), "  ")
            when = e.get("date", "")
            L.append(f"    {tag} {when:<14} {e.get('event', '')}")

    L.append("")
    L.append("  " + "-" * 60)
    L.append(f"  Educational pre-opening model - not investment advice. Data as of")
    L.append(f"  {mc.get('as_of', 'n/a')}; offline snapshot - refresh before relying on it.")
    return "\n".join(L)


def _wrap(text, width, indent):
    out, line = [], ""
    for word in text.split():
        if len(line) + len(word) + 1 > width:
            out.append(line)
            line = word
        else:
            line = f"{line} {word}".strip()
    if line:
        out.append(line)
    return ("\n" + indent).join(out)


# --------------------------------------------------------------------------- #
# Self-test                                                                    #
# --------------------------------------------------------------------------- #
def selftest():
    mc = {
        "as_of": "test", "session": "selftest",
        "macro": {
            "futures_closed": True, "prev_close": {"sp500_pct": 1.0, "nasdaq100_pct": 1.9,
                "dow_pct": 0.31, "dow_points": 157, "dow_level": 51650},
            "vix": 16.4, "vix_change_pct": -0.1, "vvix": 88.4, "vvix_change_pct": -6.4,
            "fear_greed": 37, "fear_greed_label": "Fear", "fear_greed_prev": 33,
            "ust10y": 4.46, "ust10y_change_bps": 0, "ust2y": 4.19, "ust2y_change_bps": 0,
            "wti_oil": 77.1, "wti_oil_pct": 0.6, "brent_oil": 79.95, "brent_oil_pct": 0.12,
            "natgas": 3.21, "natgas_pct": -0.82, "gold": 4178.25, "gold_change_pct": 0.4,
            "silver": 66.0, "silver_change_pct": 1.2, "copper": 6.4, "copper_change_pct": 0.3,
            "gasoline_price": 3.973, "gasoline_change_pct": 1.5, "dxy": 100.72, "dxy_change_pct": 0.3,
            "btc": 62500, "btc_change_pct": -2.4,
            "move_index": 65.39, "move_change_pct": -7.47, "hy_oas_pct": 2.78,
            "put_call_equity": 0.59, "vix3m": 18.6, "breakeven_10y": 2.29,
            "global_equities": {"nikkei_pct": 0.28, "hang_seng_pct": -1.59,
                                "euro_stoxx50_pct": -0.08, "dax_pct": 0.03, "ftse100_pct": -0.18},
            "events": [{"date": "2026-06-26", "event": "May PCE inflation", "importance": "high"}],
            "summary": "selftest snapshot",
            "headlines": [{"text": "rally", "impact": "bullish", "weight": 7},
                          {"text": "hawkish fed", "impact": "bearish", "weight": 6}],
        },
    }
    macro = score_macro(mc)
    out = render(mc, macro)
    # Every requested indicator group must appear in the rendered screen.
    for token in ("EQUITY FUTURES", "GLOBAL / OVERNIGHT", "Nikkei", "VIX", "VVIX",
                  "VIX term structure", "Fear & Greed", "Gold", "Silver", "Gasoline",
                  "WTI crude", "US dollar (DXY)", "Bitcoin", "10y breakeven inflation",
                  "CREDIT, RATES-VOL & POSITIONING", "MOVE", "HY credit OAS", "Equity put/call",
                  "ECONOMIC CALENDAR", "TOP DRIVERS"):
        assert token in out, f"missing section/indicator: {token}"
    assert 0 <= macro["score"] <= 100, macro
    # JSON mode round-trips.
    payload = to_json(mc, macro)
    assert json.loads(json.dumps(payload))["score"] == macro["score"]
    print("preopen selftest: all assertions passed")
    print(f"  score {macro['score']} regime {macro['regime']}; "
          f"{len(macro['drivers'])} drivers rendered")
    return 0


def to_json(mc, macro):
    return {
        "as_of": mc.get("as_of"), "session": mc.get("session"),
        "score": macro["score"], "regime": macro["regime"],
        "drivers": [{"label": l, "adj": round(a, 1)} for l, a in macro["drivers"]],
        "macro": mc.get("macro", {}),
    }


# --------------------------------------------------------------------------- #
# CLI                                                                          #
# --------------------------------------------------------------------------- #
def main(argv=None):
    p = argparse.ArgumentParser(description="US stock-market pre-opening screen.")
    p.add_argument("--market-conditions", default=DEFAULT_MC)
    p.add_argument("--json", action="store_true", help="Emit a machine-readable snapshot")
    p.add_argument("--selftest", action="store_true")
    args = p.parse_args(argv)
    if args.selftest:
        return selftest()

    if not os.path.exists(args.market_conditions):
        raise SystemExit(f"ERROR: snapshot not found: {args.market_conditions}")
    mc = load_market_conditions(args.market_conditions)
    macro = score_macro(mc)
    if args.json:
        print(json.dumps(to_json(mc, macro), indent=2, ensure_ascii=False))
    else:
        print(render(mc, macro))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
