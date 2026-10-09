"""Report generator: top3.json (spec §11) and its deterministic Markdown rendering.

Every number in the Markdown is read from the JSON (invariant I1). The narrative slots
stay empty in v0.1, because no LLM is in the production path yet.
"""
from __future__ import annotations

import json
import os

DISCLAIMER = ("Decision-support research output — not investment advice. No order "
              "execution pathway exists in this system.")


def _pct(x, nd=1):
    return "—" if x is None else f"{x * 100:.{nd}f}%"


def render_markdown(top3: dict, blend: dict | None = None) -> str:
    fc = top3["funnel_counts"]
    L = [f"# Hermes Top-3 — {top3['session']}",
         "",
         f"*Run `{top3['run_id']}` · strategy `{top3['strategy_version']}` · "
         f"snapshot `{top3['data_snapshot']}` · seed `{top3['seed']}`*",
         "",
         "**Funnel:** " + " → ".join(f"{k} {v}" for k, v in fc.items()),
         "",
         f"**Regime:** {top3['regime'].get('label', 'unknown')} "
         f"{top3['regime'].get('probs', {})} · **Exposure gate:** {top3['exposure_gate']}"
         + (" (macro_stale)" if top3.get("macro_stale") else ""),
         ""]
    if top3["status"] != "ok":
        L += [f"> **{top3['status'].upper()}** — {top3.get('status_reason', '')}", ""]
    if top3["top3"]:
        L += ["## Today's actionable entries from the optimal book", "",
              "| # | ticker | final weight | ref price | marginal CVaR (pct of book) | "
              "basket CVaR95 21d | worst scenario class | TA pct | trigger | macro risk |",
              "|---|---|---|---|---|---|---|---|---|---|"]
        for i, e in enumerate(top3["top3"], 1):
            r, s = e["risk"], e["scores"]
            L.append(f"| {i} | **{e['ticker']}** | {_pct(e['final_weight'], 2)} | "
                     f"{e['entry']['reference_price']} | {r['marginal_cvar_pct_of_book']} | "
                     f"{r['basket_cvar95_21d_pct']} | {r['worst_scenario_class']} | "
                     f"{s['ta_consensus_pct']} | {s['trigger_source']} | "
                     f"{'—' if s['macro_event_risk'] is None else s['macro_event_risk']} |")
        L.append("")
        for e in top3["top3"]:
            fam = ", ".join(f"{k} {v}" for k, v in e["scores"]["fund_families"].items())
            size = e["size_suggestion"]
            usd = f" ≈ ${size['usd']:,.0f}" if size.get("usd") is not None else ""
            L += [f"### {e['ticker']}",
                  f"- Entry: {e['entry']['window']}, valid until {e['entry']['valid_until']}",
                  f"- Size: {_pct(size['account_fraction'], 2)} of account{usd} "
                  f"({size['note']})",
                  f"- Fundamental families: {fam}",
                  ""]
    if top3.get("all_entries") and len(top3["all_entries"]) > len(top3["top3"]):
        L += ["## Full restricted basket", "", "| ticker | final weight |", "|---|---|"]
        L += [f"| {t} | {_pct(w, 2)} |" for t, w in top3["all_entries"].items()]
        L.append("")
    L += ["## In the book but not triggered today (veto trail)", ""]
    if top3["not_triggered"]:
        L += ["| ticker | book weight | reason |", "|---|---|---|"]
        L += [f"| {n['ticker']} | {_pct(n['book_weight'], 2)} | {n['reason']} |"
              for n in top3["not_triggered"]]
    else:
        L.append("(none)")
    L.append("")
    if top3.get("relaxations"):
        L += ["## Constraint relaxations in the restricted re-solve", ""]
        L += [f"- {r}" for r in top3["relaxations"]] + [""]
    if top3["warnings"]:
        L += ["## Warnings", ""] + [f"- {w}" for w in top3["warnings"]] + [""]
    if blend and blend.get("status") == "ran":
        buy = [r for r in blend["ranked"] if r["in_buy_list"]]
        L += [f"## Parallel output — Blend screen ({blend['variant']}, {blend['hold']}d hold)",
              "",
              f"*Separate momentum + golden-cross screen; not part of the Hermes funnel. "
              f"As of {blend['as_of']}: {blend['eligible']} eligible, top {blend['cutoff']} "
              f"rank-weighted.*", "",
              "| # | ticker | score | weight | idio 12-1 | vs 200dMA | golden X | beta |",
              "|---|---|---|---|---|---|---|---|"]
        for r in buy:
            L.append(f"| {r['rank']} | {r['ticker']} | {r['score']:.3f} | {_pct(r['weight'], 2)} "
                     f"| {_pct(r['imom252_21'])} | {_pct(r['ma_1_200'])} | "
                     f"{r['golden_cross']:.3f} | {r['beta']:.2f} |")
        L += ["", f"Held-out reference: {blend['held_out_note']}.", ""]
    L += ["---", f"*{DISCLAIMER}*", ""]
    return "\n".join(L)


def write_report(reports_dir: str, session: str, top3: dict, blend: dict | None) -> dict:
    out = os.path.join(reports_dir, session)
    os.makedirs(out, exist_ok=True)
    pj = os.path.join(out, "top3.json")
    with open(pj, "w") as f:
        json.dump(top3, f, indent=2, default=str)
    pm = os.path.join(out, "report.md")
    with open(pm, "w") as f:
        f.write(render_markdown(top3, blend))
    paths = {"top3": pj, "markdown": pm}
    if blend and blend.get("ranked"):
        import pandas as pd  # noqa: PLC0415
        pb = os.path.join(out, f"blend_{blend['variant']}.csv")
        pd.DataFrame(blend["ranked"]).to_csv(pb, index=False)
        paths["blend"] = pb
    return paths
