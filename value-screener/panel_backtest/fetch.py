#!/usr/bin/env python3
"""Fetch daily OHLCV from Nasdaq's public historical endpoint into CSVs."""
import csv, json, os, subprocess, sys, time

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
os.makedirs(OUT, exist_ok=True)
UA = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
      "Chrome/120 Safari/537.36")

# Dow Jones Industrial Average constituents + SPY benchmark + a few large-cap
# laggards/non-Dow names so the universe is not purely blue-chip survivors.
DOW = ["AAPL","AMGN","AXP","BA","CAT","CRM","CSCO","CVX","DIS","DOW","GS","HD",
       "HON","IBM","JNJ","JPM","KO","MCD","MMM","MRK","MSFT","NKE","NVDA","PG",
       "SHW","TRV","UNH","V","VZ","WMT"]
EXTRA = ["INTC","PFE","T","F","WBA","PYPL","MRNA","XOM"]
BENCH = ["SPY"]
SYMBOLS = BENCH + DOW + EXTRA

FROM, TO = "2019-01-01", "2026-08-26"


def money(x):
    return float(str(x).replace("$", "").replace(",", "").strip())


def fetch(sym, attempt=1):
    url = (f"https://api.nasdaq.com/api/quote/{sym}/historical"
           f"?assetclass=stocks&fromdate={FROM}&todate={TO}&limit=99999")
    p = subprocess.run(["curl", "-sSL", "--max-time", "60", "-A", UA, url],
                       capture_output=True, text=True)
    try:
        payload = json.loads(p.stdout)
    except Exception:
        if attempt < 3:
            time.sleep(3 * attempt)
            return fetch(sym, attempt + 1)
        return None, "unparseable response"
    data = (payload or {}).get("data")
    if not data or not data.get("tradesTable"):
        if attempt < 3:
            time.sleep(3 * attempt)
            return fetch(sym, attempt + 1)
        return None, str((payload or {}).get("status", {}).get("bCodeMessage")) or "no data"

    rows = []
    for r in data["tradesTable"]["rows"]:
        try:
            m, d, y = r["date"].split("/")
            vol = float(str(r["volume"]).replace(",", ""))
            rows.append({
                "date": f"{y}-{m}-{d}",
                "open": money(r["open"]), "high": money(r["high"]),
                "low": money(r["low"]), "close": money(r["close"]),
                "volume": vol,
            })
        except Exception:
            continue
    if len(rows) < 400:
        return None, f"only {len(rows)} rows"
    rows.sort(key=lambda x: x["date"])
    path = os.path.join(OUT, f"{sym}.csv")
    with open(path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["date", "open", "high", "low", "close", "volume"])
        w.writeheader()
        w.writerows(rows)
    return len(rows), f"{rows[0]['date']}..{rows[-1]['date']}"


if __name__ == "__main__":
    ok, bad = [], []
    for s in SYMBOLS:
        n, info = fetch(s)
        if n:
            ok.append(s)
            print(f"  {s:5s} {n:5d} bars  {info}", flush=True)
        else:
            bad.append((s, info))
            print(f"  {s:5s} FAILED: {info}", flush=True)
        time.sleep(0.7)
    print(f"\nfetched {len(ok)}/{len(SYMBOLS)}")
    if bad:
        print("failed:", bad)
