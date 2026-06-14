#!/usr/bin/env python3
"""
Warren Buffett Value Investing Screener
=======================================

A transparent, stdlib-only engine that scores companies against a quantitative
approximation of Warren Buffett's value-investing approach:

  1. Quality gates  - is this a "wonderful business"? (high ROE/ROIC, fat
     margins, low debt, cash-backed earnings)
  2. Valuation      - estimate intrinsic value from a two-stage discounted
     owner-earnings model, cross-checked with Benjamin Graham's formulas.
  3. Margin of safety - only a bargain if price sits well below intrinsic value.

Each company gets a 0-100 Buffett score and a verdict (Strong Candidate /
Watch / Pass), plus the list of gates it failed.

The engine reads every threshold from config.json so the "formula" is fully
transparent and tunable. It takes a CSV of fundamentals as input, so it runs
with no API keys or network access:

    python3 screener.py --input data/fundamentals.csv
    python3 screener.py --selftest          # run built-in sanity checks

See buffett_formula.md for the math and the sourced rationale behind every
threshold. This is an educational tool, not investment advice.
"""

import argparse
import csv
import json
import math
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_CONFIG = os.path.join(HERE, "config.json")
DEFAULT_INPUT = os.path.join(HERE, "data", "fundamentals.csv")
DEFAULT_CSV_OUT = os.path.join(HERE, "data", "results", "screen_results.csv")
DEFAULT_MD_OUT = os.path.join(HERE, "data", "results", "top_candidates.md")
DEFAULT_RISK_NOTES = os.path.join(HERE, "data", "risk_notes.json")

# Columns the engine understands. Missing/blank values are treated as None and
# handled gracefully (a gate with no data is reported as "n/a", not a failure).
NUMERIC_FIELDS = [
    "price", "eps", "owner_earnings_ps", "roe", "roic", "gross_margin",
    "net_margin", "debt_to_equity", "net_debt_to_ebitda", "interest_coverage",
    "current_ratio", "pe", "pb", "book_value_ps", "fcf_yield", "graham_number",
    "dividend_yield", "payout_ratio", "income_quality", "eps_growth_5y",
]


# --------------------------------------------------------------------------- #
# Loading                                                                      #
# --------------------------------------------------------------------------- #
def load_config(path):
    with open(path) as fh:
        return json.load(fh)


def _to_float(value):
    if value is None:
        return None
    value = str(value).strip()
    if value == "" or value.lower() in ("na", "n/a", "none", "null"):
        return None
    try:
        return float(value)
    except ValueError:
        return None


def load_fundamentals(path):
    rows = []
    with open(path, newline="") as fh:
        reader = csv.DictReader(fh)
        for raw in reader:
            row = {
                "symbol": (raw.get("symbol") or "").strip().upper(),
                "name": (raw.get("name") or "").strip(),
                "sector": (raw.get("sector") or "").strip(),
            }
            for field in NUMERIC_FIELDS:
                row[field] = _to_float(raw.get(field))
            if row["symbol"]:
                rows.append(row)
    return rows


# --------------------------------------------------------------------------- #
# Intrinsic value                                                              #
# --------------------------------------------------------------------------- #
def intrinsic_value_dcf(row, cfg):
    """Two-stage discounted owner-earnings model, returning value per share.

    Stage 1: grow owner earnings per share at `g` (capped) for N years.
    Stage 2: a perpetuity growing at the terminal rate.
    Owner earnings falls back to EPS when not supplied.
    """
    v = cfg["valuation"]
    oe0 = row.get("owner_earnings_ps")
    if oe0 is None or oe0 <= 0:
        oe0 = row.get("eps")
    if oe0 is None or oe0 <= 0:
        return None  # no positive cash earnings -> cannot value as a going concern

    g = row.get("eps_growth_5y")
    g = v["default_growth"] if g is None else g
    g = max(0.0, min(g, v["growth_cap"]))

    r = v["discount_rate"]
    tg = v["terminal_growth"]
    n = int(v["projection_years"])

    pv = 0.0
    oe = oe0
    for t in range(1, n + 1):
        oe = oe0 * (1 + g) ** t
        pv += oe / (1 + r) ** t
    # Terminal value (Gordon growth) discounted back from year N.
    terminal = oe * (1 + tg) / (r - tg)
    pv += terminal / (1 + r) ** n
    return pv


