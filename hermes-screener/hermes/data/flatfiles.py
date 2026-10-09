"""Massive Flat Files: bulk daily OHLCV history from the S3-compatible file store.

Massive serves historical data as one file per trading day covering every US
stock and ETF (Stocks Day Aggregates; Starter = 5 years, Developer = 10,
Advanced = all history since 2003). It is far cheaper than per-ticker REST
calls: ~250 files a year, cached locally, so after the first build each night
downloads only the newest day.

  endpoint   https://files.massive.com       bucket  flatfiles
  key        us_stocks_sip/day_aggs_v1/YYYY/MM/YYYY-MM-DD.csv.gz
  columns    ticker, volume, open, close, high, low, window_start (ns), transactions
  auth       S3 Access Key ID + Secret Access Key from massive.com/dashboard/keys
             (env MASSIVE_S3_ACCESS_KEY_ID / MASSIVE_S3_SECRET_ACCESS_KEY)

The bars are raw (unadjusted). Splits and dividends come from the REST
corporate-action endpoints in bulk, and `apply_corporate_actions` builds
split_close and the total-return adjclose. The vendored ta panel then
back-adjusts the raw OHLC by adjclose/close, exactly as it did with FMP data.
"""
from __future__ import annotations

import datetime as dt
import logging
import os
from concurrent.futures import ThreadPoolExecutor

import numpy as np
import pandas as pd

from .http import require_key

log = logging.getLogger(__name__)


class FlatFiles:
    def __init__(self, cfg_ff: dict, cache_dir: str, client=None):
        self.c = cfg_ff
        self.cache_dir = cache_dir
        self.prefix = cfg_ff["day_aggs_prefix"].strip("/")
        self.bucket = cfg_ff["bucket"]
        self._client = client
        self.downloaded = 0
        self.cached = 0
        self.missing: list[str] = []
        os.makedirs(cache_dir, exist_ok=True)

    @staticmethod
    def available(cfg_ff: dict) -> bool:
        return bool(os.environ.get(cfg_ff["access_key_env"])
                    and os.environ.get(cfg_ff["secret_key_env"]))

    @property
    def client(self):
        if self._client is None:
            import boto3  # noqa: PLC0415 — only needed for live flat-file downloads
            from botocore.config import Config  # noqa: PLC0415
            session = boto3.Session(
                aws_access_key_id=require_key(self.c["access_key_env"]),
                aws_secret_access_key=require_key(self.c["secret_key_env"]))
            self._client = session.client(
                "s3", endpoint_url=self.c["endpoint"],
                config=Config(signature_version="s3v4", retries={"max_attempts": 5}))
        return self._client

    def _key(self, day: str) -> str:
        return f"{self.prefix}/{day[:4]}/{day[5:7]}/{day}.csv.gz"

    def _local(self, day: str) -> str:
        return os.path.join(self.cache_dir, day[:4], f"{day}.csv.gz")

    def list_days(self, frm: str, to: str) -> list[str]:
        """Trading days that have a file, from the bucket listing (month by month)."""
        days = []
        start = dt.date.fromisoformat(frm).replace(day=1)
        end = dt.date.fromisoformat(to)
        m = start
        while m <= end:
            pfx = f"{self.prefix}/{m.year:04d}/{m.month:02d}/"
            token = None
            while True:
                kw = {"Bucket": self.bucket, "Prefix": pfx}
                if token:
                    kw["ContinuationToken"] = token
                resp = self.client.list_objects_v2(**kw)
                for obj in resp.get("Contents", []) or []:
                    name = obj["Key"].rsplit("/", 1)[-1]
                    day = name.split(".")[0]
                    if frm <= day <= to:
                        days.append(day)
                if not resp.get("IsTruncated"):
                    break
                token = resp.get("NextContinuationToken")
            m = (m.replace(day=28) + dt.timedelta(days=4)).replace(day=1)
        return sorted(set(days))

    def fetch_day(self, day: str) -> str | None:
        path = self._local(day)
        if os.path.exists(path) and os.path.getsize(path) > 0:
            self.cached += 1
            return path
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp = path + ".part"
        try:
            self.client.download_file(self.bucket, self._key(day), tmp)
            os.replace(tmp, path)
            self.downloaded += 1
            return path
        except Exception as e:  # noqa: BLE001 — missing day / permission -> recorded
            log.warning("flat file %s unavailable: %s", day, type(e).__name__)
            self.missing.append(day)
            if os.path.exists(tmp):
                os.remove(tmp)
            return None

    def bars(self, tickers: set[str], frm: str, to: str, workers: int = 8) -> pd.DataFrame:
        """Long raw bars for `tickers` between frm and to: ticker, date, OHLCV, vwap."""
        days = self.list_days(frm, to)
        if not days:
            raise RuntimeError(f"no Massive flat files listed between {frm} and {to} "
                               "(check S3 credentials and that the plan includes Flat Files)")
        with ThreadPoolExecutor(max_workers=workers) as ex:
            paths = list(ex.map(self.fetch_day, days))
        frames = []
        for day, p in zip(days, paths):
            if p is None:
                continue
            df = pd.read_csv(p, usecols=["ticker", "volume", "open", "close", "high", "low"],
                             dtype={"ticker": str})
            df = df[df["ticker"].isin(tickers)]
            if df.empty:
                continue
            df.insert(1, "date", day)
            frames.append(df)
        if not frames:
            return pd.DataFrame(columns=["ticker", "date", "open", "high", "low", "close",
                                         "volume", "vwap"])
        out = pd.concat(frames, ignore_index=True)
        out["vwap"] = np.nan      # not in day aggregates; screens needing vwap skip (NaN)
        return out[["ticker", "date", "open", "high", "low", "close", "volume", "vwap"]] \
            .sort_values(["ticker", "date"]).reset_index(drop=True)


