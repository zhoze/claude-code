#!/usr/bin/env python3
"""Fine-tune the amended blend's parameters; target >= +10% vs current screen.

CURRENT screen (baseline): 3-leg equal rank composite, decile 10%, linear rank
weights. Held out 60d: +4.390%/20d excess, +18.10% raw.

Pre-registered grid (one parameter at a time from baseline, then combos of the
per-parameter winners — 15 variants max, fixed before any result is seen):

  A decile fraction   .05 / .075 / .15          (baseline .10)
  B rank-weight power p=2 / p=3                 (baseline p=1: w ~ (k-j)^p)
  C leg weights       (.5,.25,.25) (.25,.5,.25) (.25,.25,.5)
                      (.4,.4,.2) (.4,.2,.4) (.2,.4,.4)     (baseline 1/3 each)
  D combos            best-A x best-B, best-A x best-C, best-B x best-C,
                      best-A x best-B x best-C

Selection IN-SAMPLE 2020-2023 only: rank by 60d own excess; gate = IS 60d excess
>= 1.10 x baseline IS AND paired t vs baseline >= 1.5 at 60d AND 30d diff > 0.
Top 2 go to ONE out-of-sample shot (2024-2026) with the full battery.
"""
import math, os, statistics, sys
from collections import defaultdict

SP = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SP)
sys.path.insert(0, "/home/user/claude-code/value-screener")

import pandas as pd
from amend_blend import build_frames, load_outcomes, nw_t

IS_LO, IS_HI = "2020-01-01", "2024-01-01"
OOS_LO, OOS_HI = "2024-01-01", "2099"


def make_score(F, lw):
    r = lambda df: df.rank(axis=1, pct=True)
    align = lambda df: df.reindex(index=F["imom"].index, columns=F["imom"].columns)
    a, b, c = lw
    sc = a * r(F["imom"]) + b * r(F["ma"]) + c * align(r(F["gc"]))
    return sc.where(align(F["gc"].notna()))


def picks(score, lo, hi, frac, power):
    out = {}
    for ts, row in score.iterrows():
        d = ts.strftime("%Y-%m-%d")
        if not (lo <= d < hi):
            continue
        vals = row.dropna()
        if len(vals) < 50:
            continue
        k = max(1, int(len(vals) * frac))
        sel = list(vals.sort_values(ascending=False).index[:k])
        w = [float(k - j) ** power for j in range(k)]
        tot = sum(w)
        out[d] = [(s, wi / tot) for s, wi in zip(sel, w)]
    return out


def monthly(pk, exc, hz, prox=None, mp=0.0):
    per = defaultdict(list)
    for d, items in pk.items():
        num = den = 0.0
        for s, w in items:
            key = (s, d, hz)
            if key not in exc:
                continue
            if prox is not None and prox.get((s, d), 0.0) < mp:
                continue
            num += w * exc[key]; den += w
        if den > 0:
            per[d[:7]].append(num / den)
    return {m: statistics.fmean(v) for m, v in sorted(per.items())}


def paired(pk, base_m, exc, hz, lag):
    own = monthly(pk, exc, hz)
    com = sorted(set(own) & set(base_m))
    om = statistics.fmean([own[m] for m in com]) * 20 / hz * 100
    dm, dt = nw_t([own[m] - base_m[m] for m in com], lag)
    _, ot = nw_t([own[m] for m in com], lag)
    return om, ot, dm * 20 / hz * 100, dt


def raw_stats(pk, fwd, hz):
    num = den = wpos = 0.0
    for d, items in pk.items():
        for s, w in items:
            if (s, d, hz) in fwd:
                x = fwd[(s, d, hz)] - 0.0012
                num += w * x; den += w
                if x > 0:
                    wpos += w
    return num / den * 100, wpos / den * 100


