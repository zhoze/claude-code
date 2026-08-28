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

Holding period: 5 to 120 days, and the re-tuning for 30-60d
-----------------------------------------------------------
Held out 2024-2026, same 26,727 observations at every horizon, Newey-West t
(lag = horizon in months) to correct for overlapping windows:

    hold    excess   per 20d   NW t   raw net   win%    cost drag/yr
      5d   +0.508%   +2.032%   3.28   +0.773%  54.7%        6.05%
     20d   +2.070%   +2.070%   3.47   +3.907%  59.1%        1.51%
     30d   +3.427%   +2.284%   3.98   +6.041%  61.3%        1.01%
     60d   +6.652%   +2.217%   3.82  +11.929%  64.2%        0.50%
     90d   +9.691%   +2.154%   4.24  +18.164%  66.8%        0.34%
    120d  +12.855%   +2.142%   4.50  +24.693%  70.2%        0.25%

Excess per unit of time is flat (~2.0-2.3%); the win rate and t-stat rise with
the horizon and the cost drag collapses. 90d and 120d rest on only 7 and 5
non-overlapping periods, so their apparent superiority is not established -- and
a long hold cannot exit a momentum crash, which is this factor's failure mode and
did not occur in the test window. 30-60 days is the supported range.

Re-tuning the legs for 30-60d. ``imom20`` is a one-month signal and decays over a
longer hold (in-sample t drops to 1.28 at 60d); the 200-day trend leg takes over.
Legs re-ranked in-sample, then validated once out of sample:

    composite                        30d/20d     t   60d/20d     t
    imom252_21 + imom20 (the 20d)    +2.284%  3.90   +2.217%  3.82
    imom252_21 + ma_1_200            +2.648%  3.67   +2.630%  4.22   <- adopted
    imom252_21 + ma_1_200 + irev5    +1.427%  2.98   +1.483%  3.52

At 60 days the trend pair is 19% better on excess and clearly better on t. The
three-leg variant looked best in-sample on t and failed out of sample, so it was
dropped. ``LEGS_BY_HOLD`` keeps the 20-day pair for a 20-day hold.

Adopted configuration, held out at 60d: +2.630% per 20 days (t=4.22), +12.75%
raw per 60-day trade, 63.8% positive. Survivorship-flat (+2.630% -> +2.333%
excluding names below 75% of their 52-week high). By year +1.96% / +2.78% /
+4.44%. Random control p < 0.0001 at both 30d and 60d.

Caveat on horizon selection: 30-60d was chosen after seeing out-of-sample results
across horizons, so that choice itself carries selection risk. The leg re-ranking
within it was done in-sample and validated once.

Blending in ta-screener's golden_cross (--blend)
------------------------------------------------
A head-to-head against the 150-screen ta-screener package (see
``panel_backtest/README.md``) put IMOM 4th of 138 on 60-day matched excess, with
the highest t-stat of the leading group. No single screen beat it — every paired
monthly difference was insignificant — because the top four overlap IMOM's decile
by 73-79% and are effectively the same trade.

``golden_cross`` is the exception: it overlaps only ~50%, so it adds information.
It is ``SMA50 > SMA200`` scored as the spread plus a freshness bonus
``1/(1 + days since the cross up)`` — it rewards a *young* cross, which 12-month
momentum and distance-above-200dMA do not encode. Because it is undefined outside
a golden cross, adding it also filters the universe (507 -> ~357 eligible).

