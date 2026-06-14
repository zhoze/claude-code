# Warren Buffett Value Investing Screener

A transparent, reproducible stock screener that scores companies against a
quantitative approximation of **Warren Buffett's value-investing approach**, and
a **live screen of S&P 500 names** run with real fundamentals.

> ⚠️ **Educational tool, not investment advice.** The thresholds here approximate
> Buffett's *quantitative* filters. His real edge — judging durable moats, honest
> management, and staying inside his circle of competence — is qualitative and
> cannot be captured by ratios. Do your own research.

---

## What it does

For each company the engine evaluates three pillars and produces a **0-100
Buffett score** plus a verdict (**Strong Candidate / Watch / Pass**):

1. **Quality gates (60 pts)** — is this a "wonderful business"? High & durable
   ROE/ROIC, fat and stable margins, conservative leverage, ample interest
   coverage, liquidity, positive free cash flow, cash-backed earnings.
2. **Intrinsic value** — a two-stage **discounted owner-earnings** model,
   cross-checked with **Benjamin Graham's formula** and the **Graham Number**.
3. **Margin of safety (40 pts)** — only a bargain if price sits well below
   intrinsic value (Graham's ≥ 25% discount), with a P/E penalty for overpaying.

It also reports **forward-return estimates** per name — *upside to intrinsic
value*, an *expected annual return* (assuming convergence to the DCF value over a
configurable horizon), and *years to a target gain* (default +35%). These are
model estimates, **not** predictions of price or timing — see §6 of
[`buffett_formula.md`](buffett_formula.md).

The full formula, every threshold, and the sourced rationale are in
**[`buffett_formula.md`](buffett_formula.md)**.

---

## Files

```
value-screener/
├── README.md            # this file
├── buffett_formula.md   # the formula, thresholds, math, and sources
├── config.json          # every threshold & weight (tunable, documented)
├── screener.py          # pure-Python scoring engine (stdlib only)
├── requirements.txt     # (optional) deps; the engine itself needs none
└── data/
    ├── universe_sp500.csv        # all 503 S&P 500 constituents (snapshot)
    ├── fundamentals.csv          # full-metric input (40 names, stockanalysis.com, 2026-06-14)
    ├── risk_notes.json           # fresh-news "why it's cheap" bear cases (dated + sourced)
    └── results/
        ├── screen_results.csv         # full ranked output
        └── top_candidates.md          # human-readable ranked report
```

---

## Quick start

No dependencies or API keys required — the engine runs on the committed CSV:

```bash
python3 screener.py --input data/fundamentals.csv
python3 screener.py --selftest          # built-in sanity checks
```

Outputs are written to `data/results/`. Tune any threshold in `config.json`
(e.g. lower `discount_rate` toward the 10-year Treasury for a more
Buffett-literal valuation) and re-run — the formula is fully transparent.

### Screen your own list

`screener.py` is universe-agnostic. Provide a CSV with the columns shown in
`data/fundamentals.csv` (header documented in the file) and point `--input` at
it. Only `symbol` plus the metrics you have are required; missing fields are
reported as "no data" rather than silently failing.

---

## The live screen (run 2026-06-14)

This repo ships a **real screen**, not mock data. Current prices and
trailing-twelve-month fundamentals for **40 sector-diversified S&P 500 companies**
were pulled from **stockanalysis.com** `/statistics/` pages on **2026-06-14**
(a single, consistent source). Bulk index endpoints weren't available, so a
40-name slice was screened rather than all 503 — the engine itself scales to any
list.

Top of the ranking (see [`data/results/top_candidates.md`](data/results/top_candidates.md)
for the full 40-name table and per-name failed gates):

| Rank | Ticker | Score | Margin of Safety | Verdict |
|---:|:--|---:|---:|:--|
| 1 | META | 96.2 | 25.8% | **Strong Candidate** |
| 2 | ADBE | 96.0 | 58.0% | **Strong Candidate** |
| 3 | MSFT | 82.0 | 16.3% | **Strong Candidate** |
| 8 | JPM | 72.8 | 42.8% | **Strong Candidate** |
| 10 | UNP | 71.8 | 9.0% | **Strong Candidate** |
| 4 | DIS | 81.8 | 42.5% | Watch |
| 5 | COP | 78.7 | 28.4% | Watch |
| 6 | AXP | 78.5 | 26.9% | Watch |
| 7 | BAC | 75.0 | 49.9% | Watch |

**Reading the results:**
- **ADBE** screens best on value — elite returns (ROIC ~60%, 89% gross margin),
  low debt, and an unusually low P/E (~12) after its 2025 sell-off give it a ~58%
  margin of safety. The "wonderful business at a fair price" pattern.
- **High quality is often expensive in 2026.** Many elite businesses (GOOGL, V,
  AAPL, KO, NVDA, MA) clear the quality bar but trade at/above conservative
  intrinsic value, so they land on *Watch* — the discipline of waiting for a fair
  price.
- **Core gates gate the verdict.** DIS, COP and AXP post high scores and large raw
  margins of safety but are held at *Watch* because a **core gate fails**: sub-15%
  ROE (DIS, COP), or — for banks (AXP) and heavily bought-back firms with negative
  book equity (MO, MCD, LOW) — ROE/leverage the industrial-style gates can't fairly
  judge. **JPM and BAC** flip on this very point: the source reports them in a
  *net-cash* position, so the leverage gate passes and JPM clears all core gates —
  read banks with sector judgment (ROE/ROTCE), not these industrial gates.
- **ABBV's** wildly negative margin of safety is an artifact of depressed trailing
  EPS (one-time IPR&D charges) — screens should be paired with a read of *why*
  earnings look the way they do.

## How the data was sourced (reproducibility)

All 40 names in `data/fundamentals.csv` were sourced from **stockanalysis.com
`/statistics/` pages on 2026-06-14** (one consistent snapshot — an earlier
version blended Financial Modeling Prep data, but FMP's data tier returns
"requires a higher plan" for many symbols, so the whole universe was moved to a
single source for freshness and comparability). One statistics page yields ROE,
ROIC, margins, debt/equity, current ratio, interest coverage, P/E, P/B, EPS,
book value, FCF yield, dividend/payout, plus the absolute figures from which
`net-debt/EBITDA` (= net debt ÷ EBITDA), `income quality` (= operating cash flow
÷ net income), and the Graham Number (= √(22.5 × EPS × BVPS)) are derived. `price`
is the live quoted price; growth uses the published EPS-growth estimate capped to
[0, 10%].

A few large, heavily-bought-back firms (MO, MCD, LOW) have **negative book equity**,
so ROE/P/B are undefined and left blank — the engine reports those gates as
"no data" rather than failing them silently, and ROIC carries the quality signal
instead. For **banks** (JPM, BAC, AXP) EBITDA, gross margin, and a clean leverage
ratio aren't meaningful; where the source reports a net-cash position the leverage
gate passes, and JPM's negative reported operating cash flow (a balance-sheet
artifact) leaves income quality blank rather than failing. Judge banks on
ROE/ROTCE, not the industrial-style gates.

