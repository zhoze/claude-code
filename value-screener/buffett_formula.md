# The Warren Buffett Value Investing Formula

A reference for the metrics, thresholds, and math the screener encodes. Every
threshold lives in [`config.json`](config.json) and is tunable. Sources are
listed at the bottom; this is an educational reference, **not investment
advice**.

---

## 1. Philosophy in one page

Buffett fused Benjamin Graham's *margin of safety* with Charlie Munger's and
Philip Fisher's emphasis on **business quality**. The goal is not "cheap"
statistically — it is:

> "It's far better to buy a wonderful company at a fair price than a fair
> company at a wonderful price." — Warren Buffett

Robert Hagstrom (*The Warren Buffett Way*) distilled Buffett's filters into four
groups of **tenets**:

| Group | Question | What we can measure |
|---|---|---|
| **Business** | Is it simple, understandable, with a consistent history and favorable long-term prospects? | Earnings/margin consistency, gross margin (moat proxy) |
| **Management** | Rational, candid, resists the "institutional imperative"? | Capital allocation: ROE/ROIC, payout discipline, share count |
| **Financial** | High return on equity, high "owner earnings", fat margins, low debt? | ROE, ROIC, owner earnings, net & gross margin, leverage |
| **Market/Value** | What is it worth, and can I buy at a meaningful discount? | Intrinsic value (DCF), margin of safety |

The two rules that govern everything:

> "Rule No. 1: Never lose money. Rule No. 2: Never forget Rule No. 1."

…and only operate inside your **circle of competence**.

---

## 2. Quality gates (the "wonderful business" test)

The screener awards up to **60 points** across these gates. Many investors treat
the first three (ROE, margins, low debt) as hard filters.

