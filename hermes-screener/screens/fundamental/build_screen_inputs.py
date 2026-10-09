#!/usr/bin/env python3
"""
build_screen_inputs.py — build the three screen input CSVs for the Russell 1000
===============================================================================

Pulls multi-year fundamentals from Financial Modeling Prep (/stable) and writes
the exact input CSVs consumed by the twenty screens in this directory:

    screens/inputs/buffett_quality_input.csv   -> screen_buffett_quality.py
    screens/inputs/magic_formula_input.csv     -> screen_magic_formula.py
    screens/inputs/piotroski_input.csv         -> screen_piotroski_f_score.py
    screens/inputs/extended_input.csv          -> the extended-formula screens
                                                  (Graham, RMW, shareholder yield,
                                                  Altman Z, Ohlson O, Beneish M,
                                                  Mohanram G, dividend growth,
                                                  Lev-Thiagarajan signals)
    screens/inputs/as_of.txt                   -> data date + universe size stamp

Cash-flow payout fields in extended_input.csv keep the FMP sign convention:
cash outflows (dividends, buybacks, debt repayment) are negative numbers.

Universe: the largest ~1000 actively-traded US common stocks by market cap
(a faithful, reproducible proxy for the Russell 1000, which FMP does not expose
as a constituent list on this plan). Override with --universe a,b,c or a file.

Sanity filters (so bad source rows can't fake a "cheap" name):
  * Valuation is anchored to the LIVE quote market cap, not the annual key-metrics
    snapshot — and PE / FCF-yield / EV-EBIT / P-B are recomputed from it. This
    prevents a stale snapshot (e.g. a years-old market cap) from inflating scores.
  * Non-common-stock tickers (baby bonds, notes, debentures, preferreds) are
    dropped via NON_EQUITY_RE; names with no usable market cap are dropped too.

The API key is read from FMP_KEY and is NEVER written to disk or committed.

    FMP_KEY=your_key  python3 build_screen_inputs.py                 # full ~1000
    FMP_KEY=your_key  python3 build_screen_inputs.py --limit 30      # smoke test
    FMP_KEY=your_key  python3 build_screen_inputs.py --universe AAPL,MSFT,KO

Stdlib only (urllib + threads). Educational tool — not investment advice.
"""

import argparse
import csv
import json
import os
import re
import sys
import time
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed

HERE = os.path.dirname(os.path.abspath(__file__))
INPUTS = os.path.join(HERE, "inputs")
BASE = "https://financialmodelingprep.com/stable"

# Names that trade under their own ticker but are NOT common equity — baby bonds,
# notes, debentures, depositary/preferred shares (e.g. CMSD = "CMS Energy ...
# 5.875% Junior Subordinated Notes due 2079"). These slip past the equity screener.
NON_EQUITY_RE = re.compile(
    r"\d%|\d+\.\d+\s*$|Notes\s+due|Subordinated|Debentures?|Depositary|\bPfd\b"
    r"|Preferred\s+(?:Stock|Shares|Series)|Cumulative\s+Preferred",
    re.I,
)


def make_get(key):
    def get(path):
        url = f"{BASE}/{path}{'&' if '?' in path else '?'}apikey={key}"
        for attempt in range(4):
            try:
                with urllib.request.urlopen(url, timeout=30) as r:
                    d = json.loads(r.read().decode())
                if isinstance(d, dict) and ("Error Message" in d or "Restricted" in str(d)[:40]):
                    return None
                return d
            except Exception:
                time.sleep(1.5 * (attempt + 1))
        return None
    return get


def g(row, *keys, default=None):
    """First present, non-None value among keys in a dict."""
    if not isinstance(row, dict):
        return default
    for k in keys:
        v = row.get(k)
        if v is not None:
            return v
    return default


def mean(xs):
    xs = [x for x in xs if x is not None]
    return sum(xs) / len(xs) if xs else None


def cagr(series):
    """CAGR from a newest-first series (series[0]=latest). None if not computable."""
    vals = [x for x in series if x is not None]
    if len(vals) < 2:
        return None
    latest, oldest = vals[0], vals[-1]
    n = len(vals) - 1
    if oldest is None or oldest <= 0 or latest <= 0:
        return None
    return (latest / oldest) ** (1.0 / n) - 1


