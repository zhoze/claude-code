#!/usr/bin/env python3
"""Consolidated verification backtest: every version of the screen, one pipeline,
held out 2024-2026. Confirms where the final screen is better (and where not)."""
import math, os, statistics, sys, random
from collections import defaultdict
SP = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SP); sys.path.insert(0, "/home/user/claude-code/value-screener")
from amend_blend import build_frames, load_outcomes, nw_t
from finetune import make_score, picks, monthly, raw_stats, sharpe
import pandas as pd

OOS = ("2024-01-01", "2099")
F = build_frames(); exc, fwd, prox = load_outcomes()
r = lambda df: df.rank(axis=1, pct=True)
align = lambda df: df.reindex(index=F["imom"].index, columns=F["imom"].columns)

imom2 = (r(F["imom"]) + r(F["ma"])) / 2                       # plain IMOM (no gc filter)
blend3 = make_score(F, (1/3, 1/3, 1/3))                       # 3-leg, gc-filtered

LADDER = [
    ("v1 IMOM 2-leg, eq-wt, 10%",      imom2,  0.10, 0.0),    # power 0 => equal weights
    ("v2 blend 3-leg, eq-wt, 10%",     blend3, 0.10, 0.0),
    ("v3 blend rank-wt, 10%",          blend3, 0.10, 1.0),
    ("v4 FINAL: blend rank-wt, 5%",    blend3, 0.05, 1.0),
]

P = {name: picks(sc, *OOS, frac, pw) for name, sc, frac, pw in LADDER}
M = {name: {hz: monthly(pk, exc, hz) for hz in (30, 60)} for name, pk in P.items()}

print("LADDER — one pipeline, held out 2024-2026")
print(f"{'version':<30}{'30d exc':>9}{'t':>6}{'raw30':>8}{'Shp':>6} |{'60d exc':>9}{'t':>6}{'raw60':>9}{'win':>6}{'Shp':>6}")
stats = {}
for name, pk in P.items():
    row = {}
    for hz, lag in ((30, 2), (60, 3)):
        m = statistics.fmean(M[name][hz].values()) * 20 / hz * 100
        _, t = nw_t(list(M[name][hz].values()), lag)
        rr, wn = raw_stats(pk, fwd, hz)
        sh, _ = sharpe(pk, fwd, hz)
        row[hz] = dict(exc=m, t=t, raw=rr, win=wn, sh=sh)
    stats[name] = row
    a, b = row[30], row[60]
    print(f"{name:<30}{a['exc']:>+8.3f}%{a['t']:>6.2f}{a['raw']:>+7.2f}%{a['sh']:>6.2f} |"
          f"{b['exc']:>+8.3f}%{b['t']:>6.2f}{b['raw']:>+8.2f}%{b['win']:>5.1f}%{b['sh']:>6.2f}")

fin = "v4 FINAL: blend rank-wt, 5%"
print("\nPAIRED: FINAL vs each predecessor (the statistic that decides 'better')")
for name in [n for n, *_ in LADDER if n != fin]:
    for hz, lag in ((30, 2), (60, 3)):
        a, b = M[fin][hz], M[name][hz]
        com = sorted(set(a) & set(b))
        dm, dt = nw_t([a[m] - b[m] for m in com], lag)
        tag = "BETTER" if dt >= 2 else ("worse" if dt <= -2 else "tied")
        print(f"  vs {name:<28}{hz}d: diff {dm*20/hz*100:+7.3f}%  t={dt:5.2f}  {tag}")

print("\nFINAL screen — full battery (60d):")
pk = P[fin]
line = "  survivorship: "
for cut in (0, 50, 75):
    mm = monthly(pk, exc, 60, prox, cut); m_, t_ = nw_t(list(mm.values()), 3)
    line += f">={cut}% {m_*20/60*100:+6.3f}%(t={t_:4.2f})  "
print(line)
yy = []
for y in ("2024", "2025", "2026"):
    sub = {d: v for d, v in pk.items() if d[:4] == y}
    mv = list(monthly(sub, exc, 60).values())
    yy.append(f"{y} {statistics.fmean(mv)*20/60*100:+6.2f}%" if len(mv) >= 2 else f"{y} n/a")
print("  by year: " + "   ".join(yy))
pool = defaultdict(list)
for (s, d, hz) in exc:
    if hz == 60 and d >= OOS[0]:
        pool[d].append(s)
rng = random.Random(21)
an = ad = 0.0
for d, items in pk.items():
    for s, w in items:
        if (s, d, 60) in exc:
            an += w * exc[(s, d, 60)]; ad += w
act = an / ad
ms = []
for _ in range(300):
    tot = cnt = 0
    for d, items in pk.items():
        avail = pool.get(d)
        if not avail: continue
        for s in rng.sample(avail, min(len(items), len(avail))):
            tot += exc[(s, d, 60)]; cnt += 1
    ms.append(tot / cnt)
ms.sort()
print(f"  random control: {act*100:+.3f}% vs {statistics.fmean(ms)*100:+.3f}%  "
      f"p={sum(1 for m in ms if m>=act)/len(ms):.4f}")
# compounded non-overlap equity, final vs v1
for name in ("v1 IMOM 2-leg, eq-wt, 10%", fin):
    days = sorted(P[name]); eq = 1.0; peak = 1.0; mdd = 0.0; nper = 0
    for d in days[::60]:
        num = den = 0.0
        for s, w in P[name][d]:
            if (s, d, 60) in fwd:
                num += w * (fwd[(s, d, 60)] - 0.0012); den += w
        if den > 0:
            eq *= 1 + num / den; peak = max(peak, eq); mdd = min(mdd, eq/peak - 1); nper += 1
    yrs = nper * 60 / 252
    print(f"  compounded 60d non-overlap [{name}]: total {(eq-1)*100:+.1f}%  "
          f"CAGR {((eq)**(1/yrs)-1)*100:+.1f}%  maxDD {mdd*100:.1f}%  ({nper} periods)")

# parity: shipped standalone picks == pipeline picks (today)
sys.path.insert(0, "/home/user/claude-code/value-screener")
import imom_screen as I
D = os.path.join(SP, "data500")
bench = {b[0]: b[4] for b in I.load_bars(f"{D}/SPY.csv")}
latest = {}
for fn in sorted(os.listdir(D)):
    sym = fn[:-4]
    if not fn.endswith(".csv") or sym == "SPY": continue
    bars = I.load_bars(f"{D}/{fn}")
    if len(bars) < I.WARMUP + 5: continue
    s = I.signals(bars, bench)
    if s and s[-1]["golden_cross"] is not None:
        latest[sym] = s[-1]
ranked = I.rank_composite(latest, I.BLEND_LEGS_BY_HOLD[60])
k = max(1, int(len(ranked) * 0.05))
ship = [s for s, _ in ranked[:k]]
last_day = max(P[fin])
pipe = [s for s, _ in P[fin][last_day]]
print(f"\nPARITY shipped-vs-backtest picks on {last_day}: "
      f"{'IDENTICAL' if ship == pipe else 'MISMATCH'} ({len(ship)} names)")