| # | Metric | Threshold | Why Buffett cares |
|---|---|---|---|
| 1 | **Return on Equity (ROE)** | ≥ 15%, ideally for 8-10 yrs | Measures how efficiently management compounds shareholders' capital. Buffett: "the primary test of managerial economic performance." |
| 2 | **Return on Invested Capital (ROIC)** | ≥ 12% | Returns above the cost of capital are the fingerprint of a durable competitive advantage ("moat"). |
| 3 | **Net profit margin** | ≥ 10%, stable/expanding | Pricing power. Buffett favors businesses that can raise prices without losing customers. |
| 4 | **Gross margin** | ≥ 40% (sector-relative) | A high, durable gross margin is a classic moat indicator (per analysts of Buffett's method). |
| 5 | **Leverage: Net debt / EBITDA** | < 3× (net cash auto-passes) | "We will reject interesting opportunities rather than over-leverage." Conservative balance sheets survive downturns. |
| 6 | **Interest coverage** | > 5× | Earnings comfortably service debt. |
| 7 | **Current ratio** | > 1.5 (>1.0 = partial) | Liquidity / short-term solvency. |
| 8 | **Free cash flow** | Positive | Real distributable cash, not just accounting profit. |
| 9 | **Earnings quality** | Net income backed by cash (CFO/NI > 1) | Guards against earnings that don't convert to cash. |

> **Why net-debt/EBITDA instead of a strict debt-to-equity < 0.5?** Aggressive
> buybacks at quality firms (KO, AAPL) shrink book equity and inflate D/E even
> when the business is barely leveraged. Net-debt/EBITDA — "how many years of
> cash earnings to pay off all debt" — is a more robust Buffett-style leverage
> test. Debt-to-equity is still reported, just not gated on.

> **Caveat — banks & insurers:** Leverage, current ratio, and interest-coverage
> gates do not fit financial firms (leverage *is* their business model). Buffett
> values banks on ROE and return on tangible common equity. JPM in the sample
> run illustrates this: strong ROE/margins but it fails the industrial leverage
> gates.

---

## 3. Owner earnings (Buffett's preferred earnings number)

From the **1986 Berkshire Hathaway shareholder letter**, Buffett defines *owner
earnings* as:

```
Owner Earnings = Net Income
               + Depreciation & Amortization (and other non-cash charges)
               - Maintenance capital expenditures
               (± changes in working capital)
```

It strips out accounting noise and the portion of capex needed just to *maintain*
competitive position. In practice maintenance capex is hard to isolate, so
**free cash flow** is the common proxy. The screener uses earnings per share as
the cash-earnings base by default and accepts an explicit `owner_earnings_ps`
column to override it with a truer owner-earnings figure when you have one.

---

## 4. Intrinsic value

### 4a. Two-stage discounted owner-earnings model (primary)

Intrinsic value per share is the present value of future owner earnings:

```
Stage 1 (years 1..N):  PV = Σ  OE₀ · (1+g)ᵗ / (1+r)ᵗ
Stage 2 (terminal):    TV = OE_N · (1+gₜ) / (r − gₜ),  discounted by (1+r)ᴺ
Intrinsic value = Stage 1 + Stage 2
```

Defaults (in `config.json`):

| Symbol | Meaning | Default | Rationale |
|---|---|---|---|
| `OE₀` | current owner earnings / EPS | from data | starting cash earnings |
| `g` | stage-1 growth | historical 5y EPS CAGR, **capped at 10%** | high growth mean-reverts; never extrapolate heroics |
| `N` | projection years | 10 | Buffett's long horizon |
| `gₜ` | terminal growth | 2.5% | ≈ long-run nominal GDP |
| `r` | discount rate | 9% | see note |

> **Discount rate.** Buffett famously discounts at the **long-term US Treasury
> yield** rather than adding an equity-risk premium — because he already demands
> a margin of safety. Most practitioners floor the rate near 9-10% to stay
> conservative. The default here is 9%; set `discount_rate` toward the current
> 10-year Treasury for a more Buffett-literal (and more generous) valuation.

### 4b. Benjamin Graham's formula (cross-check)

Graham's intrinsic-value shortcut, with his later bond-yield revision:

```
V = EPS × (8.5 + 2g)              (original)
V = EPS × (8.5 + 2g) × 4.4 / Y    (revised, bond-adjusted)
```

- `8.5` = base P/E for a no-growth company
- `g` = expected annual growth (percentage points, capped)
- `4.4` = average AAA corporate bond yield when Graham wrote
- `Y` = current AAA corporate bond yield

The screener reports this as a second opinion alongside the DCF and the
**Graham Number** (`√(22.5 × EPS × Book Value per Share)`), Graham's ceiling for
a defensive purchase.

---

## 5. Margin of safety (the buy decision)

Graham's central principle, and Buffett's "three most important words in
investing":

```
Margin of Safety = (Intrinsic Value − Price) / Intrinsic Value
```

The screener targets a **≥ 25% discount** to intrinsic value. A wonderful
business at or above intrinsic value is a *Watch*, not a *Buy* — patience is part
of the method.

---

## 6. How the score becomes a verdict

```
Buffett score (0-100) = Quality pillar (0-60) + Valuation pillar (0-40)

Valuation pillar:
  • Margin-of-safety points  (0-30): scales 0 → 30 as MOS goes 0% → 25%
  • P/E points               (0-10): 10 at P/E ≤ 15, fading to 0 by P/E 30

Verdict:
  • Strong Candidate : score ≥ 70  AND core gates (ROE, net margin,
                       leverage) all pass  AND margin of safety ≥ 0
  • Watch            : score ≥ 50
  • Pass             : otherwise
```

---

## Sources

- Warren Buffett, *Berkshire Hathaway Shareholder Letters* (esp. 1986 "owner
  earnings"; 1992 "wonderful company at a fair price") — berkshirehathaway.com/letters
- Benjamin Graham, *The Intelligent Investor* — margin of safety; Graham formula & Graham Number
- Robert G. Hagstrom, *The Warren Buffett Way* — the business/management/financial/market tenets
- Mary Buffett & David Clark, *Buffettology* — quantitative interpretation of the tenets
- Investopedia: "Warren Buffett: How He Does It", "Owner Earnings", "Margin of Safety", "Return on Invested Capital (ROIC)"
- AAII (American Association of Individual Investors): "Buffett: Hagstrom" screening methodology

> Thresholds are a widely-used *approximation* of Buffett's approach. His real
> edge — judging moats, management quality, and staying within his circle of
> competence — is qualitative and cannot be reduced to a formula.
