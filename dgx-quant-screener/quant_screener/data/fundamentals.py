"""Point-in-time fundamentals normalized into a flat metric dict per ticker.

Publication-date correctness (spec §2): with FMP, quarterly statements are
filtered by `fillingDate <= as_of`. With yfinance (no filing dates) a
conservative 75-day reporting lag is assumed and the record is flagged so the
confidence score takes a haircut — data limitations are surfaced, not hidden.
"""

from __future__ import annotations

import datetime as dt
import logging
import math
from dataclasses import dataclass, field

import pandas as pd

log = logging.getLogger(__name__)

REPORTING_LAG_DAYS = 75  # conservative assumed lag when no filing dates exist


@dataclass
class FundamentalRecord:
    ticker: str
    as_of: dt.date
    metrics: dict = field(default_factory=dict)     # flat name -> float
    sector: str | None = None
    industry: str | None = None
    has_filing_dates: bool = False
    flags: list[str] = field(default_factory=list)

    def get(self, name: str, default: float = math.nan) -> float:
        v = self.metrics.get(name, default)
        try:
            v = float(v)
        except (TypeError, ValueError):
            return default
        return v if math.isfinite(v) else default


def _pit_quarters(df: pd.DataFrame, as_of: dt.date, has_filing_dates: bool) -> pd.DataFrame:
    """Newest-first quarterly rows observable at as_of."""
    if df is None or df.empty:
        return pd.DataFrame()
    df = df.copy()
    if has_filing_dates and "fillingDate" in df.columns:
        pub = pd.to_datetime(df["fillingDate"], errors="coerce").dt.date
    else:
        period = pd.to_datetime(df.get("date", df.index), errors="coerce")
        pub = (period + pd.Timedelta(days=REPORTING_LAG_DAYS)).dt.date \
            if hasattr(period, "dt") else pd.Series([None] * len(df))
    mask = pd.Series(pub).le(as_of).fillna(False).values
    df = df[mask]
    if "date" in df.columns:
        df = df.sort_values("date", ascending=False)
    return df.reset_index(drop=True)


def _ttm(df: pd.DataFrame, col: str, offset_quarters: int = 0) -> float:
    """Trailing-twelve-month sum starting `offset_quarters` back."""
    if df.empty or col not in df.columns:
        return math.nan
    vals = pd.to_numeric(df[col], errors="coerce").iloc[offset_quarters:offset_quarters + 4]
    return float(vals.sum()) if len(vals) == 4 and not vals.isna().any() else math.nan


def _latest(df: pd.DataFrame, col: str, offset: int = 0) -> float:
    if df.empty or col not in df.columns or len(df) <= offset:
        return math.nan
    v = pd.to_numeric(df[col], errors="coerce").iloc[offset]
    return float(v) if pd.notna(v) else math.nan


