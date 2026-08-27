#!/usr/bin/env python3
import json, math, os, random, statistics, sys
import analyze as A
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
import magic_screener_v2_4 as ms

MIN_SCORE = 75.0
syms = sorted(os.path.basename(p)[:-5] for p in os.listdir(A.TAPES) if p.endswith(".json"))
bars = {s: A.load(s) for s in syms}
tapes = {s: A.tape(s) for s in syms}

print("=" * 78)
print(f"PANEL BACKTEST — v2.4 engine, {len(syms)} symbols, horizon {A.HORIZON}d, "
      f"score >= {MIN_SCORE}, non-overlapping")
print(f"Period: {bars[syms[0]][A.WARMUP].date} .. {bars[syms[0]][-1].date}   "
      f"costs {A.SLIP_BPS}bps slip + {A.COMM_BPS}bps commission per side")
print("=" * 78)

# ---- Fidelity check: does the tape-driven sim reproduce the engine's own loop?
probe = "KO"
eng = ms.backtest_entry_model(bars[probe], benchmark=A.load("SPY"), horizon=A.HORIZON,
                              min_score=MIN_SCORE, warmup=A.WARMUP, mode="trade",
                              barrier_mode="atr", slippage_bps=A.SLIP_BPS,
                              commission_bps=A.COMM_BPS)
sim, _ = A.simulate(bars[probe], tapes[probe], MIN_SCORE, "atr")
print(f"\n[fidelity] {probe}: engine={eng['signals']} trades, tape-sim={len(sim)} trades", end="")
if eng["signals"] == len(sim) and eng["signals"]:
    d = max(abs(a["netReturn"] - b["netReturn"]) for a, b in zip(eng["details"], sim))
    print(f", max |return diff| = {d:.2e}  -> {'MATCH' if d < 1e-9 else 'MISMATCH'}")
else:
    print("  -> MISMATCH" if eng["signals"] != len(sim) else "")

# ---- Main panel run, both barrier modes
results = {}
for mode in ("atr", "pct"):
    by_sym, uncond_all = {}, []
    for s in syms:
        t, u = A.simulate(bars[s], tapes[s], MIN_SCORE, mode)
        by_sym[s] = t
        uncond_all.extend(u)
    flat = [t for ts in by_sym.values() for t in ts]
    results[mode] = (by_sym, flat, uncond_all)

    a = A.agg(flat)
    print(f"\n{'-'*78}\nBARRIER MODE: {mode.upper()}"
          + ("  (target 3.0xATR / stop 2.0xATR)" if mode == "atr" else "  (target +5% / stop -3%)"))
    if not a:
        print("  no trades"); continue
    lo, hi = A.cluster_bootstrap_ci(by_sym)
    prof = sum(1 for s in syms if by_sym[s] and statistics.fmean(x["netReturn"] for x in by_sym[s]) > 0)
    active = sum(1 for s in syms if by_sym[s])
    base = statistics.fmean(uncond_all)
    sel = statistics.fmean(t["fixedHorizonReturn"] for t in flat)
    print(f"  Trades                : {a['trades']}  across {active}/{len(syms)} symbols")
    print(f"  Expectancy / trade    : {a['expectancy']*100:+.3f}%   median {a['median']*100:+.3f}%")
    print(f"  95% CI (cluster boot) : [{lo*100:+.3f}%, {hi*100:+.3f}%]" if lo is not None else "")
    print(f"  Win rate (net)        : {a['winRate']*100:.1f}%")
    print(f"  Target / Stop / Neither: {a['targetRate']*100:.1f}% / {a['stopRate']*100:.1f}% / {a['neitherRate']*100:.1f}%")
    print(f"  Profit factor         : {a['profitFactor']:.3f}")
    print(f"  Avg holding           : {a['avgHold']:.1f} sessions")
    print(f"  Avg MFE / MAE         : {a['avgMFE']*100:+.2f}% / {a['avgMAE']*100:+.2f}%")
    print(f"  Symbols net-profitable: {prof}/{active}")
    print(f"  Selection lift (fixed {A.HORIZON}d, signal days vs all days): "
          f"{(sel-base)*100:+.3f}%   [signal {sel*100:+.3f}% vs baseline {base*100:+.3f}%]")

