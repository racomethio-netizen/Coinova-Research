"""Trading sessions per instrument (multi-asset Stage 2).

Crypto never closes; stocks, futures and forex do, and gap when they reopen. Every check
that assumes continuous data ("is this price stale?") must first ask whether the market
was open.

Session kinds:
* ``24x7``    crypto, and Binance perpetuals that follow stocks or commodities.
* ``fx_24x5`` spot forex. It opens Sunday 17:00 New York time and closes Friday 17:00
              New York time (the standard FX week, daylight saving included). Holiday
              thin trading is not modelled.
* ``XNYS``    the New York Stock Exchange regular session, with its holidays and early
              closes (``exchange_calendars``).
* ``CMES``    CME Globex (``exchange_calendars``). The product-specific hours of each
              futures contract are read from the broker at connection time.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from functools import lru_cache
from typing import Any
from zoneinfo import ZoneInfo

NEW_YORK = ZoneInfo("America/New_York")
KINDS = ("24x7", "fx_24x5", "XNYS", "CMES")


@lru_cache(maxsize=4)
def _calendar(code: str) -> Any:
    import exchange_calendars as xc

    return xc.get_calendar(code, start="2020-01-01")


def _fx_open(ts: datetime) -> bool:
    ny = ts.astimezone(NEW_YORK)
    wd, minutes = ny.weekday(), ny.hour * 60 + ny.minute      # Monday = 0, Sunday = 6
    if wd == 5:                                               # Saturday
        return False
    if wd == 6:                                               # Sunday: from 17:00
        return minutes >= 17 * 60
    if wd == 4:                                               # Friday: until 17:00
        return minutes < 17 * 60
    return True


def is_open(kind: str, ts: datetime) -> bool:
    """Whether the market of this session kind trades at ``ts`` (timezone-aware)."""
    if ts.tzinfo is None:
        raise ValueError("timestamp must be timezone-aware")
    if kind == "24x7":
        return True
    if kind == "fx_24x5":
        return _fx_open(ts)
    if kind in ("XNYS", "CMES"):
        import pandas as pd

        return bool(_calendar(kind).is_open_on_minute(pd.Timestamp(ts).floor("min"),
                                                      ignore_breaks=False))
    raise ValueError(f"unknown session kind {kind!r}")


def next_open(kind: str, ts: datetime, *, limit_days: int = 10) -> datetime | None:
    """The first minute at or after ``ts`` when the market is open (None beyond
    ``limit_days``). Used to label a closed market's reopening time."""
    if is_open(kind, ts):
        return ts
    if kind in ("XNYS", "CMES"):
        import pandas as pd

        nxt = _calendar(kind).next_open(pd.Timestamp(ts).floor("min"))
        out: datetime = nxt.to_pydatetime()
        return out
    t = ts.replace(second=0, microsecond=0)
    end = ts + timedelta(days=limit_days)
    while t < end:
        t += timedelta(minutes=15 - t.minute % 15)
        if is_open(kind, t):
            return t
    return None
