"""Independent CONFIDENCE_SCORE 0-100 (spec §38).

Confidence measures evidential quality — data completeness, cross-model
agreement, regime similarity, model uncertainty — and is deliberately NOT a
function of FINAL_SCORE.
"""

from __future__ import annotations

import math

import numpy as np


def compute_confidence(*, data_completeness: float, model_agreement: float,
                       historical_reliability: float, regime_similarity: float,
                       model_uncertainty: float, options_liquidity: float,
                       fundamental_overlap: int, technical_confirmed: bool,
                       macro_compatible: bool) -> float:
    """All inputs 0-1 except overlap (0-10). Missing evidence -> pass 0."""
    def clip01(x):
        return float(np.clip(x, 0, 1)) if isinstance(x, (int, float)) and math.isfinite(x) else 0.0

    pts = 0.0
    pts += 20 * clip01(data_completeness)          # completeness of inputs
    pts += 18 * clip01(model_agreement)            # fundamentals/ML/technicals agree
    pts += 14 * clip01(historical_reliability)     # past system hit-rate on similar picks
    pts += 12 * clip01(regime_similarity)          # training regimes resemble today
    pts += 12 * (1 - clip01(model_uncertainty))    # walk-forward dispersion
    pts += 10 * clip01(options_liquidity)
    pts += 8 * clip01(fundamental_overlap / 10.0)
    pts += 3 * (1.0 if technical_confirmed else 0.0)
    pts += 3 * (1.0 if macro_compatible else 0.0)
    return float(np.clip(pts, 0, 100))
