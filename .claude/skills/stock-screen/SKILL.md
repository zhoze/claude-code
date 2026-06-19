---
name: stock-screen
description: >-
  Run the staged stock-screening workflow: a pre-market screen and summary, then
  pause and ask whether to proceed with the Warren Buffett value screen or the Elite
  Magic screen. If Buffett is chosen, screen the Russell 1000, take the top 10, and
  summarize; then run the Elite Magic screen on exactly those 10 names and summarize.
  Use when the user asks to "run the pre-market screen", "screen stocks", "run the
  Buffett / value screen", "run the Elite Magic screen", or invokes /stock-screen.
---

# Stock screening agent (`/stock-screen`)

A staged, interactive screening workflow that orchestrates the existing engines in
this repo. **Educational only — it gives no buy/sell signals and is not investment
advice.** Always say this once in the final output.

**Universe is ALWAYS the Russell 1000 (large + mid cap). Never screen the S&P 500.**
The committed dataset is the Russell 1000 set; if you must refresh, refresh Russell
1000 (see "Refreshing data"), never fall back to S&P 500.

## Tools & paths
- Engines live in `value-screener/` (Python, stdlib only) and `stock-screener/`
  (the Elite Magic JS engine, Node).
- Live data comes from the **FMP** and **Bigdata.com** MCP tools. Their tool names are
  prefixed per session (e.g. `mcp__<id>__quote`); load them with `ToolSearch`
  (`select:...` or keyword search) before calling. Never put an API key in the repo —
  the MCP tools are pre-authenticated; bulk refreshes use the `FMP_KEY` CI secret.
- Run all commands from the repo root unless noted; the Python scripts default their
  paths relative to `value-screener/`.

Run the stages in order. Do not skip the Stage 2 checkpoint.

---

## Stage 1 — Pre-market screen + summary

Goal: a single, dated pre-market read of the tape.

1. **Refresh the macro snapshot** that `preopen.py` reads
   (`value-screener/data/market_conditions.json`, the `macro` block). Pull fresh
   values via FMP MCP and keep the file's dated/sourced convention:
   - Equity futures / indexes & VIX → FMP `indexes` / `quote`.
   - Rates (UST 2y/10y) and breakevens → FMP `economics` / `quote`.
   - Commodities (WTI/Brent/gold/gas) → FMP `commodity`.
   - Pre-market movers → FMP `marketPerformance` (biggest gainers / losers / most active)
     and aftermarket `quote`.
   - Overnight headlines & the economic calendar → Bigdata.com `bigdata_search` and
     `bigdata_events_calendar` (one focus per call; natural-language queries; cite
     sources inline as "Source - MMM DD, YYYY").
   Update only fields you have fresh values for; every field is optional and the engine
   still runs on a partial snapshot. Set/refresh the top-level `as_of` date.
2. **Run the dashboard:**
   ```bash
   python3 value-screener/preopen.py            # human dashboard
   python3 value-screener/preopen.py --json     # machine-readable (for your summary)
   ```
3. **Present a concise Pre-market summary:** the 0–100 directional score + risk regime,
   the top drivers, notable pre-market movers, and key headlines/catalysts. Cite
   Bigdata.com sources inline and end with a short "Sources" list.

Then go to Stage 2.

---

## Stage 2 — Checkpoint (ask the user)

Use `AskUserQuestion` to ask which screen to run next. Two options:
- **Warren Buffett value screen** → Stage 3a (then chains into Elite Magic).
- **Elite Magic screen** → Stage 3b (standalone).

Wait for the answer. Do not proceed until the user chooses.

---

## Stage 3a — Warren Buffett value screen → top 10

1. **Confirm the universe is fresh Russell 1000.** Check `as_of` in
   `value-screener/data/market_conditions.json` / the dataset. If it is stale and a
   refresh is possible, refresh **Russell 1000** (see "Refreshing data"); otherwise note
   the as-of date. Never screen S&P 500.
2. **Run the Buffett engine and take the top 10:**
   ```bash
   python3 value-screener/screener.py --input value-screener/data/fundamentals.csv --top 10
   ```
   It writes `data/results/top_candidates.md` and `screen_results.csv` and prints the
   ranked top 10 by Buffett score.
3. **Summarize the Buffett top 10:** a table of rank, ticker, company, Buffett score,
   margin of safety, the key passing/failing quality gates, and verdict (Strong
   Candidate / Watch / Pass). State the Russell 1000 universe size and as-of date.
4. **Capture the 10 tickers** and continue to Stage 4 (Elite Magic on exactly those 10).

---

## Stage 3b — Elite Magic screen (chosen directly)

Run the Elite Magic engine standalone. Pick the candidate set (e.g. the pre-market
movers from Stage 1, or the Russell 1000 names with technicals in
`market_conditions.json`), score each as in Stage 4, and present the Elite Magic
summary (Magic score + 8D risk). No Buffett step in this branch.

---

## Stage 4 — Elite Magic on the Buffett top 10 + summary

For **each** of the 10 tickers from Stage 3a:

1. **Fetch fresh technicals via FMP MCP** (do NOT use `cli.js --live`; pass numbers as
   flags):
   - `technicalIndicators` → SMA 50, SMA 200, RSI 14 (daily).
   - `quote` → price, volume, beta.
   - `chart` → 1-month and YTD price performance (compute % change).
   - Optionally `pe` and `roic` (from the Buffett run / `fundamentals.csv`) for the 8D
     fundamental dimension.
2. **Score with the full JS engine:**
   ```bash
   node stock-screener/cli.js TICKER \
     --price <p> --sma50 <s50> --sma200 <s200> --rsi <rsi> --beta <b> \
     --perf-month <pm> --perf-ytd <py> --volume <v> --pe <pe> --roic <roic>
   ```
   It prints Blue-Line territory, Magic Lines gate, Trading Zone, Magic Candle, the
   Five Ingredients, the 0–100 **Magic score**, and the **Magic Eight Dimensions 8D
   weighted-risk** breakdown.
3. **Present the Elite Magic summary** of the 10: a table with Magic score, territory,
   candle, Five-Ingredient alignment, and the 8D risk number. Call out names that are
   **high-conviction (high Magic) AND low 8D risk** vs. those that are extended /
   late-stage (high Magic but high 8D risk). End with the educational disclaimer.

---

## Refreshing data (Russell 1000 only)
- Preferred: trigger the GitHub Actions workflow **"Refresh screener dataset"**
  (`workflow_dispatch`, `universe: russell1000`) — it runs `refresh_universe.py` with the
  `FMP_KEY` secret in the `screen` environment and commits the refreshed
  `data/{universe,fundamentals,market_conditions,sentiment}`.
- Local (if `FMP_KEY` is exported): `python3 value-screener/refresh_universe.py`.
- For a few tickers (e.g. Stage 4 technicals), just use the FMP MCP tools directly.
- Never write an API key to disk or echo it. Never substitute the S&P 500.
