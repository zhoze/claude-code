#!/usr/bin/env python3
"""Head-to-head: the 150 ta-screener screens vs the IMOM screen, at 30 and 60 days.

Every screen is judged on the SAME yardstick already used for IMOM:

  - rank the universe cross-sectionally each day, buy the top decile at the next
    open, hold H days;
  - score on MATCHED EXCESS return: the pick's forward return minus the mean
    forward return of every stock that day in the SAME 52-week-drawdown bucket.
    Date matching removes market direction; bucket matching removes the
    survivorship gradient;
  - significance from non-overlapping MONTHLY portfolio returns with Newey-West
    standard errors (lag = horizon in months), because overlapping H-day windows
    otherwise inflate t-stats several-fold;
  - held out on 2024-2026. ta-screener's rules were never fitted to this data, and
    IMOM's legs were selected on 2020-2023, so this window is out-of-sample for both.

Screens whose inputs are absent (the 12 earnings/PEAD screens — no earnings feed)
are reported as skipped rather than scored.
"""
import json, math, os, pickle, statistics, sys, time, traceback

TA = os.path.join(os.path.dirname(os.path.abspath(__file__)), "ta_zip", "ta-screener")
sys.path.insert(0, TA)
os.chdir(TA)

import numpy as np
import pandas as pd
from panel import MissingInputError, load_config, load_panel
import catalog

HERE = os.path.dirname(os.path.abspath(__file__))
OOS_START = "2024-01-01"
DECILE = 0.10
HORIZONS = (30, 60)


def load_outcomes():
    """(sym, date) -> matched excess and raw forward return, from the IMOM panel."""
    with open(os.path.join(HERE, "panelH.pkl"), "rb") as fh:
        rows = pickle.load(fh)
    out = {}
    for r in rows:
        rec = {}
        ok = True
        for hz in HORIZONS:
            e = r.get(f"exc{hz}")
            if e is None:
                ok = False
                break
            rec[f"exc{hz}"] = e
            rec[f"fwd{hz}"] = r[f"fwd{hz}"]
        if ok:
            out[(r["sym"], r["date"])] = rec
    return out


def nw_t(monthly, lag):
    """Newey-West t on a monthly series."""
    n = len(monthly)
    if n < lag + 3:
        return None, None, n
    m = statistics.fmean(monthly)
    e = [x - m for x in monthly]
    var = sum(x * x for x in e) / n
    for l in range(1, lag + 1):
        var += 2 * (1 - l / (lag + 1)) * sum(e[i] * e[i - l] for i in range(l, n)) / n
    se = math.sqrt(max(var, 1e-18) / n)
    return m, m / se, n


def evaluate(score_df, outcomes, decile=DECILE):
    """Top-decile daily selection -> monthly matched-excess series per horizon."""
    per_month = {hz: {} for hz in HORIZONS}
    raw = {hz: [] for hz in HORIZONS}
    n_picks = 0
    for ts, row in score_df.iterrows():
        date = ts.strftime("%Y-%m-%d")
        if date < OOS_START:
            continue
        vals = row.dropna()
        if len(vals) < 50:
            continue
        k = max(1, int(len(vals) * decile))
        top = vals.nlargest(k).index
        day = {hz: [] for hz in HORIZONS}
        for sym in top:
            rec = outcomes.get((sym, date))
            if not rec:
                continue
            for hz in HORIZONS:
                day[hz].append(rec[f"exc{hz}"])
                raw[hz].append(rec[f"fwd{hz}"])
        for hz in HORIZONS:
            if day[hz]:
                per_month[hz].setdefault(date[:7], []).append(statistics.fmean(day[hz]))
        n_picks += len(top)
    res = {}
    for hz in HORIZONS:
        series = [statistics.fmean(v) for _, v in sorted(per_month[hz].items())]
        lag = max(1, round(hz / 21))
        m, t, months = nw_t(series, lag)
        if m is None:
            res[hz] = None
            continue
        rr = raw[hz]
        res[hz] = {
            "excess": m * 100,
            "per20": m * 20 / hz * 100,
            "t": t,
            "months": months,
            "raw": statistics.fmean(rr) * 100 - 0.12,   # 12bps round trip
            "win": sum(1 for x in rr if x > 0) / len(rr) * 100,
            "n": len(rr),
        }
    return res


def imom_scores(panel):
    """The IMOM screen expressed on this panel: rank(idio 12-1) + rank(vs 200dMA)."""
    close = panel.adjclose
    ret = close.pct_change()
    spy = panel.benchmarks["SPY"].reindex(close.index)
    mret = spy.pct_change().fillna(0.0)
    # rolling 252d beta of each name on SPY, then residual returns
    W = 252
    m = mret.to_numpy()
    cov = ret.mul(mret, axis=0).rolling(W, min_periods=W).mean() \
          - ret.rolling(W, min_periods=W).mean().mul(
              pd.Series(m, index=ret.index).rolling(W, min_periods=W).mean(), axis=0)
    mvar = pd.Series(m, index=ret.index).rolling(W, min_periods=W).var(ddof=0)
    beta = cov.div(mvar, axis=0)
    resid = ret.sub(beta.mul(mret, axis=0))
    cres = resid.fillna(0.0).cumsum()
    imom252_21 = cres.shift(21) - cres.shift(252)
    ma200 = close.rolling(200, min_periods=200).mean()
    trend = close / ma200 - 1.0
    valid = beta.notna() & ma200.notna()
    r1 = imom252_21.where(valid).rank(axis=1, pct=True)
    r2 = trend.where(valid).rank(axis=1, pct=True)
    return (r1 + r2) / 2.0


if __name__ == "__main__":
    print("loading panel + outcomes...", flush=True)
    cfg = load_config()
    panel = load_panel()
    outcomes = load_outcomes()
    print(f"  panel {panel.close.shape[1]} tickers x {panel.close.shape[0]} days; "
          f"{len(outcomes)} scored (sym,date) outcomes", flush=True)

    results, skipped, errors = {}, [], []

    imom = imom_scores(panel)
    results["IMOM (this screen)"] = {"family": "imom", "res": evaluate(imom, outcomes),
                                     "title": "rank(idio 12-1) + rank(vs 200dMA)"}
    print(f"  IMOM: {results['IMOM (this screen)']['res'][60]}", flush=True)

    specs = catalog.collect()
    t0 = time.time()
    for i, spec in enumerate(specs, 1):
        try:
            score = spec.runner(panel, cfg)
        except MissingInputError as e:
            skipped.append((spec.key, str(e)[:60]))
            continue
        except Exception:
            errors.append((spec.key, traceback.format_exc(limit=2)))
            continue
        try:
            res = evaluate(score, outcomes)
        except Exception:
            errors.append((spec.key, traceback.format_exc(limit=2)))
            continue
        if res.get(60) is None:
            skipped.append((spec.key, "insufficient coverage"))
            continue
        results[spec.key] = {"family": spec.family, "res": res, "title": spec.title}
        if i % 20 == 0:
            print(f"  {i}/{len(specs)}  ({time.time()-t0:.0f}s)", flush=True)

    with open(os.path.join(HERE, "compare_results.json"), "w") as fh:
        json.dump({"results": results, "skipped": skipped,
                   "errors": [(k, v[-300:]) for k, v in errors]}, fh, indent=1)
    print(f"\nscored {len(results)}, skipped {len(skipped)}, errors {len(errors)} "
          f"in {time.time()-t0:.0f}s", flush=True)
