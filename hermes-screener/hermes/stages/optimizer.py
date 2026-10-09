"""Stage 6 — optimize_portfolio: scenario Mean-CVaR (Rockafellar–Uryasev), spec §6.

Variables (in this order):  w[N]  z[N] (only for the MILP)  zeta  u[S]  t[N] (turnover)
  minimize   zeta + 1/((1-alpha) S) * sum_s u_s              (CVaR_alpha of loss)
  s.t.       -R_s . w - zeta - u_s <= 0,  u_s >= 0           (scenario rows)
             sum w = 1                                        (fully invested)
             mu . w >= target                                 (frontier point)
             w_min z_i <= w_i <= w_max z_i,  k_min <= sum z <= k_max   (MILP only)
             sector / cluster caps,  turnover L1 <= cap,  w_i <= ADV liquidity cap

The model is built once in a backend-neutral form (`LPModel`, CSR + bounds) and
handed unchanged to HiGHS (scipy.optimize.milp, the CPU path) or cuOpt (optional
GPU path). `fingerprint()` hashes every array, so two backends provably solved the
same model (PERF4 rule).

Frontier (§6.1): K points from min-CVaR to max-E[r], swept as LPs. The point that
maximises E[r]/CVaR subject to CVaR <= cvar_cap is selected (ties: lower CVaR);
the final book is the MILP (cardinality + position floor) at that E[r] target,
stepping down the frontier if the MILP is infeasible there.
"""
from __future__ import annotations

import hashlib
import logging
import math
import time
from dataclasses import dataclass, field

import numpy as np
import scipy
import scipy.sparse as sp
from scipy.optimize import Bounds, LinearConstraint, milp

from .. import backend as bk

log = logging.getLogger(__name__)


@dataclass
class Constraints:
    alpha: float = 0.95
    w_max: float = 0.15
    w_min: float = 0.0
    floor_all: bool = False                   # every name held at >= w_min (restricted re-solve)
    cardinality: tuple[int, int] | None = None
    sector_of: dict = field(default_factory=dict)
    sector_cap: float | None = None
    cluster_of: dict = field(default_factory=dict)
    cluster_cap: float | None = None
    prev_weights: dict = field(default_factory=dict)
    turnover_cap: float | None = None
    liquidity_cap: dict = field(default_factory=dict)   # name -> max weight


@dataclass
class LPModel:
    c: np.ndarray
    A: sp.csr_matrix
    row_lo: np.ndarray
    row_hi: np.ndarray
    x_lo: np.ndarray
    x_hi: np.ndarray
    integrality: np.ndarray
    names: list
    n_scen: int
    idx: dict
    sense: str = "min"
    meta: dict = field(default_factory=dict)

    @property
    def is_mip(self) -> bool:
        return bool(self.integrality.any())


def fingerprint(m: LPModel) -> str:
    h = hashlib.sha256()
    for arr in (m.c, m.A.data, m.A.indices, m.A.indptr, m.row_lo, m.row_hi, m.x_lo, m.x_hi,
                m.integrality):
        a = np.ascontiguousarray(arr)
        h.update(str(a.dtype).encode())
        h.update(a.tobytes())
    h.update("|".join(m.names).encode())
    h.update(repr(sorted(m.meta.items())).encode())
    return h.hexdigest()


