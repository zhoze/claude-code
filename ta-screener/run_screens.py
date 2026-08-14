"""Run the 150 technical screens: ranked picks, family-balanced consensus, reports.

Flow: load the input panel (or the synthetic fixture for --selftest) -> run every
catalog screen (evidence-rank order) -> convert each score history to a ranked
cross-section -> aggregate a family-balanced consensus -> write:

    results/CATALOG.md          all 150 screens ranked by evidence composite
                                (+ per-screen top picks, + RankIC columns with --empirical)
    results/SUMMARY.md          headline consensus tables + skip lists + provenance
    results/top_picks.csv       long format: screen_key, family, rank, ticker, score, score_pct
    results/consensus_by_family.csv, results/consensus_overall.csv

Screens can end in three states, all reported, none fatal:
    ran        produced a ranked cross-section
    no-signal  ran, but no recent date reaches min coverage (conditional screens off)
    skipped    a required input is missing (MissingInputError), e.g. no earnings file

Usage:
    python3 run_screens.py                       # all 150, top 10
    python3 run_screens.py --top 15 --empirical
    python3 run_screens.py --family earnings --only sue_decile,ear_3day
    python3 run_screens.py --selftest            # synthetic panel, no key needed
"""
from __future__ import annotations

import argparse
import os
import sys
import traceback

import numpy as np
import pandas as pd

import catalog
from empirical import ic_stats
from panel import MissingInputError, Panel, load_config, load_panel, synthetic_panel
from screen_lib import to_ranked

HERE = os.path.dirname(os.path.abspath(__file__))
RESULTS = os.path.join(HERE, "results")

DISCLAIMER = ("*Educational screens ranked by published-evidence heuristics — "
              "not investment advice. Data is a point-in-time snapshot.*")


# ------------------------------------------------------------------ running

def run_all(panel: Panel, cfg: dict, only: set[str] | None = None,
            family: str | None = None, top: int = 10, empirical: bool = False):
    """Run screens; returns (rows, ranked_tables, skipped, no_signal, ic_by_key)."""
    min_cov = cfg["ranking"]["min_coverage"]
    rows = catalog.ranked(cfg)
    if family:
        rows = [r for r in rows if r[1].family == family]
    if only:
        rows = [r for r in rows if r[1].key in only]

    ranked_tables: dict[str, pd.DataFrame] = {}
    full_pct: dict[str, pd.Series] = {}
    ic_by_key: dict[str, dict] = {}
    skipped: list[tuple[str, str]] = []
    no_signal: list[str] = []
    errors: list[tuple[str, str]] = []

    for _rank, spec, _sc in rows:
        try:
            score = spec.runner(panel, cfg)
        except MissingInputError as e:
            skipped.append((spec.key, str(e)))
            continue
        except Exception:
            errors.append((spec.key, traceback.format_exc(limit=3)))
            continue
        try:
            table = to_ranked(score, panel, spec.key, top=None, min_coverage=min_cov)
        except MissingInputError:
            no_signal.append(spec.key)
            continue
        ranked_tables[spec.key] = table.head(top)
        full_pct[spec.key] = table.set_index("ticker")["score_pct"]
        if empirical:
            ic_by_key[spec.key] = ic_stats(score, panel, cfg)

    return rows, ranked_tables, full_pct, skipped, no_signal, errors, ic_by_key


# ------------------------------------------------------------------ consensus

