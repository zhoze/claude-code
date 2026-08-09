"""Options-market filter (spec §27): liquidity 0-100 and sentiment -100..+100.

Options are a confirmation/risk signal only — they never override fundamentals.
Stocks whose options are technically listed but effectively untradeable
(liquidity score below config threshold) are rejected.
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

log = logging.getLogger(__name__)


@dataclass
class OptionsAssessment:
    ticker: str
    has_options: bool
    liquidity_score: float = 0.0          # 0-100
    sentiment_score: float = 0.0          # -100..+100
    metrics: dict = field(default_factory=dict)
    usable: bool = False
    notes: list[str] = field(default_factory=list)


def assess_options(ticker: str, chain_data: dict, spot: float | None, cfg) -> OptionsAssessment:
    a = OptionsAssessment(ticker=ticker, has_options=bool(chain_data.get("has_options")))
    if not a.has_options:
        a.notes.append("no listed options")
        return a
    chain: pd.DataFrame = chain_data.get("chain", pd.DataFrame())
    if chain.empty or not spot:
        a.notes.append("empty chain or missing spot")
        return a

    chain = chain.copy()
    for col in ("volume", "openInterest", "bid", "ask", "impliedVolatility", "strike"):
        if col in chain.columns:
            chain[col] = pd.to_numeric(chain[col], errors="coerce")

    total_volume = float(chain["volume"].fillna(0).sum())
    total_oi = float(chain["openInterest"].fillna(0).sum())

    # near-the-money contracts drive real usability
    ntm = chain[(chain["strike"] >= spot * 0.9) & (chain["strike"] <= spot * 1.1)]
    ntm = ntm[(ntm["bid"] > 0) & (ntm["ask"] > 0)]
    if ntm.empty:
        a.notes.append("no quoted near-the-money contracts")
        return a
    mid = (ntm["bid"] + ntm["ask"]) / 2
    rel_spread = float(((ntm["ask"] - ntm["bid"]) / mid.replace(0, np.nan)).median())

    # ---- liquidity score: volume, OI, spread each contribute
    vol_pts = min(40.0, 40 * math.log10(max(total_volume, 1)) / 5)       # 100k vol -> 40
    oi_pts = min(30.0, 30 * math.log10(max(total_oi, 1)) / 5)            # 100k OI  -> 30
    spread_pts = max(0.0, 30 * (1 - min(rel_spread / cfg.options.max_rel_spread, 1.5)))
    a.liquidity_score = round(min(100.0, vol_pts + oi_pts + spread_pts), 1)
    a.usable = a.liquidity_score >= cfg.options.min_liquidity_score \
        and rel_spread <= cfg.options.max_rel_spread * 1.5
    if not a.usable:
        a.notes.append("options effectively unusable (liquidity below threshold)")

    # ---- sentiment: put/call volume + OI skew + IV skew
    calls = chain[chain["side"] == "call"]
    puts = chain[chain["side"] == "put"]
    pc_vol = float(puts["volume"].fillna(0).sum()) / max(float(calls["volume"].fillna(0).sum()), 1.0)
    pc_oi = float(puts["openInterest"].fillna(0).sum()) / max(float(calls["openInterest"].fillna(0).sum()), 1.0)
    # IV skew: OTM put IV vs OTM call IV (positive skew = downside fear)
    otm_puts = puts[(puts["strike"] < spot) & puts["impliedVolatility"].notna()]
    otm_calls = calls[(calls["strike"] > spot) & calls["impliedVolatility"].notna()]
    iv_skew = float(otm_puts["impliedVolatility"].median() - otm_calls["impliedVolatility"].median()) \
        if len(otm_puts) and len(otm_calls) else 0.0

    # neutral P/C ~0.9; below = call-tilted (bullish). Clamp each component.
    s_pc = np.clip((0.9 - pc_vol) * 100, -50, 50)
    s_oi = np.clip((0.9 - pc_oi) * 60, -30, 30)
    s_skew = np.clip(-iv_skew * 200, -20, 20)
    a.sentiment_score = round(float(s_pc + s_oi + s_skew), 1)
    a.sentiment_score = float(np.clip(a.sentiment_score, -100, 100))

    atm = ntm.loc[(ntm["strike"] - spot).abs().sort_values().index[:4]]
    atm_iv = float(atm["impliedVolatility"].median()) if len(atm) else math.nan
    all_iv = chain["impliedVolatility"].dropna()
    a.metrics = {
        "total_volume": total_volume, "total_open_interest": total_oi,
        "ntm_rel_spread": rel_spread, "put_call_volume": pc_vol, "put_call_oi": pc_oi,
        "iv_skew": iv_skew, "atm_iv": atm_iv,
        "iv_rank_chain": float((all_iv < atm_iv).mean() * 100) if len(all_iv) and pd.notna(atm_iv) else math.nan,
        "expected_move_1m": atm_iv * spot * math.sqrt(21 / 252) if pd.notna(atm_iv) else math.nan,
    }
    return a
