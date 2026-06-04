#!/usr/bin/env python3
"""
beat_and_drop_screener.py
=========================
Screen the top ~1000 US stocks for the "beat-and-drop" pattern: companies that
fell after quarterly results despite *good* fundamentals — i.e. the drop is
driven by the market having priced in even higher expectations, not by a
deterioration in the business.

Logic
-----
A candidate is flagged when, for its most recent reported quarter, it:
  1. BEAT (or met) consensus on EPS and revenue            -> results were good
  2. Grew revenue and EPS year-over-year                   -> business is healthy
  3. Did NOT see margins deteriorate vs the year-ago qtr   -> no quality erosion
  4. Still fell hard on the earnings reaction              -> punished anyway
  5. Fell MORE than the market did that day (vs SPY)       -> not a macro selloff

It then ranks survivors by an "expectation gap" score = how good the print was
minus how badly the stock reacted. A big positive score = strongly punished
despite a strong beat = the cleanest examples of "expectations were too high".

What it can NOT see
-------------------
Forward GUIDANCE. A beat with soft guidance is the single most common reason
for a justified post-beat drop, and guidance is a fundamental factor that the
reported quarter doesn't capture. Treat every hit as a candidate to eyeball the
guidance/management commentary on, not a final verdict.

Requirements: Python 3.9+, `requests`, `pandas`
    pip install requests pandas
Set your key:  export FMP_API_KEY=...    (or pass --api-key)
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
import time
from dataclasses import dataclass, asdict
from datetime import datetime, timedelta

import pandas as pd
import requests

# --------------------------------------------------------------------------- #
# Tunable thresholds — adjust to taste
# --------------------------------------------------------------------------- #
DEFAULTS = dict(
    universe_size=1000,          # top N US stocks by market cap
    min_market_cap=2_000_000_000,  # ignore micro/small caps (noise)
    lookback_days=120,           # only consider earnings in this recent window
    price_drop_threshold=-0.04,  # stock fell at least 4% on the reaction
    rel_underperf_threshold=-0.03,  # underperformed SPY by at least 3% that day
    eps_surprise_min=0.0,        # actual EPS >= estimate (beat or in-line)
    rev_surprise_min=0.0,        # actual revenue >= estimate
    yoy_rev_growth_min=0.0,      # revenue grew YoY
    yoy_eps_growth_min=0.0,      # EPS grew YoY
    margin_tolerance=-0.01,      # operating margin allowed to slip at most 1pp
    request_pause=0.12,          # seconds between API calls (rate limiting)
)

FMP_BASE = "https://financialmodelingprep.com/api/v3"

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s %(levelname)s  %(message)s"
)
log = logging.getLogger("screener")


# --------------------------------------------------------------------------- #
# HTTP layer (one session, light retry, gentle throttle)
# --------------------------------------------------------------------------- #
class FMP:
    def __init__(self, api_key: str, pause: float):
        self.key = api_key
        self.pause = pause
        self.s = requests.Session()

    def get(self, path: str, **params):
        params["apikey"] = self.key
        url = f"{FMP_BASE}/{path}"
        for attempt in range(3):
            try:
                r = self.s.get(url, params=params, timeout=30)
                if r.status_code == 429:  # rate limited
                    time.sleep(1.5 * (attempt + 1))
                    continue
                r.raise_for_status()
                time.sleep(self.pause)
                return r.json()
            except requests.RequestException as e:
                log.debug("retry %s on %s (%s)", attempt, path, e)
                time.sleep(0.8 * (attempt + 1))
        return None


# --------------------------------------------------------------------------- #
# Data fetchers
# --------------------------------------------------------------------------- #
def get_universe(api: FMP, n: int, min_cap: float) -> list[str]:
    """Top-N actively traded US common stocks by market cap."""
    data = api.get(
        "stock-screener",
        marketCapMoreThan=int(min_cap),
        country="US",
        isActivelyTrading="true",
        exchange="NASDAQ,NYSE,AMEX",
        isEtf="false",
        isFund="false",
        limit=n,
    )
    if not data:
        return []
    rows = sorted(data, key=lambda d: d.get("marketCap", 0), reverse=True)[:n]
    return [r["symbol"] for r in rows]


def latest_earnings(api: FMP, symbol: str, lookback_days: int):
    """Most recent reported quarter with actual + estimate for EPS and revenue."""
    cal = api.get(f"historical/earning_calendar/{symbol}", limit=8)
    if not cal:
        return None
    cutoff = datetime.utcnow() - timedelta(days=lookback_days)
    for row in cal:  # newest first
        try:
            edate = datetime.strptime(row["date"], "%Y-%m-%d")
        except (KeyError, ValueError, TypeError):
            continue
        if edate < cutoff:
            return None  # nothing recent enough
        if row.get("eps") is None or row.get("epsEstimated") is None:
            continue
        return row
    return None


def price_reaction(api: FMP, symbol: str, edate: str, spy_prices: pd.Series):
    """
    Close[t-1] -> Close[t+1] return around the earnings date. This 2-day window
    absorbs both before-open and after-close reporting (we usually don't know
    which). Also returns the same-window SPY move so we can compute the
    market-relative reaction.
    """
    e = datetime.strptime(edate, "%Y-%m-%d")
    frm = (e - timedelta(days=8)).strftime("%Y-%m-%d")
    to = (e + timedelta(days=8)).strftime("%Y-%m-%d")
    hist = api.get(f"historical-price-full/{symbol}", **{"from": frm, "to": to})
    if not hist or "historical" not in hist:
        return None
    df = pd.DataFrame(hist["historical"]).sort_values("date").reset_index(drop=True)
    if df.empty:
        return None
    df["d"] = pd.to_datetime(df["date"])
    # index of the first trading day on/after the earnings date
    after = df.index[df["d"] >= e]
    if len(after) == 0:
        return None
    idx = int(after[0])
    if idx == 0 or idx + 1 >= len(df):
        return None
    c_before = df.loc[idx - 1, "close"]
    c_after = df.loc[idx + 1, "close"]
    stock_ret = c_after / c_before - 1

    # market-relative: SPY over the same calendar window
    d0, d1 = df.loc[idx - 1, "d"], df.loc[idx + 1, "d"]
    try:
        spy_ret = spy_prices.asof(d1) / spy_prices.asof(d0) - 1
    except (KeyError, ZeroDivisionError, TypeError):
        spy_ret = 0.0
    return stock_ret, stock_ret - spy_ret


def get_spy_series(api: FMP, lookback_days: int) -> pd.Series:
    frm = (datetime.utcnow() - timedelta(days=lookback_days + 20)).strftime("%Y-%m-%d")
    hist = api.get("historical-price-full/SPY", **{"from": frm})
    if not hist or "historical" not in hist:
        return pd.Series(dtype=float)
    df = pd.DataFrame(hist["historical"])
    df["d"] = pd.to_datetime(df["date"])
    return df.set_index("d")["close"].sort_index()


def quarterly_quality(api: FMP, symbol: str):
    """
    Pull last 5 quarterly income statements to compute YoY growth and a margin
    trend (this quarter vs the same quarter a year ago = index 0 vs index 4).
    """
    stmts = api.get(f"income-statement/{symbol}", period="quarter", limit=5)
    if not stmts or len(stmts) < 5:
        return None
    cur, yoy = stmts[0], stmts[4]

    def margin(s):
        rev = s.get("revenue") or 0
        return (s.get("operatingIncome", 0) / rev) if rev else None

    rev_now, rev_yoy = cur.get("revenue"), yoy.get("revenue")
    eps_now, eps_yoy = cur.get("eps"), yoy.get("eps")
    m_now, m_yoy = margin(cur), margin(yoy)
    if not rev_yoy or rev_yoy == 0:
        return None
    return dict(
        yoy_rev_growth=(rev_now / rev_yoy - 1) if rev_now else None,
        yoy_eps_growth=((eps_now - eps_yoy) / abs(eps_yoy)) if eps_yoy else None,
        op_margin_delta=(m_now - m_yoy) if (m_now is not None and m_yoy is not None) else None,
    )


# --------------------------------------------------------------------------- #
# Screen
# --------------------------------------------------------------------------- #
@dataclass
class Hit:
    symbol: str
    earnings_date: str
    eps_actual: float
    eps_estimate: float
    eps_surprise_pct: float
    rev_surprise_pct: float
    yoy_rev_growth: float
    yoy_eps_growth: float
    op_margin_delta: float
    price_reaction: float
    rel_reaction: float
    expectation_gap_score: float


def pct(actual, est):
    if est in (None, 0) or actual is None:
        return None
    return (actual - est) / abs(est)


def screen(api: FMP, cfg: dict) -> pd.DataFrame:
    log.info("Building universe (top %d, cap > $%.0fB)...",
             cfg["universe_size"], cfg["min_market_cap"] / 1e9)
    universe = get_universe(api, cfg["universe_size"], cfg["min_market_cap"])
    log.info("Universe: %d tickers", len(universe))
    spy = get_spy_series(api, cfg["lookback_days"])

    hits: list[Hit] = []
    for i, sym in enumerate(universe, 1):
        if i % 50 == 0:
            log.info("...%d/%d processed, %d hits so far", i, len(universe), len(hits))
        try:
            er = latest_earnings(api, sym, cfg["lookback_days"])
            if not er:
                continue

            eps_s = pct(er.get("eps"), er.get("epsEstimated"))
            rev_s = pct(er.get("revenue"), er.get("revenueEstimated"))
            # Need a genuine beat on BOTH lines to call results "good".
            if eps_s is None or rev_s is None:
                continue
            if eps_s < cfg["eps_surprise_min"] or rev_s < cfg["rev_surprise_min"]:
                continue

            q = quarterly_quality(api, sym)
            if not q:
                continue
            if (q["yoy_rev_growth"] is None or q["yoy_rev_growth"] < cfg["yoy_rev_growth_min"]):
                continue
            if (q["yoy_eps_growth"] is None or q["yoy_eps_growth"] < cfg["yoy_eps_growth_min"]):
                continue
            if (q["op_margin_delta"] is not None and q["op_margin_delta"] < cfg["margin_tolerance"]):
                continue  # margins eroded materially -> that's a fundamental reason

            pr = price_reaction(api, sym, er["date"], spy)
            if not pr:
                continue
            reaction, rel = pr
            if reaction > cfg["price_drop_threshold"]:
                continue  # didn't actually fall hard
            if rel > cfg["rel_underperf_threshold"]:
                continue  # fell only because the whole market fell

            # Expectation gap: reward the size of the beat/growth, penalise the
            # drop. Bigger = punished harder despite a stronger print.
            good = (eps_s + rev_s + q["yoy_rev_growth"]) / 3
            gap = good - reaction  # reaction is negative, so -reaction adds

            hits.append(Hit(
                symbol=sym,
                earnings_date=er["date"],
                eps_actual=round(er.get("eps", 0), 3),
                eps_estimate=round(er.get("epsEstimated", 0), 3),
                eps_surprise_pct=round(eps_s, 4),
                rev_surprise_pct=round(rev_s, 4),
                yoy_rev_growth=round(q["yoy_rev_growth"], 4),
                yoy_eps_growth=round(q["yoy_eps_growth"], 4),
                op_margin_delta=round(q["op_margin_delta"], 4) if q["op_margin_delta"] is not None else None,
                price_reaction=round(reaction, 4),
                rel_reaction=round(rel, 4),
                expectation_gap_score=round(gap, 4),
            ))
        except Exception as e:  # never let one bad ticker kill the run
            log.debug("skip %s (%s)", sym, e)
            continue

    df = pd.DataFrame([asdict(h) for h in hits])
    if not df.empty:
        df = df.sort_values("expectation_gap_score", ascending=False).reset_index(drop=True)
    return df


# --------------------------------------------------------------------------- #
def main():
    p = argparse.ArgumentParser(description="Beat-and-drop earnings screener")
    p.add_argument("--api-key", default=os.environ.get("FMP_API_KEY"))
    p.add_argument("--universe-size", type=int, default=DEFAULTS["universe_size"])
    p.add_argument("--lookback-days", type=int, default=DEFAULTS["lookback_days"])
    p.add_argument("--price-drop", type=float, default=DEFAULTS["price_drop_threshold"],
                   help="e.g. -0.04 = fell at least 4%")
    p.add_argument("--out", default="beat_and_drop_results.csv")
    args = p.parse_args()

    if not args.api_key:
        sys.exit("Set FMP_API_KEY env var or pass --api-key")

    cfg = dict(DEFAULTS)
    cfg.update(universe_size=args.universe_size,
               lookback_days=args.lookback_days,
               price_drop_threshold=args.price_drop)

    api = FMP(args.api_key, cfg["request_pause"])
    df = screen(api, cfg)

    if df.empty:
        log.info("No candidates matched the screen.")
        return
    df.to_csv(args.out, index=False)
    log.info("Found %d candidates -> %s", len(df), args.out)
    with pd.option_context("display.max_rows", 30, "display.width", 160):
        print(df.head(30).to_string(index=False))


if __name__ == "__main__":
    main()
