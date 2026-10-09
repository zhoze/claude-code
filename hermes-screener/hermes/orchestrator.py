"""The orchestrator: production is a fixed call order (invariant I3, spec §10).

nightly:   build_snapshot -> validate -> screen_universe -> run_fundamental_screens
           -> run_risk_layer -> generate_scenarios -> optimize_portfolio (weekly or
           event-driven re-opt, else the previous book is refreshed)
           -> run_technical_screens -> macro (stub) -> resolve_final
           -> learning_log -> report
premarket: reload the nightly artifacts -> re-read the macro JSON -> re-check the
           gates -> resolve_final -> report
Any stage crash produces no Top 3 that day, never a partially computed one (§10).
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import logging
import os
import traceback

import numpy as np
import pandas as pd

from . import vendored
from .config import Config
from .data.snapshot import build_snapshot, last_session, snapshot_dir
from .data.validation import validate_snapshot
from .envelope import RunContext
from .learning.db import LearningDB
from .report.render import write_report
from .stages.blend import run_blend
from .stages.fundamental import run_fundamental_screens
from .stages.macro import load_macro
from .stages.optimizer import Constraints, SolverPolicy, optimize_portfolio, portfolio_risk
from .stages.resolve import resolve_final
from .stages.risk import run_risk_layer
from .stages.scenarios import generate_scenarios
from .stages.technical import gate, run_ta_registry, ta_config
from .stages.universe import screen_universe

log = logging.getLogger(__name__)


class StageError(RuntimeError):
    pass


# ------------------------------------------------------------------ audit log (R5)

class AuditLog:
    """Append-only, hash-chained JSONL of every tool call (args/response hashed)."""

    def __init__(self, path: str):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        self.path = path
        self.prev = "0" * 64
        if os.path.exists(path):
            with open(path) as f:
                for line in f:
                    if line.strip():
                        self.prev = json.loads(line)["hash"]

    @staticmethod
    def _h(obj) -> str:
        return hashlib.sha256(json.dumps(obj, sort_keys=True, default=str).encode()).hexdigest()

    def record(self, env, args: dict) -> None:
        rec = {"ts": dt.datetime.now(dt.timezone.utc).isoformat(), "run_id": env.run_id,
               "tool": env.tool, "args_hash": self._h(args),
               "response_hash": self._h(env.payload), "warnings": env.warnings,
               "timings_ms": env.timings_ms, "prev": self.prev}
        rec["hash"] = self._h(rec)
        with open(self.path, "a") as f:
            f.write(json.dumps(rec, default=str) + "\n")
        self.prev = rec["hash"]


# ------------------------------------------------------------------ helpers

def _artifacts(cfg, session: str) -> str:
    p = os.path.join(cfg.path("artifacts"), session)
    os.makedirs(p, exist_ok=True)
    return p


def _prior_state(cfg, session: str) -> dict | None:
    root = cfg.path("artifacts")
    if not os.path.isdir(root):
        return None
    for d in sorted((d for d in os.listdir(root) if d < session), reverse=True):
        p = os.path.join(root, d, "state.json")
        if os.path.exists(p):
            with open(p) as f:
                return json.load(f)
    return None


def next_run_seq(cfg, session: str, pass_: str) -> int:
    """1 + the number of runs already logged for this session and pass (unique run_id)."""
    if not os.path.exists(cfg.path("db")):
        return 1
    db = LearningDB(cfg.path("db"))
    try:
        n = db.con.execute("SELECT count(*) FROM runs WHERE date = ? AND pass = ?",
                           [session, pass_]).fetchone()[0]
    finally:
        db.close()
    return int(n) + 1


def _next_session(session: str) -> str:
    return str(np.busday_offset(np.datetime64(session), 1, roll="forward"))


def needs_reoptimization(session: str, prev: dict | None, regime: str, carried: list[str]
                         ) -> tuple[bool, str]:
    """Weekly (first session of the week) or event-driven re-optimization (§6.3)."""
    if not prev or not prev.get("book"):
        return True, "no previous book"
    d, pd_ = dt.date.fromisoformat(session), dt.date.fromisoformat(prev["session"])
    if d.isocalendar()[:2] != pd_.isocalendar()[:2]:
        return True, "first session of the week"
    if prev.get("regime") and prev["regime"] != regime:
        return True, f"regime flip {prev['regime']} -> {regime}"
    old = set(prev.get("carried", []))
    if old:
        turn = 1 - len(old & set(carried)) / len(old)
        if turn > 0.30:
            return True, f"candidate-set turnover {turn:.0%} > 30%"
    gone = [t for t in prev["book"] if t not in carried]
    if gone:
        return True, f"book names no longer carried: {gone}"
    return False, "daily refresh (book kept)"


def base_constraints(cfg, names, sectors, clusters, liquidity, prev_book) -> Constraints:
    oc = cfg.strategy["optimizer"]
    liq_cap = {}
    acct = oc.get("account_value_usd")
    if acct:
        for t in names:
            adv = (liquidity.get(t) or {}).get("adv20")
            if adv and np.isfinite(adv):
                liq_cap[t] = min(oc["w_max"], oc["adv_participation"] * adv / float(acct))
    return Constraints(alpha=oc["alpha"], w_max=oc["w_max"], w_min=oc["w_min"],
                       cardinality=tuple(oc["cardinality"]), sector_of=sectors,
                       sector_cap=oc["sector_cap"], cluster_of=clusters,
                       cluster_cap=oc["cluster_cap"], prev_weights=prev_book or {},
                       turnover_cap=oc["turnover_cap"], liquidity_cap=liq_cap)


# ------------------------------------------------------------------ the nightly pass

class Orchestrator:
    def __init__(self, cfg: Config, fmp=None, massive=None):
        self.cfg = cfg
        self.fmp = fmp
        self.massive = massive

    def _tool(self, ctx, audit, name, fn, args=None):
        with ctx.tool(name) as env:
            out = fn()
            env.payload = {k: v for k, v in out.items()
                           if isinstance(v, (str, int, float, bool, list, dict, type(None)))
                           and k not in ("R",)} if isinstance(out, dict) else out
            env.warnings = list(out.get("warnings", [])) if isinstance(out, dict) else []
        audit.record(env, args or {})
        return out, env

    def run_nightly(self, session: str | None = None, build: bool = True,
                    tickers: list[str] | None = None, limit: int | None = None,
                    macro_path: str | None = None, run_seq: int | None = None) -> dict:
        cfg = self.cfg
        session = session or last_session(self.massive)
        seed = cfg.seed_for(session)
        run_seq = run_seq or next_run_seq(cfg, session, "nightly")
        run_id = f"{session}-nightly-{run_seq:03d}"
        ctx = RunContext(run_id, session, seed, cfg.strategy_version)
        audit = AuditLog(os.path.join(cfg.path("audit"), "tool_calls.jsonl"))
        snap = snapshot_dir(cfg, session)
        warnings: list[str] = []
        try:
            return self._nightly(ctx, audit, snap, session, seed, build, tickers, limit,
                                 macro_path, warnings)
        except Exception as e:  # noqa: BLE001 — any crash: no Top 3 today (§10)
            log.error("nightly run failed: %s", traceback.format_exc())
            return self._abort(ctx, session, f"stage crash: {type(e).__name__}: {e}", warnings)

    def _abort(self, ctx, session, reason, warnings, counts=None) -> dict:
        top3 = {"run_id": ctx.run_id, "session": session, "as_of": dt.datetime.now(
            dt.timezone.utc).isoformat(), "strategy_version": ctx.strategy_version,
            "data_snapshot": ctx.snapshot, "seed": ctx.seed, "status": "aborted",
            "status_reason": reason, "funnel_counts": counts or {}, "regime": {},
            "exposure_gate": None, "top3": [], "not_triggered": [], "warnings": warnings}
        paths = write_report(self.cfg.path("reports"), session, top3, None)
        try:
            db = LearningDB(self.cfg.path("db"))
            db.log_run({"run_id": ctx.run_id, "date": session, "pass": "nightly",
                        "strategy_version": ctx.strategy_version,
                        "strategy_hash": self.cfg.strategy_hash,
                        "engine_version": ctx.engine_version, "data_snapshot": session,
                        "seed": ctx.seed, "warnings": warnings + [reason], "status": "aborted",
                        "funnel_counts": counts or {}})
            db.close()
        except Exception:  # noqa: BLE001
            log.exception("could not log the aborted run")
        return {"status": "aborted", "reason": reason, "paths": paths, "top3": top3}

    def _nightly(self, ctx, audit, snap, session, seed, build, tickers, limit, macro_path,
                 warnings) -> dict:
        cfg = self.cfg
        # 1. snapshot + validation gates
        if build and not os.path.exists(os.path.join(snap, "manifest.json")):
            self._tool(ctx, audit, "build_snapshot",
                       lambda: build_snapshot(cfg, self.fmp, self.massive, session,
                                              tickers=tickers, limit=limit),
                       {"session": session, "tickers": tickers, "limit": limit})
        if not os.path.exists(os.path.join(snap, "manifest.json")):
            return self._abort(ctx, session, f"snapshot {session} missing", warnings)
        ok, errs = validate_snapshot(cfg, snap, session,
                                     expected_universe=len(tickers) if tickers else limit)
        if not ok:
            return self._abort(ctx, session, "validation failed: " + "; ".join(errs), warnings)

        tcfg = ta_config(cfg)
        panel = vendored.ta().panel.load_panel(os.path.join(snap, "ta"), tcfg)
        market = pd.read_csv(os.path.join(snap, "market.csv.gz"))
        returns = panel.returns
        sectors = {t: (s if isinstance(s, str) else None)
                   for t, s in panel.meta["sector"].items()}

        # 2. universe
        uni, _ = self._tool(ctx, audit, "screen_universe",
                            lambda: screen_universe(cfg, snap, session))
        universe = [t for t in uni["universe"] if t in panel.close.columns]
        counts = {"universe": len(universe)}

        # 3. fundamental screens + family consensus
        fund, env = self._tool(ctx, audit, "run_fundamental_screens",
                               lambda: run_fundamental_screens(
                                   cfg, os.path.join(snap, "fundamental"), universe))
        warnings += fund["warnings"]
        art = _artifacts(cfg, session)
        fund["consensus"].to_parquet(os.path.join(art, "fund_consensus.parquet"), index=False)
        counts["fund_candidates"] = len(fund["candidates"])
        if len(fund["candidates"]) < 10:
            return self._abort(ctx, session, "fewer than 10 fundamental candidates", warnings,
                               counts)

        # 4. risk layer
        risk, _ = self._tool(ctx, audit, "run_risk_layer",
                             lambda: run_risk_layer(cfg, fund["candidates"], fund["consensus"],
                                                    returns, market, session))
        counts["risk_carried"] = len(risk["carried"])

        # 5–6. scenarios + optimizer (weekly / event-driven re-optimization)
        prev = _prior_state(cfg, session)
        reopt, why = needs_reoptimization(session, prev, risk["regime"]["label"],
                                          risk["carried"])
        names = list(risk["carried"])
        if not reopt:
            names += [t for t in prev["book"] if t not in names]
        sc, env = self._tool(ctx, audit, "generate_scenarios",
                             lambda: generate_scenarios(cfg, names, returns, market, sectors,
                                                        panel.earnings, session, seed),
                             {"names": names, "seed": seed})
        warnings += sc["warnings"]
        R = sc["R"]
        np.savez_compressed(os.path.join(art, "scenarios.npz"), R=R, names=np.array(names),
                            component=sc["component"])
        clusters = dict(risk["clusters"])
        for t in names:
            clusters.setdefault(t, max(clusters.values(), default=0) + 1)
        cons = base_constraints(cfg, names, sectors, clusters, uni["liquidity"],
                                (prev or {}).get("book"))
        policy = SolverPolicy(cfg)
        if reopt:
            opt, _ = self._tool(ctx, audit, "optimize_portfolio",
                                lambda: optimize_portfolio(cfg, R, names, cons, mip=True,
                                                           policy=policy),
                                {"names": names, "fingerprint": "see payload"})
            if opt["status"] == "infeasible":
                return self._abort(ctx, session, "optimizer infeasible: "
                                   + "; ".join(opt["warnings"]), warnings, counts)
            warnings += opt["warnings"]
            book = opt["weights"]
        else:
            book = prev["book"]
            w = np.array([book.get(t, 0.0) for t in names])
            rk = portfolio_risk(R, w, cons.alpha)
            opt = {"status": "refresh", "weights": book, "er": rk["er"], "cvar": rk["cvar"],
                   "var": rk["var"], "solver_log": [], "warnings": []}
        warnings.append(f"optimization: {'re-optimized' if reopt else 'kept'} ({why})")
        counts["book"] = len(book)

        # 7. technical gate on the book
        macro = load_macro(macro_path, prev_gate=(prev or {}).get("exposure_gate"))
        ta, _ = self._tool(ctx, audit, "run_technical_screens",
                           lambda: gate(cfg, run_ta_registry(panel, tcfg), list(book), session,
                                        panel.earnings, macro))
        warnings += ta["warnings"]
        counts["triggered"] = len(ta["triggers"])

        # 8. macro (stub) -> 9. resolve_final
        if macro.get("macro_stale"):
            warnings.append("macro_stale=true (no macro agent output; neutral gate)")
        res, _ = self._tool(ctx, audit, "resolve_final",
                            lambda: resolve_final(cfg, R, names, book, ta["triggers"], cons,
                                                  float(macro["exposure_gate"]), policy))
        warnings += [f"resolve: {w}" for w in res["warnings"]]

        state = {"session": session, "run_id": ctx.run_id, "book": book,
                 "carried": risk["carried"], "regime": risk["regime"]["label"],
                 "exposure_gate": macro["exposure_gate"], "names": names,
                 "clusters": clusters, "ta": ta, "reopt": reopt}
        with open(os.path.join(art, "state.json"), "w") as f:
            json.dump(state, f, indent=1, default=str)
        pd.DataFrame({"ticker": list(book), "weight": list(book.values())}).to_parquet(
            os.path.join(art, "book.parquet"), index=False)

        top3 = self._build_top3(ctx, session, counts, risk, macro, book, ta, res, sc, panel,
                                fund, uni, warnings, art)
        blend = None
        if cfg.strategy["blend"]["enabled"]:
            try:
                blend, _ = self._tool(ctx, audit, "run_blend", lambda: run_blend(cfg, snap))
            except Exception as e:  # noqa: BLE001 — parallel output never blocks the funnel
                warnings.append(f"blend screen failed: {type(e).__name__}: {e}")
        paths = write_report(cfg.path("reports"), session, top3, blend)
        self._learning_log(ctx, session, counts, risk, opt, policy, warnings, fund, book, ta,
                           res, macro, panel, top3)
        return {"status": top3["status"], "paths": paths, "top3": top3, "blend": blend}

    # -------------------------------------------------------------- top3.json
    def _build_top3(self, ctx, session, counts, risk, macro, book, ta, res, sc, panel, fund,
                    uni, warnings, art) -> dict:
        cfg = self.cfg
        final = res["final_entries"]
        basket = res.get("basket") or {}
        R, names = sc["R"], sc["names"]
        cons_tbl = fund["consensus"].set_index("ticker")
        fam_cols = [c for c in cons_tbl.columns if c.startswith("fam_")]
        ev_risk = {e["ticker"]: e["score"] for e in macro.get("event_risk", [])}
        nxt = _next_session(session)
        acct = cfg.strategy["optimizer"].get("account_value_usd")
        worst_class = {}
        if final:
            S_names = list(basket["weights"])
            idx = [names.index(t) for t in S_names]
            w = np.array([basket["weights"][t] for t in S_names])
            rk = portfolio_risk(R[:, idx], w, cfg.strategy["optimizer"]["alpha"])
            tail = rk["tail_index"]
            for j, t in enumerate(S_names):
                contrib = -R[tail, idx[j]] * w[j]
                worst_class[t] = str(sc["component"][tail[int(np.argmax(contrib))]])
        entries = []
        for t, fw in sorted(final.items(), key=lambda kv: (-kv[1], kv[0]))[:3]:
            close = panel.close[t].dropna()
            p = ta["per_name"].get(t, {})
            entries.append({
                "ticker": t, "final_weight": fw,
                "size_suggestion": {"account_fraction": fw,
                                    "usd": round(fw * float(acct), 2) if acct else None,
                                    "note": "scaled by exposure_gate"},
                "entry": {"window": f"open {nxt} ET",
                          "reference_price": round(float(close.iloc[-1]), 4),
                          "valid_until": f"close {nxt}"},
                "risk": {"marginal_cvar_pct_of_book":
                         round(basket["marginal_cvar"][t] * 100, 3),
                         "basket_cvar95_21d_pct": round(basket["cvar"] * 100, 3),
                         "worst_scenario_class": worst_class.get(t)},
                "scores": {"fund_families": {c[4:]: round(float(cons_tbl.at[t, c]), 1)
                                             for c in fam_cols
                                             if t in cons_tbl.index
                                             and pd.notna(cons_tbl.at[t, c])},
                           "ta_consensus_pct": p.get("ta_consensus_pct"),
                           "trigger_source": p.get("trigger_source"),
                           "macro_event_risk": ev_risk.get(t)},
                "rationale_slots": {"narrative": "", "citations": []},
                "veto_trail": []})
        not_trig = [{"ticker": t, "book_weight": w,
                     "reason": ta["per_name"].get(t, {}).get("veto_reason", "")}
                    for t, w in book.items() if t not in final]
        status = "ok" if len(final) >= cfg.strategy["optimizer"]["min_entries"] else (
            "no_entries" if not final else "fewer_than_3")
        return {"run_id": ctx.run_id, "session": session,
                "as_of": dt.datetime.now(dt.timezone.utc).isoformat(),
                "strategy_version": ctx.strategy_version, "engine_version": ctx.engine_version,
                "data_snapshot": ctx.snapshot, "seed": ctx.seed, "status": status,
                "status_reason": "" if status == "ok" else
                f"{len(final)} actionable entries (not padded)",
                "funnel_counts": counts, "regime": risk["regime"],
                "exposure_gate": macro["exposure_gate"], "macro_stale": macro["macro_stale"],
                "book_ref": os.path.relpath(os.path.join(art, "book.parquet"), self.cfg.root),
                "top3": entries, "all_entries": dict(sorted(final.items(),
                                                            key=lambda kv: -kv[1])),
                "basket_cvar95_21d": basket.get("cvar"), "basket_er_21d": basket.get("er"),
                "not_triggered": not_trig, "relaxations": res.get("relaxations", []),
                "calibration_status": "insufficient_history",
                "scenario_diagnostics": sc["diagnostics"],
                "warnings": warnings}

    # -------------------------------------------------------------- learning log
    def _learning_log(self, ctx, session, counts, risk, opt, policy, warnings, fund, book, ta,
                      res, macro, panel, top3) -> None:
        db = LearningDB(self.cfg.path("db"))
        try:
            db.log_run({"run_id": ctx.run_id, "date": session, "pass": "nightly",
                        "strategy_version": ctx.strategy_version,
                        "strategy_hash": self.cfg.strategy_hash,
                        "engine_version": ctx.engine_version, "data_snapshot": session,
                        "seed": ctx.seed, "regime_label": risk["regime"].get("label"),
                        "regime_probs": risk["regime"].get("probs"),
                        "universe_size": counts.get("universe"), "funnel_counts": counts,
                        "solver_info": {"log": policy.log, "status": opt.get("status"),
                                        "fingerprint": opt.get("fingerprint"),
                                        "crosscheck": opt.get("crosscheck")},
                        "warnings": warnings, "status": top3["status"]})
            cons = fund["consensus"].set_index("ticker")
            fam_cols = [c for c in cons.columns if c.startswith("fam_")]
            ev = {e["ticker"]: e["score"] for e in macro.get("event_risk", [])}
            rows = []
            for t in fund["candidates"]:
                stage = 3 + (t in risk["carried"]) + (t in book) * 2 + \
                    (t in res["final_entries"]) * 3
                p = ta["per_name"].get(t, {})
                close = panel.close[t].dropna() if t in panel.close.columns else pd.Series()
                rows.append({
                    "run_id": ctx.run_id, "ticker": t, "reached_stage": int(stage),
                    "fund_family_pcts": {c[4:]: (None if pd.isna(cons.at[t, c])
                                                 else float(cons.at[t, c])) for c in fam_cols},
                    "fund_consensus": float(cons.at[t, "fund_consensus"]),
                    "cluster_id": risk["clusters"].get(t), "er_score": None,
                    "risk_score": None, "anomaly_flags": risk["anomalies"].get(t, []),
                    "in_book": t in book, "book_weight": book.get(t),
                    "ta_consensus": p.get("ta_consensus_pct"),
                    "triggered": p.get("triggered"), "veto_reason": p.get("veto_reason"),
                    "macro_event_risk": ev.get(t),
                    "final_weight": res["final_entries"].get(t),
                    "entry_ref_price": float(close.iloc[-1]) if len(close) else None})
            db.log_candidates(rows)
            db.log_entries([{"run_id": ctx.run_id, "ticker": e["ticker"],
                             "entry_date": _next_session(session),
                             "entry_ref_price": e["entry"]["reference_price"],
                             "planned_weight": e["final_weight"],
                             "exposure_gate": top3["exposure_gate"],
                             "regime_at_entry": risk["regime"].get("label")}
                            for e in top3["top3"]])
            db.log_macro(ctx.run_id, macro)
            spy = self.cfg.infra["benchmarks"]["market"]
            if spy in panel.benchmarks.columns:
                db.backfill_outcomes(panel.adjclose, panel.benchmarks[spy])
        finally:
            db.close()

    # -------------------------------------------------------------- premarket pass
    def run_premarket(self, session: str, macro_path: str | None,
                      run_seq: int | None = None) -> dict:
        """Re-check gates with a fresh macro read and re-run resolve_final (§10, 15:00–15:30)."""
        cfg = self.cfg
        run_seq = run_seq or next_run_seq(cfg, session, "premarket")
        art = os.path.join(cfg.path("artifacts"), session)
        with open(os.path.join(art, "state.json")) as f:
            state = json.load(f)
        z = np.load(os.path.join(art, "scenarios.npz"), allow_pickle=False)
        R, names = z["R"], [str(x) for x in z["names"]]
        ctx = RunContext(f"{session}-premarket-{run_seq:03d}", session, cfg.seed_for(session),
                         cfg.strategy_version)
        macro = load_macro(macro_path, prev_gate=state.get("exposure_gate"))
        ev = {e["ticker"]: e["score"] for e in macro.get("event_risk", [])}
        thr = cfg.strategy["macro"]["event_risk_delay"]
        triggers = [t for t in state["ta"]["triggers"] if ev.get(t, 0.0) < thr]
        delayed = sorted(set(state["ta"]["triggers"]) - set(triggers))
        snap = snapshot_dir(cfg, session)
        uni = screen_universe(cfg, snap, session)
        panel = vendored.ta().panel.load_panel(os.path.join(snap, "ta"), ta_config(cfg))
        sectors = {t: (s if isinstance(s, str) else None) for t, s in panel.meta["sector"].items()}
        clusters = state["clusters"]
        fund_cons = pd.read_parquet(os.path.join(art, "fund_consensus.parquet"))
        cons = base_constraints(cfg, names, sectors, clusters, uni["liquidity"], None)
        res = resolve_final(cfg, R, names, state["book"], triggers, cons,
                            float(macro["exposure_gate"]))
        warnings = list(res["warnings"]) + [f"premarket: delayed by macro event risk: {delayed}"
                                            if delayed else "premarket: no new delays"]
        if macro.get("macro_stale"):
            warnings.append("macro_stale=true")
        ta = dict(state["ta"])
        for t in delayed:
            ta["per_name"][t]["veto_reason"] = f"macro_event_risk {ev[t]:.2f} (delayed)"
        sc = {"R": R, "names": names, "component": z["component"], "diagnostics": {}}
        fund = {"consensus": fund_cons}
        counts = {"book": len(state["book"]), "triggered": len(triggers)}
        top3 = self._build_top3(ctx, session, counts, {"regime": {"label": state["regime"]}},
                                macro, state["book"], ta, res, sc, panel, fund, uni, warnings,
                                art)
        paths = write_report(os.path.join(cfg.path("reports")), f"{session}-premarket", top3,
                             None)
        db = LearningDB(cfg.path("db"))
        try:
            db.log_run({"run_id": ctx.run_id, "date": session, "pass": "premarket",
                        "strategy_version": ctx.strategy_version,
                        "strategy_hash": cfg.strategy_hash, "engine_version": ctx.engine_version,
                        "data_snapshot": session, "seed": ctx.seed,
                        "regime_label": state["regime"], "funnel_counts": counts,
                        "warnings": warnings, "status": top3["status"]})
            db.log_macro(ctx.run_id, macro)
        finally:
            db.close()
        return {"status": top3["status"], "paths": paths, "top3": top3}
