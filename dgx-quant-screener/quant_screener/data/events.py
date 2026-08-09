"""Company-specific events, catalysts and event risk (spec §26).

Earnings proximity is the dominant mechanical risk: an ordinary technical setup
is NOT treated as a normal trade when earnings are imminent.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field

import pandas as pd


@dataclass
class EventAssessment:
    ticker: str
    next_earnings: dt.date | None = None
    days_to_earnings: int | None = None
    event_risk_penalty: float = 0.0       # 0..config max, subtracted from final score
    catalyst_score: float = 50.0          # 0-100 (identified positive catalysts)
    flags: list[str] = field(default_factory=list)


def assess_events(ticker: str, calendar: dict, as_of: dt.date,
                  max_penalty: float) -> EventAssessment:
    a = EventAssessment(ticker=ticker)
    dates = []
    for d in calendar.get("earnings_dates", []):
        try:
            parsed = pd.to_datetime(d).date()
        except Exception:
            continue
        if parsed >= as_of:
            dates.append(parsed)
    if dates:
        a.next_earnings = min(dates)
        a.days_to_earnings = (a.next_earnings - as_of).days
        if a.days_to_earnings <= 2:
            a.event_risk_penalty = max_penalty
            a.flags.append(f"EARNINGS_IMMINENT ({a.next_earnings}) — full event-risk penalty")
        elif a.days_to_earnings <= 7:
            a.event_risk_penalty = max_penalty * 0.6
            a.flags.append(f"EARNINGS_WITHIN_WEEK ({a.next_earnings})")
        elif a.days_to_earnings <= 14:
            a.event_risk_penalty = max_penalty * 0.25
            a.flags.append(f"EARNINGS_WITHIN_2W ({a.next_earnings})")
        # a known upcoming report is also a (dated, disclosed) catalyst
        a.catalyst_score = 55.0 if a.days_to_earnings > 14 else 50.0
    else:
        a.flags.append("no earnings date available — event risk unknown, mild penalty")
        a.event_risk_penalty = max_penalty * 0.15
    return a