Anyone with a fundamentals provider can regenerate the CSV and re-run the engine;
the scoring logic is entirely in `screener.py` + `config.json` with no hidden state.

## Fresh-news risk notes ("why it's cheap")

A low price usually reflects a real risk the ratios can't see — a moat under
attack, a credit cycle, a pending deal. `data/risk_notes.json` holds the
**qualitative bear case** per flagged ticker, each entry **dated (`as_of`) and
sourced with links**. The screener merges it in: a `risk_note` + `risk_note_date`
column in the CSV, and a **"Why it's cheap — key risks (fresh news)"** section in
the report. It's optional — delete the file and the screen still runs.

> **News goes stale — keep it fresh.** The engine is offline and does **not**
> fetch news itself; notes are gathered at screen time and stored. Refresh before
> relying on them: for each flagged ticker, pull current news (e.g. search
> *"TICKER stock <month year> valuation risks bear case"*) and rewrite that
> entry with **today's** date and live source links. The committed notes are
> dated 2026-06-14.

Example (ADBE, as of 2026-06-14): *"Down ~60% from its 2024 peak — the market is
pricing AI disruption to Adobe's creative moat (OpenAI/Canva/Figma/Google), a
slowdown to ~10% growth, and a CEO transition — not weak fundamentals."* The
screen flags the bargain; the note is the reason to dig deeper before buying.

---

## Limitations

- Point-in-time snapshot (2026-06-14); fundamentals change.
- TTM EPS is used as the owner-earnings base by default; true owner earnings
  (net income + D&A − *maintenance* capex) is more precise — supply an
  `owner_earnings_ps` column to use it.
- Ratio-based gates are tuned for non-financial operating companies; financials,
  REITs, and deeply cyclical businesses need sector-aware judgment.
- A screen surfaces *candidates*. It is the start of research, not the end.
