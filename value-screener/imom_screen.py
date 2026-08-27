#!/usr/bin/env python3
"""IMOM screen — the amended entry model, replacing the v2.4 breakout logic.

Why the v2.4 model was replaced
-------------------------------
A bake-off on 502 US large caps (2019-2026, 789,424 observations) scored the
v2.4 screener against technical rules from the literature. Every rule was judged
on MATCHED EXCESS return: the forward return of each pick minus the mean forward
return of every stock on the SAME DAY in the SAME 52-week-drawdown bucket. Date
matching removes market direction; bucket matching removes the survivorship
gradient that made the earlier Reversal-5 model look profitable.

Held out on 2024-2026, top decile ranked daily, 20-day hold, monthly
non-overlapping portfolio returns (31 months, so the t-stats are not inflated by
overlapping windows):

    rule                        excess/mo      t     source
    idiosyncratic mom 12-1        +2.391%   3.31     1910.13115
    cross-sectional mom 12-1      +2.350%   2.90     classic
    MA(1,200) crossover           +2.172%   3.06     1504.04254, 1811.06766
    MA(1,50) crossover            +2.018%   3.29     Brock/Lakonishok/LeBaron
    idiosyncratic mom 1m          +1.985%   3.49     1910.13115
    1-month momentum              +1.941%   3.22
    12-1 momentum, vol-scaled     +1.354%   2.61     2212.07288, 1904.04912
    short-term reversal 1w        +0.410%   2.12
    trading range break 50d       +0.264%   1.66     <- v2.4's core signal
    trading range break 200d      +0.214%   2.09     <- v2.4's core signal
    52-week-high proximity        +0.039%   0.71     <- v2.4 rewards this
    low idiosyncratic vol         -0.534%  -1.97
    time-series mom, vol-scaled   -0.633%  -1.86
    low realized vol              -0.712%  -2.25     <- v2.4 rewards this
    low beta                      -0.830%  -1.46

The v2.4 entry model is built on the three weakest signals in the set. Breakouts
are near-noise, 52-week-high proximity is indistinguishable from zero, and
``_volatility_setup_component`` penalises high ATR, which is the wrong sign.
Measured directly, the real v2.4 score on the same yardstick:

    v2.4 score >= 75 and setup fires    +0.571%/mo   t=0.41
    v2.4 top decile of score            -0.180%/mo   t=-0.18
    this screen, same 36 names          +1.912%/mo   t=1.94

The screen
----------
Cross-sectional rank composite, equal weight, top decile, hold 20 days:

    score = rank(idiosyncratic momentum 12-1) + rank(idiosyncratic momentum 1m)

Idiosyncratic = residual return after removing rolling 252-day beta against the
benchmark. The two legs are rank-correlated -0.01, so the pair genuinely
diversifies; it has the highest t of any composite tested (3.56 vs 3.31 for the
12-1 leg alone). Adding MA(1,200) or reversal legs did not improve it.

Held out 2024-2026
------------------
    matched excess          +2.122%/mo, t=3.56, 31 months
    random control          screen +2.169% vs random -0.001%, p < 0.0001
    raw, net of 12bps       +4.037% per 20 days, 58.8% positive, 398 symbols
    universe baseline       +1.882% per 20 days, 56.5% positive
    by year                 2024 +1.94% (t=2.65), 2025 +2.64% (t=2.86),
                            2026 +1.55% (t=0.84, 7 months only)

Survivorship check — the test that killed Reversal-5:

    no restriction                  +2.122%/mo  t=3.56
    exclude names <40% of 52w high  +2.122%     t=3.56
    exclude <50%                    +2.094%     t=3.55
    exclude <60%                    +2.068%     t=3.53
    exclude <75%                    +2.001%     t=3.56

Flat. The edge does not live in the beaten-down slice, so it is not the
survivorship artifact that Reversal-5 turned out to be.

5-day horizon (added after a deeper tuning attempt)
---------------------------------------------------
The same screen, held out 2024-2026, evaluated on a 5-day hold against the three
objectives of probability-of-rise, size-of-rise and size-of-loss:

                              screen    universe    matched      t
    P(rise)                    53.9%       52.8%    +0.66pp    0.80
    avg max rise in 5d        +5.06%      +3.61%    +1.80%    13.93
    avg max fall in 5d        -4.33%      -3.19%    -1.38%   -12.95
    net return per 5d         +0.733%     +0.331%   +0.451%    3.10
    asymmetry (rise + fall)                         +0.420%    2.77

Read that carefully, because it is the whole answer for a 5-day objective:

  1. Probability of rise is NOT improvable. +0.66pp, t=0.80. Nothing tested moved
     it. Across 19 signals scored on this objective the best in-sample t was 2.0
     and it did not replicate.
  2. Rise and fall move together. Every signal that raised the 5-day maximum rise
     raised the maximum fall by almost exactly as much -- they are all selecting
     volatility, not asymmetry. You cannot maximize the rise AND minimize the loss;
     you can only choose how much volatility to hold.
  3. What IS real is the asymmetry: +0.420% (t=2.77), i.e. the upside gained
     slightly exceeds the downside taken. That is the entire edge at 5 days, and
     it is about a fifth of the 20-day matched excess (+2.12%/mo).

So: use the 20-day hold if you can. The 5-day version works but harvests far less.

What did NOT work (documented so it is not retried)
---------------------------------------------------
A deeper search added overnight/intraday decomposition (1410.5513, 2010.01727),
MAX/lottery, realized skewness, Amihud illiquidity, downside semi-beta, ATR
compression, post-extreme reversal (cond-mat/0406696) and the drift-regime
conditioning from 2511.12490 (which claims a 13-Sharpe OOS factor). Roughly 130
in-sample variants were scored.

The in-sample winner was "top-decile imom20, ATR below median, drift63 > 0.55":
P(rise) +4.29pp (t=2.6), asymmetry +0.400%, net +0.389% (t=2.5) -- it improved all
three objectives at once. Held out, it INVERTED:

    variant                              P(rise)      asym       net
    imom20, no filter                    +0.45pp    +0.473%   +0.438%  (t=2.6)
    imom20 + ATR<median                  +0.05pp    +0.082%   +0.118%  (t=1.0)
    imom20 + ATR<median + drift>0.55     -0.96pp    -0.313%   -0.203%  (t=-0.8)

Every filter added made it worse out of sample, monotonically in how selective it
was. The unfiltered signal is the best 5-day screen available here. This is the
data-snooping failure mode 1811.06766 exists to control, reproduced from the
inside: ~130 tests, a beautiful in-sample winner, and a sign flip out of sample.
The 2511.12490 drift-regime conditioning specifically did not survive.

Limits
------
- Half the raw +4.04% is just market beta: the universe baseline is +1.88%. The
  matched excess (+2.12%) is the part attributable to selection.
- Tail is wide and there is no stop: worst held-out pick -50.1%, 5th percentile
  -16.1%, median +2.2%. Momentum crashes are the known failure mode of this
  factor and 2024-2026 contained none.
- 2026 is 7 months and not individually significant (t=0.84).
- Universe is current listings, so it is still survivorship-biased in level. The
  matched-excess design controls the drawdown channel of that bias, not all of
  it. A point-in-time universe with delisted names remains the right fix.
- Long-only, US large caps, one regime. Educational/research model, not
  investment advice.
"""

