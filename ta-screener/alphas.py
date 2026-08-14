"""Aggregator for the 101 Formulaic Alphas (Kakushadze, arXiv:1601.00991).

The alpha implementations live in four defs modules (alphas_defs_a/b/c/d, 25-26 each).
Each defs module exports:
    ALPHAS:   dict[int, Callable[[AlphaInputs], pd.DataFrame]]   # date x ticker score
    FORMULAS: dict[int, str]     # the paper's formula, verbatim, for audit
    META:     dict[int, dict]    # optional per-alpha overrides: turnover, needs, notes

This module merges them (asserting exactly alphas 1..101), precomputes shared inputs
once per panel (AlphaInputs), and builds the 101 ScreenSpec entries with family-level
evidence rubric values (the paper reports portfolio-level, not per-alpha, performance —
so alphas share one honest rubric; ties are broken downstream by empirical IC).

Paper conventions encoded here:
- every alpha is oriented "higher = buy" (leading minus signs are in the formulas);
- delay-0 alphas (42, 48, 53, 54) need same-day execution -> flagged in notes;
- adv{d} = average daily dollar volume; vwap comes straight from FMP;
- IndNeutralize uses the FMP sector for all of the paper's group levels;
- cap (alpha 56) uses current market cap for all dates (documented approximation).
"""
from __future__ import annotations

import importlib
from typing import Callable

import numpy as np
import pandas as pd

import ops
from panel import Panel
from screen_lib import ScreenSpec

DEFS_MODULES = ("alphas_defs_a", "alphas_defs_b", "alphas_defs_c", "alphas_defs_d")
DELAY0 = {42, 48, 53, 54}
IND_NEUTRALIZED = {48, 58, 59, 63, 67, 69, 70, 76, 79, 80, 82, 87, 89, 90, 91, 93, 97, 100}

# Family-level evidence rubric (see screen_lib docstring + README):
# arXiv backtest-only (0.6 validation), US universe (1.0), attenuated persistence (0.6),
# data-mined family with no per-alpha OOS (0.7 overfit), portfolio-level Sharpe only
# (0.6 perf bucket). Holding periods 0.6-6.4 days -> "high" turnover unless overridden.
FAMILY_RUBRIC = dict(validation=0.6, us_applicability=1.0, persistence=0.6,
                     overfit_risk=0.7, perf_bucket=0.6)


class AlphaInputs:
    """Shared, precomputed views over a Panel — built once, reused by all 101 alphas."""

    def __init__(self, panel: Panel, cfg: dict):
        self.panel = panel
        self.open = panel.open
        self.high = panel.high
        self.low = panel.low
        self.close = panel.close
        self.volume = panel.volume
        self.vwap = panel.vwap
        self.returns = panel.returns
        self.cap = panel.cap()
        self.sector = panel.sectors()
        self._adv: dict[int, pd.DataFrame] = {}

    def adv(self, d: int) -> pd.DataFrame:
        if d not in self._adv:
            self._adv[d] = ops.adv(self.close, self.volume, d)
        return self._adv[d]

    def ind(self, x: pd.DataFrame) -> pd.DataFrame:
        """IndNeutralize against the FMP sector (all paper group levels)."""
        return ops.ind_neutralize(x, self.sector)

    def cap_frame(self) -> pd.DataFrame:
        """Market cap broadcast to date x ticker (static — documented approximation)."""
        return pd.DataFrame(
            np.tile(self.cap.reindex(self.close.columns).to_numpy(dtype=float),
                    (len(self.close.index), 1)),
            index=self.close.index, columns=self.close.columns)


# --------------------------------------------------------------------- loading

def load_defs(strict: bool = True):
    """Merge the four defs modules. strict=True asserts exactly alphas 1..101."""
    alphas: dict[int, Callable] = {}
    formulas: dict[int, str] = {}
    meta: dict[int, dict] = {}
    missing_modules = []
    for name in DEFS_MODULES:
        try:
            m = importlib.import_module(name)
        except ImportError:
            missing_modules.append(name)
            continue
        overlap = alphas.keys() & m.ALPHAS.keys()
        assert not overlap, f"{name}: duplicate alpha numbers {sorted(overlap)}"
        alphas.update(m.ALPHAS)
        formulas.update(m.FORMULAS)
        meta.update(getattr(m, "META", {}))
    if strict:
        assert not missing_modules, f"missing defs modules: {missing_modules}"
        assert set(alphas) == set(range(1, 102)), (
            f"expected alphas 1..101, missing {sorted(set(range(1, 102)) - set(alphas))}, "
            f"unexpected {sorted(set(alphas) - set(range(1, 102)))}")
        assert set(formulas) == set(alphas), "every alpha needs its FORMULAS entry"
    return alphas, formulas, meta


