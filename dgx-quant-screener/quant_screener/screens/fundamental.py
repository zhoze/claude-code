"""The 10 fundamental screens (spec §3).

Each screen is a pure function over the cross-section:
    run(records: dict[ticker, FundamentalRecord]) -> DataFrame[raw, passed, confidence]

Conventions:
- raw is oriented so HIGHER = BETTER (ensemble converts to percentiles).
- NaN inputs reduce `confidence` (fraction of required inputs present); a screen
  never invents a value for a missing metric (spec §2).
- `passed` is the screen's own absolute criterion, independent of ranking.
"""

from __future__ import annotations

import math

import numpy as np
import pandas as pd

from ..data.fundamentals import FundamentalRecord


def _frame(rows: dict[str, dict]) -> pd.DataFrame:
    df = pd.DataFrame.from_dict(rows, orient="index")
    df.index.name = "ticker"
    return df


def _conf(*vals) -> float:
    present = sum(1 for v in vals if v is not None and isinstance(v, float) and math.isfinite(v))
    return present / max(len(vals), 1)


# ------------------------------------------------------------------ 1. Piotroski
def screen_piotroski(records: dict[str, FundamentalRecord]) -> pd.DataFrame:
    rows = {}
    for t, r in records.items():
        ni, ni_p = r.get("net_income_ttm"), r.get("net_income_ttm_prior")
        ocf, ocf_p = r.get("ocf_ttm"), r.get("ocf_ttm_prior")
        ta, ta_p = r.get("total_assets"), r.get("total_assets_prior")
        debt, debt_p = r.get("total_debt"), r.get("total_debt_prior")
        ca, cl = r.get("current_assets"), r.get("current_liabilities")
        ca_p, cl_p = r.get("current_assets_prior"), r.get("current_liabilities_prior")
        gp, gp_p = r.get("gross_profit_ttm"), r.get("gross_profit_ttm_prior")
        rev, rev_p = r.get("revenue_ttm"), r.get("revenue_ttm_prior")
        sh, sh_p = r.get("shares_outstanding"), r.get("shares_outstanding_prior")

        pts, tested = 0, 0
        def test(cond_ok, cond):
            nonlocal pts, tested
            if cond_ok:
                tested += 1
                if cond:
                    pts += 1
        roa = ni / ta if math.isfinite(ni) and math.isfinite(ta) and ta else math.nan
        roa_p = ni_p / ta_p if math.isfinite(ni_p) and math.isfinite(ta_p) and ta_p else math.nan
        test(math.isfinite(roa), roa > 0)                                   # 1 ROA > 0
        test(math.isfinite(ocf), ocf > 0)                                   # 2 CFO > 0
        test(math.isfinite(roa) and math.isfinite(roa_p), roa > roa_p)      # 3 ΔROA > 0
        test(math.isfinite(ocf) and math.isfinite(ni), ocf > ni)            # 4 accruals
        lev = debt / ta if math.isfinite(debt) and math.isfinite(ta) and ta else math.nan
        lev_p = debt_p / ta_p if math.isfinite(debt_p) and math.isfinite(ta_p) and ta_p else math.nan
        test(math.isfinite(lev) and math.isfinite(lev_p), lev <= lev_p)     # 5 Δleverage
        cr = ca / cl if math.isfinite(ca) and math.isfinite(cl) and cl else math.nan
        cr_p = ca_p / cl_p if math.isfinite(ca_p) and math.isfinite(cl_p) and cl_p else math.nan
        test(math.isfinite(cr) and math.isfinite(cr_p), cr >= cr_p)         # 6 Δcurrent ratio
        test(math.isfinite(sh) and math.isfinite(sh_p), sh <= sh_p * 1.02)  # 7 no dilution
        gm = gp / rev if math.isfinite(gp) and math.isfinite(rev) and rev else math.nan
        gm_p = gp_p / rev_p if math.isfinite(gp_p) and math.isfinite(rev_p) and rev_p else math.nan
        test(math.isfinite(gm) and math.isfinite(gm_p), gm >= gm_p)         # 8 Δgross margin
        at = rev / ta if math.isfinite(rev) and math.isfinite(ta) and ta else math.nan
        at_p = rev_p / ta_p if math.isfinite(rev_p) and math.isfinite(ta_p) and ta_p else math.nan
        test(math.isfinite(at) and math.isfinite(at_p), at >= at_p)         # 9 Δasset turnover

        score = pts * 9 / tested if tested >= 5 else math.nan  # rescale partial coverage
        rows[t] = {"raw": score, "passed": bool(math.isfinite(score) and score >= 7),
                   "confidence": tested / 9}
    return _frame(rows)


