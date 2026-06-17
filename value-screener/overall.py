#!/usr/bin/env python3
"""
Overall — the full screening pipeline (trigger: "Pre-screen")
=============================================================

Runs the three lenses in sequence for one stock and blends them into a single
near-term directional read plus the **dominant driver** — the lens most likely
to push the stock up or down right now:

    1. Pre-screen   (macro / fresh-news, near-term)      -> news direction
    2. Warren Buffett (quality + value)                  -> value direction
    3. Magic Elite  (technical / trend)                  -> technical direction
    4. Overall      = weighted blend (0-100, 50 neutral) + dominant driver

Each lens is mapped to a 0-100 *directional* score (50 = neutral, >50 = upward
lean). Value direction comes from the Buffett margin of safety (cheap = upward
room); technical from the Magic score; news from the Pre-screen.

    python3 overall.py --ticker ADBE     # full pipeline for one stock
    python3 overall.py                   # pre-market conditions only (no stock)
    python3 overall.py --selftest

Offline & deterministic; news is gathered from reliable sources at run time and
stored in data/market_conditions.json. Educational only — not investment advice.
"""

import argparse
import os

import prescreen
import magic_lite
import screener

HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_WEIGHTS = {"news": 0.35, "technical": 0.40, "value": 0.25}
LENS_LABEL = {"news": "Pre-screen (news/macro)", "value": "Warren Buffett (value)",
              "technical": "Magic Elite (technical)"}


def clamp(v, lo, hi):
    return max(lo, min(hi, v))


def value_direction(mos):
    """Buffett margin of safety -> 0-100 directional (cheap = upward room)."""
    if mos is None:
        return None
    return round(clamp(50 + mos * 50, 0, 100), 1)


def direction_label(score):
    return "Bullish lean" if score >= 60 else ("Bearish lean" if score < 45 else "Neutral")


def run(ticker, cfg, fundamentals, mc):
    ticker = ticker.upper()
    macro = prescreen.score_macro(mc)
    lenses = {}          # name -> directional 0-100
    detail = {"macro": macro}

    # 1. Pre-screen (news) ---------------------------------------------------
    news = prescreen.score_stock_news(mc, ticker, macro["score"])
    if news:
        lenses["news"] = news["news_score"]
    detail["news"] = news

    # 2. Warren Buffett (value) ---------------------------------------------
    row = next((r for r in fundamentals if r["symbol"] == ticker), None)
    if row:
        b = screener.score_company(row, cfg)
        detail["buffett"] = b
        vd = value_direction(b["margin_of_safety"])
        if vd is not None:
            lenses["value"] = vd

    # 3. Magic Elite (technical) --------------------------------------------
    tech = (news or {}).get("technicals") if news else None
    if row and tech and all(tech.get(k) is not None for k in ("sma50", "sma200", "rsi14")):
        mstock = {"ticker": ticker, "price": row.get("price"), "sma50": tech["sma50"],
                  "sma200": tech["sma200"], "rsi14": tech["rsi14"],
                  "beta": tech.get("beta", 1.0), "perf_month": tech.get("perf_month", 0.0),
                  "perf_ytd": tech.get("perf_ytd", 0.0), "volume": tech.get("volume", 0)}
        mg = magic_lite.compute_magic_lite(mstock)
        detail["magic"] = mg
        lenses["technical"] = float(mg["magic_score"])

    # 4. Overall blend + dominant driver ------------------------------------
    weights = cfg.get("overall_weights", DEFAULT_WEIGHTS)
    avail = {k: v for k, v in lenses.items() if k in weights}
    wsum = sum(weights[k] for k in avail) or 1.0
    overall = round(sum(v * weights[k] for k, v in avail.items()) / wsum, 1) if avail else None
    # dominant = lens pushing hardest away from neutral, weighted
    dominant = max(avail, key=lambda k: abs(avail[k] - 50) * weights[k] / wsum) if avail else None

    detail["lenses"] = lenses
    detail["overall"] = overall
    detail["dominant"] = dominant
    detail["weights"] = {k: weights[k] for k in avail}
    return detail


# --------------------------------------------------------------------------- #
# Rendering                                                                    #
# --------------------------------------------------------------------------- #
def _arrow(score):
    return "▲" if score >= 60 else ("▼" if score < 45 else "►")


