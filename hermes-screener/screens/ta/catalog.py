"""The validated catalog: exactly 150 screens, ranked by evidence composite.

Merges the six SPECS sources (101 alphas built by alphas.build_specs() + the five
family modules) and validates the roster: exactly 150 unique keys, prescribed family
counts, every rubric field in [0, 1]. The ranked order — composite descending, ties by
p_success then key — IS the "top 150" deliverable ordering.
"""
from __future__ import annotations

import alphas
from panel import load_config
from screen_lib import ScreenSpec

EXPECTED_FAMILY_COUNTS = {
    "alphas101": 101,
    "earnings": 12,
    "momentum": 12,
    "meanrev": 10,
    "lowrisk": 5,
    "trend": 10,
}


def collect() -> list[ScreenSpec]:
    import screens_earnings
    import screens_lowrisk
    import screens_meanrev
    import screens_momentum
    import screens_trend

    specs = list(alphas.build_specs())
    for mod in (screens_earnings, screens_momentum, screens_meanrev,
                screens_lowrisk, screens_trend):
        specs.extend(mod.SPECS)
    return specs


def validate(specs: list[ScreenSpec]) -> None:
    assert len(specs) == 150, f"expected 150 screens, got {len(specs)}"
    keys = [s.key for s in specs]
    dupes = {k for k in keys if keys.count(k) > 1}
    assert not dupes, f"duplicate keys: {sorted(dupes)}"
    counts: dict[str, int] = {}
    for s in specs:
        s.validate()
        counts[s.family] = counts.get(s.family, 0) + 1
    assert counts == EXPECTED_FAMILY_COUNTS, f"family counts {counts}"


def ranked(cfg: dict | None = None) -> list[tuple[int, ScreenSpec, dict]]:
    """[(rank, spec, {p_success, profitability, composite}), ...] best first."""
    from evidence import composite, p_success, profitability
    cfg = cfg or load_config()
    specs = collect()
    validate(specs)
    scored = [(s, {"p_success": p_success(s),
                   "profitability": profitability(s, cfg),
                   "composite": composite(s, cfg)}) for s in specs]
    scored.sort(key=lambda t: (-t[1]["composite"], -t[1]["p_success"], t[0].key))
    return [(i + 1, s, sc) for i, (s, sc) in enumerate(scored)]


def selftest() -> int:
    rows = ranked()
    assert len(rows) == 150
    assert rows[0][2]["composite"] >= rows[-1][2]["composite"]
    print(f"{'rank':>4}  {'key':<28} {'family':<10} {'comp':>6} {'p_suc':>6} "
          f"{'profit':>6} {'turn':<6} title")
    for rank_, spec, sc in rows:
        print(f"{rank_:>4}  {spec.key:<28} {spec.family:<10} {sc['composite']:>6.3f} "
              f"{sc['p_success']:>6.3f} {sc['profitability']:>6.3f} {spec.turnover:<6} "
              f"{spec.title}")
    fams = {}
    for _, spec, _sc in rows:
        fams[spec.family] = fams.get(spec.family, 0) + 1
    print(f"catalog.py selftest: OK — 150 screens validated, families {fams}")
    return 0


if __name__ == "__main__":
    import sys
    if "--selftest" in sys.argv:
        sys.exit(selftest())
    print(__doc__)
