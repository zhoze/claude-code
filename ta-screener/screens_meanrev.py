"""Mean-reversion / stat-arb screens (10) for the ta-screener 150 catalog.

Includes the Avellaneda-Lee residual OU "s-score" machinery (sector-ETF and PCA
variants) in closed form on rolling windows: residual returns -> cumulated process X
-> AR(1) fit within the window -> OU equilibrium (m, sigma_eq) -> s = (X - m)/sigma_eq.
Contrarian orientation: scores are -s (or the negated stretch measure), so higher =
more attractively oversold. NaN = cannot evaluate (incl. AR(1) coefficient outside
(0, 1), where the OU fit is meaningless).
"""
from __future__ import annotations

import numpy as np
import pandas as pd

import ops
from panel import Panel, load_config, synthetic_panel
from screen_lib import MissingInputError, ScreenSpec


# ------------------------------------------------------------------ OU machinery

def _ou_sscore_from_eps(eps: pd.DataFrame, window: int) -> pd.DataFrame:
    """s-score of an OU fit on X = rolling-window cumulated residuals."""
    X = eps.rolling(window, min_periods=window).sum()
    Xl = X.shift(1)
    var_l = ops.stddev(Xl, window) ** 2
    b = ops.clean(ops.covariance(X, Xl, window) / var_l)
    mean_X = X.rolling(window, min_periods=window).mean()
    mean_l = Xl.rolling(window, min_periods=window).mean()
    a = mean_X - b * mean_l
    var_X = ops.stddev(X, window) ** 2
    var_z = (var_X - b ** 2 * var_l).clip(lower=0.0)
    m = ops.clean(a / (1.0 - b))
    # negative variance ratios only occur where b is outside (0,1) — masked below;
    # clip keeps sqrt quiet, the resulting inf/0 artifacts are cleaned to NaN.
    sigma_eq = ops.clean(np.sqrt(ops.clean(var_z / (1.0 - b ** 2)).clip(lower=0.0)))
    s = ops.clean((X - m) / sigma_eq)
    return s.where((b > 0) & (b < 1))          # mask: NaN b -> False -> NaN (intended)


def _residual_eps(r: pd.DataFrame, f: pd.DataFrame, window: int) -> pd.DataFrame:
    """Residual daily returns from a rolling one-factor regression of r on f."""
    var_f = ops.stddev(f, window) ** 2
    beta = ops.clean(ops.covariance(r, f, window) / var_f)
    return r - beta * f


def _market_frame(panel: Panel, cfg: dict, like: pd.DataFrame) -> pd.DataFrame:
    m = panel.market_returns(cfg).reindex(like.index)
    return pd.DataFrame(np.tile(m.to_numpy(dtype=float)[:, None], (1, like.shape[1])),
                        index=like.index, columns=like.columns)


def _sector_factor_frame(panel: Panel, cfg: dict, like: pd.DataFrame) -> pd.DataFrame:
    ser = panel.sector_etf_returns(cfg).reindex(like.index)
    sect = panel.sectors()
    cols = {}
    for t in like.columns:
        s = sect.get(t)
        cols[t] = ser[s] if s in ser.columns else pd.Series(np.nan, index=like.index)
    return pd.DataFrame(cols)


# ------------------------------------------------------------------ screens

def str_reversal_5d(panel: Panel, cfg: dict) -> pd.DataFrame:
    d = cfg["meanrev"]["reversal_days"]
    ac = panel.adjclose
    return -(ops.clean(ac / ops.delay(ac, d)) - 1.0)


def weekly_reversal(panel: Panel, cfg: dict) -> pd.DataFrame:
    """Return over [t-5, t-1] (skipping the most recent day), negated."""
    ac = panel.adjclose
    return -(ops.clean(ops.delay(ac, 1) / ops.delay(ac, 5)) - 1.0)


def al_sscore_sector(panel: Panel, cfg: dict) -> pd.DataFrame:
    mc = cfg["meanrev"]
    r = panel.returns
    f = _sector_factor_frame(panel, cfg, r)
    if f.isna().all().all():
        raise MissingInputError("no sector ETF factor returns available")
    eps = _residual_eps(r, f, mc["ou_window"])
    return -_ou_sscore_from_eps(eps, mc["ou_window"])


