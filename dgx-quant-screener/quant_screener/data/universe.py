"""Point-in-time Russell universe + liquidity filters (spec §1).

Point-in-time membership comes from a user-supplied CSV of add/drop events
(`date,ticker,action`). Without it we fall back to the current IWV holdings
snapshot and mark every downstream backtest with SURVIVORSHIP_BIAS_WARNING —
the bias is surfaced, never hidden (spec §2).
"""

from __future__ import annotations

import datetime as dt
import logging
from dataclasses import dataclass, field
from pathlib import Path

import pandas as pd

from .integrity import Provenance

log = logging.getLogger(__name__)

_EXCLUDE_NAME_TOKENS = (
    " ETF", " FUND", " TRUST UNITS", " ACQUISITION", " SPAC", " PFD",
    " PREFERRED", " WARRANT", " UNIT ", " CLOSED-END",
)


@dataclass
class Universe:
    tickers: list[str]
    as_of: dt.date
    provenance: Provenance
    survivorship_biased: bool = False
    excluded: dict[str, str] = field(default_factory=dict)


def load_membership_events(csv_path: str | Path) -> pd.DataFrame:
    df = pd.read_csv(csv_path)
    df.columns = [c.strip().lower() for c in df.columns]
    df["date"] = pd.to_datetime(df["date"]).dt.date
    df["action"] = df["action"].str.lower().str.strip()
    return df.sort_values("date")


def membership_as_of(events: pd.DataFrame, as_of: dt.date) -> set[str]:
    members: set[str] = set()
    for _, row in events[events["date"] <= as_of].iterrows():
        if row["action"] in ("add", "added"):
            members.add(row["ticker"])
        else:
            members.discard(row["ticker"])
    return members


def _iwv_holdings_snapshot() -> list[str]:
    """Current Russell 3000 proxy via iShares IWV holdings CSV (best-effort)."""
    import io

    import requests

    url = ("https://www.ishares.com/us/products/239714/"
           "ishares-russell-3000-etf/1467271812596.ajax"
           "?fileType=csv&fileName=IWV_holdings&dataType=fund")
    r = requests.get(url, timeout=60, headers={"User-Agent": "Mozilla/5.0"})
    r.raise_for_status()
    lines = r.text.splitlines()
    header_idx = next(i for i, ln in enumerate(lines) if ln.startswith("Ticker"))
    df = pd.read_csv(io.StringIO("\n".join(lines[header_idx:])))
    df = df[df.get("Asset Class", "Equity") == "Equity"]
    return sorted({str(t).strip() for t in df["Ticker"] if isinstance(t, str) and t.strip()})


def build_universe(cfg, as_of: dt.date | None = None) -> Universe:
    as_of = as_of or dt.date.today()
    biased = False
    if cfg.universe.membership_csv:
        events = load_membership_events(cfg.universe.membership_csv)
        tickers = sorted(membership_as_of(events, as_of))
        source = f"membership_csv:{cfg.universe.membership_csv}"
    else:
        try:
            tickers = _iwv_holdings_snapshot()
        except Exception as e:
            log.warning("IWV holdings fetch failed (%s); using empty universe", e)
            tickers = []
        source = f"{cfg.universe.fallback_proxy_etf}_holdings_snapshot"
        biased = True

    prov = Provenance(source=source, data_timestamp=None)
    if biased:
        prov.flag("SURVIVORSHIP_BIAS_WARNING: current index snapshot used as universe; "
                  "supply universe.membership_csv for point-in-time membership")

    # security-type exclusions by symbol shape (dots/dashes = classes/preferreds vary
    # by vendor; name-based filtering happens after profile data is loaded)
    kept, excluded = [], {}
    for t in tickers:
        if not t.isalpha() or len(t) > 5:
            excluded[t] = "non_common_symbol"
        else:
            kept.append(t)
    return Universe(tickers=kept, as_of=as_of, provenance=prov,
                    survivorship_biased=biased, excluded=excluded)


def apply_liquidity_filters(universe: Universe, prices: dict[str, pd.DataFrame],
                            cfg) -> Universe:
    """Price, dollar-volume, history-length and staleness filters (spec §1).
    Names with missing price data are excluded — never silently substituted."""
    u = cfg.universe
    kept: list[str] = []
    for t in universe.tickers:
        df = prices.get(t)
        if df is None or df.empty:
            universe.excluded[t] = "no_price_data"
            continue
        if len(df) < u.min_history_days:
            universe.excluded[t] = "insufficient_history"
            continue
        last = df.iloc[-1]
        if float(last["close"]) < u.min_price:
            universe.excluded[t] = "price_below_min"
            continue
        adv = float((df["close"] * df["volume"]).tail(20).mean())
        if adv < float(u.min_avg_dollar_volume):
            universe.excluded[t] = "dollar_volume_below_min"
            continue
        age_days = (universe.as_of - df.index[-1].date()).days
        if age_days > 5:
            universe.excluded[t] = "stale_market_data"
            continue
        kept.append(t)
    universe.tickers = kept
    universe.provenance.flag(f"liquidity_filters: kept {len(kept)}, "
                             f"excluded {len(universe.excluded)}")
    return universe
