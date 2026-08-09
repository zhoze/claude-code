# DGX Quant Screener

Autonomous quantitative stock screening & portfolio selection agent, designed to run
locally on an **NVIDIA DGX Spark** (GB10 Grace-Blackwell, aarch64) every US trading day
before the market open.

The system analyzes the Russell 3000 universe and selects **0–3 stocks** (never forced)
with the strongest combined evidence across:

1. Fundamental quality (10 independent screens)
2. Cross-screen overlap
3. Machine-learning expected return (NVIDIA cuML, walk-forward validated)
4. Mean-CVaR portfolio contribution (NVIDIA cuOpt)
5. Technical setup (10 per-stock backtested strategies)
6. Options-market confirmation
7. Macro / market-regime compatibility
8. Catalyst strength, liquidity and confidence

If evidence is insufficient the output is `NO HIGH-CONFIDENCE OPPORTUNITY TODAY`.

## Architecture

```
run_daily.py                     entrypoint — 30-step daily workflow (spec §48)
quant_screener/
  config.py                      YAML config + dataclasses
  gpu.py                         RAPIDS/cuML/cuOpt detection, transparent CPU fallback
  calendar_utils.py              US exchange calendar, America/New_York timing
  pipeline.py                    orchestrator for the full daily workflow
  data/
    providers.py                 pluggable data providers (yfinance default, FMP optional)
    universe.py                  point-in-time Russell universe + liquidity filters
    fundamentals.py              point-in-time fundamentals (publication-date aware)
    prices.py                    split/dividend-adjusted price history + caching
    options.py                   options chain, liquidity + sentiment scores
    macro.py                     futures, rates, USD, energy, commodities, VIX
    integrity.py                 staleness / look-ahead guards, timestamps
    store.py                     parquet price cache + SQLite learning database
  screens/                       10 fundamental screens (spec §3) + ensemble/overlap (§4-5)
  ml/                            cuML features, models, walk-forward validation (§6-7)
  portfolio/                     scenario matrix, cuOpt Mean-CVaR, frontier, stress tests (§8-11)
  technicals/                    indicators, 10 strategies, per-stock backtests,
                                 robustness scoring, current-signal detection (§12-15)
  regime/                        market-regime detection + per-stock macro sensitivity (§16, §28)
  scoring/                       FINAL_SCORE and independent CONFIDENCE_SCORE (§37-38)
  learning/                      prediction snapshots, outcome tracking, adaptive weights,
                                 champion/challenger, model changelog (§31-35, §47)
  report/                        daily pre-market markdown report (§40-46)
scripts/
  setup_dgx_spark.sh             one-shot environment setup for DGX Spark (CUDA 13 / aarch64)
  install_systemd.sh             systemd timer to run the pipeline before each US open
tests/                           smoke tests (CPU-only, synthetic data)
storage/                         parquet cache, SQLite DB, snapshots, reports (gitignored)
```

## Install on DGX Spark

```bash
cd dgx-quant-screener
bash scripts/setup_dgx_spark.sh        # creates conda env "quant" with RAPIDS + cuOpt
conda activate quant
python run_daily.py --dry-run          # verify GPU + data plumbing
```

The setup script installs RAPIDS (cudf/cuml) and cuOpt for CUDA 13 on aarch64.
If the GPU stack is unavailable the system falls back to pandas / scikit-learn /
SciPy HiGHS automatically — results are identical, only slower.

## Configuration

Everything is driven by `config.yaml`. Key entries:

- `data.fmp_api_key_env`: set `FMP_API_KEY` in the environment to enable
  Financial Modeling Prep fundamentals (recommended — provides filing dates for
  point-in-time correctness). Without it, yfinance fundamentals are used and
  candidates get a data-confidence haircut.
- `universe.membership_csv`: point-in-time Russell membership file
  (`date,ticker,action` rows). Without it, the current IWV holdings snapshot is
  used and every backtest is flagged `SURVIVORSHIP_BIAS_WARNING`.
- `portfolio.lambda_grid`: risk-aversion sweep for the Mean-CVaR frontier.
- `run.mode`: `live` | `backtest` | `dry-run`.

## Daily run

```bash
python run_daily.py                    # full pre-market run, freezes snapshot before open
python run_daily.py --score-outcomes   # score matured past predictions (also runs automatically)
python run_daily.py --backtest 2018-01-01 2024-12-31   # walk-forward system backtest
```

Reports land in `storage/reports/YYYY-MM-DD.md`; frozen prediction snapshots in
`storage/snapshots/`; the learning database in `storage/learning.db`.

## Non-negotiable principles (spec §2, §50)

- Point-in-time only: no look-ahead, no survivorship, publication dates over period dates.
- Missing data is never invented — confidence is lowered or the candidate rejected.
- Snapshots are frozen before the open and never modified after outcomes are known.
- Model/weight changes only through validated walk-forward evidence, recorded in the
  model changelog with version bumps.
- Zero selections is a valid — and common — output.
