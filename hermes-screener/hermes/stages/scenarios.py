"""Stage 5 — generate_scenarios: S horizon-return scenarios for the carried names.

Mixture (strategy.yaml scenarios.mix; spec Appendix B / review C6):
  kde              multivariate Gaussian KDE over overlapping horizon log-returns
                   (Silverman bandwidth, full covariance) — NVIDIA's Spark playbook
                   uses KDE; this is the exact-sampling form (data point + kernel draw)
  block_bootstrap  Politis–Romano stationary bootstrap of daily cross-sections,
                   mean block length `block_mean_days`; keeps volatility clustering
  historical       named stress windows mapped through each name's market + sector
                   betas (the per-name history does not reach 2008), residuals
                   scaled by the window's market-volatility ratio
  stress           hypothetical market / sector / single-name-gap shocks
  + earnings-jump overlay: each name's own past announcement reactions (3-day
    idiosyncratic return), applied with p=1 if its next scheduled date falls in the
    horizon, p=0 if scheduled after it, `jump_unknown_date_prob` if unknown.

Determinism: every random draw comes from numpy Generators spawned from one seed,
on the host. The GPU path (CuPy) does only the heavy gathers and sums, so a CPU and
a GPU run consume the same draws and agree to float64 rounding (PERF4 rule).
"""
from __future__ import annotations

import logging
import math

import numpy as np
import pandas as pd

from .. import backend as bk

log = logging.getLogger(__name__)
COMPONENTS = ("kde", "block_bootstrap", "historical", "stress")


def allocate(n: int, mix: dict) -> dict[str, int]:
    """Largest-remainder split of n scenarios across components (deterministic)."""
    w = np.array([float(mix.get(c, 0.0)) for c in COMPONENTS])
    w = w / w.sum()
    raw = w * n
    base = np.floor(raw).astype(int)
    rem = n - base.sum()
    order = np.argsort(-(raw - base), kind="stable")
    base[order[:rem]] += 1
    return dict(zip(COMPONENTS, base.tolist()))


def _daily_log(returns: pd.DataFrame, names: list[str], days: int) -> np.ndarray:
    r = returns.reindex(columns=names).iloc[-days:]
    return np.log1p(r.fillna(0.0).to_numpy(dtype=np.float64))


def _horizon_windows(daily: np.ndarray, h: int) -> np.ndarray:
    c = np.vstack([np.zeros((1, daily.shape[1])), np.cumsum(daily, axis=0)])
    return c[h:] - c[:-h]


def kde_scenarios(daily, h, n, rng, xp) -> np.ndarray:
    if n == 0:
        return np.zeros((0, daily.shape[1]))
    X = _horizon_windows(daily, h)
    m, d = X.shape
    cov = np.cov(X, rowvar=False).reshape(d, d)
    bw = (4.0 / (d + 2)) ** (2.0 / (d + 4)) * m ** (-2.0 / (d + 4))
    H = bw * cov + 1e-12 * np.eye(d)
    L = np.linalg.cholesky(H)
    idx = rng.integers(0, m, size=n)
    z = rng.standard_normal((n, d))
    Xd, zd, Ld = xp.asarray(X), xp.asarray(z), xp.asarray(L)
    return bk.to_host(Xd[xp.asarray(idx)] + zd @ Ld.T)


def bootstrap_scenarios(daily, h, n, mean_block, rng, xp) -> np.ndarray:
    T, d = daily.shape
    if n == 0:
        return np.zeros((0, d))
    p = 1.0 / mean_block
    starts = rng.integers(0, T, size=(h, n))
    jumps = rng.random((h, n)) < p
    D = xp.asarray(daily)
    idx = starts[0]
    acc = D[xp.asarray(idx)]
    for k in range(1, h):
        idx = np.where(jumps[k], starts[k], (idx + 1) % T)
        acc = acc + D[xp.asarray(idx)]
    return bk.to_host(acc)


