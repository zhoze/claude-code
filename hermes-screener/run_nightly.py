#!/usr/bin/env python3
"""Hermes stock-screening agent — CLI for the orchestrator (spec §10 call order).

  FMP_KEY=... MASSIVE_KEY=... python3 run_nightly.py                 # full nightly pass
  python3 run_nightly.py --limit 60                                  # smoke run, 60 names
  python3 run_nightly.py --tickers AAPL,MSFT,...                     # explicit universe
  python3 run_nightly.py --session 2026-10-08 --no-build             # reuse a snapshot
  python3 run_nightly.py --pass premarket --macro macro.json        # latest nightly
  python3 run_nightly.py --demo                                      # offline synthetic data

Keys come from the environment only. Output: reports/<session>/top3.json + report.md.
Decision-support research tooling — not investment advice; no order execution exists.
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from hermes.config import load_config  # noqa: E402
from hermes.orchestrator import Orchestrator  # noqa: E402


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--pass", dest="pass_", choices=["nightly", "premarket"], default="nightly")
    ap.add_argument("--session", help="session date YYYY-MM-DD (default: last US session)")
    ap.add_argument("--tickers", help="comma-separated explicit universe")
    ap.add_argument("--limit", type=int, help="only the first N discovered names (smoke)")
    ap.add_argument("--no-build", action="store_true", help="use an existing snapshot")
    ap.add_argument("--macro", help="macro-agent JSON (spec §5 schema); optional")
    ap.add_argument("--strategy", help="alternate strategy.yaml (research only)")
    ap.add_argument("--root", help="output root for data/db/reports (default: this dir)")
    ap.add_argument("--massive-rpm", type=float,
                    help="Massive requests/minute cap (plan limit; overrides infra.yaml)")
    ap.add_argument("--history-years", type=float,
                    help="price history depth for names and benchmarks (e.g. 1); also scales "
                         "the TA panel's minimum bar count")
    ap.add_argument("--history-source", choices=["auto", "flatfiles", "grouped", "rest"],
                    help="Massive price-history source (overrides infra.yaml)")
    ap.add_argument("--no-options-check", action="store_true",
                    help="skip the listed-options universe filter (saves ~1 call per name)")
    ap.add_argument("--demo", action="store_true", help="offline synthetic data (no keys)")
    ap.add_argument("-v", "--verbose", action="store_true")
    a = ap.parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if a.verbose else logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")

    over: dict = {"infra": {"apis": {"massive": {}}, "history": {}}, "strategy": {}}
    if a.massive_rpm:
        over["infra"]["apis"]["massive"]["max_rpm"] = a.massive_rpm
    if a.history_source:
        over["infra"]["apis"]["massive"]["history_source"] = a.history_source
    if a.history_years:
        y = a.history_years - 31 / 365.25   # years_ago() adds a 30-day margin; keep the cap exact
        over["infra"]["history"].update({
            "ta_years": y, "market_years": y + 0.1,
            "min_price_bars": int(min(400, 200 * a.history_years))})
    if a.no_options_check:
        over["strategy"]["universe"] = {"require_options": False}
    cfg = load_config(strategy_path=a.strategy, root=a.root, overrides=over)
    if a.pass_ == "premarket":
        session = a.session
        if not session:   # latest nightly pass with saved state
            art = cfg.path("artifacts")
            done = sorted(d for d in (os.listdir(art) if os.path.isdir(art) else [])
                          if os.path.exists(os.path.join(art, d, "state.json")))
            if not done:
                ap.error("no nightly state found; run the nightly pass first")
            session = done[-1]
        out = Orchestrator(cfg).run_premarket(session, a.macro)
    else:
        if a.demo:
            sys.path.insert(0, os.path.join(HERE, "tests"))
            from fakes import FakeFMP, FakeMarket, FakeMassive  # noqa: PLC0415
            m = FakeMarket(n=a.limit or 120, session=a.session or "2026-10-08")
            fmp, massive = FakeFMP(m), FakeMassive(m)
            session = m.session
        else:
            fmp = massive = None
            if not a.no_build or not a.session:
                from hermes.data.fmp import FMPClient  # noqa: PLC0415
                from hermes.data.massive import MassiveClient  # noqa: PLC0415
                api = cfg.infra["apis"]
                fmp = FMPClient(api["fmp"]["base_url"], api["fmp"]["key_env"],
                                api["retries"], api["timeout_s"],
                                max_rpm=api["fmp"].get("max_rpm"))
                massive = MassiveClient(api["massive"]["base_url"], api["massive"]["key_env"],
                                        api["retries"], api["timeout_s"],
                                        max_rpm=api["massive"].get("max_rpm"))
            session = a.session
        tickers = [t.strip().upper() for t in a.tickers.split(",")] if a.tickers else None
        out = Orchestrator(cfg, fmp, massive).run_nightly(
            session=session, build=not a.no_build, tickers=tickers,
            limit=len(m.tickers) if a.demo else a.limit, macro_path=a.macro)
    t3 = out["top3"]
    print(json.dumps({"status": out["status"], "funnel": t3.get("funnel_counts"),
                      "top3": [(e["ticker"], e["final_weight"]) for e in t3.get("top3", [])],
                      "reports": out.get("paths"), "reason": out.get("reason")}, indent=1))
    return 0 if out["status"] != "aborted" else 1


if __name__ == "__main__":
    raise SystemExit(main())