def discover_universe(get, target=1000):
    """Largest ~`target` US common stocks by market cap (Russell 1000 proxy)."""
    seen = {}
    for exch in ("NASDAQ", "NYSE"):
        q = (f"company-screener?exchange={exch}&country=US&isEtf=false&isFund=false"
             f"&isActivelyTrading=true&marketCapMoreThan=1500000000&limit=3000")
        for row in (get(q) or []):
            sym = row.get("symbol")
            mc = row.get("marketCap") or 0
            if sym and "." not in sym and "-" not in sym:
                seen[sym] = max(mc, seen.get(sym, 0))
    ranked = sorted(seen, key=lambda s: seen[s], reverse=True)
    return ranked[:target]


def pull_symbol(get, sym):
    km = get(f"key-metrics?symbol={sym}&period=annual&limit=5")
    rat = get(f"ratios?symbol={sym}&period=annual&limit=5")
    inc = get(f"income-statement?symbol={sym}&period=annual&limit=5")
    bal = get(f"balance-sheet-statement?symbol={sym}&period=annual&limit=5")
    cf = get(f"cash-flow-statement?symbol={sym}&period=annual&limit=5")
    prof = get(f"profile?symbol={sym}")
    quote = get(f"quote?symbol={sym}")
    if not (km and rat and inc and bal):
        return None
    prof0 = prof[0] if isinstance(prof, list) and prof else {}
    quote0 = quote[0] if isinstance(quote, list) and quote else {}
    return build_record(sym, km, rat, inc, bal, cf or [], prof0, quote0)


