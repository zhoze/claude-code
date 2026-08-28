#!/usr/bin/env python3
"""blend_screen_core_decile10 — the decile-10 blend screen as a pure algorithm.
No I/O of any kind.

The caller supplies price data; this module computes the picks. There is no
downloading, no file access, no universe construction and no assumption about
how many symbols are screened — all of that belongs to the integrating system.
Standard library only, deterministic, Python 3.9+.

THE ALGORITHM (validated configuration)
---------------------------------------
For each symbol, three signal legs, each using only data up to the evaluation
day (no look-ahead anywhere):

  imom252_21    idiosyncratic momentum 12-1: the sum of residual daily returns
                (after removing a rolling 252-day beta against the benchmark)
                over the last 252 trading days, skipping the most recent 21
  ma_1_200      close / SMA200 - 1
  golden_cross  defined only while SMA50 > SMA200:
                (SMA50/SMA200 - 1) + 1/(1 + trading days since the cross up);
                undefined otherwise — this is the eligibility filter

Selection: each leg is percentile-ranked across EVERY symbol where that leg is
defined (rank first, filter second — ties get average ranks, rank/n). A symbol
is scored only if all legs are defined. The composite is the equal mean of the
three leg ranks. The top ``selection`` fraction (default 10%) is taken, weighted
linearly by rank: k, k-1, ..., 1, normalized (top position ~2x average weight).
This file is the DECILE-10 configuration — the calmer point on the measured
risk/return frontier (best 60-day own-series t-stat and Sharpe of the ladder).
The top-5% configuration lives in its sibling file, blend_screen_core.py.

Intended protocol: enter at the next session's open, hold ``60`` trading days
(30 also validated), re-rank, rebalance.

INPUT CONTRACT
--------------
prices     {symbol: bars} for the stocks to be screened (any quantity)
benchmark  bars for the market index the betas are computed against (the
           validation used SPY)
bars       an iterable of daily records, one of:
             (date, open, high, low, close, volume)  sequences, or
             {"date": ..., "open": ..., "high": ..., "low": ..., "close": ...,
              "volume": ...} mappings
           date is "YYYY-MM-DD" (or any string whose lexicographic order is
           chronological). Prices must be SPLIT-ADJUSTED. Records may be
           unsorted or contain duplicates; normalization sorts, deduplicates
           (last wins) and drops impossible candles (non-positive prices,
           negative volume, high/low inconsistent beyond float tolerance).
history    a symbol needs >= 313 valid bars (260 warmup + 52-week lookback
           overlap) before it can produce a signal; fewer bars -> it is
           silently reported in ``skipped``.

OUTPUT (run_screen)
-------------------
{
  "as_of":     latest signal date across the universe,
  "universe":  number of symbols that produced a signal,
  "eligible":  number scored (all legs defined, i.e. in a golden cross),
  "cutoff":    number of picks (max(1, int(eligible * selection))),
  "picks":     [ {symbol, rank, score, weight, imom252_21, ma_1_200,
                  golden_cross, beta, close, date}, ... ]   # rank-ordered
  "ranked":    the full scored list [(symbol, score), ...] descending,
  "skipped":   {symbol: reason} for symbols that produced no signal,
}
Weights sum to 1.0 and decrease monotonically. Symbols whose latest signal date
is older than ``as_of`` are still ranked (their most recent data is used); the
integrating system can filter on ``picks[i]["date"]`` if staleness matters.

VALIDATION SUMMARY (full detail in panel_backtest/README.md of the repository)
------------------------------------------------------------------------------
Held out 2024-2026 on ~500 US large caps, next-open execution, 12bps round trip:
60-day hold +18.10% raw per trade (66.5% positive), +4.39% per 20 days matched
excess vs same-day same-drawdown-bucket peers (Newey-West t=4.12, Sharpe 2.53),
survivorship-flat, random control p<0.0001; 30-day hold +8.98% raw, t=3.99.
The 5% sibling returns ~28% more excess per trade (+21.61% raw at 60d) at the
cost of concentration and a lower 60d t (3.83). Caveats: about
half the raw return is market beta; the portfolio runs beta ~2 and concentrates
in the leading momentum theme; there is no stop loss (stops tested worse); the
window contained no momentum crash; validated long-only on liquid US large caps.
Educational/research model, not investment advice.
"""

from __future__ import annotations

from typing import Any, Iterable, Mapping, Optional, Sequence

