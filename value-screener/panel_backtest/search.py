#!/usr/bin/env python3
"""Grid search for configurations reaching >= 80% success, with honest scoring.

Every config is judged on three things, not one:
  1. success rate (net profit > 0)
  2. expectancy per trade
  3. the SAME config's success rate under random entry timing

(3) is the control. If random entry hits the same success rate with the same
barriers, the win rate is barrier geometry and contains no signal.

Tuned in-sample (2020-2023), then validated out-of-sample (2024-2026).
"""
import json, os, random, statistics, sys
import analyze as A
sys.path.insert(0, "/home/user/claude-code/value-screener")
import magic_screener_v2_4 as ms

SLIP, COMM = A.SLIP_BPS, A.COMM_BPS
IS_END = "2024-01-01"          # in-sample  < IS_END
OOS_END = "2026-12-31"

syms = sorted(os.path.basename(p)[:-5] for p in os.listdir(A.TAPES) if p.endswith(".json"))
bars = {s: A.load(s) for s in syms}
tapes = {s: A.tape(s) for s in syms}

# Market regime: is SPY above its own 200-day SMA at the signal date?
spy = A.load("SPY")
spy_close = [b.close for b in spy]
sma200 = ms.sma_series(spy_close, 200)
REGIME = {spy[i].date.isoformat(): (sma200[i] is not None and spy_close[i] > sma200[i])
          for i in range(len(spy))}


def simulate(sym, horizon, min_score, atr_t, atr_s, regime_on, strong_only,
             lo, hi, forced=None):
    b, rows = bars[sym], tapes[sym]
    slip = SLIP / 10_000.0
    out, blocked = [], -1
    for r in rows:
        if not (lo <= r["date"] < hi):
            continue
        i = r["i"]
        fut = b[i + 1:i + 1 + horizon]
        if len(fut) < horizon:
            continue
        if forced is None:
            if r["score"] < min_score:
                continue
            if strong_only:
                if r["setup"] != "STRONG_LONG_SETUP":
                    continue
            elif r["setup"] == "WAIT":
                continue
            if regime_on and not REGIME.get(r["date"], False):
                continue
        elif i not in forced:
            continue
        if i < blocked:
            continue
        bar_ = ms._resolve_barriers(r["atrPct"], "atr", atr_t, atr_s, 0.05, 0.03)
        if bar_ is None:
            continue
        tgt, stp = bar_
        ep = b[i + 1].open * (1.0 + slip)
        p = ms._path_outcome(fut, ep, tgt, stp, exit_slippage_bps=SLIP, commission_bps=COMM)
        out.append({"i": i, "date": r["date"], "ret": p["netReturn"],
                    "outcome": p["outcome"], "hold": p["holdingDays"]})
        blocked = min(i + int(p["holdingDays"]), len(b) - 1)
    return out


def run(cfg, lo, hi, forced_map=None):
    allt = []
    per = {}
    for s in syms:
        t = simulate(s, cfg["h"], cfg["ms"], cfg["t"], cfg["s"], cfg["reg"], cfg["strong"],
                     lo, hi, forced=None if forced_map is None else forced_map.get(s))
        per[s] = t
        allt.extend(t)
    if not allt:
        return None
    r = [x["ret"] for x in allt]
    win = [x for x in r if x > 0]
    return {"n": len(r), "success": len(win) / len(r) * 100,
            "expectancy": statistics.fmean(r) * 100,
            "median": statistics.median(r) * 100,
            "avgWin": statistics.fmean(win) * 100 if win else 0.0,
            "avgLoss": statistics.fmean([x for x in r if x <= 0]) * 100 if len(win) < len(r) else 0.0,
            "symbols": sum(1 for v in per.values() if v), "per": per}


def random_control(cfg, lo, hi, trials=40, seed=3):
    """Same config, random entry dates, matched trade count."""
    rng = random.Random(seed)
    succ, exp = [], []
    base = run(cfg, lo, hi)
    if not base:
        return None, None
    counts = {s: len(v) for s, v in base["per"].items()}
    for _ in range(trials):
        fm = {}
        for s in syms:
            k = counts.get(s, 0)
            if not k:
                fm[s] = set(); continue
            idxs = [r["i"] for r in tapes[s] if lo <= r["date"] < hi]
            if not idxs:
                fm[s] = set(); continue
            fm[s] = set(rng.sample(idxs, min(k * 3, len(idxs))))
        res = run(cfg, lo, hi, forced_map=fm)
        if res:
            succ.append(res["success"]); exp.append(res["expectancy"])
    return (statistics.fmean(succ) if succ else None,
            statistics.fmean(exp) if exp else None)


if __name__ == "__main__":
    grid = []
    for h in (10, 20, 40):
        for t in (0.25, 0.5, 0.75, 1.0, 1.5, 2.0, 3.0):
            for s in (2.0, 3.0, 4.0, 6.0, 8.0):
                for msc in (70.0, 75.0, 80.0, 85.0):
                    for reg in (False, True):
                        grid.append({"h": h, "t": t, "s": s, "ms": msc,
                                     "reg": reg, "strong": False})
    print(f"searching {len(grid)} configs in-sample (2019..{IS_END})", flush=True)
    hits = []
    for k, cfg in enumerate(grid):
        res = run(cfg, "0000", IS_END)
        if res and res["n"] >= 60 and res["success"] >= 80.0:
            hits.append((cfg, res))
        if (k + 1) % 200 == 0:
            print(f"  ...{k+1}/{len(grid)}  hits so far: {len(hits)}", flush=True)
    print(f"\n{len(hits)} configs reach >=80% success in-sample (min 60 trades)\n")
    hits.sort(key=lambda x: -x[1]["expectancy"])
    json.dump([{"cfg": c, "is": {k: v for k, v in r.items() if k != "per"}} for c, r in hits],
              open("search_hits.json", "w"), indent=1)
    print(f"{'h':>3}{'tgt':>5}{'stop':>5}{'score':>6}{'reg':>5} | "
          f"{'n':>5}{'succ%':>7}{'exp%':>8}{'med%':>7}{'avgW':>7}{'avgL':>7}")
    for c, r in hits[:25]:
        print(f"{c['h']:>3}{c['t']:>5}{c['s']:>5}{c['ms']:>6.0f}{str(c['reg']):>5} | "
              f"{r['n']:>5}{r['success']:>6.1f}%{r['expectancy']:>+7.3f}%"
              f"{r['median']:>+6.2f}%{r['avgWin']:>+6.2f}%{r['avgLoss']:>+6.2f}%")