def report(ticker, d):
    L = [f"############ OVERALL SCREEN — {ticker.upper()} ############\n"]

    # 1. Pre-screen
    L.append(prescreen.macro_report({"as_of": "", "session": "", **{}}, d["macro"]).splitlines()[1])
    if d.get("news"):
        n = d["news"]
        L.append(f"1. PRE-SCREEN (news/macro)  {_arrow(n['news_score'])} {n['news_score']}/100   "
                 f"sentiment {n['sentiment']}, sector {n['sector']} ({n['sector_bias']})")
        if n.get("note"): L.append(f"   {n['note']}")
    else:
        L.append("1. PRE-SCREEN (news/macro)  n/a   (no news entry — refresh market_conditions.json)")

    # 2. Buffett
    if d.get("buffett"):
        b = d["buffett"]
        vd = d["lenses"].get("value")
        L.append(f"2. WARREN BUFFETT (value)   {_arrow(vd) if vd is not None else '·'} "
                 f"{vd if vd is not None else 'n/a'}/100   "
                 f"score {b['buffett_score']}/100 [{b['verdict']}], "
                 f"margin of safety {b['margin_of_safety']*100:+.1f}%" if b['margin_of_safety'] is not None
                 else f"2. WARREN BUFFETT (value)   score {b['buffett_score']} [{b['verdict']}]")
    else:
        L.append("2. WARREN BUFFETT (value)   n/a   (ticker not in fundamentals.csv)")

    # 3. Magic
    if d.get("magic"):
        mg = d["magic"]
        gate = {1: "LONG-OK", -1: "SHORT-OK", 0: "NO-TRADE"}[mg["gate"]]
        L.append(f"3. MAGIC ELITE (technical)  {_arrow(mg['magic_score'])} {mg['magic_score']}/100   "
                 f"{mg['territory']} territory, {gate}, Zone {mg['zone']}/6, {mg['candle']} candle")
    else:
        L.append("3. MAGIC ELITE (technical)  n/a   (no technicals — add to market_conditions.json)")

    # 4. Overall
    L.append("")
    if d["overall"] is not None:
        L.append(f"4. OVERALL DIRECTION  {_arrow(d['overall'])} {d['overall']}/100  "
                 f"→ {direction_label(d['overall'])}")
        L.append(f"   Dominant driver: {LENS_LABEL[d['dominant']]} "
                 f"(score {d['lenses'][d['dominant']]}, weight {d['weights'][d['dominant']]:.0%}) "
                 f"— the lens most influencing {ticker.upper()}'s near-term move.")
    else:
        L.append("4. OVERALL DIRECTION  n/a (no lenses available)")
    L.append("\nDirectional 0-100: 50 = neutral, >60 upward lean, <45 downward lean. "
             "Educational only — not investment advice.")
    return "\n".join(L)


def screen_all(cfg, fundamentals, mc):
    rows = []
    for r in fundamentals:
        d = run(r["symbol"], cfg, fundamentals, mc)
        rows.append((r["symbol"], d))
    rows.sort(key=lambda x: (x[1]["overall"] if x[1]["overall"] is not None else -1), reverse=True)
    return rows


def all_table(rows):
    L = ["#### OVERALL SCREEN — all names (Pre-screen -> Buffett -> Magic -> Overall) ####",
         "Directional 0-100 (50 neutral). News=Pre-screen, Value=Buffett MOS, Tech=Magic.\n"]
    L.append(f"{'#':>2}  {'TICK':<5} {'NEWS':>5} {'VALUE':>6} {'TECH':>5} {'OVERALL':>8}  {'LEAN':<13} DOMINANT DRIVER")
    fmt = lambda x: "  -  " if x is None else f"{x:5.1f}"
    for i, (sym, d) in enumerate(rows, 1):
        ln = d["lenses"]
        lean = direction_label(d["overall"]) if d["overall"] is not None else "n/a"
        dom = LENS_LABEL.get(d["dominant"], "-").split(" (")[0] if d["dominant"] else "-"
        L.append(f"{i:>2}  {sym:<5} {fmt(ln.get('news'))} {fmt(ln.get('value'))} "
                 f"{fmt(ln.get('technical'))} {(fmt(d['overall'])).rjust(8)}  {lean:<13} {dom}")
    return "\n".join(L)


