"""Timeframe arithmetic. All times are timezone-aware UTC datetimes."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta

TIMEFRAME_SECONDS: dict[str, int] = {"15m": 900, "1h": 3600, "4h": 14_400, "1d": 86_400}
TIMEFRAMES: tuple[str, ...] = tuple(TIMEFRAME_SECONDS)


def tf_delta(tf: str) -> timedelta:
    try:
        return timedelta(seconds=TIMEFRAME_SECONDS[tf])
    except KeyError:
        raise ValueError(f"unsupported timeframe {tf!r}") from None


def require_utc(dt: datetime) -> datetime:
    if dt.tzinfo is None or dt.utcoffset() != timedelta(0):
        raise ValueError("datetime must be timezone-aware UTC")
    return dt


def floor_to(dt: datetime, tf: str) -> datetime:
    """Start of the candle that contains `dt`."""
    require_utc(dt)
    step = TIMEFRAME_SECONDS[tf]
    epoch = int(dt.timestamp())
    return datetime.fromtimestamp(epoch - epoch % step, tz=UTC)


def is_aligned(open_time: datetime, tf: str) -> bool:
    require_utc(open_time)
    return open_time.microsecond == 0 and int(open_time.timestamp()) % TIMEFRAME_SECONDS[tf] == 0


def close_time_for(open_time: datetime, tf: str) -> datetime:
    """Exchange convention: close time is the last millisecond of the candle."""
    return open_time + tf_delta(tf) - timedelta(milliseconds=1)


def last_closed_open_time(now: datetime, tf: str) -> datetime:
    """Open time of the most recent candle that has fully closed at `now`."""
    return floor_to(now, tf) - tf_delta(tf)


def open_times(start: datetime, end: datetime, tf: str) -> Iterator[datetime]:
    """Aligned open times in [start, end] (inclusive)."""
    t = floor_to(start, tf)
    if t < start:
        t += tf_delta(tf)
    step = tf_delta(tf)
    while t <= end:
        yield t
        t += step