Measured on THIS implementation (not ta-screener's), held out 2024-2026:

                         30d/20d     t    raw30   win%   60d/20d     t    raw60   win%
    IMOM (2 legs)        +2.650%  3.73   +6.33%  60.8%   +2.635%  4.20  +12.72%  63.9%
    BLEND (3 legs)       +3.392%  3.72   +7.39%  60.4%   +3.476%  4.21  +14.82%  64.7%

Paired monthly difference, blend minus IMOM:

    30d  +0.742%  t=3.08     60d  +0.841%  t=3.55

and it replicates in the period the blend was NOT chosen on:

    2020-2023   30d  +1.064%  t=1.96      60d  +0.946%  t=2.57

Survivorship-flat: 60d +3.476% -> +3.476% (>=50% of 52w high) -> +3.135% (>=75%).
By year, 60d: 2024 +2.57%, 2025 +3.45%, 2026 +7.20%. Random control p < 0.0001 at
both horizons.

Note the 60-day case is the better supported one: its 2020-2023 replication is
significant (t=2.57) where the 30-day replication is marginal (t=1.96).

Implementation note: this ``_golden_cross`` computes its moving averages over each
ticker's OWN bars, whereas ta-screener computes them over shared panel rows that
contain NaN gaps (dropped bad bars, pre-IPO history). Ours is the right definition
for a screen and scores names theirs cannot (e.g. recent listings), which is why
the 60-day blend measures +3.476% here against +3.565% there. The number above is
the one this code produces.

Caveat: golden_cross was picked after seeing the 138-screen leaderboard, so the
choice carries selection risk. The 2020-2023 replication is what makes it credible.

Amendment: rank-weighted decile (adopted)
-----------------------------------------
Six pre-registered candidates to raise both profit and t (obv_slope leg
[2310.09903], sector cap, inverse-vol weights [2212.07288/1904.04912], combos,
rank weights [signal-weighted construction, Kakushadze 1601.00991]) were ranked
in-sample 2020-2023 on the paired difference vs the equal-weighted blend. Only
RANK WEIGHTS passed (the diversification/vol-reduction candidates all HURT --
in this panel the return lives in the strongest-signal names, matching the
measured monotonic rank-return relation). One out-of-sample shot, 2024-2026:

                        30d/20d     t   Sharpe | 60d/20d     t   Sharpe
    equal-weight blend  +3.392%  3.68    2.15  | +3.476%  4.21    2.45
    RANK-WEIGHTED       +4.386%  3.99    2.28  | +4.390%  4.12    2.53

    paired diff +0.995% (t=4.14) at 30d, +0.914% (t=3.16) at 60d;
    replicates 2020-2023 (+0.569% t=1.76, +0.486% t=1.85); survivorship-flat;
    every year positive; random control p < 0.0001.

Stated plainly: at 60d the own-series t is a statistical tie (4.12 vs 4.21);
at 30d it is clearly higher. The paired test is the correct instrument and is
decisive at both horizons.

Fine-tuning (adopted): a second pre-registered grid over decile fraction,
rank-weight power and leg weights (15 variants, in-sample selection with a +10%
gate, one OOS shot) settled on cutting the selection from the top 10% to the TOP
5% (~18 names, linear rank weights, top position ~11%). Held out 2024-2026:
30d excess +5.604% (t=4.17), 60d +5.624% (t=3.83) -- +28% over the decile-10%
version (paired t=3.34/2.56), raw +10.71%/30d and +21.61%/60d, survivorship-flat,
every year positive. Costs stated plainly: 60d own-t 4.12 -> 3.83 and 60d Sharpe
2.53 -> 2.41; breadth halves. Concentration levers do not stack (all combos
failed the in-sample paired-t gate) and leg weights are flat (all six
alternatives within 0.05% of equal thirds). --blend now defaults to 5%;
--decile overrides. This was the seventh analytical pass over the 2024-2026
window -- the pre-registration, gates and in-sample selection are what keep it
honest, not the holdout label.

5-day horizon (from an earlier tuning attempt)
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
TREND_MA = 200
SMA_FAST = 50      # golden-cross fast leg
SMA_SLOW = 200     # golden-cross slow leg
WARMUP = 260
DEFAULT_HOLD = 60
DEFAULT_DECILE = 0.10

# The best leg pair depends on how long you hold. imom20 is a 1-month signal and
# decays over a 60-day hold (in-sample t falls to 1.28); the 200-day trend leg
# takes over. Both sets were selected in-sample and validated once out-of-sample.
LEGS_BY_HOLD = {
    20: ("imom252_21", "imom20"),      # +2.12%/mo matched excess, t=3.56
    30: ("imom252_21", "ma_1_200"),    # +2.648% per 20d, t=3.67
    60: ("imom252_21", "ma_1_200"),    # +2.630% per 20d, t=4.22  <- best evidence
}
LEG_LABEL = {"imom20": "idio 1m", "ma_1_200": "vs 200dMA", "golden_cross": "golden X"}

# The blended screen adds ta-screener's golden_cross leg. It overlaps IMOM's decile
# only ~50%, so it carries information the momentum legs do not: the freshness of
# the SMA50/SMA200 cross. Because golden_cross is undefined outside a golden cross,
# adding it also acts as a FILTER -- only names with SMA50 > SMA200 are eligible.
BLEND_LEGS_BY_HOLD = {
    30: ("imom252_21", "ma_1_200", "golden_cross"),   # rank-weighted: +4.386%/20d, t=3.99
    60: ("imom252_21", "ma_1_200", "golden_cross"),   # rank-weighted: +4.390%/20d, t=4.12
}


def rank_weights(n: int) -> list[float]:
    """Linear rank weights over an n-name decile: k, k-1, ..., 1, normalized.

    Validated amendment to the blend (see the docstring): vs equal weight the
    paired diff is +0.995%/20d (t=4.14) at 30d and +0.914% (t=3.16) at 60d, with
    Sharpe up at both horizons; it replicated in 2020-2023 (t=1.76/1.85).
    Construction follows signal-weighted portfolios (Kakushadze arXiv:1601.00991).
    """
    total = n * (n + 1) / 2.0
    return [(n - j) / total for j in range(n)]


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


def _sma(vals: list[float], period: int) -> list[Optional[float]]:
    n = len(vals); out: list[Optional[float]] = [None] * n; run = 0.0
    for i, x in enumerate(vals):
        run += x
        if i >= period:
            run -= vals[i - period]
        if i >= period - 1:
            out[i] = run / period
    return out


def _golden_cross(closes: list[float]) -> list[Optional[float]]:
    """ta-screener's golden_cross leg, reimplemented without pandas.

        bull    = SMA50 > SMA200
        score   = (SMA50/SMA200 - 1) + 1/(1 + trading days since the cross up)
        NaN when not in a golden cross

    The freshness term is what makes this add to IMOM: it rewards a *young*
    cross, which 12-month momentum and distance-above-200dMA do not encode.
    """
    n = len(closes)
    fast = _sma(closes, SMA_FAST)
    slow = _sma(closes, SMA_SLOW)
    out: list[Optional[float]] = [None] * n
    last_cross: Optional[int] = None
    prev_bull: Optional[bool] = None
    for i in range(n):
        if fast[i] is None or slow[i] is None or slow[i] == 0:
            prev_bull = None
            continue
        bull = fast[i] > slow[i]
        # A cross-up needs a defined previous state, matching and_(bull, not_(delay(bull,1)))
        if bull and prev_bull is False:
            last_cross = i
        prev_bull = bull
        if not bull or last_cross is None:
            continue
        out[i] = (fast[i] / slow[i] - 1.0) + 1.0 / (1.0 + (i - last_cross))
    return out


def signals(bars: list[tuple], bench_close_by_date: dict[str, float]) -> list[dict[str, Any]]:
    """Per-day signal legs. No look-ahead: every value at index i uses only bars 0..i."""
    dates = [b[0] for b in bars]
    closes = [b[4] for b in bars]
    n = len(bars)
    resid, beta = residual_returns(dates, closes, bench_close_by_date)
    cres = [0.0] * (n + 1)
    for i in range(n):
        cres[i + 1] = cres[i] + resid[i]
    ma200 = _sma(closes, TREND_MA)
    gcross = _golden_cross(closes)
    out = []
    for i in range(WARMUP, n):
        if beta[i] is None or i < LOOKBACK_LONG or ma200[i] is None:
            continue
        out.append({
            "date": dates[i],
            "close": closes[i],
            "beta": beta[i],
            "imom252_21": cres[i + 1 - SKIP] - cres[i + 1 - LOOKBACK_LONG],
            "imom20": cres[i + 1] - cres[i + 1 - LOOKBACK_SHORT],
            "ma_1_200": closes[i] / ma200[i] - 1.0,
            "golden_cross": gcross[i],
        })
    return out


def rank_composite(records_by_symbol: dict[str, dict[str, Any]],
                   legs: tuple[str, ...] = LEGS_BY_HOLD[DEFAULT_HOLD]) -> list[tuple[str, float]]:
    """Cross-sectional rank composite for ONE date. Input: {symbol: signal dict}.

    Each leg is percentile-ranked over EVERY name where that leg is defined
    (rank first, filter second — the exact construction the backtests validated);
    a name is scored only if all legs are defined for it, so an undefined
    golden_cross leg acts as the eligibility filter. Ties get average ranks,
    pandas pct convention (avg_rank / n).
    """
    leg_rank: dict[str, dict[str, float]] = {}
    for leg in legs:
        vals = sorted((r[leg], s) for s, r in records_by_symbol.items()
                      if r.get(leg) is not None)
        n = len(vals)
        rk: dict[str, float] = {}
        i = 0
        while i < n:
            j = i
            while j + 1 < n and vals[j + 1][0] == vals[i][0]:
                j += 1
            avg = (i + j) / 2.0 + 1.0
            for t in range(i, j + 1):
                rk[vals[t][1]] = avg / n
            i = j + 1
        leg_rank[leg] = rk
    syms = [s for s in records_by_symbol if all(s in leg_rank[l] for l in legs)]
    if len(syms) < 20:
        return []
    return sorted(((s, sum(leg_rank[l][s] for l in legs) / len(legs)) for s in syms),
                  key=lambda x: -x[1])


def main(argv: Optional[list[str]] = None) -> None:
    p = argparse.ArgumentParser(
        description="IMOM screen — cross-sectional idiosyncratic momentum (20-day hold)")
    p.add_argument("--data-dir", required=True,
                   help="directory of per-symbol OHLCV CSVs (date,open,high,low,close,volume)")
    p.add_argument("--benchmark", default="SPY", help="benchmark symbol inside --data-dir")
    p.add_argument("--hold", type=int, choices=sorted(LEGS_BY_HOLD), default=DEFAULT_HOLD,
                   help="holding period in trading days; selects the validated leg pair")
    p.add_argument("--blend", action="store_true",
                   help="add ta-screener's golden_cross leg (30/60-day holds only). "
                        "Higher excess, but only names in a golden cross are eligible.")
    p.add_argument("--decile", type=float, default=None,
                   help="selection fraction (default 0.10 plain, 0.05 for --blend)")
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

    if args.blend:
        if args.hold not in BLEND_LEGS_BY_HOLD:
            raise SystemExit(f"--blend is validated for holds {sorted(BLEND_LEGS_BY_HOLD)} only")
        legs = BLEND_LEGS_BY_HOLD[args.hold]
        # golden_cross is undefined outside a golden cross; rank_composite ranks
        # each leg over the FULL universe first (the validated construction) and
        # the undefined gc leg then filters eligibility on its own.
    else:
        legs = LEGS_BY_HOLD[args.hold]
    if args.decile is None:
        args.decile = 0.05 if args.blend else DEFAULT_DECILE
    ranked = rank_composite(latest, legs)
    if not ranked:
        raise SystemExit("not enough symbols with sufficient history to rank")
    cutoff = max(1, int(len(ranked) * args.decile))

    asof = max(r["date"] for r in latest.values())
    second = legs[1]
    head = LEG_LABEL[second]
    print(f"===== {'IMOM + GOLDEN-CROSS BLEND' if args.blend else 'IMOM SCREEN'} — {asof} =====")
    print(f"Universe {len(ranked)} symbols; top {args.decile*100:g}% = {cutoff} names; "
          f"hold {args.hold} days; legs {' + '.join(legs)}\n")
    extra = "golden X" if args.blend else ""
    wts = rank_weights(cutoff) if args.blend else None
    print(f"  {'#':>3} {'symbol':<8}{'score':>8}"
          + (f"{'weight':>8}" if args.blend else "")
          + f"{'imom12-1':>11}{head:>11}"
          + (f"{extra:>10}" if args.blend else "") + f"{'beta':>7}")
    for k, (sym, score) in enumerate(ranked[:args.top], 1):
        r = latest[sym]
        flag = "" if k <= cutoff else "  (below decile)"
        gc = f"{r['golden_cross']:>10.3f}" if args.blend else ""
        w = (f"{wts[k-1]*100:>7.2f}%" if k <= cutoff else f"{'--':>8}") if args.blend else ""
        print(f"  {k:>3} {sym:<8}{score:>8.3f}{w}{r['imom252_21']*100:>10.1f}%"
              f"{r[second]*100:>10.1f}%{gc}{r['beta']:>7.2f}{flag}")
    held = {20: "+2.12% per 20d matched excess (t=3.56), +4.04% raw, 58.8% positive",
            30: "+2.65% per 20d matched excess (t=3.67), +6.35% raw per 30d, 60.7% positive",
            60: "+2.63% per 20d matched excess (t=4.22), +12.75% raw per 60d, 63.8% positive"}
    held_blend = {30: "+5.60% per 20d matched excess (t=4.17), +10.71% raw per 30d, 63.4% positive",
                  60: "+5.62% per 20d matched excess (t=3.83), +21.61% raw per 60d, 67.9% positive"}
    tag = held_blend[args.hold] if args.blend else held[args.hold]
    print(f"\n  Held out 2024-2026 at a {args.hold}-day hold{' (blend)' if args.blend else ''}: {tag}.")
    print("  Excess per unit of time is flat across 5-120 day holds; longer holds win on")
    print("  costs (60d = 0.50%/yr vs 5d = 6.05%/yr) and hit rate, but are more exposed")
    print("  to a momentum crash -- the test window contained none.")
    print("  No stop. Worst held-out pick -50.1%; momentum crashes are the known")
    print("  failure mode and the test window contained none.")
    print("  Educational/research model — not investment advice.")


if __name__ == "__main__":
    main()
