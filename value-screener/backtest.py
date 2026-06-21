#!/usr/bin/env python3
"""
backtest.py — walk-forward test of the Buffett screen
=====================================================

Re-runs the Buffett value screen as it would have looked ~N days ago (default ~60,
i.e. "2 months ago") and evaluates the picks by their **forward price return** to the
latest close.

How it works (transparent, reproducible):
  1. Read the universe symbol list from data/universe.csv.
  2. For every symbol, pull ~90 days of end-of-day closes from FMP (one call each).
     The LAST bar is treated as "now"; the close ~`--days` calendar days before it
     (the nearest trading day on/before) is the "as-of" price. This self-calibrates to
     whatever date the data reflects — no reliance on the runner clock.
  3. Build fundamentals_asof.csv = the current fundamentals snapshot with the `price`
     column swapped for the as-of price, then run screener.py on it to get the
     as-of top-N (the engine is reused unchanged).
  4. Forward return per pick = now_price / asof_price - 1.
  5. Benchmark = equal-weight forward return of the whole priced universe (and SPY).

IMPORTANT APPROXIMATION: only price is rewound. The TTM fundamentals (margins, ROIC,
growth, book value, etc.) are the CURRENT values, used as a proxy for what they were
~2 months ago. Over a short window these move little, but this introduces mild
look-ahead on the quality/intrinsic-value inputs. Treat results as indicative, not a
clean point-in-time backtest. Educational only — not investment advice.

The API key is read from FMP_KEY and never written to disk or committed.
"""

import argparse
import csv
import datetime as dt
import json
import os
import re
import subprocess
import sys
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, "data")
BASE = "https://financialmodelingprep.com/stable"


def make_get(key):
    def get(path):
        url = f"{BASE}/{path}{'&' if '?' in path else '?'}apikey={key}"
        for attempt in range(4):
            try:
                with urllib.request.urlopen(url, timeout=30) as r:
                    return json.loads(r.read().decode())
            except Exception:
                pass
        return None
    return get


def series(get, sym, lookback_days):
    """Return sorted [(date, close)] for the last ~lookback_days days (historical EOD)."""
    end = dt.date.today()
    start = end - dt.timedelta(days=lookback_days)
    d = get(f"historical-price-eod/light?symbol={sym}&from={start}&to={end}")
    if not isinstance(d, list) or not d:
        return []
    out = []
    for row in d:
        c = row.get("price", row.get("close"))
        if row.get("date") and c is not None:
            out.append((row["date"][:10], float(c)))
    out.sort(key=lambda x: x[0])
    return out


# Map a "~N days back" request to the nearest standard quote-change window.
def change_field(days):
    return "1M" if days <= 45 else ("3M" if days <= 135 else "6M")


def tech_asof(get, sym, kind, period, asof_date):
    """Value of an FMP technical indicator (sma/rsi) on the nearest trading day <= asof_date."""
    frm = (dt.date.fromisoformat(asof_date) - dt.timedelta(days=20)).isoformat()
    d = get(f"technical-indicators/{kind}?symbol={sym}&periodLength={period}"
            f"&timeframe=1day&from={frm}&to={asof_date}")
    if not isinstance(d, list) or not d:
        return None
    rows = sorted([r for r in d if r.get("date")], key=lambda r: r["date"])
    val = None
    for r in rows:
        if r["date"][:10] <= asof_date:
            val = r.get(kind, r.get("value"))
    return val


