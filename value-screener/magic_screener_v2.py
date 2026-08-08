#!/usr/bin/env python3
"""AEGIS Magic Screener v2 — evidence-oriented fundamental + technical entry engine.

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

The script has no third-party Python dependencies.

Examples
--------
# Local historical CSV (date,open,high,low,close,volume):
python magic_screener_v2.py NVDA --history-csv nvda.csv

# Add benchmark-relative strength:
python magic_screener_v2.py NVDA --history-csv nvda.csv --benchmark-csv spy.csv

# Optional anchored VWAP from an earnings/catalyst date:
python magic_screener_v2.py NVDA --history-csv nvda.csv --anchor-date 2026-05-20

# Live mode using Financial Modeling Prep (current /stable endpoints, with
# automatic fallback to the legacy v3 routes for older API keys):
export FMP_API_KEY='...'
python magic_screener_v2.py NVDA --live

# Walk-forward validation of the entry model:
python magic_screener_v2.py NVDA --history-csv nvda.csv --backtest \
    --bt-horizon 20 --bt-profit 0.05 --bt-stop 0.03 --bt-min-score 75

# Export one ML-ready feature row:
python magic_screener_v2.py NVDA --history-csv nvda.csv --features-json nvda_features.json

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
    adj = as_float(row.get("adjClose") or row.get("adj_close") or row.get("adjclose"))
    v = as_float(row.get("volume"))
    if c is None:
        c, adj = adj, None
    if None in (o, h, l, c, v):
        return None
    # Keep OHLC internally consistent: when an adjusted close is provided,
    # apply the same adjustment factor to open/high/low so split/dividend gaps
    # do not corrupt long-lookback features. Volume cannot be back-adjusted
    # reliably from this payload and is left as reported.
    if adj is not None and adj > 0 and c > 0 and not math.isclose(adj, c, rel_tol=1e-9):
        factor = adj / c
        o, h, l, c = o * factor, h * factor, l * factor, adj
    if h < l or min(o, h, l, c) <= 0 or v < 0:
        return None
    return Bar(d, float(o), float(h), float(l), float(c), float(v))


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
    req = urllib.request.Request(url, headers={"User-Agent": "AEGIS-Magic-Screener/2.0"})
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
    roe = as_float(km0.get("roeTTM") or r0.get("returnOnEquityTTM"))
    roa = as_float(km0.get("returnOnTangibleAssetsTTM") or r0.get("returnOnAssetsTTM"))
    earnings_yield = as_float(km0.get("earningsYieldTTM"))
    fcf_yield = as_float(km0.get("freeCashFlowYieldTTM"))
    debt_to_equity = as_float(r0.get("debtEquityRatioTTM") or km0.get("debtToEquityTTM"))
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
        "beta": as_float(q0.get("beta")) or as_float(km0.get("betaTTM")),
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
    """Transparent quality/value composite. Uses only fields that really exist.

    Optional fields can come from Zacks/Estimize/FMP later. Missing fields are
    omitted and weights are renormalized rather than replaced by fake values.
    """
    components: list[tuple[str, Optional[float], float]] = [
        # Quality / profitability
        ("roic", _score_linear(as_float(s.get("roic")), 8, 30, True), 0.16),
        ("roe", _score_linear(as_float(s.get("roe")), 8, 30, True), 0.06),
        ("grossMargin", _score_linear(as_float(s.get("grossMargin")), 15, 60, True), 0.05),
        ("operatingMargin", _score_linear(as_float(s.get("operatingMargin")), 5, 30, True), 0.06),
        # Value
        ("earnYield", _score_linear(as_float(s.get("earnYield")), 1, 8, True), 0.12),
        ("fcfYield", _score_linear(as_float(s.get("fcfYield")), 0, 8, True), 0.12),
        ("pe", _score_linear(as_float(s.get("pe")), 45, 12, False), 0.07),
        ("evToEbitda", _score_linear(as_float(s.get("evToEbitda")), 25, 8, False), 0.06),
        # Financial health / earnings quality
        ("debtToEquity", _score_linear(as_float(s.get("debtToEquity")), 2.0, 0.3, False), 0.05),
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
        return {"fundamentalScore": None, "fundamentalCoverage": 0.0, "fundamentalComponents": {}}
    weight_sum = sum(w for _, _, w in present)
    score = sum(float(sc) * w for _, sc, w in present) / weight_sum
    total_weight = sum(w for _, _, w in components)
    coverage = weight_sum / total_weight * 100.0
    return {
        "fundamentalScore": score,
        "fundamentalCoverage": coverage,
        "fundamentalComponents": {name: sc for name, sc, _ in present},
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
    entry_score = sum(float(v) * w for _, v, w in present) / wsum if wsum else 50.0

    trend = comps["trend"] if comps["trend"] is not None else 50.0
    breakout_confirm = bool(f.get("breakout20") or f.get("breakout55"))
    rvol = f.get("relVol20") or 0.0
    macd_ok = (f.get("macdHist") or 0) > 0 and (f.get("macdHistAccel") or 0) >= 0
    weekly_ok = f.get("weeklyTrend") == "bull"
    liquid = (f.get("avgDollarVol20") or 0) >= 5_000_000

    trigger = "WAIT"
    reasons: list[str] = []
    if entry_score >= 78 and trend >= 70 and breakout_confirm and rvol >= 1.3 and weekly_ok and liquid:
        trigger = "STRONG_LONG"
        reasons.extend(["trend confirmed", "20/55d breakout", "volume confirms", "weekly trend bull"])
    elif entry_score >= 70 and trend >= 68 and breakout_confirm and weekly_ok and liquid:
        trigger = "LONG"
        reasons.extend(["trend confirmed", "breakout present", "weekly trend bull"])
    else:
        # Pullback entry: strong established trend, RSI cooled, price reclaims EMA20/VWAP.
        rsi = f.get("rsi14")
        reclaim = bool(f.get("price") and f.get("ema20") and f["price"] > f["ema20"] and f.get("aboveVwap20"))
        if entry_score >= 66 and trend >= 75 and rsi is not None and 42 <= rsi <= 60 and reclaim and macd_ok and liquid:
            trigger = "LONG_PULLBACK"
            reasons.extend(["strong primary trend", "RSI reset", "EMA20/VWAP reclaim", "MACD improving"])

    if not reasons:
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

    return {
        "technicalEntryScore": entry_score,
        "entryTrigger": trigger,
        "entryReasons": reasons,
        "technicalComponents": comps,
        "technicalCoverage": sum(weights[k] for k, v in comps.items() if v is not None) / sum(weights.values()) * 100.0,
    }


# ---------------------------------------------------------------------------
# Risk layer — separate from expected return / entry timing
# ---------------------------------------------------------------------------


def compute_risk_score(f: dict[str, Any], fundamentals: dict[str, Any]) -> dict[str, Any]:
    dims: dict[str, float] = {}

    atrp = f.get("atrPct")
    rv = f.get("realizedVol20AnnPct")
    dims["volatility"] = clamp(((atrp or 2.5) / 7.0) * 100.0 * 0.55 + ((rv or 35.0) / 80.0) * 100.0 * 0.45, 0, 100)

    adv = f.get("avgDollarVol20") or 0.0
    if adv >= 100_000_000:
        dims["liquidity"] = 10
    elif adv >= 20_000_000:
        dims["liquidity"] = 25
    elif adv >= 5_000_000:
        dims["liquidity"] = 50
    elif adv >= 1_000_000:
        dims["liquidity"] = 75
    else:
        dims["liquidity"] = 95

    trend = _trend_component(f)
    dims["trendBreakRisk"] = 100.0 - trend

    prox = f.get("proximity52wPct")
    bwp = f.get("bollingerBandwidthPercentile")
    extension = 35.0
    if prox is not None and prox >= 99 and (f.get("rsi14") or 50) > 72:
        extension += 25
    if bwp is not None and bwp >= 90:
        extension += 20
    dims["extension"] = clamp(extension, 0, 100)

    fscore = fundamentals.get("fundamentalScore")
    dims["fundamental"] = 50.0 if fscore is None else 100.0 - float(fscore)

    weights = {"volatility": 0.30, "liquidity": 0.20, "trendBreakRisk": 0.20, "extension": 0.15, "fundamental": 0.15}
    score = sum(dims[k] * w for k, w in weights.items())
    return {"riskScore": score, "riskComponents": dims}


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
    risk = compute_risk_score(tech, fundamental)

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
        "fundamentalCoverage": result["fundamental"].get("fundamentalCoverage"),
        "technicalEntryScore": result["entry"].get("technicalEntryScore"),
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


def _path_outcome(
    future: list[Bar], entry: float, profit_target: float, stop_loss: float
) -> dict[str, Any]:
    target = entry * (1.0 + profit_target)
    stop = entry * (1.0 - stop_loss)
    outcome = "NEITHER"
    hit_day = None
    mfe = -math.inf
    mae = math.inf
    for i, b in enumerate(future, 1):
        mfe = max(mfe, b.high / entry - 1.0)
        mae = min(mae, b.low / entry - 1.0)
        # Conservative ordering when both are touched in the same daily bar:
        # count stop first because intraday ordering is unknowable from daily OHLC.
        if b.low <= stop:
            outcome, hit_day = "STOP", i
            break
        if b.high >= target:
            outcome, hit_day = "TARGET", i
            break
    end_return = future[-1].close / entry - 1.0 if future else 0.0
    return {
        "outcome": outcome,
        "hitDay": hit_day,
        "endReturn": end_return,
        "mfe": mfe if mfe != -math.inf else None,
        "mae": mae if mae != math.inf else None,
    }


def backtest_entry_model(
    bars: list[Bar],
    benchmark: Optional[list[Bar]] = None,
    horizon: int = 20,
    profit_target: float = 0.05,
    stop_loss: float = 0.03,
    min_score: float = 75.0,
    warmup: int = 260,
) -> dict[str, Any]:
    if len(bars) < warmup + horizon + 5:
        raise ValueError(f"Backtest needs at least {warmup + horizon + 5} bars")

    bmap = {b.date: b for b in benchmark} if benchmark else {}
    signals: list[dict[str, Any]] = []

    # Use only information available up to each historical date.
    for i in range(warmup, len(bars) - horizon):
        hist = bars[:i + 1]
        bhist = [bmap[x.date] for x in hist if x.date in bmap] if benchmark else None
        try:
            f = compute_technical_features(hist, benchmark=bhist)
            e = compute_entry_model(f)
        except Exception:
            continue
        if e["technicalEntryScore"] < min_score or e["entryTrigger"] == "WAIT":
            continue
        path = _path_outcome(bars[i + 1:i + 1 + horizon], bars[i].close, profit_target, stop_loss)
        signals.append({
            "date": bars[i].date.isoformat(),
            "score": e["technicalEntryScore"],
            "trigger": e["entryTrigger"],
            **path,
        })

    n = len(signals)
    if n == 0:
        return {
            "signals": 0,
            "targetHitRate": None,
            "stopHitRate": None,
            "avgHorizonReturn": None,
            "medianHorizonReturn": None,
            "avgMFE": None,
            "avgMAE": None,
            "details": [],
        }
    returns = [x["endReturn"] for x in signals]
    mfes = [x["mfe"] for x in signals if x["mfe"] is not None]
    maes = [x["mae"] for x in signals if x["mae"] is not None]
    return {
        "signals": n,
        "targetHitRate": sum(x["outcome"] == "TARGET" for x in signals) / n,
        "stopHitRate": sum(x["outcome"] == "STOP" for x in signals) / n,
        "neitherRate": sum(x["outcome"] == "NEITHER" for x in signals) / n,
        "avgHorizonReturn": statistics.fmean(returns),
        "medianHorizonReturn": statistics.median(returns),
        "avgMFE": statistics.fmean(mfes) if mfes else None,
        "avgMAE": statistics.fmean(maes) if maes else None,
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
        f"===== AEGIS MAGIC SCREENER v2 — {r['ticker']} ({r['name']}) =====",
        f"As of {t['asOf']} | ${fmt(t['price'])} | {q['dailyBars']} daily bars | synthetic market fields: NO",
        "",
        "TECHNICAL ENTRY",
        f"  Entry trigger        : {e['entryTrigger']}",
        f"  Technical entry score: {fmt(e['technicalEntryScore'], 1)}/100  (heuristic, NOT probability)",
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
        f"  RVOL20 / volume z     : {fmt(t['relVol20'],2,'x')} / {fmt(t['volumeZ20'],2)}",
        f"  Avg dollar vol 20d    : ${fmt(t['avgDollarVol20'],0)}",
        f"  VWAP20                : {fmt(t['vwap20'])}  above={fmt(t['aboveVwap20'],0)}",
        f"  Anchored VWAP         : {fmt(t['anchoredVwap'])}  above={fmt(t['aboveAnchoredVwap'],0)}",
        f"  Bollinger width pctile: {fmt(t['bollingerBandwidthPercentile'],1)}",
        f"  Relative strength     : 20={fmt(t['rs20'],2,'%')} 60={fmt(t['rs60'],2,'%')} 120={fmt(t['rs120'],2,'%')} 252={fmt(t['rs252'],2,'%')}",
        "",
        "FUNDAMENTAL LAYER",
        f"  Fundamental score     : {fmt(f['fundamentalScore'],1)}/100",
        f"  Fundamental coverage  : {fmt(f['fundamentalCoverage'],1,'%')}",
        "  (Add Zacks revisions / Estimize divergence / Piotroski / accrual quality as fields when available.)",
        "",
        "RISK LAYER",
        f"  Risk score            : {fmt(risk['riskScore'],1)}/100 (higher = more risk)",
        f"  Components            : " + ", ".join(f"{k}={fmt(v,1)}" for k, v in risk["riskComponents"].items()),
        "",
        "PORTFOLIO HANDOFF",
        "  This screener selects/times candidates. Position weights should be set by Mean-CVaR/cuOpt, not by this score.",
        "",
        "Educational/research model — not investment advice.",
    ]
    return "\n".join(lines)


def format_backtest(bt: dict[str, Any], horizon: int, profit: float, stop: float, min_score: float) -> str:
    if bt["signals"] == 0:
        return f"BACKTEST: no qualifying signals at score >= {min_score:.1f}."
    return "\n".join([
        "===== WALK-FORWARD ENTRY BACKTEST =====",
        f"Signals               : {bt['signals']}",
        f"Rule                  : score >= {min_score:.1f}, horizon {horizon}d, target +{profit*100:.1f}%, stop -{stop*100:.1f}%",
        f"Target hit rate       : {fmt(bt['targetHitRate']*100,2,'%')}",
        f"Stop hit rate         : {fmt(bt['stopHitRate']*100,2,'%')}",
        f"Neither               : {fmt(bt['neitherRate']*100,2,'%')}",
        f"Avg horizon return    : {fmt(bt['avgHorizonReturn']*100,2,'%')}",
        f"Median horizon return : {fmt(bt['medianHorizonReturn']*100,2,'%')}",
        f"Avg max favorable     : {fmt(bt['avgMFE']*100,2,'%')}",
        f"Avg max adverse       : {fmt(bt['avgMAE']*100,2,'%')}",
        "NOTE: This tests the heuristic only; robust research should also include costs, delistings, survivorship controls, and out-of-sample periods.",
    ])


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
        description="AEGIS Magic Screener v2 — real OHLCV technical-entry engine",
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
    p.add_argument("--bt-details-csv", help="export each historical qualifying signal")
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
    if snapshot.get("earnYield") is None and as_float(snapshot.get("pe")):
        pe = float(snapshot["pe"])
        if pe > 0:
            snapshot["earnYield"] = 100.0 / pe

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