def build_model(R: np.ndarray, names: list, cons: Constraints, objective: str = "min_cvar",
                er_target: float | None = None, mip: bool = False) -> tuple[LPModel, list]:
    S, N = R.shape
    mu = R.mean(axis=0)
    warnings: list[str] = []
    prev = {k: float(v) for k, v in (cons.prev_weights or {}).items() if v > 0}
    use_turn = cons.turnover_cap is not None and bool(prev)
    outside = sum(v for k, v in prev.items() if k not in set(names))
    if use_turn and cons.turnover_cap - outside < 0:
        warnings.append(f"turnover cap {cons.turnover_cap:.2f} unattainable: "
                        f"{outside:.2f} of the previous book is outside the candidate set; "
                        "turnover constraint dropped")
        use_turn = False

    iw = np.arange(N)
    nz = N if mip else 0
    iz = np.arange(N, N + nz)
    izeta = N + nz
    iu = np.arange(izeta + 1, izeta + 1 + S)
    it = np.arange(izeta + 1 + S, izeta + 1 + S + (N if use_turn else 0))
    nvar = izeta + 1 + S + len(it)

    c = np.zeros(nvar)
    if objective == "min_cvar":
        c[izeta] = 1.0
        c[iu] = 1.0 / ((1.0 - cons.alpha) * S)
    elif objective == "max_er":
        c[iw] = -mu
    else:
        raise ValueError(objective)

    rows, cols, vals, lo, hi = [], [], [], [], []
    r0 = 0
    # scenario rows: -R w - zeta - u <= 0  (dense block built vectorised)
    rr = np.repeat(np.arange(S), N)
    rows.append(rr)
    cols.append(np.tile(iw, S))
    vals.append(-R.reshape(-1))
    rows.append(np.arange(S))
    cols.append(np.full(S, izeta))
    vals.append(-np.ones(S))
    rows.append(np.arange(S))
    cols.append(iu)
    vals.append(-np.ones(S))
    lo.append(np.full(S, -np.inf))
    hi.append(np.zeros(S))
    r0 = S

    def add_row(c_idx, c_val, l_, h_):
        nonlocal r0
        c_idx = np.asarray(c_idx)
        rows.append(np.full(len(c_idx), r0))
        cols.append(c_idx)
        vals.append(np.asarray(c_val, dtype=float))
        lo.append(np.array([l_]))
        hi.append(np.array([h_]))
        r0 += 1

    add_row(iw, np.ones(N), 1.0, 1.0)                              # budget
    if er_target is not None:
        add_row(iw, mu, er_target, np.inf)                          # E[r] >= target
    if mip:
        kmin, kmax = cons.cardinality or (1, N)
        add_row(iz, np.ones(N), float(kmin), float(kmax))
        for i in range(N):
            add_row([iw[i], iz[i]], [1.0, -cons.w_max], -np.inf, 0.0)
            add_row([iw[i], iz[i]], [1.0, -cons.w_min], 0.0, np.inf)
    for groups, cap in ((cons.sector_of, cons.sector_cap), (cons.cluster_of, cons.cluster_cap)):
        if cap is None or not groups:
            continue
        by: dict = {}
        for i, t in enumerate(names):
            by.setdefault(groups.get(t, f"_none_{t}"), []).append(i)
        for g in sorted(by, key=str):
            members = by[g]
            if len(members) * cons.w_max > cap + 1e-12:
                add_row(iw[members], np.ones(len(members)), -np.inf, cap)
    if use_turn:
        p = np.array([prev.get(t, 0.0) for t in names])
        for i in range(N):
            add_row([iw[i], it[i]], [1.0, -1.0], -np.inf, p[i])
            add_row([iw[i], it[i]], [-1.0, -1.0], -np.inf, -p[i])
        add_row(it, np.ones(N), -np.inf, cons.turnover_cap - outside)

    A = sp.csr_matrix((np.concatenate(vals), (np.concatenate(rows), np.concatenate(cols))),
                      shape=(r0, nvar))
    A.sum_duplicates()
    A.sort_indices()
    x_lo = np.zeros(nvar)
    x_hi = np.full(nvar, np.inf)
    w_hi = np.full(N, cons.w_max)
    for i, t in enumerate(names):
        if t in cons.liquidity_cap:
            w_hi[i] = min(w_hi[i], cons.liquidity_cap[t])
    x_hi[iw] = w_hi
    if cons.floor_all:
        x_lo[iw] = np.minimum(cons.w_min, w_hi)
    if mip:
        x_hi[iz] = 1.0
    x_lo[izeta], x_hi[izeta] = -np.inf, np.inf
    integ = np.zeros(nvar, dtype=np.uint8)
    if mip:
        integ[iz] = 1
    m = LPModel(c=c, A=A, row_lo=np.concatenate(lo), row_hi=np.concatenate(hi), x_lo=x_lo,
                x_hi=x_hi, integrality=integ, names=list(names), n_scen=S,
                idx={"w": iw, "z": iz, "zeta": izeta, "u": iu, "t": it},
                meta={"alpha": cons.alpha, "objective": objective,
                      "er_target": None if er_target is None else float(er_target),
                      "mip": mip})
    return m, warnings


