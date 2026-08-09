"""Data-integrity guards (spec §2).

Every dataset that enters the pipeline is wrapped in a Provenance record so the
report can state DATA_TIMESTAMP / RUN_TIMESTAMP / MARKET_SESSION / DATA_SOURCE /
DATA_FRESHNESS, and so stale or missing data lowers confidence instead of being
silently substituted.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field

from ..calendar_utils import NY, market_open, now_ny


@dataclass
class Provenance:
    source: str
    data_timestamp: dt.datetime | None  # newest observation in the dataset
    run_timestamp: dt.datetime = field(default_factory=now_ny)
    notes: list[str] = field(default_factory=list)

    @property
    def freshness_hours(self) -> float | None:
        if self.data_timestamp is None:
            return None
        ts = self.data_timestamp
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=NY)
        return (self.run_timestamp - ts).total_seconds() / 3600.0

    def is_stale(self, max_hours: float) -> bool:
        fh = self.freshness_hours
        return fh is None or fh > max_hours

    @property
    def market_session(self) -> str:
        t = self.run_timestamp
        mo = market_open(t.date())
        if t < mo:
            return "PRE_MARKET"
        if t < mo.replace(hour=16, minute=0):
            return "REGULAR"
        return "AFTER_HOURS"

    def flag(self, msg: str) -> None:
        if msg not in self.notes:
            self.notes.append(msg)

    def as_dict(self) -> dict:
        return {
            "DATA_SOURCE": self.source,
            "DATA_TIMESTAMP": self.data_timestamp.isoformat() if self.data_timestamp else None,
            "RUN_TIMESTAMP": self.run_timestamp.isoformat(),
            "MARKET_SESSION": self.market_session,
            "DATA_FRESHNESS_HOURS": self.freshness_hours,
            "NOTES": list(self.notes),
        }


def assert_point_in_time(as_of: dt.date, observation_date: dt.date, what: str) -> None:
    """Hard guard against look-ahead: raises if a record dated after `as_of` is used."""
    if observation_date > as_of:
        raise LookAheadError(
            f"{what}: observation dated {observation_date} used at as_of={as_of}"
        )


class LookAheadError(RuntimeError):
    pass


def filter_point_in_time(df, date_col: str, as_of: dt.date):
    """Keep only rows observable at `as_of` (publication date, not period date)."""
    import pandas as pd

    if df is None or len(df) == 0:
        return df
    dates = pd.to_datetime(df[date_col]).dt.date
    return df[dates <= as_of]
