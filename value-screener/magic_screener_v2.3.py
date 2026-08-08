#!/usr/bin/env python3
"""AEGIS Magic Screener v2.3 — evidence-oriented fundamental + technical entry engine.

This is an upgrade of the original ``magic_screener.py`` prototype.

What changed in v2
------------------
1. REMOVED synthetic/hash-generated ATR and relative-volume values.
2. Uses real daily OHLCV history for:
   - ATR(14) / ATR %
   - realized volatility
   - real relative volume (RVOL vs prior 20 sessions)
   - 20/55/252-day breakouts
   - 20/50/100/200-day moving averages and slopes
   - 5/20/60/120/252-day momentum
   - RSI(14), MACD(12,26,9), Bollinger bandwidth/percentile
   - rolling 20-day VWAP and optional anchored VWAP
   - real weekly trend from aggregated weekly candles
3. Optional benchmark history (e.g. SPY) for relative-strength features.
4. Separates:
      WHAT?  -> fundamental quality/value score
      WHEN?  -> technical entry score / trigger
      RISK?  -> market/liquidity/volatility risk score
   Portfolio sizing is deliberately left to Mean-CVaR / cuOpt.
5. Scores are explicitly HEURISTIC until calibrated. A built-in walk-forward
   backtest can test whether high entry scores actually improve future outcomes.
6. Exports a flat ML-ready feature record for later cuML training.

v2.1 corrections
----------------
- Split/dividend consistency: when the data source provides ``adjClose``, all
  OHLC fields are rescaled by the adjustment factor so long-lookback features
  (SMA200, perf252, 52-week breakout) are not corrupted by splits.
- Live mode targets FMP's current ``/stable`` endpoints first and falls back
  to the legacy ``/api/v3`` routes for older API keys.
- A failed benchmark fetch no longer aborts the whole run.
- ``proximity52wPct`` is only reported with a full 252 bars of history.
- Removed falsy-zero coercions on trend/ATR/VWAP fields.

v2.2 validation / execution corrections
---------------------------------------
- Backtests now generate the setup at day T close and execute at day T+1 open.
- Configurable entry/exit slippage and per-side trading costs are included.
- Default backtest mode is non-overlapping trades; signal-observation mode remains
  available for ML/calibration research, with optional transition-only filtering.
- Fundamental scoring rejects semantically invalid negative P/E, EV/EBITDA and
  debt/equity values instead of accidentally rewarding them as "cheap" or safe.
- Fundamental and technical scores now expose raw score, coverage and a
  confidence-adjusted score that shrinks sparse observations toward neutral 50.
- OHLC validation is stricter and normalization records whether adjusted-close
  rescaling was used; vendor-reported volume remains unadjusted and is flagged.
- EOD setup status is explicitly separated from live/intraday execution status.
- Screener risk is more orthogonal to alpha: volatility, downside volatility,
  gap risk, liquidity, drawdown and beta replace trend/fundamental duplication.
- Backtests add benchmark/unconditional baselines, expectancy, profit factor,
  win rate, holding period, approximate Sharpe/Sortino, max drawdown, bootstrap
  confidence intervals and score-bucket calibration statistics.

v2.3 corrections
----------------
- Backtest loop off-by-one fixed: the final eligible signal day (with a full
  next-open entry and forward horizon) is no longer silently skipped.
- Equity-curve statistics (max strategy drawdown, Sharpe/Sortino) are reported
  only in non-overlapping ``trade`` mode; in ``signal`` mode overlapping
  forward windows made them look like tradable-strategy stats when they are not.
- Removed a reintroduced falsy-``or`` fallback on the adjusted entry score.
- The score-calibration table is printed even when no trade qualifies — that is
  exactly when the signal-level evidence matters most.

The script has no third-party Python dependencies.

Examples
--------
# Local historical CSV (date,open,high,low,close,volume):
python magic_screener_v2.3.py NVDA --history-csv nvda.csv

# Add benchmark-relative strength:
python magic_screener_v2.3.py NVDA --history-csv nvda.csv --benchmark-csv spy.csv

# Optional anchored VWAP from an earnings/catalyst date:
python magic_screener_v2.3.py NVDA --history-csv nvda.csv --anchor-date 2026-05-20

# Live mode using Financial Modeling Prep (current /stable endpoints, with
# automatic fallback to the legacy v3 routes for older API keys):
export FMP_API_KEY='...'
python magic_screener_v2.3.py NVDA --live

# Walk-forward validation of the entry model:
python magic_screener_v2.3.py NVDA --history-csv nvda.csv --backtest \
    --bt-horizon 20 --bt-profit 0.05 --bt-stop 0.03 --bt-min-score 75

# Export one ML-ready feature row:
python magic_screener_v2.3.py NVDA --history-csv nvda.csv --features-json nvda_features.json

IMPORTANT
---------
- This is a research/educational model, not investment advice.
- ``technicalEntryScore`` is NOT a probability. It is a deterministic heuristic
  score until calibrated on point-in-time historical data.
- Do not use snapshot-only/manual mode for live entry decisions; real OHLCV
  history is required for reliable technical signals.
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import json
import math
import os
import random
import statistics
import sys
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Any, Iterable, Optional


# ---------------------------------------------------------------------------
# Generic helpers
# ---------------------------------------------------------------------------


def clamp(v: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, v))


def safe_mean(values: Iterable[float]) -> Optional[float]:
    vals = [float(v) for v in values if v is not None and math.isfinite(float(v))]
    return statistics.fmean(vals) if vals else None


def safe_stdev(values: Iterable[float], sample: bool = True) -> Optional[float]:
    vals = [float(v) for v in values if v is not None and math.isfinite(float(v))]
    if len(vals) < (2 if sample else 1):
        return None
    return statistics.stdev(vals) if sample else statistics.pstdev(vals)


def pct_change(new: Optional[float], old: Optional[float]) -> Optional[float]:
    if new is None or old in (None, 0):
        return None
    return (new / old - 1.0) * 100.0


def as_float(v: Any) -> Optional[float]:
    if v is None or v == "":
        return None
    try:
        x = float(v)
        return x if math.isfinite(x) else None
    except (TypeError, ValueError):
        return None


def first_float(*values: Any) -> Optional[float]:
    """Return the first parseable numeric value, preserving legitimate zeroes."""
    for v in values:
        x = as_float(v)
        if x is not None:
            return x
    return None


def fmt(v: Any, digits: int = 2, suffix: str = "") -> str:
    if v is None:
        return "n/a"
    if isinstance(v, bool):
        return "yes" if v else "no"
    if isinstance(v, (int, float)):
        if isinstance(v, float) and not math.isfinite(v):
            return "n/a"
        return f"{v:.{digits}f}{suffix}"
    return str(v)


def percentile_rank(history: list[float], value: float) -> Optional[float]:
    vals = [v for v in history if v is not None and math.isfinite(v)]
    if not vals:
        return None
    return 100.0 * sum(v <= value for v in vals) / len(vals)


def parse_date(text: str) -> dt.date:
    return dt.date.fromisoformat(text[:10])


def percentile_value(values: Iterable[float], q: float) -> Optional[float]:
    vals = sorted(float(v) for v in values if v is not None and math.isfinite(float(v)))
    if not vals:
        return None
    q = clamp(q, 0.0, 1.0)
    pos = (len(vals) - 1) * q
    lo = int(math.floor(pos))
    hi = int(math.ceil(pos))
    if lo == hi:
        return vals[lo]
    w = pos - lo
    return vals[lo] * (1.0 - w) + vals[hi] * w


def confidence_adjusted_score(raw_score: Optional[float], coverage_pct: float, neutral: float = 50.0) -> Optional[float]:
    """Shrink a sparse score toward neutral instead of over-trusting partial data."""
    if raw_score is None:
        return None
    c = clamp(float(coverage_pct) / 100.0, 0.0, 1.0)
    return neutral + (float(raw_score) - neutral) * c


def confidence_label(coverage_pct: float) -> str:
    if coverage_pct >= 90:
        return "HIGH"
    if coverage_pct >= 70:
        return "MEDIUM"
    return "LOW"


# ---------------------------------------------------------------------------
# OHLCV model and loading
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Bar:
    date: dt.date
    open: float
    high: float
    low: float
    close: float
    volume: float
    # True when raw OHLC was rescaled to an adjusted-close basis.  Kept on each
    # daily record so downstream reports can audit adjustment usage.
    adjusted: bool = False


def _normalize_bar(row: dict[str, Any]) -> Optional[Bar]:
    date_raw = row.get("date") or row.get("datetime") or row.get("timestamp")
    if not date_raw:
        return None
    try:
        d = parse_date(str(date_raw))
    except Exception:
        return None
    o = as_float(row.get("open"))
    h = as_float(row.get("high"))
    l = as_float(row.get("low"))
    c = as_float(row.get("close"))
    adj = first_float(row.get("adjClose"), row.get("adj_close"), row.get("adjclose"))
    v = as_float(row.get("volume"))
    if c is None:
        c, adj = adj, None
    if None in (o, h, l, c, v):
        return None
    # Keep OHLC internally consistent: when an adjusted close is provided,
    # apply the same factor to open/high/low.  This is suitable for technical
    # continuity, but volume remains vendor-reported because an adjClose factor
    # may include both splits and dividends; blindly inverse-scaling volume can
    # therefore introduce a different error.  We record this in dataQuality.
    adjusted = False
    if adj is not None and adj > 0 and c > 0 and not math.isclose(adj, c, rel_tol=1e-9):
        factor = adj / c
        o, h, l, c = o * factor, h * factor, l * factor, adj
        adjusted = True
    # Strong OHLC integrity checks.  Reject impossible candles rather than let
    # one bad vendor row contaminate ATR, breakouts, VWAP and backtests.
    if min(o, h, l, c) <= 0 or v < 0:
        return None
    tol = max(1e-10, abs(c) * 1e-10)
    if h + tol < max(o, c, l) or l - tol > min(o, c, h):
        return None
    return Bar(d, float(o), float(h), float(l), float(c), float(v), adjusted)


def normalize_history(rows: Iterable[dict[str, Any]]) -> list[Bar]:
    by_date: dict[dt.date, Bar] = {}
    for row in rows:
        b = _normalize_bar(row)
        if b:
            by_date[b.date] = b
    return [by_date[d] for d in sorted(by_date)]


def load_history_csv(path: str) -> list[Bar]:
    with open(path, newline="", encoding="utf-8-sig") as f:
        rows = list(csv.DictReader(f))
    bars = normalize_history(rows)
    if not bars:
        raise SystemExit(f"No valid OHLCV rows found in {path}")
    return bars


def load_history_json(path: str) -> list[Bar]:
    with open(path, encoding="utf-8") as f:
        payload = json.load(f)
    if isinstance(payload, dict):
        rows = payload.get("historical") or payload.get("data") or payload.get("results") or []
    else:
        rows = payload
    bars = normalize_history(rows)
    if not bars:
        raise SystemExit(f"No valid OHLCV rows found in {path}")
    return bars


# ---------------------------------------------------------------------------
# Market-data API (FMP-compatible live mode)
# ---------------------------------------------------------------------------


FMP_STABLE = "https://financialmodelingprep.com/stable"
FMP_V3 = "https://financialmodelingprep.com/api/v3"  # legacy fallback for older API keys


def _fmp_key() -> str:
    key = os.environ.get("FMP_API_KEY", "").strip()
    if not key:
        raise SystemExit("--live requires FMP_API_KEY in the environment")
    return key


def _get_json(url: str) -> Any:
    req = urllib.request.Request(url, headers={"User-Agent": "AEGIS-Magic-Screener/2.3"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read().decode("utf-8"))


def fetch_fmp_history(ticker: str, years: int = 4) -> list[Bar]:
    key = _fmp_key()
    today = dt.date.today()
    start = today - dt.timedelta(days=int(years * 365.25) + 30)
    symbol = urllib.parse.quote(ticker.replace(".", "-"))
    span = f"from={start.isoformat()}&to={today.isoformat()}&apikey={urllib.parse.quote(key)}"
    urls = [
        f"{FMP_STABLE}/historical-price-eod/full?symbol={symbol}&{span}",
        f"{FMP_V3}/historical-price-full/{symbol}?{span}",
    ]
    last_error: Optional[Exception] = None
    best = 0
    for url in urls:
        try:
            payload = _get_json(url)
        except Exception as exc:
            last_error = exc
            continue
        rows = payload.get("historical", []) if isinstance(payload, dict) else payload
        bars = normalize_history(rows if isinstance(rows, list) else [])
        if len(bars) >= 60:
            return bars
        best = max(best, len(bars))
    detail = f"; last error: {last_error}" if last_error else ""
    raise RuntimeError(
        f"FMP returned only {best} valid daily bars for {ticker} (need >= 60){detail}"
    )


def _fmp_first(urls: list[str]) -> dict[str, Any]:
    """Return the first record from the first endpoint that answers usefully."""
    for url in urls:
        try:
            data = _get_json(url)
        except Exception:
            continue
        if isinstance(data, list) and data and isinstance(data[0], dict):
            return data[0]
        if isinstance(data, dict) and data:
            return data
    return {}


def fetch_fmp_snapshot(ticker: str) -> dict[str, Any]:
    """Best-effort fundamentals. Missing fields stay None instead of being invented."""
    key = urllib.parse.quote(_fmp_key())
    symbol = urllib.parse.quote(ticker.replace(".", "-"))
    q0 = _fmp_first([
        f"{FMP_STABLE}/quote?symbol={symbol}&apikey={key}",
        f"{FMP_V3}/quote/{symbol}?apikey={key}",
    ])
    km0 = _fmp_first([
        f"{FMP_STABLE}/key-metrics-ttm?symbol={symbol}&apikey={key}",
        f"{FMP_V3}/key-metrics-ttm/{symbol}?apikey={key}",
    ])
    r0 = _fmp_first([
        f"{FMP_STABLE}/ratios-ttm?symbol={symbol}&apikey={key}",
        f"{FMP_V3}/ratios-ttm/{symbol}?apikey={key}",
    ])

    pe = as_float(q0.get("pe"))
    roic = as_float(km0.get("roicTTM"))
    roe = first_float(km0.get("roeTTM"), r0.get("returnOnEquityTTM"))
    roa = first_float(km0.get("returnOnTangibleAssetsTTM"), r0.get("returnOnAssetsTTM"))
    earnings_yield = as_float(km0.get("earningsYieldTTM"))
    fcf_yield = as_float(km0.get("freeCashFlowYieldTTM"))
    debt_to_equity = first_float(r0.get("debtEquityRatioTTM"), km0.get("debtToEquityTTM"))
    gross_margin = as_float(r0.get("grossProfitMarginTTM"))
    operating_margin = as_float(r0.get("operatingProfitMarginTTM"))

    def to_pct(x: Optional[float]) -> Optional[float]:
        if x is None:
            return None
        # FMP ratios are generally decimal fractions; protect already-percent inputs.
        return x * 100.0 if abs(x) <= 5 else x

    return {
        "ticker": ticker,
        "name": q0.get("name") or ticker,
        "sector": q0.get("sector") or "",
        "marketCap": as_float(q0.get("marketCap")),
        "beta": first_float(q0.get("beta"), km0.get("betaTTM")),
        "pe": pe if pe and pe > 0 else None,
        "pb": as_float(km0.get("pbRatioTTM")),
        "evToEbitda": as_float(km0.get("enterpriseValueOverEBITDATTM")),
        "roic": to_pct(roic),
        "roe": to_pct(roe),
        "roa": to_pct(roa),
        "grossMargin": to_pct(gross_margin),
        "operatingMargin": to_pct(operating_margin),
        "earnYield": to_pct(earnings_yield),
        "fcfYield": to_pct(fcf_yield),
        "debtToEquity": debt_to_equity,
    }


# ---------------------------------------------------------------------------
# Technical calculations
# ---------------------------------------------------------------------------


def sma(values: list[float], period: int) -> Optional[float]:
    return safe_mean(values[-period:]) if len(values) >= period else None


def ema_series(values: list[float], period: int) -> list[float]:
    if not values:
        return []
    alpha = 2.0 / (period + 1.0)
    out = [values[0]]
    for x in values[1:]:
        out.append(alpha * x + (1.0 - alpha) * out[-1])
    return out


def sma_series(values: list[float], period: int) -> list[Optional[float]]:
    out: list[Optional[float]] = [None] * len(values)
    if period <= 0:
        return out
    running = 0.0
    for i, x in enumerate(values):
        running += x
        if i >= period:
            running -= values[i - period]
        if i >= period - 1:
            out[i] = running / period
    return out


def rsi_wilder(values: list[float], period: int = 14) -> Optional[float]:
    if len(values) < period + 1:
        return None
    gains: list[float] = []
    losses: list[float] = []
    for i in range(1, len(values)):
        chg = values[i] - values[i - 1]
        gains.append(max(chg, 0.0))
        losses.append(max(-chg, 0.0))
    avg_gain = safe_mean(gains[:period]) or 0.0
    avg_loss = safe_mean(losses[:period]) or 0.0
    for i in range(period, len(gains)):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return 100.0 - 100.0 / (1.0 + rs)


def true_ranges(bars: list[Bar]) -> list[float]:
    out: list[float] = []
    for i, b in enumerate(bars):
        if i == 0:
            out.append(b.high - b.low)
        else:
            pc = bars[i - 1].close
            out.append(max(b.high - b.low, abs(b.high - pc), abs(b.low - pc)))
    return out


def atr_wilder(bars: list[Bar], period: int = 14) -> Optional[float]:
    trs = true_ranges(bars)
    if len(trs) < period:
        return None
    atr = safe_mean(trs[:period]) or 0.0
    for tr in trs[period:]:
        atr = (atr * (period - 1) + tr) / period
    return atr


def realized_volatility(closes: list[float], period: int = 20) -> Optional[float]:
    if len(closes) < period + 1:
        return None
    returns = [math.log(closes[i] / closes[i - 1]) for i in range(len(closes) - period, len(closes))]
    sd = safe_stdev(returns)
    return sd * math.sqrt(252) * 100.0 if sd is not None else None


def macd(closes: list[float]) -> tuple[Optional[float], Optional[float], Optional[float], Optional[float]]:
    if len(closes) < 35:
        return None, None, None, None
    e12 = ema_series(closes, 12)
    e26 = ema_series(closes, 26)
    line = [a - b for a, b in zip(e12, e26)]
    signal = ema_series(line, 9)
    hist = [a - b for a, b in zip(line, signal)]
    accel = hist[-1] - hist[-2] if len(hist) >= 2 else None
    return line[-1], signal[-1], hist[-1], accel


def rolling_vwap(bars: list[Bar], period: int = 20) -> Optional[float]:
    if len(bars) < period:
        return None
    window = bars[-period:]
    denom = sum(b.volume for b in window)
    if denom <= 0:
        return None
    return sum(((b.high + b.low + b.close) / 3.0) * b.volume for b in window) / denom


def anchored_vwap(bars: list[Bar], anchor: Optional[dt.date]) -> Optional[float]:
    if anchor is None:
        return None
    window = [b for b in bars if b.date >= anchor]
    if not window:
        return None
    denom = sum(b.volume for b in window)
    if denom <= 0:
        return None
    return sum(((b.high + b.low + b.close) / 3.0) * b.volume for b in window) / denom


def bollinger_bandwidth_series(closes: list[float], period: int = 20) -> list[Optional[float]]:
    out: list[Optional[float]] = [None] * len(closes)
    for i in range(period - 1, len(closes)):
        w = closes[i - period + 1:i + 1]
        m = safe_mean(w)
        sd = safe_stdev(w, sample=False)
        if m and sd is not None:
            upper = m + 2 * sd
            lower = m - 2 * sd
            out[i] = (upper - lower) / m * 100.0
    return out


def rate_of_change(closes: list[float], lookback: int) -> Optional[float]:
    if len(closes) <= lookback:
        return None
    return pct_change(closes[-1], closes[-1 - lookback])


def breakout_level(bars: list[Bar], lookback: int) -> tuple[Optional[float], Optional[bool], Optional[float]]:
    if len(bars) <= lookback:
        return None, None, None
    prior = bars[-lookback - 1:-1]
    level = max(b.high for b in prior)
    close = bars[-1].close
    return level, close > level, pct_change(close, level)


def ma_slope(closes: list[float], period: int, slope_lookback: int = 10) -> Optional[float]:
    series = sma_series(closes, period)
    if len(series) <= slope_lookback:
        return None
    now = series[-1]
    old = series[-1 - slope_lookback]
    return pct_change(now, old) if now is not None and old is not None else None


def downside_deviation_pct(closes: list[float], period: int = 60) -> Optional[float]:
    if len(closes) < period + 1:
        return None
    rets = [closes[i] / closes[i - 1] - 1.0 for i in range(len(closes) - period, len(closes)) if closes[i - 1] > 0]
    if not rets:
        return None
    downside_sq = [min(r, 0.0) ** 2 for r in rets]
    return math.sqrt(statistics.fmean(downside_sq)) * math.sqrt(252.0) * 100.0


def gap_risk_pct(bars: list[Bar], period: int = 60, quantile: float = 0.95) -> Optional[float]:
    if len(bars) < 2:
        return None
    start = max(1, len(bars) - period)
    gaps = []
    for i in range(start, len(bars)):
        prev = bars[i - 1].close
        if prev > 0:
            gaps.append(abs(bars[i].open / prev - 1.0) * 100.0)
    return percentile_value(gaps, quantile)


def max_drawdown_pct(closes: list[float], period: int = 252) -> Optional[float]:
    vals = closes[-period:] if len(closes) >= period else closes[:]
    if len(vals) < 2:
        return None
    peak = vals[0]
    worst = 0.0
    for x in vals:
        peak = max(peak, x)
        if peak > 0:
            worst = min(worst, x / peak - 1.0)
    return worst * 100.0


def aggregate_weekly(bars: list[Bar]) -> list[Bar]:
    if not bars:
        return []
    groups: dict[tuple[int, int], list[Bar]] = {}
    for b in bars:
        iso = b.date.isocalendar()
        groups.setdefault((iso.year, iso.week), []).append(b)
    out: list[Bar] = []
    for key in sorted(groups):
        g = groups[key]
        out.append(Bar(
            date=g[-1].date,
            open=g[0].open,
            high=max(x.high for x in g),
            low=min(x.low for x in g),
            close=g[-1].close,
            volume=sum(x.volume for x in g),
        ))
    return out


def align_benchmark(stock: list[Bar], benchmark: list[Bar]) -> tuple[list[Bar], list[Bar]]:
    bmap = {b.date: b for b in benchmark}
    s2, b2 = [], []
    for s in stock:
        b = bmap.get(s.date)
        if b:
            s2.append(s)
            b2.append(b)
    return s2, b2


def relative_strength_features(stock: list[Bar], benchmark: Optional[list[Bar]]) -> dict[str, Optional[float]]:
    out = {"rs20": None, "rs60": None, "rs120": None, "rs252": None, "rsComposite": None}
    if not benchmark:
        return out
    s, b = align_benchmark(stock, benchmark)
    if len(s) < 30:
        return out
    sc = [x.close for x in s]
    bc = [x.close for x in b]
    vals = []
    for lb in (20, 60, 120, 252):
        sr = rate_of_change(sc, lb)
        br = rate_of_change(bc, lb)
        rs = (sr - br) if sr is not None and br is not None else None
        out[f"rs{lb}"] = rs
        if rs is not None:
            vals.append((lb, rs))
    weights = {20: 0.35, 60: 0.30, 120: 0.20, 252: 0.15}
    wsum = sum(weights[lb] for lb, _ in vals)
    if wsum:
        out["rsComposite"] = sum(weights[lb] * rs for lb, rs in vals) / wsum
    return out


def compute_technical_features(
    bars: list[Bar],
    benchmark: Optional[list[Bar]] = None,
    anchor_date: Optional[dt.date] = None,
) -> dict[str, Any]:
    if len(bars) < 60:
        raise ValueError("At least 60 daily bars are required; 260+ is strongly recommended")

    closes = [b.close for b in bars]
    vols = [b.volume for b in bars]
    latest = bars[-1]

    ma20 = sma(closes, 20)
    ma50 = sma(closes, 50)
    ma100 = sma(closes, 100)
    ma200 = sma(closes, 200)
    ema20 = ema_series(closes, 20)[-1]
    rsi14 = rsi_wilder(closes, 14)
    atr14 = atr_wilder(bars, 14)
    atr_pct = atr14 / latest.close * 100.0 if atr14 is not None else None
    rv20 = realized_volatility(closes, 20)
    downside60 = downside_deviation_pct(closes, 60)
    gap95_60 = gap_risk_pct(bars, 60, 0.95)
    maxdd252 = max_drawdown_pct(closes, 252)

    prev20v = vols[-21:-1] if len(vols) >= 21 else vols[:-1]
    avg_vol20 = safe_mean(prev20v)
    rel_vol20 = latest.volume / avg_vol20 if avg_vol20 and avg_vol20 > 0 else None
    dollar_vol20 = safe_mean([bars[i].close * bars[i].volume for i in range(max(0, len(bars) - 20), len(bars))])
    vol_sd20 = safe_stdev(prev20v)
    volume_z20 = ((latest.volume - avg_vol20) / vol_sd20
                  if avg_vol20 is not None and vol_sd20 not in (None, 0) else None)

    b20_lvl, b20, b20_pct = breakout_level(bars, 20)
    b55_lvl, b55, b55_pct = breakout_level(bars, 55)
    b252_lvl, b252, b252_pct = breakout_level(bars, 252)
    high252 = max(b.high for b in bars[-252:]) if len(bars) >= 252 else None
    proximity_52w = latest.close / high252 * 100.0 if high252 else None

    mline, msignal, mhist, maccel = macd(closes)
    bw_series = bollinger_bandwidth_series(closes, 20)
    bw = bw_series[-1]
    bw_hist = [x for x in bw_series[-252:] if x is not None]
    bw_pctile = percentile_rank(bw_hist, bw) if bw is not None else None

    weekly = aggregate_weekly(bars)
    wcl = [b.close for b in weekly]
    wsma10 = sma(wcl, 10)
    wsma30 = sma(wcl, 30)
    w_slope10 = ma_slope(wcl, 10, 4) if len(wcl) >= 15 else None
    weekly_trend = "neutral"
    if wsma10 is not None and wsma30 is not None:
        if weekly[-1].close > wsma10 > wsma30 and (w_slope10 or 0) > 0:
            weekly_trend = "bull"
        elif weekly[-1].close < wsma10 < wsma30 and (w_slope10 or 0) < 0:
            weekly_trend = "bear"

    vwap20 = rolling_vwap(bars, 20)
    avwap = anchored_vwap(bars, anchor_date)

    rs = relative_strength_features(bars, benchmark)

    out: dict[str, Any] = {
        "asOf": latest.date.isoformat(),
        "price": latest.close,
        "open": latest.open,
        "high": latest.high,
        "low": latest.low,
        "volume": latest.volume,
        "sma20": ma20,
        "sma50": ma50,
        "sma100": ma100,
        "sma200": ma200,
        "ema20": ema20,
        "sma20Slope10d": ma_slope(closes, 20, 10),
        "sma50Slope10d": ma_slope(closes, 50, 10),
        "sma200Slope20d": ma_slope(closes, 200, 20),
        "rsi14": rsi14,
        "atr14": atr14,
        "atrPct": atr_pct,
        "realizedVol20AnnPct": rv20,
        "downsideDeviation60AnnPct": downside60,
        "gapRisk95_60Pct": gap95_60,
        "maxDrawdown252Pct": maxdd252,
        "avgVol20": avg_vol20,
        "relVol20": rel_vol20,
        "volumeZ20": volume_z20,
        "avgDollarVol20": dollar_vol20,
        "perf5": rate_of_change(closes, 5),
        "perf20": rate_of_change(closes, 20),
        "perf60": rate_of_change(closes, 60),
        "perf120": rate_of_change(closes, 120),
        "perf252": rate_of_change(closes, 252),
        "breakout20Level": b20_lvl,
        "breakout20": b20,
        "breakout20Pct": b20_pct,
        "breakout55Level": b55_lvl,
        "breakout55": b55,
        "breakout55Pct": b55_pct,
        "breakout252Level": b252_lvl,
        "breakout252": b252,
        "breakout252Pct": b252_pct,
        "proximity52wPct": proximity_52w,
        "macd": mline,
        "macdSignal": msignal,
        "macdHist": mhist,
        "macdHistAccel": maccel,
        "bollingerBandwidthPct": bw,
        "bollingerBandwidthPercentile": bw_pctile,
        "vwap20": vwap20,
        "aboveVwap20": latest.close > vwap20 if vwap20 is not None else None,
        "anchorDate": anchor_date.isoformat() if anchor_date else None,
        "anchoredVwap": avwap,
        "aboveAnchoredVwap": latest.close > avwap if avwap is not None else None,
        "weeklyClose": weekly[-1].close if weekly else None,
        "weeklySma10": wsma10,
        "weeklySma30": wsma30,
        "weeklySma10Slope4w": w_slope10,
        "weeklyTrend": weekly_trend,
    }
    out.update(rs)
    return out


# ---------------------------------------------------------------------------
# Fundamental scoring — independent of technical timing
# ---------------------------------------------------------------------------


def _score_linear(value: Optional[float], bad: float, good: float, higher_is_better: bool = True) -> Optional[float]:
    if value is None:
        return None
    if good == bad:
        return 50.0
    if higher_is_better:
        return clamp((value - bad) / (good - bad) * 100.0, 0.0, 100.0)
    return clamp((bad - value) / (bad - good) * 100.0, 0.0, 100.0)


def compute_fundamental_score(s: dict[str, Any]) -> dict[str, Any]:
    """Transparent quality/value composite with semantic validation.

    ``fundamentalRawScore`` is the weighted score on fields that are present.
    ``fundamentalScore`` is coverage-adjusted and shrunk toward neutral 50 so a
    single excellent metric cannot masquerade as a fully observed 100/100 stock.
    """
    warnings: list[str] = []

    def valid_positive_multiple(key: str) -> Optional[float]:
        v = as_float(s.get(key))
        if v is None:
            return None
        if v <= 0:
            warnings.append(f"{key} <= 0 is not treated as a cheap positive multiple")
            return None
        return v

    pe = valid_positive_multiple("pe")
    ev_ebitda = valid_positive_multiple("evToEbitda")
    dte = as_float(s.get("debtToEquity"))
    if dte is not None and dte < 0:
        warnings.append("debtToEquity < 0 is ambiguous/often associated with negative equity; leverage score omitted")
        dte = None

    components: list[tuple[str, Optional[float], float]] = [
        # Quality / profitability
        ("roic", _score_linear(as_float(s.get("roic")), 8, 30, True), 0.16),
        ("roe", _score_linear(as_float(s.get("roe")), 8, 30, True), 0.06),
        ("grossMargin", _score_linear(as_float(s.get("grossMargin")), 15, 60, True), 0.05),
        ("operatingMargin", _score_linear(as_float(s.get("operatingMargin")), 5, 30, True), 0.06),
        # Value
        ("earnYield", _score_linear(as_float(s.get("earnYield")), 1, 8, True), 0.12),
        ("fcfYield", _score_linear(as_float(s.get("fcfYield")), 0, 8, True), 0.12),
        ("pe", _score_linear(pe, 45, 12, False), 0.07),
        ("evToEbitda", _score_linear(ev_ebitda, 25, 8, False), 0.06),
        # Financial health / earnings quality
        ("debtToEquity", _score_linear(dte, 2.0, 0.3, False), 0.05),
        ("piotroski", _score_linear(as_float(s.get("piotroski")), 3, 8, True), 0.06),
        ("accrualQuality", _score_linear(as_float(s.get("accrualQuality")), -1, 1, True), 0.04),
        # Growth / expectations
        ("revenueGrowth", _score_linear(as_float(s.get("revenueGrowth")), -5, 20, True), 0.04),
        ("epsGrowth", _score_linear(as_float(s.get("epsGrowth")), -10, 25, True), 0.04),
        ("earningsRevision", _score_linear(as_float(s.get("earningsRevision")), -5, 8, True), 0.08),
        ("estimizeDivergence", _score_linear(as_float(s.get("estimizeDivergence")), -3, 5, True), 0.05),
        ("shareholderYield", _score_linear(as_float(s.get("shareholderYield")), -2, 8, True), 0.04),
    ]
    present = [(name, score, w) for name, score, w in components if score is not None]
    if not present:
        return {
            "fundamentalScore": None,
            "fundamentalRawScore": None,
            "fundamentalCoverage": 0.0,
            "fundamentalConfidence": "LOW",
            "fundamentalComponents": {},
            "fundamentalWarnings": warnings,
        }
    weight_sum = sum(w for _, _, w in present)
    raw_score = sum(float(sc) * w for _, sc, w in present) / weight_sum
    total_weight = sum(w for _, _, w in components)
    coverage = weight_sum / total_weight * 100.0
    adjusted = confidence_adjusted_score(raw_score, coverage)
    return {
        "fundamentalScore": adjusted,
        "fundamentalRawScore": raw_score,
        "fundamentalCoverage": coverage,
        "fundamentalConfidence": confidence_label(coverage),
        "fundamentalComponents": {name: sc for name, sc, _ in present},
        "fundamentalWarnings": warnings,
    }


# ---------------------------------------------------------------------------
# Technical entry model — deliberately interpretable and backtestable
# ---------------------------------------------------------------------------


def _trend_component(f: dict[str, Any]) -> float:
    p = f["price"]
    points = 0.0
    possible = 0.0
    checks = [
        (f.get("sma20"), 12), (f.get("sma50"), 18),
        (f.get("sma100"), 12), (f.get("sma200"), 18),
    ]
    for ma, w in checks:
        if ma is not None:
            possible += w
            if p > ma:
                points += w
    if f.get("sma20") and f.get("sma50") and f.get("sma200"):
        possible += 15
        if f["sma20"] > f["sma50"] > f["sma200"]:
            points += 15
    for key, w in (("sma20Slope10d", 8), ("sma50Slope10d", 10), ("sma200Slope20d", 7)):
        v = f.get(key)
        if v is not None:
            possible += w
            if v > 0:
                points += w
    if f.get("weeklyTrend"):
        possible += 20
        points += 20 if f["weeklyTrend"] == "bull" else 0 if f["weeklyTrend"] == "bear" else 10
    return 50.0 if possible == 0 else points / possible * 100.0


def _breakout_component(f: dict[str, Any]) -> float:
    score = 25.0
    if f.get("breakout20"):
        score += 25
    if f.get("breakout55"):
        score += 25
    if f.get("breakout252"):
        score += 15
    prox = f.get("proximity52wPct")
    if prox is not None:
        if prox >= 99:
            score += 10
        elif prox >= 95:
            score += 7
        elif prox >= 90:
            score += 4
    return clamp(score, 0, 100)


def _momentum_component(f: dict[str, Any]) -> float:
    pairs = [("perf20", 0.35, 12), ("perf60", 0.30, 20), ("perf120", 0.20, 30), ("perf252", 0.15, 40)]
    total = 0.0
    weights = 0.0
    for key, w, target in pairs:
        v = f.get(key)
        if v is not None:
            # 50 = flat, 100 ≈ strong positive target, 0 ≈ equally strong negative.
            sc = clamp(50.0 + (v / target) * 50.0, 0.0, 100.0)
            total += sc * w
            weights += w
    return total / weights if weights else 50.0


def _relative_strength_component(f: dict[str, Any]) -> Optional[float]:
    rs = f.get("rsComposite")
    if rs is None:
        return None
    # +/- 15 percentage points of benchmark outperformance maps roughly to 0/100.
    return clamp(50.0 + rs / 15.0 * 50.0, 0.0, 100.0)


def _volume_component(f: dict[str, Any]) -> float:
    rv = f.get("relVol20")
    vz = f.get("volumeZ20")
    if rv is None:
        return 50.0
    score = 25.0
    if rv >= 2.0:
        score += 55
    elif rv >= 1.5:
        score += 45
    elif rv >= 1.2:
        score += 30
    elif rv >= 1.0:
        score += 20
    else:
        score += max(0, rv * 20)
    if vz is not None and vz >= 2:
        score += 15
    elif vz is not None and vz >= 1:
        score += 8
    return clamp(score, 0, 100)


def _vwap_component(f: dict[str, Any]) -> float:
    score = 50.0
    if f.get("aboveVwap20") is True:
        score += 20
    elif f.get("aboveVwap20") is False:
        score -= 20
    if f.get("aboveAnchoredVwap") is True:
        score += 25
    elif f.get("aboveAnchoredVwap") is False:
        score -= 25
    p = f.get("price")
    e = f.get("ema20")
    if p is not None and e is not None:
        score += 5 if p > e else -5
    return clamp(score, 0, 100)


def _volatility_setup_component(f: dict[str, Any]) -> float:
    """Rewards tradable volatility and squeeze->breakout setups; penalizes extremes."""
    atrp = f.get("atrPct")
    bwp = f.get("bollingerBandwidthPercentile")
    score = 50.0
    if atrp is not None:
        if 1.0 <= atrp <= 4.0:
            score += 15
        elif atrp > 7.0:
            score -= 25
        elif atrp > 5.0:
            score -= 10
    if bwp is not None:
        if bwp <= 20 and (f.get("breakout20") or f.get("breakout55")):
            score += 30
        elif bwp <= 25:
            score += 12  # contraction setup, not yet confirmation
        elif bwp >= 90:
            score -= 15
    return clamp(score, 0, 100)


def _oscillator_component(f: dict[str, Any]) -> float:
    rsi = f.get("rsi14")
    hist = f.get("macdHist")
    accel = f.get("macdHistAccel")
    score = 50.0
    if rsi is not None:
        if 50 <= rsi <= 68:
            score += 20
        elif 40 <= rsi < 50:
            score += 8
        elif rsi > 78:
            score -= 20
        elif rsi < 30:
            score -= 10  # oversold is not automatically bullish
    if hist is not None:
        score += 15 if hist > 0 else -10
    if accel is not None:
        score += 15 if accel > 0 else -8
    return clamp(score, 0, 100)


def compute_entry_model(f: dict[str, Any]) -> dict[str, Any]:
    comps: dict[str, Optional[float]] = {
        "trend": _trend_component(f),
        "breakout": _breakout_component(f),
        "momentum": _momentum_component(f),
        "relativeStrength": _relative_strength_component(f),
        "volume": _volume_component(f),
        "vwap": _vwap_component(f),
        "volatilitySetup": _volatility_setup_component(f),
        "oscillator": _oscillator_component(f),
    }
    weights = {
        "trend": 0.22,
        "breakout": 0.20,
        "momentum": 0.16,
        "relativeStrength": 0.14,
        "volume": 0.10,
        "vwap": 0.08,
        "volatilitySetup": 0.05,
        "oscillator": 0.05,
    }
    present = [(k, v, weights[k]) for k, v in comps.items() if v is not None]
    wsum = sum(w for _, _, w in present)
    raw_score = sum(float(v) * w for _, v, w in present) / wsum if wsum else 50.0
    coverage = wsum / sum(weights.values()) * 100.0
    adjusted = confidence_adjusted_score(raw_score, coverage)
    entry_score = adjusted if adjusted is not None else 50.0

    trend = comps["trend"] if comps["trend"] is not None else 50.0
    breakout_confirm = bool(f.get("breakout20") or f.get("breakout55"))
    rvol = f.get("relVol20") if f.get("relVol20") is not None else 0.0
    mh = f.get("macdHist")
    ma = f.get("macdHistAccel")
    macd_ok = mh is not None and ma is not None and mh > 0 and ma >= 0
    weekly_ok = f.get("weeklyTrend") == "bull"
    adv = f.get("avgDollarVol20")
    liquid = adv is not None and adv >= 5_000_000
    coverage_ok = coverage >= 70.0

    setup = "WAIT"
    reasons: list[str] = []
    if coverage_ok and entry_score >= 78 and trend >= 70 and breakout_confirm and rvol >= 1.3 and weekly_ok and liquid:
        setup = "STRONG_LONG_SETUP"
        reasons.extend(["trend confirmed", "20/55d breakout", "volume confirms", "weekly trend bull"])
    elif coverage_ok and entry_score >= 70 and trend >= 68 and breakout_confirm and weekly_ok and liquid:
        setup = "LONG_SETUP"
        reasons.extend(["trend confirmed", "breakout present", "weekly trend bull"])
    else:
        # Pullback setup: strong established trend, RSI cooled, price reclaimed EMA20/VWAP.
        rsi = f.get("rsi14")
        price = f.get("price")
        ema20 = f.get("ema20")
        reclaim = price is not None and ema20 is not None and price > ema20 and f.get("aboveVwap20") is True
        if coverage_ok and entry_score >= 66 and trend >= 75 and rsi is not None and 42 <= rsi <= 60 and reclaim and macd_ok and liquid:
            setup = "PULLBACK_SETUP"
            reasons.extend(["strong primary trend", "RSI reset", "EMA20/VWAP reclaim", "MACD improving"])

    if not reasons:
        if not coverage_ok:
            reasons.append("technical coverage below 70%")
        if trend < 60:
            reasons.append("trend not strong enough")
        if not weekly_ok:
            reasons.append("weekly trend not bullish")
        if not breakout_confirm:
            reasons.append("no confirmed 20/55d breakout")
        if rvol < 1.0:
            reasons.append("volume below normal")
        if not liquid:
            reasons.append("20d dollar liquidity below $5m")

    # Daily bars can define an EOD setup, but they cannot prove an intraday
    # execution trigger such as session VWAP reclaim/opening-range breakout.
    live_status = "NONE" if setup == "WAIT" else "AWAIT_INTRADAY_CONFIRMATION"
    return {
        "technicalEntryScore": entry_score,
        "technicalRawScore": raw_score,
        "technicalCoverage": coverage,
        "technicalConfidence": confidence_label(coverage),
        "eodSetup": setup,
        "entryTrigger": live_status,
        "executionReady": False,
        "signalType": "EOD_SETUP",
        "entryReasons": reasons,
        "technicalComponents": comps,
    }


# ---------------------------------------------------------------------------
# Risk layer — separate from expected return / entry timing
# ---------------------------------------------------------------------------


def compute_risk_score(f: dict[str, Any], snapshot: dict[str, Any]) -> dict[str, Any]:
    """Security-level implementation risk, intentionally separate from alpha.

    Portfolio correlation/concentration/scenario CVaR belong downstream in
    Mean-CVaR/cuOpt; this layer focuses on single-security tradability/tail risk.
    """
    dims: dict[str, Optional[float]] = {}

    atrp = f.get("atrPct")
    rv = f.get("realizedVol20AnnPct")
    if atrp is not None or rv is not None:
        a = 35.0 if atrp is None else clamp(atrp / 7.0 * 100.0, 0, 100)
        r = 45.0 if rv is None else clamp(rv / 80.0 * 100.0, 0, 100)
        dims["volatility"] = 0.55 * a + 0.45 * r
    else:
        dims["volatility"] = None

    downside = f.get("downsideDeviation60AnnPct")
    dims["downsideVolatility"] = None if downside is None else clamp(downside / 55.0 * 100.0, 0, 100)

    gap = f.get("gapRisk95_60Pct")
    dims["gapRisk"] = None if gap is None else clamp(gap / 6.0 * 100.0, 0, 100)

    adv = f.get("avgDollarVol20")
    if adv is None:
        dims["liquidity"] = None
    elif adv >= 100_000_000:
        dims["liquidity"] = 10.0
    elif adv >= 20_000_000:
        dims["liquidity"] = 25.0
    elif adv >= 5_000_000:
        dims["liquidity"] = 50.0
    elif adv >= 1_000_000:
        dims["liquidity"] = 75.0
    else:
        dims["liquidity"] = 95.0

    dd = f.get("maxDrawdown252Pct")
    dims["drawdown"] = None if dd is None else clamp(abs(min(float(dd), 0.0)) / 50.0 * 100.0, 0, 100)

    beta = as_float(snapshot.get("beta"))
    dims["beta"] = None if beta is None else clamp((beta - 0.6) / (2.0 - 0.6) * 100.0, 0, 100)

    weights = {
        "volatility": 0.24,
        "downsideVolatility": 0.20,
        "gapRisk": 0.18,
        "liquidity": 0.18,
        "drawdown": 0.15,
        "beta": 0.05,
    }
    present = [(k, v, weights[k]) for k, v in dims.items() if v is not None]
    if not present:
        return {"riskScore": None, "riskCoverage": 0.0, "riskComponents": dims}
    wsum = sum(w for _, _, w in present)
    score = sum(float(v) * w for _, v, w in present) / wsum
    return {
        "riskScore": score,
        "riskCoverage": wsum / sum(weights.values()) * 100.0,
        "riskComponents": dims,
    }


# ---------------------------------------------------------------------------
# Combined scoring and ML-ready export
# ---------------------------------------------------------------------------


def analyze(
    ticker: str,
    bars: list[Bar],
    snapshot: Optional[dict[str, Any]] = None,
    benchmark: Optional[list[Bar]] = None,
    anchor_date: Optional[dt.date] = None,
) -> dict[str, Any]:
    snapshot = dict(snapshot or {})
    snapshot["ticker"] = ticker
    tech = compute_technical_features(bars, benchmark=benchmark, anchor_date=anchor_date)
    fundamental = compute_fundamental_score(snapshot)
    entry = compute_entry_model(tech)
    risk = compute_risk_score(tech, snapshot)

    out = {
        "ticker": ticker,
        "name": snapshot.get("name") or ticker,
        "sector": snapshot.get("sector") or "",
        "dataQuality": {
            "dailyBars": len(bars),
            "historyStart": bars[0].date.isoformat(),
            "historyEnd": bars[-1].date.isoformat(),
            "has252dHistory": len(bars) >= 253,
            "hasBenchmark": benchmark is not None,
            "hasAnchor": anchor_date is not None,
            "syntheticMarketFields": False,
            "adjustedBars": sum(1 for b in bars if b.adjusted),
            "priceAdjustmentPolicy": "adjClose-rescaled OHLC when vendor adjClose differs from close",
            "volumeAdjustmentPolicy": "vendor-reported volume; not inverse-scaled from adjClose factor",
        },
        "technical": tech,
        "fundamental": fundamental,
        "entry": entry,
        "risk": risk,
        "snapshot": snapshot,
    }
    return out


def flatten_ml_features(result: dict[str, Any]) -> dict[str, Any]:
    """Flat feature row suitable for CSV/cuDF/cuML ingestion.

    Labels/outcomes are intentionally not included here: build them from future
    returns in a point-in-time training pipeline so there is no look-ahead bias.
    """
    out: dict[str, Any] = {
        "ticker": result["ticker"],
        "asOf": result["technical"]["asOf"],
        "fundamentalScore": result["fundamental"].get("fundamentalScore"),
        "fundamentalRawScore": result["fundamental"].get("fundamentalRawScore"),
        "fundamentalCoverage": result["fundamental"].get("fundamentalCoverage"),
        "technicalEntryScore": result["entry"].get("technicalEntryScore"),
        "technicalRawScore": result["entry"].get("technicalRawScore"),
        "technicalCoverage": result["entry"].get("technicalCoverage"),
        "eodSetup": result["entry"].get("eodSetup"),
        "entryTrigger": result["entry"].get("entryTrigger"),
        "riskScore": result["risk"].get("riskScore"),
    }
    for k, v in result["technical"].items():
        if isinstance(v, (str, int, float, bool)) or v is None:
            out[k] = v
    for k, v in result["snapshot"].items():
        if k not in out and (isinstance(v, (str, int, float, bool)) or v is None):
            out[f"fund_{k}"] = v
    return out


# ---------------------------------------------------------------------------
# Walk-forward validation of the heuristic entry model
# ---------------------------------------------------------------------------


def _net_trade_return(entry_price: float, exit_price: float, commission_bps: float) -> float:
    fee = max(0.0, commission_bps) / 10_000.0
    return (exit_price * (1.0 - fee)) / (entry_price * (1.0 + fee)) - 1.0


def _path_outcome(
    future: list[Bar],
    entry: float,
    profit_target: float,
    stop_loss: float,
    exit_slippage_bps: float = 0.0,
    commission_bps: float = 0.0,
) -> dict[str, Any]:
    """Evaluate a long trade from an already executed next-open entry price."""
    target = entry * (1.0 + profit_target)
    stop = entry * (1.0 - stop_loss)
    outcome = "NEITHER"
    hit_day: Optional[int] = None
    exit_price: Optional[float] = None
    mfe = -math.inf
    mae = math.inf
    slip = max(0.0, exit_slippage_bps) / 10_000.0

    for i, b in enumerate(future, 1):
        mfe = max(mfe, b.high / entry - 1.0)
        mae = min(mae, b.low / entry - 1.0)

        # Opening gaps happen before the intraday high/low path is observed.
        if b.open <= stop:
            outcome, hit_day = "STOP", i
            exit_price = b.open * (1.0 - slip)
            break
        if b.open >= target:
            outcome, hit_day = "TARGET", i
            exit_price = b.open * (1.0 - slip)
            break

        # Conservative ordering if both levels are touched in one daily candle.
        if b.low <= stop:
            outcome, hit_day = "STOP", i
            exit_price = stop * (1.0 - slip)
            break
        if b.high >= target:
            outcome, hit_day = "TARGET", i
            exit_price = target * (1.0 - slip)
            break

    holding_days = hit_day if hit_day is not None else len(future)
    if exit_price is None:
        exit_price = future[-1].close * (1.0 - slip) if future else entry
    gross_return = exit_price / entry - 1.0
    net_return = _net_trade_return(entry, exit_price, commission_bps)
    return {
        "outcome": outcome,
        "hitDay": hit_day,
        "holdingDays": holding_days,
        "exitPrice": exit_price,
        "grossReturn": gross_return,
        "netReturn": net_return,
        "endReturn": net_return,  # backward-compatible alias
        "mfe": mfe if mfe != -math.inf else None,
        "mae": mae if mae != math.inf else None,
    }


def _equity_max_drawdown(returns: list[float]) -> Optional[float]:
    if not returns:
        return None
    equity = 1.0
    peak = 1.0
    worst = 0.0
    for r in returns:
        equity *= 1.0 + r
        peak = max(peak, equity)
        worst = min(worst, equity / peak - 1.0)
    return worst


def _profit_factor(returns: list[float]) -> Optional[float]:
    gains = sum(r for r in returns if r > 0)
    losses = -sum(r for r in returns if r < 0)
    if losses == 0:
        return math.inf if gains > 0 else None
    return gains / losses


def _sharpe_sortino_approx(returns: list[float], avg_holding_days: Optional[float]) -> tuple[Optional[float], Optional[float]]:
    if len(returns) < 2 or not avg_holding_days or avg_holding_days <= 0:
        return None, None
    mean_r = statistics.fmean(returns)
    sd = safe_stdev(returns)
    periods_per_year = 252.0 / avg_holding_days
    sharpe = None if sd in (None, 0) else mean_r / sd * math.sqrt(periods_per_year)
    downside = [min(r, 0.0) ** 2 for r in returns]
    dd = math.sqrt(statistics.fmean(downside)) if downside else 0.0
    sortino = None if dd == 0 else mean_r / dd * math.sqrt(periods_per_year)
    return sharpe, sortino


def _bootstrap_mean_ci(values: list[float], samples: int = 1000, seed: int = 42) -> tuple[Optional[float], Optional[float]]:
    if len(values) < 2:
        return None, None
    rng = random.Random(seed)
    n = len(values)
    means = []
    for _ in range(max(100, samples)):
        means.append(statistics.fmean(values[rng.randrange(n)] for _ in range(n)))
    means.sort()
    lo = percentile_value(means, 0.025)
    hi = percentile_value(means, 0.975)
    return lo, hi


def _score_bucket(score: float) -> str:
    if score < 50:
        return "<50"
    if score < 60:
        return "50-59"
    if score < 70:
        return "60-69"
    if score < 80:
        return "70-79"
    if score < 90:
        return "80-89"
    return "90-100"


def _summarize_calibration(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    order = ["<50", "50-59", "60-69", "70-79", "80-89", "90-100"]
    out: list[dict[str, Any]] = []
    for bucket in order:
        g = [x for x in rows if x["bucket"] == bucket]
        if not g:
            continue
        out.append({
            "bucket": bucket,
            "observations": len(g),
            "targetHitRate": sum(x["outcome"] == "TARGET" for x in g) / len(g),
            "stopHitRate": sum(x["outcome"] == "STOP" for x in g) / len(g),
            "avgFixedHorizonReturn": statistics.fmean(x["fixedHorizonReturn"] for x in g),
        })
    return out


def backtest_entry_model(
    bars: list[Bar],
    benchmark: Optional[list[Bar]] = None,
    horizon: int = 20,
    profit_target: float = 0.05,
    stop_loss: float = 0.03,
    min_score: float = 75.0,
    warmup: int = 260,
    mode: str = "trade",
    slippage_bps: float = 5.0,
    commission_bps: float = 1.0,
    cooldown_days: int = 0,
    transition_only: bool = False,
) -> dict[str, Any]:
    """Walk-forward EOD-setup validation with next-session-open execution.

    mode='trade' blocks overlapping positions and is the default realistic
    strategy simulation. mode='signal' keeps every qualifying observation for
    ML/signal research and may contain overlapping forward windows.
    """
    if mode not in {"trade", "signal"}:
        raise ValueError("mode must be 'trade' or 'signal'")
    if len(bars) < warmup + horizon + 5:
        raise ValueError(f"Backtest needs at least {warmup + horizon + 5} bars")
    if horizon < 1 or profit_target <= 0 or stop_loss <= 0:
        raise ValueError("horizon, profit_target and stop_loss must be positive")

    bmap = {b.date: b for b in benchmark} if benchmark else {}
    signals: list[dict[str, Any]] = []
    calibration_rows: list[dict[str, Any]] = []
    unconditional_returns: list[float] = []
    blocked_until = -1
    prev_setup = "WAIT"
    slip = max(0.0, slippage_bps) / 10_000.0

    # Use only information available up to each historical signal date T.
    # The last eligible T is len(bars) - horizon - 1: entry at bar T+1's open
    # still leaves a full `horizon`-bar forward window.
    for i in range(warmup, len(bars) - horizon):
        hist = bars[:i + 1]
        bhist = [bmap[x.date] for x in hist if x.date in bmap] if benchmark else None
        try:
            f = compute_technical_features(hist, benchmark=bhist)
            e = compute_entry_model(f)
        except Exception:
            continue

        entry_bar = bars[i + 1]
        entry_price = entry_bar.open * (1.0 + slip)
        full_future = bars[i + 1:i + 1 + horizon]
        fixed_exit = full_future[-1].close * (1.0 - slip)
        fixed_ret = _net_trade_return(entry_price, fixed_exit, commission_bps)
        unconditional_returns.append(fixed_ret)

        # Calibration intentionally records all historical score observations;
        # it is signal-level research, not a non-overlapping portfolio P&L.
        cal_path = _path_outcome(
            full_future, entry_price, profit_target, stop_loss,
            exit_slippage_bps=slippage_bps, commission_bps=commission_bps,
        )
        calibration_rows.append({
            "bucket": _score_bucket(float(e["technicalEntryScore"])),
            "score": e["technicalEntryScore"],
            "outcome": cal_path["outcome"],
            "fixedHorizonReturn": fixed_ret,
        })

        setup = e.get("eodSetup", "WAIT")
        score_ok = e["technicalEntryScore"] >= min_score
        setup_ok = setup != "WAIT"
        transition_ok = (not transition_only) or (setup_ok and setup != prev_setup)
        prev_setup = setup
        if not (score_ok and setup_ok and transition_ok):
            continue
        if mode == "trade" and i < blocked_until:
            continue

        path = _path_outcome(
            full_future, entry_price, profit_target, stop_loss,
            exit_slippage_bps=slippage_bps, commission_bps=commission_bps,
        )
        exit_idx = i + int(path["holdingDays"])
        exit_idx = min(exit_idx, len(bars) - 1)
        exit_date = bars[exit_idx].date

        benchmark_horizon_return = None
        benchmark_trade_return = None
        if benchmark:
            b_entry = bmap.get(entry_bar.date)
            b_horizon = bmap.get(full_future[-1].date)
            b_exit = bmap.get(exit_date)
            if b_entry and b_horizon:
                b_ep = b_entry.open * (1.0 + slip)
                b_xp = b_horizon.close * (1.0 - slip)
                benchmark_horizon_return = _net_trade_return(b_ep, b_xp, commission_bps)
            if b_entry and b_exit:
                b_ep = b_entry.open * (1.0 + slip)
                b_xp = b_exit.close * (1.0 - slip)
                benchmark_trade_return = _net_trade_return(b_ep, b_xp, commission_bps)

        signals.append({
            "signalDate": bars[i].date.isoformat(),
            "entryDate": entry_bar.date.isoformat(),
            "exitDate": exit_date.isoformat(),
            "score": e["technicalEntryScore"],
            "rawScore": e.get("technicalRawScore"),
            "coverage": e.get("technicalCoverage"),
            "setup": setup,
            "entryPrice": entry_price,
            "exitPrice": path["exitPrice"],
            "fixedHorizonReturn": fixed_ret,
            "benchmarkHorizonReturn": benchmark_horizon_return,
            "benchmarkTradeReturn": benchmark_trade_return,
            **path,
        })

        if mode == "trade":
            # Exit bar can generate a new EOD setup after the prior intraday exit;
            # cooldown_days can explicitly require additional flat sessions.
            blocked_until = exit_idx + max(0, cooldown_days)

    n = len(signals)
    calibration = _summarize_calibration(calibration_rows)
    base_avg = statistics.fmean(unconditional_returns) if unconditional_returns else None
    if n == 0:
        return {
            "mode": mode,
            "signals": 0,
            "independentTrades": 0 if mode == "trade" else None,
            "targetHitRate": None,
            "stopHitRate": None,
            "avgHorizonReturn": None,
            "medianHorizonReturn": None,
            "avgMFE": None,
            "avgMAE": None,
            "avgUnconditionalHorizonReturn": base_avg,
            "scoreCalibration": calibration,
            "details": [],
        }

    returns = [x["netReturn"] for x in signals]
    fixed_returns = [x["fixedHorizonReturn"] for x in signals]
    mfes = [x["mfe"] for x in signals if x["mfe"] is not None]
    maes = [x["mae"] for x in signals if x["mae"] is not None]
    holding = [float(x["holdingDays"]) for x in signals]
    benchmark_hr = [x["benchmarkHorizonReturn"] for x in signals if x["benchmarkHorizonReturn"] is not None]
    avg_hold = statistics.fmean(holding) if holding else None
    # Equity-curve statistics assume sequential, non-overlapping full-capital
    # trades; in 'signal' mode the forward windows overlap, so compounding them
    # into a drawdown/Sharpe would misrepresent a tradable strategy.
    if mode == "trade":
        sharpe, sortino = _sharpe_sortino_approx(returns, avg_hold)
        equity_dd = _equity_max_drawdown(returns)
    else:
        sharpe, sortino, equity_dd = None, None, None
    ci_lo, ci_hi = _bootstrap_mean_ci(returns)
    avg_fixed = statistics.fmean(fixed_returns)
    avg_bench = statistics.fmean(benchmark_hr) if benchmark_hr else None

    return {
        "mode": mode,
        "signals": n,
        "independentTrades": n if mode == "trade" else None,
        "targetHitRate": sum(x["outcome"] == "TARGET" for x in signals) / n,
        "stopHitRate": sum(x["outcome"] == "STOP" for x in signals) / n,
        "neitherRate": sum(x["outcome"] == "NEITHER" for x in signals) / n,
        "winRate": sum(x["netReturn"] > 0 for x in signals) / n,
        "avgHorizonReturn": statistics.fmean(returns),
        "medianHorizonReturn": statistics.median(returns),
        "avgFixedHorizonReturn": avg_fixed,
        "avgUnconditionalHorizonReturn": base_avg,
        "selectionLiftVsUnconditional": (avg_fixed - base_avg) if base_avg is not None else None,
        "avgBenchmarkHorizonReturn": avg_bench,
        "avgExcessHorizonReturnVsBenchmark": (avg_fixed - avg_bench) if avg_bench is not None else None,
        "expectancy": statistics.fmean(returns),
        "profitFactor": _profit_factor(returns),
        "avgHoldingDays": avg_hold,
        "maxDrawdown": equity_dd,
        "sharpeApprox": sharpe,
        "sortinoApprox": sortino,
        "avgNetReturn95CI": [ci_lo, ci_hi],
        "avgMFE": statistics.fmean(mfes) if mfes else None,
        "avgMAE": statistics.fmean(maes) if maes else None,
        "slippageBpsPerSide": slippage_bps,
        "commissionBpsPerSide": commission_bps,
        "cooldownDays": cooldown_days,
        "transitionOnly": transition_only,
        "scoreCalibration": calibration,
        "details": signals,
    }


# ---------------------------------------------------------------------------
# Output
# ---------------------------------------------------------------------------


def format_report(r: dict[str, Any]) -> str:
    t = r["technical"]
    e = r["entry"]
    f = r["fundamental"]
    risk = r["risk"]
    q = r["dataQuality"]
    c = e["technicalComponents"]

    lines = [
        f"===== AEGIS MAGIC SCREENER v2.3 — {r['ticker']} ({r['name']}) =====",
        f"As of {t['asOf']} | ${fmt(t['price'])} | {q['dailyBars']} daily bars | synthetic market fields: NO",
        "",
        "TECHNICAL ENTRY",
        f"  EOD setup            : {e['eodSetup']}",
        f"  Live entry status    : {e['entryTrigger']}  (daily data cannot confirm intraday execution)",
        f"  Technical score      : {fmt(e['technicalEntryScore'], 1)}/100 adjusted | raw={fmt(e['technicalRawScore'],1)} | coverage={fmt(e['technicalCoverage'],1,'%')}",
        f"  Trend                : {fmt(c['trend'], 1)}",
        f"  Breakout              : {fmt(c['breakout'], 1)}",
        f"  Momentum              : {fmt(c['momentum'], 1)}",
        f"  Relative strength     : {fmt(c['relativeStrength'], 1)}",
        f"  Volume                : {fmt(c['volume'], 1)}",
        f"  VWAP / AVWAP          : {fmt(c['vwap'], 1)}",
        f"  Volatility setup      : {fmt(c['volatilitySetup'], 1)}",
        f"  RSI/MACD confirmation : {fmt(c['oscillator'], 1)}",
        f"  Reasons               : {', '.join(e['entryReasons'])}",
        "",
        "REAL MARKET FEATURES",
        f"  SMA20 / 50 / 200      : {fmt(t['sma20'])} / {fmt(t['sma50'])} / {fmt(t['sma200'])}",
        f"  MA slopes             : 20d={fmt(t['sma20Slope10d'],2,'%')}  50d={fmt(t['sma50Slope10d'],2,'%')}  200d={fmt(t['sma200Slope20d'],2,'%')}",
        f"  Weekly trend          : {t['weeklyTrend']}  (W10={fmt(t['weeklySma10'])}, W30={fmt(t['weeklySma30'])})",
        f"  Momentum 20/60/120/252: {fmt(t['perf20'],2,'%')} / {fmt(t['perf60'],2,'%')} / {fmt(t['perf120'],2,'%')} / {fmt(t['perf252'],2,'%')}",
        f"  20d/55d/52w breakout : {fmt(t['breakout20'],0)} / {fmt(t['breakout55'],0)} / {fmt(t['breakout252'],0)}",
        f"  52w high proximity    : {fmt(t['proximity52wPct'],2,'%')}",
        f"  RSI14                 : {fmt(t['rsi14'])}",
        f"  MACD hist / accel     : {fmt(t['macdHist'],4)} / {fmt(t['macdHistAccel'],4)}",
        f"  ATR14 / ATR%          : {fmt(t['atr14'])} / {fmt(t['atrPct'],2,'%')}",
        f"  Realized vol 20d ann. : {fmt(t['realizedVol20AnnPct'],2,'%')}",
        f"  Downside vol 60d ann. : {fmt(t['downsideDeviation60AnnPct'],2,'%')}",
        f"  95% gap risk / Max DD : {fmt(t['gapRisk95_60Pct'],2,'%')} / {fmt(t['maxDrawdown252Pct'],2,'%')}",
        f"  RVOL20 / volume z     : {fmt(t['relVol20'],2,'x')} / {fmt(t['volumeZ20'],2)}",
        f"  Avg dollar vol 20d    : ${fmt(t['avgDollarVol20'],0)}",
        f"  VWAP20                : {fmt(t['vwap20'])}  above={fmt(t['aboveVwap20'],0)}",
        f"  Anchored VWAP         : {fmt(t['anchoredVwap'])}  above={fmt(t['aboveAnchoredVwap'],0)}",
        f"  Bollinger width pctile: {fmt(t['bollingerBandwidthPercentile'],1)}",
        f"  Relative strength     : 20={fmt(t['rs20'],2,'%')} 60={fmt(t['rs60'],2,'%')} 120={fmt(t['rs120'],2,'%')} 252={fmt(t['rs252'],2,'%')}",
        "",
        "FUNDAMENTAL LAYER",
        f"  Fundamental score     : {fmt(f['fundamentalScore'],1)}/100 adjusted (raw={fmt(f['fundamentalRawScore'],1)})",
        f"  Fundamental coverage  : {fmt(f['fundamentalCoverage'],1,'%')} ({f['fundamentalConfidence']})",
        f"  Fundamental warnings  : {('; '.join(f['fundamentalWarnings'])) if f['fundamentalWarnings'] else 'none'}",
        "  (Add Zacks revisions / Estimize divergence / Piotroski / accrual quality as fields when available.)",
        "",
        "RISK LAYER",
        f"  Risk score            : {fmt(risk['riskScore'],1)}/100 (higher = more risk; coverage={fmt(risk['riskCoverage'],1,'%')})",
        f"  Components            : " + ", ".join(f"{k}={fmt(v,1)}" for k, v in risk["riskComponents"].items()),
        "",
        "PORTFOLIO HANDOFF",
        "  This screener selects/times candidates. Position weights should be set by Mean-CVaR/cuOpt, not by this score.",
        "",
        "Educational/research model — not investment advice.",
    ]
    return "\n".join(lines)


def _format_calibration(bt: dict[str, Any], horizon: int) -> list[str]:
    lines = ["SCORE CALIBRATION (signal-level observations; overlapping windows allowed):"]
    for row in bt.get("scoreCalibration", []):
        lines.append(
            f"  {row['bucket']:>6}  n={row['observations']:>4}  "
            f"target={row['targetHitRate']*100:5.1f}%  stop={row['stopHitRate']*100:5.1f}%  "
            f"avg{horizon}d={row['avgFixedHorizonReturn']*100:+6.2f}%"
        )
    return lines


def format_backtest(bt: dict[str, Any], horizon: int, profit: float, stop: float, min_score: float) -> str:
    if bt["signals"] == 0:
        lines = [f"BACKTEST: no qualifying setups at adjusted score >= {min_score:.1f}.", ""]
        lines.extend(_format_calibration(bt, horizon))
        return "\n".join(lines)
    ci = bt.get("avgNetReturn95CI") or [None, None]
    pf = bt.get("profitFactor")
    pf_text = "inf" if pf is not None and math.isinf(pf) else fmt(pf, 2)
    dd = bt.get("maxDrawdown")
    lines = [
        "===== WALK-FORWARD ENTRY BACKTEST v2.3 =====",
        f"Mode                  : {bt['mode']} (trade = non-overlapping)",
        f"Trades/signals        : {bt['signals']}",
        f"Rule                  : EOD setup score >= {min_score:.1f}; execute NEXT OPEN; horizon {horizon}d; target +{profit*100:.1f}%; stop -{stop*100:.1f}%",
        f"Costs                 : slippage {fmt(bt.get('slippageBpsPerSide'),1)} bps/side + commission {fmt(bt.get('commissionBpsPerSide'),1)} bps/side",
        f"Target hit rate       : {fmt(bt['targetHitRate']*100,2,'%')}",
        f"Stop hit rate         : {fmt(bt['stopHitRate']*100,2,'%')}",
        f"Neither               : {fmt(bt['neitherRate']*100,2,'%')}",
        f"Win rate (net)        : {fmt(bt['winRate']*100,2,'%')}",
        f"Expectancy / trade    : {fmt(bt['expectancy']*100,2,'%')}",
        f"Median trade return   : {fmt(bt['medianHorizonReturn']*100,2,'%')}",
        f"Profit factor         : {pf_text}",
        f"Avg holding period    : {fmt(bt['avgHoldingDays'],2)} sessions",
        f"Max strategy drawdown : {fmt(dd*100 if dd is not None else None,2,'%')}",
        f"Sharpe approx         : {fmt(bt['sharpeApprox'],2)}",
        f"Sortino approx        : {fmt(bt['sortinoApprox'],2)}",
        f"Mean return 95% CI    : {fmt(ci[0]*100 if ci[0] is not None else None,2,'%')} to {fmt(ci[1]*100 if ci[1] is not None else None,2,'%')}",
        f"Avg fixed {horizon}d return: {fmt(bt['avgFixedHorizonReturn']*100,2,'%')}",
        f"Unconditional baseline: {fmt(bt['avgUnconditionalHorizonReturn']*100 if bt['avgUnconditionalHorizonReturn'] is not None else None,2,'%')}",
        f"Selection lift        : {fmt(bt['selectionLiftVsUnconditional']*100 if bt['selectionLiftVsUnconditional'] is not None else None,2,'%')}",
        f"Benchmark {horizon}d avg   : {fmt(bt['avgBenchmarkHorizonReturn']*100 if bt['avgBenchmarkHorizonReturn'] is not None else None,2,'%')}",
        f"Excess vs benchmark   : {fmt(bt['avgExcessHorizonReturnVsBenchmark']*100 if bt['avgExcessHorizonReturnVsBenchmark'] is not None else None,2,'%')}",
        f"Avg max favorable     : {fmt(bt['avgMFE']*100 if bt['avgMFE'] is not None else None,2,'%')}",
        f"Avg max adverse       : {fmt(bt['avgMAE']*100 if bt['avgMAE'] is not None else None,2,'%')}",
        "",
    ]
    lines.extend(_format_calibration(bt, horizon))
    lines.append("NOTE: still requires point-in-time fundamentals, delisting/survivorship controls and true out-of-sample testing before capital deployment.")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


FUND_FLAGS = {
    "pe": "pe", "pb": "pb", "ev_to_ebitda": "evToEbitda", "roic": "roic",
    "roe": "roe", "roa": "roa", "gross_margin": "grossMargin",
    "operating_margin": "operatingMargin", "earn_yield": "earnYield",
    "fcf_yield": "fcfYield", "debt_to_equity": "debtToEquity",
    "piotroski": "piotroski", "accrual_quality": "accrualQuality",
    "revenue_growth": "revenueGrowth", "eps_growth": "epsGrowth",
    "earnings_revision": "earningsRevision", "estimize_divergence": "estimizeDivergence",
    "shareholder_yield": "shareholderYield", "beta": "beta",
}


def build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="AEGIS Magic Screener v2.3 — real OHLCV EOD setup + validated next-open backtest engine",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    p.add_argument("ticker", help="ticker symbol")
    src = p.add_mutually_exclusive_group(required=False)
    src.add_argument("--history-csv", help="daily OHLCV CSV with date,open,high,low,close,volume")
    src.add_argument("--history-json", help="daily OHLCV JSON/list or FMP historical payload")
    src.add_argument("--live", action="store_true", help="fetch history + fundamentals from FMP (FMP_API_KEY)")
    p.add_argument("--years", type=int, default=4, help="history years in --live mode")
    p.add_argument("--benchmark-csv", help="benchmark OHLCV CSV, e.g. SPY, for relative strength")
    p.add_argument("--benchmark-live", default="SPY", help="benchmark ticker fetched automatically in --live mode")
    p.add_argument("--anchor-date", help="YYYY-MM-DD catalyst date for anchored VWAP")
    p.add_argument("--snapshot-json", help="optional JSON object with fundamental fields")
    p.add_argument("--name")
    p.add_argument("--sector")
    for flag in FUND_FLAGS:
        p.add_argument(f"--{flag.replace('_','-')}", type=float)

    p.add_argument("--json-output", help="write full analysis JSON")
    p.add_argument("--features-json", help="write flat ML-ready features JSON")
    p.add_argument("--features-csv", help="write flat ML-ready one-row CSV")

    p.add_argument("--backtest", action="store_true", help="walk-forward test the entry heuristic")
    p.add_argument("--bt-horizon", type=int, default=20)
    p.add_argument("--bt-profit", type=float, default=0.05, help="profit target as decimal")
    p.add_argument("--bt-stop", type=float, default=0.03, help="stop loss as positive decimal")
    p.add_argument("--bt-min-score", type=float, default=75.0)
    p.add_argument("--bt-mode", choices=["trade", "signal"], default="trade", help="trade blocks overlapping positions; signal keeps every observation")
    p.add_argument("--bt-slippage-bps", type=float, default=5.0, help="assumed adverse slippage per side, basis points")
    p.add_argument("--bt-commission-bps", type=float, default=1.0, help="commission/fees per side, basis points")
    p.add_argument("--bt-cooldown", type=int, default=0, help="flat sessions required after an exit in trade mode")
    p.add_argument("--bt-transition-only", action="store_true", help="only accept a setup when setup state changes")
    p.add_argument("--bt-details-csv", help="export each historical qualifying trade/signal")
    return p


def main(argv: Optional[list[str]] = None) -> None:
    args = build_arg_parser().parse_args(argv)
    ticker = args.ticker.upper()

    snapshot: dict[str, Any] = {"ticker": ticker}
    benchmark: Optional[list[Bar]] = None

    if args.live:
        try:
            bars = fetch_fmp_history(ticker, years=args.years)
        except RuntimeError as exc:
            raise SystemExit(str(exc))
        snapshot.update(fetch_fmp_snapshot(ticker))
        try:
            benchmark = fetch_fmp_history(args.benchmark_live.upper(), years=args.years)
        except Exception as exc:
            print(f"WARNING: benchmark fetch failed ({exc}); relative-strength features unavailable", file=sys.stderr)
    elif args.history_csv:
        bars = load_history_csv(args.history_csv)
    elif args.history_json:
        bars = load_history_json(args.history_json)
    else:
        raise SystemExit(
            "Real OHLCV history is required in v2. Use --history-csv, --history-json, or --live.\n"
            "Synthetic ATR/RVOL fallbacks were intentionally removed."
        )

    if args.benchmark_csv:
        benchmark = load_history_csv(args.benchmark_csv)

    if args.snapshot_json:
        with open(args.snapshot_json, encoding="utf-8") as f:
            snap = json.load(f)
        if not isinstance(snap, dict):
            raise SystemExit("--snapshot-json must contain a JSON object")
        snapshot.update(snap)

    if args.name:
        snapshot["name"] = args.name
    if args.sector:
        snapshot["sector"] = args.sector
    for flag, key in FUND_FLAGS.items():
        v = getattr(args, flag)
        if v is not None:
            snapshot[key] = v

    # Useful fallback: earnings yield from a real P/E if user supplied P/E.
    pe_for_yield = as_float(snapshot.get("pe"))
    if snapshot.get("earnYield") is None and pe_for_yield is not None and pe_for_yield > 0:
        snapshot["earnYield"] = 100.0 / pe_for_yield

    anchor: Optional[dt.date] = None
    if args.anchor_date:
        try:
            anchor = parse_date(args.anchor_date)
        except ValueError:
            raise SystemExit(f"--anchor-date must be YYYY-MM-DD, got {args.anchor_date!r}")
    result = analyze(ticker, bars, snapshot=snapshot, benchmark=benchmark, anchor_date=anchor)
    print(format_report(result))

    if args.json_output:
        with open(args.json_output, "w", encoding="utf-8") as f:
            json.dump(result, f, indent=2, default=str)
        print(f"\nFull analysis JSON -> {args.json_output}")

    flat = flatten_ml_features(result)
    if args.features_json:
        with open(args.features_json, "w", encoding="utf-8") as f:
            json.dump(flat, f, indent=2, default=str)
        print(f"ML features JSON -> {args.features_json}")
    if args.features_csv:
        with open(args.features_csv, "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=list(flat.keys()))
            w.writeheader()
            w.writerow(flat)
        print(f"ML features CSV -> {args.features_csv}")

    if args.backtest:
        bt = backtest_entry_model(
            bars,
            benchmark=benchmark,
            horizon=args.bt_horizon,
            profit_target=args.bt_profit,
            stop_loss=args.bt_stop,
            min_score=args.bt_min_score,
            mode=args.bt_mode,
            slippage_bps=args.bt_slippage_bps,
            commission_bps=args.bt_commission_bps,
            cooldown_days=args.bt_cooldown,
            transition_only=args.bt_transition_only,
        )
        print("\n" + format_backtest(bt, args.bt_horizon, args.bt_profit, args.bt_stop, args.bt_min_score))
        if args.bt_details_csv and bt.get("details"):
            with open(args.bt_details_csv, "w", newline="", encoding="utf-8") as f:
                w = csv.DictWriter(f, fieldnames=list(bt["details"][0].keys()))
                w.writeheader()
                w.writerows(bt["details"])
            print(f"Backtest details -> {args.bt_details_csv}")


if __name__ == "__main__":
    main()
