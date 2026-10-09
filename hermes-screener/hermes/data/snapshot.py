"""build_snapshot: ingestion into an immutable nightly snapshot (spec §2.3).

data/snapshots/YYYY-MM-DD/
  universe_raw.csv                    FMP discovery (live-only market caps)
  fundamental/                        the four CSVs the 20 screens read + as_of.txt
    buffett_quality_input.csv  magic_formula_input.csv  piotroski_input.csv
    extended_input.csv  fundamentals_pit.csv (ticker, accepted_date)
  ta/                                 exactly the vendored ta-screener input contract
    prices.csv.gz  earnings.csv  universe.csv  benchmarks.csv.gz  as_of.txt
  market.csv.gz                       long-history SPY + sector ETFs (stress windows)
  liquidity.csv                       adv20, spread_bp, spread_source, has_options
  raw/fmp_fundamentals.jsonl.gz       raw FMP statement pulls
  fetch_failures.json
  manifest.json                       row counts + SHA-256 per file (written last)

This is the only engine tool allowed network egress. A snapshot is write-once:
if manifest.json already exists, building it again is refused.
"""
from __future__ import annotations

import datetime as dt
import gzip
import hashlib
import json
import logging
import os
from concurrent.futures import ThreadPoolExecutor

import numpy as np
import pandas as pd

from .. import vendored
from .flatfiles import FlatFiles, apply_corporate_actions
from .massive import years_ago

log = logging.getLogger(__name__)

PIT_FIELDS = {
    # spec §2.2.1: live-only fields may feed today's screen, never backtests/learning
    "universe_raw.csv": "live-only (current-membership universe proxy)",
    "fundamental/*": "live-only valuation anchor (live quote market cap); statements "
                     "keyed by accepted_date in fundamentals_pit.csv",
    "ta/universe.csv:market_cap": "live-only (current market cap used for all dates)",
    "ta/prices.csv.gz": "pit-safe (back-adjusted at load)",
    "ta/earnings.csv": "pit-safe except scheduled dates (eps_actual empty)",
    "liquidity.csv": "live-only",
}


class SnapshotExistsError(RuntimeError):
    pass


def snapshot_dir(cfg, session: str) -> str:
    return os.path.join(cfg.path("snapshots"), session)


def sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def corwin_schultz_bp(high: pd.Series, low: pd.Series, days: int = 20) -> float:
    """Median Corwin-Schultz (2012) high-low spread estimate over the last `days`, in bp."""
    h, lo = np.log(high.to_numpy(float)), np.log(low.to_numpy(float))
    if len(h) < days + 1:
        return float("nan")
    beta = (h[1:] - lo[1:]) ** 2 + (h[:-1] - lo[:-1]) ** 2
    gamma = (np.maximum(h[1:], h[:-1]) - np.minimum(lo[1:], lo[:-1])) ** 2
    k = 3 - 2 * np.sqrt(2)
    a = (np.sqrt(2 * beta) - np.sqrt(beta)) / k - np.sqrt(gamma / k)
    s = 2 * (np.exp(a) - 1) / (1 + np.exp(a))
    s = np.clip(s, 0, None)[-days:]
    return float(np.nanmedian(s) * 1e4)


def last_session(massive, today: str | None = None) -> str:
    """Most recent completed US session = SPY's last daily bar."""
    to = today or dt.date.today().isoformat()
    frm = (dt.date.fromisoformat(to) - dt.timedelta(days=10)).isoformat()
    bars = massive.aggs("SPY", frm, to, adjusted=True)
    if bars is None or bars.empty:
        raise RuntimeError("cannot determine the last session (Massive SPY bars unavailable)")
    return str(bars["date"].iloc[-1])


