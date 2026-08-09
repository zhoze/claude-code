"""US exchange calendar + pre-open timing (spec §48 step 1, §49).

Uses pandas_market_calendars (XNYS) when installed; otherwise falls back to a
weekday + fixed-holiday approximation and flags the fallback.
"""

from __future__ import annotations

import datetime as dt
from zoneinfo import ZoneInfo

NY = ZoneInfo("America/New_York")

try:
    import pandas_market_calendars as mcal

    _XNYS = mcal.get_calendar("XNYS")
    HAS_MCAL = True
except Exception:  # pragma: no cover
    _XNYS = None
    HAS_MCAL = False

# Fallback fixed-date holiday approximation (only used without pandas_market_calendars)
_APPROX_HOLIDAYS_MMDD = {(1, 1), (6, 19), (7, 4), (12, 25)}


def now_ny() -> dt.datetime:
    return dt.datetime.now(tz=NY)


def is_trading_day(d: dt.date | None = None) -> bool:
    d = d or now_ny().date()
    if HAS_MCAL:
        sched = _XNYS.schedule(start_date=d, end_date=d)
        return not sched.empty
    if d.weekday() >= 5:
        return False
    return (d.month, d.day) not in _APPROX_HOLIDAYS_MMDD


def market_open(d: dt.date | None = None) -> dt.datetime:
    """Regular-session open (handles half days when mcal is present)."""
    d = d or now_ny().date()
    if HAS_MCAL:
        sched = _XNYS.schedule(start_date=d, end_date=d)
        if not sched.empty:
            return sched.iloc[0]["market_open"].tz_convert(NY).to_pydatetime()
    return dt.datetime.combine(d, dt.time(9, 30), tzinfo=NY)


def next_trading_days(start: dt.date, n: int) -> list[dt.date]:
    """The next n trading days strictly after `start` (for outcome horizons)."""
    days: list[dt.date] = []
    d = start
    while len(days) < n:
        d += dt.timedelta(days=1)
        if is_trading_day(d):
            days.append(d)
    return days


def minutes_to_open(ts: dt.datetime | None = None) -> float:
    ts = ts or now_ny()
    return (market_open(ts.date()) - ts).total_seconds() / 60.0
