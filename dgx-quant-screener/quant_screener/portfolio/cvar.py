"""Scenario-based Mean-CVaR optimization via NVIDIA cuOpt (spec §8, §10).

Rockafellar–Uryasev LP formulation:

    max  mu'w - lambda * ( zeta + (1/((1-alpha)*S)) * sum_s u_s )
    s.t. u_s >= -r_s'w - zeta        for every scenario s
         u_s >= 0
         0 <= w_i <= max_weight
         sum(w) <= 1                 (long-only, cash allowed)
         sector weight caps

Solved with cuOpt's LP solver on the DGX Spark GPU when available, otherwise
SciPy HiGHS — the LP is identical either way.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from .. import gpu

log = logging.getLogger(__name__)


@dataclass
class CVaRSolution:
    weights: pd.Series
    cash: float
    expected_return: float
    cvar: float                     # positive number = loss magnitude at alpha
    lam: float
    alpha: float
    solver: str = "highs"
    contributions: pd.DataFrame | None = None


def _solve_lp(c, A_ub, b_ub, bounds):
    """Returns (x, solver_name). Tries cuOpt first, falls back to HiGHS."""
    if gpu.HAS_CUOPT:
        try:
            x = _solve_cuopt(c, A_ub, b_ub, bounds)
            if x is not None:
                return x, "cuopt"
        except Exception as e:  # pragma: no cover
            log.warning("cuOpt solve failed (%s); falling back to HiGHS", e)
    from scipy.optimize import linprog

    res = linprog(c, A_ub=A_ub, b_ub=b_ub, bounds=bounds, method="highs")
    if not res.success:
        raise RuntimeError(f"LP infeasible: {res.message}")
    return res.x, "highs"


def _solve_cuopt(c, A_ub, b_ub, bounds):  # pragma: no cover - GPU only
    """cuOpt >= 25.x linear_programming DataModel path."""
    from cuopt.linear_programming import data_model, solver, solver_settings
    from scipy.sparse import csr_matrix

    A = csr_matrix(A_ub)
    dm = data_model.DataModel()
    dm.set_csr_constraint_matrix(A.data, A.indices, A.indptr)
    dm.set_constraint_bounds(np.asarray(b_ub, dtype=np.float64))
    dm.set_row_types(np.array(["L"] * len(b_ub)))
    dm.set_objective_coefficients(np.asarray(c, dtype=np.float64))
    lo = np.array([b[0] if b[0] is not None else -1e20 for b in bounds])
    hi = np.array([b[1] if b[1] is not None else 1e20 for b in bounds])
    dm.set_variable_lower_bounds(lo)
    dm.set_variable_upper_bounds(hi)
    ss = solver_settings.SolverSettings()
    ss.set_optimality_tolerance(1e-7)
    sol = solver.Solve(dm, ss)
    status = sol.get_termination_status()
    if str(status).lower().find("optimal") < 0 and int(getattr(status, "value", 1)) != 1:
        return None
    return np.asarray(sol.get_primal_solution())


def solve_mean_cvar(scenarios: np.ndarray, tickers: list[str], expected: pd.Series,
                    lam: float, alpha: float, max_weight: float,
                    sum_weights_max: float = 1.0,
                    sector_map: dict[str, str] | None = None,
                    max_sector_weight: float | None = None) -> CVaRSolution:
    """Variables: [w_1..w_n, zeta, u_1..u_S]. Minimize
    -mu'w + lam*(zeta + su * sum(u))."""
    S, n = scenarios.shape
    mu = expected[tickers].to_numpy()
    su = 1.0 / ((1.0 - alpha) * S)

    c = np.concatenate([-mu, [lam], np.full(S, lam * su)])
    # -r_s'w - zeta - u_s <= 0
    A = np.zeros((S + 1, n + 1 + S))
    A[:S, :n] = -scenarios
    A[:S, n] = -1.0
    A[np.arange(S), n + 1 + np.arange(S)] = -1.0
    b = np.zeros(S + 1)
    A[S, :n] = 1.0          # sum(w) <= budget
    b[S] = sum_weights_max
    rows_A, rows_b = [A], [b]
    if sector_map and max_sector_weight:
        for sector in sorted({sector_map.get(t, "UNKNOWN") for t in tickers}):
            row = np.zeros(n + 1 + S)
            for i, t in enumerate(tickers):
                if sector_map.get(t, "UNKNOWN") == sector:
                    row[i] = 1.0
            rows_A.append(row.reshape(1, -1))
            rows_b.append(np.array([max_sector_weight]))
    A_ub = np.vstack(rows_A)
    b_ub = np.concatenate(rows_b)
    bounds = [(0.0, max_weight)] * n + [(None, None)] + [(0.0, None)] * S

    x, solver_name = _solve_lp(c, A_ub, b_ub, bounds)
    w = pd.Series(np.round(x[:n], 6), index=tickers)
    zeta = x[n]
    u = x[n + 1:]
    cvar = float(zeta + su * u.sum())
    port_ret = float(mu @ w.to_numpy())

    losses = -(scenarios @ w.to_numpy())
    marginal = []
    tail_mask = losses >= np.quantile(losses, alpha)
    for i, t in enumerate(tickers):
        contrib_cvar = float((-scenarios[tail_mask, i] * w[t]).mean()) if tail_mask.any() else np.nan
        marginal.append({"ticker": t, "weight": w[t],
                         "expected_return_contribution": float(mu[i] * w[t]),
                         "cvar_contribution": contrib_cvar})
    return CVaRSolution(weights=w, cash=float(max(0.0, sum_weights_max - w.sum())),
                        expected_return=port_ret, cvar=cvar, lam=lam, alpha=alpha,
                        solver=solver_name,
                        contributions=pd.DataFrame(marginal).set_index("ticker"))


@dataclass
class Frontier:
    solutions: list[CVaRSolution] = field(default_factory=list)

    @property
    def max_return(self) -> CVaRSolution:
        return max(self.solutions, key=lambda s: s.expected_return)

    @property
    def min_cvar(self) -> CVaRSolution:
        return min(self.solutions, key=lambda s: s.cvar)

    @property
    def balanced(self) -> CVaRSolution:
        """Best return-per-CVaR tradeoff (max slope vs min-CVaR anchor)."""
        anchor = self.min_cvar
        def slope(s: CVaRSolution) -> float:
            dc = s.cvar - anchor.cvar
            return (s.expected_return - anchor.expected_return) / dc if dc > 1e-9 else -np.inf
        candidates = [s for s in self.solutions if s is not anchor]
        best = max(candidates, key=slope, default=anchor)
        return best if slope(best) > 0 else anchor


def efficient_frontier(scenarios: np.ndarray, tickers: list[str], expected: pd.Series,
                       cfg, sector_map: dict[str, str] | None = None) -> Frontier:
    fr = Frontier()
    for lam in cfg.portfolio.lambda_grid:
        try:
            fr.solutions.append(solve_mean_cvar(
                scenarios, tickers, expected, lam=float(lam),
                alpha=float(cfg.portfolio.cvar_alpha),
                max_weight=float(cfg.portfolio.max_weight),
                sum_weights_max=float(cfg.portfolio.sum_weights_max),
                sector_map=sector_map,
                max_sector_weight=float(cfg.portfolio.max_sector_weight)))
        except Exception as e:
            log.warning("Mean-CVaR solve failed for lambda=%s: %s", lam, e)
    return fr
