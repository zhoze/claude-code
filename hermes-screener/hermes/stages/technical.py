"""Stage 7 — run_technical_screens: the 150-screen registry as an entry-timing gate (§4.3–4.4).

The vendored screens run unmodified on the full universe panel, because many alpha
formulas are cross-sectional (rank(), IndNeutralize) and would be meaningless on
10–20 names. Their family-balanced scores (vendored run_screens.consensus) are then
re-ranked WITHIN THE BOOK when technical.percentile_basis == "book" (spec §4.4), or
used at universe level when it is "universe".

Gate: a book name triggers iff its TA consensus percentile >= t_entry and no veto is
active. Vetoes: beat_and_drop on the latest announcement; max_spike (the most
lottery-like MAX decile); earnings announcement within ±N trading days unless the
trigger comes from the earnings family; macro event_risk >= threshold (a delay).
Fallback ladder: if fewer than `min_entries` trigger, lower to t_entry_floor; if
still short, report fewer with an explicit flag. Untriggered names are never
used to pad the list.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from .. import vendored


def _bday_gap(a: pd.Timestamp, b: pd.Timestamp) -> int:
    lo, hi = sorted([a.normalize(), b.normalize()])
    return int(np.busday_count(lo.date(), hi.date()))


def ta_config(cfg) -> dict:
    """The vendored ta-screener config, with the panel's bar minimum overridable."""
    tcfg = vendored.ta().panel.load_config()
    mpb = cfg.infra.get("history", {}).get("min_price_bars")
    if mpb:
        tcfg["universe"]["min_price_bars"] = int(mpb)
    return tcfg


def run_ta_registry(panel, tcfg: dict | None = None) -> dict:
    v = vendored.ta()
    tcfg = tcfg or v.panel.load_config()
    rows, ranked, full_pct, skipped, no_signal, errors, _ = v.run_screens.run_all(
        panel, tcfg, top=10, empirical=False)
    fam_df, overall = v.run_screens.consensus(rows, full_pct, tcfg)
    specs = {spec.key: spec for _, spec, _ in rows}
    latest = {}
    for key in ("beat_and_drop", "max_avoidance"):
        try:
            with vendored.using("ta"):
                latest[key] = specs[key].runner(panel, tcfg).iloc[-1]
        except Exception:  # noqa: BLE001 — veto source unavailable -> recorded below
            latest[key] = None
    return {"fam_df": fam_df, "overall": overall, "latest": latest,
            "status": {"ran": len(ranked), "no_signal": no_signal,
                       "skipped": [k for k, _ in skipped], "errors": [k for k, _ in errors]}}


def gate(cfg, reg: dict, book: list[str], session: str, earnings: pd.DataFrame,
         macro: dict) -> dict:
    tc = cfg.strategy["technical"]
    warnings = []
    fam = reg["fam_df"].reindex(book)
    if tc["percentile_basis"] == "book":
        fam_pct = fam.rank(pct=True, method="average") * 100
        need = min(int(tc["min_families"]), max(1, fam.notna().any().sum()))
        cons = fam_pct.mean(axis=1).where(fam_pct.notna().sum(axis=1) >= need)
    else:
        fam_pct = fam
        cons = reg["overall"]["consensus_balanced"].reindex(book) \
            if not reg["overall"].empty else pd.Series(np.nan, index=book)

    # vetoes
    vetoes: dict[str, list[str]] = {t: [] for t in book}
    bad = reg["latest"].get("beat_and_drop")
    if bad is None:
        warnings.append("beat_and_drop veto source unavailable (no earnings input)")
    else:
        for t in book:
            if pd.notna(bad.get(t, np.nan)):
                vetoes[t].append("beat_and_drop")
    mx = reg["latest"].get("max_avoidance")
    if mx is None:
        warnings.append("max_spike veto source unavailable")
    else:
        pct = mx.rank(pct=True) * 100
        for t in book:
            if pd.notna(pct.get(t, np.nan)) and pct[t] <= tc["max_spike_pct"]:
                vetoes[t].append("max_spike")
    sess = pd.Timestamp(session)
    blackout: dict[str, str] = {}
    if earnings is not None and not earnings.empty:
        ed = earnings.assign(date=pd.to_datetime(earnings["date"]))
        for t in book:
            ds = ed.loc[ed["ticker"] == t, "date"]
            near = [d for d in ds if _bday_gap(sess, d) <= tc["earnings_blackout_days"]]
            if near:
                blackout[t] = str(min(near, key=lambda d: abs((d - sess).days)).date())
    event_risk = {e["ticker"]: float(e["score"]) for e in (macro or {}).get("event_risk", [])
                  if "ticker" in e and "score" in e}

    def evaluate(t_entry):
        out = {}
        for t in book:
            pct = cons.get(t, np.nan)
            sources = [f for f in fam_pct.columns
                       if pd.notna(fam_pct.at[t, f]) and fam_pct.at[t, f] >= t_entry]
            sources.sort(key=lambda f: -fam_pct.at[t, f])
            reasons = list(vetoes[t])
            if t in blackout and "earnings" not in sources:
                reasons.append(f"earnings_blackout (announcement {blackout[t]})")
            if event_risk.get(t, 0.0) >= cfg.strategy["macro"]["event_risk_delay"]:
                reasons.append(f"macro_event_risk {event_risk[t]:.2f} (delayed)")
            trig = bool(pd.notna(pct) and pct >= t_entry and not reasons)
            if pd.isna(pct):
                reasons = reasons + ["no TA consensus coverage"]
            elif pct < t_entry:
                reasons = reasons + [f"ta_consensus {pct:.0f} < {t_entry}"]
            out[t] = {"ta_consensus_pct": None if pd.isna(pct) else round(float(pct), 2),
                      "families": {f: round(float(fam_pct.at[t, f]), 2)
                                   for f in fam_pct.columns if pd.notna(fam_pct.at[t, f])},
                      "triggered": trig, "trigger_source": "+".join(sources) if trig else "",
                      "veto_reason": "; ".join(reasons)}
        return out

    min_n = int(cfg.strategy["optimizer"]["min_entries"])
    t_used = tc["t_entry"]
    per = evaluate(t_used)
    if sum(p["triggered"] for p in per.values()) < min_n:
        t_used = tc["t_entry_floor"]
        per = evaluate(t_used)
        warnings.append(f"fallback_ladder: t_entry lowered {tc['t_entry']} -> {t_used}")
    n_trig = sum(p["triggered"] for p in per.values())
    if n_trig < min_n:
        warnings.append(f"fewer_than_{min_n}_triggered: {n_trig} (not padded)")
    return {"per_name": per, "t_entry_used": t_used,
            "triggers": sorted(t for t, p in per.items() if p["triggered"]),
            "vetoes": {t: p["veto_reason"] for t, p in per.items() if not p["triggered"]},
            "warnings": warnings, "registry_status": reg["status"]}
