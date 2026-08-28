#!/usr/bin/env python3
"""Amend-blend experiment: can the 3-leg blend be beaten on BOTH profit and t-stat
at 30-60 days?

Pre-registered candidates (fixed before looking at any result; ranked in-sample
2020-2023, at most 2 carried to ONE out-of-sample shot on 2024-2026):

  a  + obv_slope 4th leg          arXiv:2310.09903 (OBV a top-validated feature)
  b  sector-capped decile (25%)   industry-neutral momentum literature
  c  inverse-vol weights (1/s60)  arXiv:2212.07288 / 1904.04912 vol scaling
  d  a + b combined
  e  rank-weighted decile

  (f, a strategy-vol gate a la Barroso-Santa-Clara, is skipped: the held-out
   window contains no momentum crash, so the mechanism it exists for is
   untestable here; including it would only spend a selection slot.)

Selection gate (IS): paired monthly diff vs BASE > 0 with NW t >= 1.5 at BOTH
30d and 60d. Adoption gate (OOS): paired NW t >= 2 at 60d, positive at 30d, own
excess AND own t >= BASE's, survivorship flat, no negative year, random control
p < 0.01.
"""
import json, math, os, pickle, statistics, sys
from collections import defaultdict

SP = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SP)
sys.path.insert(0, "/home/user/claude-code/value-screener")

import pandas as pd
import imom_screen as I

DATA = os.path.join(SP, "data500")
ETF = {"SPY", "XLK", "XLF", "XLE", "XLV", "XLI", "XLP", "XLY", "XLB", "XLRE", "XLU", "XLC"}
IS_LO, IS_HI = "2020-01-01", "2024-01-01"
OOS_LO, OOS_HI = "2024-01-01", "2099"
DECILE = 0.10
SECTOR_CAP = 0.25
OBV_D = 20


# ---------------------------------------------------------------- extra legs

def obv_slope_own(closes, vols):
    """ta-screener's obv_slope over each ticker's OWN bars.

    step  = sign(dClose) * volume            (first bar: 0)
    obv   = cumsum(step)
    slope = sample_cov(obv, t; 20) / ((20^2 - 1) / 12)     [their exact formula:
            pandas rolling cov is sample (d-1); the divisor is the POPULATION
            variance of t -- a constant scale, irrelevant to ranks]
    score = slope / adv20,  adv20 = 20d mean of close*volume
    """
    n = len(closes)
    obv = [0.0] * n
    for i in range(1, n):
        d = closes[i] - closes[i - 1]
        step = vols[i] if d > 0 else (-vols[i] if d < 0 else 0.0)
        obv[i] = obv[i - 1] + step
    out = [None] * n
    d = OBV_D
    denom_t = (d * d - 1) / 12.0
    s1 = st = 0.0            # sum(obv), sum(j*obv) with j the global index
    dv_run = 0.0
    for i in range(n):
        s1 += obv[i]; st += i * obv[i]
        dv_run += closes[i] * vols[i]
        if i >= d:
            s1 -= obv[i - d]; st -= (i - d) * obv[i - d]
            dv_run -= closes[i - d] * vols[i - d]
        if i >= d - 1:
            tbar = i - (d - 1) / 2.0
            cov = (st - s1 * tbar) / (d - 1)          # sample covariance
            adv = dv_run / d
            if adv > 0:
                out[i] = (cov / denom_t) / adv
    return out


def vol60_own(closes):
    """Annualized 60d sample stdev of simple daily returns, %."""
    n = len(closes)
    ret = [0.0] + [closes[i] / closes[i - 1] - 1.0 for i in range(1, n)]
    out = [None] * n
    w = 60
    s = s2 = 0.0
    for i in range(n):
        x = ret[i]
        s += x; s2 += x * x
        if i >= w:
            y = ret[i - w]; s -= y; s2 -= y * y
        if i >= w:                                    # full window of returns
            mu = s / w
            var = max(0.0, (s2 - w * mu * mu) / (w - 1))
            out[i] = math.sqrt(var * 252.0) * 100.0
    return out


# ---------------------------------------------------------------- build frames

