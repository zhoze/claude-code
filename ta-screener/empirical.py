"""Walk-forward empirical validation layer: per-screen RankIC against forward returns.

For each screen's date x ticker score history (causal by contract), compute the daily
cross-sectional Spearman rank correlation with the h-day forward return that STARTS
skip_days after the score date (skip=1 honors the delay-1 trading convention: a score
formed at close t is tradable at t+1, so it is judged on the return over [t+1, t+1+h]).

There is no lookahead in the *scores* (screen contract); the forward return is
forward-looking by design — that is what an information coefficient is.

Reported per horizon:
    ic_mean   mean daily RankIC
    ic_tstat  ic_mean / (std / sqrt(n))   — a rough significance gauge, not gospel
    hit_rate  fraction of days with RankIC > 0
    n_dates   days entering the average (NaN stats if < min_dates)
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from panel import Panel


def forward_returns(panel: Panel, h: int, skip: int = 1) -> pd.DataFrame:
    """Return over [t+skip, t+skip+h] aligned to score date t."""
    ac = panel.adjclose
    return ac.shift(-(skip + h)) / ac.shift(-skip) - 1


def _rowwise_rank_ic(score: pd.DataFrame, fwd: pd.DataFrame, min_names: int) -> pd.Series:
    """Vectorized per-date Spearman: pearson of cross-sectional pct-ranks."""
    both = score.notna() & fwd.notna()
    s = score.where(both)
    f = fwd.where(both)
    rs = s.rank(axis=1, pct=True)
    rf = f.rank(axis=1, pct=True)
    n = both.sum(axis=1)

    rs_c = rs.sub(rs.mean(axis=1), axis=0)
    rf_c = rf.sub(rf.mean(axis=1), axis=0)
    cov = (rs_c * rf_c).sum(axis=1)
    denom = np.sqrt((rs_c ** 2).sum(axis=1) * (rf_c ** 2).sum(axis=1))
    ic = cov / denom.replace(0.0, np.nan)
    return ic.where(n >= min_names)


def ic_stats(score: pd.DataFrame, panel: Panel, cfg: dict) -> dict:
    """Per-horizon RankIC statistics for one screen's score history."""
    ecfg = cfg["empirical"]
    out = {}
    for h in ecfg["horizons"]:
        fwd = forward_returns(panel, h, ecfg["skip_days"])
        ic = _rowwise_rank_ic(score, fwd, ecfg["min_names"]).dropna()
        key = f"{h}d"
        if len(ic) < ecfg["min_dates"]:
            out[key] = {"ic_mean": float("nan"), "ic_tstat": float("nan"),
                        "hit_rate": float("nan"), "n_dates": int(len(ic))}
            continue
        mean = float(ic.mean())
        std = float(ic.std())
        tstat = float(mean / (std / np.sqrt(len(ic)))) if std > 0 else float("nan")
        out[key] = {"ic_mean": round(mean, 4), "ic_tstat": round(tstat, 2),
                    "hit_rate": round(float((ic > 0).mean()), 3), "n_dates": int(len(ic))}
    return out


def selftest() -> int:
    from panel import synthetic_panel, load_config
    p = synthetic_panel()
    cfg = load_config()
    ecfg = cfg["empirical"]

    # alignment: fwd at t is exactly adjclose[t+skip+h]/adjclose[t+skip] - 1
    h, skip = 5, 1
    fwd = forward_returns(p, h, skip)
    t = 100
    want = p.adjclose.iloc[t + skip + h] / p.adjclose.iloc[t + skip] - 1
    got = fwd.iloc[t]
    assert np.allclose(got, want, equal_nan=True), "forward_returns misaligned"
    assert fwd.iloc[-(skip + h):].isna().all().all(), "forward_returns must be NaN at the tail"

    # perfect foresight -> IC == 1 every date; inverted -> -1
    ic = _rowwise_rank_ic(fwd, fwd, 8).dropna()
    assert (ic > 0.999).all(), "perfect foresight must give IC 1"
    ic_inv = _rowwise_rank_ic(-fwd, fwd, 8).dropna()
    assert (ic_inv < -0.999).all(), "inverted foresight must give IC -1"

    # full stats path on a real-ish score (12-1 momentum), small-n thresholds
    cfg_small = {"empirical": {"horizons": [5, 20], "skip_days": 1,
                               "min_dates": 50, "min_names": 8}}
    mom = p.adjclose.shift(21) / p.adjclose.shift(252) - 1
    stats = ic_stats(mom, p, cfg_small)
    for k in ("5d", "20d"):
        assert k in stats and stats[k]["n_dates"] > 50, stats
        assert -1 <= stats[k]["ic_mean"] <= 1
    # insufficient dates -> NaN stats, not crash
    stats2 = ic_stats(mom.iloc[:60], p, cfg)
    assert np.isnan(stats2["5d"]["ic_mean"])

    print(f"empirical.py selftest: OK (mom 12-1 on synthetic: {stats['5d']})")
    return 0


if __name__ == "__main__":
    import sys
    if "--selftest" in sys.argv:
        sys.exit(selftest())
    print(__doc__)