def pca_sscore(panel: Panel, cfg: dict) -> pd.DataFrame:
    """PCA-residual s-score: project standardized returns off the top-k eigenvectors.

    Eigenvectors are re-estimated every 21 trading days from the trailing pca_window
    correlation matrix (estimation window strictly precedes the scored block — causal).
    Missing returns enter the projection as 0 (documented approximation); the
    residual itself stays NaN wherever the stock's own return is NaN.
    """
    mc = cfg["meanrev"]
    k, window, refresh = mc["pca_k"], mc["pca_window"], 21
    r = panel.returns
    resid = pd.DataFrame(np.nan, index=r.index, columns=r.columns)
    for start in range(window, len(r.index), refresh):
        est = r.iloc[start - window:start]
        ok = est.notna().sum() >= int(window * 0.9)
        cols = est.columns[ok]
        if len(cols) <= k:
            continue
        E = est[cols]
        mu, sd = E.mean(), E.std().replace(0.0, np.nan)
        Z = (E - mu) / sd
        C = Z.corr(min_periods=int(window * 0.8)).fillna(0.0).to_numpy()
        _w, V = np.linalg.eigh(C)
        Q = V[:, -k:]
        block = slice(start, min(start + refresh, len(r.index)))
        Zb = (r.iloc[block][cols] - mu) / sd
        proj = Zb.fillna(0.0).to_numpy() @ Q @ Q.T
        res = Zb.to_numpy() - proj
        col_idx = [r.columns.get_loc(c) for c in cols]
        resid.iloc[block, col_idx] = res
    return -_ou_sscore_from_eps(resid, mc["ou_window"])


def rsi2(panel: Panel, cfg: dict) -> pd.DataFrame:
    n = cfg["meanrev"]["rsi_days"]
    ch = ops.delta(panel.adjclose, 1)
    gain = ch.clip(lower=0.0)
    loss = (-ch).clip(lower=0.0)
    avg_gain = gain.ewm(alpha=1.0 / n, adjust=False, min_periods=n).mean()
    avg_loss = loss.ewm(alpha=1.0 / n, adjust=False, min_periods=n).mean()
    rs = ops.clean(avg_gain / avg_loss)
    rsi = 100.0 - 100.0 / (1.0 + rs)
    rsi = rsi.where(~(avg_loss == 0) | ch.isna(), 100.0)   # all-gain window -> RSI 100
    return -(rsi.mask(ch.isna()))


def bollinger_pctb_rev(panel: Panel, cfg: dict) -> pd.DataFrame:
    mc = cfg["meanrev"]
    d, sig = mc["bb_days"], mc["bb_sigma"]
    c = panel.adjclose
    mid = c.rolling(d, min_periods=d).mean()
    sd = ops.stddev(c, d)
    pctb = ops.clean((c - (mid - sig * sd)) / (2.0 * sig * sd))
    return -pctb


def gap_fade(panel: Panel, cfg: dict) -> pd.DataFrame:
    """Fade the overnight gap when it exceeds gap_min (delay-0 flavor: trade near open)."""
    g = ops.clean(panel.open / ops.delay(panel.close, 1)) - 1.0
    cond = ops.ge(abs(g), cfg["meanrev"]["gap_min"])
    return ops.where(cond, -g, float("nan"))


def drawdown_rebound(panel: Panel, cfg: dict) -> pd.DataFrame:
    mc = cfg["meanrev"]
    c = panel.adjclose
    dd = ops.clean(c / ops.ts_max(c, mc["dd_days"])) - 1.0
    stab = ops.clean(c / ops.delay(c, mc["stabilize_days"])) - 1.0
    return ops.where(ops.gt(stab, 0.0), -dd, float("nan"))


def ibs(panel: Panel, cfg: dict) -> pd.DataFrame:
    return -ops.clean((panel.close - panel.low) / (panel.high - panel.low))


def ou_threshold(panel: Panel, cfg: dict) -> pd.DataFrame:
    """Positive only beyond the Bertram-style entry band: score = -s - |s_entry|."""
    mc = cfg["meanrev"]
    r = panel.returns
    f = _market_frame(panel, cfg, r)
    eps = _residual_eps(r, f, mc["ou_window"])
    s = _ou_sscore_from_eps(eps, mc["ou_window"])
    return -s - abs(mc["s_entry"])