def graham_formula_value(row, cfg):
    """Graham's revised intrinsic value, bond-adjusted.

        V = EPS * (8.5 + 2g) * 4.4 / Y

    g is expected annual growth in percentage points (capped), Y the current
    AAA corporate bond yield. A quick second opinion, not the primary estimate.
    """
    eps = row.get("eps")
    if eps is None or eps <= 0:
        return None
    g = row.get("eps_growth_5y")
    g = cfg["valuation"]["default_growth"] if g is None else g
    g = max(0.0, min(g, cfg["valuation"]["growth_cap"])) * 100.0
    y = cfg["valuation"]["graham_aaa_yield"] * 100.0
    return eps * (8.5 + 2 * g) * 4.4 / y


def margin_of_safety(intrinsic, price):
    if intrinsic is None or price is None or intrinsic <= 0:
        return None
    return (intrinsic - price) / intrinsic


# --------------------------------------------------------------------------- #
# Scoring                                                                      #
# --------------------------------------------------------------------------- #
def _passes(op, value, threshold):
    return value >= threshold if op == ">=" else (
        value > threshold if op == ">" else (
            value <= threshold if op == "<=" else value < threshold))


def evaluate_gate(row, gate):
    """Return (points_awarded, status) where status is 'pass'/'partial'/'fail'/'n/a'."""
    value = row.get(gate["key"])

    # Net-cash override: a company with no net debt trivially satisfies the
    # leverage and interest-coverage gates regardless of the raw ratio.
    if gate["key"] in ("net_debt_to_ebitda", "interest_coverage"):
        nde = row.get("net_debt_to_ebitda")
        if nde is not None and nde <= 0:
            return gate["points"], "pass"

    if value is None:
        return 0.0, "n/a"

    if _passes(gate["op"], value, gate["threshold"]):
        return gate["points"], "pass"
    if "partial" in gate and _passes(gate["op"], value, gate["partial"]):
        return gate["points"] / 2.0, "partial"
    return 0.0, "fail"


def quality_score(row, cfg):
    points = 0.0
    failed, statuses = [], {}
    for gate in cfg["quality_gates"]:
        awarded, status = evaluate_gate(row, gate)
        points += awarded
        statuses[gate["key"]] = status
        if status in ("fail", "n/a"):
            failed.append(gate["label"] + ("" if status == "fail" else " (no data)"))
    return points, failed, statuses


def valuation_score(row, cfg, mos):
    vs = cfg["valuation_scoring"]
    score = 0.0
    # Margin-of-safety component.
    if mos is not None:
        frac = max(0.0, min(mos / cfg["margin_of_safety_target"], 1.0))
        score += frac * vs["mos_points"]
    # P/E component - reward not overpaying.
    pe = row.get("pe")
    if pe is not None and pe > 0:
        if pe <= vs["pe_full"]:
            score += vs["pe_points"]
        elif pe < vs["pe_zero"]:
            span = vs["pe_zero"] - vs["pe_full"]
            score += vs["pe_points"] * (vs["pe_zero"] - pe) / span
    return score


def expected_return_metrics(intrinsic, price, cfg):
    """Forward-return estimates derived from the screen's own DCF.

    Assumes price converges to intrinsic value over `horizon_years`, while
    intrinsic value itself compounds at the discount rate (a standard DCF
    property). Returns (upside_to_intrinsic, expected_return_annual,
    years_to_target). These are MODEL ESTIMATES, not predictions of price or
    timing - a cheap stock can stay cheap, or the value estimate can be wrong.
    """
    er = cfg.get("expected_return", {})
    horizon = er.get("horizon_years", 5)
    target = er.get("target_gain", 0.35)
    if intrinsic is None or price is None or price <= 0 or intrinsic <= 0:
        return None, None, None
    r = cfg["valuation"]["discount_rate"]
    ratio = intrinsic / price
    upside = ratio - 1.0
    expected_annual = (1 + r) * ratio ** (1.0 / horizon) - 1.0
    years = math.log(1 + target) / math.log(1 + expected_annual) if expected_annual > 0 else None
    return upside, expected_annual, years


