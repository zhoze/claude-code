# Hermes stock-screening agent (v0.1, core pipeline)

A deterministic screening engine plus a fixed-order orchestrator that implements
the core of `hermes-agent-spec-v0.1` for the Russell 1000. **Fundamentals come
from FMP. Prices, corporate actions, options tradability and quotes come from
Massive.** The engine computes every number, and the orchestrator calls the stages
in the spec §10 order. No component can place orders. The output is a
decision-support report, not investment advice.

```
build_snapshot ─ validate (§2.4) ─ screen_universe ─ run_fundamental_screens (20, family consensus)
   ─ run_risk_layer ─ generate_scenarios ─ optimize_portfolio (Mean-CVaR frontier + MILP)
   ─ run_technical_screens (150, gate on the book) ─ macro (stub) ─ resolve_final ─ learning_log ─ report
                                         └─ blend screen (decile10) — separate parallel output
```

Funnel: ~1000 names → universe (options + ADV ≥ $10M + spread ≤ 25 bp) → 75 fundamental
candidates → 40 carried → book of 10–20 → triggered entries → **Top 3**.

## Quick start

```bash
pip install -r requirements.txt
python3 run_nightly.py --demo                      # offline synthetic market, no keys
python3 -m pytest                                  # 20 tests, ~35 s, offline

export FMP_KEY=...  MASSIVE_KEY=...                # environment only, never on disk
python3 run_nightly.py --limit 60                  # smoke run on the 60 largest names
python3 run_nightly.py                             # full nightly pass (Russell 1000 proxy)
python3 run_nightly.py --pass premarket --macro macro.json    # latest nightly session
```

Outputs: `reports/<session>/top3.json` (spec §11 schema), `report.md` and `blend_decile10.csv`.
Also written: `data/snapshots/<session>/` (immutable, with a SHA-256 manifest),
`db/learning.duckdb`, `db/artifacts/<session>/` (book, scenarios, state) and
`audit/tool_calls.jsonl` (hash-chained).

## Layout

| Path | What |
|---|---|
| `config/strategy.yaml` | the versioned champion strategy; every threshold lives here |
| `config/infra.yaml` | API hosts, paths, CPU/GPU backend policy, validation limits |
| `screens/fundamental/`, `screens/ta/`, `screens/blend/` | your screen code, **vendored byte-for-byte unmodified** |
| `hermes/vendored.py` | loads the vendored packages side by side (both use flat `screen_lib` imports) |
| `hermes/data/` | FMP + Massive clients, snapshot builder, §2.4 validation gates |
| `hermes/stages/` | one module per engine tool (universe … resolve, blend) |
| `hermes/orchestrator.py` | fixed call order, envelopes, audit log, weekly/event re-optimization |
| `hermes/learning/db.py` | DuckDB learning DB (append-only; every stage-3 name logged) |
| `hermes/report/render.py` | top3.json → Markdown (every number read from the JSON) |

## Data sources

| Need | Source | Endpoint |
|---|---|---|
| Universe (largest US common stocks) | FMP | `company-screener` |
| 5y annual statements → the 4 screen-input CSVs | FMP | `key-metrics`, `ratios`, `income/balance-sheet/cash-flow-statement`, `profile`, `quote` (via the vendored `build_record`) |
| EPS/revenue actual vs estimate + next date | FMP | `earnings` (Massive has no consensus estimates; agreed) |
| Daily OHLCV raw + split-adjusted | Massive | `/v2/aggs/ticker/{t}/range/1/day/...` (`adjusted=false/true`) |
| Total-return adjustment | Massive | `/stocks/v1/dividends` → `historical_adjustment_factor` |
| Listed options (tradability) | Massive | `/v3/reference/options/contracts?underlying_ticker=` |
| Spread | Massive | full-market snapshot NBBO. If the plan lacks quotes for ≥ 20 % of names, a 20-day Corwin–Schultz high-low estimate is used instead (`universe.spread_source`) |
| SPY + 11 SPDR sector ETFs since 2007 | Massive | aggregates (named stress windows, beta, regime) |

`adjclose` = split-adjusted close × the dividend factor. It matches the
dividend-adjusted series that `ta-screener/panel.py` back-adjusts by.

## Decisions taken (from the Q&A)

- **Scope:** core pipeline. The LLM macro agent is a stub. It reads an optional
  §5-schema JSON via `--macro`; with none, the gate is 1.0 and the run is flagged
  `macro_stale`. The backtester and champion–challenger loop are deferred. The
  `challengers` table already exists.
