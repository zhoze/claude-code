"""Daily execution workflow orchestrator (spec §48, steps 1-30).

`run_daily()` executes the full pre-market pipeline and freezes the prediction
snapshot before the open. Every stage degrades gracefully: missing data lowers
confidence or rejects candidates — it is never invented.
"""

from __future__ import annotations

import datetime as dt
import logging
import math

import numpy as np
import pandas as pd

from . import MODEL_VERSION, gpu
from .calendar_utils import is_trading_day, market_open, now_ny
from .config import Config, fmp_api_key
from .data.events import assess_events
from .data.fundamentals import build_fundamental_record
from .data.integrity import Provenance
from .data.macro import build_macro_snapshot
from .data.options import assess_options
from .data.prices import SECTOR_ETFS, PriceLibrary
from .data.providers import build_providers
from .data.store import Store
from .data.universe import apply_liquidity_filters, build_universe
from .learning.outcomes import score_matured_predictions, system_performance
from .learning.snapshot import freeze_snapshot
from .learning.weights import maybe_promote_challenger, update_screen_performance
from .ml.models import predict_finalist
from .portfolio.cvar import efficient_frontier
from .portfolio.scenarios import blended_expected_returns, joint_return_matrix, scenario_matrix
from .portfolio.stress import portfolio_metrics, stress_test
from .regime.detector import detect_regime
from .regime.macro_sensitivity import estimate_sensitivity
from .report.daily_report import render_report
from .scoring.confidence import compute_confidence
from .scoring.final_score import CandidateScores, compute_final_score, gate_candidate
from .screens.ensemble import build_ensemble
from .screens.fundamental import run_all_screens
from .technicals.evaluate import evaluate_finalist

log = logging.getLogger("quant_screener.pipeline")