def write_all_outputs(rows, mc, csv_path, md_path):
    import csv as _csv
    os.makedirs(os.path.dirname(csv_path), exist_ok=True)
    with open(csv_path, "w", newline="") as fh:
        w = _csv.writer(fh)
        w.writerow(["rank", "symbol", "news_dir", "value_dir", "tech_dir", "overall", "lean", "dominant"])
        for i, (sym, d) in enumerate(rows, 1):
            ln = d["lenses"]
            w.writerow([i, sym, ln.get("news"), ln.get("value"), ln.get("technical"),
                        d["overall"], direction_label(d["overall"]) if d["overall"] is not None else "n/a",
                        d["dominant"] or ""])
    with open(md_path, "w") as fh:
        fh.write(f"# Overall screen — Pre-screen → Buffett → Magic → Overall\n\n"
                 f"_Market snapshot {mc.get('as_of','n/a')} ({mc.get('session','')}); "
                 f"regime {prescreen.score_macro(mc)['regime']}. Directional 0-100, 50=neutral. "
                 f"Educational — not investment advice._\n\n")
        fh.write("| # | Ticker | News | Value | Tech | Overall | Lean | Dominant driver |\n")
        fh.write("|---:|:--|---:|---:|---:|---:|:--|:--|\n")
        for i, (sym, d) in enumerate(rows, 1):
            ln = d["lenses"]
            g = lambda x: "—" if x is None else f"{x:.0f}"
            dom = LENS_LABEL.get(d["dominant"], "—") if d["dominant"] else "—"
            fh.write(f"| {i} | {sym} | {g(ln.get('news'))} | {g(ln.get('value'))} | {g(ln.get('technical'))} | "
                     f"{g(d['overall'])} | {direction_label(d['overall']) if d['overall'] is not None else 'n/a'} | {dom} |\n")


def selftest():
    cfg = screener.load_config(screener.DEFAULT_CONFIG)
    fundamentals = screener.load_fundamentals(screener.DEFAULT_INPUT)
    mc = prescreen.load_market_conditions(prescreen.DEFAULT_MC)
    d = run("ADBE", cfg, fundamentals, mc)
    assert d["lenses"].get("technical") == 0.0, d["lenses"]      # ADBE deep bear technically
    assert d["lenses"].get("value", 0) > 60, d["lenses"]          # cheap -> upward value room
    assert d["overall"] is not None and d["dominant"] in d["weights"], d
    print("overall selftest: passed —", {k: round(v, 1) for k, v in d["lenses"].items()},
          "overall", d["overall"], "dominant", d["dominant"])
    return 0


def main(argv=None):
    p = argparse.ArgumentParser(description="Overall screening pipeline (Pre-screen -> Buffett -> Magic -> Overall).")
    p.add_argument("--ticker", help="Stock to run the full pipeline on")
    p.add_argument("--config", default=screener.DEFAULT_CONFIG)
    p.add_argument("--input", default=screener.DEFAULT_INPUT)
    p.add_argument("--market-conditions", default=prescreen.DEFAULT_MC)
    p.add_argument("--all", action="store_true", help="Run every name in fundamentals.csv and print a ranked table")
    p.add_argument("--selftest", action="store_true")
    args = p.parse_args(argv)
    if args.selftest:
        return selftest()

    cfg = screener.load_config(args.config)
    mc = prescreen.load_market_conditions(args.market_conditions)

    if args.all:
        fundamentals = screener.load_fundamentals(args.input)
        rows = screen_all(cfg, fundamentals, mc)
        print(prescreen.macro_report(mc, prescreen.score_macro(mc)).splitlines()[0])
        print(prescreen.score_macro(mc)["regime"], "regime |", mc.get("as_of"), "\n")
        print(all_table(rows))
        csv_path = os.path.join(HERE, "data", "results", "overall_screen.csv")
        md_path = os.path.join(HERE, "data", "results", "overall_screen.md")
        write_all_outputs(rows, mc, csv_path, md_path)
        print(f"\nWrote {csv_path} and {md_path}")
        return 0

    if not args.ticker:
        # Pre-market conditions only.
        print(prescreen.macro_report(mc, prescreen.score_macro(mc)))
        print("\nNo --ticker given: showing overall pre-market conditions only. "
              "Add --ticker SYM for the full Pre-screen→Buffett→Magic→Overall run.")
        return 0

    fundamentals = screener.load_fundamentals(args.input)
    d = run(args.ticker, cfg, fundamentals, mc)
    print(report(args.ticker, d))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
