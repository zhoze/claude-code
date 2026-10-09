"""Build the FMP input snapshot for the 150 technical screens.

Fetches, for the ~1000 largest US common stocks (Russell 1000 proxy, same discovery
as value-screener/screens):
  - 3 years of raw daily OHLCV+vwap        (stable/historical-price-eod/full)
  - 3 years of dividend/split-adjusted close (stable/historical-price-eod/dividend-adjusted)
  - the last ~16 quarters of EPS/revenue actual-vs-estimate + next scheduled date
                                             (stable/earnings)
plus adjusted closes for SPY + the 11 SPDR sector ETFs, and writes:

  inputs/prices.csv.gz     ticker,date,open,high,low,close,volume,vwap,adjclose
  inputs/earnings.csv      ticker,date,eps_actual,eps_est,rev_actual,rev_est
  inputs/universe.csv      ticker,company,sector,industry,market_cap
  inputs/benchmarks.csv.gz symbol,date,adjclose
  inputs/as_of.txt         snapshot provenance line
  inputs/fetch_failures.txt  symbols that failed all retries (absent from panel, never 0-filled)

The API key comes from the FMP_KEY environment variable — never hardcode or commit it.
Full run is ~2,000 price calls + ~1,000 earnings calls + 12 benchmark calls.

Usage:
  FMP_KEY=... python3 build_screen_inputs.py --target 1000
  FMP_KEY=... python3 build_screen_inputs.py --limit 25            # smoke test
  FMP_KEY=... python3 build_screen_inputs.py --resume              # only missing symbols
  FMP_KEY=... python3 build_screen_inputs.py --universe AAPL,MSFT  # explicit tickers
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import sys
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed

import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
INPUTS = os.path.join(HERE, "inputs")
BASE = "https://financialmodelingprep.com/stable"

# Non-common-equity names that slip past the equity screener (same guard as the
# value-screener template): baby bonds, notes, preferred/depositary shares.
NON_EQUITY_RE = re.compile(
    r"\d%|\d+\.\d+\s*$|Notes\s+due|Subordinated|Debentures?|Depositary|\bPfd\b"
    r"|Preferred\s+(?:Stock|Shares|Series)|Cumulative\s+Preferred",
    re.I,
)


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


def discover_universe(get, target=1000):
    """Largest ~target US common stocks by market cap, with screener metadata."""
    seen = {}
    for exch in ("NASDAQ", "NYSE"):
        q = (f"company-screener?exchange={exch}&country=US&isEtf=false&isFund=false"
             f"&isActivelyTrading=true&marketCapMoreThan=1500000000&limit=3000")
        for row in (get(q) or []):
            sym = row.get("symbol")
            name = row.get("companyName") or ""
            mc = row.get("marketCap") or 0
            if not sym or "." in sym or "-" in sym:
                continue
            if NON_EQUITY_RE.search(name):
                continue
            if sym not in seen or mc > (seen[sym].get("market_cap") or 0):
                seen[sym] = {
                    "ticker": sym,
                    "company": name,
                    "sector": row.get("sector"),
                    "industry": row.get("industry"),
                    "market_cap": mc,
                }
    ranked = sorted(seen.values(), key=lambda r: r["market_cap"] or 0, reverse=True)
    return ranked[:target]


def fetch_symbol(get, sym, frm, to):
    """One symbol's price rows + earnings rows, or None if the price fetch failed."""
    full = get(f"historical-price-eod/full?symbol={sym}&from={frm}&to={to}")
    adj = get(f"historical-price-eod/dividend-adjusted?symbol={sym}&from={frm}&to={to}")
    if not isinstance(full, list) or not full:
        return None
    adj_by_date = {}
    if isinstance(adj, list):
        for row in adj:
            adj_by_date[row.get("date")] = row.get("adjClose")
    prices = []
    for row in full:
        d = row.get("date")
        c = row.get("close")
        if not d or c is None or c <= 0:
            continue
        prices.append({
            "ticker": sym, "date": d,
            "open": row.get("open"), "high": row.get("high"), "low": row.get("low"),
            "close": c, "volume": row.get("volume"), "vwap": row.get("vwap"),
            "adjclose": adj_by_date.get(d, c),
        })

    earnings = []
    earn = get(f"earnings?symbol={sym}&limit=16")
    if isinstance(earn, list):
        for row in earn:
            d = row.get("date")
            if not d:
                continue
            earnings.append({
                "ticker": sym, "date": d,
                "eps_actual": row.get("epsActual"), "eps_est": row.get("epsEstimated"),
                "rev_actual": row.get("revenueActual"), "rev_est": row.get("revenueEstimated"),
            })
    return prices, earnings


