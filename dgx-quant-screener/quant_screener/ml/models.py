"""cuML model wrappers + per-finalist prediction (spec §6).

Models run on the DGX Spark GPU via cuML when available; identical sklearn
estimators otherwise (see quant_screener.gpu). A model's predictions are used
only if its walk-forward report is accepted (spec §7).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from .. import gpu
from .features import build_training_frame, feature_columns
from .validation import WalkForwardReport, evaluate_walk_forward

log = logging.getLogger(__name__)


def make_model_factory(kind: str, seed: int):
    if kind == "logistic":
        return lambda: gpu.get_logistic_regression()
    if kind == "random_forest":
        return lambda: gpu.get_random_forest_classifier(
            n_estimators=200, max_depth=6, random_state=seed)
    raise ValueError(kind)


def _clean_xy(data: pd.DataFrame, feats: list[str], target: str, idx: pd.Index):
    sub = data.loc[data.index.intersection(idx), feats + [target]].replace(
        [np.inf, -np.inf], np.nan).dropna()
    return sub[feats].to_numpy(dtype=np.float32), sub[target].to_numpy(dtype=np.float32), sub


def fit_predict_fold(data: pd.DataFrame, feats: list[str], horizon: int, fold,
                     model_factory) -> dict | None:
    """One walk-forward fold: fit classifier on train, score on test."""
    ycls = f"fwd_pos_{horizon}"
    yret = f"fwd_ret_{horizon}"
    Xtr, ytr, _ = _clean_xy(data, feats, ycls, fold.train)
    Xte, yte, sub_te = _clean_xy(data, feats, ycls, fold.test)
    if len(Xtr) < 100 or len(Xte) < 5 or len(np.unique(ytr)) < 2:
        return None
    mu, sd = Xtr.mean(0), Xtr.std(0) + 1e-9
    Xtr, Xte = (Xtr - mu) / sd, (Xte - mu) / sd
    model = model_factory()
    try:
        model.fit(Xtr, ytr)
        prob_te = np.asarray(gpu.to_host(model.predict_proba(Xte)))[:, 1]
        prob_tr = np.asarray(gpu.to_host(model.predict_proba(Xtr)))[:, 1]
    except Exception as e:  # pragma: no cover
        log.debug("fold fit failed: %s", e)
        return None
    pred_te = (prob_te > 0.5).astype(float)
    tp = float(((pred_te == 1) & (yte == 1)).sum())
    from sklearn.metrics import roc_auc_score

    try:
        auc = float(roc_auc_score(yte, prob_te)) if len(np.unique(yte)) > 1 else np.nan
    except Exception:
        auc = np.nan
    real_ret = sub_te[yret].to_numpy() if yret in sub_te else np.full(len(yte), np.nan)
    return {
        "acc": float((pred_te == yte).mean()),
        "train_acc": float(((prob_tr > 0.5) == ytr).mean()),
        "precision": tp / max(float((pred_te == 1).sum()), 1.0),
        "recall": tp / max(float((yte == 1).sum()), 1.0),
        "auc": auc,
        "prob": prob_te.tolist(), "hit": yte.tolist(),
        "pred_ret": prob_te.tolist(),   # prob as return-rank proxy for IC
        "real_ret": real_ret.tolist(),
    }


@dataclass
class MLPrediction:
    ticker: str
    expected_return: dict[int, float] = field(default_factory=dict)       # horizon -> E[r]
    probability_positive: dict[int, float] = field(default_factory=dict)  # horizon -> P(r>0)
    expected_downside: float = np.nan
    tail_risk: float = np.nan
    model_confidence: float = 0.0
    validation: dict[int, WalkForwardReport] = field(default_factory=dict)
    accepted: bool = False
    notes: list[str] = field(default_factory=list)


def predict_finalist(ticker: str, px: pd.DataFrame, cfg,
                     bench_close: pd.Series | None,
                     sector_close: pd.Series | None,
                     macro_series: dict[str, pd.Series]) -> MLPrediction:
    """Walk-forward validate per horizon, then fit on all history and predict today."""
    pred = MLPrediction(ticker=ticker)
    horizons = list(cfg.ml.horizons_days)
    df = build_training_frame(px, horizons, bench_close, sector_close, macro_series)
    feats = feature_columns(df)
    feats = [f for f in feats if df[f].notna().mean() > 0.5]  # drop mostly-missing features
    if len(df.dropna(subset=feats, how="any")) < cfg.ml.min_train_days:
        pred.notes.append("insufficient history for ML")
        return pred

    accepted_any = False
    for h in horizons:
        best_rep, best_factory = None, None
        for kind in cfg.ml.models:
            factory = make_model_factory(kind, cfg.ml.random_state)
            rep = evaluate_walk_forward(df, feats, h, factory, cfg)
            if best_rep is None or (np.nan_to_num(rep.rank_ic) > np.nan_to_num(best_rep.rank_ic)):
                best_rep, best_factory = rep, factory
        pred.validation[h] = best_rep
        if not best_rep.accepted:
            pred.notes.append(f"h={h}: model rejected ({'; '.join(best_rep.reject_reasons)})")
            continue
        accepted_any = True
        # final fit on all clean rows whose target is known (past data only)
        ycls, yret = f"fwd_pos_{h}", f"fwd_ret_{h}"
        train = df.dropna(subset=feats + [ycls])
        X = train[feats].to_numpy(np.float32)
        mu, sd = X.mean(0), X.std(0) + 1e-9
        model = best_factory()
        model.fit((X - mu) / sd, train[ycls].to_numpy(np.float32))
        latest = df[feats].iloc[[-1]].replace([np.inf, -np.inf], np.nan)
        if latest.isna().any(axis=None):
            pred.notes.append(f"h={h}: latest features incomplete — no prediction")
            continue
        p_up = float(np.asarray(gpu.to_host(
            model.predict_proba(((latest.to_numpy(np.float32) - mu) / sd))))[:, 1][0])
        pred.probability_positive[h] = p_up
        # expected return: probability-blended historical conditional means
        r = train[yret]
        up_mean, dn_mean = float(r[r > 0].mean()), float(r[r <= 0].mean())
        pred.expected_return[h] = p_up * up_mean + (1 - p_up) * dn_mean

    rets = px["close"].pct_change().dropna()
    if len(rets) > 100:
        pred.expected_downside = float(rets.quantile(0.05))
        tail = rets[rets <= rets.quantile(0.05)]
        pred.tail_risk = float(tail.mean()) if len(tail) else np.nan

    ics = [r.rank_ic for r in pred.validation.values() if np.isfinite(r.rank_ic)]
    if ics and accepted_any:
        pred.model_confidence = float(np.clip(np.mean(ics) * 1000, 0, 100)) * \
            (1 - min(np.std(ics) * 10, 0.5))
        pred.model_confidence = float(np.clip(pred.model_confidence, 0, 100))
    pred.accepted = accepted_any
    return pred
