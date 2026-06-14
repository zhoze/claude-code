# Elite Magic Trader — Stock Screener

A self-contained, "trading terminal" style **stock screener** modeled on the
**Magic Trader® Elite** add-on for MetaStock (Save Dollar Enterprises). Per the
product's own framing, it is *"designed to identify risk on a weighted basis —
no buy or sell signals are given for live trading."* It reproduces the manual's
*believer-based signals, risk-color scheme, the **Magic Eight Dimensions℠**
weighted-risk framework, Explorations (scans), and bond-inversion macro gauge* —
as an interpretive teaching model in a single static web app with **no build
step and no API key required**.

> **Not affiliated with MetaStock or Save Dollar Enterprises.** This is an
> independent, educational re-interpretation of the concepts described in the
> Magic Trader® Elite manual, the Magic Trader® Overview, and the
> "Creating Templates Using MetaStock" guide. The real product's formulas are
> proprietary and are **not** reproduced here. Demo data only — **not
> investment advice.**

## Quick start

```bash
cd stock-screener
python3 -m http.server 8000   # then open http://localhost:8000
```

Or open `index.html` directly — it runs offline on the bundled demo universe
(~60 large-cap US tickers + the Treasury curve).

## Command-line (CLI)

`cli.js` runs the **same engine** from the terminal — no browser needed.

```bash
# from the stock-screener/ directory:
node cli.js AAPL                  # bundled demo data (offline; may be stale)
node cli.js ADBE --price 204.02 --sma50 244.89 --sma200 298.34 --rsi 29.38 \
            --beta 1.40 --perf-month -18 --perf-ytd -30 --volume 6857428 \
            --pe 11.67 --roic 59.7        # fresh data you provide
node cli.js ADBE --live           # fetch fresh data from FMP (needs FMP_API_KEY)
node cli.js --screen              # score the whole demo universe (+ bond banner)
node cli.js --list                # list demo tickers
node cli.js --help
```

Install the **`magic`** keyword globally (optional):

```bash
cd stock-screener && npm link     # then: magic ADBE --live   ·   magic --screen
```

The CLI prints the full directional read — Blue-Line territory, Magic Lines gate,
Trading Zone, Magic Candle, HTR Ribbon, the Five Ingredients and the 0–100 Magic
score — plus the **Magic Eight Dimensions℠ 8D weighted-risk** breakdown. Pass
fresh numbers via flags (or `--live`) so the read reflects current data, not the
stale demo snapshot. **Educational risk model — no buy/sell signals, not
investment advice.**

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

### Magic Eight Dimensions℠ — weighted risk

The Overview document defines the system's organizing taxonomy: the **Magic
Eight Dimensions℠ of Markets and Securities**. The **8D Risk** column rolls the
engine's signals up into a single **0–100 weighted-risk** read (higher = more
risk) — **click any row** to expand the per-dimension breakdown:

| # | Dimension | Driven by |
| --- | --- | --- |
| 1 | Dynamic Sectional Price Risk | DSPR preceptor cycle position (a–f) |
| 2 | Vertical Risk | zone extremity & distance from the 200-MA |
| 3 | Horizontal Time Risk | Ribbon colour + elapsed-time risk number |
| 4 | Health Risk | health direction vs. price territory |
| 5 | Sudden Spot Risk | turquoise / golden sudden-reversal candles |
| 6 | Trend Change Risk & Warnings | yellow / indigo / neutral warning candles |
| 7 | Special Conditional Risks | excessive / calm volatility, volume surge |
| 8 | Fundamental Risk | valuation & quality (P/E, ROIC, earnings yield) |

This weighted risk is **orthogonal to the directional Magic score** — a name can
read fully-aligned bull (Magic 100) yet carry high 8D risk because it is
extended and late-stage, which is exactly the risk-first distinction the product
is built to surface.

### Multi-timeframe: weekly trend → daily 5 ingredients

The canonical Magic Trader template stacks timeframes biggest-to-smallest
(`W-D-60m-5m`) and trades the lower-timeframe entry only in the direction of the
higher-timeframe trend. The screener models the two ends of that stack:

- **Weekly** column — the higher-timeframe trend (long-horizon Blue-Line
  territory + year-to-date momentum): Bull / Bear / Flat.
- **Daily** column — the daily Blue-Line territory, where the 5-ingredient
  alignment is evaluated.

A ⇉ glyph marks names where the two **agree** (weekly trend + daily 5-ingredient
alignment in the same direction). The **Wk→Daily Long / Short** Explorations take
only those agreements — so a daily 5/5 bull that fights a bearish weekly trend is
filtered out as counter-trend, and a strong weekly with no daily entry yet is set
aside until the daily aligns.

## Explorations (one-click scans)

Mirroring the manual's *Explorations*:

- **5 Ingredients — Bull / Bear** — full believer alignment
- **Entry Triggers** — 5/5 ingredients + volume + a confirming candle
- **Magic Lines Long** — above both lines with a green ribbon
- **Turquoise Reversal** / **Yellow Warnings** / **Golden Warnings** — candle scans
- **Volume Surge** — unusually high volume (VQua)
- **Lowest Risk** — calm volatility, neutral health
- **Deep Zones 5–6** — washed-out vs. trend
- **Risk-Adjusted Longs** — bull-confirmed names sorted by lowest 8D weighted risk
- **Wk→Daily Long / Short** — multi-timeframe: weekly trend confirms the direction, then the daily 5-ingredient alignment provides the entry

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
| `magic.js` | **Signal engine** — every Magic Trader indicator + the Magic Eight Dimensions weighted risk + bond inversions |
| `app.js` | Screener UI: filters, Explorations, sorting, color rendering, CSV, live-data hook |
| `data.js` | Bundled demo universe + Treasury yields |

## Fidelity & caveats

- The Magic Trader Elite formulas are **proprietary and unpublished**; this is a
  faithful interpretation of the manual's *rules and color logic*, computed from
  price, moving averages, RSI, momentum, volume and beta.
- Two fields the demo dataset doesn't carry — **Pure Volatility %** and
  **relative volume** — are synthesized deterministically per ticker for
  illustration.
- The real product is **multi-timeframe** — its canonical template stacks
  Weekly → Daily → 60-min → 5-min (`W-D-60m-5m`). This single-snapshot screen
  works on one timeframe; treat each higher-timeframe confirmation as out of
  scope here.
- All figures are illustrative snapshots. **Educational use only; not investment
  advice.**