def build_frames():
    bench = {b[0]: b[4] for b in I.load_bars(os.path.join(DATA, "SPY.csv"))}
    legs = defaultdict(dict)      # leg -> {sym: {date: value}}
    for fn in sorted(os.listdir(DATA)):
        sym = fn[:-4]
        if not fn.endswith(".csv") or sym in ETF:
            continue
        bars = I.load_bars(os.path.join(DATA, fn))
        if len(bars) < I.WARMUP + 5:
            continue
        sig = I.signals(bars, bench)
        if not sig:
            continue
        closes = [b[4] for b in bars]
        vols = [b[5] for b in bars]
        obv = obv_slope_own(closes, vols)
        v60 = vol60_own(closes)
        idx = {b[0]: k for k, b in enumerate(bars)}
        for r in sig:
            i = idx[r["date"]]
            legs["imom"][sym] = legs["imom"].get(sym, {}); legs["imom"][sym][r["date"]] = r["imom252_21"]
            legs["ma"][sym] = legs["ma"].get(sym, {}); legs["ma"][sym][r["date"]] = r["ma_1_200"]
            if r["golden_cross"] is not None:
                legs["gc"][sym] = legs["gc"].get(sym, {}); legs["gc"][sym][r["date"]] = r["golden_cross"]
            if obv[i] is not None:
                legs["obv"][sym] = legs["obv"].get(sym, {}); legs["obv"][sym][r["date"]] = obv[i]
            if v60[i] is not None:
                legs["vol"][sym] = legs["vol"].get(sym, {}); legs["vol"][sym][r["date"]] = v60[i]
    frames = {}
    for leg, d in legs.items():
        df = pd.DataFrame(d)
        df.index = pd.to_datetime(df.index)
        frames[leg] = df.sort_index()
    return frames


# ---------------------------------------------------------------- evaluation

def load_outcomes():
    rows = pickle.load(open(os.path.join(SP, "panelH.pkl"), "rb"))
    exc, fwd, prox = {}, {}, {}
    for r in rows:
        ok = True
        for hz in (30, 60):
            if r.get(f"exc{hz}") is None:
                ok = False
        if not ok:
            continue
        for hz in (30, 60):
            exc[(r["sym"], r["date"], hz)] = r[f"exc{hz}"]
            fwd[(r["sym"], r["date"], hz)] = r[f"fwd{hz}"]
        prox[(r["sym"], r["date"])] = r["prox52w"]
    return exc, fwd, prox


def nw_t(series, lag):
    n = len(series)
    if n < lag + 3:
        return None, None
    m = statistics.fmean(series)
    e = [x - m for x in series]
    var = sum(x * x for x in e) / n
    for l in range(1, lag + 1):
        var += 2 * (1 - l / (lag + 1)) * sum(e[i] * e[i - l] for i in range(l, n)) / n
    return m, m / math.sqrt(max(var, 1e-18) / n)


def daily_picks(score, lo, hi, weight_mode="equal", sector_cap=None,
                vol_frame=None, sectors=None):
    """{date: [(sym, weight)]} for the top decile under the given construction."""
    out = {}
    for ts, row in score.iterrows():
        d = ts.strftime("%Y-%m-%d")
        if not (lo <= d < hi):
            continue
        vals = row.dropna()
        if len(vals) < 50:
            continue
        k = max(1, int(len(vals) * DECILE))
        ordered = vals.sort_values(ascending=False).index
        if sector_cap is not None:
            cap_n = math.ceil(sector_cap * k)
            counts = defaultdict(int)
            picks = []
            for s in ordered:
                sec = sectors.get(s, "?")
                if counts[sec] >= cap_n:
                    continue
                counts[sec] += 1
                picks.append(s)
                if len(picks) == k:
                    break
        else:
            picks = list(ordered[:k])
        if weight_mode == "equal":
            w = [1.0] * len(picks)
        elif weight_mode == "invvol":
            w = []
            for s in picks:
                v = vol_frame.at[ts, s] if s in vol_frame.columns else None
                w.append(1.0 / v if v and v > 0 else 0.0)
        elif weight_mode == "rank":
            w = [float(len(picks) - j) for j in range(len(picks))]
        tot = sum(w)
        if tot <= 0:
            continue
        out[d] = [(s, wi / tot) for s, wi in zip(picks, w)]
    return out


