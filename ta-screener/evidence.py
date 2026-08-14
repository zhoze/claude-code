"""Evidence scoring: probability-of-success x profitability per screen.

The "top 150" ordering is deterministic metadata, not a backtest: every ScreenSpec
carries rubric values encoding its published evidence (see screen_lib docstring and
README for the rubric). This module turns those into the composite ranking score:

    p_success     = 0.25*validation + 0.25*us_applicability
                  + 0.25*persistence + 0.25*(1 - overfit_risk)          in [0, 1]
    profitability = perf_bucket * turnover_multiplier[turnover]          in [0, 1]
    composite     = p_success * profitability

The optional empirical RankIC layer (empirical.py) is reported ALONGSIDE the composite
(as `adjusted_score`), never silently substituted for it — the base ordering stays
reproducible from metadata alone.
"""
from __future__ import annotations

from screen_lib import ScreenSpec


def p_success(spec: ScreenSpec) -> float:
    return round(0.25 * spec.validation + 0.25 * spec.us_applicability
                 + 0.25 * spec.persistence + 0.25 * (1.0 - spec.overfit_risk), 4)


def profitability(spec: ScreenSpec, cfg: dict) -> float:
    mult = cfg["ranking"]["turnover_multipliers"][spec.turnover]
    return round(spec.perf_bucket * mult, 4)


def composite(spec: ScreenSpec, cfg: dict) -> float:
    return round(p_success(spec) * profitability(spec, cfg), 4)


def adjusted_score(comp: float, ic_tstat_pct_rank: float | None) -> float | None:
    """Empirical tilt: composite x (0.5 + 0.5 * pct-rank of the screen's IC t-stat)."""
    if ic_tstat_pct_rank is None:
        return None
    return round(comp * (0.5 + 0.5 * ic_tstat_pct_rank), 4)


def selftest() -> int:
    from panel import load_config
    cfg = load_config()
    spec = ScreenSpec(key="probe", family="momentum", title="p", citation="c", arxiv=None,
                      runner=lambda panel, cfg: None, validation=1.0, us_applicability=1.0,
                      persistence=0.6, overfit_risk=0.2, perf_bucket=0.8, turnover="low")
    ps = p_success(spec)
    assert abs(ps - 0.85) < 1e-9, ps
    pr = profitability(spec, cfg)
    assert abs(pr - 0.8) < 1e-9, pr
    c = composite(spec, cfg)
    assert abs(c - 0.68) < 1e-9, c
    assert adjusted_score(c, 1.0) == round(c, 4)
    assert adjusted_score(c, 0.0) == round(c * 0.5, 4)
    assert adjusted_score(c, None) is None
    # turnover haircut ordering
    hi = ScreenSpec(**{**spec.__dict__, "key": "probe_hi", "turnover": "high"})
    assert profitability(hi, cfg) < profitability(spec, cfg)
    print("evidence.py selftest: OK")
    return 0


if __name__ == "__main__":
    import sys
    if "--selftest" in sys.argv:
        sys.exit(selftest())
    print(__doc__)