def consensus(rows, full_pct: dict[str, pd.Series], cfg: dict):
    """Family-balanced + evidence-weighted consensus over per-screen percentiles.

    Within a family: a ticker needs output in >= half that family's ran screens;
    its family score is the composite-weighted mean of its score_pct values.
    Overall balanced = mean of family scores (>= 2 families required);
    evidence-weighted = mean of family scores weighted by family mean composite.
    """
    by_family: dict[str, list] = {}
    comp = {spec.key: sc["composite"] for _r, spec, sc in rows}
    for _r, spec, _sc in rows:
        if spec.key in full_pct:
            by_family.setdefault(spec.family, []).append(spec.key)

    fam_scores: dict[str, pd.Series] = {}
    fam_weight: dict[str, float] = {}
    for fam, keys in by_family.items():
        pct = pd.DataFrame({k: full_pct[k] for k in keys})
        wts = pd.Series({k: comp[k] for k in keys})
        need = int(np.ceil(len(keys) / 2))
        cover = pct.notna().sum(axis=1)
        w = pct.notna().mul(wts, axis=1)
        score = (pct * wts).sum(axis=1, min_count=1) / w.sum(axis=1).replace(0, np.nan)
        fam_scores[fam] = score.where(cover >= need).dropna()
        fam_weight[fam] = float(np.mean([comp[k] for k in keys]))

    fam_df = pd.DataFrame(fam_scores)
    if fam_df.empty:
        return fam_df, pd.DataFrame()
    n_fam = fam_df.notna().sum(axis=1)
    balanced = fam_df.mean(axis=1)
    wser = pd.Series(fam_weight)
    weighted = (fam_df.mul(wser).sum(axis=1, min_count=1)
                / fam_df.notna().mul(wser).sum(axis=1).replace(0, np.nan))
    overall = pd.DataFrame({
        "consensus_balanced": balanced.round(2),
        "consensus_evidence_weighted": weighted.round(2),
        "n_families": n_fam,
    })
    overall = overall[overall["n_families"] >= min(2, len(fam_scores))]
    overall = overall.sort_values(["consensus_balanced", "consensus_evidence_weighted"],
                                  ascending=False)
    return fam_df.round(2), overall


# ------------------------------------------------------------------ reports

def _md_table(df: pd.DataFrame, max_rows: int | None = None) -> str:
    d = df.head(max_rows) if max_rows else df
    cols = list(d.columns)
    lines = ["| " + " | ".join(str(c) for c in cols) + " |",
             "|" + "|".join("---" for _ in cols) + "|"]
    for _, r in d.iterrows():
        vals = []
        for c in cols:
            v = r[c]
            if isinstance(v, float):
                v = f"{v:,.2f}" if abs(v) >= 100 else f"{v:.3f}"
            vals.append(str(v))
        lines.append("| " + " | ".join(vals) + " |")
    return "\n".join(lines)


def write_catalog_md(rows, ranked_tables, skipped, no_signal, ic_by_key, panel, cfg,
                     top: int) -> str:
    skipped_keys = {k for k, _ in skipped}
    lines = [
        "# The Top-150 Technical-Analysis Screen Catalog",
        "",
        f"Snapshot: `{panel.as_of}`. Ranked by `composite = p_success x profitability` — "
        "a deterministic score encoding each screen's published evidence "
        "(see README for the rubric). The empirical RankIC columns (when present) are "
        "reported alongside and never alter the base ordering.",
        "",
        "Honesty notes: the 101 formulaic alphas share family-level rubric values — the "
        "paper (arXiv:1601.00991) reports portfolio-level, not per-alpha, performance; "
        "their identical composites are tie-broken alphabetically and by empirical IC. "
        "Alphas 042/048/053/054 are delay-0 (same-day execution). `cap`-based screens "
        "use current market cap for all dates. PEAD-family rubric encodes Martineau "
        "(2021): drift is largely dead for big US names since ~2006.",
        "",
        "## Ranked catalog",
        "",
    ]
    hdr = ["rank", "key", "family", "composite", "p_success", "profitability",
           "turnover", "arXiv", "status"]
    if ic_by_key:
        hdr[8:8] = ["ic_5d", "ic_t20d"]
    lines.append("| " + " | ".join(hdr) + " |")
    lines.append("|" + "|".join("---" for _ in hdr) + "|")
    for rank_, spec, sc in rows:
        status = ("ran" if spec.key in ranked_tables else
                  "skipped" if spec.key in skipped_keys else
                  "no-signal" if spec.key in no_signal else "error")
        cells = [str(rank_), f"`{spec.key}`", spec.family, f"{sc['composite']:.3f}",
                 f"{sc['p_success']:.2f}", f"{sc['profitability']:.2f}", spec.turnover,
                 spec.arxiv or "—", status]
        if ic_by_key:
            ic = ic_by_key.get(spec.key, {})
            i5 = ic.get("5d", {}).get("ic_mean", float("nan"))
            t20 = ic.get("20d", {}).get("ic_tstat", float("nan"))
            cells[8:8] = [f"{i5:.3f}" if i5 == i5 else "—",
                          f"{t20:.1f}" if t20 == t20 else "—"]
        lines.append("| " + " | ".join(cells) + " |")

    lines += ["", "## Per-screen top picks", ""]
    for rank_, spec, sc in rows:
        if spec.key not in ranked_tables:
            continue
        t = ranked_tables[spec.key].head(min(5, top))
        lines += [f"### {rank_}. `{spec.key}` — {spec.title}",
                  f"*{spec.citation}*" + (f" — arXiv:{spec.arxiv}" if spec.arxiv else ""),
                  ""]
        if spec.notes:
            lines += [f"Note: {spec.notes}", ""]
        show = t[["ticker", "company", spec.key, "score_pct"]].copy()
        lines += [_md_table(show), ""]
    lines += ["---", DISCLAIMER, ""]
    return "\n".join(lines)


