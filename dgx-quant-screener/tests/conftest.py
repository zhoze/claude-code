import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np
import pandas as pd
import pytest


@pytest.fixture
def synthetic_px():
    """~5y of synthetic OHLCV with drift + vol clustering."""
    rng = np.random.default_rng(42)
    n = 1300
    idx = pd.bdate_range("2021-01-04", periods=n)
    vol = 0.015 * (1 + 0.5 * np.sin(np.arange(n) / 60))
    rets = rng.normal(0.0004, vol)
    close = 50 * np.exp(np.cumsum(rets))
    open_ = close * (1 + rng.normal(0, 0.003, n))
    high = np.maximum(open_, close) * (1 + abs(rng.normal(0, 0.006, n)))
    low = np.minimum(open_, close) * (1 - abs(rng.normal(0, 0.006, n)))
    volume = rng.integers(1e6, 5e6, n).astype(float)
    df = pd.DataFrame({"open": open_, "high": high, "low": low,
                       "close": close, "volume": volume}, index=idx)
    df.index.name = "date"
    return df


@pytest.fixture
def cfg():
    from quant_screener.config import load_config

    return load_config()


def make_record(ticker="TEST", good=True):
    """A FundamentalRecord populated with coherent statement metrics."""
    import datetime as dt

    from quant_screener.data.fundamentals import FundamentalRecord

    rec = FundamentalRecord(ticker=ticker, as_of=dt.date(2026, 8, 7),
                            has_filing_dates=True)
    scale = 1.0 if good else 0.3
    m = rec.metrics
    m.update({
        "revenue_ttm": 10e9, "revenue_ttm_prior": 9e9 if good else 11e9,
        "net_income_ttm": 1.5e9 * scale, "net_income_ttm_prior": 1.1e9 * scale,
        "gross_profit_ttm": 4.5e9, "gross_profit_ttm_prior": 3.9e9,
        "operating_income_ttm": 2.0e9 * scale, "operating_income_ttm_prior": 1.6e9 * scale,
        "ebitda_ttm": 2.6e9 * scale, "interest_expense_ttm": 0.1e9,
        "eps_ttm": 5.0 * scale, "eps_ttm_prior": 4.0 * scale,
        "total_assets": 20e9, "total_assets_prior": 19e9,
        "total_debt": 4e9 if good else 14e9, "total_debt_prior": 5e9 if good else 12e9,
        "cash": 3e9, "current_assets": 8e9, "current_liabilities": 4e9,
        "current_assets_prior": 7e9, "current_liabilities_prior": 4e9,
        "inventory": 1e9, "equity": 10e9, "retained_earnings": 6e9,
        "working_capital": 4e9,
        "ocf_ttm": 2.2e9 * scale, "ocf_ttm_prior": 1.8e9 * scale,
        "capex_ttm": 0.5e9, "dividends_paid_ttm": 0.4e9,
        "buybacks_ttm": 0.6e9 if good else 0.0, "issuance_ttm": 0.0 if good else 1.0e9,
        "fcf_ttm": 1.7e9 * scale, "fcf_ttm_prior": 1.3e9 * scale,
        "shares_outstanding": 3e8, "shares_outstanding_prior": 3.05e8 if good else 2.7e8,
        "market_cap": 30e9 if good else 60e9, "price": 100.0,
        "net_debt": 1e9 if good else 11e9, "net_debt_prior": 2e9 if good else 9e9,
        "enterprise_value": 31e9 if good else 71e9,
        "pe_trailing": 20.0 if good else 55.0, "pe_forward": 17.0 if good else 50.0,
        "peg": 1.1 if good else 4.0,
        "avg_eps_surprise": 0.06 if good else -0.05,
        "est_eps_fwd": 5.5, "est_eps_fwd_prior": 5.2 if good else 6.0,
    })
    rec.sector = "Technology"
    return rec