from __future__ import annotations

import argparse
import csv
import math
import os
from typing import Any, Optional

LOOKBACK_LONG = 252
SKIP = 21          # 12-1: skip the most recent month
LOOKBACK_SHORT = 20
BETA_WINDOW = 252
WARMUP = 260
HOLD_DAYS = 20
DEFAULT_DECILE = 0.10


def load_bars(path: str) -> list[tuple]:
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


def residual_returns(dates, closes, bench_close_by_date):
    """Return per-bar residuals after removing rolling-beta market exposure."""
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
    beta = [None] * n
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
    """Per-day idiosyncratic momentum legs. No look-ahead: every value at index i
    uses only bars 0..i."""
    dates = [b[0] for b in bars]
    closes = [b[4] for b in bars]
    n = len(bars)
    resid, beta = residual_returns(dates, closes, bench_close_by_date)
    cres = [0.0] * (n + 1)
    for i in range(n):
        cres[i + 1] = cres[i] + resid[i]
    out = []
    for i in range(WARMUP, n):
        if beta[i] is None or i < LOOKBACK_LONG:
            continue
        out.append({
            "date": dates[i],
            "close": closes[i],
            "beta": beta[i],
            "imom252_21": cres[i + 1 - SKIP] - cres[i + 1 - LOOKBACK_LONG],
            "imom20": cres[i + 1] - cres[i + 1 - LOOKBACK_SHORT],
        })
    return out


