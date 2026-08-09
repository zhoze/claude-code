"""Pluggable market-data providers.

Default free path: yfinance (prices, options chains, coarse fundamentals).
Recommended: Financial Modeling Prep (FMP) for fundamentals, because its
statements carry `fillingDate` — required for publication-date point-in-time
correctness (spec §2). Any provider that implements the Provider protocol can
be dropped in via config.
"""

from __future__ import annotations

import datetime as dt
import logging
import time
from typing import Any, Protocol

import pandas as pd
import requests

log = logging.getLogger(__name__)


class Provider(Protocol):
    def price_history(self, ticker: str, start: dt.date, end: dt.date) -> pd.DataFrame: ...
    def quote(self, ticker: str) -> dict: ...
    def fundamentals(self, ticker: str) -> dict[str, pd.DataFrame]: ...
    def options_chain(self, ticker: str) -> dict[str, Any]: ...
    def calendar_events(self, ticker: str) -> dict: ...


# --------------------------------------------------------------------------- #
class YFinanceProvider:
    """Free default. Prices are auto-adjusted for splits/dividends by yfinance."""

    name = "yfinance"

    def __init__(self):
        import yfinance as yf

        self._yf = yf

    def price_history(self, ticker: str, start: dt.date, end: dt.date) -> pd.DataFrame:
        df = self._yf.download(
            ticker, start=start, end=end + dt.timedelta(days=1),
            auto_adjust=True, progress=False, threads=False,
        )
        if df is None or df.empty:
            return pd.DataFrame()
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.get_level_values(0)
        df = df.rename(columns=str.lower)[["open", "high", "low", "close", "volume"]]
        df.index = pd.to_datetime(df.index).tz_localize(None)
        df.index.name = "date"
        return df

    def batch_price_history(self, tickers: list[str], start: dt.date,
                            end: dt.date) -> dict[str, pd.DataFrame]:
        out: dict[str, pd.DataFrame] = {}
        for chunk_start in range(0, len(tickers), 100):
            chunk = tickers[chunk_start:chunk_start + 100]
            raw = self._yf.download(chunk, start=start, end=end + dt.timedelta(days=1),
                                    auto_adjust=True, progress=False, group_by="ticker",
                                    threads=True)
            for t in chunk:
                try:
                    df = raw[t].dropna(how="all") if len(chunk) > 1 else raw.dropna(how="all")
                    df = df.rename(columns=str.lower)[["open", "high", "low", "close", "volume"]]
                    df.index = pd.to_datetime(df.index).tz_localize(None)
                    df.index.name = "date"
                    out[t] = df
                except Exception:
                    out[t] = pd.DataFrame()
            time.sleep(0.5)  # be polite
        return out

    def quote(self, ticker: str) -> dict:
        tk = self._yf.Ticker(ticker)
        fi = tk.fast_info
        return {
            "price": getattr(fi, "last_price", None),
            "previous_close": getattr(fi, "previous_close", None),
            "pre_market_price": (tk.info or {}).get("preMarketPrice"),
            "currency": getattr(fi, "currency", None),
            "timestamp": dt.datetime.now(),
        }

    def fundamentals(self, ticker: str) -> dict[str, pd.DataFrame]:
        tk = self._yf.Ticker(ticker)
        out = {}
        for key, attr in (("income", "income_stmt"), ("balance", "balance_sheet"),
                          ("cashflow", "cashflow")):
            try:
                df = getattr(tk, attr)
                out[key] = df.T if df is not None else pd.DataFrame()
            except Exception:
                out[key] = pd.DataFrame()
        try:
            out["info"] = tk.info or {}
        except Exception:
            out["info"] = {}
        # yfinance has no publication dates -> flag for confidence haircut
        out["_has_filing_dates"] = False
        return out

    def options_chain(self, ticker: str) -> dict[str, Any]:
        tk = self._yf.Ticker(ticker)
        try:
            expiries = list(tk.options or [])
        except Exception:
            expiries = []
        if not expiries:
            return {"has_options": False}
        chains = []
        for exp in expiries[:3]:  # near-dated expiries are what matter for liquidity
            try:
                ch = tk.option_chain(exp)
                for side, df in (("call", ch.calls), ("put", ch.puts)):
                    d = df.copy()
                    d["side"], d["expiry"] = side, exp
                    chains.append(d)
            except Exception:
                continue
        if not chains:
            return {"has_options": False}
        return {"has_options": True, "chain": pd.concat(chains, ignore_index=True),
                "expiries": expiries}

    def calendar_events(self, ticker: str) -> dict:
        tk = self._yf.Ticker(ticker)
        out: dict = {"earnings_dates": []}
        try:
            cal = tk.calendar
            if isinstance(cal, dict):
                out["earnings_dates"] = [str(d) for d in cal.get("Earnings Date", [])]
        except Exception:
            pass
        return out