# ------------------------------------------------------------------ solvers

@dataclass
class Solution:
    status: str
    x: np.ndarray | None
    objective: float | None
    solver: str
    seconds: float
    info: dict = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return self.x is not None and self.status in ("optimal", "time_limit_feasible")


def solve_highs(m: LPModel, time_limit: float = 300.0, mip_rel_gap: float = 1e-4) -> Solution:
    t0 = time.perf_counter()
    opts = {"disp": False, "time_limit": float(time_limit)}
    if m.is_mip:
        opts["mip_rel_gap"] = float(mip_rel_gap)
    res = milp(m.c, integrality=m.integrality, bounds=Bounds(m.x_lo, m.x_hi),
               constraints=LinearConstraint(m.A, m.row_lo, m.row_hi), options=opts)
    status = {0: "optimal", 1: "time_limit_feasible" if res.x is not None else "time_limit",
              2: "infeasible", 3: "unbounded"}.get(res.status, "error")
    return Solution(status, res.x, None if res.x is None else float(res.fun),
                    f"highs(scipy {scipy.__version__})", time.perf_counter() - t0,
                    {"message": res.message,
                     "mip_gap": getattr(res, "mip_gap", None)})


def solve_cuopt(m: LPModel, method: str = "barrier", time_limit: float = 300.0,
                tol: float = 1e-6) -> Solution:
    """Optional GPU solve via NVIDIA cuOpt's Python data-model API (not installed on CPU hosts)."""
    from cuopt.linear_programming import data_model, solver, solver_settings  # noqa: PLC0415
    t0 = time.perf_counter()
    dm = data_model.DataModel()
    A = m.A.tocsr()
    dm.set_csr_constraint_matrix(A.data.astype(np.float64), A.indices.astype(np.int32),
                                 A.indptr.astype(np.int32))
    big = 1e30
    dm.set_constraint_lower_bounds(np.where(np.isfinite(m.row_lo), m.row_lo, -big))
    dm.set_constraint_upper_bounds(np.where(np.isfinite(m.row_hi), m.row_hi, big))
    dm.set_objective_coefficients(m.c.astype(np.float64))
    dm.set_variable_lower_bounds(np.where(np.isfinite(m.x_lo), m.x_lo, -big))
    dm.set_variable_upper_bounds(np.where(np.isfinite(m.x_hi), m.x_hi, big))
    dm.set_variable_types(np.where(m.integrality == 1, "I", "C"))
    st = solver_settings.SolverSettings()
    st.set_parameter("time_limit", float(time_limit))
    if not m.is_mip:
        st.set_parameter("method", {"pdlp": 1, "dual_simplex": 2, "barrier": 3}.get(method, 0))
        st.set_optimality_tolerance(tol)
    sol = solver.Solve(dm, st)
    status = str(sol.get_termination_reason()).lower()
    x = np.asarray(sol.get_primal_solution(), dtype=np.float64)
    ok = "optimal" in status or "feasible" in status
    return Solution("optimal" if "optimal" in status else
                    ("time_limit_feasible" if ok else status),
                    x if ok else None, float(sol.get_primal_objective()) if ok else None,
                    f"cuopt_{method}", time.perf_counter() - t0, {"termination": status})


