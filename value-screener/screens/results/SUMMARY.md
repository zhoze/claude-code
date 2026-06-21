# Three-Screen Run — Buffett Quality · Magic Formula · Piotroski F-Score

**Universe:** Russell 1000 (production pipeline pulls the largest ~1,000 US names
by market cap from Financial Modeling Prep). **Data date:** 2026-06-19 (FMP
`/stable`, annual statements FY2021–FY2025).

> ⚠️ **Read this first — two different runs below.**
> The valid FMP API key that can pull ~1,000 companies × 5 years lives in the
> GitHub **`screen`** environment (CI), **not** in this session. So:
>
> 1. **Full Russell 1000 run** → `build_screen_inputs.py` + `run_screens.py`,
>    wired into `.github/workflows/russell1000-screens.yml`. Trigger that
>    workflow to regenerate the *real* top-10 lists across all ~1,000 names.
> 2. **Live cross-section below** → a **6-company** sample pulled fresh via the
>    FMP MCP server **in-session** (the MCP plan throttled after the first
>    burst, so the sample is small). It proves all three screens run correctly
>    on real multi-year data and is what the tables below show. Treat it as a
>    *worked example*, not the definitive Russell 1000 top-10.
>
> Sample = AAPL, MSFT, GOOGL, V, UNH (quality mega-caps) + GM (deep-value
> cyclical). This is an **educational screen, not investment advice.**

---

## 1. Buffett Quality Compounder — top of sample

Ranks durable, cash-generative compounders: 5-yr ROIC/ROE, gross/operating/FCF
margins, growth, balance-sheet strength, and valuation. Hard gates: ROIC≥10%,
ROE≥12%, FCF margin≥5%, D/E≤2, interest-coverage≥3, FCF>0, PE>0.

| # | Ticker | Company | Total | Quality | Growth | Balance | Value | ROIC 5y | ROE 5y | FCF marg 5y | PE |
|---|--------|---------|------:|--------:|-------:|--------:|------:|--------:|-------:|------------:|----:|
| 1 | **V**     | Visa            | 80.4 | 99.6 | 100.0 | 70.6 | 18.9 | 24.7% | 44.5% | 59.0% | 33.1 |
| 2 | **MSFT**  | Microsoft       | 77.9 | 97.4 | 97.0  | 80.3 | 5.0  | 23.3% | 36.9% | 30.9% | 36.3 |
| 3 | **GOOGL** | Alphabet        | 77.8 | 85.9 | 99.3  | 89.3 | 13.7 | 22.7% | 28.5% | 21.8% | 28.7 |
| 4 | **AAPL**  | Apple           | 58.9 | 88.9 | 38.3  | 50.7 | 11.6 | 44.8% | 163.9%| 26.7% | 34.1 |
| 5 | **UNH**   | UnitedHealth    | 36.3 | 23.0 | 48.7  | 32.3 | 52.8 | 12.5% | 20.7% | 6.6%  | 24.9 |

(GM fails the quality gates — 5-yr ROIC 3.4% < 10% — so it is correctly excluded.)

## 2. Magic Formula (Greenblatt) — top of sample

Ranks on earnings yield (EBIT/EV) + return on capital (EBIT/(NWC+net PP&E)).
Financials/Utilities/Real Estate excluded — so **Visa drops out**.

| # | Ticker | Company | Magic score | Earnings yield | Return on capital |
|---|--------|---------|------------:|---------------:|------------------:|
| 1 | **AAPL**  | Apple          | 100.0 | 3.42% | 413.7% |
| 2 | **MSFT**  | Microsoft      | 66.7  | 3.40% | 46.0%  |
| 3 | **GOOGL** | Alphabet       | 33.3  | 3.38% | 35.4%  |
| 4 | **GM**    | General Motors | 0.0   | 1.58% | 2.9%   |

