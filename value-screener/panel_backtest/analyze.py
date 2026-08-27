#!/usr/bin/env python3
"""Pass 2: panel backtest, calibration and null tests off the signal tapes."""
import csv, json, math, os, random, statistics, sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
import magic_screener_v2_4 as ms

HERE = os.path.dirname(os.path.abspath(__file__))
DATA, TAPES = os.path.join(HERE, "data"), os.path.join(HERE, "tapes")
HORIZON, WARMUP, BENCH = 20, 260, "SPY"
SLIP_BPS, COMM_BPS = 5.0, 1.0


def load(sym):
    return ms.load_history_csv(os.path.join(DATA, f"{sym}.csv"))


def tape(sym):
    with open(os.path.join(TAPES, f"{sym}.json")) as f:
        return json.load(f)["rows"]


def simulate(bars, rows, min_score, barrier_mode, atr_t=3.0, atr_s=2.0,
             profit=0.05, stop=0.03, require_setup=True, forced_days=None):
    """Non-overlapping trade simulation, mirroring backtest_entry_model's loop.

    forced_days: iterable of tape indices to enter on regardless of score/setup
    (used for the random-timing null).
    """
    slip = SLIP_BPS / 10_000.0
    trades, uncond = [], []
    blocked_until = -1
    forced = set(forced_days) if forced_days is not None else None

    for r in rows:
        i = r["i"]
        entry_bar = bars[i + 1]
        entry_price = entry_bar.open * (1.0 + slip)
        future = bars[i + 1:i + 1 + HORIZON]
        if len(future) < HORIZON:
            continue
        fixed_ret = ms._net_trade_return(entry_price, future[-1].close * (1.0 - slip), COMM_BPS)
        uncond.append(fixed_ret)

        if forced is None:
            if r["score"] < min_score:
                continue
            if require_setup and r["setup"] == "WAIT":
                continue
        elif i not in forced:
            continue
        if i < blocked_until:
            continue

        b = ms._resolve_barriers(r["atrPct"], barrier_mode, atr_t, atr_s, profit, stop)
        if b is None:
            continue
        tgt, stp = b
        path = ms._path_outcome(future, entry_price, tgt, stp,
                                exit_slippage_bps=SLIP_BPS, commission_bps=COMM_BPS)
        exit_idx = min(i + int(path["holdingDays"]), len(bars) - 1)
        trades.append({
            "signalIdx": i, "signalDate": r["date"], "entryDate": entry_bar.date.isoformat(),
            "exitDate": bars[exit_idx].date.isoformat(), "score": r["score"], "setup": r["setup"],
            "atrPct": r["atrPct"], "targetPct": tgt * 100, "stopPct": stp * 100,
            "netReturn": path["netReturn"], "outcome": path["outcome"],
            "holdingDays": path["holdingDays"], "fixedHorizonReturn": fixed_ret,
            "mfe": path["mfe"], "mae": path["mae"],
        })
        blocked_until = exit_idx
    return trades, uncond


def agg(trades):
    if not trades:
        return None
    r = [t["netReturn"] for t in trades]
    n = len(r)
    return {
        "trades": n,
        "expectancy": statistics.fmean(r),
        "median": statistics.median(r),
        "winRate": sum(x > 0 for x in r) / n,
        "targetRate": sum(t["outcome"] == "TARGET" for t in trades) / n,
        "stopRate": sum(t["outcome"] == "STOP" for t in trades) / n,
        "neitherRate": sum(t["outcome"] == "NEITHER" for t in trades) / n,
        "profitFactor": ms._profit_factor(r),
        "avgHold": statistics.fmean(t["holdingDays"] for t in trades),
        "avgMFE": statistics.fmean(t["mfe"] for t in trades if t["mfe"] is not None),
        "avgMAE": statistics.fmean(t["mae"] for t in trades if t["mae"] is not None),
    }


def cluster_bootstrap_ci(by_symbol, samples=2000, seed=11):
    """Resample whole symbols, not individual trades: trades inside one ticker
    share a price path and are not independent draws."""
    syms = [s for s, t in by_symbol.items() if t]
    if len(syms) < 2:
        return None, None
    rng = random.Random(seed)
    means = []
    for _ in range(samples):
        pool = []
        for _ in range(len(syms)):
            pool.extend(by_symbol[syms[rng.randrange(len(syms))]])
        if pool:
            means.append(statistics.fmean(t["netReturn"] for t in pool))
    means.sort()
    return ms.percentile_value(means, 0.025), ms.percentile_value(means, 0.975)


def calibration(all_rows):
    order = ["<50", "50-59", "60-69", "70-79", "80-89", "90-100"]
    out = []
    for b in order:
        g = [x for x in all_rows if x["bucket"] == b]
        if not g:
            continue
        out.append({
            "bucket": b, "n": len(g),
            "avgFwd": statistics.fmean(x["fwd"] for x in g),
            "posRate": sum(x["fwd"] > 0 for x in g) / len(g),
        })
    return out
