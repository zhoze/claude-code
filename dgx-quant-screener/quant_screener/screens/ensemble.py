"""Screen normalization, overlap and composite (spec §4-5).

Every screen's raw score becomes a 0-100 percentile; PASS/FAIL and per-screen
confidence are carried through. Overlap count drives finalist selection with
progressively greater importance for higher overlap.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

OVERLAP_TIERS = [  # (min_overlap, label, multiplier) — progressive importance
    (10, "EXCEPTIONAL", 1.30), (8, "EXTREMELY_STRONG", 1.20),
    (6, "STRONG", 1.10), (4, "ACCEPTABLE", 1.00), (0, "REJECT", 0.0),
]


@dataclass
class EnsembleResult:
    table: pd.DataFrame                  # per-ticker: percentiles, overlap, composite
    finalists: list[str] = field(default_factory=list)
    screen_weights: dict[str, float] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)


def overlap_tier(count: int) -> tuple[str, float]:
    for min_n, label, mult in OVERLAP_TIERS:
        if count >= min_n:
            return label, mult
    return "REJECT", 0.0


def build_ensemble(screen_results: dict[str, pd.DataFrame], cfg,
                   active_weights: dict[str, float] | None = None,
                   sectors: dict[str, str] | None = None) -> EnsembleResult:
    weights = dict(cfg.screens.weights)
    if active_weights:  # validated adaptive weights from learning DB (spec §33)
        weights.update({k: v for k, v in active_weights.items() if k in weights})
    wsum = sum(weights.values())
    weights = {k: v / wsum for k, v in weights.items()}

    tickers = sorted(set().union(*[set(df.index) for df in screen_results.values()]))
    out = pd.DataFrame(index=pd.Index(tickers, name="ticker"))

    for name, df in screen_results.items():
        raw = df["raw"].reindex(tickers)
        pct = raw.rank(pct=True) * 100
        if sectors and cfg.screens.sector_normalize and name in ("value_composite", "quality"):
            sec = pd.Series({t: sectors.get(t, "UNKNOWN") for t in tickers})
            sec_pct = raw.groupby(sec).rank(pct=True) * 100
            counts = sec.map(sec.value_counts())
            pct = sec_pct.where(counts >= 5, pct)
        out[f"{name}_pct"] = pct
        out[f"{name}_pass"] = df["passed"].reindex(tickers).fillna(False)
        out[f"{name}_conf"] = df["confidence"].reindex(tickers).fillna(0.0)

    pass_cols = [c for c in out.columns if c.endswith("_pass")]
    conf_cols = [c for c in out.columns if c.endswith("_conf")]
    out["overlap"] = out[pass_cols].sum(axis=1).astype(int)
    out["data_confidence"] = out[conf_cols].mean(axis=1)

    # composite = confidence-weighted mean of percentile scores × validated weights
    comp = pd.Series(0.0, index=out.index)
    wtot = pd.Series(0.0, index=out.index)
    for name in screen_results:
        pct, conf = out[f"{name}_pct"], out[f"{name}_conf"]
        w = weights.get(name, 0.1) * conf
        comp = comp.add((pct * w).fillna(0.0))
        wtot = wtot.add(w.where(pct.notna(), 0.0))
    out["composite"] = (comp / wtot.replace(0, np.nan)).round(2)

    tiers = out["overlap"].map(lambda n: overlap_tier(int(n)))
    out["overlap_tier"] = tiers.map(lambda x: x[0])
    out["overlap_mult"] = tiers.map(lambda x: x[1])
    out["rank_score"] = out["composite"] * out["overlap_mult"]

    res = EnsembleResult(table=out, screen_weights=weights)

    # finalists: must clear min_overlap — thresholds are never loosened to fill slots
    eligible = out[(out["overlap"] >= cfg.screens.min_overlap) & out["composite"].notna()]
    eligible = eligible.sort_values(["overlap", "rank_score"], ascending=False)
    res.finalists = list(eligible.head(cfg.screens.max_finalists).index)
    if len(res.finalists) < cfg.screens.max_finalists:
        res.notes.append(
            f"only {len(res.finalists)} names cleared min_overlap="
            f"{cfg.screens.min_overlap}; thresholds NOT loosened (spec §5)")
    return res
