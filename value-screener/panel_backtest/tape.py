#!/usr/bin/env python3
"""Pass 1: walk each ticker day by day and record the EOD signal tape.

For every evaluation day T this stores exactly what the engine knew at T close:
the entry score, the setup state, coverage and ATR%. Everything downstream
(barrier simulation, calibration, null tests) then runs off the tape in
milliseconds instead of recomputing indicators.
"""
import csv, json, os, sys
from multiprocessing import Pool

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
import magic_screener_v2_4 as ms

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, "data")
TAPES = os.path.join(HERE, "tapes")
os.makedirs(TAPES, exist_ok=True)

WARMUP = 260
HORIZON = 20
BENCH = "SPY"

# Unadjusted corporate action: a -51% re-basing gap on 2026-06-29 with the whole
# subsequent series at the new level. Not a tradable move; it would fabricate a
# catastrophic loss and a false trend break.
EXCLUDE = {"HON"}


def load(sym):
    return ms.load_history_csv(os.path.join(DATA, f"{sym}.csv"))


def build(sym):
    try:
        bars = load(sym)
        bench = load(BENCH)
    except Exception as exc:
        return sym, None, f"load failed: {exc}"
    if len(bars) < WARMUP + HORIZON + 5:
        return sym, None, f"only {len(bars)} bars"

    bmap = {b.date: b for b in bench}
    rows = []
    for i in range(WARMUP, len(bars) - HORIZON):
        hist = bars[:i + 1]
        bhist = [bmap[x.date] for x in hist if x.date in bmap]
        try:
            f = ms.compute_technical_features(hist, benchmark=bhist)
            e = ms.compute_entry_model(f)
        except Exception:
            continue
        rows.append({
            "i": i,
            "date": bars[i].date.isoformat(),
            "score": e["technicalEntryScore"],
            "coverage": e["technicalCoverage"],
            "setup": e["eodSetup"],
            "atrPct": f.get("atrPct"),
            "relVol20": f.get("relVol20"),
            "avgDollarVol20": f.get("avgDollarVol20"),
        })
    path = os.path.join(TAPES, f"{sym}.json")
    with open(path, "w") as fh:
        json.dump({"symbol": sym, "bars": len(bars), "warmup": WARMUP,
                   "horizon": HORIZON, "rows": rows}, fh)
    return sym, len(rows), "ok"


if __name__ == "__main__":
    syms = sorted(os.path.basename(p)[:-4] for p in os.listdir(DATA) if p.endswith(".csv"))
    syms = [s for s in syms if s not in EXCLUDE and s != BENCH]
    print(f"building tapes for {len(syms)} symbols (benchmark {BENCH}, excluded {sorted(EXCLUDE)})",
          flush=True)
    with Pool(4) as pool:
        for sym, n, msg in pool.imap_unordered(build, syms):
            print(f"  {sym:6s} {str(n):>6} signal days  {msg}", flush=True)
    print("done", flush=True)