def build_snapshot(cfg, fmp, massive, session: str, tickers: list[str] | None = None,
                   limit: int | None = None, flatfiles=None) -> dict:
    out_dir = snapshot_dir(cfg, session)
    if os.path.exists(os.path.join(out_dir, "manifest.json")):
        raise SnapshotExistsError(f"snapshot {session} already exists and is immutable")
    for sub in ("fundamental", "ta", "raw"):
        os.makedirs(os.path.join(out_dir, sub), exist_ok=True)
    infra, strat = cfg.infra, cfg.strategy
    failures: dict[str, list] = {"fundamentals": [], "prices": [], "earnings": [],
                                 "options": [], "benchmarks": []}

    # ---------------------------------------------------------- universe
    if tickers:
        universe = [{"ticker": t, "company": None, "sector": None, "industry": None,
                     "market_cap": None} for t in tickers]
    else:
        universe = fmp.discover_universe(strat["universe"]["target"],
                                         strat["universe"]["min_market_cap"])
    if limit:
        universe = universe[:limit]
    if not universe:
        raise RuntimeError("universe discovery returned nothing (FMP plan limits or network?)")
    syms = [u["ticker"] for u in universe]
    pd.DataFrame(universe).to_csv(os.path.join(out_dir, "universe_raw.csv"), index=False)
    log.info("snapshot %s: universe %d symbols", session, len(syms))

    # ------------------------------------------------------ fundamentals
    fund = vendored.fundamental().build_screen_inputs
    records, pit = [], []
    raw_path = os.path.join(out_dir, "raw", "fmp_fundamentals.jsonl.gz")
    with ThreadPoolExecutor(max_workers=infra["apis"]["fmp"]["workers"]) as ex, \
            gzip.open(raw_path, "wt") as raw_f:
        for sym, (rec, raw) in zip(syms, ex.map(fmp.fundamentals, syms)):
            raw_f.write(json.dumps({"ticker": sym, **raw}, default=str) + "\n")
            if rec is None:
                failures["fundamentals"].append(sym)
                continue
            pit.append({"ticker": sym, "accepted_date": rec.pop("accepted_date", None)})
            records.append(rec)
    records.sort(key=lambda r: r["ticker"])
    fdir = os.path.join(out_dir, "fundamental")
    fund.write_csv(os.path.join(fdir, "buffett_quality_input.csv"), fund.BUFFETT_COLS, records)
    fund.write_csv(os.path.join(fdir, "magic_formula_input.csv"), fund.MAGIC_COLS, records)
    fund.write_csv(os.path.join(fdir, "piotroski_input.csv"), fund.PIOTROSKI_COLS, records)
    fund.write_csv(os.path.join(fdir, "extended_input.csv"), fund.EXTENDED_COLS, records)
    pd.DataFrame(pit, columns=["ticker", "accepted_date"]).to_csv(
        os.path.join(fdir, "fundamentals_pit.csv"), index=False)
    with open(os.path.join(fdir, "as_of.txt"), "w") as f:
        f.write(f"{session} — {len(records)} companies\n")

    # meta from fundamentals overrides empty discovery meta (explicit-ticker runs)
    meta = {r["ticker"]: r for r in universe}
    for r in records:
        m = meta.setdefault(r["ticker"], {"ticker": r["ticker"]})
        for k in ("company", "sector", "industry", "market_cap"):
            if not m.get(k) and r.get(k):
                m[k] = r[k]

    # ------------------------------------------------- prices + earnings
    frm = years_ago(session, infra["history"]["ta_years"])
    bench = infra["benchmarks"]
    bsyms = [bench["market"], *bench["sector_etfs"]]
    long_from = infra["history"].get("market_since") or \
        years_ago(session, infra["history"].get("market_years", 5) - 0.1)
    histories, source = load_histories(cfg, massive, syms, bsyms, frm, long_from, session,
                                       flatfiles)
    log.info("snapshot %s: price history from %s (%d of %d names)", session, source,
             sum(1 for t in syms if t in histories), len(syms))
    options = options_flags(cfg, massive, syms, session) \
        if strat["universe"]["require_options"] else {}

    with ThreadPoolExecutor(max_workers=infra["apis"]["fmp"]["workers"]) as ex:
        earnings_by = dict(zip(syms, ex.map(fmp.earnings, syms)))

    price_frames, earn_rows, liq = [], [], []
    adv_days = strat["universe"]["adv_days"]
    for sym in syms:
        hist = histories.get(sym)
        if hist is not None:
            hist = hist[hist["date"] >= frm]
        if hist is None or hist.empty:
            failures["prices"].append(sym)
            continue
        hist = hist.copy()
        hist.insert(0, "ticker", sym)
        price_frames.append(hist)
        earn = earnings_by.get(sym) or []
        if not earn:
            failures["earnings"].append(sym)
        earn_rows.extend(earn)
        opts = options.get(sym)
        if opts is None and strat["universe"]["require_options"]:
            failures["options"].append(sym)
        tail = hist.tail(adv_days)
        liq.append({"ticker": sym,
                    "adv20": float((tail["close"] * tail["volume"]).mean())
                    if len(tail) == adv_days else np.nan,
                    "spread_cs_bp": corwin_schultz_bp(hist["high"], hist["low"], adv_days),
                    "has_options": opts,
                    "last_bar": str(hist["date"].iloc[-1])})

    prices = pd.concat(price_frames, ignore_index=True) if price_frames else pd.DataFrame()
    if prices.empty:
        raise RuntimeError("no price data fetched from Massive — aborting snapshot")
    tdir = os.path.join(out_dir, "ta")
    cols = ["ticker", "date", "open", "high", "low", "close", "volume", "vwap", "adjclose",
            "split_close"]  # split_close is extra: ignored by ta panel, used by the blend
    prices[cols].to_csv(os.path.join(tdir, "prices.csv.gz"), index=False, compression="gzip")
    pd.DataFrame(earn_rows, columns=["ticker", "date", "eps_actual", "eps_est", "rev_actual",
                                     "rev_est"]).to_csv(os.path.join(tdir, "earnings.csv"),
                                                        index=False)
    have = set(prices["ticker"])
    pd.DataFrame([m for t, m in sorted(meta.items()) if t in have],
                 columns=["ticker", "company", "sector", "industry", "market_cap"]).to_csv(
        os.path.join(tdir, "universe.csv"), index=False)

    # ------------------------------------------------------- benchmarks
    brows = []
    for sym in bsyms:
        h = histories.get(sym)
        if h is None or h.empty:
            failures["benchmarks"].append(sym)
            continue
        brows.append(pd.DataFrame({"symbol": sym, "date": h["date"], "adjclose": h["adjclose"],
                                   "split_close": h["split_close"], "high": h["high"],
                                   "low": h["low"], "volume": h["volume"]}))
    market = pd.concat(brows, ignore_index=True) if brows else pd.DataFrame(
        columns=["symbol", "date", "adjclose"])
    market.to_csv(os.path.join(out_dir, "market.csv.gz"), index=False, compression="gzip")
    market[market["date"] >= frm][["symbol", "date", "adjclose"]].to_csv(
        os.path.join(tdir, "benchmarks.csv.gz"), index=False, compression="gzip")

    # ------------------------------------------------------ liquidity
    liq_df = pd.DataFrame(liq)
    src = strat["universe"]["spread_source"]
    snap = {}
    if src in ("auto", "snapshot"):
        snap = massive.snapshot_spreads_bp(sorted(have))
    liq_df["spread_snapshot_bp"] = liq_df["ticker"].map(snap)
    if src == "snapshot" or (src == "auto" and len(snap) >= 0.8 * len(liq_df)):
        liq_df["spread_bp"] = liq_df["spread_snapshot_bp"]
        liq_df["spread_source"] = "massive_snapshot_nbbo"
    else:
        liq_df["spread_bp"] = liq_df["spread_cs_bp"]
        liq_df["spread_source"] = "corwin_schultz_20d_median"
    liq_df.to_csv(os.path.join(out_dir, "liquidity.csv"), index=False)

    n_earn = len({r["ticker"] for r in earn_rows})
    stamp = (f"{session}; {len(have)} tickers; {n_earn} with earnings; "
             f"{len(failures['prices'])} price fetch failures")
    with open(os.path.join(tdir, "as_of.txt"), "w") as f:
        f.write(stamp + "\n")
    with open(os.path.join(out_dir, "fetch_failures.json"), "w") as f:
        json.dump(failures, f, indent=1, sort_keys=True)
    status = {"fmp": _status(fmp), "massive": _status(massive)}
    for src, st in status.items():
        log.info("snapshot %s: %s HTTP status by endpoint: %s", session, src, st)
    return write_manifest(out_dir, session, extra={
        "api_calls": {"fmp": fmp.http.calls, "massive": massive.http.calls},
        "http_status": status, "universe_requested": len(syms),
        "price_source": source,
        "flatfiles": None if flatfiles is None else
        {"downloaded": flatfiles.downloaded, "cached": flatfiles.cached,
         "missing_days": len(flatfiles.missing), "errors": flatfiles.errors},
        "pit_classification": PIT_FIELDS})