def magic_overlay(get, picks, prices, asof_date, fund, fwd, top, hereroot):
    """For each as-of pick, score the Elite Magic trend with as-of technicals and report
    which picks pass a simple trend gate (Magic >= 50 and not a SHORT weekly->daily call)."""
    cli = os.path.join(hereroot, "stock-screener", "cli.js")
    mcp = os.path.join(DATA, "market_conditions.json")
    betas = {}
    try:
        for s, v in json.load(open(mcp)).get("stocks", {}).items():
            betas[s] = v.get("technicals", {}).get("beta")
    except Exception:
        pass

    def fnum(s, k):
        try:
            return float(fund[s][k])
        except Exception:
            return None

    print("\n" + "=" * 64)
    print(f"  ELITE MAGIC TREND OVERLAY on the as-of {asof_date} Buffett top {top}")
    print("=" * 64)
    print(f"  {'TICKER':<6} {'magic':>5} {'align':>6} {'pass?':>5} {'fwd %':>8}")
    kept, dropped = [], []
    for r in picks:
        s = r["symbol"]; a = prices.get(s)
        if not a:
            continue
        price = a[1]
        sma50 = tech_asof(get, s, "sma", 50, asof_date)
        sma200 = tech_asof(get, s, "sma", 200, asof_date)
        rsi = tech_asof(get, s, "rsi", 14, asof_date)
        if not (sma50 and sma200 and rsi):
            print(f"  {s:<6} {'n/a (no as-of technicals)':>30}")
            continue
        beta = betas.get(s) or 1.0
        pe = fnum(s, "pe"); roic = fnum(s, "roic")
        argv = ["node", cli, s, "--price", str(price), "--sma50", str(sma50),
                "--sma200", str(sma200), "--rsi", str(rsi), "--beta", str(beta),
                "--perf-month", str(round((price / sma50 - 1) * 100, 2)),
                "--perf-ytd", str(round((price / sma200 - 1) * 100, 2)),
                "--volume", "1000000"]
        if pe is not None:
            argv += ["--pe", str(pe)]
        if roic is not None:
            argv += ["--roic", str(round(roic * 100, 2))]
        out = subprocess.run(argv, capture_output=True, text=True).stdout
        m = re.search(r"MAGIC SCORE.*?:\s*(-?\d+)", out)
        magic = int(m.group(1)) if m else None
        am = re.search(r"alignment:\s*([A-Z/]+)", out)
        align = am.group(1) if am else "?"
        passed = (magic is not None and magic >= 50 and align != "SHORT")
        fr = fwd(s)
        (kept if passed else dropped).append(fr)
        print(f"  {s:<6} {str(magic):>5} {align:>6} {('YES' if passed else 'no'):>5} {fr:>+7.1f}%")
    print("-" * 64)

    def avg(xs):
        return sum(xs) / len(xs) if xs else float("nan")
    print(f"  KEPT (passed trend gate): {len(kept)} names  ->  avg fwd {avg(kept):>+6.1f}%")
    print(f"  DROPPED (failed gate)   : {len(dropped)} names  ->  avg fwd {avg(dropped):>+6.1f}%")
    all_fr = kept + dropped
    print(f"  Unfiltered top {top}       : {len(all_fr)} names  ->  avg fwd {avg(all_fr):>+6.1f}%")
    print("=" * 64)


def asof_now_via_change(get, sym, now_price, field):
    """Fallback when historical EOD is plan-restricted: derive the as-of price from the
    current price and the trailing %-change window. Returns (asof_price, now_price, pct)."""
    d = get(f"stock-price-change?symbol={sym}")
    row = d[0] if isinstance(d, list) and d else (d if isinstance(d, dict) else None)
    if not row or row.get(field) is None or now_price in (None, 0):
        return None
    pct = float(row[field])
    asof = now_price / (1 + pct / 100.0)
    return asof, now_price, pct


def asof_and_now(rows, days):
    """Pick (asof_date, asof_price, now_date, now_price) from an EOD series."""
    if not rows:
        return None
    now_date, now_price = rows[-1]
    target = (dt.date.fromisoformat(now_date) - dt.timedelta(days=days)).isoformat()
    asof = None
    for d, p in rows:
        if d <= target:
            asof = (d, p)
        else:
            break
    if asof is None:                       # series doesn't reach back far enough
        return None
    return asof[0], asof[1], now_date, now_price


