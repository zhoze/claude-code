# Elite Magic Trader — Stock Screener

A self-contained, "trading terminal" style **stock screener** modeled on the
**Magic Trader® Elite** add-on for MetaStock (Save Dollar Enterprises, 2019).
It reproduces the manual's *believer-based signals, risk-color scheme,
Explorations (scans), and bond-inversion macro gauge* — as an interpretive
teaching model in a single static web app with **no build step and no API key
required**.

> **Not affiliated with MetaStock or Save Dollar Enterprises.** This is an
> independent, educational re-interpretation of the concepts described in the
> Magic Trader® Elite manual. The real product's formulas are proprietary and
> are **not** reproduced here. Demo data only — **not investment advice.**

## Quick start

```bash
cd stock-screener
python3 -m http.server 8000   # then open http://localhost:8000
```

Or open `index.html` directly — it runs offline on the bundled demo universe
(~60 large-cap US tickers + the Treasury curve).

## The Magic Trader model

Magic Trader Elite frames the market as a contest between **bullish believers**
and **bearish believers**, and reduces eight dimensions of risk to colors,
numbers and symbols. This screener implements the manual's key components:

| Component | What it shows here |
| --- | --- |
| **Magic Blue Line** | Higher-timeframe **territory** — bull when Close is above it, bear below (proxied by the 200-day MA). |
| **Magic Lines (green/red)** | The confirmation **gate**: *long* needs Close above **both** lines, *short* needs Close below both (proxied by the 50/200-day MAs). |
| **Magic Trading Zones** | 7 adaptive lines → **6 zones**; Zone 1 = top (rich), Zone 6 = bottom (washed out). |
| **Magic Candle** | The pre-entry color signal: 🟦 Turquoise (sudden bull reversal), 🟩 Bull, 🟨 Yellow (bull warning), ◇ Neutral, 🟪 Indigo (bear warning), 🟧 Golden (sudden bear reversal), 🟥 Bear. |
| **Health Warning** | Green-vs-red dominance and a black-line momentum gauge. |
| **Volatility Combinator** | VLL1 histogram vs. VLL2 blue line → call/put bias; flags **excessive** and **lowest-risk** volatility. |
| **Directional Lines & Spikes** | Concentration of believers; green/red entry spikes. |
| **Pure Health** | Strong one-sided concentration confirming a trend. |
| **HTR Ribbon** | Who controls health (green/red/black) plus the **elapsed-time risk number** (+1→+7 bull, −1→−7 bear) — risk grows the longer since the ribbon flipped. |
| **DSPR Preceptors** | Price-perception types **a–f** (codes 1–6) classifying where the cycle stands. |
| **Five Ingredients** | The core entry system — Direction + Health + Spike + Pure Health + Ribbon aligned on one bar, then confirmed by a candle, with volume > 500,000. |
| **Bond Inversions** | Yield-curve macro gauge: counts inverted maturity pairs and shows **% inverted** as a market-risk banner. |

### The "Magic" score

The **Magic** column is a 0–100 directional conviction (50 = neutral): it blends
the net Five-Ingredient alignment, the Health black-line, and Blue-Line
territory. 100 = fully aligned bull, 0 = fully aligned bear.

## Explorations (one-click scans)

Mirroring the manual's *Explorations*:

- **5 Ingredients — Bull / Bear** — full believer alignment
- **Entry Triggers** — 5/5 ingredients + volume + a confirming candle
- **Magic Lines Long** — above both lines with a green ribbon
- **Turquoise Reversal** / **Yellow Warnings** / **Golden Warnings** — candle scans
- **Volume Surge** — unusually high volume (VQua)
- **Lowest Risk** — calm volatility, neutral health
- **Deep Zones 5–6** — washed-out vs. trend

Plus filters for sector, price band (the Volume-Explorer ranges: <$5, $5–15,
$15–100, $100–200, $200–500, >$500), territory, Magic Lines gate, candle,
ribbon, DSPR type, zone range, and market cap. Click any column to sort; **⤓
Export CSV** saves the current screen.

## Connecting live data (optional)

Provide a free [Financial Modeling Prep](https://site.financialmodelingprep.com/)
key before `app.js` loads, then click **⟳ Live data**:

```html
<script>window.FMP_API_KEY = "YOUR_KEY_HERE";</script>
<script src="data.js"></script>
<script src="magic.js"></script>
<script src="app.js"></script>
```

The hook refreshes price / MAs / volume / momentum and recomputes every Magic
signal. Without a key it stays on demo data.

## Files

| File | Purpose |
| --- | --- |
| `index.html` | Layout: bond banner, filter sidebar, results table |
| `styles.css` | Dark terminal theme + the Magic risk-color palette |
| `magic.js` | **Signal engine** — computes every Magic Trader indicator per stock + bond inversions |
| `app.js` | Screener UI: filters, Explorations, sorting, color rendering, CSV, live-data hook |
| `data.js` | Bundled demo universe + Treasury yields |

## Fidelity & caveats

- The Magic Trader Elite formulas are **proprietary and unpublished**; this is a
  faithful interpretation of the manual's *rules and color logic*, computed from
  price, moving averages, RSI, momentum, volume and beta.
- Two fields the demo dataset doesn't carry — **Pure Volatility %** and
  **relative volume** — are synthesized deterministically per ticker for
  illustration.
- All figures are illustrative snapshots. **Educational use only; not investment
  advice.**
