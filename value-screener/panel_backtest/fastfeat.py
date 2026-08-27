#!/usr/bin/env python3
"""O(n) per-symbol computation of every signal needed for the literature bake-off.

Rule families implemented (sources in bakeoff.py):
  MA crossover (VMA)        Brock/Lakonishok/LeBaron, used by 1504.04254, 1811.06766
  Trading range break (TRB) 1504.04254 finds TRB beats MA
  Time-series momentum      sign of trailing return, vol-scaled (1904.04912)
  Cross-sectional 12-1      classic momentum, skipping the last month
  52-week-high proximity    George/Hwang
  Short-term reversal       past-week loser
  Idiosyncratic momentum    residual vs benchmark (1910.13115)
  Idiosyncratic volatility  1910.13115 links performance to IVol
  Volatility scaling        inverse realized vol (2212.07288)
"""
import csv, math, os
from collections import deque

WARMUP = 260


def load_bars(path):
    rows, seen = [], {}
    for r in csv.DictReader(open(path, encoding="utf-8-sig")):
        try:
            o, h, l, c = float(r["open"]), float(r["high"]), float(r["low"]), float(r["close"])
            v = float(r["volume"])
        except (TypeError, ValueError, KeyError):
            continue
        if min(o, h, l, c) <= 0 or v < 0:
            continue
        tol = max(1e-10, abs(c) * 1e-10)
        if h + tol < max(o, c, l) or l - tol > min(o, c, h):
            continue
        seen[r["date"]] = (r["date"], o, h, l, c, v)
    for d in sorted(seen):
        rows.append(seen[d])
    return rows


def sma(vals, p):
    n = len(vals); out = [None] * n; run = 0.0
    for i, x in enumerate(vals):
        run += x
        if i >= p:
            run -= vals[i - p]
        if i >= p - 1:
            out[i] = run / p
    return out


def rmax(vals, window, offset=0):
    n = len(vals); out = [None] * n; dq = deque()
    for i in range(n):
        j = i - offset
        if j < 0:
            continue
        while dq and vals[dq[-1]] <= vals[j]:
            dq.pop()
        dq.append(j)
        while dq[0] <= j - window:
            dq.popleft()
        if j >= window - 1:
            out[i] = vals[dq[0]]
    return out


def roll_stats(xs, w):
    """rolling mean and population variance, O(n)."""
    n = len(xs); m = [None] * n; v = [None] * n
    s = s2 = 0.0
    for i, x in enumerate(xs):
        s += x; s2 += x * x
        if i >= w:
            s -= xs[i - w]; s2 -= xs[i - w] * xs[i - w]
        if i >= w - 1:
            mu = s / w
            m[i] = mu
            v[i] = max(0.0, s2 / w - mu * mu)
    return m, v


