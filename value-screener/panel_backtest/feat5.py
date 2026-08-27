#!/usr/bin/env python3
"""Extended 5-day feature set, O(n) per symbol.

New families beyond the 20-day bake-off, each with a source:

  overnight / intraday split   1410.5513 (4-factor model for overnight returns),
                               2010.01727 (overnight positive, intraday negative)
  MAX / lottery                high max daily return -> LOW future return
  realized skewness            positive skew -> lower return
  drift regime                 2511.12490: >60% positive days in trailing 63d
  post-extreme reversal        cond-mat/0406696: reversal after a large 1-day move
  Amihud illiquidity           |ret| / dollar volume
  range compression            current ATR vs its own 60d norm
  downside semi-beta           beta estimated on down-market days only
"""
import csv, math
from collections import deque

WARMUP = 260
HZ = 5


def load_bars(path):
    seen = {}
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
    return [seen[d] for d in sorted(seen)]


def sma(v, p):
    n = len(v); out = [None] * n; run = 0.0
    for i, x in enumerate(v):
        run += x
        if i >= p:
            run -= v[i - p]
        if i >= p - 1:
            out[i] = run / p
    return out


def rmax(v, w, off=0):
    n = len(v); out = [None] * n; dq = deque()
    for i in range(n):
        j = i - off
        if j < 0:
            continue
        while dq and v[dq[-1]] <= v[j]:
            dq.pop()
        dq.append(j)
        while dq[0] <= j - w:
            dq.popleft()
        if j >= w - 1:
            out[i] = v[dq[0]]
    return out


def roll_moments(xs, w):
    """rolling mean, sample variance, and skewness."""
    n = len(xs); m = [None] * n; var = [None] * n; sk = [None] * n
    s = s2 = s3 = 0.0
    for i, x in enumerate(xs):
        s += x; s2 += x * x; s3 += x ** 3
        if i >= w:
            y = xs[i - w]; s -= y; s2 -= y * y; s3 -= y ** 3
        if i >= w - 1:
            mu = s / w
            pv = max(0.0, s2 / w - mu * mu)
            m[i] = mu
            var[i] = pv * w / (w - 1)
            if pv > 1e-18:
                m3 = s3 / w - 3 * mu * s2 / w + 2 * mu ** 3
                sk[i] = m3 / (pv ** 1.5)
    return m, var, sk