# ---- validated constants (changing them invalidates the numbers above) ----
LOOKBACK_LONG = 252     # 12 months
SKIP = 21               # 12-1: skip the most recent month
BETA_WINDOW = 252
TREND_MA = 200
SMA_FAST = 50           # golden-cross fast leg
SMA_SLOW = 200          # golden-cross slow leg
WARMUP = 260
DEFAULT_SELECTION = 0.10
LEGS = ("imom252_21", "ma_1_200", "golden_cross")
MIN_BARS = WARMUP + 5


# ---------------------------------------------------------------------------
# Normalization
# ---------------------------------------------------------------------------


def normalize_bars(bars: Iterable[Any]) -> list[tuple]:
    """(date, open, high, low, close, volume) tuples: sorted, deduplicated,
    impossible candles dropped. Accepts sequences or mappings."""
    seen: dict[str, tuple] = {}
    for b in bars:
        try:
            if isinstance(b, Mapping):
                d = str(b["date"])
                o, h, l, c = (float(b["open"]), float(b["high"]),
                              float(b["low"]), float(b["close"]))
                v = float(b["volume"])
            else:
                d = str(b[0])
                o, h, l, c, v = (float(b[1]), float(b[2]), float(b[3]),
                                 float(b[4]), float(b[5]))
        except (KeyError, IndexError, TypeError, ValueError):
            continue
        if min(o, h, l, c) <= 0 or v < 0:
            continue
        tol = max(1e-10, abs(c) * 1e-10)
        if h + tol < max(o, c, l) or l - tol > min(o, c, h):
            continue
        seen[d] = (d, o, h, l, c, v)
    return [seen[d] for d in sorted(seen)]


# ---------------------------------------------------------------------------
# Signal legs (identical to the validated implementation)
# ---------------------------------------------------------------------------


def _sma(vals: Sequence[float], period: int) -> list[Optional[float]]:
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


def _golden_cross(closes: Sequence[float]) -> list[Optional[float]]:
    """Spread + freshness while SMA50 > SMA200; None otherwise."""
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
        if bull and prev_bull is False:
            last_cross = i
        prev_bull = bull
        if not bull or last_cross is None:
            continue
        out[i] = (fast[i] / slow[i] - 1.0) + 1.0 / (1.0 + (i - last_cross))
    return out


def compute_signals(bars: list[tuple],
                    bench_close_by_date: Mapping[str, float]) -> list[dict[str, Any]]:
    """Full daily signal series for one symbol. Every value at index i uses only
    bars 0..i — safe for both live use and walk-forward backtesting."""
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
            "ma_1_200": closes[i] / ma200[i] - 1.0,
            "golden_cross": gcross[i],
        })
    return out


# ---------------------------------------------------------------------------
# Ranking and weights (rank first, filter second — the validated construction)
# ---------------------------------------------------------------------------


def rank_composite(records_by_symbol: Mapping[str, Mapping[str, Any]],
                   legs: Sequence[str] = LEGS) -> list[tuple[str, float]]:
    """Each leg percentile-ranked over every symbol where that leg is defined
    (average ranks for ties, rank/n); a symbol is scored only if all legs are
    defined. Returns [(symbol, composite score)] descending."""
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
    if not syms:
        return []
    return sorted(((s, sum(leg_rank[l][s] for l in legs) / len(legs)) for s in syms),
                  key=lambda x: -x[1])


def rank_weights(n: int) -> list[float]:
    """Linear rank weights k, k-1, ..., 1, normalized to sum to 1."""
    total = n * (n + 1) / 2.0
    return [(n - j) / total for j in range(n)]


# ---------------------------------------------------------------------------
# Top-level entry point
# ---------------------------------------------------------------------------


