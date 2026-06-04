# Elite Magic Trader — Stock Screener

A self-contained, "elite trading terminal" style **stock screener** inspired by
Finviz-style screeners and Joel Greenblatt's **Magic Formula**. Filter a stock
universe by fundamentals and technicals, rank by a 0–100 **Magic Score**, and
export your screen to CSV — all in a single static web app with **no build step
and no API key required**.

![pure HTML/CSS/JS — open and go](https://img.shields.io/badge/stack-vanilla%20JS-ffc83d)

## Quick start

```bash
cd stock-screener
# any static server works; pick one:
python3 -m http.server 8000
# then open http://localhost:8000
```

Or just open `index.html` directly in a browser — it works offline against the
bundled demo dataset (~60 large-cap US tickers).

## What it does

| Feature | Detail |
| --- | --- |
| **Magic Score** | Combines *earnings yield* (cheapness) and *return on capital* (quality) into a single 0–100 rank — the Magic Formula. Higher = cheaper **and** higher quality. |
| **Fundamental filters** | Sector, market cap, price range, max P/E, profitable-only, min dividend yield, min Magic Score. |
| **Technical signals** | Price vs. 50/200-day moving averages, golden cross, RSI oversold/overbought. The **Signal** column shows ▲ bullish trend, ▽ below trend, and ⚑ RSI extreme. |
| **One-click presets** | Magic Formula, Deep Value, Momentum, Dividend, Oversold Dip, Mega Caps. |
| **Sortable table** | Click any column header to sort; click again to flip direction. |
| **CSV export** | Download the current screen for use in a spreadsheet. |
| **Search** | Filter by ticker, company name, or sector as you type. |

## Connecting live data (optional)

The screener ships with a snapshot dataset so it works offline. To pull **live
quotes** for the bundled tickers, get a free
[Financial Modeling Prep](https://site.financialmodelingprep.com/) API key and
provide it before `app.js` loads:

```html
<script>window.FMP_API_KEY = "YOUR_KEY_HERE";</script>
<script src="data.js"></script>
<script src="app.js"></script>
```

Then click **⟳ Live data** in the top bar. Without a key, the button simply
notes that it's running on demo data. The live hook refreshes price, market cap,
P/E, moving averages, and recomputes Magic Scores.

## Files

| File | Purpose |
| --- | --- |
| `index.html` | Markup and layout |
| `styles.css` | Dark "trading terminal" theme |
| `app.js` | Screening pipeline: Magic Score → filters → sort → render, plus presets, CSV export, and the live-data hook |
| `data.js` | Bundled demo universe |

## How the Magic Score is computed

1. Rank every stock by **earnings yield** (EBIT / enterprise value) — descending.
2. Rank every stock by **return on capital** (ROIC) — descending.
3. Sum the two ranks (lower combined rank = better).
4. Rescale the combined rank to **0–100**, so the single best stock scores 100.

This mirrors Greenblatt's approach of buying good businesses at bargain prices.

## Disclaimer

The bundled figures are **illustrative snapshots for demonstration only**. This
project is for educational purposes and is **not investment advice**.
