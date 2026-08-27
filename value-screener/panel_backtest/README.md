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

---

# 5-day horizon study → Reversal-5

`tape2.py` rebuilds the tape with the full feature set plus 5-day outcomes;
`find5.py` ranks features by information coefficient; `setup5.py` builds and
validates the composite; `search.py` is the earlier win-rate grid search.

Discovery used 2020–2023 only. 2024–2026 was held out and touched once.

## What predicts a 5-day rise

In-sample Spearman IC vs forward 5-day return (n=35,865):

```
  atrPct                      +0.0613      maxDrawdown252Pct    -0.0566
  realizedVol20AnnPct         +0.0515      sma50Slope10d        -0.0412
  downsideDeviation60AnnPct   +0.0502      volatilitySetup      -0.0395
  bollingerBandwidthPct       +0.0483      breakout55Pct        -0.0384
  gapRisk95_60Pct             +0.0459      perf60               -0.0369
                                           proximity52wPct      -0.0316
```

High own-volatility and deep drawdown predict a rise; breakouts, 52-week-high
proximity and 60-day momentum predict the opposite. **The v2.4 entry model is
built on the wrong side of every one of these at this horizon** — it rewards
breakouts and 52w proximity and explicitly penalises high ATR in
`_volatility_setup_component`.

The effect survives per-symbol demeaning (atrPct IC +0.0603 within-stock), so it
is timing rather than a standing high-beta bet, and it was positive in every
in-sample year including 2022 (spread +0.42%).

## Reversal-5, held out

`score5 = z(atrPct) − z(proximity52wPct) − z(perf60) − z(breakout55Pct)`,
per-symbol z-scores, top 5%. Net of 12bps round trip:

```
                          IN-SAMPLE 2020-23        HELD OUT 2024-26
  all days baseline       +0.102%  50.9%           +0.199%  52.1%
  Reversal-5 top 20%      +0.690%  55.5%           +0.359%  53.6%
  Reversal-5 top 10%      +1.224%  58.7%           +0.554%  53.9%
  Reversal-5 top  5%      +1.818%  61.2%           +0.818%  56.4%
  Reversal-5 top  1%      +4.970%  67.0%           +0.748%  53.2%
  v2.4 screener >=75      +0.028%  50.7%           +0.210%  49.8%
```

Random-entry control on the held-out top 5%: setup +0.818% vs random +0.206%,
**one-sided p < 0.0001**.

Note the top 1% collapses out of sample (+4.97% → +0.75%) — the extreme tail was
overfit. Top 5% is the recommended operating point, and even it retains only ~45%
of its in-sample magnitude.

## Exits: do not add a stop

Barrier pairs tuned in-sample, applied held-out. None beat a plain 5-day hold:

```
  hold 5 days (no barriers)      IS +1.818%   OOS +0.818%   <- best held-out
  target 3.0xATR / stop 2.0xATR  IS +1.808%   OOS +0.760%
  target 2.0xATR / stop 3.0xATR  IS +1.926%   OOS +0.715%   <- best in-sample
  target 1.0xATR / stop 2.0xATR  IS +1.173%   OOS +0.312%
```

Barrier outcomes here are approximated from the 5-day excursion envelope, taking
the stop first when both were touched.

## Risks

- **Falling knives.** Worst held-out trades: UNH −28.9/−23.4/−21.2/−21.0% in one
  week, INTC −19.0%. 5th percentile −7.9%. And the evidence says no stop.
- **Concentration.** 71% of held-out signals came from 5 names (UNH 24%). Few
  stocks are deeply beaten down at once in a 36-name universe. Excluding UNH the
  result *improves* to +1.032%, so it is not one lucky name — but breadth needs a
  wider universe.
- **Decay.** ~45% of in-sample magnitude survived. Expect more attrition live.
- **Crowding.** Short-horizon reversal is well documented and capacity-limited.
- Liquid US large caps, 2020–2026, long-only. The held-out period has now been
  looked at, so it is no longer a clean holdout for further tuning.

---

# 507-name re-test → Reversal-5 is a survivorship artifact

`fetch500.py` pulls the top ~520 US listings by market cap; `fast5.py` computes the
four Reversal-5 inputs in O(n) (verified **bit-exact** against
`compute_technical_features` across 59,625 rows and 7 fields, so the 80s/symbol
reference path is not needed); `wide.py` runs the tests.

## The rule generalizes across stocks

Applying the rule **unchanged** — same features, same signs, same normalizer fitted
on 2020–2023 — to the 468 names it was never derived from, held out on 2024–2026:

```
                                      n   symbols   net 5d  success
  all 507 names, top 5%           16070       319  +1.113%    57.5%
  OUT-OF-UNIVERSE only, top 5%    14948       290  +1.115%    57.5%
```

Identical. Concentration is also solved: 290 symbols instead of 5.

Realistic portfolio (rank all 507 daily, buy the best N, hold 5 days) gives
+0.606% net at top-10/day, random control p < 0.0001. Note the "top 5% pooled"
figure needs the full-period score distribution to set its threshold and is
therefore not implementable; top-N-per-day is the honest number.

## …and none of it is real

The unconditional baseline — holding **any** stock in the universe for 5 days —
slopes with how beaten down it is:

```
  proximity to 52w high        n      net 5d   annualized
  <40%                      1704     +1.780%       +142%
  40-60%                   12576     +1.120%        +74%
  60-75%                   39099     +0.493%        +28%
  75-90%                  110771     +0.340%        +18%
  >90%                    164142     +0.211%        +11%
```

That column should be flat or mildly negative — distressed stocks are riskier, not
a free +142%/yr. The gradient exists because a stock down 60% only appears in a
"today's largest 500" universe if it later recovered. The ones that kept falling
left the universe or delisted.

Reversal-5's lift lives entirely inside that contaminated slice:

```
  no drawdown restriction          lift +0.275pp over baseline   (n=6600)
  exclude names <40% of 52w high   lift +0.054pp                 (n=6008)
  exclude names <50%               lift -0.026pp                 (n=4950)
  exclude names <60%               lift -0.214pp                 (n=3654)
```

Require a stock to be within 50% of its 52-week high and the edge is gone. The
deep bucket is 0.52% of observations and just 35 symbols.

**This also retracts the 36-name result** (+0.818% held out) reported in the
previous section: the Dow 30 are the most extreme survivors available, so that
number was the same artifact measured on a smaller sample.

## What would settle it

A point-in-time universe including delisted and dropped names — CRSP, or
Sharadar/Norgate-style delisted history. Every free endpoint used here (Nasdaq,
FMP, Stooq) serves *current* listings only, so no amount of extra symbols from
them fixes this. Until that data is in place, any "buy the dip" result on this
infrastructure should be assumed contaminated.

The baseline-gradient check above is cheap and worth running against any future
mean-reversion signal before believing it.

---

# Literature bake-off → the v2.4 screen is replaced by IMOM

`fastfeat.py` computes every rule's inputs in O(n) (validated against
`compute_technical_features`: SMA/ATR/momentum/proximity/realized-vol all match to
1e-15 or exactly). `bakeoff.py` runs the comparison.

## Method

Rank the universe cross-sectionally each day, buy the top decile at the next open,
hold 20 days. Judge on **matched excess return**:

```
excess = fwd(stock) − mean(fwd of every stock that day in the SAME 52w-drawdown bucket)
```

Date matching removes market direction. Bucket matching removes the survivorship
gradient that made Reversal-5 look profitable. Significance uses **monthly
non-overlapping portfolio returns** (31 months held out), so overlapping 20-day
windows do not inflate the t-stats — the naive pooled t-stats were ~26, the honest
ones are ~3.

## Results — held out 2024-2026, 502 symbols, 789,424 obs

```
  rule                        excess/mo      t     source
  idiosyncratic mom 12-1        +2.391%   3.31     1910.13115
  cross-sectional mom 12-1      +2.350%   2.90     classic
  MA(1,200) crossover           +2.172%   3.06     1504.04254, 1811.06766
  MA(1,50) crossover            +2.018%   3.29     Brock/Lakonishok/LeBaron
  idiosyncratic mom 1m          +1.985%   3.49     1910.13115
  1-month momentum              +1.941%   3.22
  12-1 momentum, vol-scaled     +1.354%   2.61     2212.07288, 1904.04912
  short-term reversal 1w        +0.410%   2.12
  trading range break 50d       +0.264%   1.66     <- v2.4's core signal
  trading range break 200d      +0.214%   2.09     <- v2.4's core signal
  52-week-high proximity        +0.039%   0.71     <- v2.4 rewards this
  low idiosyncratic vol         -0.534%  -1.97
  time-series mom, vol-scaled   -0.633%  -1.86
  low realized vol              -0.712%  -2.25     <- v2.4 rewards this
  low beta                      -0.830%  -1.46
```

**The v2.4 model is built on the three weakest signals in the set.** Breakouts are
near-noise, 52-week-high proximity is zero, and `_volatility_setup_component`
penalises high ATR — the wrong sign. Measured directly on the same yardstick:

```
  v2.4 score >= 75 and setup fires   +0.571%/mo   t= 0.41
  v2.4 top decile of score           -0.180%/mo   t=-0.18
  IMOM on the SAME 36 names          +1.912%/mo   t= 1.94
```

Note `1504.04254`'s finding that TRB beats MA does **not** replicate here: on US
large caps 2024-2026, MA(1,200) beats TRB50 by an order of magnitude. Their result
is index-level on Chinese exchanges; this is cross-sectional stock selection.

## The amended screen (`../imom_screen.py`)

```
score = rank(idiosyncratic momentum 12-1) + rank(idiosyncratic momentum 1m)
top decile, hold 20 days, no stop
```

The two legs are rank-correlated **−0.01**, so the pair genuinely diversifies and
has the best t of any composite tried (3.56 vs 3.31 for the 12-1 leg alone).
Adding MA(1,200) (+2.085%, t=3.30) or a reversal leg (+1.898%, t=3.43) made it
worse — MA is 0.59 correlated with the 12-1 leg and adds nothing.

```
  matched excess       +2.122%/mo, t=3.56, 31 months
  random control       screen +2.169% vs random -0.001%, p < 0.0001
  raw, net of 12bps    +4.037% per 20 days, 58.8% positive, 398 symbols
  universe baseline    +1.882% per 20 days, 56.5% positive
  by year              2024 +1.94% (t=2.65)  2025 +2.64% (t=2.86)
                       2026 +1.55% (t=0.84, 7 months)
```

Survivorship check — the test Reversal-5 failed:

```
  no restriction                  +2.122%/mo  t=3.56
  exclude names <40% of 52w high  +2.122%     t=3.56
  exclude <50%                    +2.094%     t=3.55
  exclude <60%                    +2.068%     t=3.53
  exclude <75%                    +2.001%     t=3.56
```

Flat. The edge is not in the beaten-down slice, so it is not the artifact that
Reversal-5 was. (Reversal-5 went +0.275pp → −0.026pp under the same restriction.)

## Limits

- **Half the raw +4.04% is beta**, not skill: the universe baseline is +1.88%. The
  honest selection component is the +2.12% matched excess.
- **No stop, wide tail**: worst held-out pick −50.1%, 5th percentile −16.1%,
  median +2.2%. Momentum crashes are this factor's known failure mode and
  2024-2026 contained none — expect a bad one eventually.
- 2026 is 7 months and not individually significant.
- Universe is current listings. Matched excess controls the drawdown channel of
  survivorship, not every channel. A point-in-time universe is still the right fix.
- Momentum is the most crowded factor in equities; published edges decay.

---

# 5-day deep tuning attempt → the filters all failed out of sample

`feat5.py` adds the extended 5-day feature set. Objective: higher probability of
rise, larger rise within 5 days, smaller loss in that window.

New families tested, with sources: overnight/intraday decomposition
(`1410.5513`, `2010.01727`), MAX/lottery, realized skewness, Amihud illiquidity,
downside semi-beta, ATR compression, post-extreme reversal (`cond-mat/0406696`),
and drift-regime conditioning (`2511.12490`, which claims a 13-Sharpe OOS factor).

## Finding 1 — the three objectives are structurally in conflict

Scoring 19 signals on all three objectives at once (in-sample, matched):

```
  signal          P(rise)     t   maxRise     t   maxFall     t
  imom252_21      -0.10pp  -0.1    +1.36%  17.8    -1.14% -11.6
  ma_1_200        -0.48pp  -0.7    +1.37%  15.7    -1.17% -13.9
  imom20          +0.04pp   0.1    +1.04%  12.6    -0.77% -10.6
  rvol20 (low)    +0.19pp   0.2    -0.83% -17.0    +0.77%  17.9
  max21 (low)     -0.41pp  -0.5    -0.79% -16.0    +0.68%  15.4
```

The maxRise and maxFall columns are near mirror images. Every signal that raises
the 5-day upside raises the downside by almost exactly as much — they are all
selecting **volatility**, not asymmetry. You can choose how much volatility to
hold; you cannot have the rise without the fall.

## Finding 2 — probability of rise is not predictable at 5 days

No signal moved it. Best in-sample was 52-week-high proximity at +0.48pp (t=2.0),
which is the weakest signal at 20 days and did not replicate.

## Finding 3 — the tuning inverted out of sample