def factor_betas(returns: pd.DataFrame, names: list[str], mkt: pd.Series,
                 sec_excess: pd.DataFrame, sectors: dict, days: int) -> pd.DataFrame:
    """Per-name OLS on market and sector-excess daily log returns (+ residual sd)."""
    rows = {}
    r = np.log1p(returns.reindex(columns=names).iloc[-days:])
    m = np.log1p(mkt.reindex(r.index))
    for t in names:
        y = r[t]
        sec = sectors.get(t)
        x_s = np.log1p(sec_excess[sec].reindex(r.index)) if sec in sec_excess else None
        cols = [m] + ([x_s] if x_s is not None else [])
        X = pd.concat(cols, axis=1)
        ok = y.notna() & X.notna().all(axis=1)
        if ok.sum() < 60:
            rows[t] = {"b_mkt": 1.0, "b_sec": 0.0, "resid_sd": float(y.std() or 0.02)}
            continue
        A = np.column_stack([np.ones(ok.sum()), X[ok].to_numpy()])
        coef, *_ = np.linalg.lstsq(A, y[ok].to_numpy(), rcond=None)
        resid = y[ok].to_numpy() - A @ coef
        rows[t] = {"b_mkt": float(coef[1]), "b_sec": float(coef[2]) if len(coef) > 2 else 0.0,
                   "resid_sd": float(resid.std(ddof=len(coef)))}
    return pd.DataFrame(rows).T


def historical_scenarios(cfg, n, h, rng, betas, sectors, market_wide, names):
    """Named windows through the factor map. Returns (S x N array, window labels)."""
    d = len(names)
    if n == 0:
        return np.zeros((0, d)), []
    sc = cfg.strategy["scenarios"]
    mkt_sym = cfg.infra["benchmarks"]["market"]
    lr = np.log(market_wide).diff()
    normal_vol = lr[mkt_sym].iloc[-756:].std()
    sec_etf = sector_etf_map()
    windows = []
    for label, (a, b) in sc["historical_windows"].items():
        w = lr.loc[a:b]
        if w[mkt_sym].notna().sum() >= h + 1:
            windows.append((label, w))
    if not windows:
        return None, []
    out = np.zeros((n, d))
    labels = []
    which = rng.integers(0, len(windows), size=n)
    z = rng.standard_normal((n, d))
    bm = betas["b_mkt"].reindex(names).to_numpy()
    bs = betas["b_sec"].reindex(names).to_numpy()
    rs = betas["resid_sd"].reindex(names).to_numpy()
    for k, (label, w) in enumerate(windows):
        rows = np.where(which == k)[0]
        if not len(rows):
            continue
        mk = w[mkt_sym].fillna(0.0).to_numpy()
        csum = np.concatenate([[0.0], np.cumsum(mk)])
        starts = rng.integers(0, len(mk) - h + 1, size=len(rows))
        R_m = csum[starts + h] - csum[starts]
        vol_ratio = max(float(w[mkt_sym].std() / normal_vol), 1.0) if normal_vol else 1.0
        sec_part = np.zeros((len(rows), d))
        for j, t in enumerate(names):
            etf = sec_etf.get(sectors.get(t))
            if etf and etf in w.columns and w[etf].notna().sum() >= h + 1:
                ex = (w[etf] - w[mkt_sym]).fillna(0.0).to_numpy()
                cs = np.concatenate([[0.0], np.cumsum(ex)])
                sec_part[:, j] = bs[j] * (cs[starts + h] - cs[starts])
        out[rows] = (R_m[:, None] * bm[None, :] + sec_part
                     + z[rows] * rs[None, :] * math.sqrt(h) * vol_ratio)
        labels.append(label)
    return out, labels