def _status(client) -> dict:
    f = getattr(client.http, "status_summary", None)
    return f() if callable(f) else {}


def load_histories(cfg, massive, syms, bsyms, frm, long_from, session, flatfiles=None
                   ) -> tuple[dict[str, pd.DataFrame], str]:
    """{ticker: daily history with split_close + adjclose} and the source used.

    Massive Flat Files when S3 credentials are configured (one file per day for all
    names, cached locally; splits/dividends in bulk via REST). Otherwise per-ticker
    REST aggregates (fallback; slow and rate-limited for a full universe).
    """
    infra = cfg.infra
    every = list(dict.fromkeys(list(syms) + list(bsyms)))
    start = min(frm, long_from)
    mode = infra["apis"]["massive"].get("history_source", "auto")
    ff_cfg = infra["apis"]["massive"]["flatfiles"]
    if flatfiles is None and mode in ("auto", "flatfiles") and FlatFiles.available(ff_cfg):
        flatfiles = FlatFiles(ff_cfg, cfg.path("flatfiles"))
    if mode == "flatfiles" and flatfiles is None:
        raise RuntimeError("history_source=flatfiles but MASSIVE_S3_ACCESS_KEY_ID / "
                           "MASSIVE_S3_SECRET_ACCESS_KEY are not set")
    if flatfiles is not None and mode != "rest":
        raw = flatfiles.bars(set(every), start, session,
                             workers=int(ff_cfg.get("workers", 8)))
        if raw.empty:
            msg = (f"Massive flat files gave no bars ({len(flatfiles.missing)} days failed: "
                   f"{flatfiles.errors})")
            if mode == "flatfiles":
                raise RuntimeError(msg)
            log.warning("%s — falling back to per-ticker REST aggregates", msg)
            flatfiles = None
    if flatfiles is not None and mode != "rest":
        splits = massive.splits_bulk(every, start)
        divs = massive.dividends_bulk(every, start)
        if splits is None or divs is None:
            raise RuntimeError("Massive corporate actions (splits/dividends) unavailable — "
                               "cannot adjust flat-file prices")
        out = {}
        for t, g in raw.groupby("ticker", sort=False):
            sp = splits[splits["ticker"] == t] if len(splits) else None
            dv = divs[divs["ticker"] == t] if len(divs) else None
            h = apply_corporate_actions(g.drop(columns="ticker"), sp, dv)
            # day aggregates carry no VWAP: typical price (H+L+C)/3 stands in, flagged
            h["vwap"] = (h["high"] + h["low"] + h["close"]) / 3.0
            out[t] = h.reset_index(drop=True)
        return out, "massive_flatfiles"

    def one(sym):
        return sym, massive.daily_history(sym, frm if sym in set(syms) else long_from, session)

    with ThreadPoolExecutor(max_workers=infra["apis"]["massive"]["workers"]) as ex:
        res = dict(ex.map(one, every))
    return {k: v for k, v in res.items() if v is not None and not v.empty}, "massive_rest"