def score_company(row, cfg):
    q_points, failed, statuses = quality_score(row, cfg)

    iv_dcf = intrinsic_value_dcf(row, cfg)
    iv_graham = graham_formula_value(row, cfg)
    mos = margin_of_safety(iv_dcf, row.get("price"))
    upside, expected_annual, years_to_target = expected_return_metrics(
        iv_dcf, row.get("price"), cfg)

    v_points = valuation_score(row, cfg, mos)
    total = round(q_points + v_points, 1)

    # Verdict.
    core_ok = all(statuses.get(k) == "pass" for k in cfg["core_gates"])
    vd = cfg["verdict"]
    if total >= vd["strong_score"] and core_ok and (mos is not None and mos >= vd["strong_min_mos"]):
        verdict = "Strong Candidate"
    elif total >= vd["watch_score"]:
        verdict = "Watch"
    else:
        verdict = "Pass"

    return {
        "symbol": row["symbol"],
        "name": row["name"],
        "sector": row["sector"],
        "price": row.get("price"),
        "quality_score": round(q_points, 1),
        "valuation_score": round(v_points, 1),
        "buffett_score": total,
        "intrinsic_value_dcf": None if iv_dcf is None else round(iv_dcf, 2),
        "graham_value": None if iv_graham is None else round(iv_graham, 2),
        "graham_number": row.get("graham_number"),
        "margin_of_safety": None if mos is None else round(mos, 4),
        "upside_to_intrinsic": None if upside is None else round(upside, 4),
        "expected_return_annual": None if expected_annual is None else round(expected_annual, 4),
        "years_to_target": None if years_to_target is None else round(years_to_target, 1),
        "verdict": verdict,
        "failed_gates": "; ".join(failed) if failed else "",
        "risk_note_date": "",
        "risk_note": "",
    }


# --------------------------------------------------------------------------- #
# Output                                                                       #
# --------------------------------------------------------------------------- #
RESULT_COLUMNS = [
    "rank", "symbol", "name", "sector", "price", "intrinsic_value_dcf",
    "graham_value", "graham_number", "margin_of_safety", "upside_to_intrinsic",
    "expected_return_annual", "years_to_target", "quality_score",
    "valuation_score", "buffett_score", "verdict", "failed_gates",
    "risk_note_date", "risk_note",
]


def load_risk_notes(path):
    """Load fresh-news 'why it's cheap' notes (optional). Returns {ticker: note}.

    The engine is offline and does NOT fetch news itself - notes are gathered at
    screen time and stored, dated and sourced, in data/risk_notes.json. Missing
    or malformed file -> no notes (the screen still runs).
    """
    if not path or not os.path.exists(path):
        return {}
    try:
        with open(path) as fh:
            data = json.load(fh)
        return {k.upper(): v for k, v in data.get("notes", {}).items()}
    except (ValueError, OSError):
        return {}


def attach_risk_notes(results, notes):
    for r in results:
        note = notes.get(r["symbol"].upper())
        if note:
            r["risk_note"] = note.get("summary", "")
            r["risk_note_date"] = note.get("as_of", "")


def screen(rows, cfg):
    results = [score_company(r, cfg) for r in rows]
    results.sort(key=lambda x: x["buffett_score"], reverse=True)
    for i, r in enumerate(results, 1):
        r["rank"] = i
    return results


def write_csv(results, path):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=RESULT_COLUMNS, extrasaction="ignore")
        writer.writeheader()
        for r in results:
            writer.writerow(r)


def _fmt_pct(x):
    return "n/a" if x is None else f"{x * 100:.1f}%"


def _fmt_money(x):
    return "n/a" if x is None else f"${x:,.2f}"


def _fmt_years(x):
    return "n/a" if x is None else f"{x:.1f}"


