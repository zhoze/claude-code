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
    ├── fundamentals.csv          # full-metric input (17 names, FMP-sourced)
    ├── fundamentals_yahoo.csv    # valuation-only input (9 names, Yahoo-sourced)
    └── results/
        ├── screen_results.csv         # full ranked output
        ├── top_candidates.md          # human-readable ranked report
        ├── yahoo_valuation_screen.csv # valuation-only ranking (Yahoo names)
        └── yahoo_valuation_screen.md  # valuation-only report
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

## The live screen (run 2026-06-07)

This repo ships a **real screen**, not mock data. Fundamentals (trailing-twelve-
month) and 5-year growth were pulled from the **Financial Modeling Prep** data
API (via its MCP server) for a **sector-diversified 17-name subset of the S&P
500**. (The bulk index/screener endpoints require a higher data tier, so a
representative subset was screened rather than all 503 names; the engine itself
scales to any list.)

Top of the ranking (see [`data/results/top_candidates.md`](data/results/top_candidates.md)
for the full table and per-name failed gates):

| Rank | Ticker | Score | Margin of Safety | Verdict |
|---:|:--|---:|---:|:--|
| 1 | META | 93.9 | 23.4% | **Strong Candidate** |
| 2 | MSFT | 74.8 | 11.1% | **Strong Candidate** |
| 3 | KO | 73.6 | 10.2% | **Strong Candidate** |
| 4 | JPM | 73.0 | 46.8% | Watch |
| 5 | JNJ | 63.2 | 2.7% | Watch |
| … | | | | |

**Reading the results:**
- **High quality is expensive in 2026.** Several elite businesses (GOOGL, V,
  AAPL, COST) clear the quality bar but trade at/above conservative intrinsic
  value, so they land on *Watch* — exactly the discipline Buffett preaches:
  wait for a fair price.
- **JPM** posts a huge raw margin of safety but is held at *Watch*, not *Strong*,
  because it fails the leverage/coverage core gates — a deliberate reminder that
  these industrial gates **don't fit banks** (Buffett values them on ROE/ROTCE).
- **ABBV's** wildly negative margin of safety is an artifact of depressed
  trailing EPS (one-time IPR&D charges) — a caution that screens should be paired
  with a read of *why* earnings look the way they do.

---

### Supplementary valuation-only screen (Yahoo-sourced)

Nine names that the FMP data tier could not serve (PG, MO, MRK, MA, HON, HD,
CAT, TXN, MCD) were back-filled from **finance.yahoo.com** quote pages. Yahoo's
detailed statistics pages (`/key-statistics`, `/financials`) and its JSON
`quoteSummary` API were not retrievable from this environment (HTTP 503/401), so
only headline figures — price, trailing P/E, EPS (TTM), dividend yield — are
available for them. That is enough for a **Graham-style earnings-value check**
(intrinsic value, Graham formula, margin of safety) but **not** the quality
pillar, so these names are scored on valuation only (growth defaults to a
conservative 5%) and reported separately in
[`data/results/yahoo_valuation_screen.md`](data/results/yahoo_valuation_screen.md).

Result: only **MO** shows a positive margin of safety (+20.8%) under these
conservative assumptions; the rest screen as richly valued — consistent with the
main run's "quality is expensive in 2026" finding. Their low *scores* reflect the
**absence of quality data**, not poor businesses — do not compare them directly
to the fully-scored table above.

## How the data was sourced (reproducibility)

The numbers in `data/fundamentals.csv` came from these FMP endpoints, per ticker:

- `key-metrics-ttm` — ROE, ROIC, current ratio, FCF yield, Graham Number, net-debt/EBITDA, income quality
- `metrics-ratios-ttm` — gross/net margin, debt-to-equity, interest coverage, P/E, P/B, EPS, dividend
- `financial-statement-growth` — 5-year net-income-per-share growth → capped EPS CAGR
- Price is derived as P/E × EPS (point-in-time, 2026-06-07).

Anyone with a fundamentals provider can regenerate the CSV and re-run the engine;
the scoring logic is entirely in `screener.py` + `config.json` and has no hidden
state.

---

## Limitations

- Point-in-time snapshot (2026-06-07); fundamentals change.
- TTM EPS is used as the owner-earnings base by default; true owner earnings
  (net income + D&A − *maintenance* capex) is more precise — supply an
  `owner_earnings_ps` column to use it.
- Ratio-based gates are tuned for non-financial operating companies; financials,
  REITs, and deeply cyclical businesses need sector-aware judgment.
- A screen surfaces *candidates*. It is the start of research, not the end.