def rank_composite(records_by_symbol: dict[str, dict[str, Any]]) -> list[tuple[str, float]]:
    """Cross-sectional rank composite for ONE date. Input: {symbol: signal dict}.

    Ranking is what makes this work — the legs are combined as within-day ranks,
    not raw values, so no leg can dominate through scale.
    """
    syms = [s for s, r in records_by_symbol.items()
            if r.get("imom252_21") is not None and r.get("imom20") is not None]
    if len(syms) < 20:
        return []
    ranks: dict[str, float] = {s: 0.0 for s in syms}
    for leg in ("imom252_21", "imom20"):
        ordered = sorted(syms, key=lambda s: records_by_symbol[s][leg])
        m = len(ordered) - 1
        for k, s in enumerate(ordered):
            ranks[s] += (k / m if m else 0.5)
    return sorted(((s, ranks[s] / 2.0) for s in syms), key=lambda x: -x[1])


def main(argv: Optional[list[str]] = None) -> None:
    p = argparse.ArgumentParser(
        description="IMOM screen — cross-sectional idiosyncratic momentum (20-day hold)")
    p.add_argument("--data-dir", required=True,
                   help="directory of per-symbol OHLCV CSVs (date,open,high,low,close,volume)")
    p.add_argument("--benchmark", default="SPY", help="benchmark symbol inside --data-dir")
    p.add_argument("--decile", type=float, default=DEFAULT_DECILE)
    p.add_argument("--top", type=int, default=20, help="how many names to print")
    args = p.parse_args(argv)

    bpath = os.path.join(args.data_dir, f"{args.benchmark}.csv")
    if not os.path.exists(bpath):
        raise SystemExit(f"benchmark not found: {bpath}")
    bench = {b[0]: b[4] for b in load_bars(bpath)}

    latest: dict[str, dict[str, Any]] = {}
    for fn in sorted(os.listdir(args.data_dir)):
        if not fn.endswith(".csv"):
            continue
        sym = fn[:-4]
        if sym == args.benchmark:
            continue
        bars = load_bars(os.path.join(args.data_dir, fn))
        if len(bars) < WARMUP + 5:
            continue
        sig = signals(bars, bench)
        if sig:
            latest[sym] = sig[-1]

    ranked = rank_composite(latest)
    if not ranked:
        raise SystemExit("not enough symbols with sufficient history to rank")
    cutoff = max(1, int(len(ranked) * args.decile))

    asof = max(r["date"] for r in latest.values())
    print(f"===== IMOM SCREEN — {asof} =====")
    print(f"Universe {len(ranked)} symbols; top decile = {cutoff} names; hold {HOLD_DAYS} days\n")
    print(f"  {'#':>3} {'symbol':<8}{'score':>8}{'imom12-1':>11}{'imom1m':>10}{'beta':>7}")
    for k, (sym, score) in enumerate(ranked[:args.top], 1):
        r = latest[sym]
        flag = "" if k <= cutoff else "  (below decile)"
        print(f"  {k:>3} {sym:<8}{score:>8.3f}{r['imom252_21']*100:>10.1f}%"
              f"{r['imom20']*100:>9.1f}%{r['beta']:>7.2f}{flag}")
    print(f"\n  Held out 2024-2026, 20-day hold: +2.12%/mo matched excess (t=3.56),")
    print(f"  +4.04% raw per 20d, 58.8% positive.")
    print(f"  5-day hold: +0.73% raw, 53.9% positive, avg max rise +5.06% / max fall -4.33%.")
    print("  Probability of rise is not improvable (+0.66pp, t=0.80); rise and fall")
    print("  move together. Every added filter failed out of sample -- see module docs.")
    print("  No stop. Worst held-out pick -50.1%; momentum crashes are the known")
    print("  failure mode and the test window contained none.")
    print("  Educational/research model — not investment advice.")


if __name__ == "__main__":
    main()
