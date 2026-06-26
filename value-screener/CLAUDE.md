# value-screener — working notes for Claude

## ALWAYS refresh before running the pre-market / pre-opening screen

Before running `preopen.py` (or `overall.py`, `prescreen.py`) **always update
`data/market_conditions.json` with fresh data first**, then run. The engines are
**offline and deterministic** — they only read the committed snapshot, so a run is
only as current as that file. Never present the screen from a stale snapshot; if
the `as_of` date isn't today, refresh it.

**When the user asks to "run the screen": refresh `market_conditions.json` to
match today's date first, then just run the screen — do not ask, do not offer
options, do not add extra steps. Refresh → run.**

### How to refresh the `macro` block
Pull the latest values, then rewrite the `macro` block with today's `as_of`,
numbers, and source links (keep the existing schema/keys):

1. **FMP MCP tools** (these work on the current plan):
   - `crypto` → `cryptocurrency-quote BTCUSD` (bitcoin)
   - `economics` → `treasury-rates` (UST 2y/10y, latest row; compute bps change)
   - Note: FMP `quote` and `commodity` are **plan-gated** (Access Denied) — use web search for those.
2. **WebSearch** for everything FMP can't return today:
   - index **futures** (S&P/Nasdaq/Dow) + **VIX/VVIX/VIX3M**, **CNN Fear & Greed**
   - **gold, silver, copper, WTI, Brent, natural gas**, **US avg gasoline** (AAA)
   - **DXY**, **MOVE**, **HY OAS** credit spreads, **equity put/call**, **10y breakeven**
   - **overnight global equities** (Nikkei, Hang Seng, Euro Stoxx 50, DAX, FTSE)
   - top market-moving **headlines** + this week's **economic calendar**
3. Set `as_of` to today, update `summary` and `_data_note`, and date/source each
   headline. For any series whose intraday delta wasn't published, carry the last
   value and say so in `_data_note` (directional, not precise).
4. Validate JSON, then run: `python3 preopen.py`.
5. Mind US market holidays (e.g. Juneteenth): if cash markets are closed, frame the
   screen as the pre-opening read for the next session and set `futures_closed`.

> Educational tool — not investment advice.