~130 in-sample variants were scored. The winner combined top-decile `imom20` with
ATR below median and `drift63 > 0.55`, improving **all three objectives at once**:
P(rise) +4.29pp (t=2.6), asymmetry +0.400%, net +0.389% (t=2.5).

Held out on 2024-2026, one shot, thresholds fixed on the in-sample half:

```
  variant                              P(rise)      asym       net       t
  imom20, no filter                    +0.45pp    +0.473%   +0.438%    2.6
  imom20 + ATR<median                  +0.05pp    +0.082%   +0.118%    1.0
  imom20 + ATR<median + drift>0.55     -0.96pp    -0.313%   -0.203%   -0.8
```

Every filter made it worse, monotonically in how selective it was. The
in-sample winner flipped sign. This is exactly the data-snooping failure mode
`1811.06766` builds its discrete-FDR machinery to control, reproduced from the
inside — and a caution about `2511.12490`'s 13-Sharpe claim, whose conditioning
rule is the component that did most damage here.

## What survives at 5 days

The unfiltered screen, unchanged from the 20-day version:

```
                          screen    universe    matched      t
  P(rise)                  53.9%       52.8%    +0.66pp    0.80
  avg max rise in 5d      +5.06%      +3.61%    +1.80%    13.93
  avg max fall in 5d      -4.33%      -3.19%    -1.38%   -12.95
  net return per 5d       +0.733%     +0.331%   +0.451%    3.10
  asymmetry (rise+fall)                         +0.420%    2.77
```

Survivorship-flat (+0.438% → +0.433% excluding names below 75% of their 52-week
high) and random control p < 0.0001. The real edge is the **asymmetry**: upside
gained slightly exceeds downside taken. It is about a fifth of the 20-day matched
excess, so prefer the 20-day hold.

---

# Holding-period sweep and the 30-60 day re-tune

Same 26,727 held-out observations at every horizon, so the rows are comparable.
Newey-West t (lag = horizon in months) corrects the overlapping windows — the
naive t-stats at 120d would otherwise be badly inflated.

```
   hold    excess   per 20d   NW t   raw net   win%   cost drag/yr
     5d   +0.508%   +2.032%   3.28   +0.773%  54.7%      6.05%
    20d   +2.070%   +2.070%   3.47   +3.907%  59.1%      1.51%
    30d   +3.427%   +2.284%   3.98   +6.041%  61.3%      1.01%
    60d   +6.652%   +2.217%   3.82  +11.929%  64.2%      0.50%
    90d   +9.691%   +2.154%   4.24  +18.164%  66.8%      0.34%
   120d  +12.855%   +2.142%   4.50  +24.693%  70.2%      0.25%
```

The signal does not decay per unit of time. What changes is turnover cost (12×
between 5d and 120d) and hit rate (54.7% → 70.2%).

Non-overlapping rebalances, compounded:

```
   hold  periods     total     CAGR   maxDD
     5d      109    +94.2%   +35.9%  -22.6%
    20d       28   +168.4%   +55.9%  -13.6%
    30d       19   +154.8%   +51.2%  -10.4%
    60d       10   +179.4%   +54.0%   -9.6%
    90d        7   +206.7%   +56.6%    0.0%
   120d        5   +212.4%   +61.4%    0.0%
```

**90d and 120d are not established.** Seven and five non-overlapping periods; a
0.0% drawdown on five observations is five coin flips landing heads. A long hold
also cannot exit a momentum crash, which is this factor's known failure mode and
did not occur in 2024-2026 — so the window structurally flatters long horizons.

## Re-tuned legs for 30-60d

`imom20` is a one-month signal and decays over a longer hold (in-sample t falls
to 1.28 at 60d). Legs re-ranked in-sample, one out-of-sample shot:

```
  composite                        30d/20d     t   60d/20d     t
  imom252_21 + imom20 (20d legs)   +2.284%  3.90   +2.217%  3.82
  imom252_21 + ma_1_200            +2.648%  3.67   +2.630%  4.22   <- adopted
  imom252_21 + ma_1_200 + irev5    +1.427%  2.98   +1.483%  3.52
```

The three-leg variant was the in-sample favourite on t and failed out of sample —
dropped. Adopted configuration at 60d: survivorship-flat (+2.630% → +2.333%
excluding names below 75% of their 52-week high), by year +1.96% / +2.78% /
+4.44%, random control p < 0.0001 at both 30d and 60d.

`--hold 20` still selects the original pair. Note the 30-60d range itself was
chosen after seeing out-of-sample results across horizons, so that choice carries
selection risk; the leg re-ranking within it did not.