# ------------------------------------------------------------- 2. Magic Formula
def screen_magic_formula(records: dict[str, FundamentalRecord]) -> pd.DataFrame:
    ey, roc = {}, {}
    for t, r in records.items():
        ebit, ev = r.get("operating_income_ttm"), r.get("enterprise_value")
        nwc, ta = r.get("working_capital"), r.get("total_assets")
        ey[t] = ebit / ev if math.isfinite(ebit) and math.isfinite(ev) and ev > 0 else math.nan
        cap_base = (nwc if math.isfinite(nwc) else 0) + (ta * 0.5 if math.isfinite(ta) else math.nan)
        roc[t] = ebit / cap_base if math.isfinite(ebit) and math.isfinite(cap_base) and cap_base > 0 else math.nan
    ey_s, roc_s = pd.Series(ey), pd.Series(roc)
    combined_rank = ey_s.rank(ascending=False) + roc_s.rank(ascending=False)
    rows = {}
    n = combined_rank.notna().sum()
    for t in records:
        cr = combined_rank.get(t, math.nan)
        raw = (n - cr) if math.isfinite(cr) else math.nan   # higher = better
        rows[t] = {"raw": raw,
                   "passed": bool(math.isfinite(cr) and cr <= max(n * 0.2, 10)),
                   "confidence": _conf(ey.get(t), roc.get(t))}
    return _frame(rows)


# ----------------------------------------------------- 3. Acquirer's Multiple
def screen_acquirers_multiple(records: dict[str, FundamentalRecord]) -> pd.DataFrame:
    rows = {}
    vals = {}
    for t, r in records.items():
        ebit, ev = r.get("operating_income_ttm"), r.get("enterprise_value")
        # unreliable when EBIT <= 0 or EV <= 0 (spec: exclude unless normalized)
        am = ev / ebit if math.isfinite(ebit) and math.isfinite(ev) and ebit > 0 and ev > 0 else math.nan
        vals[t] = am
    s = pd.Series(vals)
    thresh = s.quantile(0.2)
    for t, am in vals.items():
        rows[t] = {"raw": -am if math.isfinite(am) else math.nan,  # low multiple = good
                   "passed": bool(math.isfinite(am) and am <= thresh),
                   "confidence": 1.0 if math.isfinite(am) else 0.0}
    return _frame(rows)


# ------------------------------------------------------------ 4. Value composite
def screen_value_composite(records: dict[str, FundamentalRecord]) -> pd.DataFrame:
    metrics = {}
    for t, r in records.items():
        mc, ev = r.get("market_cap"), r.get("enterprise_value")
        ni, ebit = r.get("net_income_ttm"), r.get("operating_income_ttm")
        ebitda, fcf, rev = r.get("ebitda_ttm"), r.get("fcf_ttm"), r.get("revenue_ttm")
        eq = r.get("equity")
        def ratio(num, den):
            return num / den if math.isfinite(num) and math.isfinite(den) and den > 0 else math.nan
        metrics[t] = {
            "ep": ratio(ni, mc), "fwd_ep": (1 / r.get("pe_forward")) if r.get("pe_forward") > 0 else math.nan,
            "ebit_ev": ratio(ebit, ev), "ebitda_ev": ratio(ebitda, ev),
            "fcf_yield": ratio(fcf, mc), "bp": ratio(eq, mc), "sp": ratio(rev, mc),
            "sector": r.sector or "UNKNOWN",
        }
    df = pd.DataFrame.from_dict(metrics, orient="index")
    yield_cols = ["ep", "fwd_ep", "ebit_ev", "ebitda_ev", "fcf_yield", "bp", "sp"]
    # sector-relative percentiles (spec §3.4: normalize by sector)
    pct = df.groupby("sector")[yield_cols].rank(pct=True)
    small = pct.groupby(df["sector"]).transform("count") < 5
    pct[small] = df[yield_cols].rank(pct=True)[small]      # tiny sectors -> global rank
    composite = pct.mean(axis=1, skipna=True)
    coverage = pct.notna().mean(axis=1)
    rows = {t: {"raw": composite.get(t, math.nan),
                "passed": bool(composite.get(t, 0) >= 0.75 and coverage.get(t, 0) >= 0.5),
                "confidence": float(coverage.get(t, 0))}
            for t in records}
    return _frame(rows)


