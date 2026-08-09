"""Macro dashboard inputs (spec §17-24): futures, VIX & term structure, rates,
USD/currencies, energy, commodities, credit proxies, global overnight moves,
economic calendar.

Everything is pulled through the price provider (yfinance symbols) so a single
cached path serves live runs and backtests. Economic-calendar data uses FMP when
a key exists; otherwise the section is reported as unavailable — never invented.
"""

from __future__ import annotations

import datetime as dt
import logging
import math
from dataclasses import dataclass, field

import pandas as pd

from .prices import MACRO_SYMBOLS, PriceLibrary

log = logging.getLogger(__name__)

GLOBAL_INDICES = {
    "EuroStoxx50": "^STOXX50E", "FTSE100": "^FTSE", "DAX": "^GDAXI",
    "Nikkei225": "^N225", "HangSeng": "^HSI", "ShanghaiComp": "000001.SS",
    "KOSPI": "^KS11", "Nifty50": "^NSEI",
}


@dataclass
class MacroSnapshot:
    as_of: dt.date
    dashboard: dict[str, dict] = field(default_factory=dict)   # name -> {value, change_pct, signal}
    global_markets: dict[str, dict] = field(default_factory=dict)
    calendar: list[dict] = field(default_factory=list)
    series: dict[str, pd.Series] = field(default_factory=dict)  # daily closes for regime/sensitivity
    notes: list[str] = field(default_factory=list)


def _last_change(px: pd.DataFrame) -> tuple[float, float]:
    closes = px["close"].dropna()
    if len(closes) < 2:
        return math.nan, math.nan
    last, prev = float(closes.iloc[-1]), float(closes.iloc[-2])
    return last, (last / prev - 1) * 100


def _signal_for(name: str, value: float, change_pct: float) -> str:
    """Coarse directional read used in the dashboard table."""
    if not math.isfinite(change_pct):
        return "NO_DATA"
    risk_assets = {"es_futures", "nq_futures", "rty_futures", "ym_futures", "copper"}
    fear_assets = {"vix", "gold"}
    if name in risk_assets:
        return "RISK_ON" if change_pct > 0.15 else "RISK_OFF" if change_pct < -0.15 else "NEUTRAL"
    if name in fear_assets:
        return "RISK_OFF" if change_pct > 2 else "RISK_ON" if change_pct < -2 else "NEUTRAL"
    if name.startswith("ust"):
        return "RATES_UP" if change_pct > 1 else "RATES_DOWN" if change_pct < -1 else "NEUTRAL"
    if name == "dxy":
        return "USD_STRONG" if change_pct > 0.3 else "USD_WEAK" if change_pct < -0.3 else "NEUTRAL"
    return "UP" if change_pct > 0.5 else "DOWN" if change_pct < -0.5 else "NEUTRAL"


def build_macro_snapshot(price_lib: PriceLibrary, as_of: dt.date,
                         fmp_key: str | None = None) -> MacroSnapshot:
    snap = MacroSnapshot(as_of=as_of)
    for name, symbol in {**MACRO_SYMBOLS, "sp500": "^GSPC", "russell2000": "^RUT"}.items():
        try:
            px = price_lib.get(symbol, as_of)
        except Exception as e:
            log.warning("macro fetch %s failed: %s", symbol, e)
            px = pd.DataFrame()
        if px.empty:
            snap.dashboard[name] = {"value": None, "change_pct": None, "signal": "NO_DATA"}
            continue
        value, chg = _last_change(px)
        snap.dashboard[name] = {"value": value, "change_pct": chg,
                                "signal": _signal_for(name, value, chg)}
        snap.series[name] = px["close"]

    for name, symbol in GLOBAL_INDICES.items():
        try:
            px = price_lib.get(symbol, as_of)
            value, chg = _last_change(px)
            snap.global_markets[name] = {"value": value, "change_pct": chg}
        except Exception:
            snap.global_markets[name] = {"value": None, "change_pct": None}

    # VIX term structure: VIX vs VIX3M — backwardation (>1) = stress
    vix = snap.dashboard.get("vix", {}).get("value")
    vix3m = snap.dashboard.get("vix3m", {}).get("value")
    if vix and vix3m:
        snap.dashboard["vix_term_ratio"] = {
            "value": vix / vix3m, "change_pct": None,
            "signal": "STRESS" if vix / vix3m > 1.0 else "NORMAL"}

    # credit proxy: HYG/LQD relative move
    hyg = snap.dashboard.get("hyg", {}).get("change_pct")
    lqd = snap.dashboard.get("lqd", {}).get("change_pct")
    if hyg is not None and lqd is not None and math.isfinite(hyg) and math.isfinite(lqd):
        snap.dashboard["credit_hy_vs_ig"] = {
            "value": hyg - lqd, "change_pct": None,
            "signal": "CREDIT_STRESS" if hyg - lqd < -0.4 else "NEUTRAL"}

    snap.calendar = fetch_economic_calendar(as_of, fmp_key)
    if not snap.calendar:
        snap.notes.append("economic calendar unavailable (no FMP key) — treated as unknown, "
                          "not as 'no events'")
    return snap


def fetch_economic_calendar(as_of: dt.date, fmp_key: str | None) -> list[dict]:
    """US releases for the run day. EXPECTED/PREVIOUS always allowed; ACTUAL only
    included when already published (spec §23)."""
    if not fmp_key:
        return []
    import requests

    try:
        r = requests.get(
            "https://financialmodelingprep.com/api/v3/economic_calendar",
            params={"from": as_of.isoformat(), "to": as_of.isoformat(), "apikey": fmp_key},
            timeout=30)
        r.raise_for_status()
        events = [e for e in r.json() if e.get("country") in ("US", "USA")]
    except Exception as e:
        log.warning("economic calendar fetch failed: %s", e)
        return []
    out = []
    now = dt.datetime.now()
    for e in events:
        ev_time = pd.to_datetime(e.get("date"), errors="coerce")
        released = ev_time is not None and not pd.isna(ev_time) and ev_time.to_pydatetime() <= now
        out.append({
            "time": e.get("date"), "event": e.get("event"),
            "expected": e.get("estimate"), "previous": e.get("previous"),
            "actual": e.get("actual") if released else None,   # never leak unreleased actuals
            "importance": e.get("impact") or e.get("importance"),
        })
    return out
