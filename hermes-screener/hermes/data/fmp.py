"""Financial Modeling Prep client: fundamentals, universe discovery, earnings.

The fundamental record is built by the vendored, unmodified
screens/fundamental/build_screen_inputs.build_record, so the 20 screens see exactly
the columns they were written for. This client also keeps the raw statement rows
(for the immutable snapshot) and the latest filing acceptance date, which is the
point-in-time key (spec §2.2.2).
"""
from __future__ import annotations

import re

from .. import vendored
from .http import JsonClient, require_key

NON_EQUITY_RE = re.compile(
    r"\d%|\d+\.\d+\s*$|Notes\s+due|Subordinated|Debentures?|Depositary|\bPfd\b"
    r"|Preferred\s+(?:Stock|Shares|Series)|Cumulative\s+Preferred",
    re.I,
)


class FMPClient:
    def __init__(self, base_url: str, key_env: str = "FMP_KEY", retries: int = 4,
                 timeout: float = 30.0, key: str | None = None):
        self.http = JsonClient(base_url, key or require_key(key_env), key_param="apikey",
                               retries=retries, timeout=timeout)

    def get(self, path: str):
        """Vendored-compatible getter: returns parsed JSON or None on error/restriction."""
        d = self.http.get(path)
        if isinstance(d, dict) and ("Error Message" in d or "Restricted" in str(d)[:40]):
            return None
        return d

    # ------------------------------------------------------------- universe
    def discover_universe(self, target: int = 1000, min_market_cap: float = 1.5e9) -> list[dict]:
        """Largest `target` US common stocks by market cap (Russell 1000 proxy, live-only)."""
        seen: dict[str, dict] = {}
        for exch in ("NASDAQ", "NYSE"):
            q = (f"company-screener?exchange={exch}&country=US&isEtf=false&isFund=false"
                 f"&isActivelyTrading=true&marketCapMoreThan={int(min_market_cap)}&limit=3000")
            for row in self.get(q) or []:
                sym = row.get("symbol")
                name = row.get("companyName") or ""
                mc = row.get("marketCap") or 0
                if not sym or "." in sym or "-" in sym or NON_EQUITY_RE.search(name):
                    continue
                if sym not in seen or mc > (seen[sym]["market_cap"] or 0):
                    seen[sym] = {"ticker": sym, "company": name, "sector": row.get("sector"),
                                 "industry": row.get("industry"), "market_cap": mc}
        ranked = sorted(seen.values(), key=lambda r: (-(r["market_cap"] or 0), r["ticker"]))
        return ranked[:target]

    # --------------------------------------------------------- fundamentals
    def fundamentals(self, sym: str) -> tuple[dict | None, dict]:
        """(screen record or None, raw rows). Same seven calls as the vendored pull_symbol."""
        km = self.get(f"key-metrics?symbol={sym}&period=annual&limit=5")
        rat = self.get(f"ratios?symbol={sym}&period=annual&limit=5")
        inc = self.get(f"income-statement?symbol={sym}&period=annual&limit=5")
        bal = self.get(f"balance-sheet-statement?symbol={sym}&period=annual&limit=5")
        cf = self.get(f"cash-flow-statement?symbol={sym}&period=annual&limit=5")
        prof = self.get(f"profile?symbol={sym}")
        quote = self.get(f"quote?symbol={sym}")
        raw = {"key_metrics": km, "ratios": rat, "income": inc, "balance": bal,
               "cash_flow": cf, "profile": prof, "quote": quote}
        if not (km and rat and inc and bal):
            return None, raw
        prof0 = prof[0] if isinstance(prof, list) and prof else {}
        quote0 = quote[0] if isinstance(quote, list) and quote else {}
        rec = vendored.fundamental().build_screen_inputs.build_record(
            sym, km, rat, inc, bal, cf or [], prof0, quote0)
        if rec is not None:
            rec["accepted_date"] = latest_acceptance(inc, bal, cf)
        return rec, raw

    # ------------------------------------------------------------- earnings
    def earnings(self, sym: str, limit: int = 16) -> list[dict]:
        """EPS/revenue actual vs estimate; rows with eps_actual None are scheduled dates."""
        out = []
        for row in self.get(f"earnings?symbol={sym}&limit={limit}") or []:
            if not row.get("date"):
                continue
            out.append({"ticker": sym, "date": row["date"],
                        "eps_actual": row.get("epsActual"), "eps_est": row.get("epsEstimated"),
                        "rev_actual": row.get("revenueActual"),
                        "rev_est": row.get("revenueEstimated")})
        return out


def latest_acceptance(*statement_lists) -> str | None:
    """Latest filing acceptance date across statements (the PIT visibility key)."""
    dates = []
    for rows in statement_lists:
        if isinstance(rows, list) and rows:
            d = rows[0].get("acceptedDate") or rows[0].get("filingDate")
            if d:
                dates.append(str(d)[:10])
    return max(dates) if dates else None