class SolverPolicy:
    """Routes models to HiGHS or cuOpt per infra.backend.solver (cpu | gpu | auto)."""

    def __init__(self, cfg):
        self.oc = cfg.strategy["optimizer"]
        self.mode = cfg.infra["backend"]["solver"]
        self.threshold = float(cfg.infra["backend"]["gpu_min_lp_nnz"])
        self.gpu_ok = bk.cuopt_available() if self.mode != "cpu" else False
        self.log: list[dict] = []

    def solve(self, m: LPModel, purpose: str) -> Solution:
        be = bk.choose(self.mode, m.A.nnz, self.threshold, self.gpu_ok)
        tl, gap = self.oc["time_limit_s"], self.oc["mip_rel_gap"]
        if be == "gpu":
            method = (self.oc["solver"]["sweep"] if purpose == "sweep"
                      else self.oc["solver"]["primary"]).replace("cuopt_", "")
            try:
                sol = solve_cuopt(m, method, tl, self.oc["solver"]["tol"])
            except Exception as e:  # noqa: BLE001 — whole-solve CPU restart
                sol = solve_highs(m, tl, gap)
                sol.info["gpu_failure"] = f"{type(e).__name__}: {e}"
        else:
            sol = solve_highs(m, tl, gap)
        self.log.append({"purpose": purpose, "solver": sol.solver, "status": sol.status,
                         "seconds": round(sol.seconds, 3), "rows": m.A.shape[0],
                         "cols": m.A.shape[1], "nnz": int(m.A.nnz), "mip": m.is_mip,
                         "fingerprint": fingerprint(m)[:16]})
        return sol


# ------------------------------------------------------------------ risk math

def portfolio_risk(R: np.ndarray, w: np.ndarray, alpha: float) -> dict:
    loss = -(R @ w)
    S = len(loss)
    k = max(1, int(math.ceil(round((1 - alpha) * S, 9))))
    order = np.argsort(-loss, kind="stable")
    tail = order[:k]
    var = float(loss[order[k - 1]])
    cvar = float(loss[tail].mean())
    comp = w * (-R[tail]).mean(axis=0)            # Euler allocation; sums to CVaR
    worst = int(order[0])
    return {"er": float((R @ w).mean()), "var": var, "cvar": cvar, "component_cvar": comp,
            "tail_index": tail, "worst_scenario": worst}


def _weights(sol: Solution, m: LPModel) -> np.ndarray:
    w = np.clip(sol.x[m.idx["w"]], 0.0, None)
    w[w < 1e-7] = 0.0
    return w / w.sum()


# ------------------------------------------------------------------ frontier

def frontier(R: np.ndarray, names: list, cons: Constraints, policy: SolverPolicy, K: int
             ) -> tuple[list[dict], list[str]]:
    warns: list[str] = []
    m0, w0 = build_model(R, names, cons, "min_cvar")
    warns += w0
    s0 = policy.solve(m0, "sweep")
    if not s0.ok:
        return [], warns + [f"min-CVaR LP {s0.status}"]
    m1, _ = build_model(R, names, cons, "max_er")
    s1 = policy.solve(m1, "sweep")
    mu = R.mean(axis=0)
    er_lo = float(mu @ _weights(s0, m0))
    er_hi = float(mu @ _weights(s1, m1)) if s1.ok else er_lo
    pts = []
    for k, tgt in enumerate(np.linspace(er_lo, er_hi, K)):
        tgt = float(tgt) - 1e-9 * (k == K - 1)
        m, _ = build_model(R, names, cons, "min_cvar", er_target=tgt)
        s = s0 if k == 0 else policy.solve(m, "sweep")
        if not s.ok:
            continue
        w = _weights(s, m0 if k == 0 else m)
        rk = portfolio_risk(R, w, cons.alpha)
        pts.append({"k": k, "er_target": tgt, "er": rk["er"], "cvar": rk["cvar"], "w": w})
    return pts, warns