def build_fundamental_record(ticker: str, raw: dict, as_of: dt.date,
                             market_cap: float | None,
                             price: float | None) -> FundamentalRecord:
    """Flatten provider statements into the metric names the 10 screens consume.
    Missing inputs stay NaN — screens treat NaN as 'cannot evaluate', not zero."""
    has_fd = bool(raw.get("_has_filing_dates"))
    inc = _pit_quarters(raw.get("income", pd.DataFrame()), as_of, has_fd)
    bal = _pit_quarters(raw.get("balance", pd.DataFrame()), as_of, has_fd)
    cf = _pit_quarters(raw.get("cashflow", pd.DataFrame()), as_of, has_fd)
    info = raw.get("info", {}) or {}

    rec = FundamentalRecord(ticker=ticker, as_of=as_of, has_filing_dates=has_fd)
    rec.sector = info.get("sector")
    rec.industry = info.get("industry")
    if not has_fd:
        rec.flags.append("NO_FILING_DATES_ASSUMED_LAG")
    if inc.empty or bal.empty or cf.empty:
        rec.flags.append("INCOMPLETE_STATEMENTS")

    m = rec.metrics
    # --- income (TTM current and year-ago for momentum/Piotroski deltas)
    m["revenue_ttm"] = _ttm(inc, "revenue")
    m["revenue_ttm_prior"] = _ttm(inc, "revenue", 4)
    m["net_income_ttm"] = _ttm(inc, "netIncome")
    m["net_income_ttm_prior"] = _ttm(inc, "netIncome", 4)
    m["gross_profit_ttm"] = _ttm(inc, "grossProfit")
    m["gross_profit_ttm_prior"] = _ttm(inc, "grossProfit", 4)
    m["operating_income_ttm"] = _ttm(inc, "operatingIncome")
    m["operating_income_ttm_prior"] = _ttm(inc, "operatingIncome", 4)
    m["ebitda_ttm"] = _ttm(inc, "ebitda")
    m["interest_expense_ttm"] = _ttm(inc, "interestExpense")
    m["eps_ttm"] = _ttm(inc, "epsdiluted") if "epsdiluted" in inc.columns else math.nan
    m["eps_ttm_prior"] = _ttm(inc, "epsdiluted", 4) if "epsdiluted" in inc.columns else math.nan

    # --- balance (latest and year-ago quarters)
    m["total_assets"] = _latest(bal, "totalAssets")
    m["total_assets_prior"] = _latest(bal, "totalAssets", 4)
    m["total_debt"] = _latest(bal, "totalDebt")
    m["total_debt_prior"] = _latest(bal, "totalDebt", 4)
    m["cash"] = _latest(bal, "cashAndCashEquivalents")
    m["current_assets"] = _latest(bal, "totalCurrentAssets")
    m["current_liabilities"] = _latest(bal, "totalCurrentLiabilities")
    m["current_assets_prior"] = _latest(bal, "totalCurrentAssets", 4)
    m["current_liabilities_prior"] = _latest(bal, "totalCurrentLiabilities", 4)
    m["inventory"] = _latest(bal, "inventory")
    m["equity"] = _latest(bal, "totalStockholdersEquity")
    m["shares_out"] = _latest(bal, "commonStock") or math.nan
    m["retained_earnings"] = _latest(bal, "retainedEarnings")
    m["working_capital"] = m["current_assets"] - m["current_liabilities"] \
        if pd.notna(m["current_assets"]) and pd.notna(m["current_liabilities"]) else math.nan

    # --- cashflow
    m["ocf_ttm"] = _ttm(cf, "operatingCashFlow") if "operatingCashFlow" in cf.columns \
        else _ttm(cf, "netCashProvidedByOperatingActivities")
    m["ocf_ttm_prior"] = _ttm(cf, "operatingCashFlow", 4) if "operatingCashFlow" in cf.columns \
        else _ttm(cf, "netCashProvidedByOperatingActivities", 4)
    m["capex_ttm"] = abs(_ttm(cf, "capitalExpenditure"))
    m["dividends_paid_ttm"] = abs(_ttm(cf, "dividendsPaid")) if "dividendsPaid" in cf.columns \
        else abs(_ttm(cf, "commonDividendsPaid"))
    m["buybacks_ttm"] = abs(_ttm(cf, "commonStockRepurchased"))
    m["issuance_ttm"] = _ttm(cf, "commonStockIssued")
    m["fcf_ttm"] = m["ocf_ttm"] - m["capex_ttm"] \
        if pd.notna(m["ocf_ttm"]) and pd.notna(m["capex_ttm"]) else math.nan
    m["fcf_ttm_prior"] = m["ocf_ttm_prior"] - abs(_ttm(cf, "capitalExpenditure", 4)) \
        if pd.notna(m["ocf_ttm_prior"]) else math.nan

    # --- shares / valuation anchors
    shares = info.get("sharesOutstanding") or info.get("mktCap", 0) / price \
        if price else info.get("sharesOutstanding")
    m["shares_outstanding"] = shares or math.nan
    prior_shares = _latest(bal, "weightedAverageShsOutDil", 4) if "weightedAverageShsOutDil" in bal.columns else math.nan
    m["shares_outstanding_prior"] = prior_shares
    m["market_cap"] = market_cap or info.get("marketCap") or info.get("mktCap") or math.nan
    m["price"] = price or math.nan
    net_debt = (m["total_debt"] - m["cash"]) \
        if pd.notna(m["total_debt"]) and pd.notna(m["cash"]) else math.nan
    m["net_debt"] = net_debt
    m["net_debt_prior"] = (m["total_debt_prior"] - _latest(bal, "cashAndCashEquivalents", 4)) \
        if pd.notna(m["total_debt_prior"]) else math.nan
    m["enterprise_value"] = m["market_cap"] + net_debt \
        if pd.notna(m["market_cap"]) and pd.notna(net_debt) else math.nan

    # --- analyst estimates / surprises (FMP only) for fundamental momentum
    est = raw.get("estimates", pd.DataFrame())
    if isinstance(est, pd.DataFrame) and not est.empty:
        est = _pit_quarters(est, as_of, has_fd)
        m["est_eps_fwd"] = _latest(est, "estimatedEpsAvg")
        m["est_eps_fwd_prior"] = _latest(est, "estimatedEpsAvg", 1)
        m["est_analyst_count"] = _latest(est, "numberAnalystsEstimatedEps")
    sur = raw.get("surprises", pd.DataFrame())
    if isinstance(sur, pd.DataFrame) and not sur.empty and "actualEarningResult" in sur.columns:
        sur = sur.head(4)
        act = pd.to_numeric(sur["actualEarningResult"], errors="coerce")
        estv = pd.to_numeric(sur["estimatedEarning"], errors="coerce")
        with pd.option_context("mode.use_inf_as_na", True):
            surprise = ((act - estv) / estv.abs()).dropna()
        m["avg_eps_surprise"] = float(surprise.mean()) if len(surprise) else math.nan

    # --- yfinance info fallbacks for ratios the screens can use directly
    for src, dest in (("trailingPE", "pe_trailing"), ("forwardPE", "pe_forward"),
                      ("priceToBook", "pb"), ("priceToSalesTrailing12Months", "ps"),
                      ("pegRatio", "peg"), ("dividendYield", "dividend_yield_info"),
                      ("returnOnEquity", "roe_info"), ("grossMargins", "gross_margin_info"),
                      ("operatingMargins", "op_margin_info"),
                      ("earningsGrowth", "eps_growth_info"),
                      ("revenueGrowth", "rev_growth_info")):
        if info.get(src) is not None:
            m[dest] = info[src]
    return rec