# ------------------------------------------------------ 5. Quality/profitability
def screen_quality(records: dict[str, FundamentalRecord]) -> pd.DataFrame:
    rows = {}
    comp = {}
    for t, r in records.items():
        ni, eq, ta = r.get("net_income_ttm"), r.get("equity"), r.get("total_assets")
        gp, rev = r.get("gross_profit_ttm"), r.get("revenue_ttm")
        op, ocf, fcf = r.get("operating_income_ttm"), r.get("ocf_ttm"), r.get("fcf_ttm")
        ebit, ev = r.get("operating_income_ttm"), r.get("enterprise_value")
        def ratio(num, den):
            return num / den if math.isfinite(num) and math.isfinite(den) and abs(den) > 1e-9 else math.nan
        comp[t] = {
            "roe": ratio(ni, eq) if eq and eq > 0 else math.nan,
            "roa": ratio(ni, ta),
            "roic_proxy": ratio(ebit, (eq or 0) + (r.get("net_debt") if math.isfinite(r.get("net_debt")) else 0))
                if math.isfinite(eq) else math.nan,
            "gross_profitability": ratio(gp, ta),          # Novy-Marx
            "op_margin": ratio(op, rev),
            "fcf_margin": ratio(fcf, rev),
            "fcf_conversion": ratio(fcf, ni) if math.isfinite(ni) and ni > 0 else math.nan,
            "accrual_quality": ratio(ocf - ni, ta) if math.isfinite(ocf) and math.isfinite(ni) else math.nan,
        }
    df = pd.DataFrame.from_dict(comp, orient="index")
    pct = df.rank(pct=True)
    composite = pct.mean(axis=1, skipna=True)
    coverage = pct.notna().mean(axis=1)
    for t in records:
        c = comp[t]
        sustainable = (math.isfinite(c["roe"]) and c["roe"] > 0.10 and
                       math.isfinite(c["fcf_margin"]) and c["fcf_margin"] > 0)
        rows[t] = {"raw": composite.get(t, math.nan),
                   "passed": bool(composite.get(t, 0) >= 0.70 and sustainable),
                   "confidence": float(coverage.get(t, 0))}
    return _frame(rows)


# ---------------------------------------------------------------------- 6. GARP
def screen_garp(records: dict[str, FundamentalRecord]) -> pd.DataFrame:
    rows = {}
    for t, r in records.items():
        rev, rev_p = r.get("revenue_ttm"), r.get("revenue_ttm_prior")
        eps, eps_p = r.get("eps_ttm"), r.get("eps_ttm_prior")
        fcf, fcf_p = r.get("fcf_ttm"), r.get("fcf_ttm_prior")
        pe = r.get("pe_trailing")
        rev_g = rev / rev_p - 1 if math.isfinite(rev) and math.isfinite(rev_p) and rev_p > 0 else r.get("rev_growth_info")
        eps_g = eps / eps_p - 1 if math.isfinite(eps) and math.isfinite(eps_p) and eps_p > 0 else r.get("eps_growth_info")
        fcf_g = fcf / fcf_p - 1 if math.isfinite(fcf) and math.isfinite(fcf_p) and fcf_p > 0 else math.nan
        peg = r.get("peg")
        if not math.isfinite(peg) and math.isfinite(pe) and math.isfinite(eps_g) and eps_g > 0:
            peg = pe / (eps_g * 100)
        growth = np.nanmean([g for g in (rev_g, eps_g, fcf_g)])
        # growth is capped: paying up for unsustainable growth is penalized
        growth = min(growth, 0.60) if math.isfinite(growth) else math.nan
        raw = growth / max(peg, 0.1) if math.isfinite(growth) and math.isfinite(peg) and peg > 0 else math.nan
        passed = (math.isfinite(peg) and 0 < peg <= 1.5 and
                  math.isfinite(growth) and growth >= 0.08)
        rows[t] = {"raw": raw, "passed": bool(passed),
                   "confidence": _conf(rev_g, eps_g, peg)}
    return _frame(rows)


