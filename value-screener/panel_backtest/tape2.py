#!/usr/bin/env python3
"""Rich signal tape: every technical feature at each evaluation day, plus the
forward 5-day outcome. Lets us search which inputs actually predict a short-horizon
rise, instead of only re-tuning the composite score."""
import csv, os, sys
from multiprocessing import Pool

sys.path.insert(0, "/home/user/claude-code/value-screener")
import magic_screener_v2_4 as ms

HERE = os.path.dirname(os.path.abspath(__file__))
DATA, OUT = os.path.join(HERE, "data"), os.path.join(HERE, "tapes5")
os.makedirs(OUT, exist_ok=True)
WARMUP, HMAX, BENCH = 260, 5, "SPY"
EXCLUDE = {"HON"}

FEATURES = [
    "price", "sma20", "sma50", "sma100", "sma200", "ema20",
    "sma20Slope10d", "sma50Slope10d", "sma200Slope20d", "rsi14", "atrPct",
    "realizedVol20AnnPct", "downsideDeviation60AnnPct", "gapRisk95_60Pct",
    "maxDrawdown252Pct", "relVol20", "volumeZ20", "avgDollarVol20",
    "perf5", "perf20", "perf60", "perf120", "perf252",
    "breakout20Pct", "breakout55Pct", "breakout252Pct", "proximity52wPct",
    "macdHist", "macdHistAccel", "bollingerBandwidthPct",
    "bollingerBandwidthPercentile", "vwap20",
    "rs20", "rs60", "rs120", "rs252", "rsComposite",
]
COMPONENTS = ["trend", "breakout", "momentum", "relativeStrength", "volume",
              "vwap", "volatilitySetup", "oscillator"]


def build(sym):
    try:
        bars = ms.load_history_csv(os.path.join(DATA, f"{sym}.csv"))
        bench = ms.load_history_csv(os.path.join(DATA, f"{BENCH}.csv"))
    except Exception as exc:
        return sym, 0, f"load failed: {exc}"
    bmap = {b.date: b for b in bench}
    cols = (["date", "i", "score", "coverage", "setup"] + FEATURES + COMPONENTS
            + ["aboveVwap20", "weeklyBull", "breakout20", "breakout55", "breakout252",
               "fwd5", "maxRise5", "maxFall5", "entryOpen"])
    rows = []
    for i in range(WARMUP, len(bars) - HMAX):
        hist = bars[:i + 1]
        bhist = [bmap[x.date] for x in hist if x.date in bmap]
        try:
            f = ms.compute_technical_features(hist, benchmark=bhist)
            e = ms.compute_entry_model(f)
        except Exception:
            continue
        fut = bars[i + 1:i + 1 + HMAX]
        if len(fut) < HMAX:
            continue
        ep = bars[i + 1].open
        rec = {"date": bars[i].date.isoformat(), "i": i,
               "score": e["technicalEntryScore"], "coverage": e["technicalCoverage"],
               "setup": e["eodSetup"]}
        for k in FEATURES:
            rec[k] = f.get(k)
        for k in COMPONENTS:
            rec[k] = e["technicalComponents"].get(k)
        rec["aboveVwap20"] = 1 if f.get("aboveVwap20") else 0
        rec["weeklyBull"] = 1 if f.get("weeklyTrend") == "bull" else 0
        for k in ("breakout20", "breakout55", "breakout252"):
            rec[k] = 1 if f.get(k) else 0
        rec["entryOpen"] = ep
        rec["fwd5"] = fut[-1].close / ep - 1.0
        rec["maxRise5"] = max(x.high for x in fut) / ep - 1.0
        rec["maxFall5"] = min(x.low for x in fut) / ep - 1.0
        rows.append(rec)
    with open(os.path.join(OUT, f"{sym}.csv"), "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=cols)
        w.writeheader()
        w.writerows(rows)
    return sym, len(rows), "ok"


if __name__ == "__main__":
    syms = sorted(p[:-4] for p in os.listdir(DATA) if p.endswith(".csv"))
    syms = [s for s in syms if s not in EXCLUDE and s != BENCH]
    print(f"rich tape for {len(syms)} symbols", flush=True)
    with Pool(4) as pool:
        for sym, n, msg in pool.imap_unordered(build, syms):
            print(f"  {sym:6s} {n:6d} rows  {msg}", flush=True)
    print("done", flush=True)
