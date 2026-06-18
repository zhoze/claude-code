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

## The full pipeline — trigger: **Pre-screen**

`overall.py` runs **four lenses in sequence** for one stock and blends them into
a single near-term **directional** read (0-100, 50 = neutral) plus the
**dominant driver** — the lens most likely to move the stock right now:

```
1. Pre-screen     fresh macro / news (VIX, futures, yields, oil, headlines)    -> news direction
2. Warren Buffett quality + value (margin of safety -> upward room)            -> value direction
3. Magic Elite    technical / trend (Magic score, ported to Python)           -> technical direction
4. Sentiment      retail social + analyst consensus + insider buying (1/3      -> sentiment direction
                  each), HYPE-TEMPERED for high-beta froth
5. Overall        weighted blend (config: news 25 / technical 35 / value 20 /  + dominant driver
                  sentiment 20) — renormalized over whichever lenses have data
```

The **Sentiment** lens (`sentiment.py` + `data/sentiment.json`) reads "most-liked
/ highly-rated" signals gathered from forums/portals/analyst data: Reddit &
StockTwits buzz, Strong-Buy/upside consensus, and insider purchases. Extreme
*bullish* social sentiment on a **high-beta** name is partly discounted and
flagged ⚠ (contrarian-aware) so hype doesn't inflate the score.

```bash
python3 overall.py                 # overall PRE-MARKET conditions only (no stock)
python3 overall.py --ticker ADBE   # full Pre-screen → Buffett → Magic → Overall
python3 overall.py --all           # rank ALL names (writes data/results/overall_screen.{csv,md})
python3 prescreen.py --ticker AVGO # just the Pre-screen lens
python3 overall.py --selftest      # built-in checks
```

> **Pre-screen is offline & deterministic** — it does not fetch news itself. When
> you trigger **Pre-screen**, fresh macro + per-stock news is gathered from
> reliable sources and stored, dated and sourced, in
> [`data/market_conditions.json`](data/market_conditions.json); the engines read
> that file. **Refresh it before relying on it.** Example (2026-06-15): risk-on
> rebound (VIX ~16, Dow at an ATH, oil down on a US–Iran peace deal) → ADBE
> Overall **40.6 (bearish lean), dominant driver = technical** (broken downtrend);
> AVGO Overall **35.9 (bearish lean), dominant driver = value** (priced for
> perfection). Educational only — not investment advice.

---

## Files

```
value-screener/
├── README.md            # this file
├── buffett_formula.md   # the formula, thresholds, math, and sources
├── config.json          # every threshold & weight (tunable, documented)
├── screener.py          # Buffett scoring engine (stdlib only)
├── prescreen.py         # Pre-screen: macro/news engine (offline, deterministic)
├── magic_lite.py        # compact Python port of the Magic Elite directional score
├── sentiment.py         # Sentiment lens: social + analyst + insider (hype-tempered)
├── overall.py           # pipeline orchestrator (Pre-screen→Buffett→Magic→Sentiment→Overall)
├── requirements.txt     # (optional) deps; the engine itself needs none
└── data/
    ├── universe_sp500.csv        # all 503 S&P 500 constituents (snapshot)
    ├── fundamentals.csv          # full-metric input (503 S&P 500 names, FMP /stable, 2026-06-17)
    ├── risk_notes.json           # fresh-news "why it's cheap" bear cases (dated + sourced)
    ├── market_conditions.json    # Pre-screen input: macro + per-stock news (dated + sourced)
    ├── sentiment.json            # Sentiment lens input: social/analyst/insider (dated + sourced)
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

## The live screen — full S&P 500 (run 2026-06-17)

This repo ships a **real screen** of the **entire S&P 500 (503 names)**. Prices,
TTM fundamentals, technicals (50/200-day MA, RSI, beta), analyst-consensus
ratings and price targets were pulled from the **Financial Modeling Prep
`/stable` API** (2026-06-17). All four lenses cover ~all names (Buffett value on
the ~491 with complete fundamentals, Magic on all 503, Sentiment on the 482 with
analyst coverage, macro Pre-screen for all). Earlier examples in this README came
from a curated 40-name run; see `data/results/` for the current full-index output.

**Buffett value screen — top Strong Candidates** (77 of 503; full list in
[`data/results/top_candidates.md`](data/results/top_candidates.md)):

| Ticker | Company | Score | Margin of Safety |
|:--|:--|---:|---:|
| HIG | Hartford Financial | 100 | 68.0% |
| CF | CF Industries | 100 | 66.6% |
| ZTS | Zoetis | 100 | 55.6% |
| EOG | EOG Resources | 100 | 53.6% |
| INCY | Incyte | 100 | 50.4% |
| DECK | Deckers Outdoor | 100 | 47.6% |

**Overall 4-lens directional ranking — most bullish** (full 503 in
[`data/results/overall_screen.md`](data/results/overall_screen.md)): CNC, GL,
KLAC, MTB, CRWD, UAL, CINF, DAL, JPM, BAC — names pairing a strong uptrend
(Magic) with decent value/sentiment. Most-bearish: HSY, KR, TSN, NRG, COIN,
TSLA, PLTR — broken trends and/or stretched valuations.

**Reading the results (still true at index scale):**
- The **dominant driver** is usually **technical (Magic)** — it carries the
  highest weight and saturates near 0/100 at trend extremes, so it sets most
  near-term leans; **value** dominates for the most over/undervalued names and
  **news/sentiment** for the rest.
- **Quality is broadly expensive in 2026** — many elite compounders clear the
  Buffett quality bar but fail on price (Watch), while the value Strong
  Candidates skew toward insurers, energy, and out-of-favor cyclicals.
- **Banks and negative-book-equity firms** need sector judgment (ROE/ROTCE), not
  the industrial leverage/current-ratio gates — see the caveats in `buffett_formula.md`.

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