# --------------------------------------------------- 7. FCF / owner earnings
def screen_fcf_owner_earnings(records: dict[str, FundamentalRecord]) -> pd.DataFrame:
    rows = {}
    for t, r in records.items():
        fcf, fcf_p, mc = r.get("fcf_ttm"), r.get("fcf_ttm_prior"), r.get("market_cap")
        ocf, ni = r.get("ocf_ttm"), r.get("net_income_ttm")
        capex, rev, ev = r.get("capex_ttm"), r.get("revenue_ttm"), r.get("enterprise_value")
        fcf_yield = fcf / mc if math.isfinite(fcf) and math.isfinite(mc) and mc > 0 else math.nan
        fcf_growth = fcf / fcf_p - 1 if math.isfinite(fcf) and math.isfinite(fcf_p) and fcf_p > 0 else math.nan
        conversion = ocf / ni if math.isfinite(ocf) and math.isfinite(ni) and ni > 0 else math.nan
        capex_intensity = capex / rev if math.isfinite(capex) and math.isfinite(rev) and rev > 0 else math.nan
        owner_yield = (fcf / ev) if math.isfinite(fcf) and math.isfinite(ev) and ev > 0 else math.nan
        raw = np.nansum([
            (fcf_yield or 0) * 5,
            min(fcf_growth, 1.0) if math.isfinite(fcf_growth) else 0,
            min(conversion, 2.0) * 0.25 if math.isfinite(conversion) else 0,
            -min(capex_intensity, 0.5) if math.isfinite(capex_intensity) else 0,
        ]) if math.isfinite(fcf_yield) else math.nan
        passed = (math.isfinite(fcf_yield) and fcf_yield >= 0.05 and
                  math.isfinite(conversion) and conversion >= 0.8)
        rows[t] = {"raw": raw, "passed": bool(passed),
                   "confidence": _conf(fcf_yield, fcf_growth, conversion, owner_yield)}
    return _frame(rows)


# -------------------------------------------------------- 8. Shareholder yield
def screen_shareholder_yield(records: dict[str, FundamentalRecord]) -> pd.DataFrame:
    rows = {}
    for t, r in records.items():
        mc = r.get("market_cap")
        div = r.get("dividends_paid_ttm")
        bb, iss = r.get("buybacks_ttm"), r.get("issuance_ttm")
        nd, nd_p = r.get("net_debt"), r.get("net_debt_prior")
        if not (math.isfinite(mc) and mc > 0):
            rows[t] = {"raw": math.nan, "passed": False, "confidence": 0.0}
            continue
        div_y = div / mc if math.isfinite(div) else math.nan
        net_bb = ((bb if math.isfinite(bb) else 0) - (iss if math.isfinite(iss) else 0)) / mc
        debt_paydown = (nd_p - nd) / mc if math.isfinite(nd) and math.isfinite(nd_p) else math.nan
        total = np.nansum([div_y, net_bb, debt_paydown])
        dilution_penalty = net_bb < -0.02      # persistent dilution
        raw = total - (0.05 if dilution_penalty else 0)
        rows[t] = {"raw": raw, "passed": bool(total >= 0.04 and not dilution_penalty),
                   "confidence": _conf(div_y, net_bb, debt_paydown)}
    return _frame(rows)


