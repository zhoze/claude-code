#!/usr/bin/env python3
"""
refresh_sp500.py — re-pull the full S&P 500 dataset from Financial Modeling Prep
================================================================================

Rebuilds data/fundamentals.csv, the technicals in data/market_conditions.json,
and data/sentiment.json (analyst consensus + price targets) for the whole index.

The API key is read from the FMP_KEY environment variable and is NEVER written to
disk or committed. Get a key at https://financialmodelingprep.com and store it as
a secret/env var (GitHub Actions secret, or your shell), not in code.

    FMP_KEY=your_key  python3 refresh_sp500.py            # full S&P 500
    FMP_KEY=your_key  python3 refresh_sp500.py --limit 5  # quick smoke test

Stdlib only (urllib + threads). Uses FMP /stable per-symbol endpoints (bulk
endpoints are plan-restricted). Educational tool — not investment advice.
"""

import argparse
import csv
import json
import os
import sys
import time
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, "data")
BASE = "https://financialmodelingprep.com/stable"

SECTOR = {"Technology": "Information Technology", "Financial Services": "Financials",
          "Consumer Cyclical": "Consumer Discretionary", "Consumer Defensive": "Consumer Staples",
          "Healthcare": "Health Care", "Basic Materials": "Materials"}
CONSENSUS = {"Strong Buy": "strong_buy", "Buy": "buy", "Hold": "hold",
             "Sell": "sell", "Strong Sell": "strong_sell"}


def sect(s):
    return SECTOR.get(s, s or "")


def make_get(key):
    def get(path):
        url = f"{BASE}/{path}{'&' if '?' in path else '?'}apikey={key}"
        for attempt in range(4):
            try:
                with urllib.request.urlopen(url, timeout=30) as r:
                    d = json.loads(r.read().decode())
                if isinstance(d, dict) and ("Error Message" in d or "Restricted" in str(d)[:40]):
                    return None
                return d
            except Exception:
                time.sleep(1.5 * (attempt + 1))
        return None
    return get


def f1(d, k):
    return d[0].get(k) if isinstance(d, list) and d else None


def capped_growth(r):
    for g, yrs in ((r.get("g5"), 5), (r.get("g3"), 3)):
        if g is not None and g > -0.99:
            return round(min(max((1 + g) ** (1.0 / yrs) - 1, 0.0), 0.10), 4)
    g1 = r.get("g1")
    return round(min(max(g1, 0.0), 0.10), 4) if g1 is not None else 0.07


def pull_symbol(get, sym, meta, quote):
    rat = get(f"ratios-ttm?symbol={sym}")
    km = get(f"key-metrics-ttm?symbol={sym}")
    prof = get(f"profile?symbol={sym}")
    rsi = get(f"technical-indicators/rsi?symbol={sym}&periodLength=14&timeframe=1day")
    grad = get(f"grades-consensus?symbol={sym}")
    tgt = get(f"price-target-consensus?symbol={sym}")
    gro = get(f"financial-growth?symbol={sym}&period=annual&limit=1")
    return {
        "symbol": sym, "name": meta.get("name", ""), "sector": meta.get("sector", ""),
        "price": quote.get("price"), "sma50": quote.get("priceAvg50"),
        "sma200": quote.get("priceAvg200"), "volume": quote.get("volume"),
        "beta": f1(prof, "beta") if prof else None,
        "rsi14": rsi[0].get("rsi") if isinstance(rsi, list) and rsi else None,
        "gross_margin": f1(rat, "grossProfitMarginTTM"), "net_margin": f1(rat, "netProfitMarginTTM"),
        "current_ratio": f1(rat, "currentRatioTTM"), "pe": f1(rat, "priceToEarningsRatioTTM"),
        "pb": f1(rat, "priceToBookRatioTTM"), "debt_to_equity": f1(rat, "debtToEquityRatioTTM"),
        "interest_coverage": f1(rat, "interestCoverageRatioTTM"), "dividend_yield": f1(rat, "dividendYieldTTM"),
        "payout_ratio": f1(rat, "dividendPayoutRatioTTM"), "eps": f1(rat, "netIncomePerShareTTM"),
        "book_value_ps": f1(rat, "bookValuePerShareTTM"),
        "roe": f1(km, "returnOnEquityTTM"), "roic": f1(km, "returnOnInvestedCapitalTTM"),
        "net_debt_to_ebitda": f1(km, "netDebtToEBITDATTM"), "income_quality": f1(km, "incomeQualityTTM"),
        "graham_number": f1(km, "grahamNumberTTM"), "fcf_yield": f1(km, "freeCashFlowYieldTTM"),
        "consensus": f1(grad, "consensus"), "target_consensus": f1(tgt, "targetConsensus"),
        "g5": f1(gro, "fiveYNetIncomeGrowthPerShare"), "g3": f1(gro, "threeYNetIncomeGrowthPerShare"),
        "g1": f1(gro, "epsgrowth"),
    }


HDR = ["symbol", "name", "sector", "price", "eps", "owner_earnings_ps", "roe", "roic",
       "gross_margin", "net_margin", "debt_to_equity", "net_debt_to_ebitda", "interest_coverage",
       "current_ratio", "pe", "pb", "book_value_ps", "fcf_yield", "graham_number", "dividend_yield",
       "payout_ratio", "income_quality", "eps_growth_5y"]


def num(x):
    return "" if x is None else x


