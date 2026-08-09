#!/usr/bin/env python3
"""DGX Quant Screener entrypoint.

Usage:
  python run_daily.py                          # full pre-market run (freezes snapshot)
  python run_daily.py --dry-run                # run without freezing a snapshot
  python run_daily.py --as-of 2026-08-07       # historical as-of run (point-in-time)
  python run_daily.py --score-outcomes         # only score matured predictions
  python run_daily.py --backtest 2024-01-02 2025-12-31   # full-system walk-forward
  python run_daily.py --max-universe 200       # cap universe size (dev/testing)
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import logging
import sys


def main() -> int:
    ap = argparse.ArgumentParser(description="DGX Quant Screener")
    ap.add_argument("--config", default=None, help="path to config.yaml")
    ap.add_argument("--as-of", default=None, help="run as of date YYYY-MM-DD")
    ap.add_argument("--dry-run", action="store_true", help="do not freeze snapshot")
    ap.add_argument("--score-outcomes", action="store_true",
                    help="only score matured past predictions")
    ap.add_argument("--backtest", nargs=2, metavar=("START", "END"),
                    help="walk-forward system backtest between two dates")
    ap.add_argument("--max-universe", type=int, default=None,
                    help="cap universe size (development)")
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    logging.getLogger("yfinance").setLevel(logging.WARNING)
    logging.getLogger("urllib3").setLevel(logging.WARNING)

    from quant_screener.config import load_config

    cfg = load_config(args.config)
    as_of = dt.date.fromisoformat(args.as_of) if args.as_of else None

    if args.score_outcomes:
        from quant_screener.config import fmp_api_key
        from quant_screener.data.prices import PriceLibrary
        from quant_screener.data.providers import build_providers
        from quant_screener.data.store import Store
        from quant_screener.learning.outcomes import score_matured_predictions

        store = Store(cfg["storage_root"])
        provider, _ = build_providers(cfg, fmp_api_key(cfg))
        lib = PriceLibrary(provider, store, cfg.data.price_cache_days)
        n = score_matured_predictions(store, lib, cfg, as_of or dt.date.today())
        print(f"scored {n} outcomes")
        return 0

    if args.backtest:
        from quant_screener.backtest_system import run_system_backtest

        start, end = (dt.date.fromisoformat(x) for x in args.backtest)
        summary = run_system_backtest(cfg, start, end, max_universe=args.max_universe or 300)
        print(json.dumps(summary, indent=2, default=str))
        return 0

    from quant_screener.pipeline import run_daily

    result = run_daily(cfg, as_of=as_of, dry_run=args.dry_run,
                       max_universe=args.max_universe)
    print(json.dumps(result, indent=2, default=str))
    if result.get("status") == "OK" and result.get("no_opportunity"):
        print("\nNO HIGH-CONFIDENCE OPPORTUNITY TODAY")
    return 0 if result.get("status") in ("OK", "NOT_A_TRADING_DAY") else 1


if __name__ == "__main__":
    sys.exit(main())
