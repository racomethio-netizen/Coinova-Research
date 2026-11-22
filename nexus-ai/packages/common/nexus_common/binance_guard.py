"""Shared back-off for Binance PUBLIC market-data APIs (spot and USD-M futures).

Binance escalates IP bans when requests keep coming during a 429/418. Every worker and the
API check ``blocked`` before calling Binance and ``record`` what Binance said after a
refusal, so one refusal pauses ALL callers of that API until the stated time. The state is
in Redis; if Redis is unreachable callers treat the API as blocked (fail closed).

Request-weight budget: Binance reports the weight used by this IP in the current minute
(``X-MBX-USED-WEIGHT-1M``). At 50% of the limit a warning is logged; at 80% every caller
pauses until the next minute starts, so NEXUS never reaches the limit itself. The IP is
shared on free hosting, so other tenants' usage counts too: that is why the stop is at 80%.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta
from typing import Any

from nexus_common.logging import get_logger
from nexus_common.timeutil import utcnow

log = get_logger("nexus.binance_guard")

SCOPES = ("spot", "futures")
_UNTIL = re.compile(r"banned until (\d{10,13})")
DEFAULT_429 = timedelta(minutes=2)
DEFAULT_418 = timedelta(minutes=30)
MAX_WAIT = timedelta(days=4)
# Per-IP request weight per minute (Binance REQUEST_WEIGHT limits).
WEIGHT_LIMITS = {"spot": 6000, "futures": 2400}
WARN_AT = 0.5
STOP_AT = 0.8
WEIGHT_HEADER = "x-mbx-used-weight-1m"


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
    if redis is not None:
        await note_weight(redis, scope, used_weight(getattr(r, "headers", None)))
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


def used_weight(headers: Any) -> int | None:
    """The used-weight value from a response's headers (any mapping, any key case)."""
    if not headers:
        return None
    for k, v in dict(headers).items():
        if str(k).lower() == WEIGHT_HEADER:
            try:
                return int(str(v))
            except ValueError:
                return None
    return None


def _weight_key(scope: str) -> str:
    return f"nexus:binance:weight:{scope}"


async def note_weight(redis: Any, scope: str, used: int | None,
                      now: datetime | None = None) -> datetime | None:
    """Store the latest used weight; at STOP_AT of the limit pause every caller until the
    next minute. Returns the pause end when one was set."""
    if used is None or redis is None:
        return None
    now = now or utcnow()
    limit = WEIGHT_LIMITS[scope]
    try:
        await redis.set(_weight_key(scope), str(used), ex=60)
    except Exception:
        return None
    if used >= limit * STOP_AT:
        until = now.replace(second=0, microsecond=0) + timedelta(minutes=1, seconds=1)
        await record(redis, scope, until)
        log.warning("binance_weight_pause", scope=scope, used=used, limit=limit,
                    until=until.isoformat())
        return until
    if used >= limit * WARN_AT:
        log.warning("binance_weight_high", scope=scope, used=used, limit=limit)
    return None


async def weight_status(redis: Any) -> dict[str, dict[str, int | None]]:
    out: dict[str, dict[str, int | None]] = {}
    for scope in SCOPES:
        try:
            raw = await redis.get(_weight_key(scope))
        except Exception:
            raw = None
        out[scope] = {"used": int(raw) if raw else None, "limit": WEIGHT_LIMITS[scope]}
    return out


def attach_weight_hooks(adapters: Any, redis: Any) -> int:
    """Feed every Binance spot adapter's used weight into the shared budget."""
    n = 0
    for code, adapter in dict(adapters).items():
        if code == "binance" and hasattr(adapter, "weight_hook"):
            async def hook(used: int) -> None:
                await note_weight(redis, "spot", used)
            adapter.weight_hook = hook
            n += 1
    return n