# ------------------------------------------------------- 9. Financial strength
def screen_financial_strength(records: dict[str, FundamentalRecord]) -> pd.DataFrame:
    rows = {}
    for t, r in records.items():
        nd, ebitda = r.get("net_debt"), r.get("ebitda_ttm")
        debt, eq = r.get("total_debt"), r.get("equity")
        ebit, ie = r.get("operating_income_ttm"), r.get("interest_expense_ttm")
        ca, cl, inv = r.get("current_assets"), r.get("current_liabilities"), r.get("inventory")
        wc, ta = r.get("working_capital"), r.get("total_assets")
        re_, rev, mc = r.get("retained_earnings"), r.get("revenue_ttm"), r.get("market_cap")

        nd_ebitda = nd / ebitda if math.isfinite(nd) and math.isfinite(ebitda) and ebitda > 0 else math.nan
        de = debt / eq if math.isfinite(debt) and math.isfinite(eq) and eq > 0 else math.nan
        icov = ebit / ie if math.isfinite(ebit) and math.isfinite(ie) and ie > 0 else math.nan
        cr = ca / cl if math.isfinite(ca) and math.isfinite(cl) and cl > 0 else math.nan
        qr = (ca - (inv if math.isfinite(inv) else 0)) / cl if math.isfinite(ca) and math.isfinite(cl) and cl > 0 else math.nan
        # Altman Z (non-manufacturer variant approximation)
        z = math.nan
        if all(math.isfinite(v) for v in (wc, ta, re_, ebit, mc, debt)) and ta > 0 and debt > 0:
            z = (6.56 * wc / ta + 3.26 * re_ / ta + 6.72 * ebit / ta + 1.05 * mc / debt)
        pts, n = 0.0, 0
        for val, good, cap in ((nd_ebitda, lambda v: max(0, 1 - v / 4), 1),
                               (de, lambda v: max(0, 1 - v / 2), 1),
                               (icov, lambda v: min(v / 8, 1), 1),
                               (cr, lambda v: min(v / 2, 1), 1),
                               (z, lambda v: min(max(v / 6, 0), 1), 1)):
            if math.isfinite(val):
                pts += good(val); n += 1
        raw = pts / n if n >= 3 else math.nan
        passed = (math.isfinite(raw) and raw >= 0.6 and
                  (not math.isfinite(nd_ebitda) or nd_ebitda <= 3) and
                  (not math.isfinite(z) or z >= 2.6))
        rows[t] = {"raw": raw, "passed": bool(passed), "confidence": n / 5}
    return _frame(rows)


# -------------------------------------- 10. Fundamental momentum / revisions
def screen_fundamental_momentum(records: dict[str, FundamentalRecord]) -> pd.DataFrame:
    rows = {}
    for t, r in records.items():
        surprise = r.get("avg_eps_surprise")
        est_now, est_prior = r.get("est_eps_fwd"), r.get("est_eps_fwd_prior")
        eps, eps_p = r.get("eps_ttm"), r.get("eps_ttm_prior")
        rev, rev_p = r.get("revenue_ttm"), r.get("revenue_ttm_prior")
        op, op_p = r.get("operating_income_ttm"), r.get("operating_income_ttm_prior")
        revision = est_now / est_prior - 1 if math.isfinite(est_now) and math.isfinite(est_prior) and est_prior > 0 else math.nan
        eps_accel = eps - eps_p if math.isfinite(eps) and math.isfinite(eps_p) else math.nan
        margin_now = op / rev if math.isfinite(op) and math.isfinite(rev) and rev > 0 else math.nan
        margin_prior = op_p / rev_p if math.isfinite(op_p) and math.isfinite(rev_p) and rev_p > 0 else math.nan
        margin_rev = margin_now - margin_prior if math.isfinite(margin_now) and math.isfinite(margin_prior) else math.nan
        parts = [np.clip(surprise, -0.5, 0.5) if math.isfinite(surprise) else math.nan,
                 np.clip(revision, -0.5, 0.5) if math.isfinite(revision) else math.nan,
                 np.clip(margin_rev * 5, -0.5, 0.5) if math.isfinite(margin_rev) else math.nan,
                 (0.2 if eps_accel > 0 else -0.2) if math.isfinite(eps_accel) else math.nan]
        valid = [p for p in parts if isinstance(p, float) and math.isfinite(p)]
        raw = float(np.mean(valid)) if len(valid) >= 2 else math.nan
        passed = math.isfinite(raw) and raw > 0.05
        rows[t] = {"raw": raw, "passed": bool(passed), "confidence": len(valid) / 4}
    return _frame(rows)


SCREEN_REGISTRY = {
    "piotroski": screen_piotroski,
    "magic_formula": screen_magic_formula,
    "acquirers_multiple": screen_acquirers_multiple,
    "value_composite": screen_value_composite,
    "quality": screen_quality,
    "garp": screen_garp,
    "fcf_owner_earnings": screen_fcf_owner_earnings,
    "shareholder_yield": screen_shareholder_yield,
    "financial_strength": screen_financial_strength,
    "fundamental_momentum": screen_fundamental_momentum,
}


def run_all_screens(records: dict[str, FundamentalRecord]) -> dict[str, pd.DataFrame]:
    return {name: fn(records) for name, fn in SCREEN_REGISTRY.items()}
