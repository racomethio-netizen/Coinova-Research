"""UTC-only time helpers. Naive datetimes are rejected everywhere."""

from __future__ import annotations

from datetime import UTC, datetime


def utcnow() -> datetime:
    return datetime.now(UTC)


def from_ms(ms: int | float) -> datetime:
    return datetime.fromtimestamp(int(ms) / 1000, tz=UTC)


def to_ms(dt: datetime) -> int:
    return int(ensure_utc(dt).timestamp() * 1000)


def ensure_utc(dt: datetime) -> datetime:
    if dt.tzinfo is None or dt.utcoffset() is None:
        raise ValueError("naive datetime; all timestamps must be timezone-aware UTC")
    return dt.astimezone(UTC)