def run_daily(cfg: Config, as_of: dt.date | None = None, dry_run: bool = False,
              max_universe: int | None = None) -> dict:
    run_ts = now_ny()
    as_of = as_of or run_ts.date()
    log.info("=== DGX Quant Screener %s run for %s (%s) ===",
             MODEL_VERSION, as_of, gpu.backend_summary())

    # STEP 1 — market calendar
    if not is_trading_day(as_of):
        log.info("%s is not a US trading day; exiting", as_of)
        return {"status": "NOT_A_TRADING_DAY", "date": as_of.isoformat()}

    store = Store(cfg["storage_root"])
    api_key = fmp_api_key(cfg)
    price_provider, fund_provider = build_providers(cfg, api_key)
    price_lib = PriceLibrary(price_provider, store, cfg.data.price_cache_days)
    provenance: list[Provenance] = []

    # STEP 30 (runs first each morning) — score matured past predictions
    try:
        score_matured_predictions(store, price_lib, cfg, as_of)
        update_screen_performance(store, as_of)
    except Exception as e:
        log.warning("outcome scoring failed: %s", e)

    # STEP 2 — point-in-time universe
    universe = build_universe(cfg, as_of)
    provenance.append(universe.provenance)
    if max_universe:
        universe.tickers = universe.tickers[:max_universe]
    log.info("universe: %d tickers (%s)", len(universe.tickers),
             "SURVIVORSHIP-BIASED SNAPSHOT" if universe.survivorship_biased else "point-in-time")
    if not universe.tickers:
        return {"status": "NO_UNIVERSE", "date": as_of.isoformat()}

    # STEP 7 (early: prices gate liquidity) — price/factor histories
    prices = price_lib.get_many(universe.tickers, as_of)
    universe = apply_liquidity_filters(universe, prices, cfg)
    log.info("after liquidity filters: %d tickers", len(universe.tickers))

    # STEP 3-4 — fundamentals + 10 screens
    records = {}
    for t in universe.tickers:
        try:
            raw = fund_provider.fundamentals(t)
            px = prices.get(t)
            price = float(px["close"].iloc[-1]) if px is not None and len(px) else None
            mcap = None
            rec = build_fundamental_record(t, raw, as_of, mcap, price)
            if not math.isfinite(rec.get("market_cap")) and price and math.isfinite(rec.get("shares_outstanding")):
                rec.metrics["market_cap"] = price * rec.get("shares_outstanding")
                nd = rec.get("net_debt")
                if math.isfinite(nd):
                    rec.metrics["enterprise_value"] = rec.metrics["market_cap"] + nd
            records[t] = rec
        except Exception as e:
            log.debug("fundamentals failed for %s: %s", t, e)
    log.info("fundamental records built: %d", len(records))
    if not records:
        return {"status": "NO_FUNDAMENTAL_DATA", "date": as_of.isoformat()}

    screen_results = run_all_screens(records)

    # STEP 5-6 — overlap + finalists (adaptive weights, champion/challenger governed)
    active_weights = maybe_promote_challenger(store, cfg)
    sectors = {t: r.sector or "UNKNOWN" for t, r in records.items()}
    ensemble = build_ensemble(screen_results, cfg, active_weights, sectors)
    finalists = ensemble.finalists
    log.info("fundamental finalists: %s", finalists)

    # STEP 9 + 15-21 — macro snapshot & regime
    macro = build_macro_snapshot(price_lib, as_of, api_key)
    regime = detect_regime(macro.series, as_of)
    log.info("market regime: %s (score %.0f)", regime.regime, regime.score)

    bench_close = macro.series.get("sp500")
    sector_closes = {}
    for sec, etf in SECTOR_ETFS.items():
        try:
            spx = price_lib.get(etf, as_of)
            sector_closes[sec] = spx["close"] if len(spx) else None
        except Exception:
            sector_closes[sec] = None

    candidates: list[dict] = []
    finalist_prices = {t: prices.get(t) for t in finalists if prices.get(t) is not None}

    # STEP 8 — cuML predictions | STEP 11-14 — technicals | STEP 22-23 — events/options
    ml_expected_5d, fundamental_scores, tech_scores = {}, {}, {}
    per_ticker: dict[str, dict] = {}
    for t in finalists:
        px = finalist_prices.get(t)
        rec = records[t]
        row = ensemble.table.loc[t]
        sec_close = sector_closes.get(rec.sector)

        ml = predict_finalist(t, px, cfg, bench_close, sec_close, macro.series) \
            if px is not None and len(px) else None
        tech = evaluate_finalist(t, px, cfg, bench_close, sec_close)
        sens = estimate_sensitivity(t, px, macro.series, sec_close) if px is not None else None
        events = assess_events(t, fund_provider.calendar_events(t)
                               if hasattr(fund_provider, "calendar_events") else {},
                               as_of, cfg.scoring.penalties.event_risk_max)
        opts = assess_options(t, price_provider.options_chain(t),
                              tech.levels.get("price") if tech.levels else None, cfg)

        per_ticker[t] = {"ml": ml, "tech": tech, "sens": sens, "events": events,
                         "opts": opts, "row": row, "rec": rec}
        if ml and 5 in ml.expected_return:
            ml_expected_5d[t] = ml.expected_return[5]
        fundamental_scores[t] = float(row["composite"]) if math.isfinite(row["composite"]) else None
        tech_scores[t] = tech.technical_score

    # STEP 10 — cuOpt Mean-CVaR over finalists
    portfolio_ctx = None
    frontier = None
    hist_matrix = joint_return_matrix(finalist_prices, cfg.portfolio.scenario_days)
    optimizable = [t for t in finalists if t in hist_matrix.columns]
    if len(optimizable) >= 2:
        expected = blended_expected_returns(
            optimizable, 21, hist_matrix,
            {t: fundamental_scores.get(t) for t in optimizable},
            {t: ml_expected_5d.get(t, np.nan) * 4 if t in ml_expected_5d else np.nan
             for t in optimizable},
            {t: tech_scores.get(t) for t in optimizable},
            regime.tilt, dict(cfg.portfolio.scenario_blend))
        scen = scenario_matrix(hist_matrix[optimizable], expected[optimizable])
        frontier = efficient_frontier(scen, optimizable, expected, cfg,
                                      {t: sectors.get(t) for t in optimizable})
        if frontier.solutions:
            bal = frontier.balanced
            stress_df = stress_test(bal.weights[bal.weights > 0.001], prices)
            port_rets = (hist_matrix[optimizable] * bal.weights).sum(axis=1)
            portfolio_ctx = {
                "solver": bal.solver, "lam": bal.lam, "alpha": bal.alpha,
                "cash": bal.cash, "expected_return": bal.expected_return,
                "cvar": bal.cvar,
                "positions": bal.contributions.reset_index().to_dict("records"),
                "stress_metrics": portfolio_metrics(port_rets),
                "stress_table": stress_df.to_dict("records") if len(stress_df) else [],
            }
            log.info("Mean-CVaR balanced portfolio (%s): %s + cash %.0f%%",
                     bal.solver,
                     {k: round(v, 3) for k, v in bal.weights[bal.weights > 0.001].items()},
                     bal.cash * 100)

    # STEP 24-26 — final scores + confidence + gates
    scoring_w = dict(cfg.scoring.weights)
    for t in finalists:
        d = per_ticker[t]
        ml, tech, sens, events, opts = d["ml"], d["tech"], d["sens"], d["events"], d["opts"]
        row, rec = d["row"], d["rec"]
        px = finalist_prices.get(t)

        cvar95 = float(np.nan)
        w_sugg = 0.0
        if frontier and frontier.solutions and t in frontier.balanced.weights.index:
            w_sugg = float(frontier.balanced.weights[t])
        if px is not None and len(px) > 100:
            r = px["close"].pct_change().dropna()
            tail = r[r <= r.quantile(1 - cfg.portfolio.cvar_alpha)]
            cvar95 = float(tail.mean()) if len(tail) else np.nan

        s = CandidateScores(ticker=t)
        s.fundamental = float(row["composite"])
        s.overlap = float(row["overlap"]) * 10
        if ml and ml.accepted and 5 in ml.expected_return:
            s.ml_expected_return = float(np.clip(50 + ml.expected_return[5] * 2500, 0, 100))
        s.mean_cvar = float(np.clip(50 + (w_sugg - 0.15) * 200, 0, 100)) if w_sugg else math.nan
        s.technical = tech.technical_score
        s.technical_robustness = tech.robustness
        s.macro = sens.macro_score if sens else math.nan
        s.options = float(np.clip(50 + opts.sentiment_score / 2, 0, 100)) if opts.has_options else math.nan
        px_adv = float((px["close"] * px["volume"]).tail(20).mean()) if px is not None and len(px) else 0
        s.liquidity = float(np.clip(np.log10(max(px_adv, 1)) * 12 - 40, 0, 100))
        s.catalyst = events.catalyst_score
        s.event_risk_penalty = events.event_risk_penalty
        if math.isfinite(cvar95):
            s.tail_risk_penalty = float(np.clip((-cvar95 - 0.03) * 300, 0,
                                                cfg.scoring.penalties.tail_risk_max))
        if ml:
            s.model_uncertainty_penalty = float(np.clip(
                (100 - ml.model_confidence) / 10, 0,
                cfg.scoring.penalties.model_uncertainty_max))
        compute_final_score(s, scoring_w)

        # -------- independent confidence (spec §38)
        completeness = float(np.mean([
            row["data_confidence"],
            1.0 if rec.has_filing_dates else 0.5,
            1.0 if (ml and ml.accepted) else 0.3,
            1.0 if opts.has_options else 0.0,
        ]))
        direction_votes = [
            1 if row["overlap"] >= cfg.screens.min_overlap else 0,
            1 if (ml and ml.probability_positive.get(5, 0.5) > 0.55) else 0,
            1 if tech.signal in ("STRONG_BUY", "BUY") else 0,
            1 if (sens and sens.assessment != "NEGATIVE") else 0,
            1 if opts.sentiment_score > 0 else 0,
        ]
        agreement = sum(direction_votes) / len(direction_votes)
        perf_hist = system_performance(store)
        reliability = perf_hist.get("last_50", perf_hist.get("all", {})).get("WIN_RATE", 0.5) \
            if perf_hist else 0.5
        uncertainty = 1 - (ml.model_confidence / 100 if ml else 0.0)
        confidence = compute_confidence(
            data_completeness=completeness, model_agreement=agreement,
            historical_reliability=reliability,
            regime_similarity=0.7 if regime.regime in ("RISK_ON", "NEUTRAL") else 0.4,
            model_uncertainty=uncertainty,
            options_liquidity=opts.liquidity_score / 100,
            fundamental_overlap=int(row["overlap"]),
            technical_confirmed=tech.signal in ("STRONG_BUY", "BUY"),
            macro_compatible=not (sens and sens.assessment == "NEGATIVE"))

        # -------- hard minimum requirements (spec §39)
        checks = {
            "in_universe": True,
            "has_listed_options": bool(opts.has_options),
            "options_usable": opts.usable,
            "liquidity_ok": s.liquidity >= 40,
            "fundamental_strong": s.fundamental >= 55 if math.isfinite(s.fundamental) else False,
            "overlap_ok": row["overlap"] >= cfg.screens.min_overlap,
            "cvar_ok": (not math.isfinite(cvar95)) is False and cvar95 > -0.06,
            "ml_ok": bool(ml and ml.accepted and ml.probability_positive.get(5, 0) > 0.5),
            "technical_validated": tech.robustness >= 45,
            "technical_signal_now": tech.signal in ("STRONG_BUY", "BUY"),
            "macro_ok": not (sens and sens.assessment == "NEGATIVE"
                             and regime.regime in ("RISK_OFF", "HIGH_STRESS")),
            "event_risk_ok": events.event_risk_penalty < cfg.scoring.penalties.event_risk_max,
        }
        passes_gate = gate_candidate(s, checks)

        why = []
        if row["overlap"] >= 6:
            why.append(f"high fundamental-screen overlap ({int(row['overlap'])}/10)")
        elif row["overlap"] >= cfg.screens.min_overlap:
            why.append(f"solid fundamental-screen overlap ({int(row['overlap'])}/10)")
        strongest = sorted(
            [(n, row.get(f"{n}_pct")) for n in screen_results],
            key=lambda x: -(x[1] if isinstance(x[1], float) and math.isfinite(x[1]) else -1))[:2]
        why.append("strongest screens: " + ", ".join(n for n, _ in strongest))
        if ml and ml.accepted:
            why.append(f"walk-forward-accepted ML expects "
                       f"{ml.expected_return.get(5, 0) * 100:+.1f}% (5D)")
        if w_sugg > 0.05:
            why.append(f"favorable Mean-CVaR contribution (suggested weight {w_sugg:.0%})")
        if tech.signal in ("STRONG_BUY", "BUY"):
            why.append(f"valid {tech.signal} from {tech.best_strategy} "
                       f"(robustness {tech.robustness:.0f}/100)")

        cand = {
            "TICKER": t,
            "COMPANY": (d["rec"].metrics.get("companyName") or ""),
            "SECTOR": rec.sector,
            "FUNDAMENTAL_OVERLAP": int(row["overlap"]),
            "OVERLAP_TIER": row["overlap_tier"],
            "FUNDAMENTAL_SCORE": s.fundamental,
            "SCREEN_PASSES": {n: bool(row[f"{n}_pass"]) for n in screen_results},
            "SCREEN_PERCENTILES": {n: (float(row[f"{n}_pct"])
                                       if math.isfinite(row[f"{n}_pct"]) else None)
                                   for n in screen_results},
            "ML_EXPECTED_RETURN_5D": ml.expected_return.get(5) if ml else None,
            "ML_EXPECTED_RETURN_20D": ml.expected_return.get(20) if ml else None,
            "PROBABILITY_POSITIVE_5D": ml.probability_positive.get(5) if ml else None,
            "ML_ACCEPTED": bool(ml and ml.accepted),
            "ML_NOTES": ml.notes if ml else ["no ML evaluation"],
            "CVAR_95": cvar95 if math.isfinite(cvar95) else None,
            "MEAN_CVAR_WEIGHT": w_sugg,
            "BEST_TECHNICAL_MODEL": tech.best_strategy,
            "TECHNICAL_ROBUSTNESS": tech.robustness,
            "TECHNICAL_SIGNAL": tech.signal,
            "TECHNICAL_PER_STRATEGY": tech.per_strategy,
            "LEVELS": tech.levels,
            "CURRENT_PRICE": tech.levels.get("price"),
            "MACRO_ASSESSMENT": sens.assessment if sens else "UNKNOWN",
            "MACRO_BETAS": sens.betas if sens else {},
            "OPTIONS_LIQUIDITY": opts.liquidity_score,
            "OPTIONS_SENTIMENT": opts.sentiment_score,
            "OPTIONS_METRICS": opts.metrics,
            "NEXT_EARNINGS": events.next_earnings.isoformat() if events.next_earnings else None,
            "EVENT_FLAGS": events.flags,
            "FINAL_SCORE": s.final_score,
            "CONFIDENCE": confidence,
            "GATE_FAILURES": s.gate_failures,
            "PASSES_GATE": passes_gate,
            "WHY_SELECTED": why,
            "BIGGEST_RISK": (f"earnings on {events.next_earnings}" if events.days_to_earnings
                             and events.days_to_earnings <= 14 else
                             f"tail risk: daily CVaR95 {cvar95:.1%}" if math.isfinite(cvar95)
                             else "incomplete risk data"),
            "INVALIDATION_THESIS": (
                f"close below {tech.levels.get('invalidation')} "
                f"(technical invalidation), loss of {tech.best_strategy} signal, "
                f"or fundamental overlap dropping below {cfg.screens.min_overlap}/10"),
        }
        candidates.append(cand)

    # STEP 27 — select max 3; never force
    selectable = [c for c in candidates if c["PASSES_GATE"]
                  and c["FINAL_SCORE"] >= cfg.scoring.min_final_score
                  and c["CONFIDENCE"] >= cfg.scoring.min_confidence]
    if regime.regime == "HIGH_STRESS":
        log.info("HIGH_STRESS regime — selections suppressed")
        selectable = []
    selectable.sort(key=lambda c: (-c["FINAL_SCORE"], -c["CONFIDENCE"]))
    selections = selectable[: cfg.scoring.max_selections]
    sel_tickers = [c["TICKER"] for c in selections]
    log.info("selections: %s", sel_tickers or "NO HIGH-CONFIDENCE OPPORTUNITY TODAY")

    # ---- report context (STEP 28)
    lb = []
    perf_df = store.screen_performance("fundamental")
    if len(perf_df):
        latest = perf_df.sort_values("run_date").groupby("screen_name").last().reset_index()
        for _, r in latest.sort_values("alpha", ascending=False, na_position="last").iterrows():
            lb.append({"screen": r["screen_name"], "alpha": r["alpha"],
                       "information_coefficient": r["information_coefficient"],
                       "hit_rate": r["hit_rate"], "n_obs": r["n_obs"],
                       "weight": (active_weights or {}).get(r["screen_name"])})
    screen_rows = []
    top = ensemble.table.sort_values(["overlap", "rank_score"], ascending=False).head(15)
    for t, row in top.iterrows():
        strongest = sorted(
            [(n, row.get(f"{n}_pct")) for n in screen_results],
            key=lambda x: -(x[1] if isinstance(x[1], float) and math.isfinite(x[1]) else -1))[:3]
        screen_rows.append({"ticker": t, "company": "", "sector": sectors.get(t, ""),
                            "overlap": int(row["overlap"]),
                            "composite": row["composite"],
                            "strongest": [n for n, _ in strongest]})
    report_ctx = {
        "date": as_of.isoformat(), "run_time": run_ts.isoformat(),
        "market_open": market_open(as_of).isoformat(),
        "data_cutoff": run_ts.isoformat(), "model_version": MODEL_VERSION,
        "backend": gpu.backend_summary(),
        "regime": {"regime": regime.regime, "score": regime.score,
                   "sub_regimes": regime.sub_regimes, "notes": regime.notes},
        "dashboard": macro.dashboard, "global_markets": macro.global_markets,
        "calendar": macro.calendar, "screen_results": screen_rows,
        "finalists": candidates, "selections": selections,
        "portfolio": portfolio_ctx, "system_performance": system_performance(store),
        "screen_leaderboard": lb,
        "changelog": store.changelog().to_dict("records"),
        "provenance": [p.as_dict() for p in provenance] + [{"DATA_SOURCE": "macro/yfinance",
                                                            "NOTES": macro.notes}],
    }
    report_md = render_report(report_ctx)
    report_path = store.report_dir / f"{as_of.isoformat()}.md"
    report_path.write_text(report_md)
    log.info("report written: %s", report_path)

    # STEP 29 — freeze snapshot BEFORE open (immutable)
    snapshot_path = None
    if not dry_run:
        try:
            snapshot_path = freeze_snapshot(
                store, as_of, run_ts, candidates, sel_tickers,
                {"regime": regime.regime, "regime_score": regime.score,
                 "dashboard": {k: v.get("value") for k, v in macro.dashboard.items()}})
            log.info("snapshot frozen: %s", snapshot_path)
        except FileExistsError as e:
            log.warning("%s", e)

    store.close()
    return {
        "status": "OK",
        "date": as_of.isoformat(),
        "regime": regime.regime,
        "finalists": finalists,
        "selections": sel_tickers,
        "no_opportunity": not sel_tickers,
        "report_path": str(report_path),
        "snapshot_path": str(snapshot_path) if snapshot_path else None,
    }
