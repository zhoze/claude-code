"""DAILY QUANTITATIVE PRE-MARKET REPORT (spec §40-46) rendered as markdown."""

from __future__ import annotations

import datetime as dt
import math
from typing import Any


def _fmt(v, pct=False, nd=2) -> str:
    if v is None or (isinstance(v, float) and not math.isfinite(v)):
        return "n/a"
    if pct:
        return f"{v * 100:.{nd}f}%"
    if isinstance(v, float):
        return f"{v:,.{nd}f}"
    return str(v)


DASHBOARD_ROWS = [
    ("S&P Futures", "es_futures"), ("Nasdaq Futures", "nq_futures"),
    ("Russell Futures", "rty_futures"), ("Dow Futures", "ym_futures"),
    ("VIX", "vix"), ("VIX/VIX3M", "vix_term_ratio"),
    ("US 2Y", "ust2y"), ("US 10Y", "ust10y"), ("US 30Y", "ust30y"),
    ("DXY", "dxy"), ("EUR/USD", "eurusd"), ("WTI", "wti"), ("Brent", "brent"),
    ("Nat Gas", "natgas"), ("Gold", "gold"), ("Copper", "copper"),
    ("HY-IG credit", "credit_hy_vs_ig"),
]


def render_report(ctx: dict[str, Any]) -> str:
    L: list[str] = []
    add = L.append
    add("# DAILY QUANTITATIVE PRE-MARKET REPORT\n")
    add(f"- **DATE:** {ctx['date']}")
    add(f"- **RUN TIME:** {ctx['run_time']}")
    add(f"- **US MARKET OPEN:** {ctx['market_open']}")
    add(f"- **DATA CUTOFF:** {ctx['data_cutoff']}")
    add(f"- **MODEL VERSION:** {ctx['model_version']}")
    add(f"- **COMPUTE BACKEND:** {ctx['backend']}")
    add(f"\n## MARKET REGIME: **{ctx['regime']['regime']}** "
        f"(score {ctx['regime']['score']:.0f})\n")
    for k, v in ctx["regime"].get("sub_regimes", {}).items():
        add(f"- {k}: {v}")
    for n in ctx["regime"].get("notes", []):
        add(f"- note: {n}")

    add("\n## MARKET DASHBOARD\n")
    add("| Instrument | Value | Change | Signal |")
    add("|---|---|---|---|")
    dash = ctx["dashboard"]
    for label, key in DASHBOARD_ROWS:
        d = dash.get(key)
        if not d:
            continue
        chg = f"{d['change_pct']:+.2f}%" if d.get("change_pct") is not None and math.isfinite(d["change_pct"] or math.nan) else "n/a"
        add(f"| {label} | {_fmt(d.get('value'))} | {chg} | {d.get('signal', '')} |")

    gm = ctx.get("global_markets") or {}
    if gm:
        add("\n### Global overnight\n")
        add("| Market | Value | Change |")
        add("|---|---|---|")
        for name, d in gm.items():
            chg = f"{d['change_pct']:+.2f}%" if d.get("change_pct") is not None and math.isfinite(d["change_pct"] or math.nan) else "n/a"
            add(f"| {name} | {_fmt(d.get('value'))} | {chg} |")

    add("\n## IMPORTANT EVENTS TODAY\n")
    cal = ctx.get("calendar") or []
    if cal:
        add("| Time | Event | Expected | Previous | Actual | Importance |")
        add("|---|---|---|---|---|---|")
        for e in cal:
            add(f"| {e.get('time', '')} | {e.get('event', '')} | {_fmt(e.get('expected'))} "
                f"| {_fmt(e.get('previous'))} | {_fmt(e.get('actual'))} "
                f"| {e.get('importance', '')} |")
    else:
        add("_Economic calendar unavailable this run (no data source configured). "
            "Treated as unknown — confidence adjusted, events NOT assumed absent._")

    add("\n## FUNDAMENTAL SCREEN RESULTS\n")
    add("| Ticker | Company | Sector | Screens Passed | Fundamental Score | Strongest Screens |")
    add("|---|---|---|---|---|---|")
    for row in ctx.get("screen_results", []):
        add(f"| {row['ticker']} | {row.get('company', '')} | {row.get('sector', '')} "
            f"| {row['overlap']}/10 | {_fmt(row['composite'], nd=1)} "
            f"| {', '.join(row.get('strongest', []))} |")

    add("\n## FINALIST ANALYSIS\n")
    for c in ctx.get("finalists", []):
        add(f"### {c['TICKER']}\n")
        add(f"- Fundamental overlap: **{c['FUNDAMENTAL_OVERLAP']}/10** ({c.get('OVERLAP_TIER', '')})")
        add(f"- Fundamental score: **{_fmt(c['FUNDAMENTAL_SCORE'], nd=1)}/100**")
        add(f"- ML predicted 5D return: {_fmt(c.get('ML_EXPECTED_RETURN_5D'), pct=True)}")
        add(f"- ML predicted 20D return: {_fmt(c.get('ML_EXPECTED_RETURN_20D'), pct=True)}")
        add(f"- Probability positive (5D): {_fmt(c.get('PROBABILITY_POSITIVE_5D'), pct=True, nd=1)}")
        add(f"- CVaR 95% (daily): {_fmt(c.get('CVAR_95'), pct=True)}")
        add(f"- Mean-CVaR suggested weight: {_fmt(c.get('MEAN_CVAR_WEIGHT'), pct=True, nd=1)}")
        add(f"- Best technical model: {c.get('BEST_TECHNICAL_MODEL', 'n/a')} "
            f"(robustness {_fmt(c.get('TECHNICAL_ROBUSTNESS'), nd=0)}/100)")
        add(f"- Current technical signal: **{c.get('TECHNICAL_SIGNAL', 'NEUTRAL')}**")
        add(f"- Macro impact: {c.get('MACRO_ASSESSMENT', 'NEUTRAL')}")
        add(f"- Options liquidity: {_fmt(c.get('OPTIONS_LIQUIDITY'), nd=0)}/100; "
            f"sentiment {_fmt(c.get('OPTIONS_SENTIMENT'), nd=0)}")
        add(f"- Event risk: {'; '.join(c.get('EVENT_FLAGS', [])) or 'none flagged'}")
        add(f"- **Final Score: {_fmt(c.get('FINAL_SCORE'), nd=1)}** | "
            f"**Confidence: {_fmt(c.get('CONFIDENCE'), nd=1)}**")
        if c.get("GATE_FAILURES"):
            add(f"- Not selectable: fails minimum requirements -> {', '.join(c['GATE_FAILURES'])}")
        add("")

    add("\n## FINAL SELECTION\n")
    sel = ctx.get("selections", [])
    if not sel:
        add("## **NO HIGH-CONFIDENCE OPPORTUNITY TODAY**\n")
        add("_Evidence across fundamentals, ML, Mean-CVaR, technicals, options and macro "
            "did not align on any candidate. Standards are not lowered to fill the table "
            "(spec §50)._")
    else:
        add("| Rank | Ticker | Company | Fund. Overlap | Fund. Score | Exp. Return (5D) "
            "| CVaR 95 | Technical Signal | Macro | Options | Final Score | Confidence |")
        add("|---|---|---|---|---|---|---|---|---|---|---|---|")
        for i, c in enumerate(sel, 1):
            add(f"| {i} | {c['TICKER']} | {c.get('COMPANY', '')} "
                f"| {c['FUNDAMENTAL_OVERLAP']}/10 | {_fmt(c['FUNDAMENTAL_SCORE'], nd=0)} "
                f"| {_fmt(c.get('ML_EXPECTED_RETURN_5D'), pct=True)} "
                f"| {_fmt(c.get('CVAR_95'), pct=True)} | {c.get('TECHNICAL_SIGNAL')} "
                f"| {c.get('MACRO_ASSESSMENT')} | {_fmt(c.get('OPTIONS_LIQUIDITY'), nd=0)} "
                f"| {_fmt(c.get('FINAL_SCORE'), nd=1)} | {_fmt(c.get('CONFIDENCE'), nd=1)} |")

        add("\n## ENTRY ANALYSIS\n")
        for c in sel:
            lv = c.get("LEVELS", {})
            add(f"### {c['TICKER']}\n")
            add(f"- Current/premarket price: {_fmt(c.get('CURRENT_PRICE'))}")
            add(f"- Entry zone: {lv.get('entry_zone')}")
            add(f"- Support: {_fmt(lv.get('support'))} | Resistance: {_fmt(lv.get('resistance'))}")
            add(f"- ATR: {_fmt(lv.get('ATR'))}")
            add(f"- Technical invalidation: {_fmt(lv.get('invalidation'))}")
            add(f"- Expected 5-day range: {lv.get('expected_range_5d')}")
            add(f"- Expected 20-day range: {lv.get('expected_range_20d')}")
            add(f"- Next major catalyst / earnings: {c.get('NEXT_EARNINGS', 'unknown')}")
            add("\n**WHY SELECTED**\n")
            for i, r in enumerate(c.get("WHY_SELECTED", [])[:5], 1):
                add(f"{i}. {r}")
            add(f"\n**BIGGEST RISK:** {c.get('BIGGEST_RISK', 'n/a')}")
            add(f"\n**WHAT WOULD INVALIDATE THE THESIS:** {c.get('INVALIDATION_THESIS', 'n/a')}\n")

    add("\n## PORTFOLIO RECOMMENDATION (cuOpt Mean-CVaR)\n")
    port = ctx.get("portfolio")
    if port:
        add(f"_Solver: {port.get('solver')} | lambda={port.get('lam')} "
            f"| alpha={port.get('alpha')}_\n")
        add("| Ticker | Weight | Exp. Return Contribution | CVaR Contribution |")
        add("|---|---|---|---|")
        for row in port.get("positions", []):
            add(f"| {row['ticker']} | {_fmt(row['weight'], pct=True, nd=1)} "
                f"| {_fmt(row['expected_return_contribution'], pct=True)} "
                f"| {_fmt(row['cvar_contribution'], pct=True)} |")
        add(f"| **CASH** | {_fmt(port.get('cash'), pct=True, nd=1)} | — | — |")
        add(f"\n- Portfolio expected return (monthly): {_fmt(port.get('expected_return'), pct=True)}")
        add(f"- Portfolio CVaR 95% (daily): {_fmt(port.get('cvar'), pct=True)}")
        for k in ("VOLATILITY", "MAX_DRAWDOWN", "SHARPE", "SORTINO"):
            if port.get("stress_metrics", {}).get(k) is not None:
                add(f"- {k}: {_fmt(port['stress_metrics'][k], pct=k in ('VOLATILITY', 'MAX_DRAWDOWN'))}")
        st = port.get("stress_table")
        if st:
            add("\n### Stress tests (current weights vs historical crises)\n")
            add("| Scenario | Total Return | Max Drawdown | CVaR | Coverage |")
            add("|---|---|---|---|---|")
            for row in st:
                add(f"| {row['scenario']} | {_fmt(row.get('total_return'), pct=True)} "
                    f"| {_fmt(row.get('max_drawdown'), pct=True)} "
                    f"| {_fmt(row.get('cvar'), pct=True)} "
                    f"| {_fmt(row.get('coverage'), pct=True, nd=0)} |")
    else:
        add("_No portfolio constructed (no selectable candidates)._")

    add("\n## HISTORICAL SYSTEM PERFORMANCE\n")
    perf = ctx.get("system_performance") or {}
    if perf:
        add("| Window | N | Win Rate | Avg Return | Median | Alpha vs IWM | Sharpe |")
        add("|---|---|---|---|---|---|---|")
        for label, m in perf.items():
            add(f"| {label} | {m['n']} | {_fmt(m['WIN_RATE'], pct=True, nd=0)} "
                f"| {_fmt(m['AVERAGE_RETURN'], pct=True)} | {_fmt(m['MEDIAN_RETURN'], pct=True)} "
                f"| {_fmt(m.get('ALPHA_VS_IWM'), pct=True)} | {_fmt(m.get('SHARPE'))} |")
    else:
        add("_No scored history yet — learning database will populate as outcomes mature._")

    add("\n## SCREEN LEADERBOARD (out-of-sample)\n")
    lb = ctx.get("screen_leaderboard") or []
    if lb:
        add("| Rank | Fundamental Screen | OOS Alpha | IC | Hit Rate | N | Active Weight |")
        add("|---|---|---|---|---|---|---|")
        for i, row in enumerate(lb, 1):
            add(f"| {i} | {row['screen']} | {_fmt(row.get('alpha'), pct=True)} "
                f"| {_fmt(row.get('information_coefficient'), nd=3)} "
                f"| {_fmt(row.get('hit_rate'), pct=True, nd=0)} | {row.get('n_obs', 0)} "
                f"| {_fmt(row.get('weight'), nd=3)} |")
    else:
        add("_No out-of-sample screen statistics yet._")

    changelog = ctx.get("changelog") or []
    add("\n## MODEL CHANGE LOG\n")
    if changelog:
        add("| Date | Old | New | Change | Reason |")
        add("|---|---|---|---|---|")
        for row in changelog[-10:]:
            add(f"| {row['date']} | {row['old_version']} | {row['new_version']} "
                f"| {row['change']} | {row['reason']} |")
    else:
        add(f"_No model changes. Production version: {ctx['model_version']}._")

    add("\n## DATA PROVENANCE\n")
    for p in ctx.get("provenance", []):
        add(f"- `{p.get('DATA_SOURCE')}` session={p.get('MARKET_SESSION')} "
            f"freshness={_fmt(p.get('DATA_FRESHNESS_HOURS'))}h")
        for n in p.get("NOTES", []):
            add(f"  - {n}")
    add("")
    return "\n".join(L)