SPECS = [
    ScreenSpec(key="str_reversal_5d", family="meanrev", title="Short-term 5-day reversal",
               citation="Jegadeesh (1990), JF 45; Lehmann (1990)", arxiv=None,
               runner=str_reversal_5d, validation=1.0, us_applicability=0.9,
               persistence=0.3, overfit_risk=0.2, perf_bucket=0.6, turnover="high"),
    ScreenSpec(key="weekly_reversal", family="meanrev", title="Weekly reversal (skip last day)",
               citation="Lehmann (1990), QJE 105", arxiv=None,
               runner=weekly_reversal, validation=0.9, us_applicability=0.9,
               persistence=0.3, overfit_risk=0.2, perf_bucket=0.4, turnover="high"),
    ScreenSpec(key="al_sscore_sector", family="meanrev",
               title="Avellaneda-Lee s-score vs sector ETF",
               citation="Avellaneda & Lee (2010), Quantitative Finance 10(7)", arxiv=None,
               runner=al_sscore_sector, validation=0.9, us_applicability=1.0,
               persistence=0.5, overfit_risk=0.4, perf_bucket=0.8, turnover="high",
               needs=("benchmarks", "sector"),
               notes="PCA/ETF stat-arb Sharpe 1.44 (1997-2007) published."),
    ScreenSpec(key="pca_sscore", family="meanrev", title="PCA-residual s-score",
               citation="Avellaneda & Lee (2010), Quantitative Finance 10(7)", arxiv=None,
               runner=pca_sscore, validation=0.9, us_applicability=1.0,
               persistence=0.5, overfit_risk=0.4, perf_bucket=0.8, turnover="high"),
    ScreenSpec(key="rsi2", family="meanrev", title="RSI(2) oversold",
               citation="Connors & Alvarez (2009); indicator set of arXiv:2310.09903",
               arxiv=None, runner=rsi2, validation=0.5, us_applicability=0.8,
               persistence=0.4, overfit_risk=0.4, perf_bucket=0.4, turnover="high"),
    ScreenSpec(key="bollinger_pctb_rev", family="meanrev", title="Bollinger %B reversion",
               citation="Bollinger; feature set of Moodi & Jahangard-Rafsanjani",
               arxiv="2310.09903", runner=bollinger_pctb_rev, validation=0.6,
               us_applicability=0.8, persistence=0.4, overfit_risk=0.3, perf_bucket=0.4,
               turnover="high"),
    ScreenSpec(key="gap_fade", family="meanrev", title="Overnight gap fade",
               citation="Berkman, Koch, Tuttle, Zhang (2012) overnight returns", arxiv=None,
               runner=gap_fade, validation=0.6, us_applicability=0.8, persistence=0.4,
               overfit_risk=0.3, perf_bucket=0.4, turnover="high",
               notes="delay-0 flavor: assumes execution near the open; conditional coverage."),
    ScreenSpec(key="drawdown_rebound", family="meanrev", title="Drawdown rebound (stabilized)",
               citation="De Bondt & Thaler (1985), JF 40 (overreaction)", arxiv=None,
               runner=drawdown_rebound, validation=0.5, us_applicability=0.8,
               persistence=0.4, overfit_risk=0.4, perf_bucket=0.4, turnover="medium",
               notes="conditional: only names with positive short-term stabilization."),
    ScreenSpec(key="ibs", family="meanrev", title="Internal Bar Strength",
               citation="Pagonidis; cautionary context arXiv:2412.15448", arxiv=None,
               runner=ibs, validation=0.5, us_applicability=0.8, persistence=0.4,
               overfit_risk=0.3, perf_bucket=0.4, turnover="high"),
    ScreenSpec(key="ou_threshold", family="meanrev", title="OU optimal entry threshold",
               citation="Bertram (2010); optimal OU thresholds", arxiv="2003.10502",
               runner=ou_threshold, validation=0.6, us_applicability=0.7, persistence=0.5,
               overfit_risk=0.4, perf_bucket=0.6, turnover="high",
               needs=("benchmarks",),
               notes="score > 0 only beyond the entry band; mostly negative otherwise."),
]

# Screens whose conditionality legitimately thins coverage on the synthetic panel.
CONDITIONAL = {"gap_fade", "drawdown_rebound"}


def selftest() -> int:
    panel = synthetic_panel()
    cfg = load_config()
    bad, fallback = [], []
    for spec in SPECS:
        try:
            spec.validate()
            score = spec.runner(panel, cfg)
            assert isinstance(score, pd.DataFrame) and score.shape == panel.close.shape
            cov = score.iloc[-1].notna().mean()
            if cov >= 0.25:
                print(f"  ok {spec.key} (cov {cov:.0%})")
            else:
                recent = score.iloc[-60:].notna().sum(axis=1).max()
                assert spec.key in CONDITIONAL and recent >= 2, \
                    f"coverage {cov:.0%} and best recent {recent} names"
                fallback.append(spec.key)
                print(f"  ok {spec.key} (conditional fallback: best recent {recent} names)")
        except Exception as e:  # noqa: BLE001
            bad.append((spec.key, str(e)))
    if bad:
        for k, m in bad:
            print(f"  FAIL {k}: {m}")
        return 1
    print(f"screens_meanrev.py selftest: OK — {len(SPECS)} screens; fallback: {fallback}")
    return 0


if __name__ == "__main__":
    import sys
    if "--selftest" in sys.argv:
        sys.exit(selftest())
    print(__doc__)