def build(bars, bench_by_date):
    n = len(bars)
    d = [b[0] for b in bars]; o = [b[1] for b in bars]; h = [b[2] for b in bars]
    lo = [b[3] for b in bars]; c = [b[4] for b in bars]; vv = [b[5] for b in bars]
    if n < WARMUP + HZ + 5:
        return []

    ret = [0.0] + [c[i] / c[i - 1] - 1.0 for i in range(1, n)]
    # overnight = prev close -> today's open; intraday = open -> close
    on = [0.0] + [o[i] / c[i - 1] - 1.0 for i in range(1, n)]
    idr = [c[i] / o[i] - 1.0 for i in range(n)]
    con = [0.0] * (n + 1); cid = [0.0] * (n + 1); cre = [0.0] * (n + 1)
    for i in range(n):
        con[i + 1] = con[i] + on[i]; cid[i + 1] = cid[i] + idr[i]

    lret = [0.0] + [math.log(c[i] / c[i - 1]) for i in range(1, n)]
    _, var20, _ = roll_moments(lret, 20)
    _, var60, skew60 = roll_moments(ret, 60)

    mret = [0.0] * n; prev = None
    for i, dt in enumerate(d):
        cur = bench_by_date.get(dt)
        if cur is not None and prev is not None and prev > 0:
            mret[i] = cur / prev - 1.0
        if cur is not None:
            prev = cur

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
            beta[i] = b; resid[i] = ret[i] - b * mret[i]
    for i in range(n):
        cre[i + 1] = cre[i] + resid[i]

    tr = [h[0] - lo[0]]
    for i in range(1, n):
        pc = c[i - 1]
        tr.append(max(h[i] - lo[i], abs(h[i] - pc), abs(lo[i] - pc)))
    atr = [None] * n
    if n >= 14:
        a = sum(tr[:14]) / 14.0; atr[13] = a
        for i in range(14, n):
            a = (a * 13.0 + tr[i]) / 14.0; atr[i] = a
    atr60 = sma([x if x is not None else 0.0 for x in atr], 60)

    dv = [c[i] * vv[i] for i in range(n)]
    adv = sma(dv, 20)
    amih = sma([abs(ret[i]) / dv[i] * 1e9 if dv[i] > 0 else 0.0 for i in range(n)], 20)
    vol20d = sma(vv, 20); vol60d = sma(vv, 60)

    hi252 = rmax(h, 252, 0)
    ma50 = sma(c, 50); ma200 = sma(c, 200)

    # MAX effect: largest single-day return in the trailing month
    maxret21 = rmax(ret, 21, 0)
    # drift regime (2511.12490): share of positive days in trailing 63
    pos = [1.0 if ret[i] > 0 else 0.0 for i in range(n)]
    drift63 = sma(pos, 63)
    # downside semi-beta: covariance on down-market days, 252d window
    dq_num = deque(); dq_den = deque()
    semib = [None] * n; num = den = 0.0
    for i in range(n):
        if mret[i] < 0:
            a_, b_ = ret[i] * mret[i], mret[i] * mret[i]
        else:
            a_, b_ = 0.0, 0.0
        dq_num.append(a_); dq_den.append(b_); num += a_; den += b_
        if len(dq_num) > W:
            num -= dq_num.popleft(); den -= dq_den.popleft()
        if i >= W - 1 and den > 1e-18:
            semib[i] = num / den

    out = []
    for i in range(WARMUP, n - HZ):
        if None in (atr[i], atr60[i], var20[i], var60[i], skew60[i], beta[i], semib[i],
                    adv[i], hi252[i], ma50[i], ma200[i], maxret21[i], drift63[i]):
            continue
        if atr60[i] <= 0 or adv[i] <= 0:
            continue
        ep = o[i + 1]
        fut_h = max(h[i + 1:i + 1 + HZ]); fut_l = min(lo[i + 1:i + 1 + HZ])
        rec = {
            "date": d[i], "i": i, "close": c[i], "avgDollarVol20": adv[i],
            "prox52w": c[i] / hi252[i] * 100.0,
            # carried over from the 20-day winner
            "imom252_21": cre[i + 1 - 21] - cre[i + 1 - 252],
            "imom20": cre[i + 1] - cre[i + 1 - 20],
            "ma_1_200": c[i] / ma200[i] - 1.0,
            "ma_1_50": c[i] / ma50[i] - 1.0,
            # --- new: overnight / intraday (1410.5513, 2010.01727)
            "on_mom20": con[i + 1] - con[i + 1 - 20],
            "id_mom20": cid[i + 1] - cid[i + 1 - 20],
            "on_minus_id20": (con[i + 1] - con[i + 1 - 20]) - (cid[i + 1] - cid[i + 1 - 20]),
            "on_mom5": con[i + 1] - con[i + 1 - 5],
            # --- new: lottery / skew (expect NEGATIVE)
            "max21": maxret21[i],
            "skew60": skew60[i],
            # --- new: liquidity
            "amihud": amih[i],
            "vol_ratio": (vol20d[i] / vol60d[i]) if vol60d[i] else 1.0,
            # --- new: regime / compression / risk
            "drift63": drift63[i],
            "atr_ratio": atr[i] / atr60[i],
            "semibeta": semib[i],
            "beta": beta[i],
            "rvol20": math.sqrt(var20[i] * 252.0) * 100.0,
            # --- reversal legs
            "rev5": -(c[i] / c[i - 5] - 1.0),
            "irev5": -(cre[i + 1] - cre[i + 1 - 5]),
            "extreme1d": ret[i],
            # outcomes
            "fwd5": c[i + HZ] / ep - 1.0,
            "maxRise5": fut_h / ep - 1.0,
            "maxFall5": fut_l / ep - 1.0,
        }
        out.append(rec)
    return out
