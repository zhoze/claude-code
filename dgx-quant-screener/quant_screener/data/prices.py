"""Adjusted daily price histories with local parquet caching."""

from __future__ import annotations

import datetime as dt
import logging

import pandas as pd

from .store import Store

log = logging.getLogger(__name__)

# Macro/benchmark symbols used across the pipeline (yfinance notation)
BENCHMARKS = {
    "russell3000": "^RUA", "russell2000": "^RUT", "sp500": "^GSPC", "nasdaq": "^IXIC",
    "iwm": "IWM", "spy": "SPY",
}
MACRO_SYMBOLS = {
    "es_futures": "ES=F", "nq_futures": "NQ=F", "rty_futures": "RTY=F", "ym_futures": "YM=F",
    "vix": "^VIX", "vix3m": "^VIX3M",
    "ust2y": "^IRX", "ust5y": "^FVX", "ust10y": "^TNX", "ust30y": "^TYX",
    "dxy": "DX-Y.NYB", "eurusd": "EURUSD=X", "usdjpy": "USDJPY=X",
    "gbpusd": "GBPUSD=X", "usdcny": "USDCNY=X",
    "wti": "CL=F", "brent": "BZ=F", "natgas": "NG=F", "gasoline": "RB=F",
    "gold": "GC=F", "silver": "SI=F", "copper": "HG=F",
    "hyg": "HYG", "lqd": "LQD",
}
SECTOR_ETFS = {
    "Technology": "XLK", "Financial Services": "XLF", "Healthcare": "XLV",
    "Consumer Cyclical": "XLY", "Consumer Defensive": "XLP", "Energy": "XLE",
    "Industrials": "XLI", "Basic Materials": "XLB", "Utilities": "XLU",
    "Real Estate": "XLRE", "Communication Services": "XLC",
}


class PriceLibrary:
    """Cached access to daily OHLCV; incremental refresh per run."""

    def __init__(self, provider, store: Store, history_days: int = 3650):
        self.provider = provider
        self.store = store
        self.history_days = history_days

    def get(self, ticker: str, as_of: dt.date, refresh: bool = True) -> pd.DataFrame:
        cached = self.store.load_cached(f"px_{ticker}")
        start = as_of - dt.timedelta(days=self.history_days)
        if cached is not None and len(cached):
            last = cached.index[-1].date()
            if not refresh or last >= as_of:
                return cached[cached.index.date <= as_of]
            fresh = self.provider.price_history(ticker, last + dt.timedelta(days=1), as_of)
            if len(fresh):
                cached = pd.concat([cached, fresh])
                cached = cached[~cached.index.duplicated(keep="last")].sort_index()
                self.store.save_cache(f"px_{ticker}", cached)
            return cached[cached.index.date <= as_of]
        df = self.provider.price_history(ticker, start, as_of)
        if len(df):
            self.store.save_cache(f"px_{ticker}", df)
        return df

    def get_many(self, tickers: list[str], as_of: dt.date) -> dict[str, pd.DataFrame]:
        out: dict[str, pd.DataFrame] = {}
        missing: list[str] = []
        for t in tickers:
            cached = self.store.load_cached(f"px_{t}")
            if cached is not None and len(cached) and cached.index[-1].date() >= as_of - dt.timedelta(days=4):
                out[t] = cached[cached.index.date <= as_of]
            else:
                missing.append(t)
        if missing and hasattr(self.provider, "batch_price_history"):
            start = as_of - dt.timedelta(days=self.history_days)
            fetched = self.provider.batch_price_history(missing, start, as_of)
            for t, df in fetched.items():
                if df is not None and len(df):
                    self.store.save_cache(f"px_{t}", df)
                    out[t] = df[df.index.date <= as_of]
                else:
                    out[t] = pd.DataFrame()
        else:
            for t in missing:
                out[t] = self.get(t, as_of)
        return out


def daily_returns(px: pd.DataFrame) -> pd.Series:
    return px["close"].pct_change().dropna()