# One-slot cache: run_screens builds AlphaInputs once per panel, not 101 times.
_inputs_cache: tuple[int, AlphaInputs] | None = None


def get_inputs(panel: Panel, cfg: dict) -> AlphaInputs:
    global _inputs_cache
    if _inputs_cache is None or _inputs_cache[0] != id(panel):
        _inputs_cache = (id(panel), AlphaInputs(panel, cfg))
    return _inputs_cache[1]


def make_runner(n: int, fn: Callable) -> Callable:
    def runner(panel: Panel, cfg: dict) -> pd.DataFrame:
        return fn(get_inputs(panel, cfg))
    runner.__name__ = f"alpha_{n:03d}"
    return runner


def build_specs(strict: bool = True) -> list[ScreenSpec]:
    alphas, formulas, meta = load_defs(strict=strict)
    specs = []
    for n in sorted(alphas):
        m = meta.get(n, {})
        needs = tuple(m.get("needs", ()))
        if n in IND_NEUTRALIZED:
            needs = tuple(sorted(set(needs) | {"sector"}))
        if n == 56:
            needs = tuple(sorted(set(needs) | {"cap"}))
        notes = m.get("notes", "")
        if n in DELAY0:
            notes = (notes + " " if notes else "") + "delay-0: requires same-day execution."
        specs.append(ScreenSpec(
            key=f"alpha_{n:03d}", family="alphas101",
            title=f"Formulaic Alpha #{n}",
            citation="Kakushadze (2016), 101 Formulaic Alphas, Wilmott 84",
            arxiv="1601.00991",
            runner=make_runner(n, alphas[n]),
            turnover=m.get("turnover", "high"),
            needs=needs, notes=notes.strip(),
            **FAMILY_RUBRIC,
        ))
    return specs


def get_formulas(strict: bool = True) -> dict[int, str]:
    return load_defs(strict=strict)[1]


# --------------------------------------------------------------------- selftest

def selftest(argv: list[str]) -> int:
    from panel import synthetic_panel, load_config
    strict = "--allow-missing" not in argv
    rng = None
    for a in argv:
        if a.startswith("--range"):
            i = argv.index(a)
            spec = a.split("=", 1)[1] if "=" in a else argv[i + 1]
            lo, hi = (int(x) for x in spec.split("-"))
            rng = range(lo, hi + 1)

    alphas, formulas, _ = load_defs(strict=strict)
    if not alphas:
        print("alphas.py selftest: 0 alphas registered (defs modules not yet present)")
        return 0

    panel = synthetic_panel()
    cfg = load_config()
    inputs = get_inputs(panel, cfg)
    todo = [n for n in sorted(alphas) if rng is None or n in rng]
    bad = []
    for n in todo:
        try:
            score = alphas[n](inputs)
            assert isinstance(score, pd.DataFrame), "must return a DataFrame"
            assert score.shape == panel.close.shape, f"shape {score.shape}"
            cov = score.iloc[-1].notna().mean()
            assert cov >= 0.25, f"last-row coverage {cov:.0%} < 25%"
            assert n in formulas and formulas[n].strip(), "formula string missing"
        except Exception as e:  # noqa: BLE001 — selftest reports, doesn't crash
            bad.append((n, str(e)))
    if bad:
        for n, msg in bad:
            print(f"  FAIL alpha_{n:03d}: {msg}")
        print(f"alphas.py selftest: {len(bad)}/{len(todo)} FAILED")
        return 1
    print(f"alphas.py selftest: {len(todo)} alphas OK "
          f"({len(alphas)} registered)")
    return 0


if __name__ == "__main__":
    import sys
    if "--selftest" in sys.argv:
        sys.exit(selftest(sys.argv))
    print(__doc__)
