"""Panel: the frozen data contract every screen runs against.

All price fields are *wide* DataFrames — index = DatetimeIndex of trading days
(ascending), columns = tickers. NaN = no data for that ticker/day (never 0).

Split/dividend adjustment (critical for gap/overnight screens): `load_panel`
back-adjusts open/high/low/close/vwap by the same-day factor adjclose/close and
divides volume by it, so raw-price fields never show phantom split gaps. Dividend
ex-dates still inject yield-sized artificial gaps — accepted at daily granularity.

`earnings` is long-format: ticker, date, eps_actual, eps_est, rev_actual, rev_est.
Rows with NaN eps_actual are *future scheduled* announcements — usable only by
screens whose information set legitimately contains the schedule (pre-earnings
run-up); every other earnings screen must filter `eps_actual.notna()`.

`benchmarks` is wide: date x symbol adjusted closes (SPY + SPDR sector ETFs).
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_INPUTS = os.path.join(HERE, "inputs")

PRICE_FIELDS = ("open", "high", "low", "close", "volume", "vwap", "adjclose")


class MissingInputError(Exception):
    """An input file/column a screen needs is absent — screen is skipped, not crashed."""


def load_config(path: str | None = None) -> dict:
    with open(path or os.path.join(HERE, "config.json")) as f:
        return json.load(f)


@dataclass
class Panel:
    open: pd.DataFrame
    high: pd.DataFrame
    low: pd.DataFrame
    close: pd.DataFrame
    volume: pd.DataFrame
    vwap: pd.DataFrame
    adjclose: pd.DataFrame
    returns: pd.DataFrame            # adjclose.pct_change()
    meta: pd.DataFrame               # index=ticker: company, sector, industry, market_cap
    earnings: pd.DataFrame           # long format, see module docstring
    benchmarks: pd.DataFrame         # date x symbol adjusted closes
    as_of: str
    _adv: dict = field(default_factory=dict, repr=False)

    # ---------------------------------------------------------------- helpers

    def adv(self, d: int = 20) -> pd.DataFrame:
        """Rolling mean dollar volume (close * volume), cached per window."""
        if d not in self._adv:
            self._adv[d] = (self.close * self.volume).rolling(d, min_periods=d).mean()
        return self._adv[d]

    def cap(self) -> pd.Series:
        return pd.to_numeric(self.meta["market_cap"], errors="coerce")

    def sectors(self) -> pd.Series:
        return self.meta["sector"]

    def market_returns(self, cfg: dict) -> pd.Series:
        sym = cfg["benchmarks"]["market"]
        if sym not in self.benchmarks.columns:
            raise MissingInputError(f"benchmark {sym} missing from benchmarks")
        return self.benchmarks[sym].pct_change()

    def sector_etf_returns(self, cfg: dict) -> pd.DataFrame:
        """date x sector-name frame of the mapped sector ETF's daily returns.

        Sectors whose ETF is missing from benchmarks come back as NaN columns.
        """
        mapping = cfg["benchmarks"]["sector_etfs"]
        out = {}
        for sector in self.sectors().dropna().unique():
            etf = mapping.get(sector)
            if etf is not None and etf in self.benchmarks.columns:
                out[sector] = self.benchmarks[etf].pct_change()
            else:
                out[sector] = pd.Series(np.nan, index=self.benchmarks.index)
        if not out:
            raise MissingInputError("no sector ETF returns available")
        return pd.DataFrame(out)

    def require_earnings(self) -> pd.DataFrame:
        if self.earnings is None or self.earnings.empty:
            raise MissingInputError("earnings.csv missing or empty")
        return self.earnings


# -------------------------------------------------------------------- loading

def _pivot(long: pd.DataFrame, value: str) -> pd.DataFrame:
    wide = long.pivot_table(index="date", columns="ticker", values=value, aggfunc="last")
    wide.index = pd.to_datetime(wide.index)
    return wide.sort_index()


def load_panel(input_dir: str = DEFAULT_INPUTS, cfg: dict | None = None) -> Panel:
    cfg = cfg or load_config()
    prices_path = os.path.join(input_dir, "prices.csv.gz")
    if not os.path.exists(prices_path):
        raise MissingInputError(f"{prices_path} not found — run build_screen_inputs.py first")
    long = pd.read_csv(prices_path)
    long["date"] = pd.to_datetime(long["date"])
    long = long.drop_duplicates(subset=["ticker", "date"], keep="last")

    frames = {f: _pivot(long, f) for f in PRICE_FIELDS}

    # Back-adjust raw fields so overnight/gap math never sees split gaps.
    factor = (frames["adjclose"] / frames["close"]).replace([np.inf, -np.inf], np.nan)
    for f in ("open", "high", "low", "close", "vwap"):
        frames[f] = frames[f] * factor
    frames["volume"] = frames["volume"] / factor

    min_bars = cfg["universe"]["min_price_bars"]
    enough = frames["close"].notna().sum() >= min_bars
    keep = enough[enough].index
    for f in PRICE_FIELDS:
        frames[f] = frames[f][keep]

    meta_path = os.path.join(input_dir, "universe.csv")
    if os.path.exists(meta_path):
        meta = pd.read_csv(meta_path).drop_duplicates("ticker").set_index("ticker")
    else:
        meta = pd.DataFrame(index=keep, columns=["company", "sector", "industry", "market_cap"])
    meta = meta.reindex(keep)

    earn_path = os.path.join(input_dir, "earnings.csv")
    if os.path.exists(earn_path):
        earnings = pd.read_csv(earn_path)
        earnings["date"] = pd.to_datetime(earnings["date"])
        earnings = earnings.drop_duplicates(subset=["ticker", "date"], keep="last")
    else:
        earnings = pd.DataFrame(
            columns=["ticker", "date", "eps_actual", "eps_est", "rev_actual", "rev_est"])

    bench_path = os.path.join(input_dir, "benchmarks.csv.gz")
    if os.path.exists(bench_path):
        blong = pd.read_csv(bench_path)
        blong["date"] = pd.to_datetime(blong["date"])
        benchmarks = (blong.pivot_table(index="date", columns="symbol", values="adjclose",
                                        aggfunc="last")
                      .sort_index().reindex(frames["close"].index))
    else:
        benchmarks = pd.DataFrame(index=frames["close"].index)

    as_of_path = os.path.join(input_dir, "as_of.txt")
    as_of = open(as_of_path).read().strip() if os.path.exists(as_of_path) else "unknown"

    return Panel(
        returns=frames["adjclose"].pct_change(),
        meta=meta, earnings=earnings, benchmarks=benchmarks, as_of=as_of,
        **frames,
    )


# ------------------------------------------------------------- synthetic fixture

SYN_SECTORS = ("Technology", "Energy", "Healthcare", "Financial Services")
SYN_END = "2025-12-31"  # fixed so the fixture is deterministic regardless of run date


def synthetic_panel(n_tickers: int = 16, n_days: int = 400, seed: int = 7) -> Panel:
    """Deterministic panel with engineered archetypes so every screen family has signal.

    Ticker archetypes cycle: trender (drift up), meanrev (AR(1)<0), highvol, gapper.
    Synthetic earnings every ~63 trading days with alternating beat/miss by ticker
    parity, plus one future scheduled row per ticker (NaN actuals).
    """
    rng = np.random.default_rng(seed)
    dates = pd.bdate_range(end=SYN_END, periods=n_days)
    tickers = [f"SYN{i:02d}" for i in range(n_tickers)]

    close = {}
    opens = {}
    vols = {}
    for i, t in enumerate(tickers):
        kind = i % 4
        n = len(dates)
        if kind == 0:      # trender
            r = rng.normal(0.0012, 0.012, n)
        elif kind == 1:    # mean reverter
            e = rng.normal(0, 0.015, n)
            r = np.zeros(n)
            for j in range(1, n):
                r[j] = -0.35 * r[j - 1] + e[j]
        elif kind == 2:    # high vol
            r = rng.normal(0.0002, 0.035, n)
        else:              # gapper (overnight jumps)
            r = rng.normal(0.0004, 0.010, n)
        px = 20.0 * (1 + i * 0.35) * np.cumprod(1 + r)
        gap = rng.normal(0, 0.012 if kind == 3 else 0.003, n)
        op = np.empty(n)
        op[0] = px[0]
        op[1:] = px[:-1] * (1 + gap[1:])
        v = rng.lognormal(mean=13.5 + 0.1 * i, sigma=0.35, size=n)
        close[t], opens[t], vols[t] = px, op, v

    close = pd.DataFrame(close, index=dates)
    open_ = pd.DataFrame(opens, index=dates)
    volume = pd.DataFrame(vols, index=dates)
    hi_noise = pd.DataFrame(np.abs(rng.normal(0, 0.006, close.shape)), index=dates,
                            columns=tickers)
    lo_noise = pd.DataFrame(np.abs(rng.normal(0, 0.006, close.shape)), index=dates,
                            columns=tickers)
    high = pd.concat([open_, close], axis=0).groupby(level=0).max() * (1 + hi_noise)
    low = pd.concat([open_, close], axis=0).groupby(level=0).min() * (1 - lo_noise)
    vwap = (open_ + high + low + close) / 4.0

    meta = pd.DataFrame({
        "company": [f"Synthetic {t}" for t in tickers],
        "sector": [SYN_SECTORS[i % len(SYN_SECTORS)] for i in range(n_tickers)],
        "industry": ["Synthetic"] * n_tickers,
        "market_cap": [1.5e9 * (1 + i) ** 2.2 for i in range(n_tickers)],
    }, index=pd.Index(tickers, name="ticker"))

    rows = []
    for i, t in enumerate(tickers):
        offset = 10 + (i * 7) % 40
        e_dates = dates[offset::63]
        for k, d in enumerate(e_dates):
            est = 1.0 + 0.05 * k
            beat = 0.08 if (i + k) % 2 == 0 else -0.06
            actual = est * (1 + beat + rng.normal(0, 0.01))
            rev_est = 1e9 * (1 + 0.02 * k)
            rows.append({"ticker": t, "date": d, "eps_actual": round(actual, 4),
                         "eps_est": round(est, 4),
                         "rev_actual": round(rev_est * (1 + beat / 2), 0),
                         "rev_est": round(rev_est, 0)})
        rows.append({"ticker": t,
                     "date": dates[-1] + pd.offsets.BDay(5 + i % 10),
                     "eps_actual": np.nan, "eps_est": 1.0 + 0.05 * len(e_dates),
                     "rev_actual": np.nan, "rev_est": 1e9})
    earnings = pd.DataFrame(rows)

    cfg = load_config()
    etf_map = cfg["benchmarks"]["sector_etfs"]
    bench = {"SPY": close.mean(axis=1)}
    for sector in SYN_SECTORS:
        cols = [t for i, t in enumerate(tickers) if SYN_SECTORS[i % len(SYN_SECTORS)] == sector]
        bench[etf_map[sector]] = close[cols].mean(axis=1)
    benchmarks = pd.DataFrame(bench, index=dates)

    return Panel(open=open_, high=high, low=low, close=close, volume=volume, vwap=vwap,
                 adjclose=close.copy(), returns=close.pct_change(),
                 meta=meta, earnings=earnings, benchmarks=benchmarks,
                 as_of=f"{SYN_END} (synthetic, seed={seed})")


# ---------------------------------------------------------------------- selftest

def selftest() -> int:
    p = synthetic_panel()
    n_days, n_tickers = p.close.shape
    assert n_tickers == 16 and n_days == 400, p.close.shape
    assert list(p.close.columns) == sorted(p.close.columns)
    assert p.close.index.is_monotonic_increasing
    assert (p.close.stack() > 0).all(), "non-positive close in synthetic panel"
    assert (p.low <= p.high).all().all()
    assert ((p.low <= p.close) & (p.close <= p.high)).all().all()
    assert ((p.low <= p.open) & (p.open <= p.high)).all().all()
    assert p.returns.iloc[0].isna().all(), "returns row 0 must be NaN (causality)"
    assert p.adv(20).iloc[:19].isna().all().all(), "adv must need a full window"

    e = p.require_earnings()
    assert set(e.columns) >= {"ticker", "date", "eps_actual", "eps_est",
                              "rev_actual", "rev_est"}
    past = e[e["eps_actual"].notna()]
    fut = e[e["eps_actual"].isna()]
    assert len(past) >= 16 * 5 and len(fut) == 16, (len(past), len(fut))
    assert (fut["date"] > p.close.index[-1]).all(), "scheduled rows must be in the future"

    cfg = load_config()
    mr = p.market_returns(cfg)
    assert len(mr) == n_days and mr.iloc[1:].notna().all()
    ser = p.sector_etf_returns(cfg)
    assert set(ser.columns) == set(SYN_SECTORS)
    assert ser.iloc[1:].notna().all().all()

    # determinism
    p2 = synthetic_panel()
    assert p.close.equals(p2.close) and p.earnings.equals(p2.earnings)

    # two panels don't share adv caches
    assert p.adv(20) is not p2.adv(20)

    print(f"panel.py selftest: OK — {n_tickers} tickers x {n_days} days, "
          f"{len(past)} earnings events, benchmarks {list(p.benchmarks.columns)}")
    return 0


if __name__ == "__main__":
    import sys
    if "--selftest" in sys.argv:
        sys.exit(selftest())
    print(__doc__)
