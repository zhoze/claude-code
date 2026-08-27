# Panel backtest — Magic Screener v2.4

A cross-sectional walk-forward test of the v2.4 technical entry model. Single-ticker
backtests yield 5–30 trades, which is far too few to distinguish signal from noise;
this harness pools a universe so the result means something.

> **Result: the entry model shows no demonstrable timing edge.** Across 865 trades
> on 36 symbols over 6.6 years it underperformed random entry into the same names,
> at every parameter setting tested. Details below.

## Running it

```bash
python3 fetch.py     # daily OHLCV -> data/*.csv   (Nasdaq public endpoint, no key)
python3 tape.py      # pass 1: signal tape per ticker -> tapes/*.json  (~15 min, 4 cores)
python3 report.py    # pass 2: panel stats, calibration, null tests
```

`tape.py` is the only expensive step. It records, for every evaluation day `T`,
exactly what the engine knew at `T` close (score, setup, coverage, ATR%). All
downstream analysis runs off the tape in milliseconds, so barrier variants,
calibration and null tests are cheap to re-run.

`report.py` starts with a **fidelity check** asserting the tape-driven simulation
reproduces `backtest_entry_model`'s own loop trade-for-trade. It matches to
machine precision on KO, AAPL and XOM. If you change the engine, re-check this
before trusting anything else.

## Setup

| | |
|---|---|
| Universe | Dow 30 constituents + INTC, PFE, T, F, PYPL, MRNA, XOM; SPY as benchmark |
| Period | 2020-01-14 .. 2026-08-26 (6.6y, after a 260-bar warmup on 2019 data) |
| Rule | EOD setup at `T` close, execute at `T+1` open, 20-day horizon, non-overlapping |
| Barriers | 3.0×ATR target / 2.0×ATR stop, measured at the signal date |
| Costs | 5 bps slippage + 1 bp commission per side |
| Signal days | 59,085 across the panel |

The universe is a published list rather than a hand-pick, but it is **still
survivorship-biased**: it is *today's* membership. WBA is a case in point — it was
dropped from the fetch because it has since been taken private. That bias flatters
the strategy, so it does not rescue the negative result below.

HON is excluded: a −51% unadjusted spin-off gap on 2026-06-29 re-bases the whole
series. Splits are correctly adjusted (verified against AAPL 4:1, NVDA 4:1 and
10:1, WMT 3:1). Prices are split- but not dividend-adjusted, which is the right
basis for technical signals — it avoids injecting future dividend information
into past price levels.

## Results

```
BARRIER MODE: ATR (3.0x target / 2.0x stop)      PCT (+5% / -3%, the v2.3 default)
  Trades                    865                    1075
  Expectancy / trade     +0.494%                 -0.112%
  95% CI (cluster boot)  [-0.044%, +1.096%]      [-0.357%, +0.101%]
  Win rate                 45.5%                   38.6%
  Target/Stop/Neither  34.1/47.9/18.0%         34.2/59.0/6.8%
  Profit factor            1.203                   0.942
  Symbols profitable       18/36                   16/36
```

The CI is a **cluster bootstrap resampling whole symbols**, not individual trades —
trades within one ticker share a price path and are not independent draws. It
includes zero.

### The score does not rank outcomes

Pooled over all 59,085 signal days, forward 20-day return by score bucket:

```
 bucket       n   avg fwd  positive
    <50   29976   +1.184%     54.8%
  50-59   10184   +0.756%     52.2%
  60-69   11134   +1.109%     55.0%
  70-79    5945   +1.264%     52.7%
  80-89    1530   +0.953%     53.7%
 90-100     316   +2.356%     52.8%

Pearson corr(score, forward 20d return) = -0.0152
```

Non-monotone, and the lowest bucket beats three of the five above it. The
correlation is indistinguishable from zero.

### Random-timing null

Matched trade count per symbol, random entry dates, same ATR barriers, same costs:

```
  Strategy expectancy        +0.494%
  Random-entry null mean     +0.711%   (5th..95th pct: +0.361% .. +1.066%)
  Strategy sits at the 17th percentile  ->  one-sided p = 0.83
```

Random entry into the same names over the same period did **better**. Sensitivity
across `min_score` ∈ {70, 75, 80, 85} × target/stop ∈ {3/2, 2/2, 4/2} — twelve
combinations — puts the strategy below the null mean in **all twelve**, percentiles
ranging 5th to 47th. Never above the median.

### What the edge actually was

```
  All trades                     n=865  +0.494%
  excluding MRNA                 n=832  +0.336%
  excluding MRNA, NVDA           n=778  +0.108%
  5% trimmed mean                       +0.172%
```

Two names carry it. And by entry year the strategy simply tracks market direction:

```
  2020 +1.429%   2021 +0.479%   2022 -1.192%   2023 +0.088%
  2024 +0.775%   2025 -0.086%   2026 +1.036%
```

2022 is the only negative SPY year in the window and the worst strategy year.
Meanwhile SPY buy-and-hold returned +133.9% (13.71%/yr) over the same period while
the strategy is in the market roughly a quarter of the time.

### The one real signal

Selection lift on a plain 20-day hold is **positive**: signal days average +1.161%
forward vs +0.983% for all days (+0.178%). So the entry conditions do carry a
little information — but the 2×ATR stop converts it into a loss. The screener
selects extended, high-volatility breakout conditions where an adverse 2-ATR
excursion arrives before a favourable 3-ATR one. The exit rule, not the entry
model, is where the money goes.

## Caveats

- Large-cap US only. Breakout/RVOL logic is arguably aimed at more speculative
  names; this says little about small/mid caps.
- One macro regime (2020–2026), one asset class, long-only.
- Survivorship bias in the universe, flattering the strategy.
- No multiple-testing correction is applied to the parameter grid — the grid is
  reported as a robustness check, not as a search for a winning setting.