def build_record(sym, km, rat, inc, bal, cf, prof, quote=None):
    """Turn raw FMP rows (newest-first) into a flat record for all three screens."""
    k0, r0, i0, b0 = km[0], rat[0], inc[0], bal[0]
    i1 = inc[1] if len(inc) > 1 else {}
    b1 = bal[1] if len(bal) > 1 else {}
    cf0 = cf[0] if cf else {}
    cf1 = cf[1] if len(cf) > 1 else {}

    revenues = [g(x, "revenue") for x in inc]
    epss = [g(x, "epsDiluted", "eps") for x in inc]

    def fcf_of(km_row, cf_row):
        """FCF in dollars: cash-flow statement if present, else derived from key-metrics."""
        fc = g(cf_row, "freeCashFlow")
        if fc is not None:
            return fc
        ev_y, mult = g(km_row, "enterpriseValue"), g(km_row, "evToFreeCashFlow")
        if g(km_row, "freeCashFlowToFirm") is not None:
            return g(km_row, "freeCashFlowToFirm")
        return ev_y / mult if (ev_y and mult) else None

    fcf_margins = []
    for idx, ii in enumerate(inc):
        rev = g(ii, "revenue")
        fc = fcf_of(km[idx] if idx < len(km) else {}, cf[idx] if idx < len(cf) else {})
        if rev and fc is not None and rev != 0:
            fcf_margins.append(fc / rev)

    # interest coverage: prefer ratio; else EBIT / interest expense; else high if no interest
    ebit = g(i0, "operatingIncome", "ebit")
    int_exp = g(i0, "interestExpense")
    icov = g(r0, "interestCoverageRatio")
    if not icov:
        if int_exp and int_exp > 0 and ebit is not None:
            icov = ebit / int_exp
        elif ebit and ebit > 0:
            icov = 50.0  # negligible interest expense -> effectively very high coverage

    shares_t = g(i0, "weightedAverageShsOut", "weightedAverageShsOutDil")
    shares_old = g(inc[-1], "weightedAverageShsOut", "weightedAverageShsOutDil")
    shares_change = None
    if shares_t and shares_old and shares_old != 0:
        shares_change = shares_t / shares_old - 1

    quote = quote or {}
    company_name = g(prof, "companyName", "name", default="")
    # Drop non-common-stock securities (baby bonds, notes, preferreds).
    if NON_EQUITY_RE.search(company_name or ""):
        return None

    # Live valuation anchor: trust the current quote's market cap, not a possibly
    # stale annual key-metrics snapshot (which made e.g. KLA look ~10x too cheap).
    mktcap = g(quote, "marketCap")
    if not mktcap or mktcap <= 0:
        mktcap = g(k0, "marketCap") or g(prof, "marketCap", "mktCap")
    if not mktcap or mktcap <= 0:
        return None  # no usable market cap -> can't value it, drop
    price = g(quote, "price") or g(prof, "price")

    # Enterprise value from the live market cap + latest net debt.
    total_debt = g(b0, "totalDebt")
    if total_debt is None:
        total_debt = (g(b0, "longTermDebt") or 0) + (g(b0, "shortTermDebt") or 0)
    cash = g(b0, "cashAndShortTermInvestments", "cashAndCashEquivalents") or 0
    ev = mktcap + (total_debt or 0) - cash

    # Recompute the price-based ratios off the live anchor so a stale snapshot
    # can't manufacture fake "cheapness".
    latest_fcf = fcf_of(k0, cf0)
    fcf_yield = (latest_fcf / mktcap) if (latest_fcf is not None and mktcap) else None
    pe = g(quote, "pe")
    if pe is None or pe <= 0:
        # quote PE is missing on some plans -> derive from live mcap / latest net income
        ni = g(i0, "netIncome")
        eps_ttm = g(quote, "eps")
        if ni and ni > 0:
            pe = mktcap / ni
        elif eps_ttm and eps_ttm > 0 and price:
            pe = price / eps_ttm
    equity = g(b0, "totalStockholdersEquity", "totalEquity")
    price_to_book = (mktcap / equity) if (equity and equity > 0) else g(r0, "priceToBookRatio")

    nwc = None
    if g(b0, "totalCurrentAssets") is not None and g(b0, "totalCurrentLiabilities") is not None:
        nwc = g(b0, "totalCurrentAssets") - g(b0, "totalCurrentLiabilities")
    npe = g(b0, "propertyPlantEquipmentNet")

    def gm(row):
        rev, gp = g(row, "revenue"), g(row, "grossProfit")
        return gp / rev if rev else None

    def at(inc_row, bal_row):
        rev, ta = g(inc_row, "revenue"), g(bal_row, "totalAssets")
        return rev / ta if ta else None

    rec = {
        "ticker": sym,
        "company": g(prof, "companyName", "name", default=""),
        "sector": g(prof, "sector", default=""),
        "industry": g(prof, "industry", default=""),
        "market_cap": mktcap,
        "price": price,
        # --- Buffett quality (5y averages + latest balance/valuation) ---
        "roic_5y_avg": mean([g(x, "returnOnInvestedCapital") for x in km]),
        "roe_5y_avg": mean([g(x, "returnOnEquity") for x in km]),
        "gross_margin_5y_avg": mean([g(x, "grossProfitMargin") for x in rat]),
        "operating_margin_5y_avg": mean([g(x, "operatingProfitMargin") for x in rat]),
        "fcf_margin_5y_avg": mean(fcf_margins),
        "revenue_cagr_5y": cagr(revenues),
        "eps_cagr_5y": cagr(epss),
        "debt_to_equity": g(r0, "debtToEquityRatio"),
        "interest_coverage": icov,
        "current_ratio": g(r0, "currentRatio"),
        "fcf_yield": fcf_yield,
        "pe": pe,
        "ev_ebit": (ev / ebit) if (ev and ebit and ebit > 0) else None,
        "shares_change_5y_pct": shares_change,
        # --- Magic Formula ---
        "enterprise_value": ev,
        "ebit": ebit,
        "net_working_capital": nwc,
        "net_fixed_assets": npe,
        # --- Piotroski (t / t-1) ---
        "price_to_book": price_to_book,
        "net_income_t": g(i0, "netIncome"),
        "net_income_t_minus_1": g(i1, "netIncome"),
        "total_assets_t": g(b0, "totalAssets"),
        "total_assets_t_minus_1": g(b1, "totalAssets"),
        "operating_cash_flow_t": g(cf0, "netCashProvidedByOperatingActivities", "operatingCashFlow")
            or ((g(k0, "enterpriseValue") / g(k0, "evToOperatingCashFlow"))
                if (g(k0, "enterpriseValue") and g(k0, "evToOperatingCashFlow")) else None),
        "long_term_debt_t": g(b0, "longTermDebt"),
        "long_term_debt_t_minus_1": g(b1, "longTermDebt"),
        "current_assets_t": g(b0, "totalCurrentAssets"),
        "current_liabilities_t": g(b0, "totalCurrentLiabilities"),
        "current_assets_t_minus_1": g(b1, "totalCurrentAssets"),
        "current_liabilities_t_minus_1": g(b1, "totalCurrentLiabilities"),
        "shares_outstanding_t": shares_t,
        "shares_outstanding_t_minus_1": g(i1, "weightedAverageShsOut", "weightedAverageShsOutDil"),
        "gross_margin_t": gm(i0),
        "gross_margin_t_minus_1": gm(i1),
        "asset_turnover_t": at(i0, b0),
        "asset_turnover_t_minus_1": at(i1, b1),
    }

    # --- Extended columns (exact-formula screens: Graham, RMW, shareholder
    # yield, Altman Z, Ohlson O, Beneish M, Mohanram G, dividend growth,
    # Lev-Thiagarajan). Cash-flow fields keep FMP signs: outflows negative. ---
    rec.update({
        "retained_earnings_t": g(b0, "retainedEarnings"),
        "total_liabilities_t": g(b0, "totalLiabilities"),
        "total_liabilities_t_minus_1": g(b1, "totalLiabilities"),
        "receivables_t": g(b0, "netReceivables", "accountsReceivables"),
        "receivables_t_minus_1": g(b1, "netReceivables", "accountsReceivables"),
        "inventory_t": g(b0, "inventory"),
        "inventory_t_minus_1": g(b1, "inventory"),
        "ppe_net_t": npe,
        "ppe_net_t_minus_1": g(b1, "propertyPlantEquipmentNet"),
        "sga_t": g(i0, "sellingGeneralAndAdministrativeExpenses",
                   "generalAndAdministrativeExpenses"),
        "sga_t_minus_1": g(i1, "sellingGeneralAndAdministrativeExpenses",
                           "generalAndAdministrativeExpenses"),
        "rd_expense_t": g(i0, "researchAndDevelopmentExpenses"),
        "rd_expense_t_minus_1": g(i1, "researchAndDevelopmentExpenses"),
        "capex_t": g(cf0, "capitalExpenditure"),
        "capex_t_minus_1": g(cf1, "capitalExpenditure"),
        "dep_amort_t": g(cf0, "depreciationAndAmortization")
            or g(i0, "depreciationAndAmortization"),
        "dep_amort_t_minus_1": g(cf1, "depreciationAndAmortization")
            or g(i1, "depreciationAndAmortization"),
        "interest_expense_t": g(i0, "interestExpense"),
        "income_tax_expense_t": g(i0, "incomeTaxExpense"),
        "income_tax_expense_t_minus_1": g(i1, "incomeTaxExpense"),
        "pretax_income_t": g(i0, "incomeBeforeTax", "pretaxIncome"),
        "pretax_income_t_minus_1": g(i1, "incomeBeforeTax", "pretaxIncome"),
        "ocf_t_minus_1": g(cf1, "netCashProvidedByOperatingActivities", "operatingCashFlow"),
        "stock_repurchased_t": g(cf0, "commonStockRepurchased", "purchaseOfOwnShares"),
        "common_stock_issued_t": g(cf0, "commonStockIssuance", "commonStockIssued"),
        "debt_flow_t": g(cf0, "netDebtIssuance", "debtRepayment"),
    })
    for y in range(1, 6):
        inc_y = inc[y - 1] if len(inc) >= y else {}
        bal_y = bal[y - 1] if len(bal) >= y else {}
        cf_y = cf[y - 1] if len(cf) >= y else {}
        ni_y, ta_y = g(inc_y, "netIncome"), g(bal_y, "totalAssets")
        rec[f"eps_y{y}"] = g(inc_y, "epsDiluted", "eps")
        rec[f"revenue_y{y}"] = g(inc_y, "revenue")
        rec[f"roa_y{y}"] = (ni_y / ta_y) if (ni_y is not None and ta_y) else None
        rec[f"dividends_paid_y{y}"] = g(cf_y, "netDividendsPaid", "dividendsPaid",
                                        "commonDividendsPaid")
    return rec


