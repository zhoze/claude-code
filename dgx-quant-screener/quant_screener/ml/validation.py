"""Walk-forward validation (spec §7). Never random splits for time series.

Expanding (or rolling) TRAIN -> VALIDATE -> TEST folds, strictly ordered in time.
Targets look `horizon` days forward, so an embargo gap of `horizon` rows is
enforced between train end and validation start (and between val and test) to
prevent forward-return leakage across the boundary.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd
from scipy import stats


@dataclass
class Fold:
    train: pd.Index
    val: pd.Index
    test: pd.Index


@dataclass
class WalkForwardReport:
    folds: int = 0
    oos_accuracy: float = np.nan
    oos_precision: float = np.nan
    oos_recall: float = np.nan
    oos_auc: float = np.nan
    rank_ic: float = np.nan            # Spearman IC of predicted vs realized return
    return_spread: float = np.nan      # top-minus-bottom-quintile mean fwd return
    calibration_gap: float = np.nan    # |mean predicted prob - realized hit rate|
    train_accuracy: float = np.nan
    regime_stability: float = np.nan   # std of per-fold accuracy
    accepted: bool = False
    reject_reasons: list[str] = field(default_factory=list)


def walk_forward_folds(index: pd.Index, min_train: int, val_len: int, test_len: int,
                       horizon: int, scheme: str = "expanding",
                       max_folds: int = 12) -> list[Fold]:
    n = len(index)
    folds: list[Fold] = []
    start = min_train
    step = test_len
    while start + horizon + val_len + horizon + test_len <= n and len(folds) < max_folds:
        train_lo = 0 if scheme == "expanding" else max(0, start - min_train)
        tr = index[train_lo:start]
        va = index[start + horizon: start + horizon + val_len]
        te = index[start + horizon + val_len + horizon:
                   start + horizon + val_len + horizon + test_len]
        if len(te):
            folds.append(Fold(tr, va, te))
        start += step
    return folds


def evaluate_walk_forward(df: pd.DataFrame, feats: list[str], horizon: int,
                          model_factory, cfg) -> WalkForwardReport:
    """Fit per fold on TRAIN, early-stop selection on VAL is implicit in the simple
    models used here; metrics reported strictly on TEST."""
    from .models import fit_predict_fold

    rep = WalkForwardReport()
    data = df.dropna(subset=feats + [f"fwd_ret_{horizon}"], how="any")
    if len(data) < cfg.ml.min_train_days // 2:
        rep.reject_reasons.append("insufficient clean history")
        return rep
    folds = walk_forward_folds(
        data.index, min_train=min(cfg.ml.min_train_days, int(len(data) * 0.5)),
        val_len=cfg.ml.walkforward.validation_days,
        test_len=cfg.ml.walkforward.test_days, horizon=horizon,
        scheme=cfg.ml.walkforward.scheme)
    if len(folds) < cfg.ml.walkforward.min_folds:
        rep.reject_reasons.append(f"only {len(folds)} folds (<{cfg.ml.walkforward.min_folds})")
        return rep

    accs, train_accs, aucs, precs, recalls = [], [], [], [], []
    all_pred, all_real, all_prob, all_hit = [], [], [], []
    for f in folds:
        r = fit_predict_fold(data, feats, horizon, f, model_factory)
        if r is None:
            continue
        accs.append(r["acc"]); train_accs.append(r["train_acc"])
        precs.append(r["precision"]); recalls.append(r["recall"])
        if np.isfinite(r["auc"]):
            aucs.append(r["auc"])
        all_pred.extend(r["pred_ret"]); all_real.extend(r["real_ret"])
        all_prob.extend(r["prob"]); all_hit.extend(r["hit"])

    if not accs:
        rep.reject_reasons.append("all folds failed")
        return rep
    rep.folds = len(accs)
    rep.oos_accuracy = float(np.mean(accs))
    rep.train_accuracy = float(np.mean(train_accs))
    rep.oos_precision = float(np.mean(precs))
    rep.oos_recall = float(np.mean(recalls))
    rep.oos_auc = float(np.mean(aucs)) if aucs else np.nan
    rep.regime_stability = float(np.std(accs))
    if len(all_pred) >= 20:
        rep.rank_ic = float(stats.spearmanr(all_pred, all_real).statistic)
        q = pd.qcut(pd.Series(all_pred), 5, labels=False, duplicates="drop")
        real = pd.Series(all_real)
        if q.nunique() >= 3:
            rep.return_spread = float(real[q == q.max()].mean() - real[q == 0].mean())
    if all_prob:
        rep.calibration_gap = abs(float(np.mean(all_prob)) - float(np.mean(all_hit)))

    # acceptance gates (spec §7: reject strong-train/weak-walkforward models)
    if not np.isfinite(rep.rank_ic) or rep.rank_ic < cfg.ml.min_oos_ic:
        rep.reject_reasons.append(f"rank IC {rep.rank_ic:.3f} < {cfg.ml.min_oos_ic}")
    if rep.train_accuracy - rep.oos_accuracy > cfg.ml.max_train_val_gap:
        rep.reject_reasons.append("overfit: train-OOS accuracy gap too large")
    if rep.regime_stability > 0.15:
        rep.reject_reasons.append("unstable across folds/regimes")
    rep.accepted = not rep.reject_reasons
    return rep