def write_outputs(recs):
    recs = sorted(recs, key=lambda x: x["symbol"])

    # fundamentals.csv
    rows = []
    for r in recs:
        if r.get("price") is None:
            continue
        rows.append({"symbol": r["symbol"], "name": r["name"], "sector": sect(r["sector"]),
            "price": r["price"], "eps": num(r.get("eps")), "owner_earnings_ps": "",
            "roe": num(r.get("roe")), "roic": num(r.get("roic")), "gross_margin": num(r.get("gross_margin")),
            "net_margin": num(r.get("net_margin")), "debt_to_equity": num(r.get("debt_to_equity")),
            "net_debt_to_ebitda": num(r.get("net_debt_to_ebitda")), "interest_coverage": num(r.get("interest_coverage")),
            "current_ratio": num(r.get("current_ratio")), "pe": num(r.get("pe")), "pb": num(r.get("pb")),
            "book_value_ps": num(r.get("book_value_ps")), "fcf_yield": num(r.get("fcf_yield")),
            "graham_number": num(r.get("graham_number")), "dividend_yield": num(r.get("dividend_yield")),
            "payout_ratio": num(r.get("payout_ratio")), "income_quality": num(r.get("income_quality")),
            "eps_growth_5y": capped_growth(r)})
    with open(os.path.join(DATA, "fundamentals.csv"), "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=HDR)
        w.writeheader()
        w.writerows(rows)

    # market_conditions.json — keep macro/sectors/news; rebuild technicals
    mcp = os.path.join(DATA, "market_conditions.json")
    mc = json.load(open(mcp)) if os.path.exists(mcp) else {"macro": {}, "sectors": {}, "stocks": {}}
    old = mc.get("stocks", {})
    stocks = {}
    for r in recs:
        s = r["symbol"]
        if not (r.get("price") and r.get("sma50") and r.get("sma200")):
            continue
        price, s50, s200 = r["price"], r["sma50"], r["sma200"]
        e = {"sector": sect(r["sector"]), "sentiment": old.get(s, {}).get("sentiment", "neutral"),
             "technicals": {"sma50": round(s50, 2), "sma200": round(s200, 2),
                "rsi14": round(r["rsi14"], 2) if r.get("rsi14") is not None else 50,
                "beta": r.get("beta") or 1.0,
                "perf_month": round((price / s50 - 1) * 100, 1),
                "perf_ytd": round((price / s200 - 1) * 100, 1)}}
        for k in ("near_term_event", "note", "catalysts", "sources"):     # preserve researched news
            if k in old.get(s, {}):
                e[k] = old[s][k]
        stocks[s] = e
    mc["stocks"] = stocks
    json.dump(mc, open(mcp, "w"), indent=2, ensure_ascii=False)

    # sentiment.json — analyst consensus + target upside; overlay researched social
    sjp = os.path.join(DATA, "sentiment.json")
    sj = json.load(open(sjp)) if os.path.exists(sjp) else {"stocks": {}}
    research_social = {k: v.get("social") for k, v in sj.get("stocks", {}).items()
                       if v.get("social") not in (None, "neutral")}
    research_note = {k: v.get("note") for k, v in sj.get("stocks", {}).items() if v.get("note")}
    sent, n_an = {}, 0
    for r in recs:
        s = r["symbol"]
        e = {"social": research_social.get(s, "neutral")}
        if r.get("consensus") in CONSENSUS:
            e["analyst"] = CONSENSUS[r["consensus"]]
            n_an += 1
            if r.get("target_consensus") and r.get("price"):
                e["analyst_upside_pct"] = round((r["target_consensus"] / r["price"] - 1) * 100, 1)
        if s in research_note:
            e["note"] = research_note[s]
        e["sources"] = [{"title": "FMP grades-consensus + price-target-consensus",
                         "url": "https://financialmodelingprep.com/"}]
        if "analyst" in e or e["social"] != "neutral":
            sent[s] = e
    sj["stocks"] = sent
    sj["as_of"] = mc.get("as_of", time.strftime("%Y-%m-%d"))
    json.dump(sj, open(sjp, "w"), indent=2, ensure_ascii=False)
    return len(rows), len(stocks), len(sent), n_an


def main(argv=None):
    p = argparse.ArgumentParser(description="Refresh the full S&P 500 dataset from FMP /stable.")
    p.add_argument("--limit", type=int, default=0, help="Only pull the first N symbols (smoke test)")
    p.add_argument("--workers", type=int, default=12)
    args = p.parse_args(argv)

    key = os.environ.get("FMP_KEY")
    if not key:
        sys.exit("ERROR: set the FMP_KEY environment variable (never hardcode the key).")
    get = make_get(key)

    cons = get("sp500-constituent") or []
    if not cons:
        sys.exit("ERROR: could not fetch S&P 500 constituents (check the key / plan).")
    meta = {c["symbol"]: {"name": c.get("name", ""), "sector": c.get("sector", "")} for c in cons}
    syms = sorted(meta)
    if args.limit:
        syms = syms[:args.limit]
    print(f"constituents: {len(syms)}")

    quotes = {}
    for i in range(0, len(syms), 50):
        chunk = urllib.parse.quote(",".join(syms[i:i + 50]))
        for q in (get(f"batch-quote?symbols={chunk}") or []):
            quotes[q["symbol"]] = q
    print(f"quotes: {len(quotes)}")

    recs = {}
    done = 0
    with ThreadPoolExecutor(max_workers=args.workers) as ex:
        futs = {ex.submit(pull_symbol, get, s, meta[s], quotes.get(s, {})): s for s in syms}
        for fut in as_completed(futs):
            r = fut.result()
            recs[r["symbol"]] = r
            done += 1
            if done % 100 == 0:
                print("...", done)

    nf, nt, ns, na = write_outputs(list(recs.values()))
    print(f"wrote fundamentals.csv ({nf}), market_conditions technicals ({nt}), "
          f"sentiment.json ({ns}, analyst-rated {na})")
    print("Done. Now run:  python3 screener.py  &&  python3 overall.py --all")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
