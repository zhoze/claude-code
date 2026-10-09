"""Stage 4 — run_risk_layer: anomalies, correlation clusters, market regime, carried set.

v0.1 scope (agreed): CPU implementations that are deterministic and small.
  - clusters: average-linkage hierarchical clustering on the correlation distance
    sqrt(2(1 - rho)) over the trailing year (cuML HDBSCAN is an optional backend);
  - regime: 2-state Gaussian Markov-switching (HMM) model on SPY daily log returns,
    fitted by EM with a deterministic initialisation (cuML has no HMM; review §2);
  - anomalies: hard data problems exclude a name (insufficient history, stale
    price); soft flags (extreme one-day moves) are recorded but do not exclude;
  - expected-return model: not fitted in v0.1. The carried set is the top
    `carried` names by fundamental consensus after hard-anomaly exclusion.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
from scipy.cluster.hierarchy import fcluster, linkage
from scipy.spatial.distance import squareform


def anomalies(returns: pd.DataFrame, names: list[str], session: str, cfg) -> dict[str, list]:
    rc = cfg.strategy["risk_layer"]
    look = rc["lookback_days"]
    flags: dict[str, list] = {}
    tail = returns.reindex(columns=names).iloc[-look:]
    for t in names:
        s = tail[t] if t in tail.columns else pd.Series(dtype=float)
        f = []
        if s.notna().sum() < 0.9 * look:
            f.append("hard:insufficient_history")
        else:
            last_valid = s.last_valid_index()
            if last_valid is None or str(last_valid.date()) < session:
                f.append("hard:stale_price")
            sd = s.iloc[:-1].std()
            last = s.iloc[-1]
            if sd and np.isfinite(last) and abs(last) > rc["anomaly_return_sigma"] * sd:
                f.append("soft:extreme_last_return")
        if f:
            flags[t] = f
    return flags


def correlation_clusters(returns: pd.DataFrame, names: list[str], cfg) -> dict[str, int]:
    rc = cfg.strategy["risk_layer"]
    if len(names) < 2:
        return {t: 1 for t in names}
    r = returns.reindex(columns=sorted(names)).iloc[-rc["lookback_days"]:]
    corr = r.corr(min_periods=60).fillna(0.0).to_numpy(copy=True)
    np.fill_diagonal(corr, 1.0)
    dist = np.sqrt(np.clip(2.0 * (1.0 - corr), 0.0, None))
    dist = (dist + dist.T) / 2
    np.fill_diagonal(dist, 0.0)
    z = linkage(squareform(dist, checks=False), method="average")
    raw = fcluster(z, t=rc["cluster_distance"], criterion="distance")
    # renumber deterministically by first (alphabetical) member
    order, ids = {}, {}
    for t, c in zip(sorted(names), raw):
        if c not in order:
            order[c] = len(order) + 1
        ids[t] = order[c]
    return ids


def _hmm_2state(x: np.ndarray, iters: int = 200, tol: float = 1e-8):
    """Gaussian HMM with 2 states via scaled Baum-Welch. Deterministic init."""
    n = len(x)
    q = np.quantile(np.abs(x - x.mean()), 0.5)
    lo = np.abs(x - x.mean()) <= q
    mu = np.array([x[lo].mean(), x[~lo].mean()])
    var = np.array([x[lo].var() + 1e-12, x[~lo].var() + 1e-12])
    A = np.array([[0.98, 0.02], [0.05, 0.95]])
    pi = np.array([0.5, 0.5])
    prev = -np.inf
    for _ in range(iters):
        b = np.exp(-0.5 * (x[:, None] - mu) ** 2 / var) / np.sqrt(2 * np.pi * var) + 1e-300
        alpha = np.zeros((n, 2))
        c = np.zeros(n)
        alpha[0] = pi * b[0]
        c[0] = alpha[0].sum()
        alpha[0] /= c[0]
        for t in range(1, n):
            alpha[t] = (alpha[t - 1] @ A) * b[t]
            c[t] = alpha[t].sum()
            alpha[t] /= c[t]
        beta = np.ones((n, 2))
        for t in range(n - 2, -1, -1):
            beta[t] = (A @ (b[t + 1] * beta[t + 1])) / c[t + 1]
        gamma = alpha * beta
        gamma /= gamma.sum(axis=1, keepdims=True)
        xi = (alpha[:-1, :, None] * A[None] * (b[1:] * beta[1:])[:, None, :]) / c[1:, None, None]
        ll = np.log(c).sum()
        pi = gamma[0]
        A = xi.sum(axis=0) / gamma[:-1].sum(axis=0)[:, None]
        A /= A.sum(axis=1, keepdims=True)
        w = gamma.sum(axis=0)
        mu = (gamma * x[:, None]).sum(axis=0) / w
        var = (gamma * (x[:, None] - mu) ** 2).sum(axis=0) / w + 1e-12
        if ll - prev < tol:
            break
        prev = ll
    return mu, var, A, alpha[-1], ll


def market_regime(market: pd.DataFrame, cfg, years: int = 10) -> dict:
    spy = market[market["symbol"] == cfg.infra["benchmarks"]["market"]].sort_values("date")
    px = spy["adjclose"].astype(float).to_numpy()
    if len(px) < 300:
        return {"label": "unknown", "probs": {}, "model": "insufficient SPY history"}
    x = np.diff(np.log(px))[-252 * years:]
    mu, var, A, filt, ll = _hmm_2state(x)
    calm = int(np.argmin(var))
    probs = {"risk_on": round(float(filt[calm]), 4), "risk_off": round(float(filt[1 - calm]), 4)}
    label = max(probs, key=probs.get)
    return {"label": label, "probs": probs, "model": "markov_switching_2state",
            "ann_vol": {"risk_on": round(float(np.sqrt(var[calm] * 252)), 4),
                        "risk_off": round(float(np.sqrt(var[1 - calm] * 252)), 4)},
            "persistence": {"risk_on": round(float(A[calm, calm]), 4),
                            "risk_off": round(float(A[1 - calm, 1 - calm]), 4)},
            "loglik": round(float(ll), 2), "as_of": str(spy["date"].iloc[-1])}


def run_risk_layer(cfg, candidates: list[str], consensus: pd.DataFrame, returns: pd.DataFrame,
                   market: pd.DataFrame, session: str) -> dict:
    rc = cfg.strategy["risk_layer"]
    flags = anomalies(returns, candidates, session, cfg)
    hard = {t for t, f in flags.items() if any(x.startswith("hard:") for x in f)}
    ranked = consensus.set_index("ticker").reindex(candidates)
    eligible = [t for t in ranked.sort_values(["fund_consensus"], ascending=False,
                                              kind="mergesort").index if t not in hard]
    carried = eligible[: rc["carried"]]
    clusters = correlation_clusters(returns, carried, cfg)
    regime = market_regime(market, cfg)
    return {"carried": carried, "clusters": clusters, "regime": regime,
            "anomalies": flags, "excluded": sorted(hard),
            "er_model": {"status": "not_fitted_v0.1"}}
