"""Massive (formerly Polygon) client: daily OHLCV, dividends, options tradability, quotes.

Prices per name = two calls:
  - aggregates with adjusted=true  -> split-adjusted open/high/low/close/vwap/volume
  - /stocks/v1/dividends           -> historical_adjustment_factor per ex-date
  adjclose (total return) = split-adjusted close x the factor of the first dividend
  whose ex-date is after the bar date, matching the "dividend-adjusted" series the
  vendored ta-screener was built on (panel.py back-adjusts by adjclose/close).
Auth: Authorization: Bearer header, so next_url pagination never carries the key.
"""
from __future__ import annotations

import datetime as dt

import numpy as np
import pandas as pd

from .http import JsonClient, require_key

BAR_COLS = ["date", "open", "high", "low", "close", "volume", "vwap"]


class MassiveClient:
    def __init__(self, base_url: str, key_env: str = "MASSIVE_KEY", retries: int = 4,
                 timeout: float = 30.0, key: str | None = None,
                 max_rpm: float | None = None):
        self.http = JsonClient(base_url, key or require_key(key_env), key_param=None,
                               bearer=True, retries=retries, timeout=timeout, max_rpm=max_rpm)

    def _paged(self, path: str, params: dict | None = None, max_pages: int = 50) -> list | None:
        out, url, first = [], path, True
        for _ in range(max_pages):
            d = self.http.get(url, params if first else None)
            first = False
            if not isinstance(d, dict):
                return None if not out else out
            out.extend(d.get("results") or [])
            url = d.get("next_url")
            if not url:
                break
        return out

    # ---------------------------------------------------------------- bars
    def aggs(self, ticker: str, frm: str, to: str, adjusted: bool) -> pd.DataFrame | None:
        rows = self._paged(f"v2/aggs/ticker/{ticker}/range/1/day/{frm}/{to}",
                           {"adjusted": str(adjusted).lower(), "sort": "asc", "limit": 50000})
        if rows is None:
            return None
        if not rows:
            return pd.DataFrame(columns=BAR_COLS)
        df = pd.DataFrame(rows)
        df["date"] = (pd.to_datetime(df["t"], unit="ms", utc=True)
                      .dt.tz_convert("America/New_York").dt.strftime("%Y-%m-%d"))
        df = df.rename(columns={"o": "open", "h": "high", "l": "low", "c": "close",
                                "v": "volume", "vw": "vwap"})
        for c in BAR_COLS[1:]:
            if c not in df.columns:
                df[c] = np.nan
        return df[BAR_COLS].drop_duplicates("date", keep="last").reset_index(drop=True)

    def dividends(self, ticker: str) -> pd.DataFrame | None:
        rows = self._paged("stocks/v1/dividends",
                           {"ticker": ticker, "limit": 5000, "sort": "ex_dividend_date.asc"})
        if rows is None:
            return None
        cols = ["ex_dividend_date", "cash_amount", "historical_adjustment_factor"]
        if not rows:
            return pd.DataFrame(columns=cols)
        df = pd.DataFrame(rows)
        for c in cols:
            if c not in df.columns:
                df[c] = np.nan
        return df[cols].dropna(subset=["ex_dividend_date"]).sort_values("ex_dividend_date")

    def daily_history(self, ticker: str, frm: str, to: str) -> pd.DataFrame | None:
        """Split-adjusted OHLCV + vwap + total-return adjclose, one row per session.

        One aggregates call (adjusted=true) plus one dividends call. Unadjusted bars are
        not needed: split-adjusted O/H/L/C/volume leave no phantom split gaps, the
        latest close equals the tradable price, and close x volume is split-invariant,
        so ADV is unchanged. The ta panel's adjclose/close factor then carries only
        the dividend adjustment.
        """
        bars = self.aggs(ticker, frm, to, adjusted=True)
        if bars is None or bars.empty:
            return bars
        divs = self.dividends(ticker)
        return assemble_history(bars, None, divs)

    # ----------------------------------------------------- tradability/quotes
    def has_listed_options(self, ticker: str) -> bool | None:
        d = self.http.get("v3/reference/options/contracts",
                          {"underlying_ticker": ticker, "expired": "false", "limit": 1})
        if not isinstance(d, dict):
            return None
        return bool(d.get("results"))

    def snapshot_spreads_bp(self, tickers: list[str], chunk: int = 200) -> dict[str, float]:
        """Current NBBO spread in bp from the full-market snapshot (plan permitting)."""
        out: dict[str, float] = {}
        for i in range(0, len(tickers), chunk):
            d = self.http.get("v2/snapshot/locale/us/markets/stocks/tickers",
                              {"tickers": ",".join(tickers[i:i + chunk])})
            for row in (d or {}).get("tickers") or []:
                q = row.get("lastQuote") or {}
                ask, bid = q.get("P"), q.get("p")
                if ask and bid and ask > 0 and bid > 0 and ask >= bid:
                    out[row.get("ticker")] = (ask - bid) / ((ask + bid) / 2) * 1e4
        return out


def assemble_history(raw: pd.DataFrame, adj: pd.DataFrame | None,
                     divs: pd.DataFrame | None) -> pd.DataFrame:
    """Combine raw bars, split-adjusted closes and dividend factors into one frame."""
    out = raw.copy()
    if adj is not None and not adj.empty:
        split_close = out["date"].map(adj.set_index("date")["close"])
        split_close = split_close.fillna(out["close"])
    else:
        split_close = out["close"].astype(float)
    factor = np.ones(len(out))
    if divs is not None and not divs.empty and divs["historical_adjustment_factor"].notna().any():
        d = divs.dropna(subset=["historical_adjustment_factor"])
        ex = d["ex_dividend_date"].astype(str).to_numpy()
        f = d["historical_adjustment_factor"].astype(float).to_numpy()
        # first dividend with ex-date strictly after the bar date
        idx = np.searchsorted(ex, out["date"].astype(str).to_numpy(), side="right")
        has = idx < len(ex)
        factor[has] = f[idx[has]]
    out["split_close"] = split_close.astype(float)
    out["adjclose"] = out["split_close"] * factor
    return out


def years_ago(session: str, years: float) -> str:
    d = dt.date.fromisoformat(session)
    return (d - dt.timedelta(days=int(years * 365.25) + 30)).isoformat()