def run_screen(prices: Mapping[str, Iterable[Any]],
               benchmark: Iterable[Any],
               selection: float = DEFAULT_SELECTION,
               min_dollar_volume: float = 5_000_000.0) -> dict[str, Any]:
    """Screen the supplied universe. See the module docstring for the contract.

    min_dollar_volume: floor on the 20-day average of close*volume (the
    validated liquidity filter). Pass 0 to disable.
    """
    bench_bars = normalize_bars(benchmark)
    if len(bench_bars) < BETA_WINDOW:
        raise ValueError(f"benchmark needs >= {BETA_WINDOW} valid bars, got {len(bench_bars)}")
    bench = {b[0]: b[4] for b in bench_bars}

    latest: dict[str, dict[str, Any]] = {}
    skipped: dict[str, str] = {}
    for sym, raw in prices.items():
        bars = normalize_bars(raw)
        if len(bars) < MIN_BARS:
            skipped[sym] = f"insufficient history ({len(bars)} < {MIN_BARS} bars)"
            continue
        sig = compute_signals(bars, bench)
        if not sig:
            skipped[sym] = "no computable signal"
            continue
        if min_dollar_volume > 0:
            dv = [b[4] * b[5] for b in bars[-20:]]
            if sum(dv) / len(dv) < min_dollar_volume:
                skipped[sym] = "below dollar-volume floor"
                continue
        latest[sym] = sig[-1]

    ranked = rank_composite(latest, LEGS)
    if not ranked:
        return {"as_of": None, "universe": len(latest), "eligible": 0,
                "cutoff": 0, "picks": [], "ranked": [], "skipped": skipped}
    cutoff = max(1, int(len(ranked) * selection))
    weights = rank_weights(cutoff)
    picks = []
    for k, (sym, score) in enumerate(ranked[:cutoff], 1):
        r = latest[sym]
        picks.append({
            "symbol": sym, "rank": k, "score": score, "weight": weights[k - 1],
            "imom252_21": r["imom252_21"], "ma_1_200": r["ma_1_200"],
            "golden_cross": r["golden_cross"], "beta": r["beta"],
            "close": r["close"], "date": r["date"],
        })
    return {
        "as_of": max(r["date"] for r in latest.values()),
        "universe": len(latest),
        "eligible": len(ranked),
        "cutoff": cutoff,
        "picks": picks,
        "ranked": ranked,
        "skipped": skipped,
    }


# ---------------------------------------------------------------------------
# Deterministic self-test — synthetic data only, no I/O
# ---------------------------------------------------------------------------


def selftest() -> None:
    import math
    import random

    def synth(seed: int, n: int = 700, mu: float = 0.0004) -> list[tuple]:
        rng = random.Random(seed)
        out = []
        px = 100.0
        y, m, d = 2020, 1, 1
        for i in range(n):
            px *= math.exp(rng.gauss(mu, 0.015))
            hi = px * (1 + abs(rng.gauss(0, 0.005)))
            lo = px * (1 - abs(rng.gauss(0, 0.005)))
            op = lo + (hi - lo) * rng.random()
            d += 1
            if d > 28:
                d = 1; m += 1
                if m > 12:
                    m = 1; y += 1
            out.append((f"{y:04d}-{m:02d}-{d:02d}", op, max(hi, op, px),
                        min(lo, op, px), px, rng.uniform(1e6, 5e6)))
        return out

    bench = synth(0, mu=0.0003)
    prices = {f"S{i:02d}": synth(i, mu=0.0002 + 0.0004 * (i % 7)) for i in range(1, 61)}
    res = run_screen(prices, bench)
    assert res["picks"], "no picks on synthetic data"
    w = [p["weight"] for p in res["picks"]]
    assert abs(sum(w) - 1.0) < 1e-9, "weights must sum to 1"
    assert all(a >= b for a, b in zip(w, w[1:])), "weights must decrease"
    assert res["cutoff"] == max(1, int(res["eligible"] * DEFAULT_SELECTION))
    scores = [s for _, s in res["ranked"]]
    assert all(a >= b for a, b in zip(scores, scores[1:])), "ranking must be sorted"
    # no look-ahead: truncating the future must not change past signals
    sym = res["picks"][0]["symbol"]
    full = compute_signals(normalize_bars(prices[sym]), {b[0]: b[4] for b in normalize_bars(bench)})
    trunc = compute_signals(normalize_bars(prices[sym][:600]), {b[0]: b[4] for b in normalize_bars(bench)})
    fmap = {r["date"]: r for r in full}
    for r in trunc:
        f = fmap[r["date"]]
        for k in ("imom252_21", "ma_1_200", "beta"):
            assert f[k] == r[k], f"look-ahead detected in {k}"
        assert (f["golden_cross"] is None) == (r["golden_cross"] is None)
        if f["golden_cross"] is not None:
            assert f["golden_cross"] == r["golden_cross"]
    # normalization: shuffled/duplicated/corrupt input converges to the same bars
    rng = random.Random(9)
    messy = list(prices[sym]) + list(prices[sym][:50]) + [("2021-01-05", -1, 2, 3, 4, 5)]
    rng.shuffle(messy)
    assert normalize_bars(messy) == normalize_bars(prices[sym]), "normalization not canonical"
    print(f"selftest OK — universe {res['universe']}, eligible {res['eligible']}, "
          f"picks {res['cutoff']}, as_of {res['as_of']}")


if __name__ == "__main__":
    selftest()
