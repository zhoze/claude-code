#!/usr/bin/env python3
"""IMOM Screen — standalone, single-file cross-sectional momentum stock screener.

Ranks a universe of large-cap US stocks on idiosyncratic (beta-adjusted) momentum
and buys the top decile for a 30-60 day hold.

    score = rank(idiosyncratic momentum 12-1) + rank(price vs 200-day MA)

"Idiosyncratic" means the residual return after removing each stock's rolling
252-day beta against SPY, so the signal measures stock-specific strength rather
than market exposure.

QUICK START — no API key, no third-party packages, Python 3.9+:

    python3 imom_screen_standalone.py                 # top 25, 60-day hold
    python3 imom_screen_standalone.py --top 50
    python3 imom_screen_standalone.py --hold 30
    python3 imom_screen_standalone.py --refresh       # re-download prices
    python3 imom_screen_standalone.py --csv picks.csv # export the ranking

The first run downloads ~500 symbols of daily history (a few minutes) into
./imom_data and caches it. Later runs top up only what is missing.

HOW IT WAS VALIDATED
--------------------
Panel test on 502 US large caps, 2019-2026, 789,424 observations. Every rule was
scored on MATCHED EXCESS return: a pick's forward return minus the mean forward
return of every stock on the SAME DAY in the SAME 52-week-drawdown bucket. Date
matching removes market direction; bucket matching removes survivorship bias
through the drawdown channel. Significance uses non-overlapping monthly portfolio
returns with Newey-West standard errors, because overlapping forward windows
otherwise inflate t-stats by roughly 8x.

Held out on 2024-2026 (never used for selection or tuning), 60-day hold:

    matched excess       +2.630% per 20 days   t = 4.22
    raw, net of 12bps   +12.75% per 60-day trade, 63.8% positive
    universe baseline    +5.59% per 60 days,    60% positive
    random control       p < 0.0001
    by year              2024 +1.96%   2025 +2.78%   2026 +4.44%

Survivorship check (the test that killed an earlier mean-reversion variant):

    no restriction                  +2.630% per 20d
    exclude names <50% of 52w high  +2.628%
    exclude names <75% of 52w high  +2.333%

Flat, so the edge does not live in the beaten-down slice.

Holding period matters less than you would think. Matched excess per unit of time
is roughly constant from 5 to 120 days (~2.0-2.3% per 20 days); what changes is
turnover cost (6.05%/yr at a 5-day hold vs 0.50%/yr at 60 days) and hit rate
(54.7% -> 64.2%). 30-60 days is the supported range. 90 and 120 day holds looked
better still but rest on only 7 and 5 non-overlapping periods, and a long hold
cannot exit a momentum crash.

WHAT THIS IS NOT
----------------
- Roughly half the raw return is market beta, not stock selection. The picks run a
  median beta near 1.6-2.0 against a universe median near 0.7. In a drawdown this
  falls harder than the index.
- There is no stop loss. Stops were tested and made results worse. The worst
  held-out pick was -50.1%.
- Momentum crashes are this factor's known failure mode and the 2024-2026 test
  window contained none. Expect one eventually.
- The universe is current listings, so it remains survivorship-biased in level.
  The matched-excess design controls the drawdown channel of that bias, not every
  channel. A point-in-time universe with delisted names is the proper fix.
- No sector constraint. Momentum concentrates; expect a dominant theme.
- Validated long-only, US large caps, one macro regime.

Educational and research use. This is not investment advice.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Optional

# --- signal parameters (validated; changing these invalidates the numbers above)
LOOKBACK_LONG = 252     # 12 months
SKIP = 21               # 12-1: skip the most recent month
LOOKBACK_SHORT = 20     # 1 month
BETA_WINDOW = 252
TREND_MA = 200
WARMUP = 260
DEFAULT_HOLD = 60
DEFAULT_DECILE = 0.10
MIN_DOLLAR_VOLUME = 5_000_000.0

# The best leg pair depends on the holding period. imom20 is a one-month signal
# and decays over a 60-day hold (in-sample t falls to 1.28); the 200-day trend leg
# takes over. Both sets were selected in-sample and validated once out-of-sample.
LEGS_BY_HOLD = {
    20: ("imom252_21", "imom20"),      # +2.12% per 20d matched excess, t=3.56
    30: ("imom252_21", "ma_1_200"),    # +2.648% per 20d, t=3.67
    60: ("imom252_21", "ma_1_200"),    # +2.630% per 20d, t=4.22
}
LEG_LABEL = {"imom20": "idio 1m", "ma_1_200": "vs 200dMA"}

BENCHMARK = "SPY"
UA = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
      "Chrome/120 Safari/537.36")
NASDAQ_SCREENER = "https://api.nasdaq.com/api/screener/stocks?tableonly=true&limit=6000&exchange={ex}"
NASDAQ_HISTORY = ("https://api.nasdaq.com/api/quote/{sym}/historical"
                  "?assetclass={cls}&fromdate={frm}&todate={to}&limit=99999")
HISTORY_START = "2019-01-01"


# ---------------------------------------------------------------------------
# Networking
# ---------------------------------------------------------------------------


def http_json(url: str, timeout: int = 60, attempts: int = 3) -> Optional[Any]:
    for attempt in range(1, attempts + 1):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA,
                                                       "Accept": "application/json"})
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return json.loads(resp.read().decode("utf-8", "replace"))
        except Exception:
            if attempt == attempts:
                return None
            time.sleep(2 * attempt)
    return None


def _money(x: Any) -> float:
    return float(str(x).replace("$", "").replace(",", "").strip())


def fetch_universe(size: int) -> list[str]:
    """Largest `size` common stocks by market cap across NASDAQ/NYSE/AMEX.

    A published, mechanical rule rather than a hand-picked list — but note it is
    TODAY's listing set, which is the survivorship caveat in the header.
    """
    rows: list[dict] = []
    for ex in ("nasdaq", "nyse", "amex"):
        payload = http_json(NASDAQ_SCREENER.format(ex=ex), timeout=90)
        if not payload:
            continue
        rows.extend((payload.get("data") or {}).get("table", {}).get("rows", []) or [])
    if not rows:
        raise SystemExit("could not download the universe list (network blocked?)")
    bad = re.compile(r"(warrant|unit|right|preferred|depositary|%|notes|debenture)", re.I)

    def cap(r: dict) -> float:
        try:
            return float(str(r.get("marketCap", "")).replace(",", "") or 0)
        except ValueError:
            return 0.0

    clean = [r for r in rows
             if cap(r) > 0
             and not bad.search(r.get("name", ""))
             and re.fullmatch(r"[A-Z]{1,5}", r.get("symbol", ""))]
    clean.sort(key=cap, reverse=True)
    return [r["symbol"] for r in clean[:size]]


def fetch_history(sym: str, cache_dir: str, refresh: bool, today: str) -> Optional[str]:
    path = os.path.join(cache_dir, f"{sym}.csv")
    if os.path.exists(path) and not refresh:
        try:
            with open(path) as fh:
                last = fh.readlines()[-1].split(",")[0]
            if last >= today:
                return path
        except (IndexError, OSError):
            pass
    cls = "etf" if sym == BENCHMARK else "stocks"
    payload = http_json(NASDAQ_HISTORY.format(sym=sym, cls=cls, frm=HISTORY_START, to=today))
    table = ((payload or {}).get("data") or {}).get("tradesTable") or {}
    raw = table.get("rows") or []
    rows = []
    for r in raw:
        try:
            m, d, y = r["date"].split("/")
            rows.append({"date": f"{y}-{m}-{d}",
                         "open": _money(r["open"]), "high": _money(r["high"]),
                         "low": _money(r["low"]), "close": _money(r["close"]),
                         "volume": float(str(r["volume"]).replace(",", ""))})
        except (KeyError, ValueError, AttributeError):
            continue          # vendor rows occasionally carry "N/A"
    if len(rows) < WARMUP + 40:
        return None
    rows.sort(key=lambda x: x["date"])
    with open(path, "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=["date", "open", "high", "low", "close", "volume"])
        w.writeheader()
        w.writerows(rows)
    return path


# ---------------------------------------------------------------------------
# Data loading
# ---------------------------------------------------------------------------


def load_bars(path: str) -> list[tuple]:
    """Deduplicated, date-sorted OHLCV with impossible candles dropped."""
    seen: dict[str, tuple] = {}
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
            tol = max(1e-10, abs(c) * 1e-10)
            if h + tol < max(o, c, l) or l - tol > min(o, c, h):
                continue
            seen[r["date"]] = (r["date"], o, h, l, c, v)
    return [seen[d] for d in sorted(seen)]


# ---------------------------------------------------------------------------
# Signal
# ---------------------------------------------------------------------------


def _sma(vals: list[float], period: int) -> list[Optional[float]]:
    n = len(vals)
    out: list[Optional[float]] = [None] * n
    run = 0.0
    for i, x in enumerate(vals):
        run += x
        if i >= period:
            run -= vals[i - period]
        if i >= period - 1:
            out[i] = run / period
    return out


def residual_returns(dates, closes, bench_close_by_date):
    """Per-bar residuals after removing rolling-beta market exposure, O(n)."""
    n = len(closes)
    ret = [0.0] + [closes[i] / closes[i - 1] - 1.0 for i in range(1, n)]
    mret = [0.0] * n
    prev = None
    for i, d in enumerate(dates):
        cur = bench_close_by_date.get(d)
        if cur is not None and prev is not None and prev > 0:
            mret[i] = cur / prev - 1.0
        if cur is not None:
            prev = cur
    sx = sy = sxx = sxy = 0.0
    resid = [0.0] * n
    beta: list[Optional[float]] = [None] * n
    for i in range(n):
        x, y = mret[i], ret[i]
        sx += x; sy += y; sxx += x * x; sxy += x * y
        if i >= BETA_WINDOW:
            px, py = mret[i - BETA_WINDOW], ret[i - BETA_WINDOW]
            sx -= px; sy -= py; sxx -= px * px; sxy -= py * px
        if i >= BETA_WINDOW - 1:
            den = sxx - sx * sx / BETA_WINDOW
            b = (sxy - sx * sy / BETA_WINDOW) / den if den > 1e-18 else 0.0
            beta[i] = b
            resid[i] = ret[i] - b * mret[i]
    return resid, beta


def signals(bars: list[tuple], bench_close_by_date: dict[str, float]) -> list[dict[str, Any]]:
    """Per-day signal legs. No look-ahead: every value at index i uses only bars 0..i."""
    dates = [b[0] for b in bars]
    closes = [b[4] for b in bars]
    vols = [b[5] for b in bars]
    n = len(bars)
    resid, beta = residual_returns(dates, closes, bench_close_by_date)
    cres = [0.0] * (n + 1)
    for i in range(n):
        cres[i + 1] = cres[i] + resid[i]
    ma200 = _sma(closes, TREND_MA)
    adv = _sma([closes[i] * vols[i] for i in range(n)], 20)
    out = []
    for i in range(WARMUP, n):
        if beta[i] is None or ma200[i] is None or adv[i] is None or i < LOOKBACK_LONG:
            continue
        out.append({
            "date": dates[i],
            "close": closes[i],
            "beta": beta[i],
            "dollarVolume": adv[i],
            "imom252_21": cres[i + 1 - SKIP] - cres[i + 1 - LOOKBACK_LONG],
            "imom20": cres[i + 1] - cres[i + 1 - LOOKBACK_SHORT],
            "ma_1_200": closes[i] / ma200[i] - 1.0,
        })
    return out


def rank_composite(records_by_symbol: dict[str, dict[str, Any]],
                   legs: tuple[str, ...]) -> list[tuple[str, float]]:
    """Cross-sectional rank composite for ONE date.

    Legs are combined as within-day ranks rather than raw values, so no leg can
    dominate through scale.
    """
    syms = [s for s, r in records_by_symbol.items()
            if all(r.get(leg) is not None for leg in legs)]
    if len(syms) < 20:
        return []
    ranks = {s: 0.0 for s in syms}
    for leg in legs:
        ordered = sorted(syms, key=lambda s: records_by_symbol[s][leg])
        m = len(ordered) - 1
        for k, s in enumerate(ordered):
            ranks[s] += (k / m if m else 0.5)
    return sorted(((s, ranks[s] / len(legs)) for s in syms), key=lambda x: -x[1])


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


HELD_OUT = {
    20: "+2.12% per 20d matched excess (t=3.56), +4.04% raw per 20d, 58.8% positive",
    30: "+2.65% per 20d matched excess (t=3.67), +6.35% raw per 30d, 60.7% positive",
    60: "+2.63% per 20d matched excess (t=4.22), +12.75% raw per 60d, 63.8% positive",
}


def main(argv: Optional[list[str]] = None) -> None:
    p = argparse.ArgumentParser(
        description="IMOM screen — cross-sectional idiosyncratic momentum",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter)
    p.add_argument("--hold", type=int, choices=sorted(LEGS_BY_HOLD), default=DEFAULT_HOLD,
                   help="holding period in trading days; selects the validated leg pair")
    p.add_argument("--top", type=int, default=25, help="how many names to print")
    p.add_argument("--universe-size", type=int, default=500,
                   help="largest N US common stocks by market cap")
    p.add_argument("--cache-dir", default="imom_data", help="where price history is cached")
    p.add_argument("--refresh", action="store_true", help="re-download all price history")
    p.add_argument("--min-dollar-volume", type=float, default=MIN_DOLLAR_VOLUME,
                   help="liquidity floor on 20-day average dollar volume")
    p.add_argument("--decile", type=float, default=DEFAULT_DECILE,
                   help="fraction of the universe treated as a buy list")
    p.add_argument("--csv", help="write the full ranking to this CSV")
    p.add_argument("--workers", type=int, default=6, help="parallel download threads")
    args = p.parse_args(argv)

    os.makedirs(args.cache_dir, exist_ok=True)
    today = time.strftime("%Y-%m-%d")

    print(f"Building universe (largest {args.universe_size} by market cap)...", flush=True)
    universe = fetch_universe(args.universe_size)
    symbols = [BENCHMARK] + [s for s in universe if s != BENCHMARK]
    print(f"  {len(symbols)} symbols including the {BENCHMARK} benchmark", flush=True)

    print(f"Downloading daily history into ./{args.cache_dir} "
          f"(first run takes a few minutes)...", flush=True)
    done = {"n": 0}

    def grab(sym: str):
        path = fetch_history(sym, args.cache_dir, args.refresh, today)
        done["n"] += 1
        if done["n"] % 100 == 0:
            print(f"  {done['n']}/{len(symbols)}", flush=True)
        return sym, path

    with ThreadPoolExecutor(max_workers=max(1, args.workers)) as ex:
        fetched = dict(ex.map(grab, symbols))

    bench_path = fetched.get(BENCHMARK)
    if not bench_path:
        raise SystemExit(f"could not download the {BENCHMARK} benchmark; cannot compute beta")
    bench = {b[0]: b[4] for b in load_bars(bench_path)}

    latest: dict[str, dict[str, Any]] = {}
    skipped_illiquid = 0
    for sym, path in fetched.items():
        if sym == BENCHMARK or not path:
            continue
        try:
            bars = load_bars(path)
        except OSError:
            continue
        if len(bars) < WARMUP + 5:
            continue
        sig = signals(bars, bench)
        if not sig:
            continue
        if sig[-1]["dollarVolume"] < args.min_dollar_volume:
            skipped_illiquid += 1
            continue
        latest[sym] = sig[-1]

    legs = LEGS_BY_HOLD[args.hold]
    ranked = rank_composite(latest, legs)
    if not ranked:
        raise SystemExit("not enough symbols with sufficient history to rank")

    asof = max(r["date"] for r in latest.values())
    stale = sum(1 for r in latest.values() if r["date"] != asof)
    cutoff = max(1, int(len(ranked) * args.decile))
    second = legs[1]

    print(f"\n===== IMOM SCREEN — {asof} =====")
    print(f"Universe {len(ranked)} ranked ({skipped_illiquid} dropped on liquidity"
          + (f", {stale} without a bar on {asof}" if stale else "") + ")")
    print(f"Top decile = {cutoff} names; hold {args.hold} days; legs {' + '.join(legs)}\n")
    print(f"  {'#':>3} {'symbol':<8}{'score':>8}{'idio 12-1':>11}"
          f"{LEG_LABEL[second]:>11}{'beta':>7}{'price':>10}")
    for k, (sym, score) in enumerate(ranked[:args.top], 1):
        r = latest[sym]
        flag = "" if k <= cutoff else "  (below decile)"
        print(f"  {k:>3} {sym:<8}{score:>8.3f}{r['imom252_21'] * 100:>10.1f}%"
              f"{r[second] * 100:>10.1f}%{r['beta']:>7.2f}{r['close']:>10.2f}{flag}")

    if args.csv:
        with open(args.csv, "w", newline="") as fh:
            w = csv.writer(fh)
            w.writerow(["rank", "symbol", "score", "asOf", "imom252_21",
                        second, "beta", "close", "dollarVolume20d", "inTopDecile"])
            for k, (sym, score) in enumerate(ranked, 1):
                r = latest[sym]
                w.writerow([k, sym, f"{score:.6f}", r["date"], f"{r['imom252_21']:.6f}",
                            f"{r[second]:.6f}", f"{r['beta']:.4f}", f"{r['close']:.4f}",
                            f"{r['dollarVolume']:.0f}", int(k <= cutoff)])
        print(f"\n  Full ranking -> {args.csv}")

    print(f"\n  Held out 2024-2026 at a {args.hold}-day hold: {HELD_OUT[args.hold]}.")
    print("  Roughly half the raw return is market beta, not selection. No stop loss;")
    print("  worst held-out pick -50.1%. Momentum crashes are the known failure mode and")
    print("  the test window contained none. See the module docstring for full caveats.")
    print("  Educational/research model — not investment advice.")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        sys.exit(130)
