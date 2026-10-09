"""Deterministic offline stand-ins for the FMP and Massive clients.

They return raw rows shaped like the real APIs, so the vendored build_record, the
history assembly and every stage run on realistic code paths without network or keys.
"""
from __future__ import annotations

import zlib

import numpy as np
import pandas as pd

from hermes.data.massive import BAR_COLS, assemble_history

SECTORS = ["Technology", "Healthcare", "Industrials", "Financial Services", "Energy",
           "Consumer Cyclical", "Consumer Defensive", "Utilities"]
ETF = {"Technology": "XLK", "Healthcare": "XLV", "Industrials": "XLI",
       "Financial Services": "XLF", "Energy": "XLE", "Consumer Cyclical": "XLY",
       "Consumer Defensive": "XLP", "Utilities": "XLU", "Basic Materials": "XLB",
       "Real Estate": "XLRE", "Communication Services": "XLC"}


class _Http:
    calls = 0


class FakeMarket:
    def __init__(self, n: int = 120, session: str = "2026-10-08", seed: int = 3):
        self.session = session
        self.rng = np.random.default_rng(seed)
        self.tickers = [f"T{i:03d}" for i in range(n)]
        self.dates = pd.bdate_range("2007-01-02", session)
        T = len(self.dates)
        r = self.rng
        mkt = r.normal(0.0003, 0.011, T)
        crash = (self.dates >= "2008-09-01") & (self.dates <= "2008-11-30")
        mkt[crash] = r.normal(-0.006, 0.035, crash.sum())
        covid = (self.dates >= "2020-02-19") & (self.dates <= "2020-04-15")
        mkt[covid] = r.normal(-0.004, 0.04, covid.sum())
        self.mkt = mkt
        self.sector_f = {s: r.normal(0, 0.006, T) for s in ETF}
        self.meta = {}
        self.ret = {}
        for i, t in enumerate(self.tickers):
            sec = SECTORS[i % len(SECTORS)]
            beta = 0.6 + 0.9 * r.random()
            drift = r.normal(0.0004, 0.0004)
            idio = r.normal(drift, 0.012 + 0.01 * r.random(), T)
            self.ret[t] = beta * mkt + self.sector_f[sec] + idio
            self.meta[t] = {"sector": sec, "beta": beta, "mcap": float(5e9 + 5e11 * r.random())}
        for s, e in ETF.items():
            self.ret[e] = mkt + self.sector_f[s]
        self.ret["SPY"] = mkt

    def bars(self, sym: str, frm: str, to: str) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
        rets = self.ret[sym]
        px = 50.0 * np.exp(np.cumsum(np.log1p(np.clip(rets, -0.5, 1.0))))
        r = np.random.default_rng(zlib.crc32(sym.encode()))
        rng_hl = 1 + np.abs(r.normal(0, 0.004, len(px)))
        df = pd.DataFrame({"date": self.dates.strftime("%Y-%m-%d"), "open": px / rng_hl ** 0.3,
                           "high": px * rng_hl, "low": px / rng_hl, "close": px,
                           "volume": np.full(len(px), 4e6), "vwap": px})
        df = df[(df["date"] >= frm) & (df["date"] <= to)].reset_index(drop=True)
        raw = df.copy()
        # a 2:1 split two years before the session for every 7th name
        split_date = (pd.Timestamp(self.session) - pd.Timedelta(days=730)).strftime("%Y-%m-%d")
        if sym.startswith("T") and int(sym[1:]) % 7 == 0:
            pre = raw["date"] < split_date
            for c in ("open", "high", "low", "close", "vwap"):
                raw.loc[pre, c] *= 2.0
            raw.loc[pre, "volume"] /= 2.0
        divs = pd.DataFrame({"ex_dividend_date": [split_date], "cash_amount": [0.5],
                             "historical_adjustment_factor": [0.99]})
        return raw[BAR_COLS], df[BAR_COLS], divs


class FakeMassive:
    def __init__(self, market: FakeMarket):
        self.m = market
        self.http = _Http()

    def aggs(self, ticker, frm, to, adjusted):
        raw, adj, _ = self.m.bars(ticker, frm, to)
        return adj if adjusted else raw

    def daily_history(self, ticker, frm, to):
        if ticker not in self.m.ret:
            return None
        raw, adj, divs = self.m.bars(ticker, frm, to)
        return assemble_history(raw, adj, divs)

    def has_listed_options(self, ticker):
        return True

    def _split_date(self):
        return (pd.Timestamp(self.m.session) - pd.Timedelta(days=730)).strftime("%Y-%m-%d")

    def splits_bulk(self, tickers, frm):
        rows = [{"ticker": t, "execution_date": self._split_date(), "split_from": 1,
                 "split_to": 2, "historical_adjustment_factor": 0.5}
                for t in tickers if t.startswith("T") and int(t[1:]) % 7 == 0]
        return pd.DataFrame(rows, columns=["ticker", "execution_date", "split_from",
                                           "split_to", "historical_adjustment_factor"])

    def dividends_bulk(self, tickers, frm):
        return pd.DataFrame([{"ticker": t, "ex_dividend_date": self._split_date(),
                              "cash_amount": 0.5, "historical_adjustment_factor": 0.99}
                             for t in tickers])

    def write_flatfiles(self, root, frm, prefix="us_stocks_sip/day_aggs_v1"):
        """Materialise the fake market as Massive day-aggregate flat files under root."""
        import os
        frames = []
        for sym in self.m.ret:
            raw, _, _ = self.m.bars(sym, frm, self.m.session)
            frames.append(raw.assign(ticker=sym))
        allb = pd.concat(frames)
        for d, g in allb.groupby("date"):
            p = os.path.join(root, prefix, d[:4], d[5:7])
            os.makedirs(p, exist_ok=True)
            g.assign(window_start=0, transactions=1)[
                ["ticker", "volume", "open", "close", "high", "low", "window_start",
                 "transactions"]].to_csv(os.path.join(p, f"{d}.csv.gz"), index=False)

    def snapshot_spreads_bp(self, tickers, chunk=200):
        return {t: 3.0 for t in tickers}