BUFFETT_COLS = ["ticker", "company", "sector", "industry", "market_cap", "roic_5y_avg",
                "roe_5y_avg", "gross_margin_5y_avg", "operating_margin_5y_avg",
                "fcf_margin_5y_avg", "revenue_cagr_5y", "eps_cagr_5y", "debt_to_equity",
                "interest_coverage", "current_ratio", "fcf_yield", "pe", "ev_ebit",
                "shares_change_5y_pct"]
MAGIC_COLS = ["ticker", "company", "sector", "industry", "market_cap",
              "enterprise_value", "ebit", "net_working_capital", "net_fixed_assets", "price"]
PIOTROSKI_COLS = ["ticker", "company", "sector", "industry", "market_cap", "price_to_book",
                  "net_income_t", "net_income_t_minus_1", "total_assets_t", "total_assets_t_minus_1",
                  "operating_cash_flow_t", "long_term_debt_t", "long_term_debt_t_minus_1",
                  "current_assets_t", "current_liabilities_t", "current_assets_t_minus_1",
                  "current_liabilities_t_minus_1", "shares_outstanding_t", "shares_outstanding_t_minus_1",
                  "gross_margin_t", "gross_margin_t_minus_1", "asset_turnover_t", "asset_turnover_t_minus_1"]