def write_summary_md(rows, ranked_tables, fam_df, overall, skipped, no_signal, errors,
                     panel, cfg) -> str:
    ran = len(ranked_tables)
    lines = [
        "# TA 150-Screen Run Summary",
        "",
        f"Snapshot: `{panel.as_of}` — {ran} screens ran, {len(no_signal)} no-signal, "
        f"{len(skipped)} skipped, {len(errors)} errors.",
        "",
        "## Consensus — family-balanced top 10",
        "",
        "A ticker needs coverage in >= half of each contributing family's screens; "
        "family scores are composite-weighted mean percentiles; the balanced column "
        "averages families equally so the 101 alphas cannot drown the other five families.",
        "",
    ]
    if not overall.empty:
        lines += [_md_table(overall.reset_index(names="ticker"), 10), ""]
    else:
        lines += ["(no ticker met the coverage requirement)", ""]
    if not fam_df.empty:
        lines += ["## Per-family consensus (top 5 each)", ""]
        for fam in fam_df.columns:
            top5 = fam_df[fam].dropna().sort_values(ascending=False).head(5)
            if top5.empty:
                continue
            lines += [f"### {fam}", "",
                      _md_table(top5.rename("family_score").reset_index(names="ticker")), ""]
    if no_signal:
        lines += ["## No current signal (conditional screens with nothing triggered)", "",
                  ", ".join(f"`{k}`" for k in no_signal), ""]
    if skipped:
        lines += ["## Skipped (missing inputs)", ""]
        lines += [f"- `{k}`: {why}" for k, why in skipped] + [""]
    if errors:
        lines += ["## Errors", ""]
        lines += [f"- `{k}`: see console output" for k, _ in errors] + [""]
    lines += ["---", DISCLAIMER, ""]
    return "\n".join(lines)


def write_outputs(rows, ranked_tables, full_pct, skipped, no_signal, errors, ic_by_key,
                  panel, cfg, top: int, results_dir: str = RESULTS) -> dict:
    os.makedirs(results_dir, exist_ok=True)
    fam_df, overall = consensus(rows, full_pct, cfg)

    picks = []
    for _rank, spec, _sc in rows:
        t = ranked_tables.get(spec.key)
        if t is None:
            continue
        for i, r in t.iterrows():
            picks.append({"screen_key": spec.key, "family": spec.family, "rank": i + 1,
                          "ticker": r["ticker"], "score": r[spec.key],
                          "score_pct": r["score_pct"]})
    picks_df = pd.DataFrame(picks)
    picks_df.to_csv(os.path.join(results_dir, "top_picks.csv"), index=False)
    fam_df.to_csv(os.path.join(results_dir, "consensus_by_family.csv"),
                  index_label="ticker")
    overall.to_csv(os.path.join(results_dir, "consensus_overall.csv"),
                   index_label="ticker")
    with open(os.path.join(results_dir, "CATALOG.md"), "w") as f:
        f.write(write_catalog_md(rows, ranked_tables, skipped, no_signal, ic_by_key,
                                 panel, cfg, top))
    with open(os.path.join(results_dir, "SUMMARY.md"), "w") as f:
        f.write(write_summary_md(rows, ranked_tables, fam_df, overall, skipped,
                                 no_signal, errors, panel, cfg))
    return {"picks": len(picks_df), "consensus_names": len(overall)}


