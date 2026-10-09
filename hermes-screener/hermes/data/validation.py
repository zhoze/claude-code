"""Validation gates that run before anything else (spec §2.4).

Any failure aborts the night with a report explaining why (no output beats wrong
output). Checks: schema conformance per file; universe size within ±10% of the
prior snapshot; per-column NaN rate within 2x its trailing 30-snapshot mean; price
staleness; fundamental-input row count >= 800.
"""
from __future__ import annotations

import os

import pandas as pd

from .snapshot import load_manifest, verify_manifest

SCHEMAS = {
    "ta/prices.csv.gz": ["ticker", "date", "open", "high", "low", "close", "volume", "vwap",
                         "adjclose"],
    "ta/earnings.csv": ["ticker", "date", "eps_actual", "eps_est", "rev_actual", "rev_est"],
    "ta/universe.csv": ["ticker", "company", "sector", "industry", "market_cap"],
    "ta/benchmarks.csv.gz": ["symbol", "date", "adjclose"],
    "liquidity.csv": ["ticker", "adv20", "spread_bp", "has_options", "last_bar"],
    "fundamental/buffett_quality_input.csv": ["ticker", "roic_5y_avg", "pe", "ev_ebit"],
    "fundamental/magic_formula_input.csv": ["ticker", "enterprise_value", "ebit"],
    "fundamental/piotroski_input.csv": ["ticker", "price_to_book", "net_income_t"],
    "fundamental/extended_input.csv": ["ticker", "retained_earnings_t"],
}


def prior_snapshots(root: str, session: str, n: int = 30) -> list[str]:
    if not os.path.isdir(root):
        return []
    names = sorted(d for d in os.listdir(root)
                   if d < session and os.path.exists(os.path.join(root, d, "manifest.json")))
    return [os.path.join(root, d) for d in names[-n:]]


def validate_snapshot(cfg, out_dir: str, session: str, expected_universe: int | None = None
                      ) -> tuple[bool, list[str]]:
    v = cfg.infra["validation"]
    errors: list[str] = []

    bad = verify_manifest(out_dir)
    if bad:
        errors.append(f"checksum mismatch vs manifest (snapshot mutated?): {bad[:5]}")

    for rel, cols in SCHEMAS.items():
        p = os.path.join(out_dir, rel)
        if not os.path.exists(p):
            errors.append(f"schema: {rel} missing")
            continue
        have = set(pd.read_csv(p, nrows=0).columns)
        missing = [c for c in cols if c not in have]
        if missing:
            errors.append(f"schema: {rel} missing columns {missing}")

    uni = pd.read_csv(os.path.join(out_dir, "ta", "universe.csv"))
    n_uni = len(uni)
    priors = prior_snapshots(os.path.dirname(out_dir), session, v["nan_trailing_days"])
    if priors:
        prev_rows = load_manifest(priors[-1])["files"].get("ta/universe.csv", {}).get("rows")
        if prev_rows:
            if abs(n_uni / prev_rows - 1) > v["universe_tolerance"]:
                errors.append(f"universe size {n_uni} outside ±{v['universe_tolerance']:.0%} "
                              f"of prior snapshot ({prev_rows})")
        cur = load_manifest(out_dir).get("nan_rates", {})
        hist = pd.DataFrame([load_manifest(p).get("nan_rates", {}) for p in priors])
        for col, rate in cur.items():
            if col in hist.columns:
                base = float(hist[col].mean())
                lim = max(v["nan_rate_multiple"] * base, 0.02)
                if rate > lim:
                    errors.append(f"NaN rate {col} = {rate:.1%} exceeds {lim:.1%} "
                                  f"({v['nan_rate_multiple']}x trailing mean)")

    liq = pd.read_csv(os.path.join(out_dir, "liquidity.csv"))
    stale = liq[liq["last_bar"].astype(str) < session]
    allowed = v["max_price_staleness_days"]
    if allowed == 0 and len(stale):
        frac = len(stale) / max(len(liq), 1)
        # individual stale names are dropped downstream; a broad stale feed aborts
        if frac > 0.05:
            errors.append(f"price staleness: {len(stale)} tickers ({frac:.0%}) have no bar on "
                          f"{session}")

    ff_path = os.path.join(out_dir, "fetch_failures.json")
    if os.path.exists(ff_path):
        import json  # noqa: PLC0415
        with open(ff_path) as f:
            ff = json.load(f)
        requested = load_manifest(out_dir).get("universe_requested") or n_uni or 1
        rate = len(ff.get("prices", [])) / requested
        if rate > v.get("max_fetch_failure_rate", 0.10):
            st = load_manifest(out_dir).get("http_status", {}).get("massive", {})
            errors.append(f"Massive price downloads failed for {len(ff['prices'])} of "
                          f"{requested} names ({rate:.0%}); HTTP status by endpoint: {st} "
                          "— 429 = plan rate limit (set apis.massive.max_rpm), "
                          "403 = endpoint not in plan")
        if ff.get("options"):
            log_opts = len(ff["options"])
            if log_opts / requested > v.get("max_fetch_failure_rate", 0.10):
                errors.append(f"options-tradability check failed for {log_opts} names; "
                              "see http_status in manifest.json")

    min_rows = v["min_fundamental_rows"]
    if expected_universe is not None:
        min_rows = min(min_rows, int(0.8 * expected_universe))
    n_fund = len(pd.read_csv(os.path.join(out_dir, "fundamental",
                                          "buffett_quality_input.csv")))
    if n_fund < min_rows:
        errors.append(f"fundamental input rows {n_fund} < {min_rows}")
    return (not errors), errors