def fetch_benchmarks(get, symbols, frm, to):
    rows = []
    for sym in symbols:
        adj = get(f"historical-price-eod/dividend-adjusted?symbol={sym}&from={frm}&to={to}")
        if not isinstance(adj, list):
            print(f"  WARNING: benchmark {sym} failed", file=sys.stderr)
            continue
        for row in adj:
            if row.get("date") and row.get("adjClose"):
                rows.append({"symbol": sym, "date": row["date"], "adjclose": row["adjClose"]})
    return pd.DataFrame(rows)


def sanity_filter(prices: pd.DataFrame, min_bars: int):
    """Drop symbols with too little history or non-positive prices; report reasons."""
    dropped = {}
    prices = prices[pd.to_numeric(prices["close"], errors="coerce") > 0]
    counts = prices.groupby("ticker")["date"].count()
    ok = counts[counts >= min_bars].index
    for t in counts[counts < min_bars].index:
        dropped[t] = f"only {counts[t]} bars (<{min_bars})"
    return prices[prices["ticker"].isin(ok)], dropped


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--target", type=int, default=None, help="universe size (default from config)")
    ap.add_argument("--limit", type=int, default=None, help="fetch only the first N symbols (smoke)")
    ap.add_argument("--universe", default=None, help="comma-separated explicit tickers")
    ap.add_argument("--years", type=int, default=None, help="history depth (default from config)")
    ap.add_argument("--resume", action="store_true",
                    help="keep existing inputs; fetch only symbols missing from prices.csv.gz")
    args = ap.parse_args(argv)

    key = os.environ.get("FMP_KEY")
    if not key:
        print("FMP_KEY environment variable not set — cannot fetch. "
              "(In CI this comes from the GitHub 'screen' environment secret.)", file=sys.stderr)
        return 2

    with open(os.path.join(HERE, "config.json")) as f:
        cfg = json.load(f)
    target = args.target or cfg["universe"]["target"]
    years = args.years or cfg["history_years"]
    min_bars = cfg["universe"]["min_price_bars"]

    today = dt.date.today()
    frm = (today - dt.timedelta(days=int(years * 365.25) + 30)).isoformat()
    to = today.isoformat()
    get = make_get(key)

    if args.universe:
        universe = [{"ticker": t.strip().upper(), "company": None, "sector": None,
                     "industry": None, "market_cap": None}
                    for t in args.universe.split(",") if t.strip()]
    else:
        print(f"Discovering universe (target {target}) ...")
        universe = discover_universe(get, target)
        if not universe:
            print("universe discovery failed (plan limits or network?)", file=sys.stderr)
            return 1
    if args.limit:
        universe = universe[: args.limit]
    symbols = [u["ticker"] for u in universe]
    print(f"Universe: {len(symbols)} symbols; history {frm} .. {to}")

    os.makedirs(INPUTS, exist_ok=True)
    prices_path = os.path.join(INPUTS, "prices.csv.gz")
    earn_path = os.path.join(INPUTS, "earnings.csv")

    old_prices = old_earnings = None
    todo = symbols
    if args.resume and os.path.exists(prices_path):
        old_prices = pd.read_csv(prices_path)
        have = set(old_prices["ticker"].unique())
        if os.path.exists(earn_path):
            old_earnings = pd.read_csv(earn_path)
        todo = [s for s in symbols if s not in have]
        print(f"--resume: {len(have)} symbols already present, fetching {len(todo)}")

    price_rows, earn_rows, failures = [], [], []
    done = 0
    with ThreadPoolExecutor(max_workers=8) as pool:
        futures = {pool.submit(fetch_symbol, get, s, frm, to): s for s in todo}
        for fut in as_completed(futures):
            sym = futures[fut]
            try:
                res = fut.result()
            except Exception as e:  # noqa: BLE001 — a symbol failure must not kill the run
                res, _ = None, print(f"  {sym}: {e}", file=sys.stderr)
            if res is None:
                failures.append(sym)
            else:
                p, e = res
                price_rows.extend(p)
                earn_rows.extend(e)
            done += 1
            if done % 100 == 0:
                print(f"  {done}/{len(todo)} fetched ({len(failures)} failures)")

    prices = pd.DataFrame(price_rows)
    earnings = pd.DataFrame(earn_rows)
    if old_prices is not None:
        prices = pd.concat([old_prices, prices], ignore_index=True)
    if old_earnings is not None:
        earnings = pd.concat([old_earnings, earnings], ignore_index=True)
    if prices.empty:
        print("no price data fetched — aborting without writing", file=sys.stderr)
        return 1

    prices = prices.drop_duplicates(subset=["ticker", "date"], keep="last")
    prices = prices.sort_values(["ticker", "date"])
    prices, dropped = sanity_filter(prices, min_bars)
    if not earnings.empty:
        earnings = (earnings.drop_duplicates(subset=["ticker", "date"], keep="last")
                    .sort_values(["ticker", "date"]))

    kept = prices["ticker"].unique()
    uni = pd.DataFrame(universe)
    if args.resume and old_prices is not None:
        uni = pd.concat([uni, pd.DataFrame({"ticker": list(set(kept) - set(uni["ticker"]))})],
                        ignore_index=True)
    uni = uni[uni["ticker"].isin(kept)].drop_duplicates("ticker")

    # numeric compaction: prices to 4dp, volume to int
    for col in ("open", "high", "low", "close", "vwap", "adjclose"):
        prices[col] = pd.to_numeric(prices[col], errors="coerce").round(4)
    prices["volume"] = pd.to_numeric(prices["volume"], errors="coerce").fillna(0).astype("int64")

    bench_syms = [cfg["benchmarks"]["market"], *cfg["benchmarks"]["sector_etfs"].values()]
    print(f"Fetching {len(bench_syms)} benchmarks ...")
    bench = fetch_benchmarks(get, bench_syms, frm, to)

    prices.to_csv(prices_path, index=False, compression="gzip")
    earnings.to_csv(earn_path, index=False)
    uni.to_csv(os.path.join(INPUTS, "universe.csv"), index=False)
    if not bench.empty:
        bench.to_csv(os.path.join(INPUTS, "benchmarks.csv.gz"), index=False, compression="gzip")
    with open(os.path.join(INPUTS, "fetch_failures.txt"), "w") as f:
        f.write("\n".join(sorted(failures + sorted(dropped))) + "\n" if (failures or dropped) else "")
    n_earn = earnings["ticker"].nunique() if not earnings.empty else 0
    with open(os.path.join(INPUTS, "as_of.txt"), "w") as f:
        f.write(f"{to}; {len(kept)} tickers; {n_earn} with earnings; "
                f"{len(failures)} fetch failures; {len(dropped)} dropped by sanity filter\n")

    print(f"Wrote inputs/: {len(kept)} tickers, {len(prices)} price rows, "
          f"{len(earnings)} earnings rows, {bench['symbol'].nunique() if not bench.empty else 0} "
          f"benchmarks. Failures: {sorted(failures)[:10]}{'...' if len(failures) > 10 else ''}")
    for t, why in list(dropped.items())[:10]:
        print(f"  dropped {t}: {why}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
