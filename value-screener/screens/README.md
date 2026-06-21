# Three classic value screens — Buffett Quality · Magic Formula · Piotroski F-Score

Three standalone, pandas-based stock screens plus the plumbing to run them on
the **Russell 1000** with fresh fundamentals.

| Script | Strategy | Key inputs |
|--------|----------|------------|
| `screen_buffett_quality.py` | Quality-compounder: 5-yr ROIC/ROE, margins, growth, balance sheet, valuation | 5-yr average fundamentals |
| `screen_magic_formula.py` | Greenblatt: rank by earnings yield (EBIT/EV) + return on capital | EV, EBIT, invested capital |
| `screen_piotroski_f_score.py` | 9-point financial-strength score on low-P/B names | t / t-1 statement line items |

These three screener scripts are used **as provided** (not modified). Everything
else here just feeds them real data and runs them.

## Pipeline

```
build_screen_inputs.py   ──pulls FMP /stable, 5y statements──▶   inputs/*.csv
                                                                    │
run_screens.py  ──runs all three screens, top-N + overlap──▶   results/*.csv + console report
```

- **`build_screen_inputs.py`** — discovers the universe (largest ~1,000 US names
  by market cap = Russell 1000 proxy; FMP has no Russell constituent endpoint on
  this plan) and writes the three input CSVs. Reads the API key from the
  `FMP_KEY` env var — **never hardcode or commit the key.**
- **`run_screens.py`** — runs the three screens, writes `results/*_top.csv`, and
  prints the top-N of each plus the overlap (names in 2+ screens).
- **`demo_build.py` / `demo_data.py`** — a small in-session live sample (built
  from FMP MCP pulls) used to demonstrate the pipeline without the bulk API key.

## Usage

```bash
pip install pandas

# Full Russell 1000 (needs an FMP key with statement access):
FMP_KEY=your_key python3 build_screen_inputs.py        # → inputs/*.csv
python3 run_screens.py --top 10                        # → results/*.csv

# Smoke test / sample:
FMP_KEY=your_key python3 build_screen_inputs.py --limit 30
python3 demo_build.py && python3 run_screens.py --top 10 --pb-quantile 0.5 --min-f-score 5

# Each screen can also be run standalone on any CSV with the required columns:
python3 screen_magic_formula.py inputs/magic_formula_input.csv --top 50
```

## Run it in CI (no key in your shell)

The valid FMP key lives in the GitHub **`screen`** environment. Trigger
`.github/workflows/russell1000-screens.yml` (Actions → "Russell 1000
three-screen run" → Run workflow) to build inputs and run all three screens on
the full Russell 1000; results are committed back to `screens/results/`.

## Latest results

See **[`results/SUMMARY.md`](results/SUMMARY.md)** for the most recent run: the
top of each screen, the overlap set, and per-stock summaries with evaluation
metrics.

---
*Educational screens, not investment advice. Data is a point-in-time snapshot.*