# --------------------------------------------------------------------------- #
class FMPProvider:
    """Financial Modeling Prep — statements include fillingDate (point-in-time safe)."""

    name = "fmp"
    BASE = "https://financialmodelingprep.com/api/v3"

    def __init__(self, api_key: str):
        self.key = api_key
        self._session = requests.Session()

    def _get(self, path: str, **params) -> Any:
        params["apikey"] = self.key
        r = self._session.get(f"{self.BASE}/{path}", params=params, timeout=30)
        r.raise_for_status()
        return r.json()

    def fundamentals(self, ticker: str) -> dict[str, pd.DataFrame]:
        out = {}
        for key, path in (("income", f"income-statement/{ticker}"),
                          ("balance", f"balance-sheet-statement/{ticker}"),
                          ("cashflow", f"cash-flow-statement/{ticker}")):
            try:
                out[key] = pd.DataFrame(self._get(path, period="quarter", limit=24))
            except Exception as e:
                log.warning("FMP %s %s failed: %s", path, ticker, e)
                out[key] = pd.DataFrame()
        try:
            prof = self._get(f"profile/{ticker}")
            out["info"] = prof[0] if prof else {}
        except Exception:
            out["info"] = {}
        try:
            out["estimates"] = pd.DataFrame(
                self._get(f"analyst-estimates/{ticker}", period="quarter", limit=12))
        except Exception:
            out["estimates"] = pd.DataFrame()
        try:
            out["surprises"] = pd.DataFrame(self._get(f"earnings-surprises/{ticker}"))
        except Exception:
            out["surprises"] = pd.DataFrame()
        out["_has_filing_dates"] = True
        return out

    def price_history(self, ticker: str, start: dt.date, end: dt.date) -> pd.DataFrame:
        data = self._get(f"historical-price-full/{ticker}",
                         **{"from": start.isoformat(), "to": end.isoformat()})
        hist = pd.DataFrame(data.get("historical", []))
        if hist.empty:
            return hist
        hist["date"] = pd.to_datetime(hist["date"])
        hist = hist.set_index("date").sort_index()
        hist = hist.rename(columns={"adjClose": "close_adj"})
        cols = {"open": "open", "high": "high", "low": "low", "close_adj": "close",
                "volume": "volume"}
        return hist[list(cols)].rename(columns=cols)

    def quote(self, ticker: str) -> dict:
        q = self._get(f"quote/{ticker}")
        q = q[0] if q else {}
        return {"price": q.get("price"), "previous_close": q.get("previousClose"),
                "pre_market_price": None, "timestamp": dt.datetime.now()}

    def options_chain(self, ticker: str) -> dict[str, Any]:  # FMP options need higher tier
        return {"has_options": None}

    def calendar_events(self, ticker: str) -> dict:
        try:
            e = self._get(f"historical/earning_calendar/{ticker}", limit=8)
            return {"earnings_dates": [row.get("date") for row in e]}
        except Exception:
            return {"earnings_dates": []}


def build_providers(cfg, api_key: str | None):
    """Returns (price_provider, fundamentals_provider). FMP used for fundamentals
    when a key is present; yfinance always available for prices/options."""
    yfp = YFinanceProvider()
    fund = FMPProvider(api_key) if api_key else yfp
    return yfp, fund
