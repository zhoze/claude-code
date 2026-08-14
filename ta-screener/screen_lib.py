"""Shared screen plumbing: the ScreenSpec contract, ranking and IC helpers.

The screen contract (FROZEN):
    runner(panel: Panel, cfg: dict) -> pd.DataFrame   # date x ticker score
- higher = better (a screen that prefers "low X" returns -X or a reversed rank);
- NaN = cannot evaluate (never 0);
- strictly causal: score at date t uses only data <= t (scheduled earnings dates are
  the one documented exception, usable only where the schedule is known ex ante);
- the full score history is returned so the empirical IC layer gets it for free.

Evidence rubric fields (all in [0, 1], encoded per screen, documented in README):
    validation        1.0 peer-reviewed + replicated ... 0.4 practitioner lore
    us_applicability  1.0 US large-cap evidence ... 0.5 non-US/index-level only
    persistence       1.0 documented post-publication ... 0.3 likely arbitraged
    overfit_risk      0.2 simple one-parameter effect ... 0.7 data-mined family
    perf_bucket       published long-short performance: 1.0 Sharpe>1.5 or alpha>10%/yr;
                      0.8 Sharpe 1.0-1.5; 0.6 Sharpe 0.5-1.0; 0.4 below/unreported
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

import numpy as np
import pandas as pd

from panel import MissingInputError, Panel  # noqa: F401  (re-exported for screens)

TURNOVER_CLASSES = ("low", "medium", "high")
FAMILIES = ("alphas101", "earnings", "momentum", "meanrev", "lowrisk", "trend")


@dataclass(frozen=True)
class ScreenSpec:
    key: str                       # unique, snake_case, e.g. "alpha_001", "sue_decile"
    family: str                    # one of FAMILIES
    title: str
    citation: str                  # human-readable source
    arxiv: str | None              # e.g. "1601.00991" (None for SSRN/journal-only)
    runner: Callable[..., pd.DataFrame]
    validation: float
    us_applicability: float
    persistence: float
    overfit_risk: float
    perf_bucket: float
    turnover: str = "medium"       # one of TURNOVER_CLASSES
    needs: tuple = ()              # ("earnings", "benchmarks", "sector", "cap", "vwap")
    notes: str = ""

    def validate(self) -> None:
        assert self.key and self.key == self.key.lower(), self.key
        assert self.family in FAMILIES, f"{self.key}: bad family {self.family}"
        assert self.turnover in TURNOVER_CLASSES, f"{self.key}: bad turnover"
        for f_ in ("validation", "us_applicability", "persistence",
                   "overfit_risk", "perf_bucket"):
            v = getattr(self, f_)
            assert 0.0 <= v <= 1.0, f"{self.key}: {f_}={v} outside [0,1]"
        assert callable(self.runner), self.key


def pct_rank(series: pd.Series, higher_is_better: bool = True) -> pd.Series:
    """Cross-sectional percentile in [0, 100]; 100 = most attractive."""
    return series.rank(pct=True, ascending=higher_is_better, method="average") * 100


def last_valid_row(score: pd.DataFrame, min_coverage: float = 0.10) -> pd.Series | None:
    """Most recent date whose cross-section has >= min_coverage non-NaN scores."""
    coverage = score.notna().mean(axis=1)
    ok = coverage[coverage >= min_coverage]
    if ok.empty:
        return None
    return score.loc[ok.index[-1]]


def to_ranked(score: pd.DataFrame, panel: Panel, score_name: str,
              top: int | None = None, min_coverage: float = 0.10) -> pd.DataFrame:
    """Convert a score history into the ranked cross-sectional output table.

    Columns: ticker, company, sector, market_cap, <score_name>, score_pct —
    sorted by score desc, ties broken by ticker for determinism.
    """
    row = last_valid_row(score, min_coverage)
    if row is None:
        raise MissingInputError(f"{score_name}: no date reaches {min_coverage:.0%} coverage")
    df = row.dropna().rename(score_name).to_frame()
    df["score_pct"] = pct_rank(df[score_name])
    df = df.join(panel.meta[["company", "sector", "market_cap"]], how="left")
    df.index.name = "ticker"
    df = (df.reset_index()
            .sort_values([score_name, "ticker"], ascending=[False, True])
            .reset_index(drop=True))
    df = df[["ticker", "company", "sector", "market_cap", score_name, "score_pct"]]
    return df.head(top) if top else df


def spearman_ic(a: pd.Series, b: pd.Series) -> float:
    """Spearman rank correlation without scipy: pearson of pct-ranks on shared names."""
    j = pd.concat([a, b], axis=1, keys=["a", "b"]).dropna()
    if len(j) < 3 or j["a"].nunique() < 2 or j["b"].nunique() < 2:
        return float("nan")
    ra = j["a"].rank(pct=True)
    rb = j["b"].rank(pct=True)
    c = np.corrcoef(ra, rb)[0, 1]
    return float(c)


def selftest() -> int:
    from panel import synthetic_panel, load_config
    p = synthetic_panel()
    cfg = load_config()

    mom = p.adjclose.shift(21) / p.adjclose.shift(252) - 1  # simple 12-1 as a probe
    ranked = to_ranked(mom, p, "probe", top=5, min_coverage=cfg["ranking"]["min_coverage"])
    assert list(ranked.columns) == ["ticker", "company", "sector", "market_cap",
                                    "probe", "score_pct"]
    assert len(ranked) == 5
    assert ranked["probe"].is_monotonic_decreasing
    assert ranked["score_pct"].iloc[0] == 100.0

    # perfect monotone -> IC 1; reversed -> -1
    s = ranked.set_index("ticker")["probe"]
    assert abs(spearman_ic(s, s) - 1.0) < 1e-9
    assert abs(spearman_ic(s, -s) + 1.0) < 1e-9
    assert np.isnan(spearman_ic(s.iloc[:2], s.iloc[:2]))

    spec = ScreenSpec(key="probe", family="momentum", title="Probe", citation="—",
                      arxiv=None, runner=lambda panel, cfg: mom, validation=1.0,
                      us_applicability=1.0, persistence=0.6, overfit_risk=0.2,
                      perf_bucket=0.6, turnover="low")
    spec.validate()
    try:
        ScreenSpec(key="bad", family="nope", title="", citation="", arxiv=None,
                   runner=lambda panel, cfg: mom, validation=1.0, us_applicability=1.0,
                   persistence=1.0, overfit_risk=0.0, perf_bucket=1.0).validate()
        raise SystemExit("bad family accepted")
    except AssertionError:
        pass

    print("screen_lib.py selftest: OK")
    return 0


if __name__ == "__main__":
    import sys
    if "--selftest" in sys.argv:
        sys.exit(selftest())
    print(__doc__)
