# 20 fundamental stock screens for the Russell 1000

Twenty standalone, pandas-based fundamental screens — each grounded in a
published formula from the quantitative-finance / accounting-anomalies
literature (the classics behind the fundamental-analysis strands of
[arXiv q-fin](https://arxiv.org/archive/q-fin), e.g.
[arXiv:1906.05327](https://arxiv.org/abs/1906.05327),
[arXiv:1711.04837](https://arxiv.org/abs/1711.04837)) — plus the plumbing to
run them all on the **Russell 1000** and aggregate a consensus top 10.

Every screen implements its literature formula **exactly** (no proxy
formulas). Screens whose inputs are not yet in the committed snapshot report
*awaiting data refresh* until `build_screen_inputs.py` runs with `FMP_KEY`
and writes `inputs/extended_input.csv`.

## The roster

| # | Script | Style | Ranking logic | Source | Runs on committed data |
|---|--------|-------|---------------|--------|:---:|
| 1 | `screen_piotroski_f_score.py` | Value + strength | 9 binary signals on low-P/B names | Piotroski (2000) | ✔ |
| 2 | `screen_magic_formula.py` | Quality + value | rank(EBIT/EV) + rank(EBIT/capital) | Greenblatt (2006) | ✔ |
| 3 | `screen_buffett_quality.py` | Quality compounder | weighted 0–100 quality/growth/valuation | Buffett/Hagstrom | ✔ |
| 4 | `screen_graham_defensive.py` | Deep value | Graham's 7 defensive criteria, rank PE×P/B | Graham (1973) ch. 14 | needs refresh |
| 5 | `screen_gross_profitability.py` | Profitability + value | GP/A percentile + 1/P-B percentile | Novy-Marx (2013) | ✔ |
| 6 | `screen_operating_profitability.py` | Profitability | (gross profit − SG&A − interest)/book equity | Fama & French (2015) RMW | needs refresh |
| 7 | `screen_accruals.py` | Earnings quality | (NI − OCF)/assets, lowest first | Sloan (1996) | ✔ |
| 8 | `screen_fcf_yield.py` | Cash-flow value | FCF/market cap, ROIC>0 guard | O'Shaughnessy | ✔ |
| 9 | `screen_shareholder_yield.py` | Payout | (dividends + net buybacks + debt paydown)/mktcap | Boudoukh et al. (2007) | needs refresh |
| 10 | `screen_altman_z.py` | Safety + value | Z > 2.99 safe zone, rank EV/EBIT | Altman (1968) | needs refresh |
| 11 | `screen_ohlson_o.py` | Safety + value | lowest-distress O-score quintile, rank value | Ohlson (1980) | needs refresh |
| 12 | `screen_beneish_m.py` | Earnings quality + value | drop M > −1.78 manipulator flags, rank value | Beneish (1999) | needs refresh |
| 13 | `screen_mohanram_g.py` | Growth quality | G-score vs sector medians in high-P/B quintile | Mohanram (2005) | needs refresh |
| 14 | `screen_residual_income.py` | Economic profit | ROIC − cost of capital, × EV/EBIT percentile | Ohlson (1995); Lee et al. (1999) | ✔ |
| 15 | `screen_composite_value.py` | Value composite | mean pctile of 1/PE, 1/EV-EBIT, 1/P-B, FCF yield | O'Shaughnessy | ✔ |
| 16 | `screen_dividend_growth.py` | Dividend growth | 5y record + CAGR>0 + payout<60% + OCF cover ≥2 | QMJ payout arm | needs refresh |
| 17 | `screen_low_leverage_quality.py` | Conservative quality | D/E≤0.5, coverage≥10, CR≥1.5; rank ROIC | van Vliet & Blitz (2018) | ✔ |
| 18 | `screen_asset_growth.py` | Conservative investment | 1y asset growth, lowest first, NI>0 | Cooper, Gulen & Schill (2008) | ✔ |
| 19 | `screen_fundamental_signals.py` | Fundamental momentum | 6 YoY signals (inventory, receivables, margins…) | Lev & Thiagarajan (1993) | needs refresh |
| 20 | `screen_garp_peg.py` | GARP | PEG = PE/(100×EPS CAGR), growth & ROE ≥10% | Lynch (1989) | ✔ |

The three original scripts (1–3) are used **as provided** (not modified). The
17 new screens share `screen_lib.py` (input joining, percentile ranking,
winsorizing, the non-equity guard, CLI, selftest frame).

## Pipeline

```
build_screen_inputs.py ──FMP /stable, 5y statements──▶ inputs/*.csv  (4 CSVs + as_of.txt)
                                                          │
run_screens.py ──all 20 screens + consensus──▶ results/<screen>_top.csv
                                               results/consensus_top10.csv
                                               results/SUMMARY.md
```

- **`build_screen_inputs.py`** — discovers the universe (largest ~1,000 US
  common stocks by market cap = Russell 1000 proxy; FMP has no Russell
  constituent endpoint on this plan) and writes the four input CSVs. Reads the
  API key from the `FMP_KEY` env var — **never hardcode or commit the key.**
- **`run_screens.py`** — runs all 20 screens, writes `results/*_top.csv`,
  aggregates the consensus (top-10 appearance count + average cross-sectional
  percentile, requiring eligibility in ≥ half the screens that ran) and
  regenerates `results/SUMMARY.md`.
- **`demo_build.py` / `demo_data.py`** — a small in-session live sample used to
  demonstrate the pipeline without the bulk API key.

## Usage

```bash
pip install pandas

# Full Russell 1000 (needs an FMP key with statement access):
FMP_KEY=your_key python3 build_screen_inputs.py        # → inputs/*.csv (incl. extended)
python3 run_screens.py --top 10                        # → results/*.csv + SUMMARY.md

# Sanity-check every screen on the built-in synthetic frame (no key needed):
python3 run_screens.py --selftest

# Run a subset:
python3 run_screens.py --only composite_value,garp_peg

# Each screen is also a standalone CLI:
python3 screen_composite_value.py --input inputs --top 10
python3 screen_magic_formula.py inputs/magic_formula_input.csv --top 50
```

## Run it in CI (no key in your shell)

The valid FMP key lives in the GitHub **`screen`** environment. Trigger
`.github/workflows/russell1000-screens.yml` (Actions → "Russell 1000
fundamental screens" → Run workflow) to rebuild all four input CSVs and run
all 20 screens on the full Russell 1000; results are committed back to
`screens/results/`. The first refresh also activates the nine
*awaiting data refresh* screens.

## Latest results

See **[`results/SUMMARY.md`](results/SUMMARY.md)** for the most recent run:
the consensus top 10, each screen's top 10 with its citation, and the list of
screens still awaiting the extended data refresh.

---
*Educational screens, not investment advice. Data is a point-in-time snapshot.*