def main(argv=None):
    p = argparse.ArgumentParser(description="Walk-forward backtest of the Buffett screen.")
    p.add_argument("--days", type=int, default=60, help="How far back the as-of screen is (default 60).")
    p.add_argument("--top", type=int, default=10)
    p.add_argument("--workers", type=int, default=12)
    p.add_argument("--limit", type=int, default=0, help="Only use the first N universe symbols (smoke test).")
    p.add_argument("--magic-overlay", action="store_true",
                   help="Score each pick's Elite Magic trend with as-of technicals (eod mode only).")
    args = p.parse_args(argv)

    key = os.environ.get("FMP_KEY")
    if not key:
        sys.exit("ERROR: set the FMP_KEY environment variable.")
    get = make_get(key)

    fund_path = os.path.join(DATA, "fundamentals.csv")
    fund = {r["symbol"]: r for r in csv.DictReader(open(fund_path))}
    fields = list(next(iter(fund.values())).keys())
    syms = sorted(fund)
    if args.limit:
        syms = syms[:args.limit]
    lookback = args.days + 30
    print(f"backtest: {len(syms)} symbols, as-of ~{args.days}d back, top {args.top}")

    def cur_price(s):
        try:
            return float(fund[s]["price"])
        except Exception:
            return None

    # Probe: is day-level historical EOD available on this plan? (SPY is a safe probe.)
    eod_mode = bool(series(get, "SPY", lookback))
    field = change_field(args.days)
    prices = {}
    done = 0
    if eod_mode:
        print("price source: historical EOD (exact as-of date)")
        with ThreadPoolExecutor(max_workers=args.workers) as ex:
            futs = {ex.submit(series, get, s, lookback): s for s in syms}
            for fut in as_completed(futs):
                s = futs[fut]
                r = asof_and_now(fut.result(), args.days)
                if r:
                    prices[s] = r
                done += 1
                if done % 200 == 0:
                    print("...", done)
        spy = asof_and_now(series(get, "SPY", lookback), args.days)
    else:
        print(f"price source: trailing %-change window '{field}' (historical EOD not in plan)")
        with ThreadPoolExecutor(max_workers=args.workers) as ex:
            futs = {ex.submit(asof_now_via_change, get, s, cur_price(s), field): s for s in syms}
            for fut in as_completed(futs):
                s = futs[fut]
                r = fut.result()
                if r:
                    prices[s] = (f"~{args.days}d", r[0], "now", r[1])
                done += 1
                if done % 200 == 0:
                    print("...", done)
        sc = asof_now_via_change(get, "SPY", None, field)  # SPY price unknown locally
        if sc is None:
            spc = get("stock-price-change?symbol=SPY")
            row = spc[0] if isinstance(spc, list) and spc else None
            spy = (None, 100.0, None, 100.0 * (1 + float(row[field]) / 100.0)) if row and row.get(field) is not None else None
        else:
            spy = (None, sc[0], None, sc[1])

    print(f"priced {len(prices)} / {len(syms)} symbols")
    if not prices:
        sys.exit("ERROR: no price data returned (check key/plan).")

    # representative dates
    any_r = next(iter(prices.values()))
    asof_date, now_date = any_r[0], any_r[2]

    # write fundamentals_asof.csv (price -> as-of price), run the screener on it
    asof_path = os.path.join(DATA, "fundamentals_asof.csv")
    with open(asof_path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        for s in syms:
            if s not in prices:
                continue
            row = dict(fund[s])
            row["price"] = round(prices[s][1], 4)        # as-of price
            w.writerow(row)

    res_csv = os.path.join(DATA, "results", "backtest_screen.csv")
    res_md = os.path.join(DATA, "results", "backtest_screen.md")
    subprocess.run([sys.executable, os.path.join(HERE, "screener.py"),
                    "--input", asof_path, "--top", str(args.top),
                    "--csv-out", res_csv, "--md-out", res_md,
                    "--source-note", f"BACKTEST as-of {asof_date} (price rewound)"],
                   check=True, stdout=subprocess.DEVNULL)
    picks = list(csv.DictReader(open(res_csv)))[:args.top]

    def fwd(s):
        a = prices.get(s)
        return (a[3] / a[1] - 1) * 100 if a else None

    # universe benchmark = equal-weight forward return of all priced names
    uni = [fwd(s) for s in prices]
    uni_avg = sum(uni) / len(uni)
    spy_ret = (spy[3] / spy[1] - 1) * 100 if spy else None

    picks_rets = [fwd(r["symbol"]) for r in picks if fwd(r["symbol"]) is not None]
    picks_avg = sum(picks_rets) / len(picks_rets) if picks_rets else float("nan")
    win = sum(1 for x in picks_rets if x > 0)

    print("\n" + "=" * 64)
    print(f"  BUFFETT SCREEN BACKTEST   as-of {asof_date}  ->  {now_date}")
    print("=" * 64)
    print(f"  {'#':>2}  {'TICKER':<6} {'asof$':>9} {'now$':>9} {'fwd %':>8}  {'MOS':>6}  verdict")
    for i, r in enumerate(picks, 1):
        s = r["symbol"]; a = prices.get(s)
        if not a:
            print(f"  {i:>2}  {s:<6} {'n/a':>9}")
            continue
        try:
            mos = f"{float(r.get('margin_of_safety', 0)) * 100:.0f}%"
        except Exception:
            mos = r.get("margin_of_safety", "")
        print(f"  {i:>2}  {s:<6} {a[1]:>9.2f} {a[3]:>9.2f} {fwd(s):>+7.1f}%  {mos:>6}  {r.get('verdict','')}")
    print("-" * 64)
    print(f"  Top-{args.top} equal-weight forward return : {picks_avg:>+6.1f}%   (winners {win}/{len(picks_rets)})")
    print(f"  Universe ({len(uni)}) equal-weight return  : {uni_avg:>+6.1f}%")
    if spy_ret is not None:
        print(f"  SPY (S&P 500 ETF) return            : {spy_ret:>+6.1f}%")
    print(f"  Excess vs universe                  : {picks_avg - uni_avg:>+6.1f} pp")
    if spy_ret is not None:
        print(f"  Excess vs SPY                       : {picks_avg - spy_ret:>+6.1f} pp")
    print("=" * 64)
    print("  NOTE: only price was rewound; TTM fundamentals are current values used as a")
    print("  proxy (mild look-ahead). Indicative, educational — not investment advice.")

    if args.magic_overlay:
        if not eod_mode:
            print("\n(magic overlay skipped: needs exact as-of dates, unavailable in change mode)")
        else:
            magic_overlay(get, picks, prices, asof_date, fund, fwd, args.top,
                          os.path.dirname(HERE))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