def write_markdown(results, cfg, path, source_note="", notes=None):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    notes = notes or {}
    strong = [r for r in results if r["verdict"] == "Strong Candidate"]
    watch = [r for r in results if r["verdict"] == "Watch"]

    lines = []
    lines.append("# Warren Buffett Screen - Ranked Candidates\n")
    lines.append("> Educational screen, **not investment advice**. Buffett's real "
                 "edge is qualitative (durable moat, honest management, circle of "
                 "competence) and cannot be fully captured by ratios.\n")
    if source_note:
        lines.append(f"_{source_note}_\n")

    lines.append("## Scoring model\n")
    lines.append("- **Quality pillar (60 pts):** ROE, ROIC, margins, leverage, "
                 "interest coverage, liquidity, free cash flow, earnings quality.")
    lines.append("- **Valuation pillar (40 pts):** margin of safety vs. a two-stage "
                 "discounted owner-earnings intrinsic value, plus a P/E penalty for "
                 "overpaying.")
    lines.append(f"- **Verdict:** Strong Candidate >= {cfg['verdict']['strong_score']} "
                 f"(and core gates pass), Watch >= {cfg['verdict']['watch_score']}, else Pass.\n")

    er = cfg.get("expected_return", {})
    horizon = er.get("horizon_years", 5)
    tgt = int(round(er.get("target_gain", 0.35) * 100))
    lines.append("## Forward-return estimates (model, not predictions)\n")
    lines.append(f"- **Upside to value** = intrinsic ÷ price − 1 (total % to reach DCF fair value).")
    lines.append(f"- **Exp. return/yr** = annualized return *if* price converges to intrinsic value "
                 f"over {horizon} years (intrinsic itself compounds at the discount rate).")
    lines.append(f"- **Yrs to +{tgt}%** = time to a +{tgt}% gain at that expected annual return.")
    lines.append("> ⚠️ These assume the price reaches the screen's *estimated* intrinsic value — a "
                 "cheap stock can stay cheap, or the estimate can be wrong. Not a forecast of price "
                 "or timing, and not investment advice.\n")

    def table(title, group):
        lines.append(f"## {title} ({len(group)})\n")
        if not group:
            lines.append("_None in this run._\n")
            return
        lines.append(f"| Rank | Ticker | Company | Sector | Score | Price | Intrinsic (DCF) | "
                     f"Margin of Safety | Upside to value | Exp. return/yr | Yrs to +{tgt}% | Failed gates |")
        lines.append("|---:|:--|:--|:--|---:|---:|---:|---:|---:|---:|---:|:--|")
        for r in group:
            lines.append(
                f"| {r['rank']} | {r['symbol']} | {r['name']} | {r['sector']} | "
                f"{r['buffett_score']:.1f} | {_fmt_money(r['price'])} | "
                f"{_fmt_money(r['intrinsic_value_dcf'])} | {_fmt_pct(r['margin_of_safety'])} | "
                f"{_fmt_pct(r['upside_to_intrinsic'])} | {_fmt_pct(r['expected_return_annual'])} | "
                f"{_fmt_years(r['years_to_target'])} | {r['failed_gates'] or '-'} |")
        lines.append("")

    table("Strong Candidates", strong)
    table("Watch List", watch)

    lines.append("## Full ranking\n")
    lines.append(f"| Rank | Ticker | Score | MOS | Exp. return/yr | Yrs to +{tgt}% | Verdict |")
    lines.append("|---:|:--|---:|---:|---:|---:|:--|")
    for r in results:
        lines.append(
            f"| {r['rank']} | {r['symbol']} | {r['buffett_score']:.1f} | "
            f"{_fmt_pct(r['margin_of_safety'])} | {_fmt_pct(r['expected_return_annual'])} | "
            f"{_fmt_years(r['years_to_target'])} | {r['verdict']} |")
    lines.append("")

    # Fresh-news risk notes: the qualitative bear case a ratio screen can't see.
    noted = [r for r in results if notes.get(r["symbol"].upper())]
    if noted:
        lines.append("## Why it's cheap - key risks (fresh news)\n")
        lines.append("> The screen is quantitative; a low price often reflects a real "
                     "risk it can't measure (a broken moat, a credit cycle, a pending deal). "
                     "These dated, sourced notes are the bear case to weigh against the score. "
                     "**News goes stale - check the date.**\n")
        for r in noted:
            note = notes[r["symbol"].upper()]
            lines.append(f"### {r['rank']}. {r['symbol']} - {r['name']}  "
                         f"({r['verdict']}, score {r['buffett_score']:.0f}) — _as of {note.get('as_of','n/a')}_\n")
            if note.get("summary"):
                lines.append(f"{note['summary']}\n")
            for risk in note.get("key_risks", []):
                lines.append(f"- {risk}")
            srcs = note.get("sources", [])
            if srcs:
                lines.append("")
                lines.append("Sources: " + " · ".join(
                    f"[{s.get('title','link')}]({s.get('url','')})" for s in srcs))
            lines.append("")

    with open(path, "w") as fh:
        fh.write("\n".join(lines))


