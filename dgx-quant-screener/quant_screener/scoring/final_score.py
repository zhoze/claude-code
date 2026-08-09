"""FINAL_SCORE 0-100 (spec §37) and minimum-requirement gate (spec §39)."""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np


@dataclass
class CandidateScores:
    """All component scores on 0-100 before weighting (NaN = unavailable)."""
    ticker: str
    fundamental: float = math.nan
    overlap: float = math.nan               # overlap count scaled: n/10*100
    ml_expected_return: float = math.nan
    mean_cvar: float = math.nan
    technical: float = math.nan
    technical_robustness: float = math.nan
    macro: float = math.nan
    options: float = math.nan
    liquidity: float = math.nan
    catalyst: float = math.nan
    event_risk_penalty: float = 0.0
    tail_risk_penalty: float = 0.0
    model_uncertainty_penalty: float = 0.0
    final_score: float = math.nan
    gate_failures: list[str] = field(default_factory=list)


def compute_final_score(s: CandidateScores, weights: dict[str, float]) -> float:
    parts = {
        "fundamental": s.fundamental, "overlap": s.overlap,
        "ml_expected_return": s.ml_expected_return, "mean_cvar": s.mean_cvar,
        "technical": s.technical, "technical_robustness": s.technical_robustness,
        "macro": s.macro, "options": s.options, "liquidity": s.liquidity,
        "catalyst": s.catalyst,
    }
    num, den = 0.0, 0.0
    for k, v in parts.items():
        w = weights.get(k, 0.0)
        if isinstance(v, (int, float)) and math.isfinite(v):
            num += w * v
            den += w
        # missing components contribute nothing but their weight still divides at
        # half strength: absence of evidence must not inflate the score
        else:
            den += w * 0.5
    base = num / den if den > 0 else 0.0
    s.final_score = float(np.clip(
        base - s.event_risk_penalty - s.tail_risk_penalty - s.model_uncertainty_penalty,
        0, 100))
    return s.final_score


MINIMUM_REQUIREMENTS = (  # (attr check name, human label) — all must hold (spec §39)
    "in_universe", "has_listed_options", "options_usable", "liquidity_ok",
    "fundamental_strong", "overlap_ok", "cvar_ok", "ml_ok",
    "technical_validated", "technical_signal_now", "macro_ok", "event_risk_ok",
)


def gate_candidate(s: CandidateScores, checks: dict[str, bool]) -> bool:
    """Hard minimum requirements. Standards are never lowered to fill the table."""
    s.gate_failures = [name for name in MINIMUM_REQUIREMENTS if not checks.get(name, False)]
    return not s.gate_failures