EXTENDED_COLS = (["ticker", "company", "sector", "industry", "market_cap",
                  "retained_earnings_t", "total_liabilities_t", "total_liabilities_t_minus_1",
                  "receivables_t", "receivables_t_minus_1", "inventory_t", "inventory_t_minus_1",
                  "ppe_net_t", "ppe_net_t_minus_1", "sga_t", "sga_t_minus_1",
                  "rd_expense_t", "rd_expense_t_minus_1", "capex_t", "capex_t_minus_1",
                  "dep_amort_t", "dep_amort_t_minus_1", "interest_expense_t",
                  "income_tax_expense_t", "income_tax_expense_t_minus_1",
                  "pretax_income_t", "pretax_income_t_minus_1", "ocf_t_minus_1",
                  "stock_repurchased_t", "common_stock_issued_t", "debt_flow_t"]
                 + [f"{base}_y{y}" for base in ("eps", "revenue", "roa", "dividends_paid")
                    for y in range(1, 6)])


def write_csv(path, cols, records):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(cols)
        for r in records:
            w.writerow(["" if r.get(c) is None else r.get(c) for c in cols])


def write_all(records):
    records = sorted(records, key=lambda r: r["ticker"])
    write_csv(os.path.join(INPUTS, "buffett_quality_input.csv"), BUFFETT_COLS, records)
    write_csv(os.path.join(INPUTS, "magic_formula_input.csv"), MAGIC_COLS, records)
    write_csv(os.path.join(INPUTS, "piotroski_input.csv"), PIOTROSKI_COLS, records)
    write_csv(os.path.join(INPUTS, "extended_input.csv"), EXTENDED_COLS, records)
    with open(os.path.join(INPUTS, "as_of.txt"), "w") as f:
        f.write(f"{time.strftime('%Y-%m-%d')} — {len(records)} companies\n")
    return len(records)


def main(argv=None):
    p = argparse.ArgumentParser(description="Build Russell 1000 screen input CSVs from FMP.")
    p.add_argument("--universe", help="Comma-separated tickers or path to a file with one ticker per line")
    p.add_argument("--limit", type=int, default=0, help="Only pull the first N symbols (smoke test)")
    p.add_argument("--target", type=int, default=1000, help="Universe size when auto-discovering")
    p.add_argument("--workers", type=int, default=12)
    args = p.parse_args(argv)

    key = os.environ.get("FMP_KEY")
    if not key:
        sys.exit("ERROR: set the FMP_KEY environment variable (never hardcode the key).")
    get = make_get(key)

    if args.universe:
        if os.path.exists(args.universe):
            syms = [ln.strip().upper() for ln in open(args.universe) if ln.strip()]
        else:
            syms = [s.strip().upper() for s in args.universe.split(",") if s.strip()]
    else:
        print("discovering universe (largest US names by market cap)...")
        syms = discover_universe(get, target=args.target)
    if args.limit:
        syms = syms[:args.limit]
    print(f"universe: {len(syms)} symbols")

    records, done = [], 0
    with ThreadPoolExecutor(max_workers=args.workers) as ex:
        futs = {ex.submit(pull_symbol, get, s): s for s in syms}
        for fut in as_completed(futs):
            r = fut.result()
            if r:
                records.append(r)
            done += 1
            if done % 100 == 0:
                print("...", done)

    n = write_all(records)
    print(f"wrote 4 input CSVs to {INPUTS} ({n} companies with usable data)")
    print("Now run:  python3 run_screens.py")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