# --------------------------------------------------------------------------- #
# Self-test                                                                    #
# --------------------------------------------------------------------------- #
def selftest():
    cfg = load_config(DEFAULT_CONFIG)

    # A textbook wonderful-and-cheap business should pass everything.
    great = {
        "symbol": "GOOD", "name": "Good Co", "sector": "Test",
        "price": 50.0, "eps": 5.0, "owner_earnings_ps": 5.0, "roe": 0.25,
        "roic": 0.20, "gross_margin": 0.55, "net_margin": 0.22,
        "net_debt_to_ebitda": 0.5, "interest_coverage": 30.0,
        "current_ratio": 2.0, "pe": 10.0, "fcf_yield": 0.06,
        "income_quality": 1.2, "eps_growth_5y": 0.08, "graham_number": 70.0,
        "book_value_ps": None, "debt_to_equity": 0.2, "pb": None,
        "dividend_yield": 0.02, "payout_ratio": 0.3,
    }
    # A junk business should fail the core gates and score poorly.
    junk = {
        "symbol": "BAD", "name": "Bad Co", "sector": "Test",
        "price": 80.0, "eps": 1.0, "owner_earnings_ps": 0.5, "roe": 0.03,
        "roic": 0.02, "gross_margin": 0.15, "net_margin": 0.02,
        "net_debt_to_ebitda": 6.0, "interest_coverage": 1.5,
        "current_ratio": 0.7, "pe": 80.0, "fcf_yield": -0.01,
        "income_quality": 0.5, "eps_growth_5y": 0.0, "graham_number": 5.0,
        "book_value_ps": None, "debt_to_equity": 3.0, "pb": None,
        "dividend_yield": 0.0, "payout_ratio": 0.0,
    }

    results = screen([great, junk], cfg)
    by_sym = {r["symbol"]: r for r in results}

    assert by_sym["GOOD"]["verdict"] == "Strong Candidate", by_sym["GOOD"]
    assert by_sym["GOOD"]["buffett_score"] > 80, by_sym["GOOD"]
    assert by_sym["GOOD"]["margin_of_safety"] > 0, by_sym["GOOD"]
    assert by_sym["BAD"]["verdict"] == "Pass", by_sym["BAD"]
    assert by_sym["BAD"]["buffett_score"] < 30, by_sym["BAD"]

    # Forward-return metrics: the cheap GOOD has upside, a positive expected
    # return, and a finite time-to-target; the expensive BAD does not.
    assert by_sym["GOOD"]["upside_to_intrinsic"] > 0, by_sym["GOOD"]
    assert by_sym["GOOD"]["expected_return_annual"] > 0, by_sym["GOOD"]
    assert by_sym["GOOD"]["years_to_target"] is not None, by_sym["GOOD"]
    assert by_sym["BAD"]["years_to_target"] is None, by_sym["BAD"]

    # Net-cash override: negative net debt passes the leverage gate.
    pts, status = evaluate_gate({"net_debt_to_ebitda": -1.0}, cfg["quality_gates"][4])
    assert status == "pass", status

    # Graham formula sanity: EPS 5, g 8% -> 5*(8.5+16)*4.4/5.5 = 98.0
    gv = graham_formula_value({"eps": 5.0, "eps_growth_5y": 0.08}, cfg)
    assert abs(gv - 98.0) < 0.5, gv

    print("selftest: all assertions passed")
    print(f"  GOOD -> score {by_sym['GOOD']['buffett_score']}, {by_sym['GOOD']['verdict']}, "
          f"MOS {_fmt_pct(by_sym['GOOD']['margin_of_safety'])}")
    print(f"  BAD  -> score {by_sym['BAD']['buffett_score']}, {by_sym['BAD']['verdict']}")
    return 0


# --------------------------------------------------------------------------- #
# CLI                                                                          #
# --------------------------------------------------------------------------- #
def main(argv=None):
    p = argparse.ArgumentParser(description="Warren Buffett value stock screener.")
    p.add_argument("--input", default=DEFAULT_INPUT, help="Fundamentals CSV (default: data/fundamentals.csv)")
    p.add_argument("--config", default=DEFAULT_CONFIG, help="Threshold config JSON")
    p.add_argument("--csv-out", default=DEFAULT_CSV_OUT, help="Ranked results CSV output")
    p.add_argument("--md-out", default=DEFAULT_MD_OUT, help="Markdown summary output")
    p.add_argument("--source-note", default="", help="Provenance note rendered into the markdown report")
    p.add_argument("--risk-notes", default=DEFAULT_RISK_NOTES, help="Fresh-news risk-notes JSON (optional)")
    p.add_argument("--top", type=int, default=0, help="Print only the top N to stdout (0 = all)")
    p.add_argument("--selftest", action="store_true", help="Run built-in sanity checks and exit")
    args = p.parse_args(argv)

    if args.selftest:
        return selftest()

    cfg = load_config(args.config)
    rows = load_fundamentals(args.input)
    if not rows:
        print(f"No fundamentals found in {args.input}", file=sys.stderr)
        return 1

    notes = load_risk_notes(args.risk_notes)
    results = screen(rows, cfg)
    attach_risk_notes(results, notes)
    write_csv(results, args.csv_out)
    write_markdown(results, cfg, args.md_out, args.source_note, notes)

    shown = results if args.top <= 0 else results[:args.top]
    print(f"Screened {len(results)} companies. Wrote {args.csv_out} and {args.md_out}.\n")
    print(f"{'#':>3}  {'TICKER':<6} {'SCORE':>6} {'MOS':>8}  VERDICT")
    for r in shown:
        print(f"{r['rank']:>3}  {r['symbol']:<6} {r['buffett_score']:>6.1f} "
              f"{_fmt_pct(r['margin_of_safety']):>8}  {r['verdict']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