# ------------------------------------------------------------------ CLI

def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--top", type=int, default=None)
    ap.add_argument("--only", default=None, help="comma-separated screen keys")
    ap.add_argument("--family", default=None)
    ap.add_argument("--empirical", action="store_true")
    ap.add_argument("--input", default=None, help="inputs directory (default ./inputs)")
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args(argv)

    cfg = load_config()
    top = args.top or cfg["ranking"]["top"]
    if args.selftest:
        return selftest()

    try:
        panel = load_panel(args.input or os.path.join(HERE, "inputs"), cfg)
    except MissingInputError as e:
        print(f"{e}\nRun: FMP_KEY=... python3 build_screen_inputs.py", file=sys.stderr)
        return 1

    only = {k.strip() for k in args.only.split(",")} if args.only else None
    rows, ranked_tables, full_pct, skipped, no_signal, errors, ic_by_key = run_all(
        panel, cfg, only=only, family=args.family, top=top, empirical=args.empirical)
    for k, tb in errors:
        print(f"ERROR in {k}:\n{tb}", file=sys.stderr)
    stats = write_outputs(rows, ranked_tables, full_pct, skipped, no_signal, errors,
                          ic_by_key, panel, cfg, top)
    print(f"{len(ranked_tables)} screens ran, {len(no_signal)} no-signal, "
          f"{len(skipped)} skipped, {len(errors)} errors; "
          f"{stats['picks']} picks, {stats['consensus_names']} consensus names. "
          f"See results/CATALOG.md and results/SUMMARY.md")
    return 1 if errors else 0


# ------------------------------------------------------------------ selftest

def selftest() -> int:
    """All 150 screens end-to-end on the synthetic panel, reports written to a tmp dir."""
    import tempfile
    cfg = load_config()
    panel = synthetic_panel()
    rows, ranked_tables, full_pct, skipped, no_signal, errors, ic_by_key = run_all(
        panel, cfg, top=5, empirical=False)

    assert len(rows) == 150, f"catalog produced {len(rows)} rows"
    for k, tb in errors:
        print(f"ERROR in {k}:\n{tb}")
    assert not errors, f"{len(errors)} screens raised: {[k for k, _ in errors]}"
    assert not skipped, f"synthetic panel has every input, yet skipped: {skipped}"
    ran = len(ranked_tables)
    assert ran >= 100, f"only {ran}/150 produced ranked output on the synthetic panel"
    for k in ranked_tables:
        t = ranked_tables[k]
        assert not t.empty and t[k].notna().all()

    with tempfile.TemporaryDirectory() as td:
        stats = write_outputs(rows, ranked_tables, full_pct, skipped, no_signal, errors,
                              ic_by_key, panel, cfg, top=5, results_dir=td)
        cat = open(os.path.join(td, "CATALOG.md")).read()
        assert cat.count("| ") > 150, "CATALOG.md missing the 150-row table"
        assert os.path.exists(os.path.join(td, "SUMMARY.md"))
        assert stats["picks"] >= ran * 3

    print(f"run_screens.py selftest: OK — {ran} ran, {len(no_signal)} no-signal "
          f"(conditional screens off on synthetic data): "
          f"{', '.join(sorted(no_signal)) or 'none'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