class FakeFMP:
    def __init__(self, market: FakeMarket):
        self.m = market
        self.http = _Http()

    def discover_universe(self, target=1000, min_market_cap=1.5e9):
        rows = [{"ticker": t, "company": f"{t} Inc.", "sector": self.m.meta[t]["sector"],
                 "industry": "Test", "market_cap": self.m.meta[t]["mcap"]}
                for t in self.m.tickers]
        return sorted(rows, key=lambda r: -r["market_cap"])[:target]

    def fundamentals(self, sym):
        from hermes import vendored
        r = np.random.default_rng(int(sym[1:]) + 100)
        mc = self.m.meta[sym]["mcap"]
        rev0 = mc * (0.2 + r.random())
        g = r.normal(0.06, 0.08)
        inc, bal, cf, km, rat = [], [], [], [], []
        for y in range(5):
            rev = rev0 / (1 + g) ** y
            gm = 0.25 + 0.4 * r.random()
            ni = rev * r.normal(0.12, 0.06)
            ta = rev * (1.2 + r.random())
            inc.append({"revenue": rev, "grossProfit": rev * gm, "operatingIncome": rev * 0.18,
                        "netIncome": ni, "epsDiluted": ni / 1e9, "interestExpense": rev * 0.01,
                        "weightedAverageShsOut": 1e9 * (1 + 0.01 * y),
                        "sellingGeneralAndAdministrativeExpenses": rev * 0.1,
                        "researchAndDevelopmentExpenses": rev * 0.03,
                        "depreciationAndAmortization": rev * 0.04,
                        "incomeTaxExpense": ni * 0.2, "incomeBeforeTax": ni * 1.25,
                        "acceptedDate": f"{2026 - y}-02-15 16:05:00"})
            bal.append({"totalAssets": ta, "totalCurrentAssets": ta * 0.4,
                        "totalCurrentLiabilities": ta * 0.2 * (1 + r.random()),
                        "longTermDebt": ta * 0.2 * r.random(), "totalDebt": ta * 0.25,
                        "cashAndShortTermInvestments": ta * 0.1,
                        "totalStockholdersEquity": ta * 0.5, "propertyPlantEquipmentNet": ta * 0.3,
                        "retainedEarnings": ta * 0.3, "totalLiabilities": ta * 0.5,
                        "netReceivables": rev * 0.1, "inventory": rev * 0.08})
            cf.append({"freeCashFlow": ni * 0.9, "netCashProvidedByOperatingActivities": ni * 1.2,
                       "capitalExpenditure": -rev * 0.05, "depreciationAndAmortization": rev * 0.04,
                       "commonStockRepurchased": -ni * 0.2, "commonStockIssuance": 0.0,
                       "netDebtIssuance": -ni * 0.05, "netDividendsPaid": -ni * 0.3})
            km.append({"returnOnInvestedCapital": r.normal(0.12, 0.06),
                       "returnOnEquity": r.normal(0.15, 0.07), "marketCap": mc,
                       "enterpriseValue": mc * 1.1})
            rat.append({"grossProfitMargin": gm, "operatingProfitMargin": 0.18,
                        "debtToEquityRatio": r.random(), "currentRatio": 1 + r.random(),
                        "interestCoverageRatio": 5 + 20 * r.random()})
        prof = {"companyName": f"{sym} Inc.", "sector": self.m.meta[sym]["sector"],
                "industry": "Test"}
        quote = {"marketCap": mc, "price": 100.0}
        rec = vendored.fundamental().build_screen_inputs.build_record(
            sym, km, rat, inc, bal, cf, prof, quote)
        if rec is not None:
            rec["accepted_date"] = "2026-02-15"
        return rec, {"income": inc}

    def earnings(self, sym, limit=16):
        r = np.random.default_rng(int(sym[1:]) + 7) if sym.startswith("T") else None
        if r is None:
            return []
        sess = pd.Timestamp(self.m.session)
        rows = []
        for q in range(1, 13):
            d = (sess - pd.Timedelta(days=91 * q - 30 + int(sym[1:]) % 40)).strftime("%Y-%m-%d")
            est = 1.0 + r.random()
            rows.append({"ticker": sym, "date": d, "eps_actual": est * (1 + r.normal(0.03, 0.08)),
                         "eps_est": est, "rev_actual": 1e9 * (1 + r.normal(0.01, 0.03)),
                         "rev_est": 1e9})
        nxt = (sess + pd.Timedelta(days=5 + int(sym[1:]) % 80)).strftime("%Y-%m-%d")
        rows.append({"ticker": sym, "date": nxt, "eps_actual": None, "eps_est": 1.5,
                     "rev_actual": None, "rev_est": 1e9})
        return rows
