"""Stage 3 — run_fundamental_screens: the 20 vendored screens + family-balanced consensus.

The screens run unmodified on the universe-filtered snapshot inputs. The consensus
replaces the vendored count-based `consensus_top10` (spec §4.2):
  1. per screen: cross-sectional percentile over the names the screen scored;
  2. per family: mean percentile over the family's screens where the name was
     scored, requiring coverage in >= half the family's *running* screens;
  3. overall: weighted mean over families with coverage (equal weights in the
     champion), requiring >= min_families families (capped at families that ran).
"""
from __future__ import annotations

import math
import os

import numpy as np
import pandas as pd

from .. import vendored

# key, score column, higher-is-better (as in the vendored run_screens registry)
MODERN = [
    ("graham_defensive", "pe_x_pb", False),
    ("gross_profitability", "gp_score", True),
    ("operating_profitability", "operating_profitability", True),
    ("accruals", "accruals_to_assets", False),
    ("fcf_yield", "fcf_yield", True),
    ("shareholder_yield", "shareholder_yield", True),
    ("altman_z", "ev_ebit", False),
    ("ohlson_o", "value_score", True),
    ("beneish_m", "value_score", True),
    ("mohanram_g", "mohanram_g_score", True),
    ("residual_income", "ri_score", True),
    ("composite_value", "value_score", True),
    ("dividend_growth", "dividend_cagr_5y", True),
    ("low_leverage_quality", "roic_5y_avg", True),
    ("asset_growth", "asset_growth_1y", False),
    ("fundamental_signals", "fundamental_score", True),
    ("garp_peg", "peg", False),
]
LEGACY_SCORES = {"buffett_quality": ("buffett_quality_total_score", True),
                 "magic_formula": ("magic_formula_score", True),
                 "piotroski": ("piotroski_f_score", True)}


def _read(fund_dir: str, name: str, universe: set[str], lib) -> pd.DataFrame:
    df = lib.drop_non_equity(pd.read_csv(os.path.join(fund_dir, name)))
    return df[df["ticker"].isin(universe)].reset_index(drop=True)


def run_screens(cfg, fund_dir: str, universe: list[str]) -> dict:
    """{key: {"full": ranked DataFrame | None, "score", "higher", "skip": str | None}}."""
    v = vendored.fundamental()
    lib = v.screen_lib
    uni = set(universe)
    joined = lib.load_joined(fund_dir)
    joined = joined[joined["ticker"].isin(uni)].reset_index(drop=True)
    p = cfg.strategy["fundamental"]["piotroski"]

    runners = {
        "buffett_quality": lambda: v.screen_buffett_quality.calculate_buffett_quality_score(
            _read(fund_dir, "buffett_quality_input.csv", uni, lib), top=None),
        "magic_formula": lambda: v.screen_magic_formula.calculate_magic_formula(
            _read(fund_dir, "magic_formula_input.csv", uni, lib), top=None),
        "piotroski": lambda: v.screen_piotroski_f_score.screen_piotroski_value(
            _read(fund_dir, "piotroski_input.csv", uni, lib),
            max_price_to_book_quantile=p["pb_quantile"], min_f_score=p["min_f_score"],
            top=None),
    }
    scores = dict(LEGACY_SCORES)
    for key, score, higher in MODERN:
        fn = getattr(getattr(v, f"screen_{key}"), f"screen_{key}")
        runners[key] = (lambda f: lambda: f(joined.copy(), top=None))(fn)
        scores[key] = (score, higher)

    out = {}
    for key in sorted(runners):
        score, higher = scores[key]
        entry = {"full": None, "score": score, "higher": higher, "skip": None}
        try:
            full = runners[key]()
            entry["full"] = full.drop_duplicates("ticker").reset_index(drop=True)
        except lib.MissingInputError as e:
            entry["skip"] = str(e)
        except (ValueError, KeyError) as e:
            entry["skip"] = f"error: {e}"
        out[key] = entry
    return out


def family_consensus(cfg, results: dict) -> tuple[pd.DataFrame, dict]:
    fcfg = cfg.strategy["fundamental"]
    lib = vendored.fundamental().screen_lib
    pct = {}
    for key, r in results.items():
        full = r["full"]
        if full is None or full.empty or r["score"] not in full.columns:
            continue
        s = pd.to_numeric(full.set_index("ticker")[r["score"]], errors="coerce").dropna()
        if len(s):
            pct[key] = lib.pct_rank(s, higher_is_better=r["higher"])
    fam_scores, fam_info = {}, {}
    for fam, keys in fcfg["families"].items():
        ran = [k for k in keys if k in pct]
        fam_info[fam] = {"screens": keys, "ran": ran}
        if not ran:
            continue
        m = pd.DataFrame({k: pct[k] for k in ran})
        need = math.ceil(len(ran) / 2)
        fam_scores[fam] = m.mean(axis=1).where(m.notna().sum(axis=1) >= need)
    fam_df = pd.DataFrame(fam_scores)
    if fam_df.empty:
        return pd.DataFrame(), fam_info
    weights = pd.Series({f: float(fcfg["family_weights"].get(f, 1.0)) for f in fam_df.columns})
    have = fam_df.notna()
    consensus = (fam_df.fillna(0).mul(weights).sum(axis=1)
                 / have.mul(weights).sum(axis=1).replace(0, np.nan))
    min_fam = min(int(fcfg["min_families"]), len(fam_df.columns))
    out = fam_df.round(4).add_prefix("fam_")
    out["n_families"] = have.sum(axis=1)
    out["fund_consensus"] = consensus.round(4)
    out = out[out["n_families"] >= min_fam].dropna(subset=["fund_consensus"])
    out = out.rename_axis("ticker").reset_index()
    out = out.sort_values(["fund_consensus", "ticker"], ascending=[False, True])
    out.insert(0, "fund_rank", np.arange(1, len(out) + 1))
    return out.reset_index(drop=True), fam_info


def run_fundamental_screens(cfg, fund_dir: str, universe: list[str]) -> dict:
    results = run_screens(cfg, fund_dir, universe)
    cons, fam_info = family_consensus(cfg, results)
    n = int(cfg.strategy["fundamental"]["n_candidates"])
    families_ran = [f for f, i in fam_info.items() if i["ran"]]
    warnings = []
    if len(families_ran) < len(fam_info):
        warnings.append(f"fundamental families with no running screen: "
                        f"{sorted(set(fam_info) - set(families_ran))} — consensus requires "
                        f">= {min(cfg.strategy['fundamental']['min_families'], len(families_ran))}"
                        f" of {len(families_ran)} families")
    per_screen = {k: ({"status": "ran", "n": int(len(r["full"]))} if r["full"] is not None
                      else {"status": "skipped", "reason": r["skip"]})
                  for k, r in results.items()}
    return {"per_screen": per_screen, "families": fam_info, "consensus": cons,
            "candidates": cons.head(n)["ticker"].tolist(), "warnings": warnings}