def stress_scenarios(cfg, n, h, rng, betas, sectors, names, mkt_daily_sd):
    d = len(names)
    if n == 0:
        return np.zeros((0, d))
    st = cfg.strategy["scenarios"]["stress"]
    bm = betas["b_mkt"].reindex(names).to_numpy()
    rs = betas["resid_sd"].reindex(names).to_numpy()
    mult = st["stress_vol_mult"]
    kinds = [("market", s) for s in st["market_shocks"]] + [("sector", None), ("single", None)]
    pick = rng.integers(0, len(kinds), size=n)
    z = rng.standard_normal((n, d))
    zm = rng.standard_normal(n)
    sec_list = sorted({sectors.get(t) or "Unknown" for t in names})
    sec_arr = np.array([sectors.get(t) or "Unknown" for t in names])
    sec_pick = rng.integers(0, len(sec_list), size=n)
    name_pick = rng.integers(0, d, size=n)
    out = z * rs[None, :] * math.sqrt(h) * mult
    for i in range(n):
        kind, shock = kinds[pick[i]]
        if kind == "market":
            out[i] += bm * math.log1p(shock)
        else:
            out[i] += bm * zm[i] * mkt_daily_sd * math.sqrt(h) * mult
            if kind == "sector":
                out[i, sec_arr == sec_list[sec_pick[i]]] += math.log1p(st["sector_shock"])
            else:
                out[i, name_pick[i]] += math.log1p(st["single_name_gap"])
    return out


def earnings_reactions(returns, mkt, earnings, names, betas) -> dict[str, np.ndarray]:
    """3-day [-1,+1] idiosyncratic log reactions around each past announcement."""
    out = {}
    if earnings is None or earnings.empty:
        return out
    past = earnings[earnings["eps_actual"].notna()]
    lr = np.log1p(returns)
    lm = np.log1p(mkt.reindex(returns.index))
    dates = returns.index
    for t in names:
        if t not in lr.columns:
            continue
        b = float(betas.loc[t, "b_mkt"]) if t in betas.index else 1.0
        vals = []
        for d in pd.to_datetime(past.loc[past["ticker"] == t, "date"]):
            k = dates.searchsorted(d)
            if 1 <= k < len(dates) - 1:
                y = lr[t].iloc[k - 1:k + 2].sum()
                m = lm.iloc[k - 1:k + 2].sum()
                if np.isfinite(y) and np.isfinite(m):
                    vals.append(y - b * m)
        out[t] = np.array(vals)
    return out


def jump_probabilities(earnings, names, session, h, p_unknown) -> np.ndarray:
    sess = pd.Timestamp(session)
    horizon_end = sess + pd.offsets.BDay(h)
    p = np.full(len(names), p_unknown)
    if earnings is None or earnings.empty:
        return p
    fut = earnings[earnings["eps_actual"].isna()].copy()
    fut["date"] = pd.to_datetime(fut["date"])
    fut = fut[fut["date"] > sess]
    nxt = fut.groupby("ticker")["date"].min()
    for j, t in enumerate(names):
        if t in nxt.index:
            p[j] = 1.0 if nxt[t] <= horizon_end else 0.0
    return p


def sector_etf_map() -> dict:
    from .. import vendored  # noqa: PLC0415
    return vendored.ta().panel.load_config()["benchmarks"]["sector_etfs"]


