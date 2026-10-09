# ta-screener — the Top-150 Technical-Analysis Screens

150 literature-grounded technical / price-action / earnings-reaction screens for US
stocks, **ranked by probability of success x profitability**, running entirely on
daily [FMP](https://financialmodelingprep.com) OHLCV + quarterly earnings data.
The technical-side sibling of [`../value-screener/screens/`](../value-screener/screens/)
(20 fundamental screens), following the same conventions: one registry, ranked CSVs,
markdown reports, keyless CI selftests, manual FMP runs.

Every screen implements a published formula from the quantitative-finance literature
(arXiv q-fin where possible) and carries an explicit evidence rubric — the "top 150"
ordering is deterministic metadata you can audit, not a hidden backtest.

## The roster (101 + 12 + 12 + 10 + 5 + 10 = 150)

| Family | Count | Sources |
|---|---|---|
| **Formulaic alphas** `alpha_001..alpha_101` | 101 | Kakushadze (2016), *101 Formulaic Alphas*, Wilmott 84 — [arXiv:1601.00991](https://arxiv.org/abs/1601.00991). Exact formulas, stored verbatim beside each implementation. |
| **Earnings / PEAD** | 12 | SUE (Bernard & Thomas 1989), EAR (Kishore et al., SSRN 909563), SUE+EAR combo, **Beat-and-Drop** (high SUE x negative EAR), revenue surprise (Jegadeesh & Livnat), double beat, beat streak, small-cap-tilted post-announcement CAR, pre-earnings run-up (Frazzini & Lamont), surprise volatility, earnings-momentum composite ([arXiv:2009.03094](https://arxiv.org/abs/2009.03094) linearized), announcement volume shock. |
| **Momentum** | 12 | 12-1 & 6-1 (Jegadeesh & Titman), time-series momentum (Moskowitz-Ooi-Pedersen), 52w-high (George & Hwang), residual momentum (Blitz et al.), vol-scaled & Sharpe momentum ([arXiv:1904.04912](https://arxiv.org/abs/1904.04912)-inspired), multi-scale MACD (Baz et al.), acceleration, information discreteness (Da-Gurun-Warachka), sector momentum, vol-regime-filtered momentum ([arXiv:2112.08534](https://arxiv.org/abs/2112.08534) idea). |
| **Mean reversion** | 10 | 5d/weekly reversal, Avellaneda-Lee s-score vs sector ETF + PCA variant (Avellaneda & Lee 2010), RSI(2), Bollinger %B, gap fade, drawdown rebound, IBS, OU optimal threshold ([arXiv:2003.10502](https://arxiv.org/abs/2003.10502)). |
| **Low-risk** | 5 | Betting Against Beta (Frazzini & Pedersen), low vol, low idiosyncratic vol (Ang et al.), MAX avoidance (Bali et al.), downside beta. |
| **Trend indicators** | 10 | PPO, MACD cross, MACD-TIN weighted ([arXiv:2507.20202](https://arxiv.org/abs/2507.20202)), Ichimoku, OBV slope, TTM squeeze, Bollinger & Donchian breakouts, ADX trend, golden cross — feature set validated in [arXiv:2310.09903](https://arxiv.org/abs/2310.09903). |

## How the "top 150" ranking works

Each screen's `ScreenSpec` carries five rubric values in [0, 1], set from its
literature (sources cited per screen in `results/CATALOG.md`):

- `validation` — 1.0 peer-reviewed + replicated ... 0.6 arXiv-backtest-only ... 0.4 practitioner lore
- `us_applicability` — 1.0 US large-cap evidence; haircuts for small-cap-only or non-US validation
- `persistence` — post-publication survival (McLean-Pontiff decay; e.g. PEAD gets 0.3 per Martineau 2021)
- `overfit_risk` — 0.2 simple one-parameter effects ... 0.7 for the data-mined alpha family
- `perf_bucket` — published performance: 1.0 Sharpe>1.5 or alpha>10%/yr; 0.8 Sharpe 1.0-1.5; 0.6 Sharpe 0.5-1.0; 0.4 below/unreported

```
p_success     = mean(validation, us_applicability, persistence, 1 - overfit_risk)
profitability = perf_bucket x turnover_multiplier      (low 1.0 / medium 0.85 / high 0.7)
composite     = p_success x profitability              -> the catalog ordering
```

`--empirical` adds a walk-forward RankIC layer (daily cross-sectional Spearman of each
screen's score vs the 5d/20d forward return starting the *next* day) reported alongside
the composite — it never silently reorders the catalog.

## Pipeline

```
build_screen_inputs.py ── FMP /stable, ~3k calls ──▶ inputs/*.csv[.gz] + as_of.txt
                                                        │
run_screens.py ── 150 screens + consensus ──▶ results/CATALOG.md      (the ranked 150)
                                              results/SUMMARY.md      (consensus + skips)
                                              results/top_picks.csv
                                              results/consensus_*.csv
```

Consensus is **family-balanced**: per-family composite-weighted mean percentile
(ticker needs coverage in >= half the family's screens), then families averaged
equally — so the 101 alphas cannot drown the other five families. An
evidence-weighted variant is reported alongside.

## Usage

```bash
pip install pandas   # the only dependency

# Full run (needs an FMP key with historical-price + earnings access):
FMP_KEY=your_key python3 build_screen_inputs.py --target 1000
python3 run_screens.py --top 10 --empirical

# Smoke test on 25 symbols:
FMP_KEY=your_key python3 build_screen_inputs.py --limit 25
python3 run_screens.py --only alpha_001,sue_decile,mom_12_1 --top 5

# Keyless sanity checks (what CI runs):
python3 ops.py --selftest          # golden operator values
python3 panel.py --selftest        # synthetic fixture
python3 catalog.py --selftest      # the full ranked 150 table from metadata alone
python3 run_screens.py --selftest  # all 150 screens on the synthetic panel
```

## Run it in CI (no key in your shell)

The FMP key lives in the GitHub **`screen`** environment. Trigger
`.github/workflows/ta-screener-run.yml` (Actions -> "TA 150-screen run") to rebuild
`inputs/` and regenerate `results/`; both are committed back `[skip ci]`.
`ta-screener-tests.yml` runs the keyless selftests on every push/PR touching
`ta-screener/`.

## Design guarantees (and honest caveats)

- **Causal by contract**: every operator uses full-window rolling stats; scores at
  date *t* use only data dated <= *t*. The one documented exception: *scheduled*
  earnings dates (known ex ante) feed the pre-earnings run-up screen. The empirical
  IC layer skips a day between score and forward window (delay-1 convention).
- **Split safety**: `panel.load_panel` back-adjusts O/H/L/C/vwap by `adjclose/close`
  (volume divided by the factor), so gap/EAR/IBS math never sees phantom split gaps.
  Dividend ex-dates still inject yield-sized artificial gaps — accepted at daily
  granularity.
- **NaN discipline**: NaN = "cannot evaluate", never zero. Comparisons go through
  NaN-aware helpers so a missing value can't silently pick a ternary branch. SUE uses
  a std floor so a flat surprise history can't explode.
- **Known approximations** (flagged in `CATALOG.md`): IndNeutralize uses the FMP
  sector for all of the paper's group levels; `cap` uses current market cap for all
  dates; FMP earnings dates don't encode BMO/AMC, so EAR uses a robust [-1, +1]
  window; delay-0 alphas (042/048/053/054) assume same-day execution.
- **PEAD honesty**: the earnings-family rubric encodes Martineau (2021, SocArXiv
  z7k3p) — multi-day drift has been near-absent for non-microcap US stocks since
  ~2006. Beat thresholds and the small/mid-cap tilt matter; do not size on
  Bernard-Thomas-era magnitudes.
- **Overfitting warning** (arXiv:2412.15448): strong in-sample fits of indicator
  models collapse out-of-sample. That is exactly why the catalog ordering is
  evidence-metadata, the alphas carry a 0.7 overfit-risk haircut, and the IC layer is
  walk-forward-only.

---
*Educational screens, not investment advice. Data is a point-in-time snapshot.*
