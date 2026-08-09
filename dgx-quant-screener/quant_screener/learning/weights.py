"""Adaptive screen weights + champion/challenger governance (spec §33-§35, §47).

Screen weights move ONLY on persistent out-of-sample evidence:
- per-screen forward performance is measured from scored outcomes (screen passed
  vs failed at prediction time -> subsequent benchmark-relative return),
- an exponentially-weighted IC with a long half-life prevents one hot period
  from dominating,
- a challenger weight set replaces the champion only when its walk-forward
  advantage clears a t-stat threshold; every change is versioned in the
  model_changelog.
"""

from __future__ import annotations

import datetime as dt
import json
import logging
import math

import numpy as np
import pandas as pd
from scipy import stats

from .. import MODEL_VERSION
from ..data.store import Store

log = logging.getLogger(__name__)


def update_screen_performance(store: Store, as_of: dt.date, horizon: int = 5) -> pd.DataFrame:
    """Recompute per-screen forward stats from the prediction/outcome history."""
    hist = store.prediction_history()
    hist = hist[(hist["horizon_days"] == horizon) & hist["ret"].notna()]
    if hist.empty:
        return pd.DataFrame()
    rows = []
    for _, row in hist.iterrows():
        try:
            payload = json.loads(row["payload"])
        except Exception:
            continue
        passes = payload.get("SCREEN_PASSES", {})
        pcts = payload.get("SCREEN_PERCENTILES", {})
        for screen, passed in passes.items():
            rows.append({"screen": screen, "passed": bool(passed),
                         "pct": pcts.get(screen), "ret": row["ret"],
                         "rel": row["bench_rel_ret"]})
    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame(rows)
    out = []
    for screen, g in df.groupby("screen"):
        rel = g["rel"].dropna()
        ic = np.nan
        gp = g.dropna(subset=["pct", "ret"])
        if len(gp) >= 10 and gp["pct"].nunique() > 3:
            ic = float(stats.spearmanr(gp["pct"], gp["ret"]).statistic)
        m = {
            "oos_return": float(g["ret"].mean()),
            "alpha": float(rel.mean()) if len(rel) else np.nan,
            "sharpe": float(g["ret"].mean() / (g["ret"].std() + 1e-12)),
            "sortino": np.nan, "cvar": float(g["ret"].quantile(0.05)),
            "drawdown": np.nan, "hit_rate": float((g["ret"] > 0).mean()),
            "information_coefficient": ic, "n_obs": int(len(g)),
        }
        store.save_screen_performance(as_of, "fundamental", screen, horizon, m)
        out.append({"screen": screen, **m})
    return pd.DataFrame(out)


def propose_weights(store: Store, base_weights: dict[str, float], cfg) -> dict[str, float]:
    """Challenger weights from EW-decayed per-screen IC/alpha. Bounded shrinkage:
    a screen can at most double or halve its base weight."""
    perf = store.screen_performance("fundamental")
    if perf.empty:
        return dict(base_weights)
    hl = cfg.learning.weight_update_half_life_days
    out = {}
    today = pd.Timestamp.today()
    for name, base in base_weights.items():
        g = perf[perf["screen_name"] == name].copy()
        if g.empty or g["n_obs"].sum() < 30:
            out[name] = base
            continue
        age = (today - pd.to_datetime(g["run_date"])).dt.days.clip(lower=0)
        decay = np.power(0.5, age / hl)
        ic = float(np.nansum(g["information_coefficient"] * decay) /
                   max(np.nansum(decay * g["information_coefficient"].notna()), 1e-9))
        alpha = float(np.nansum(g["alpha"] * decay) /
                      max(np.nansum(decay * g["alpha"].notna()), 1e-9))
        signal = np.nanmean([ic * 5, alpha * 20])
        mult = float(np.clip(1.0 + signal, 0.5, 2.0)) if math.isfinite(signal) else 1.0
        out[name] = base * mult
    return out


def challenger_beats_champion(store: Store, champion: dict[str, float],
                              challenger: dict[str, float], cfg,
                              horizon: int = 5) -> tuple[bool, str]:
    """Paired comparison over history: score each past finalist under both weight
    sets; compare weighted-composite-implied selections' forward returns."""
    hist = store.prediction_history()
    hist = hist[(hist["horizon_days"] == horizon) & hist["ret"].notna()]
    if len(hist) < 60:
        return False, f"insufficient scored history (n={len(hist)}<60)"
    diffs = []
    for run_date, g in hist.groupby("run_date"):
        scored = []
        for _, row in g.iterrows():
            try:
                pcts = json.loads(row["payload"]).get("SCREEN_PERCENTILES", {})
            except Exception:
                continue
            def wscore(ws):
                num = sum(ws.get(k, 0) * v for k, v in pcts.items()
                          if isinstance(v, (int, float)) and math.isfinite(v))
                den = sum(ws.get(k, 0) for k, v in pcts.items()
                          if isinstance(v, (int, float)) and math.isfinite(v))
                return num / den if den else np.nan
            scored.append((wscore(champion), wscore(challenger), row["ret"]))
        if len(scored) < 2:
            continue
        df = pd.DataFrame(scored, columns=["champ", "chall", "ret"]).dropna()
        if len(df) < 2:
            continue
        champ_pick = df.loc[df["champ"].idxmax(), "ret"]
        chall_pick = df.loc[df["chall"].idxmax(), "ret"]
        diffs.append(chall_pick - champ_pick)
    if len(diffs) < 30:
        return False, f"insufficient decision days (n={len(diffs)}<30)"
    t, p = stats.ttest_1samp(diffs, 0.0)
    ok = t >= cfg.learning.champion_min_advantage_t
    return ok, f"t={t:.2f} p={p:.3f} mean_diff={np.mean(diffs):.4%} n={len(diffs)}"


def maybe_promote_challenger(store: Store, cfg) -> dict[str, float]:
    """Returns the weight set to use today. Promotion is logged + versioned."""
    champion = store.get_active_weights("fundamental_screens") or dict(cfg.screens.weights)
    challenger = propose_weights(store, dict(cfg.screens.weights), cfg)
    if all(abs(challenger.get(k, 0) - champion.get(k, 0)) < 1e-6 for k in champion):
        return champion
    ok, evidence = challenger_beats_champion(store, champion, challenger, cfg)
    if not ok:
        log.info("challenger not promoted: %s", evidence)
        return champion
    old_v = MODEL_VERSION
    new_v = _bump(old_v)
    store.set_active_weights("fundamental_screens", challenger, new_v)
    store.log_model_change(
        old=old_v, new=new_v, change="fundamental screen weights updated",
        reason="challenger beat champion in paired walk-forward comparison",
        oos_evidence=evidence)
    log.info("challenger promoted (%s): %s", new_v, evidence)
    return challenger


def _bump(version: str) -> str:
    try:
        head, minor = version.rsplit(".", 1)
        return f"{head}.{int(minor) + 1}"
    except Exception:
        return version + ".1"