# ---- Score calibration pooled across the whole panel (all signal days)
print(f"\n{'-'*78}\nSCORE CALIBRATION — pooled, every signal day, fixed {A.HORIZON}d forward return")
rows = []
for s in syms:
    b = bars[s]
    for r in tapes[s]:
        i = r["i"]
        fut = b[i + 1:i + 1 + A.HORIZON]
        if len(fut) < A.HORIZON:
            continue
        ep = b[i + 1].open
        rows.append({"bucket": ms._score_bucket(float(r["score"])),
                     "fwd": fut[-1].close / ep - 1.0, "score": r["score"]})
print(f"  {'bucket':>7} {'n':>7} {'avg fwd':>9} {'positive':>9}")
for c in A.calibration(rows):
    print(f"  {c['bucket']:>7} {c['n']:>7} {c['avgFwd']*100:>+8.3f}% {c['posRate']*100:>8.1f}%")
sc = [r["score"] for r in rows]; fw = [r["fwd"] for r in rows]
ms_, mf = statistics.fmean(sc), statistics.fmean(fw)
num = sum((x-ms_)*(y-mf) for x, y in zip(sc, fw))
den = math.sqrt(sum((x-ms_)**2 for x in sc)*sum((y-mf)**2 for y in fw))
print(f"  Pearson corr(score, forward {A.HORIZON}d return) = {num/den:+.4f}  (n={len(rows)})")

# ---- Random-timing null: same trade count per symbol, random entry dates
print(f"\n{'-'*78}\nRANDOM-TIMING NULL (atr barriers) — is the *timing* doing anything?")
by_sym, flat, _ = results["atr"]
rng = random.Random(4242)
null_means = []
for trial in range(400):
    pool = []
    for s in syms:
        k = len(by_sym[s])
        if not k:
            continue
        idxs = [r["i"] for r in tapes[s]]
        picks = rng.sample(idxs, min(k * 3, len(idxs)))
        t, _ = A.simulate(bars[s], tapes[s], 0, "atr", forced_days=picks)
        pool.extend(t[:k] if len(t) > k else t)
    if pool:
        null_means.append(statistics.fmean(x["netReturn"] for x in pool))
null_means.sort()
actual = statistics.fmean(t["netReturn"] for t in flat)
pct = 100.0 * sum(m <= actual for m in null_means) / len(null_means)
print(f"  Strategy expectancy      : {actual*100:+.3f}%")
print(f"  Random-entry null mean   : {statistics.fmean(null_means)*100:+.3f}%  "
      f"(5th..95th pct: {ms.percentile_value(null_means,0.05)*100:+.3f}% .. "
      f"{ms.percentile_value(null_means,0.95)*100:+.3f}%)")
print(f"  Strategy percentile vs null: {pct:.1f}%   -> one-sided p = {(100-pct)/100:.3f}")

# ---- Per-symbol dispersion
print(f"\n{'-'*78}\nPER-SYMBOL (atr barriers)")
print(f"  {'sym':<6}{'n':>4}{'expectancy':>12}{'win%':>7}{'stop%':>7}")
for s in sorted(syms, key=lambda x: -(statistics.fmean([t['netReturn'] for t in by_sym[x]]) if by_sym[x] else -9)):
    t = by_sym[s]
    if not t:
        print(f"  {s:<6}{0:>4}{'—':>12}"); continue
    aa = A.agg(t)
    print(f"  {s:<6}{aa['trades']:>4}{aa['expectancy']*100:>+11.2f}%{aa['winRate']*100:>6.0f}%{aa['stopRate']*100:>6.0f}%")

json.dump({"minScore": MIN_SCORE,
           "atr": {s: results['atr'][0][s] for s in syms}},
          open(os.path.join(A.HERE, "panel_results.json"), "w"), default=str)
print("\nwrote panel_results.json")
