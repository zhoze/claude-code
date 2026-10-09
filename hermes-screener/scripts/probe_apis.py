#!/usr/bin/env python3
"""Probe every FMP and Massive endpoint Hermes uses, once, and print the HTTP status.

  FMP_KEY=... MASSIVE_KEY=... python3 scripts/probe_apis.py [TICKER]

Use it to find plan/permission problems in seconds (403 = not in your plan,
429 = rate limited, 404 = wrong path). Keys are read from the environment and
never printed. Exit code 1 if any endpoint the pipeline needs is unusable.
"""
from __future__ import annotations

import datetime as dt
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

FMP = "https://financialmodelingprep.com/stable"
MASSIVE = "https://api.massive.com"


def get(url: str, bearer: str | None = None):
    headers = {"User-Agent": "hermes-screener-probe/0.1", "Accept": "application/json"}
    if bearer:
        headers["Authorization"] = f"Bearer {bearer}"
    t0 = time.perf_counter()
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers=headers),
                                    timeout=30) as r:
            body = json.loads(r.read().decode("utf-8", "replace"))
            return r.status, body, time.perf_counter() - t0
    except urllib.error.HTTPError as e:
        try:
            body = json.loads(e.read().decode("utf-8", "replace"))
        except Exception:  # noqa: BLE001
            body = None
        return e.code, body, time.perf_counter() - t0
    except Exception as e:  # noqa: BLE001
        return type(e).__name__, None, time.perf_counter() - t0


def summarize(body) -> str:
    if isinstance(body, list):
        return f"list[{len(body)}]"
    if isinstance(body, dict):
        res = body.get("results")
        n = len(res) if isinstance(res, list) else ("tickers" in body and len(body["tickers"]))
        msg = body.get("message") or body.get("error") or body.get("Error Message") or ""
        first = res[0] if isinstance(res, list) and res else None
        extra = ""
        if first and "t" in first:
            extra = f" first={dt.datetime.utcfromtimestamp(first['t'] / 1000).date()}"
        return f"status={body.get('status')} n={n}{extra} {str(msg)[:120]}"
    return str(body)[:120]


def main() -> int:
    sym = (sys.argv[1] if len(sys.argv) > 1 else "AAPL").upper()
    today = dt.date.today()
    y3 = (today - dt.timedelta(days=3 * 365 + 30)).isoformat()
    y5 = (today - dt.timedelta(days=5 * 365 - 10)).isoformat()
    y8 = (today - dt.timedelta(days=8 * 365)).isoformat()
    fk, mk = os.environ.get("FMP_KEY"), os.environ.get("MASSIVE_KEY")
    needed_bad = []
    rows = []
    if fk:
        for name, path, needed in [
            ("fmp company-screener", "company-screener?exchange=NASDAQ&country=US&limit=5", True),
            ("fmp key-metrics", f"key-metrics?symbol={sym}&period=annual&limit=5", True),
            ("fmp ratios", f"ratios?symbol={sym}&period=annual&limit=5", True),
            ("fmp income-statement", f"income-statement?symbol={sym}&period=annual&limit=5", True),
            ("fmp balance-sheet", f"balance-sheet-statement?symbol={sym}&period=annual&limit=5", True),
            ("fmp cash-flow", f"cash-flow-statement?symbol={sym}&period=annual&limit=5", True),
            ("fmp profile", f"profile?symbol={sym}", True),
            ("fmp quote", f"quote?symbol={sym}", True),
            ("fmp earnings", f"earnings?symbol={sym}&limit=16", True),
        ]:
            sep = "&" if "?" in path else "?"
            code, body, s = get(f"{FMP}/{path}{sep}{urllib.parse.urlencode({'apikey': fk})}")
            rows.append((name, code, s, summarize(body)))
            if needed and code != 200:
                needed_bad.append(name)
    else:
        print("FMP_KEY not set — skipping FMP")
    if mk:
        for name, path, needed in [
            ("massive aggs raw 3y", f"v2/aggs/ticker/{sym}/range/1/day/{y3}/{today}?adjusted=false&limit=50000", True),
            ("massive aggs adj 3y", f"v2/aggs/ticker/{sym}/range/1/day/{y3}/{today}?adjusted=true&limit=50000", True),
            ("massive aggs SPY 5y", f"v2/aggs/ticker/SPY/range/1/day/{y5}/{today}?adjusted=true&limit=50000", True),
            ("massive aggs SPY 8y", f"v2/aggs/ticker/SPY/range/1/day/{y8}/{today}?adjusted=true&limit=50000", False),
            ("massive dividends", f"stocks/v1/dividends?ticker={sym}&limit=10", False),
            ("massive options contracts", f"v3/reference/options/contracts?underlying_ticker={sym}&limit=1", False),
            ("massive snapshot NBBO", f"v2/snapshot/locale/us/markets/stocks/tickers?tickers={sym}", False),
        ]:
            code, body, s = get(f"{MASSIVE}/{path}", bearer=mk)
            rows.append((name, code, s, summarize(body)))
            if needed and code != 200:
                needed_bad.append(name)
    else:
        print("MASSIVE_KEY not set — skipping Massive")
    w = max(len(r[0]) for r in rows) if rows else 10
    for name, code, s, info in rows:
        print(f"{name:<{w}}  {str(code):>5}  {s:5.2f}s  {info}")
    if needed_bad:
        print(f"\nREQUIRED endpoints failing: {needed_bad}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
