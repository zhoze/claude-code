#!/usr/bin/env python3
"""O(n) computation of the four Reversal-5 inputs, plus 5-day outcomes.

The reference implementation recomputes ~50 indicators over a growing slice for
every day, which is O(n^2) and takes ~80s per symbol. These four features all
have incremental forms. validate.py asserts this path reproduces
magic_screener_v2_4.compute_technical_features exactly on the 36 symbols that
already have reference tapes.
"""
import csv, os
from collections import deque

WARMUP, HORIZON = 260, 5


def load_bars(path):
    rows = []
    for r in csv.DictReader(open(path, encoding="utf-8-sig")):
        try:
            o, h, l, c = (float(r["open"]), float(r["high"]), float(r["low"]), float(r["close"]))
            v = float(r["volume"])
        except (TypeError, ValueError, KeyError):
            continue
        if min(o, h, l, c) <= 0 or v < 0:
            continue
        # Same integrity rule as the reference normalizer.
        tol = max(1e-10, abs(c) * 1e-10)
        if h + tol < max(o, c, l) or l - tol > min(o, c, h):
            continue
        rows.append((r["date"], o, h, l, c, v))
    rows.sort(key=lambda x: x[0])
    # de-dup on date, keeping the last
    out, seen = [], {}
    for x in rows:
        seen[x[0]] = x
    for d in sorted(seen):
        out.append(seen[d])
    return out


def rolling_max(vals, window, offset=0):
    """rolling_max[i] = max(vals[i-window-offset+1-offset .. i-offset]) via monotonic deque."""
    n = len(vals)
    out = [None] * n
    dq = deque()
    for i in range(n):
        j = i - offset            # index entering the window
        if j >= 0:
            while dq and vals[dq[-1]] <= vals[j]:
                dq.pop()
            dq.append(j)
            while dq[0] <= j - window:
                dq.popleft()
            if j >= window - 1:
                out[i] = vals[dq[0]]
    return out


def features(bars):
    n = len(bars)
    o = [b[1] for b in bars]; h = [b[2] for b in bars]
    l = [b[3] for b in bars]; c = [b[4] for b in bars]; v = [b[5] for b in bars]

    # Wilder ATR(14), seeded exactly as atr_wilder does: mean of the first 14 TRs.
    tr = [h[0] - l[0]]
    for i in range(1, n):
        pc = c[i - 1]
        tr.append(max(h[i] - l[i], abs(h[i] - pc), abs(l[i] - pc)))
    atr = [None] * n
    if n >= 14:
        a = sum(tr[:14]) / 14.0
        atr[13] = a
        for i in range(14, n):
            a = (a * 13.0 + tr[i]) / 14.0
            atr[i] = a

    # proximity52w: close / max(high over the 252 bars ENDING at i, inclusive)
    hi252 = rolling_max(h, 252, offset=0)
    # breakout55: close vs max(high over the 55 bars ending at i-1)
    hi55prior = rolling_max(h, 55, offset=1)

    # 20-day average dollar volume (liquidity screen)
    dv = [c[i] * v[i] for i in range(n)]
    run, adv = 0.0, [None] * n
    for i in range(n):
        run += dv[i]
        if i >= 20:
            run -= dv[i - 20]
        if i >= 19:
            adv[i] = run / 20.0

    out = []
    for i in range(n):
        if i < WARMUP or i + HORIZON >= n:
            continue
        if atr[i] is None or hi252[i] is None or hi55prior[i] is None or i < 60:
            continue
        ep = o[i + 1]
        fut_h = max(h[i + 1:i + 1 + HORIZON]); fut_l = min(l[i + 1:i + 1 + HORIZON])
        out.append({
            "date": bars[i][0], "i": i,
            "atrPct": atr[i] / c[i] * 100.0,
            "proximity52wPct": c[i] / hi252[i] * 100.0,
            "perf60": (c[i] / c[i - 60] - 1.0) * 100.0,
            "breakout55Pct": (c[i] / hi55prior[i] - 1.0) * 100.0,
            "avgDollarVol20": adv[i],
            "fwd5": c[i + HORIZON] / ep - 1.0,
            "maxRise5": fut_h / ep - 1.0,
            "maxFall5": fut_l / ep - 1.0,
        })
    return out
