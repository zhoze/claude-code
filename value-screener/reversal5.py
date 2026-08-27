#!/usr/bin/env python3
"""Reversal-5 — a 5-day-horizon entry model for the AEGIS screener family.

Why this exists
---------------
``magic_screener_v2_4``'s entry model was panel-tested on 36 US large caps over
2020-2026 (see ``panel_backtest/``). It showed no measurable timing edge: the
composite score correlated -0.0152 with forward 20-day returns and lost to random
entry. Searching the underlying features for a *5-day* signal found a real one,
and it runs opposite to what that model rewards.

At a 5-day horizon the predictive features (in-sample Spearman IC, n=35,865) are:

    atrPct                      +0.0613     high own-volatility  -> higher return
    maxDrawdown252Pct           -0.0566     deeper drawdown      -> higher return
    realizedVol20AnnPct         +0.0515
    proximity52wPct             -0.0316     near 52w high        -> LOWER return
    breakout55Pct               -0.0384     extended breakout    -> LOWER return
    perf60                      -0.0369     strong 60d momentum  -> LOWER return

So: buy elevated-volatility names that are beaten down, not breakouts making new
highs. This is short-horizon reversal, and it is the inverse of the momentum /
breakout / 52-week-high logic in ``compute_entry_model``.

The signal survives per-symbol demeaning, so it is a timing signal rather than a
standing bet on high-beta stocks, and it was positive in every in-sample year
including 2022.

Held-out performance (2024-2026, never used for discovery or tuning), top 5% of
the composite, net of 12bps round-trip cost:

    all days baseline        +0.199% per 5 days,  52.1% positive
    Reversal-5 top 5%        +0.818% per 5 days,  56.4% positive
    v2.4 screener score>=75  +0.210% per 5 days,  49.8% positive
    random-entry control     +0.206%              one-sided p < 0.0001

Exits: a plain 5-day hold beat every ATR barrier pair tested out-of-sample.
Stops subtract from this model; do not add one without re-testing.

READ THE RISKS in the module docstring of ``score_history`` before using this.
Educational/research model, not investment advice.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import statistics
from typing import Any, Optional

import magic_screener_v2_4 as ms

# Feature -> sign. Positive means "higher raw value is more bullish at 5 days".
SIGNALS: dict[str, float] = {
    "atrPct": +1.0,
    "proximity52wPct": -1.0,
    "perf60": -1.0,
    "breakout55Pct": -1.0,
}

# Fraction of the score distribution treated as a signal. 5% held up out of
# sample; the top 1% decayed badly (+4.97% -> +0.75%) and is not recommended.
DEFAULT_SELECT_PCT = 0.05
HORIZON = 5


class Normalizer:
    """Per-symbol median/IQR, fitted on history strictly before the signal date.

    Fitting on a trailing window is what keeps this usable live: the location and
    scale used to judge "is this stock's ATR unusual *for it*" must not be
    computed from data the model would not have had.
    """

    def __init__(self, stats: dict[str, tuple[float, float]]):
        self.stats = stats

    @classmethod
    def fit(cls, records: list[dict[str, Any]]) -> "Normalizer":
        stats: dict[str, tuple[float, float]] = {}
        for feat in SIGNALS:
            vals = sorted(r[feat] for r in records if r.get(feat) is not None)
            if len(vals) < 100:
                continue
            med = statistics.median(vals)
            q1 = vals[int(len(vals) * 0.25)]
            q3 = vals[int(len(vals) * 0.75)]
            stats[feat] = (med, (q3 - q1) or 1.0)
        return cls(stats)

    def score(self, features: dict[str, Any]) -> Optional[float]:
        total, used = 0.0, 0
        for feat, sign in SIGNALS.items():
            v = features.get(feat)
            fitted = self.stats.get(feat)
            if v is None or fitted is None:
                continue
            med, iqr = fitted
            total += sign * (v - med) / iqr
            used += 1
        return total / used if used == len(SIGNALS) else None


def feature_record(bars: list[ms.Bar]) -> Optional[dict[str, Any]]:
    try:
        f = ms.compute_technical_features(bars)
    except ValueError:
        return None
    return {k: f.get(k) for k in SIGNALS}


def score_history(
    bars: list[ms.Bar],
    warmup: int = 260,
    fit_window: int = 504,
) -> list[dict[str, Any]]:
    """Walk-forward Reversal-5 scores.

    At each day T the normalizer is fitted only on the ``fit_window`` days ending
    at T-1, so the score carries no look-ahead.

    RISKS, measured on the held-out period:
      - This buys falling knives. The five worst held-out trades were -28.9%,
        -23.4%, -21.2%, -21.0% (all one name in one week) and -19.0%. The 5th
        percentile trade was -7.9%, and the model says not to use a stop.
      - Held-out magnitude was ~45% of in-sample (+1.82% -> +0.82%). Expect
        further decay live; treat +0.8% as an optimistic ceiling, not a forecast.
      - In a 36-stock universe 71% of signals came from 5 names, because few
        stocks are deeply beaten down at once. Use a wider universe or accept
        heavy concentration.
      - Short-horizon reversal is capacity-constrained and crowded; it is also
        the trade most exposed to a name that is falling for a solid reason.
      - Validated only on liquid US large caps, 2020-2026, long-only.
    """
    out: list[dict[str, Any]] = []
    records: list[dict[str, Any]] = []
    for i in range(len(bars)):
        rec = feature_record(bars[:i + 1]) if i >= warmup else None
        if rec is not None:
            rec["date"] = bars[i].date
            records.append(rec)
            history = records[:-1][-fit_window:]
            if len(history) >= 100:
                norm = Normalizer.fit(history)
                s = norm.score(rec)
                if s is not None:
                    out.append({"date": bars[i].date, "score5": s,
                                "close": bars[i].close, **{k: rec[k] for k in SIGNALS}})
    return out


def select_threshold(scored: list[dict[str, Any]], pct: float = DEFAULT_SELECT_PCT) -> Optional[float]:
    vals = sorted((r["score5"] for r in scored), reverse=True)
    if not vals:
        return None
    return vals[min(len(vals) - 1, max(0, int(len(vals) * pct)))]


def main(argv: Optional[list[str]] = None) -> None:
    p = argparse.ArgumentParser(description="Reversal-5: 5-day mean-reversion entry model")
    p.add_argument("ticker")
    p.add_argument("--history-csv", required=True)
    p.add_argument("--select-pct", type=float, default=DEFAULT_SELECT_PCT)
    p.add_argument("--json-output")
    args = p.parse_args(argv)

    bars = ms.load_history_csv(args.history_csv)
    scored = score_history(bars)
    if not scored:
        raise SystemExit("not enough history: need ~360+ daily bars")
    thr = select_threshold(scored, args.select_pct)
    latest = scored[-1]
    fires = latest["score5"] >= thr

    print(f"===== REVERSAL-5 — {args.ticker.upper()} =====")
    print(f"As of {latest['date']}  close {latest['close']:.2f}")
    print(f"  score5              : {latest['score5']:+.3f}")
    print(f"  top-{args.select_pct*100:.0f}% threshold  : {thr:+.3f}  (from this ticker's own history)")
    print(f"  SETUP               : {'BUY (5-day hold, no stop)' if fires else 'NONE'}")
    print("  inputs:")
    for k in SIGNALS:
        print(f"    {k:<20}{ms.fmt(latest[k], 2)}")
    print(f"\n  Held-out expectancy at this selectivity: +0.82% per 5-day trade "
          f"(56.4% positive). Falling-knife risk is real; see module docstring.")
    print("  Educational/research model — not investment advice.")

    if args.json_output:
        with open(args.json_output, "w", encoding="utf-8") as fh:
            json.dump({"ticker": args.ticker.upper(), "threshold": thr,
                       "latest": {**latest, "date": latest["date"].isoformat()},
                       "fires": fires}, fh, indent=2, default=str)
        print(f"\nJSON -> {args.json_output}")


if __name__ == "__main__":
    main()