def build(bars, bench_close_by_date, horizons=(5, 20)):
    n = len(bars)
    dates = [b[0] for b in bars]
    o = [b[1] for b in bars]; h = [b[2] for b in bars]
    l = [b[3] for b in bars]; c = [b[4] for b in bars]; vol = [b[5] for b in bars]

    ma5, ma50, ma150, ma200 = sma(c, 5), sma(c, 50), sma(c, 150), sma(c, 200)
    hi252 = rmax(h, 252, 0)
    trb50 = rmax(h, 50, 1)
    trb200 = rmax(h, 200, 1)

    tr = [h[0] - l[0]]
    for i in range(1, n):
        pc = c[i - 1]
        tr.append(max(h[i] - l[i], abs(h[i] - pc), abs(l[i] - pc)))
    atr = [None] * n
    if n >= 14:
        a = sum(tr[:14]) / 14.0; atr[13] = a
        for i in range(14, n):
            a = (a * 13.0 + tr[i]) / 14.0; atr[i] = a

    ret = [0.0] + [c[i] / c[i - 1] - 1.0 for i in range(1, n)]
    # Log returns + sample variance, matching realized_volatility() in the engine.
    lret = [0.0] + [math.log(c[i] / c[i - 1]) for i in range(1, n)]
    _, pvar20 = roll_stats(lret, 20)
    _, pvar60 = roll_stats(ret, 60)
    var20 = [None if v is None else v * 20.0 / 19.0 for v in pvar20]
    var60 = [None if v is None else v * 60.0 / 59.0 for v in pvar60]

    # market returns aligned to this symbol's dates; missing days -> 0 (no move)
    mret = [0.0] * n
    prev = None
    for i, d in enumerate(dates):
        cur = bench_close_by_date.get(d)
        if cur is not None and prev is not None and prev > 0:
            mret[i] = cur / prev - 1.0
        if cur is not None:
            prev = cur

    # rolling 252d beta via rolling sums, then residual returns
    W = 252
    sx = sy = sxx = sxy = 0.0
    beta = [None] * n; resid = [0.0] * n
    for i in range(n):
        x, y = mret[i], ret[i]
        sx += x; sy += y; sxx += x * x; sxy += x * y
        if i >= W:
            px, py = mret[i - W], ret[i - W]
            sx -= px; sy -= py; sxx -= px * px; sxy -= px * py
        if i >= W - 1:
            den = sxx - sx * sx / W
            b = (sxy - sx * sy / W) / den if den > 1e-18 else 0.0
            beta[i] = b
            resid[i] = ret[i] - b * mret[i]
    # cumulative residual sums for residual momentum
    cres = [0.0] * (n + 1)
    for i in range(n):
        cres[i + 1] = cres[i] + resid[i]
    _, prvar60 = roll_stats(resid, 60)
    rvar60 = [None if v is None else v * 60.0 / 59.0 for v in prvar60]

    dv = [c[i] * vol[i] for i in range(n)]
    adv = sma(dv, 20)

    hmax = max(horizons)
    out = []
    for i in range(WARMUP, n - hmax):
        if None in (ma50[i], ma150[i], ma200[i], hi252[i], trb50[i], trb200[i],
                    atr[i], var20[i], var60[i], beta[i], rvar60[i], adv[i]):
            continue
        rec = {
            "date": dates[i], "i": i, "close": c[i], "avgDollarVol20": adv[i],
            # --- trend / breakout family
            "ma_1_50": c[i] / ma50[i] - 1.0,
            "ma_1_200": c[i] / ma200[i] - 1.0,
            "ma_5_150": ma5[i] / ma150[i] - 1.0,
            "trb50": c[i] / trb50[i] - 1.0,
            "trb200": c[i] / trb200[i] - 1.0,
            "prox52w": c[i] / hi252[i] * 100.0,
            # --- momentum family
            "mom20": c[i] / c[i - 20] - 1.0,
            "mom60": c[i] / c[i - 60] - 1.0,
            "mom252": c[i] / c[i - 252] - 1.0,
            "mom252_21": c[i - 21] / c[i - 252] - 1.0,      # 12-1, skip last month
            "rev5": -(c[i] / c[i - 5] - 1.0),               # short-term reversal
            "rev20": -(c[i] / c[i - 20] - 1.0),
            # --- risk family
            "atrPct": atr[i] / c[i] * 100.0,
            "vol20": math.sqrt(var20[i] * 252.0) * 100.0,
            "ivol60": math.sqrt(rvar60[i] * 252.0) * 100.0,
            "beta": beta[i],
            # --- idiosyncratic momentum / reversal (residual vs benchmark)
            "imom20": cres[i + 1] - cres[i + 1 - 20],
            "imom252_21": cres[i + 1 - 21] - cres[i + 1 - 252],
            "irev5": -(cres[i + 1] - cres[i + 1 - 5]),
        }
        # vol-scaled variants (2212.07288, 1904.04912)
        sd = math.sqrt(var60[i] * 252.0) or 1e-9
        rec["tsmom_vs"] = (1.0 if rec["mom252"] > 0 else -1.0) / sd
        rec["mom252_21_vs"] = rec["mom252_21"] / sd
        for hz in horizons:
            ep = o[i + 1]
            rec[f"fwd{hz}"] = c[i + hz] / ep - 1.0
            rec[f"maxRise{hz}"] = max(h[i + 1:i + 1 + hz]) / ep - 1.0
        out.append(rec)
    return out
