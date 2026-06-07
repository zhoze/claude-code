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
    ├── fundamentals.csv          # full-metric input (40 names, FMP + stockanalysis.com)
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

## The live screen (run 2026-06-07)

This repo ships a **real screen**, not mock data. Trailing-twelve-month
fundamentals were pulled for **40 sector-diversified S&P 500 companies** from two
reliable sources: the **Financial Modeling Prep** API (via its MCP server, 17
names) and **stockanalysis.com** `/statistics/` pages (23 names, including the
ones FMP's data tier could not serve). Bulk index endpoints weren't available, so
a 40-name slice was screened rather than all 503 — the engine itself scales to
any list.

Top of the ranking (see [`data/results/top_candidates.md`](data/results/top_candidates.md)
for the full 40-name table and per-name failed gates):

| Rank | Ticker | Score | Margin of Safety | Verdict |
|---:|:--|---:|---:|:--|
| 1 | ADBE | 96.0 | 47.3% | **Strong Candidate** |
| 2 | META | 93.9 | 23.4% | **Strong Candidate** |
| 7 | MSFT | 74.8 | 11.1% | **Strong Candidate** |
| 8 | KO | 73.6 | 10.2% | **Strong Candidate** |
| 10 | UNP | 72.0 | 9.1% | **Strong Candidate** |
| 3 | DIS | 81.9 | 42.7% | Watch |
| 4 | AXP | 79.1 | 30.2% | Watch |
| 6 | BAC | 75.0 | 51.9% | Watch |

**Reading the results:**
- **ADBE** screens best — elite returns (ROIC ~63%, 89% gross margin), low debt,
  and an unusually low P/E (~15) after its 2025 sell-off give it a wide margin of
  safety. Exactly the "wonderful business at a fair price" pattern.
- **High quality is often expensive in 2026.** Many elite businesses (GOOGL, V,
  AAPL, COST, NVDA, MA) clear the quality bar but trade at/above conservative
  intrinsic value, so they land on *Watch* — the discipline of waiting for a fair
  price.
- **Core gates gate the verdict.** DIS, AXP, BAC and MO post high scores and large
  raw margins of safety but are held at *Watch*, not *Strong*, because a **core
  gate fails**: sub-15% ROE (DIS, COP), or — for banks (BAC, AXP) and heavily
  bought-back firms with negative book equity (MO, MCD, LOW) — ROE/leverage that
  the industrial-style gates can't fairly judge. Buffett values banks on
  ROE/ROTCE; treat those names with sector-specific judgment.
- **ABBV's** wildly negative margin of safety is an artifact of depressed trailing
  EPS (one-time IPR&D charges) — screens should be paired with a read of *why*
  earnings look the way they do.

## How the data was sourced (reproducibility)

`data/fundamentals.csv` combines two sources, both as of **2026-06-07**:

**1. Financial Modeling Prep MCP server** (17 names), per ticker:
- `key-metrics-ttm` — ROE, ROIC, current ratio, FCF yield, Graham Number, net-debt/EBITDA, income quality
- `metrics-ratios-ttm` — gross/net margin, debt-to-equity, interest coverage, P/E, P/B, EPS, dividend
- `financial-statement-growth` — 5-year net-income-per-share growth → capped EPS CAGR

**2. stockanalysis.com `/statistics/` pages** (23 names — used because FMP's data
tier returns "requires a higher plan" for many symbols). One page yields ROE,
ROIC, margins, debt/equity, current ratio, interest coverage, P/E, P/B, EPS,
book value, FCF yield, dividend/payout, plus the absolute figures from which
`net-debt/EBITDA` (= net debt ÷ EBITDA) and `income quality` (= operating cash
flow ÷ net income) are derived. Growth uses the published EPS-growth figure,
capped to [0, 10%].

For both sources, **price = P/E × EPS** (keeps the recorded P/E consistent with
price ÷ EPS, which is what the valuation logic uses). Cross-checks against
finance.yahoo.com quote pages matched to within ~0.1% (e.g. PG $146.58 vs $146.54).

A few large, heavily-bought-back firms (MO, MCD, LOW) have **negative book equity**,
so ROE/P/B are undefined and left blank — the engine reports those gates as
"no data" rather than failing them silently, and ROIC carries the quality signal
instead. Banks (BAC, AXP) lack a meaningful EBITDA/leverage ratio; their
leverage gate is treated accordingly.

Anyone with a fundamentals provider can regenerate the CSV and re-run the engine;
the scoring logic is entirely in `screener.py` + `config.json` with no hidden state.

---

## Limitations

- Point-in-time snapshot (2026-06-07); fundamentals change.
- TTM EPS is used as the owner-earnings base by default; true owner earnings
  (net income + D&A − *maintenance* capex) is more precise — supply an
  `owner_earnings_ps` column to use it.
- Ratio-based gates are tuned for non-financial operating companies; financials,
  REITs, and deeply cyclical businesses need sector-aware judgment.
- A screen surfaces *candidates*. It is the start of research, not the end.