def monthly_series(picks, exc, hz, prox=None, min_prox=0.0):
    per = defaultdict(list)
    for d, items in picks.items():
        num = den = 0.0
        for s, w in items:
            key = (s, d, hz)
            if key not in exc:
                continue
            if prox is not None and prox.get((s, d), 0.0) < min_prox:
                continue
            num += w * exc[key]; den += w
        if den > 0:
            per[d[:7]].append(num / den)
    return {m: statistics.fmean(v) for m, v in sorted(per.items())}


def stats_vs_base(picks, base_monthly, exc, hz, lag):
    own = monthly_series(picks, exc, hz)
    com = sorted(set(own) & set(base_monthly))
    om = statistics.fmean([own[m] for m in com]) * 20 / hz * 100
    bm = statistics.fmean([base_monthly[m] for m in com]) * 20 / hz * 100
    dm, dt = nw_t([own[m] - base_monthly[m] for m in com], lag)
    _, ot = nw_t([own[m] for m in com], lag)
    return {"own": om, "own_t": ot, "base": bm, "diff": dm * 20 / hz * 100, "diff_t": dt,
            "months": len(com)}


def main():
    print("building frames from the shipped implementation...", flush=True)
    F = build_frames()
    exc, fwd, prox = load_outcomes()
    meta = json.load(open(os.path.join(SP, "nasdaq_meta.json")))
    sectors = {r["symbol"]: (r.get("sector") or "?") for r in meta}
    r = lambda df: df.rank(axis=1, pct=True)
    gcmask = F["gc"].notna()
    align = lambda df: df.reindex(index=F["imom"].index, columns=F["imom"].columns)
    ri, rm = r(F["imom"]), r(F["ma"])
    rg = r(F["gc"])
    ro = r(F["obv"])
    base_score = ((ri + align(rg) + rm) / 3).where(align(gcmask))
    a_score = ((ri + rm + align(rg) + align(ro)) / 4).where(align(gcmask))
    volf = align(F["vol"])

    VARIANTS = {
        "BASE (current blend)": dict(score=base_score, weight_mode="equal", cap=None),
        "a: + obv_slope leg":   dict(score=a_score, weight_mode="equal", cap=None),
        "b: sector cap 25%":    dict(score=base_score, weight_mode="equal", cap=SECTOR_CAP),
        "c: inverse-vol wts":   dict(score=base_score, weight_mode="invvol", cap=None),
        "d: obv + sector cap":  dict(score=a_score, weight_mode="equal", cap=SECTOR_CAP),
        "e: rank weights":      dict(score=base_score, weight_mode="rank", cap=None),
    }

    print("\nIN-SAMPLE 2020-2023 — paired vs BASE (gate: diff>0 and t>=1.5 at BOTH horizons)")
    print(f"  {'variant':<24}{'30d own':>9}{'diff':>8}{'t':>6}{'60d own':>9}{'diff':>8}{'t':>6}  gate")
    is_picks = {}
    base_is = None
    survivors = []
    for name, v in VARIANTS.items():
        pk = daily_picks(v["score"], IS_LO, IS_HI, v["weight_mode"], v["cap"], volf, sectors)
        is_picks[name] = pk
        if name.startswith("BASE"):
            base_is = {hz: monthly_series(pk, exc, hz) for hz in (30, 60)}
            m30, t30 = nw_t(list(base_is[30].values()), 2)
            m60, t60 = nw_t(list(base_is[60].values()), 3)
            print(f"  {name:<24}{m30*20/30*100:>+8.3f}%{'':>8}{t30:>6.2f}"
                  f"{m60*20/60*100:>+8.3f}%{'':>8}{t60:>6.2f}  --")
            continue
        s30 = stats_vs_base(pk, base_is[30], exc, 30, 2)
        s60 = stats_vs_base(pk, base_is[60], exc, 60, 3)
        ok = s30["diff"] > 0 and s30["diff_t"] >= 1.5 and s60["diff"] > 0 and s60["diff_t"] >= 1.5
        if ok:
            survivors.append((s60["diff"], name))
        print(f"  {name:<24}{s30['own']:>+8.3f}%{s30['diff']:>+7.3f}%{s30['diff_t']:>6.2f}"
              f"{s60['own']:>+8.3f}%{s60['diff']:>+7.3f}%{s60['diff_t']:>6.2f}  {'PASS' if ok else 'fail'}")

    survivors.sort(reverse=True)
    finalists = [n for _, n in survivors[:2]]
    print(f"\nfinalists (max 2, by 60d IS diff): {finalists or 'NONE — negative result'}")
    if not finalists:
        return

    print("\nOUT-OF-SAMPLE 2024-2026 — one shot")
    base_pk = daily_picks(base_score, OOS_LO, OOS_HI, "equal", None, volf, sectors)
    base_oos = {hz: monthly_series(base_pk, exc, hz) for hz in (30, 60)}
    bm30, bt30 = nw_t(list(base_oos[30].values()), 2)
    bm60, bt60 = nw_t(list(base_oos[60].values()), 3)
    print(f"  BASE: 30d {bm30*20/30*100:+.3f}% (t={bt30:.2f})   60d {bm60*20/60*100:+.3f}% (t={bt60:.2f})")
    for name in finalists:
        v = VARIANTS[name]
        pk = daily_picks(v["score"], OOS_LO, OOS_HI, v["weight_mode"], v["cap"], volf, sectors)
        s30 = stats_vs_base(pk, base_oos[30], exc, 30, 2)
        s60 = stats_vs_base(pk, base_oos[60], exc, 60, 3)
        print(f"\n  === {name} ===")
        print(f"  30d own {s30['own']:+.3f}% (t={s30['own_t']:.2f})   diff {s30['diff']:+.3f}% (t={s30['diff_t']:.2f})")
        print(f"  60d own {s60['own']:+.3f}% (t={s60['own_t']:.2f})   diff {s60['diff']:+.3f}% (t={s60['diff_t']:.2f})")
        for hz, lag in ((60, 3),):
            line = "  survivorship 60d: "
            for cut in (0, 50, 75):
                m = monthly_series(pk, exc, hz, prox, cut)
                mm, tt = nw_t(list(m.values()), lag)
                line += f"prox>={cut:>2}% {mm*20/hz*100:+6.3f}%(t={tt:4.2f})  "
            print(line)
        for hz in (30, 60):
            yy = []
            for y in ("2024", "2025", "2026"):
                sub = {d: it for d, it in pk.items() if d[:4] == y}
                m = list(monthly_series(sub, exc, hz).values())
                yy.append(f"{y} {statistics.fmean(m)*20/hz*100:+6.2f}%" if len(m) >= 2 else f"{y}   n/a")
            print(f"  by year {hz}d: " + "   ".join(yy))
        # random control (equal-weight, matched count per day)
        import random
        pool = defaultdict(list)
        for (s, d, hz) in exc:
            if hz == 60 and d >= OOS_LO:
                pool[d].append(s)
        rng = random.Random(11)
        act_num = act_den = 0.0
        for d, items in pk.items():
            for s, w in items:
                if (s, d, 60) in exc:
                    act_num += w * exc[(s, d, 60)]; act_den += w
        act = act_num / act_den
        ms = []
        for _ in range(400):
            tot = cnt = 0
            for d, items in pk.items():
                avail = pool.get(d)
                if not avail:
                    continue
                k = min(len(items), len(avail))
                for s in rng.sample(avail, k):
                    tot += exc[(s, d, 60)]; cnt += 1
            ms.append(tot / cnt)
        ms.sort()
        p = sum(1 for m in ms if m >= act) / len(ms)
        print(f"  random control 60d: variant {act*100:+.3f}%  random {statistics.fmean(ms)*100:+.3f}%  p={p:.4f}")
        # Sharpe, non-overlapping rebalances, raw net
        days = sorted(pk)
        for hz in (30, 60):
            per = []
            for d in days[::hz]:
                num = den = 0.0
                for s, w in pk[d]:
                    if (s, d, hz) in fwd:
                        num += w * (fwd[(s, d, hz)] - 0.0012); den += w
                if den > 0:
                    per.append(num / den)
            if len(per) >= 3:
                m = statistics.fmean(per); sd = statistics.stdev(per)
                print(f"  Sharpe {hz}d (non-overlap, {len(per)} periods): "
                      f"{m/sd*math.sqrt(252/hz):.2f}   mean {m*100:+.2f}%/period")


if __name__ == "__main__":
    main()
