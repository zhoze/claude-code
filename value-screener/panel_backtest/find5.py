#!/usr/bin/env python3
"""Search for a 5-day setup: which features predict a near-term rise, and what is
the best achievable profit once costs and a random-entry control are applied.

Discovery uses 2020-2023 only. 2024-2026 is untouched until validation.
"""
import csv, math, os, random, statistics, sys

HERE = os.path.dirname(os.path.abspath(__file__))
TAPES = os.path.join(HERE, "tapes5")
IS_END = "2024-01-01"
COST = 2 * (5.0 + 1.0) / 10_000.0     # round trip: 5bps slip + 1bp commission per side

NUM = None  # filled at load


def load():
    rows = []
    for fn in sorted(os.listdir(TAPES)):
        if not fn.endswith(".csv"):
            continue
        sym = fn[:-4]
        for r in csv.DictReader(open(os.path.join(TAPES, fn))):
            d = {"sym": sym, "date": r["date"], "setup": r["setup"]}
            for k, v in r.items():
                if k in ("sym", "date", "setup"):
                    continue
                d[k] = float(v) if v not in ("", "None") else None
            rows.append(d)
    return rows


def spearman(pairs):
    """Rank correlation, robust to the fat tails in return data."""
    pairs = [(a, b) for a, b in pairs if a is not None and b is not None]
    if len(pairs) < 100:
        return None, 0
    n = len(pairs)
    def ranks(vals):
        order = sorted(range(n), key=lambda i: vals[i])
        rk = [0.0] * n
        i = 0
        while i < n:
            j = i
            while j + 1 < n and vals[order[j + 1]] == vals[order[i]]:
                j += 1
            avg = (i + j) / 2.0 + 1
            for k in range(i, j + 1):
                rk[order[k]] = avg
            i = j + 1
        return rk
    xa = ranks([p[0] for p in pairs]); ya = ranks([p[1] for p in pairs])
    mx, my = statistics.fmean(xa), statistics.fmean(ya)
    num = sum((a - mx) * (b - my) for a, b in zip(xa, ya))
    den = math.sqrt(sum((a - mx) ** 2 for a in xa) * sum((b - my) ** 2 for b in ya))
    return (num / den if den else None), n


def decile_table(rows, feat, label="fwd5"):
    vals = [(r[feat], r[label]) for r in rows if r.get(feat) is not None and r.get(label) is not None]
    if len(vals) < 500:
        return []
    vals.sort(key=lambda x: x[0])
    k = len(vals) // 10
    out = []
    for d in range(10):
        chunk = vals[d * k:(d + 1) * k] if d < 9 else vals[9 * k:]
        out.append({"decile": d + 1, "n": len(chunk),
                    "avg": statistics.fmean(x[1] for x in chunk) * 100,
                    "pos": sum(1 for x in chunk if x[1] > 0) / len(chunk) * 100})
    return out
