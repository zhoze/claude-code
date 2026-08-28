#!/usr/bin/env python3
"""Build ta-screener's input contract from the panel data already on disk.

ta-screener expects FMP-shaped inputs. We have Nasdaq daily OHLCV for 502 US large
caps plus SPY. This writes:

    inputs/prices.csv.gz    long: ticker,date,open,high,low,close,volume,vwap,adjclose
    inputs/universe.csv     ticker,company,sector,industry,market_cap
    inputs/benchmarks.csv.gz  long: date,symbol,adjclose (SPY + SPDR sectors)
    inputs/as_of.txt

Notes on fidelity:
  - Nasdaq history is split-adjusted, so adjclose = close and load_panel's
    back-adjust factor is exactly 1.0 (a no-op, which is what we want).
  - vwap is not served, so we use the standard typical-price proxy (H+L+C)/3.
    Only a handful of alphas use vwap; they are flagged in the comparison.
  - No earnings data, so the 12 earnings/PEAD screens will be skipped by the
    runner rather than silently scored.
  - Nasdaq sector names are mapped onto the FMP names config.json expects.
"""
import csv, gzip, json, os, subprocess, sys, time

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, "data500")
ETF_DATA = os.path.join(HERE, "data500_etf")
OUT = os.path.join(HERE, "ta_zip", "ta-screener", "inputs")
os.makedirs(OUT, exist_ok=True)
UA = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
      "Chrome/120 Safari/537.36")

# Nasdaq's sector vocabulary -> the FMP names in ta-screener/config.json
SECTOR_MAP = {
    "Technology": "Technology",
    "Finance": "Financial Services",
    "Industrials": "Industrials",
    "Consumer Discretionary": "Consumer Cyclical",
    "Health Care": "Healthcare",
    "Utilities": "Utilities",
    "Energy": "Energy",
    "Real Estate": "Real Estate",
    "Consumer Staples": "Consumer Defensive",
    "Basic Materials": "Basic Materials",
    "Telecommunications": "Communication Services",
}
SECTOR_ETFS = ["XLK", "XLF", "XLE", "XLV", "XLI", "XLP", "XLY", "XLB", "XLRE", "XLU", "XLC"]


def load_csv(path):
    out = []
    with open(path, newline="", encoding="utf-8-sig") as fh:
        for r in csv.DictReader(fh):
            try:
                o, h, l, c = (float(r["open"]), float(r["high"]),
                              float(r["low"]), float(r["close"]))
                v = float(r["volume"])
            except (TypeError, ValueError, KeyError):
                continue
            if min(o, h, l, c) <= 0 or v < 0:
                continue
            out.append((r["date"], o, h, l, c, v))
    return out


def fetch_etf(sym):
    os.makedirs(ETF_DATA, exist_ok=True)
    path = os.path.join(ETF_DATA, f"{sym}.csv")
    if os.path.exists(path):
        return path
    url = (f"https://api.nasdaq.com/api/quote/{sym}/historical?assetclass=etf"
           f"&fromdate=2019-01-01&todate={time.strftime('%Y-%m-%d')}&limit=99999")
    p = subprocess.run(["curl", "-sSL", "--max-time", "90", "-A", UA, url],
                       capture_output=True, text=True)
    try:
        rows = json.loads(p.stdout)["data"]["tradesTable"]["rows"]
    except Exception:
        return None
    def money(x):
        return float(str(x).replace("$", "").replace(",", "").strip())
    recs = []
    for r in rows:
        try:
            m, d, y = r["date"].split("/")
            recs.append({"date": f"{y}-{m}-{d}", "open": money(r["open"]),
                         "high": money(r["high"]), "low": money(r["low"]),
                         "close": money(r["close"]),
                         "volume": float(str(r["volume"]).replace(",", ""))})
        except Exception:
            continue
    if len(recs) < 400:
        return None
    recs.sort(key=lambda x: x["date"])
    with open(path, "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=["date", "open", "high", "low", "close", "volume"])
        w.writeheader(); w.writerows(recs)
    return path


if __name__ == "__main__":
    meta_rows = json.load(open(os.path.join(HERE, "nasdaq_meta.json")))
    meta = {r["symbol"]: r for r in meta_rows}

    print("fetching sector ETFs...", flush=True)
    bench_syms = ["SPY"]
    for s in SECTOR_ETFS:
        if fetch_etf(s):
            bench_syms.append(s)
        else:
            print(f"  {s}: unavailable", flush=True)
    print(f"  benchmarks: {bench_syms}", flush=True)

    stocks = sorted(f[:-4] for f in os.listdir(DATA)
                    if f.endswith(".csv") and f[:-4] != "SPY")

    # prices.csv.gz
    n_rows = 0
    with gzip.open(os.path.join(OUT, "prices.csv.gz"), "wt", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["ticker", "date", "open", "high", "low", "close",
                    "volume", "vwap", "adjclose"])
        for sym in stocks:
            for d, o, h, l, c, v in load_csv(os.path.join(DATA, f"{sym}.csv")):
                w.writerow([sym, d, o, h, l, c, v, (h + l + c) / 3.0, c])
                n_rows += 1
    print(f"prices.csv.gz: {len(stocks)} tickers, {n_rows} rows", flush=True)

    # universe.csv
    def cap(r):
        try:
            return float(str(r.get("marketCap", "")).replace(",", "") or 0)
        except ValueError:
            return 0.0
    with open(os.path.join(OUT, "universe.csv"), "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["ticker", "company", "sector", "industry", "market_cap"])
        missing = 0
        for sym in stocks:
            r = meta.get(sym)
            if not r:
                missing += 1
                w.writerow([sym, sym, "", "", ""])
                continue
            w.writerow([sym, r.get("name", sym),
                        SECTOR_MAP.get(r.get("sector", ""), ""),
                        r.get("industry", ""), cap(r)])
    print(f"universe.csv: {len(stocks)} tickers ({missing} without metadata)", flush=True)

    # benchmarks.csv.gz — long: date,symbol,adjclose (load_panel pivots it)
    series = {}
    for s in bench_syms:
        src = DATA if s == "SPY" else ETF_DATA
        series[s] = {d: c for d, _o, _h, _l, c, _v in load_csv(os.path.join(src, f"{s}.csv"))}
    all_dates = sorted(set().union(*[set(v) for v in series.values()]))
    with gzip.open(os.path.join(OUT, "benchmarks.csv.gz"), "wt", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["date", "symbol", "adjclose"])
        for d in all_dates:
            for s in bench_syms:
                c = series[s].get(d)
                if c is not None:
                    w.writerow([d, s, c])
    print(f"benchmarks.csv.gz: {len(bench_syms)} symbols, {len(all_dates)} dates", flush=True)

    as_of = max(all_dates)
    open(os.path.join(OUT, "as_of.txt"), "w").write(as_of + "\n")
    print(f"as_of: {as_of}", flush=True)
    print("NOTE: no earnings.csv -> the 12 earnings/PEAD screens will be skipped.")