def sharpe(pk, fwd, hz):
    days = sorted(pk)
    per = []
    for d in days[::hz]:
        num = den = 0.0
        for s, w in pk[d]:
            if (s, d, hz) in fwd:
                num += w * (fwd[(s, d, hz)] - 0.0012); den += w
        if den > 0:
            per.append(num / den)
    if len(per) < 3:
        return None, None
    m = statistics.fmean(per); sd = statistics.stdev(per)
    return m / sd * math.sqrt(252 / hz), len(per)


def main():
    print("building frames...", flush=True)
    F = build_frames()
    exc, fwd, prox = load_outcomes()

    BASE = dict(frac=0.10, power=1.0, lw=(1/3, 1/3, 1/3))
    grid = [("BASELINE (current)", BASE)]
    for f in (0.05, 0.075, 0.15):
        grid.append((f"A decile {f:.3f}", dict(BASE, frac=f)))
    for p in (2.0, 3.0):
        grid.append((f"B power {p:.0f}", dict(BASE, power=p)))
    LWS = [(.5, .25, .25), (.25, .5, .25), (.25, .25, .5),
           (.4, .4, .2), (.4, .2, .4), (.2, .4, .4)]
    for lw in LWS:
        grid.append((f"C legs {lw}", dict(BASE, lw=lw)))

    print("\nIN-SAMPLE 2020-2023 (baseline first; gate: 60d own >= 1.10x base, "
          "diff t>=1.5, 30d diff>0)")
    print(f"  {'variant':<26}{'60d own':>9}{'own t':>7}{'diff':>8}{'t':>6}"
          f"{'30d own':>9}{'diff':>8}{'t':>6}  gate")
    scores_cache = {}
    def get_score(lw):
        if lw not in scores_cache:
            scores_cache[lw] = make_score(F, lw)
        return scores_cache[lw]

    base_pk_is = picks(get_score(BASE["lw"]), IS_LO, IS_HI, BASE["frac"], BASE["power"])
    base_m = {hz: monthly(base_pk_is, exc, hz) for hz in (30, 60)}
    b60 = statistics.fmean(base_m[60].values()) * 20 / 60 * 100
    b30 = statistics.fmean(base_m[30].values()) * 20 / 30 * 100
    _, bt60 = nw_t(list(base_m[60].values()), 3)
    print(f"  {'BASELINE (current)':<26}{b60:>+8.3f}%{bt60:>7.2f}{'':>8}{'':>6}"
          f"{b30:>+8.3f}%{'':>8}{'':>6}  --")

    results = {}
    def run_variant(name, cfg):
        pk = picks(get_score(cfg["lw"]), IS_LO, IS_HI, cfg["frac"], cfg["power"])
        o60, t60, d60, dt60 = paired(pk, base_m[60], exc, 60, 3)
        o30, t30, d30, dt30 = paired(pk, base_m[30], exc, 30, 2)
        ok = o60 >= 1.10 * b60 and dt60 >= 1.5 and d30 > 0
        results[name] = (o60, cfg, ok)
        print(f"  {name:<26}{o60:>+8.3f}%{t60:>7.2f}{d60:>+7.3f}%{dt60:>6.2f}"
              f"{o30:>+8.3f}%{d30:>+7.3f}%{dt30:>6.2f}  {'PASS' if ok else 'fail'}")
        return o60, ok

    for name, cfg in grid[1:]:
        run_variant(name, cfg)

    # per-parameter winners (by IS 60d own excess within each family)
    def best(prefix, default):
        fam = [(v[0], n) for n, v in results.items() if n.startswith(prefix)]
        if not fam:
            return default, None
        fam.sort(reverse=True)
        return results[fam[0][1]][1], fam[0][1]

    ba, na = best("A ", BASE)
    bb, nb = best("B ", BASE)
    bc, nc = best("C ", BASE)
    print(f"\nper-parameter winners: {na} | {nb} | {nc}")
    combos = [
        ("D A*+B*", dict(BASE, frac=ba["frac"], power=bb["power"])),
        ("D A*+C*", dict(BASE, frac=ba["frac"], lw=bc["lw"])),
        ("D B*+C*", dict(BASE, power=bb["power"], lw=bc["lw"])),
        ("D A*+B*+C*", dict(frac=ba["frac"], power=bb["power"], lw=bc["lw"])),
    ]
    for name, cfg in combos:
        run_variant(name, cfg)

    finalists = sorted(((v[0], n) for n, v in results.items() if v[2]), reverse=True)[:2]
    print(f"\nfinalists (max 2): {[n for _, n in finalists] or 'NONE'}")
    if not finalists:
        print("no variant met the +10% in-sample gate — honest stop.")
        return

    print("\n" + "=" * 78)
    print("OUT-OF-SAMPLE 2024-2026 — one shot (target: >= +10% vs current screen)")
    base_pk = picks(get_score(BASE["lw"]), OOS_LO, OOS_HI, BASE["frac"], BASE["power"])
    bm = {hz: monthly(base_pk, exc, hz) for hz in (30, 60)}
    for hz in (30, 60):
        m = statistics.fmean(bm[hz].values()) * 20 / hz * 100
        _, t = nw_t(list(bm[hz].values()), 3 if hz == 60 else 2)
        rr, wn = raw_stats(base_pk, fwd, hz)
        sh, np_ = sharpe(base_pk, fwd, hz)
        print(f"  CURRENT {hz}d: excess {m:+.3f}% (t={t:.2f})  raw {rr:+.2f}%  "
              f"win {wn:.1f}%  Sharpe {sh:.2f}")
    for _, name in finalists:
        cfg = results[name][1]
        pk = picks(get_score(cfg["lw"]), OOS_LO, OOS_HI, cfg["frac"], cfg["power"])
        print(f"\n  === {name}  {cfg} ===")
        for hz, lag in ((30, 2), (60, 3)):
            o, t, d, dt = paired(pk, bm[hz], exc, hz, lag)
            rr, wn = raw_stats(pk, fwd, hz)
            sh, np_ = sharpe(pk, fwd, hz)
            bexc = statistics.fmean(bm[hz].values()) * 20 / hz * 100
            gain = (o / bexc - 1) * 100
            print(f"  {hz}d: excess {o:+.3f}% (t={t:.2f})  vs current {gain:+5.1f}%  "
                  f"diff t={dt:.2f}  raw {rr:+.2f}%  win {wn:.1f}%  Sharpe {sh:.2f}")
        line = "  survivorship 60d: "
        for cut in (0, 50, 75):
            mm = monthly(pk, exc, 60, prox, cut)
            m_, t_ = nw_t(list(mm.values()), 3)
            line += f"prox>={cut:>2}% {m_*20/60*100:+6.3f}%(t={t_:4.2f})  "
        print(line)
        yy = []
        for y in ("2024", "2025", "2026"):
            sub = {d: it for d, it in pk.items() if d[:4] == y}
            mv = list(monthly(sub, exc, 60).values())
            yy.append(f"{y} {statistics.fmean(mv)*20/60*100:+6.2f}%" if len(mv) >= 2 else f"{y} n/a")
        print("  by year 60d: " + "   ".join(yy))
        import random
        pool = defaultdict(list)
        for (s, d, hz) in exc:
            if hz == 60 and d >= OOS_LO:
                pool[d].append(s)
        rng = random.Random(13)
        act_n = act_d = 0.0
        for d, items in pk.items():
            for s, w in items:
                if (s, d, 60) in exc:
                    act_n += w * exc[(s, d, 60)]; act_d += w
        act = act_n / act_d
        ms = []
        for _ in range(300):
            tot = cnt = 0
            for d, items in pk.items():
                avail = pool.get(d)
                if not avail:
                    continue
                for s in rng.sample(avail, min(len(items), len(avail))):
                    tot += exc[(s, d, 60)]; cnt += 1
            ms.append(tot / cnt)
        ms.sort()
        print(f"  random control 60d: {act*100:+.3f}% vs {statistics.fmean(ms)*100:+.3f}%  "
              f"p={sum(1 for m in ms if m>=act)/len(ms):.4f}")


if __name__ == "__main__":
    main()