- **Blend screen:** a separate parallel output that does not feed the funnel. The
  default variant is `decile10`; set `blend.variant: top5` to switch.
- **Earnings data:** from FMP.
- **PERF4 document:** used as design rules only (`hermes/backend.py`):
  - CPU is always correct.
  - CuPy and cuOpt are optional.
  - AUTO picks the GPU only above a crossover threshold and only after a CUDA smoke test passes.
  - A GPU failure restarts the whole calculation on CPU.
  - Random draws are made on the host, so CPU and GPU runs consume identical draws.
  - Every LP gets a SHA-256 `fingerprint` and is cross-checked against HiGHS (‖Δw‖∞ ≤ 1e-4).
- **CPU fallback:** with no healthy GPU, scenarios drop from 100k to `scenarios.n_cpu`
  (20k), with a time limit on each MILP. The report flags this.
- **Restricted re-solve (stage 9):** caps are relaxed only as far as feasibility needs.
  The position cap becomes max(15 %, 1/|S|), and the sector and cluster caps rise in
  5 % steps. Every relaxation is listed in top3.json, and the reported CVaR is the
  restricted basket's.
- **Account size:** `optimizer.account_value_usd` (off by default). When it is set,
  the 5 %-of-ADV liquidity constraint and the dollar sizes are enabled.

## Implementation notes and known limitations

- **Fundamental consensus (§4.2):** the per-family coverage rule is ≥ half of that
  family's running screens, and a name needs ≥ 4 families. The family minimum is
  capped at the number of families that actually ran.
- **TA gate (§4.4):** the 150 screens run on the full universe panel, because the
  alpha formulas are cross-sectional. Family scores are then re-ranked within the book
  (`technical.percentile_basis: book`).
  - Vetoes: `beat_and_drop` is active on the latest row; `max_spike` fires in the most
    lottery-like decile; earnings within ±2 business days blocks entry unless the
    earnings family is a trigger source; macro event risk ≥ 0.7 delays the name.
- **Frontier:** 15 LP points from min-CVaR to max-E[r]. The selected point maximises
  E[r]/CVaR with CVaR ≤ 8 %. The final book is a MILP (cardinality 10–20, 2 % floor)
  at that E[r] target, stepping down the frontier if that target is infeasible.
- **Risk layer:**
  - Clustering is average-linkage on correlation distance.
  - The regime model is a 2-state Gaussian Markov-switching model on SPY.
  - No expected-return model is fitted in v0.1. The carried set is the top 40 by
    consensus after hard anomaly exclusions.
- **Point-in-time (§2.2):** the universe, live-quote valuations and current market caps
  are `live-only`; manifest.json records each file's classification. Statement
  acceptance dates are stored in `fundamentals_pit.csv` for future backtests.
- **Calendar:** business days are used with no NYSE holiday calendar yet. Entry
  windows and blackout counts can be off by one around holidays.
- **Re-optimization cadence:** weekly or on events (regime flip, more than 30 %
  turnover in the candidate set, a book name no longer carried). The drawdown-breach
  trigger needs realised-P&L tracking and is not implemented.
- **Testing:** the FMP/Massive clients were written against the documented APIs, but
  were not run against live keys in this environment. Run `--limit 25` first and check
  `fetch_failures.json` in the snapshot.

## GPU on DGX Spark (optional)

Install CuPy (`cupy-cuda13x`) and cuOpt for CUDA 13 / aarch64 into the same environment.
`backend.arrays: auto` and `backend.solver: auto` then switch to the GPU only above
`gpu_min_scenario_cells` and `gpu_min_lp_nnz`. Both thresholds are placeholders: set
them from a measured size sweep on the Spark. Set `cpu` to force the CPU path.
The cuOpt adapter (`solve_cuopt`) uses the `cuopt.linear_programming` data-model
API. It has not been exercised here, because no GPU is available. A failure falls
back to HiGHS and is recorded in the solver log.

## Schedule (Europe/Tallinn, spec §10)

```cron
0 1 * * 2-6  cd /path/hermes-screener && FMP_KEY=… MASSIVE_KEY=… python3 run_nightly.py
30 15 * * 1-5 cd /path/hermes-screener && python3 run_nightly.py --pass premarket --macro /path/macro.json
```

---
*Research tooling. Not investment advice.*
