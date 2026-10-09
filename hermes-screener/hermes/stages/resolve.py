"""Stage 9 — resolve_final: restricted re-solve over the triggered names (invariant I2).

Nothing downstream of the optimizer changes the portfolio silently. The LP is
re-solved restricted to S = triggered, non-vetoed book names, with the book's
constraints minus cardinality: every name in S is held (w_i >= w_min). The caps
are relaxed only as far as feasibility needs (agreed policy), and each relaxation
is reported:
  - position cap -> max(w_max, 1/|S|)   (sum w = 1 needs at least 1/w_max names)
  - sector and cluster caps -> raised in `resolve_relax_step` steps until feasible
The turnover constraint belongs to the full book and is not applied here.
The reported CVaR is the restricted basket's, never the full book's.
"""
from __future__ import annotations

import math
from dataclasses import replace

from .optimizer import Constraints, SolverPolicy, optimize_portfolio


def resolve_final(cfg, R, names: list, book: dict, triggers: list, base: Constraints,
                  exposure_gate: float, policy: SolverPolicy | None = None) -> dict:
    oc = cfg.strategy["optimizer"]
    S = [t for t in names if t in triggers and t in book]
    warnings, relaxations = [], []
    if not S:
        return {"status": "no_entries", "final_entries": {}, "basket": None,
                "relaxations": [], "warnings": ["no triggered names — no entries today"]}
    if len(S) < int(oc["min_entries"]):
        warnings.append(f"fewer_than_{oc['min_entries']}_entries: {len(S)}")
    idx = [names.index(t) for t in S]
    Rs = R[:, idx]
    w_max = base.w_max
    if len(S) * w_max < 1.0 - 1e-12:
        w_max = math.ceil(1e6 / len(S)) / 1e6
        relaxations.append(f"position cap {base.w_max:.2%} -> {w_max:.2%} (|S|={len(S)})")
    cons = replace(base, w_max=w_max, floor_all=True, cardinality=None,
                   turnover_cap=None, prev_weights={})
    sec, clu = base.sector_cap, base.cluster_cap
    step = float(oc["resolve_relax_step"])
    policy = policy or SolverPolicy(cfg)
    while True:
        cons = replace(cons, sector_cap=sec, cluster_cap=clu)
        res = optimize_portfolio(cfg, Rs, S, cons, mip=False, policy=policy)
        if res["status"] != "infeasible":
            break
        if (sec is None or sec >= 1.0) and (clu is None or clu >= 1.0):
            return {"status": "infeasible", "final_entries": {}, "basket": None,
                    "relaxations": relaxations, "warnings": warnings + res["warnings"]}
        if sec is not None and sec < 1.0:
            sec = min(1.0, round(sec + step, 6))
        if clu is not None and clu < 1.0:
            clu = min(1.0, round(clu + step, 6))
    if sec != base.sector_cap:
        relaxations.append(f"sector cap {base.sector_cap:.0%} -> {sec:.0%}")
    if clu != base.cluster_cap:
        relaxations.append(f"cluster cap {base.cluster_cap:.0%} -> {clu:.0%}")
    final = {t: round(w * exposure_gate, 6) for t, w in res["weights"].items()}
    return {"status": res["status"], "final_entries": final, "basket": res,
            "exposure_gate": exposure_gate, "relaxations": relaxations,
            "warnings": warnings + res["warnings"]}
