#!/usr/bin/env python3
"""Stress-test the leading screens: survivorship, year stability, overlap, turnover."""
import json, math, os, pickle, statistics, sys

TA = os.path.join(os.path.dirname(os.path.abspath(__file__)), "ta_zip", "ta-screener")
sys.path.insert(0, TA)
HERE = os.path.dirname(os.path.abspath(__file__))
os.chdir(TA)

import pandas as pd
from panel import load_config, load_panel
import catalog
import compare_screens as C

OOS = "2024-01-01"
CANDIDATES = ["golden_cross", "ts_momentum", "mom_12_1", "regime_filtered_momentum",
              "mom_6_1", "ppo", "donchian_55", "alpha_042", "alpha_100",
              "vol_scaled_momentum", "obv_slope"]


def load_prox():
    """(sym,date) -> proximity to 52-week high, for the survivorship stress test."""
    with open(os.path.join(HERE, "panelH.pkl"), "rb") as fh:
        rows = pickle.load(fh)
    return {(r["sym"], r["date"]): r["prox52w"] for r in rows}


def picks_by_date(score_df, decile=0.10):
    out = {}
    for ts, row in score_df.iterrows():
        date = ts.strftime("%Y-%m-%d")
        if date < OOS:
            continue
        vals = row.dropna()
        if len(vals) < 50:
            continue
        k = max(1, int(len(vals) * decile))
        out[date] = list(vals.nlargest(k).index)
    return out


def monthly_excess(picks, outcomes, hz, prox=None, min_prox=0.0):
    per = {}
    for date, syms in picks.items():
        vals = []
        for s in syms:
            rec = outcomes.get((s, date))
            if not rec:
                continue
            if prox is not None and prox.get((s, date), 0) < min_prox:
                continue
            vals.append(rec[f"exc{hz}"])
        if vals:
            per.setdefault(date[:7], []).append(statistics.fmean(vals))
    return [statistics.fmean(v) for _, v in sorted(per.items())]


def turnover(picks):
    dates = sorted(picks)
    ch = []
    for a, b in zip(dates, dates[1:]):
        pa, pb = set(picks[a]), set(picks[b])
        if pa:
            ch.append(1 - len(pa & pb) / len(pa))
    return statistics.fmean(ch) * 100 if ch else float("nan")


if __name__ == "__main__":
    cfg = load_config(); panel = load_panel()
    outcomes = C.load_outcomes(); prox = load_prox()
    specs = {s.key: s for s in catalog.collect()}

    scores = {"IMOM": C.imom_scores(panel)}
    for k in CANDIDATES:
        scores[k] = specs[k].runner(panel, cfg)
    picks = {k: picks_by_date(v) for k, v in scores.items()}

    print("SURVIVORSHIP STRESS TEST (60-day matched excess per 20 days)")
    print("The test that killed the earlier Reversal-5 model: exclude beaten-down names.\n")
    print(f"  {'screen':<26}{'all':>9}{'>=50%':>9}{'>=75%':>9}{'decay':>8}{'t(all)':>8}")
    surv = {}
    for k, pk in picks.items():
        row = []
        for cut in (0, 50, 75):
            s = monthly_excess(pk, outcomes, 60, prox, cut)
            m, t, _ = C.nw_t(s, 3)
            row.append((m * 20 / 60 * 100, t))
        decay = (row[2][0] - row[0][0]) / abs(row[0][0]) * 100 if row[0][0] else float("nan")
        surv[k] = row
        print(f"  {k:<26}{row[0][0]:>+8.3f}%{row[1][0]:>+8.3f}%{row[2][0]:>+8.3f}%"
              f"{decay:>+7.1f}%{row[0][1]:>8.2f}")

    print("\nBY YEAR (60-day matched excess per 20 days)")
    print(f"  {'screen':<26}{'2024':>9}{'2025':>9}{'2026':>9}{'worst':>9}")
    for k, pk in picks.items():
        yr = []
        for y in ("2024", "2025", "2026"):
            sub = {d: v for d, v in pk.items() if d[:4] == y}
            s = monthly_excess(sub, outcomes, 60)
            yr.append(statistics.fmean(s) * 20 / 60 * 100 if len(s) >= 3 else float("nan"))
        print(f"  {k:<26}" + "".join(f"{x:>+8.2f}%" for x in yr) + f"{min(yr):>+8.2f}%")

    print("\nTURNOVER (share of the decile replaced day over day) and BREADTH")
    print(f"  {'screen':<26}{'turnover':>10}{'names':>8}{'avg picks':>11}")
    for k, pk in picks.items():
        allnames = set()
        for v in pk.values():
            allnames |= set(v)
        print(f"  {k:<26}{turnover(pk):>9.2f}%{len(allnames):>8}"
              f"{statistics.fmean(len(v) for v in pk.values()):>11.0f}")

    print("\nOVERLAP with IMOM's decile (mean share of the same names on the same day)")
    base = picks["IMOM"]
    for k, pk in picks.items():
        if k == "IMOM":
            continue
        ov = [len(set(base[d]) & set(pk[d])) / len(base[d]) for d in base if d in pk and base[d]]
        print(f"  {k:<26}{statistics.fmean(ov) * 100:>7.1f}%")