def options_flags(cfg, massive, syms, session) -> dict[str, bool | None]:
    """Listed-options flag per name, cached for a week (tradability rarely changes)."""
    cache = os.path.join(cfg.path("cache"), "options_flags.json")
    data = {}
    if os.path.exists(cache):
        with open(cache) as f:
            data = json.load(f)
    week = dt.date.fromisoformat(session).isocalendar()[:2]
    fresh = {t: v["flag"] for t, v in data.items()
             if tuple(v.get("week", ())) == tuple(week) and v.get("flag") is not None}
    todo = [t for t in syms if t not in fresh]
    with ThreadPoolExecutor(max_workers=cfg.infra["apis"]["massive"]["workers"]) as ex:
        for t, flag in zip(todo, ex.map(massive.has_listed_options, todo)):
            fresh[t] = flag
            if flag is not None:
                data[t] = {"flag": flag, "week": list(week)}
    os.makedirs(os.path.dirname(cache), exist_ok=True)
    with open(cache, "w") as f:
        json.dump(data, f, sort_keys=True)
    return {t: fresh.get(t) for t in syms}


def nan_rates(out_dir: str) -> dict[str, float]:
    rates = {}
    for name in ("buffett_quality_input.csv", "magic_formula_input.csv",
                 "piotroski_input.csv", "extended_input.csv"):
        p = os.path.join(out_dir, "fundamental", name)
        if os.path.exists(p):
            df = pd.read_csv(p)
            for c in df.columns:
                if c not in ("ticker", "company", "sector", "industry"):
                    rates[f"{name}:{c}"] = round(float(df[c].isna().mean()), 6)
    return rates


def write_manifest(out_dir: str, session: str, extra: dict | None = None) -> dict:
    files = {}
    for dirpath, _, names in os.walk(out_dir):
        for n in sorted(names):
            if n == "manifest.json":
                continue
            p = os.path.join(dirpath, n)
            rel = os.path.relpath(p, out_dir)
            rows = None
            if n.endswith((".csv", ".csv.gz")):
                rows = int(len(pd.read_csv(p)))
            files[rel] = {"sha256": sha256_file(p), "rows": rows, "bytes": os.path.getsize(p)}
    manifest = {"session": session, "created_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
                "files": dict(sorted(files.items())), "nan_rates": nan_rates(out_dir),
                **(extra or {})}
    with open(os.path.join(out_dir, "manifest.json"), "w") as f:
        json.dump(manifest, f, indent=1, sort_keys=True, default=str)
    return manifest


def load_manifest(out_dir: str) -> dict:
    with open(os.path.join(out_dir, "manifest.json")) as f:
        return json.load(f)


def verify_manifest(out_dir: str) -> list[str]:
    """Files whose checksum no longer matches the manifest (immutability check)."""
    bad = []
    for rel, meta in load_manifest(out_dir)["files"].items():
        p = os.path.join(out_dir, rel)
        if not os.path.exists(p) or sha256_file(p) != meta["sha256"]:
            bad.append(rel)
    return bad
