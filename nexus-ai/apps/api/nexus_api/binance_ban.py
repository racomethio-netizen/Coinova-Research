"""Respect Binance IP bans (HTTP 418 / code -1003).

Binance escalates a ban when requests keep arriving during it, so once Binance reports
"banned until <ms>", NEXUS makes no further request-time calls to that Binance environment
from this server until then. The ban is stored in Redis so every process sees it.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta

from nexus_common.timeutil import utcnow

_UNTIL = re.compile(r"banned until (\d{10,13})")
# A rate-limit rejection without a stated end: back off for a fixed period.
_RATE_LIMITED = re.compile(r"-1003|Way too many requests|\b418\b|\b429\b", re.IGNORECASE)
DEFAULT_BACKOFF = timedelta(minutes=5)
MAX_BAN = timedelta(days=4)


def ban_until(text: str, *, now: datetime | None = None) -> datetime | None:
    """End of the ban described by a Binance error text, or None if it is not one."""
    now = now or utcnow()
    m = _UNTIL.search(text)
    if m:
        raw = int(m.group(1))
        until = datetime.fromtimestamp(raw / (1000 if raw > 10**11 else 1), tz=UTC)
        return min(until, now + MAX_BAN) if until > now else None
    if _RATE_LIMITED.search(text):
        return now + DEFAULT_BACKOFF
    return None


def _key(environment: str) -> str:
    return f"nexus:binance:ban:{environment}"


async def active_ban(redis: object, environment: str) -> datetime | None:
    raw = await redis.get(_key(environment))  # type: ignore[attr-defined]
    if not raw:
        return None
    until = datetime.fromisoformat(raw.decode() if isinstance(raw, bytes) else raw)
    return until if until > utcnow() else None


async def record_ban(redis: object, environment: str, text: str) -> datetime | None:
    """Store the ban described by ``text`` (if any); returns its end."""
    until = ban_until(text)
    if until is None:
        return None
    seconds = max(1, int((until - utcnow()).total_seconds()))
    await redis.set(_key(environment), until.isoformat(), ex=seconds)  # type: ignore[attr-defined]
    return until


def ban_message(until: datetime) -> str:
    return ("Binance has temporarily blocked this server's IP address for too many requests "
            f"(until {until.strftime('%Y-%m-%d %H:%M')} UTC). NEXUS sends no requests to "
            "Binance until then, because requests during a ban make it longer.")
