"""Stage 2 — screen_universe: Russell 1000 proxy -> listed options -> ADV/spread floors.

The universe is defined BEFORE the fundamental screens run (review C1), so no screen
can surface an untradeable name.
"""
from __future__ import annotations

import os

import pandas as pd


def screen_universe(cfg, snap_dir: str, session: str) -> dict:
    u = cfg.strategy["universe"]
    meta = pd.read_csv(os.path.join(snap_dir, "ta", "universe.csv"))
    liq = pd.read_csv(os.path.join(snap_dir, "liquidity.csv"))
    df = meta.merge(liq, on="ticker", how="left")

    reasons: dict[str, str] = {}

    def drop(mask, why):
        for t in df.loc[mask & ~df["ticker"].isin(reasons), "ticker"]:
            reasons[t] = why

    mcap = pd.to_numeric(df["market_cap"], errors="coerce")
    drop(mcap.notna() & (mcap < u["min_market_cap"]), "market cap below floor")
    drop(df["last_bar"].astype(str) < session, "stale price (no bar on session date)")
    if u["require_options"]:
        drop(df["has_options"].astype(str).str.lower() != "true", "no listed options")
    adv = pd.to_numeric(df["adv20"], errors="coerce")
    drop(adv.isna() | (adv < u["min_adv_usd"]), f"ADV20 < ${u['min_adv_usd'] / 1e6:.0f}M")
    spr = pd.to_numeric(df["spread_bp"], errors="coerce")
    # A quoted (NBBO) spread is held to max_spread_bp. A Corwin-Schultz estimate from daily
    # high/low ranges is noisy and biased high for large caps, so it gets its own ceiling.
    est = df.get("spread_source", pd.Series("", index=df.index)).astype(str) \
        .str.startswith("corwin")
    cap = est.map({True: float(u.get("max_spread_bp_estimated", u["max_spread_bp"])),
                   False: float(u["max_spread_bp"])})
    drop(spr.isna() | (spr > cap), "spread above ceiling")

    kept = df[~df["ticker"].isin(reasons)].sort_values("ticker")
    liquidity = kept.set_index("ticker")[["adv20", "spread_bp", "spread_source"]].to_dict("index")
    return {"universe": kept["ticker"].tolist(),
            "liquidity": liquidity,
            "dropped": dict(sorted(reasons.items())),
            "counts": {"discovered": int(len(df)), "kept": int(len(kept))}}
