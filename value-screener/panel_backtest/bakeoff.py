#!/usr/bin/env python3
"""Head-to-head: the v2.4 screen vs technical rules from the literature.

Every rule is scored the same way:
  - rank the whole universe cross-sectionally each day
  - buy the top decile at the next open, hold H days
  - net of 12bps round trip

and judged on MATCHED EXCESS return, not raw return:

    excess = fwd(stock) - mean(fwd of every stock that day in the SAME
                               52-week-drawdown bucket)

Matching on the date removes market direction. Matching on the drawdown bucket
removes the survivorship gradient that made Reversal-5 look profitable (deeply
beaten-down names only remain in a "today's largest 500" universe if they
recovered). What survives is cross-sectional selection skill.
"""
import math, os, random, statistics
import fastfeat

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, "data500")
IS_END = "2024-01-01"
COST = 2 * (5.0 + 1.0) / 10_000.0
MIN_ADV = 5_000_000.0
BUCKETS = [(0, 40), (40, 60), (60, 75), (75, 85), (85, 92), (92, 97), (97, 200)]

# signal -> (higher-is-better?, source)
RULES = {
    "ma_1_50":      (True,  "MA(1,50) crossover — Brock/LB/LeB; 1504.04254, 1811.06766"),
    "ma_1_200":     (True,  "MA(1,200) crossover"),
    "ma_5_150":     (True,  "MA(5,150) crossover"),
    "trb50":        (True,  "Trading range break 50d — 1504.04254 (TRB > MA)"),
    "trb200":       (True,  "Trading range break 200d"),
    "prox52w":      (True,  "52-week-high proximity — George/Hwang"),
    "mom252_21":    (True,  "Cross-sectional momentum 12-1"),
    "mom252_21_vs": (True,  "12-1 momentum, vol-scaled — 2212.07288 / 1904.04912"),
    "tsmom_vs":     (True,  "Time-series momentum, vol-scaled — 1904.04912"),
    "mom20":        (True,  "1-month momentum"),
    "imom252_21":   (True,  "Idiosyncratic momentum 12-1 — 1910.13115"),
    "imom20":       (True,  "Idiosyncratic momentum 1m — 1910.13115"),
    "rev5":         (True,  "Short-term reversal (1 week)"),
    "rev20":        (True,  "Short-term reversal (1 month)"),
    "irev5":        (True,  "Idiosyncratic reversal 1w — 1910.13115"),
    "ivol60":       (False, "Low idiosyncratic vol — 1910.13115 (IVol negatively related)"),
    "vol20":        (False, "Low realized vol"),
    "beta":         (False, "Low beta"),
}


def bucket(p):
    for k, (lo, hi) in enumerate(BUCKETS):
        if lo <= p < hi:
            return k
    return len(BUCKETS) - 1


def load_panel():
    bench = {b[0]: b[4] for b in fastfeat.load_bars(os.path.join(DATA, "SPY.csv"))} \
        if os.path.exists(os.path.join(DATA, "SPY.csv")) else None
    if bench is None:
        bench = {b[0]: b[4] for b in fastfeat.load_bars(os.path.join(HERE, "data", "SPY.csv"))}
    rows = []
    for fn in sorted(os.listdir(DATA)):
        if not fn.endswith(".csv"):
            continue
        sym = fn[:-4]
        try:
            bars = fastfeat.load_bars(os.path.join(DATA, fn))
        except Exception:
            continue
        if len(bars) < 700:
            continue
        for r in fastfeat.build(bars, bench):
            if r["avgDollarVol20"] is None or r["avgDollarVol20"] < MIN_ADV:
                continue
            r["sym"] = sym
            r["bkt"] = bucket(r["prox52w"])
            rows.append(r)
    return rows


def add_matched(rows, hz):
    """excess = fwd - mean(fwd | same date, same drawdown bucket)."""
    grp = {}
    for r in rows:
        grp.setdefault((r["date"], r["bkt"]), []).append(r[f"fwd{hz}"])
    means = {k: statistics.fmean(v) for k, v in grp.items()}
    counts = {k: len(v) for k, v in grp.items()}
    for r in rows:
        k = (r["date"], r["bkt"])
        r[f"exc{hz}"] = r[f"fwd{hz}"] - means[k] if counts[k] >= 5 else None
    return rows


def daily_top(rows, feat, higher, frac=0.10):
    byday = {}
    for r in rows:
        if r.get(feat) is not None:
            byday.setdefault(r["date"], []).append(r)
    out = []
    for d, rs in byday.items():
        rs.sort(key=lambda r: r[feat], reverse=higher)
        out.extend(rs[:max(1, int(len(rs) * frac))])
    return out


def evaluate(rows, feat, higher, hz, frac=0.10):
    sel = daily_top(rows, feat, higher, frac)
    exc = [r[f"exc{hz}"] for r in sel if r.get(f"exc{hz}") is not None]
    raw = [r[f"fwd{hz}"] - COST for r in sel]
    if len(exc) < 200:
        return None
    n = len(exc)
    se = statistics.stdev(exc) / math.sqrt(n)
    return {"n": n, "raw": statistics.fmean(raw) * 100,
            "exc": statistics.fmean(exc) * 100,
            "t": statistics.fmean(exc) / se if se else 0.0,
            "succ": sum(1 for x in raw if x > 0) / len(raw) * 100,
            "syms": len({r["sym"] for r in sel})}