def select_point(pts: list[dict], cvar_cap: float) -> tuple[dict | None, list[str]]:
    ok = [p for p in pts if p["cvar"] <= cvar_cap]
    if not ok:
        best = min(pts, key=lambda p: (p["cvar"], p["k"])) if pts else None
        return best, ["cvar_cap_unattainable: no frontier point has CVaR <= cap; "
                      "min-CVaR point selected"]

    def ratio(p):
        return p["er"] / p["cvar"] if p["cvar"] > 1e-12 else math.copysign(1e12, p["er"])
    return max(ok, key=lambda p: (ratio(p), -p["cvar"])), []


def optimize_portfolio(cfg, R: np.ndarray, names: list, cons: Constraints, mip: bool = True,
                       policy: SolverPolicy | None = None) -> dict:
    oc = cfg.strategy["optimizer"]
    policy = policy or SolverPolicy(cfg)
    pts, warns = frontier(R, names, cons, policy, int(oc["frontier_points"]))
    if not pts:
        return {"status": "infeasible", "warnings": warns, "solver_log": policy.log}
    sel, w2 = select_point(pts, float(oc["cvar_cap"]))
    warns += w2
    final_sol, final_m, used_target = None, None, None
    if mip:
        targets = sorted({p["er_target"] for p in pts if p["er_target"] <= sel["er_target"]},
                         reverse=True) + [None]
        for tgt in targets:
            m, _ = build_model(R, names, cons, "min_cvar", er_target=tgt, mip=True)
            s = policy.solve(m, "final")
            if s.ok:
                final_sol, final_m, used_target = s, m, tgt
                break
        if final_sol is None:
            return {"status": "infeasible", "warnings": warns + ["final MILP infeasible"],
                    "solver_log": policy.log}
        if used_target != sel["er_target"]:
            warns.append("final MILP infeasible at the selected frontier target; stepped "
                         f"down to {'no E[r] floor' if used_target is None else f'{used_target:.4f}'}")
        if final_sol.status != "optimal":
            warns.append(f"final MILP {final_sol.status}")
    else:
        final_m, _ = build_model(R, names, cons, "min_cvar", er_target=sel["er_target"])
        final_sol = policy.solve(final_m, "final")
        used_target = sel["er_target"]
        if not final_sol.ok:
            return {"status": "infeasible", "warnings": warns + ["final LP infeasible"],
                    "solver_log": policy.log}
    w = _weights(final_sol, final_m)

    crosscheck = None
    if not final_sol.solver.startswith("highs"):
        ref = solve_highs(final_m, oc["time_limit_s"], oc["mip_rel_gap"])
        if ref.ok:
            w_ref = _weights(ref, final_m)
            dev = float(np.max(np.abs(w - w_ref)))
            crosscheck = {"solver": ref.solver, "linf": dev, "tol": oc["crosscheck_tol"],
                          "agree": dev <= oc["crosscheck_tol"]}
            if not crosscheck["agree"]:
                warns.append("solver_disagreement: HiGHS solution reported")
                w = w_ref

    rk = portfolio_risk(R, w, cons.alpha)
    held = [i for i in range(len(names)) if w[i] > 0]
    book = {names[i]: round(float(w[i]), 6) for i in sorted(held, key=lambda i: (-w[i], names[i]))}
    return {"status": final_sol.status, "weights": book, "er": rk["er"], "var": rk["var"],
            "cvar": rk["cvar"], "alpha": cons.alpha,
            "marginal_cvar": {names[i]: float(rk["component_cvar"][i]) for i in held},
            "worst_scenario": rk["worst_scenario"],
            "selected_point": {"k": sel["k"], "er": sel["er"], "cvar": sel["cvar"],
                               "er_target_used": used_target},
            "frontier": [{"k": p["k"], "er": round(p["er"], 6), "cvar": round(p["cvar"], 6)}
                         for p in pts],
            "fingerprint": fingerprint(final_m), "crosscheck": crosscheck,
            "solver_log": policy.log, "warnings": warns}
