#!/usr/bin/env python3
"""The 5-day setup discovered in-sample, validated out-of-sample.

Rule (all inputs demeaned per symbol, so it is a timing signal rather than a
standing bet on high-beta names):

    score5 = z(atrPct) - z(proximity52wPct) - z(perf60) - z(breakout55Pct)

i.e. buy a stock when its own volatility is unusually high AND it is NOT
extended. This is the opposite of what the v2.4 entry model rewards.
"""
import math, os, random, statistics, sys
import find5 as F

IS_END = F.IS_END
COST = F.COST


def zmap(rows, feat):
    """Per-symbol z-score: (x - median_sym) / IQR_sym."""
    bysym = {}
    for r in rows:
        bysym.setdefault(r["sym"], []).append(r)
    out = {}
    for s, rs in bysym.items():
        vals = sorted(x[feat] for x in rs if x.get(feat) is not None)
        if len(vals) < 100:
            continue
        med = statistics.median(vals)
        q1 = F.__dict__.get("_q", None)
        lo = vals[int(len(vals) * 0.25)]
        hi = vals[int(len(vals) * 0.75)]
        iqr = (hi - lo) or 1.0
        out[s] = (med, iqr)
    return out


def add_score(rows, params=None):
    feats = ["atrPct", "proximity52wPct", "perf60", "breakout55Pct"]
    signs = {"atrPct": +1.0, "proximity52wPct": -1.0, "perf60": -1.0, "breakout55Pct": -1.0}
    norms = {f: zmap(rows, f) for f in feats}
    for r in rows:
        tot, k = 0.0, 0
        for f in feats:
            v = r.get(f)
            n = norms[f].get(r["sym"])
            if v is None or n is None:
                continue
            med, iqr = n
            tot += signs[f] * (v - med) / iqr
            k += 1
        r["score5"] = tot / k if k == len(feats) else None
    return rows


def top_slice(rows, pct):
    v = [r for r in rows if r.get("score5") is not None]
    v.sort(key=lambda r: -r["score5"])
    return v[:max(1, int(len(v) * pct))]


def summarize(sel, label, gross_key="fwd5"):
    if not sel:
        return None
    net = [r[gross_key] - COST for r in sel]
    return {"label": label, "n": len(net),
            "net": statistics.fmean(net) * 100,
            "success": sum(1 for x in net if x > 0) / len(net) * 100,
            "median": statistics.median(net) * 100,
            "maxRise": statistics.fmean(r["maxRise5"] for r in sel) * 100,
            "maxFall": statistics.fmean(r["maxFall5"] for r in sel) * 100}
