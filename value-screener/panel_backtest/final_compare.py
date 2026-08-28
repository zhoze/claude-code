#!/usr/bin/env python3
"""Final head-to-head: fine-tuned blend vs the leading ta-screener rules,
ALL under the identical construction (top 5%, linear rank weights, 30/60d holds,
held out 2024-2026, matched excess + NW t + raw net + Sharpe)."""
import math, os, statistics, sys
from collections import defaultdict
SP = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SP); sys.path.insert(0, os.path.join(SP, "ta_zip", "ta-screener"))
sys.path.insert(0, "/home/user/claude-code/value-screener")
from panel import load_config, load_panel
import catalog
import compare_screens as C
from amend_blend import build_frames, load_outcomes, nw_t
from finetune import picks, monthly, paired, raw_stats, sharpe, make_score

OOS = ("2024-01-01", "2099")
CONTENDERS = ["golden_cross", "ts_momentum", "mom_12_1", "regime_filtered_momentum",
              "mom_6_1", "ppo", "donchian_55", "alpha_042", "alpha_100",
              "vol_scaled_momentum", "obv_slope"]

cfg = load_config(); panel = load_panel()
specs = {s.key: s for s in catalog.collect()}
F = build_frames()
exc, fwd, prox = load_outcomes()

scores = {"BLEND (fine-tuned)": make_score(F, (1/3, 1/3, 1/3))}
for k in CONTENDERS:
    scores[k] = specs[k].runner(panel, cfg)

blend_pk = picks(scores["BLEND (fine-tuned)"], *OOS, 0.05, 1.0)
bm = {hz: monthly(blend_pk, exc, hz) for hz in (30, 60)}

rows = []
for name, sc in scores.items():
    pk = blend_pk if name.startswith("BLEND") else picks(sc, *OOS, 0.05, 1.0)
    rec = {"name": name}
    for hz, lag in ((30, 2), (60, 3)):
        own_m = monthly(pk, exc, hz)
        m = statistics.fmean(own_m.values()) * 20 / hz * 100
        _, t = nw_t(list(own_m.values()), lag)
        rr, wn = raw_stats(pk, fwd, hz)
        sh, _ = sharpe(pk, fwd, hz)
        com = sorted(set(own_m) & set(bm[hz]))
        dm, dt = nw_t([own_m[x] - bm[hz][x] for x in com], lag)
        rec[hz] = dict(exc=m, t=t, raw=rr, win=wn, sh=sh, d=dm * 20 / hz * 100, dt=dt)
    rows.append(rec)

rows.sort(key=lambda r: -r[60]["exc"])
print("ALL AT THE SAME CONSTRUCTION: top 5%, rank weights, held out 2024-2026")
print(f"{'screen':<26}{'60d exc':>9}{'t':>6}{'raw60':>9}{'win':>6}{'Shp':>6}"
      f"{'vs blend':>10}{'t':>6} | {'30d exc':>9}{'t':>6}{'raw30':>9}")
for r in rows:
    a, b = r[60], r[30]
    d = f"{a['d']:>+9.3f}%{a['dt']:>6.2f}" if not r["name"].startswith("BLEND") else f"{'--':>10}{'':>6}"
    print(f"{r['name']:<26}{a['exc']:>+8.3f}%{a['t']:>6.2f}{a['raw']:>+8.2f}%{a['win']:>5.1f}%"
          f"{a['sh']:>6.2f}{d} | {b['exc']:>+8.3f}%{b['t']:>6.2f}{b['raw']:>+8.2f}%")