def _factor_after(dates: np.ndarray, ev_dates: np.ndarray, ev_factors: np.ndarray) -> np.ndarray:
    """Factor of the first event strictly after each date (1.0 when none)."""
    out = np.ones(len(dates))
    if len(ev_dates):
        idx = np.searchsorted(ev_dates, dates, side="right")
        has = idx < len(ev_dates)
        out[has] = ev_factors[idx[has]]
    return out


def apply_corporate_actions(raw: pd.DataFrame, splits: pd.DataFrame | None,
                            divs: pd.DataFrame | None) -> pd.DataFrame:
    """Add split_close and total-return adjclose to one ticker's raw bars.

    Both REST corporate-action feeds carry `historical_adjustment_factor`: the
    cumulative factor for a price dated before the event. A split factor
    missing from the feed is rebuilt from split_from / split_to.
    """
    out = raw.sort_values("date").reset_index(drop=True).copy()
    d = out["date"].astype(str).to_numpy()
    sf = np.ones(len(out))
    if splits is not None and not splits.empty:
        s = splits.sort_values("execution_date")
        f = s["historical_adjustment_factor"].astype(float).to_numpy() \
            if "historical_adjustment_factor" in s and s["historical_adjustment_factor"].notna().all() \
            else np.cumprod((s["split_from"].astype(float) / s["split_to"].astype(float))
                            .to_numpy()[::-1])[::-1]
        sf = _factor_after(d, s["execution_date"].astype(str).to_numpy(), f)
    df_ = np.ones(len(out))
    if divs is not None and not divs.empty:
        v = divs.dropna(subset=["historical_adjustment_factor"]).sort_values("ex_dividend_date")
        df_ = _factor_after(d, v["ex_dividend_date"].astype(str).to_numpy(),
                            v["historical_adjustment_factor"].astype(float).to_numpy())
    out["split_close"] = out["close"].astype(float) * sf
    out["adjclose"] = out["split_close"] * df_
    return out