def generate_scenarios(cfg, names: list[str], returns: pd.DataFrame, market: pd.DataFrame,
                       sectors: dict, earnings: pd.DataFrame, session: str, seed: int,
                       n: int | None = None) -> dict:
    sc = cfg.strategy["scenarios"]
    h = int(sc["horizon_days"])
    warnings = []
    names = list(names)
    d = len(names)

    gpu_ok = bk.cupy_healthy()
    mode = cfg.infra["backend"]["arrays"]
    n_req = int(n or sc["n"])
    cells = n_req * d * h
    be = bk.choose(mode, cells, float(cfg.infra["backend"]["gpu_min_scenario_cells"]), gpu_ok)
    if n is None and be == "cpu" and not gpu_ok and sc.get("n_cpu"):
        n_req = int(sc["n_cpu"])
        warnings.append(f"cpu_fallback: scenarios reduced {sc['n']} -> {n_req} "
                        "(no healthy GPU; strategy.scenarios.n_cpu)")
    counts = allocate(n_req, sc["mix"])

    market_wide = (market.pivot_table(index="date", columns="symbol", values="adjclose",
                                      aggfunc="last").sort_index())
    market_wide.index = pd.to_datetime(market_wide.index)
    mkt_sym = cfg.infra["benchmarks"]["market"]
    mkt_ret = market_wide[mkt_sym].pct_change()
    etf_map = sector_etf_map()
    sec_excess = pd.DataFrame({s: market_wide[e].pct_change() - mkt_ret
                               for s, e in etf_map.items() if e in market_wide.columns})
    betas = factor_betas(returns, names, mkt_ret, sec_excess, sectors, sc["history_days"])
    daily = _daily_log(returns, names, int(sc["history_days"]))

    def make_rngs():
        seqs = np.random.SeedSequence(seed).spawn(len(COMPONENTS) + 1)
        return dict(zip(COMPONENTS + ("jump",), (np.random.default_rng(s) for s in seqs)))

    def run(backend_name, rngs):
        xp = bk.array_module(backend_name)
        return {
            "kde": kde_scenarios(daily, h, counts["kde"], rngs["kde"], xp),
            "block_bootstrap": bootstrap_scenarios(daily, h, counts["block_bootstrap"],
                                                   sc["block_mean_days"],
                                                   rngs["block_bootstrap"], xp),
        }

    rngs = make_rngs()
    try:
        parts = run(be, rngs)
    except Exception as e:  # noqa: BLE001 — GPU failure: restart the whole calc on CPU
        if be == "cpu":
            raise
        warnings.append(f"gpu_failure_restarted_on_cpu: {type(e).__name__}")
        be = "cpu"
        rngs = make_rngs()
        parts = run(be, rngs)

    hist, labels = historical_scenarios(cfg, counts["historical"], h, rngs["historical"],
                                        betas, sectors, market_wide, names)
    if hist is None:
        warnings.append("historical windows unavailable (market history too short); "
                        "their share reallocated to stress")
        counts["stress"] += counts["historical"]
        counts["historical"] = 0
        hist = np.zeros((0, d))
    parts["historical"] = hist
    parts["stress"] = stress_scenarios(cfg, counts["stress"], h, rngs["stress"], betas, sectors,
                                       names, float(np.log1p(mkt_ret).iloc[-756:].std()))

    L = np.vstack([parts[c] for c in COMPONENTS])
    comp_label = np.concatenate([np.full(len(parts[c]), c) for c in COMPONENTS])

    # earnings jump overlay
    reactions = earnings_reactions(returns, mkt_ret, earnings, names, betas)
    pooled = np.concatenate([v for v in reactions.values() if len(v)] or [np.zeros(1)])
    probs = jump_probabilities(earnings, names, session, h, sc["jump_unknown_date_prob"])
    rj = rngs["jump"]
    occur = rj.random(L.shape) < probs[None, :]
    for j, t in enumerate(names):
        pool = reactions.get(t)
        pool = pool if pool is not None and len(pool) >= 4 else pooled
        draws = pool[rj.integers(0, len(pool), size=L.shape[0])]
        L[:, j] += np.where(occur[:, j], draws, 0.0)

    R = np.expm1(L)
    diag = diagnostics(R, daily, h)
    diag.update({"counts": counts, "historical_windows_used": labels, "backend": be,
                 "jump_prob": dict(zip(names, np.round(probs, 3).tolist()))})
    return {"names": names, "R": R, "component": comp_label, "betas": betas,
            "diagnostics": diag, "warnings": warnings, "backend": be}


def diagnostics(R: np.ndarray, daily: np.ndarray, h: int) -> dict:
    X = np.expm1(_horizon_windows(daily, h))
    ew = R.mean(axis=1)
    out = {"n_scenarios": int(R.shape[0]), "n_names": int(R.shape[1]),
           "mean_name_mean": float(R.mean(axis=0).mean()),
           "mean_name_sd": float(R.std(axis=0).mean()),
           "realized_name_sd": float(X.std(axis=0).mean()),
           "ew_q01": float(np.quantile(ew, 0.01)), "ew_q05": float(np.quantile(ew, 0.05)),
           "ew_cvar95": float(-ew[ew <= np.quantile(ew, 0.05)].mean())}
    if R.shape[1] > 1:
        cs = np.corrcoef(R, rowvar=False)
        cr = np.corrcoef(X, rowvar=False)
        iu = np.triu_indices_from(cs, 1)
        out["corr_fidelity_mae"] = float(np.nanmean(np.abs(cs[iu] - cr[iu])))
    return out
