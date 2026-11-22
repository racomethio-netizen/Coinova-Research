"""Shared back-off for Binance PUBLIC market-data APIs (spot and USD-M futures).

Binance escalates IP bans when requests keep coming during a 429/418. Every worker and the
API check ``blocked`` before calling Binance and ``record`` what Binance said after a
refusal, so one refusal pauses ALL callers of that API until the stated time. The state is
in Redis; if Redis is unreachable callers treat the API as blocked (fail closed).
"""

from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta
from typing import Any

from nexus_common.timeutil import utcnow

SCOPES = ("spot", "futures")
_UNTIL = re.compile(r"banned until (\d{10,13})")
DEFAULT_429 = timedelta(minutes=2)
DEFAULT_418 = timedelta(minutes=30)
MAX_WAIT = timedelta(days=4)


class BinanceBackoff(Exception):
    """Raised instead of calling Binance while a back-off is active."""


def scope_for(url: str) -> str:
    return "futures" if "fapi." in url or "/fapi/" in url or "futures/data" in url else "spot"


def _key(scope: str) -> str:
    return f"nexus:binance:public_backoff:{scope}"


def until_from(status: int | None, text: str, retry_after: str | None,
               now: datetime | None = None) -> datetime | None:
    """End of the back-off implied by a Binance refusal, or None if it is not one."""
    now = now or utcnow()
    m = _UNTIL.search(text or "")
    if m:
        raw = int(m.group(1))
        t = datetime.fromtimestamp(raw / (1000 if raw > 10**11 else 1), tz=UTC)
        return min(t, now + MAX_WAIT) if t > now else now + DEFAULT_429
    if status in (418, 429) or "-1003" in (text or "") or "Too Many Requests" in (text or ""):
        if retry_after and retry_after.isdigit():
            return now + timedelta(seconds=int(retry_after))
        return now + (DEFAULT_418 if status == 418 or " 418 " in (text or "") else DEFAULT_429)
    return None


async def blocked(redis: Any, scope: str) -> datetime | None:
    try:
        raw = await redis.get(_key(scope))
    except Exception:
        return utcnow() + DEFAULT_429                # cannot know: do not call Binance
    if not raw:
        return None
    t = datetime.fromisoformat(raw.decode() if isinstance(raw, bytes) else raw)
    return t if t > utcnow() else None


async def record(redis: Any, scope: str, until: datetime) -> None:
    current = await blocked(redis, scope)
    if current is not None and current >= until:
        return
    seconds = max(1, int((until - utcnow()).total_seconds()))
    await redis.set(_key(scope), until.isoformat(), ex=seconds)


async def guarded_get(client: Any, redis: Any, url: str, params: dict[str, Any]) -> Any:
    """GET a Binance public endpoint unless backed off; records refusals."""
    scope = scope_for(url)
    if redis is not None:
        until = await blocked(redis, scope)
        if until is not None:
            raise BinanceBackoff(f"Binance {scope} API paused until {until:%H:%M} UTC "
                                 "(rate limit / IP ban); not calling")
    r = await client.get(url, params=params)
    if redis is not None and r.status_code in (418, 429):
        until = until_from(r.status_code, r.text[:500], r.headers.get("retry-after"))
        if until is not None:
            await record(redis, scope, until)
    return r


async def note_exception(redis: Any, scope: str, exc: BaseException) -> datetime | None:
    """Record a back-off from a library exception (e.g. ccxt DDoSProtection) if it is one."""
    text = f"{exc} {exc.__cause__ or ''}"
    status = 418 if "418" in text else 429 if ("429" in text or "-1003" in text) else None
    until = until_from(status, text, None)
    if until is not None and redis is not None:
        await record(redis, scope, until)
    return until