(Apple's 414% ROC is the Greenblatt negative-working-capital quirk — tiny
invested capital. GM's EBIT collapsed on 2025 charges, so it ranks last.)

## 3. Piotroski F-Score — low-P/B value, sample

9-point financial-strength score on the lowest-P/B half of the sample
(demo thresholds: P/B quantile 0.5, min F-score 5; standard run uses 0.4 / 7).

| # | Ticker | Company | F-score | P/B | ROA | OCF/assets | Asset turn |
|---|--------|---------|--------:|----:|----:|-----------:|-----------:|
| 1 | **GOOGL** | Alphabet       | 7 | 9.13 | 25.3% | 31.5% | 0.68 |
| 2 | **UNH**   | UnitedHealth   | 6 | 3.19 | 4.0%  | 6.5%  | 1.45 |
| 3 | **GM**    | General Motors | 5 | 1.22 | 1.0%  | 9.6%  | 0.66 |

---

## Overlap — names appearing in 2+ screens (the "similar stocks")

The sample produced exactly **5 names that screen on more than one lens** —
these are the stocks profiled below.

| Ticker | Buffett Quality | Magic Formula | Piotroski | Screens |
|--------|:---:|:---:|:---:|:---:|
| **GOOGL** | ✅ #3 | ✅ #3 | ✅ #1 | **3 / 3** |
| **AAPL**  | ✅ #4 | ✅ #1 | — | 2 |
| **MSFT**  | ✅ #2 | ✅ #2 | — | 2 |
| **UNH**   | ✅ #5 | — | ✅ #2 | 2 |
| **GM**    | — | ✅ #4 | ✅ #3 | 2 |

---

## Stock summaries + evaluation metrics

### 🥇 GOOGL — Alphabet Inc. *(only name in all three screens)*
**Communication Services · ~$3.79T**
The cleanest "compounder at a value price" in the sample: it clears Buffett's
quality bar, is mid-pack on Magic Formula, **and** posts the highest Piotroski
F-score (7/9). High returns on capital with a fortress balance sheet, and the
cheapest large-cap multiple of the quality names.

| ROIC 5y | ROE 5y | Op margin 5y | FCF margin 5y | Rev CAGR 5y | EPS CAGR 5y | D/E | Int. cov | PE | EY (EBIT/EV) | ROC | F-score |
|--------:|-------:|-------------:|--------------:|------------:|------------:|----:|---------:|---:|-------------:|----:|--------:|
| 22.7% | 28.5% | 29.7% | 21.8% | 11.8% | 17.8% | 0.14 | very high | 28.7 | 3.38% | 35.4% | 7 |

**Why it screens well:** strongest revenue + EPS growth combo, lowest leverage,
rising ROA/margins (improving fundamentals drive the F-score). **Watch:** heavy
AI capex compresses near-term FCF; P/B of 9.1 is not "cheap" on assets.

### 🥈 MSFT — Microsoft Corp.
**Information Technology · ~$3.70T**
The most balanced quality profile — #2 on Buffett (97 quality / 97 growth) and
#2 on Magic Formula. Premium multiple is the only thing holding back the
valuation sub-score.

| ROIC 5y | ROE 5y | Op margin 5y | FCF margin 5y | Rev CAGR 5y | EPS CAGR 5y | D/E | Int. cov | PE | EY | ROC |
|--------:|-------:|-------------:|--------------:|------------:|------------:|----:|---------:|---:|---:|----:|
| 23.3% | 36.9% | 43.1% | 30.9% | 13.8% | 14.1% | 0.33 | 53.9 | 36.3 | 3.40% | 46.0% |

**Why it screens well:** ~43% operating margins, double-digit growth on both
lines, low leverage. **Watch:** PE ~36 and AI/datacenter capex (FCF margin
dipped as capex rose).

### 🥉 AAPL — Apple Inc.
**Information Technology · ~$3.82T**
**#1 on Magic Formula** (highest earnings yield + extreme ROC from negative
working capital) and elite quality, but a **low growth sub-score (38)** —
5-yr revenue CAGR only 3.3% — pulls its Buffett total to 58.9.

| ROIC 5y | ROE 5y | Op margin 5y | FCF margin 5y | Rev CAGR 5y | EPS CAGR 5y | D/E | Int. cov | PE | EY | ROC |
|--------:|-------:|-------------:|--------------:|------------:|------------:|----:|---------:|---:|---:|----:|
| 44.8% | 163.9% | 30.7% | 26.7% | 3.3% | 7.4% | 1.52 | very high | 34.1 | 3.42% | 413.7% |

**Why it screens well:** best capital efficiency and earnings yield in the set,
huge buybacks (ROE>100% on shrunken equity). **Watch:** sluggish top-line growth
and a full multiple.

### UNH — UnitedHealth Group Inc.
**Health Care · ~$0.30T**
A genuine **value/turnaround** read: lowest Buffett total of the qualifiers
(thin ~7.6% operating margins and **declining EPS, −7.5% 5-yr CAGR**), but #2 on
Piotroski (F=6) at a modest 24.9 PE / 3.2 P/B with rising asset turnover.

| ROIC 5y | ROE 5y | Op margin 5y | FCF margin 5y | Rev CAGR 5y | EPS CAGR 5y | D/E | Int. cov | PE | P/B | F-score |
|--------:|-------:|-------------:|--------------:|------------:|------------:|----:|---------:|---:|----:|--------:|
| 12.5% | 20.7% | 7.6% | 6.6% | 11.7% | −7.5% | 0.83 | 4.7 | 24.9 | 3.19 | 6 |

**Why it screens:** cheap vs. its own history, still grows revenue ~12%/yr,
deleveraging. **Watch:** margin/earnings compression is real — the EPS decline
is the reason quality scores it low.

### GM — General Motors Co.
**Consumer Discretionary · ~$0.07T**
The **deep-value cyclical** — by far the cheapest name (P/B 1.22, the only
sub-2× book in the set). Screens on Magic Formula and Piotroski (F=5) but
**fails Buffett's quality gates** (5-yr ROIC 3.4%). 2025 EBIT was depressed by
large charges, so its Magic rank is last despite the cheap valuation.

| ROIC 5y | ROE 5y | Op margin 5y | FCF margin 5y | D/E | Int. cov | PE | P/B | EY | ROC | F-score |
|--------:|-------:|-------------:|--------------:|----:|---------:|---:|----:|---:|----:|--------:|
| 3.4% | 11.4% | ~5% | low/neg | 2.13 | 4.0 | 27.7 | 1.22 | 1.58% | 2.9% | 5 |

**Why it screens:** rock-bottom price-to-book and positive operating cash flow
> net income. **Watch:** high leverage (D/E 2.1), low returns on capital,
cyclical earnings — a classic "cheap for a reason" value name.

---

## How to reproduce / run the full Russell 1000

```bash
# Full universe (needs an FMP key with statement access — runs in CI):
FMP_KEY=*** python3 build_screen_inputs.py            # → screens/inputs/*.csv
python3 run_screens.py --top 10                       # → screens/results/*.csv

# This in-session sample:
python3 demo_build.py                                 # builds the 6-name inputs
python3 run_screens.py --top 10 --pb-quantile 0.5 --min-f-score 5
```

Or trigger **`.github/workflows/russell1000-screens.yml`** (environment:
`screen`) to build inputs from FMP and run all three screens on the full
Russell 1000 automatically.
