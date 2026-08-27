#!/usr/bin/env python3
"""Reversal-5 on a 507-name universe: out-of-universe and out-of-sample tests."""
import os, statistics, sys
import fast5

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, "data500")
IS_END = "2024-01-01"
COST = 2 * (5.0 + 1.0) / 10_000.0
SIGNS = {"atrPct": +1.0, "proximity52wPct": -1.0, "perf60": -1.0, "breakout55Pct": -1.0}
MIN_ADV = 5_000_000.0     # the engine's own liquidity floor

# The 36 names the rule was discovered on. Everything else is out-of-universe.
ORIGINAL = set("""AAPL AMGN AXP BA CAT CRM CSCO CVX DIS DOW GS HD IBM JNJ JPM KO MCD MMM
MRK MSFT NKE NVDA PG SHW TRV UNH V VZ WMT INTC PFE T F PYPL MRNA XOM""".split())


def load_all():
    rows = []
    for fn in sorted(os.listdir(DATA)):
        if not fn.endswith(".csv"):
            continue
        sym = fn[:-4]
        try:
            bars = fast5.load_bars(os.path.join(DATA, fn))
        except Exception:
            continue
        if len(bars) < 700:
            continue
        for r in fast5.features(bars):
            if r["avgDollarVol20"] is None or r["avgDollarVol20"] < MIN_ADV:
                continue
            r["sym"] = sym
            rows.append(r)
    return rows


def fit_norms(rows):
    """Per-symbol median/IQR from the given (in-sample) rows only."""
    by = {}
    for r in rows:
        by.setdefault(r["sym"], []).append(r)
    norms = {}
    for s, rs in by.items():
        d = {}
        for f in SIGNS:
            v = sorted(x[f] for x in rs if x.get(f) is not None)
            if len(v) < 100:
                break
            q1, q3 = v[int(len(v) * .25)], v[int(len(v) * .75)]
            d[f] = (statistics.median(v), (q3 - q1) or 1.0)
        if len(d) == len(SIGNS):
            norms[s] = d
    return norms


def apply_score(rows, norms):
    for r in rows:
        n = norms.get(r["sym"])
        if not n:
            r["score5"] = None
            continue
        tot = 0.0
        for f, sign in SIGNS.items():
            med, iqr = n[f]
            tot += sign * (r[f] - med) / iqr
        r["score5"] = tot / len(SIGNS)
    return rows


def top_pct(rows, pct):
    v = [r for r in rows if r.get("score5") is not None]
    v.sort(key=lambda r: -r["score5"])
    return v[:max(1, int(len(v) * pct))]


def summ(sel, label):
    if not sel:
        return None
    net = [r["fwd5"] - COST for r in sel]
    return {"label": label, "n": len(net), "net": statistics.fmean(net) * 100,
            "succ": sum(1 for x in net if x > 0) / len(net) * 100,
            "med": statistics.median(net) * 100,
            "syms": len({r["sym"] for r in sel})}


def daily_topn(rows, n_per_day):
    """Realistic portfolio: each day rank the whole universe, take the best N."""
    byday = {}
    for r in rows:
        if r.get("score5") is not None:
            byday.setdefault(r["date"], []).append(r)
    out = []
    for d, rs in byday.items():
        rs.sort(key=lambda r: -r["score5"])
        out.extend(rs[:n_per_day])
    return out
