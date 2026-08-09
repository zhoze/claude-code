import numpy as np
import pandas as pd

from quant_screener.portfolio.cvar import efficient_frontier, solve_mean_cvar
from quant_screener.portfolio.stress import portfolio_metrics
from quant_screener.ml.validation import walk_forward_folds


def _scenarios(n_assets=3, n_days=500, seed=1):
    rng = np.random.default_rng(seed)
    cov = np.full((n_assets, n_assets), 0.00005) + np.eye(n_assets) * 0.0002
    return rng.multivariate_normal([0.0006, 0.0003, -0.0002][:n_assets], cov, n_days)


def test_mean_cvar_lp_solves():
    scen = _scenarios()
    tickers = ["A", "B", "C"]
    expected = pd.Series({"A": 0.02, "B": 0.01, "C": -0.005})
    sol = solve_mean_cvar(scen, tickers, expected, lam=2.0, alpha=0.95, max_weight=0.4)
    assert sol.weights.sum() <= 1.0 + 1e-6
    assert (sol.weights >= -1e-9).all()
    assert (sol.weights <= 0.4 + 1e-6).all()
    assert sol.cvar >= 0
    # negative-expectation asset should get ~no weight
    assert sol.weights["C"] < 0.05
    assert sol.contributions is not None


def test_frontier_orders_by_lambda(cfg):
    scen = _scenarios()
    expected = pd.Series({"A": 0.02, "B": 0.01, "C": -0.005})
    fr = efficient_frontier(scen, ["A", "B", "C"], expected, cfg)
    assert len(fr.solutions) == len(cfg.portfolio.lambda_grid)
    assert fr.max_return.expected_return >= fr.min_cvar.expected_return - 1e-9
    assert fr.min_cvar.cvar <= fr.max_return.cvar + 1e-9
    bal = fr.balanced
    assert bal in fr.solutions


def test_sector_cap_respected():
    scen = _scenarios()
    expected = pd.Series({"A": 0.03, "B": 0.03, "C": 0.001})
    sol = solve_mean_cvar(scen, ["A", "B", "C"], expected, lam=0.5, alpha=0.95,
                          max_weight=0.5, sector_map={"A": "Tech", "B": "Tech", "C": "Util"},
                          max_sector_weight=0.6)
    assert sol.weights[["A", "B"]].sum() <= 0.6 + 1e-6


def test_portfolio_metrics_keys():
    rng = np.random.default_rng(3)
    r = pd.Series(rng.normal(0.0005, 0.01, 400))
    m = portfolio_metrics(r)
    for k in ("MAX_DRAWDOWN", "VOLATILITY", "VAR", "CVAR", "SHARPE", "SORTINO",
              "CALMAR", "WIN_RATE", "PROFIT_FACTOR"):
        assert k in m


def test_walk_forward_folds_are_time_ordered():
    idx = pd.RangeIndex(1500)
    folds = walk_forward_folds(idx, min_train=750, val_len=63, test_len=21, horizon=5)
    assert len(folds) >= 6
    for f in folds:
        assert f.train.max() < f.val.min()   # embargo gap enforced
        assert f.val.max() < f.test.min()
