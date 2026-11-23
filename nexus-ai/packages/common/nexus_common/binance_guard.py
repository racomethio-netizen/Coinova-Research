"""Central gate for every Binance REST request (spot and USD-M futures).

Every Binance REST call in NEXUS (httpx helpers and the ccxt adapters) goes through
``acquire`` before it is sent and ``report`` after the answer. State lives in Redis, so all
workers and the API share one view; if Redis cannot be read, callers do not call Binance.

Circuit breaker per scope ("spot", "futures"):

* CLOSED     normal. Each request reserves its endpoint weight in a per-minute budget that is
             a fraction of Binance's per-IP limit (the IP is shared on free hosting: other
             tenants use the rest). Binance's ``X-MBX-USED-WEIGHT-1M`` header is recorded; at
             80% of the IP limit every caller pauses until the next minute.
* OPEN       after a 429/418 (or a "banned until" message): nobody calls until
             max(Binance's stated time, exponential back-off with random jitter). The back-off
             grows with consecutive failures (30 s, 60 s, 120 s ... capped at 1 h).
* HALF-OPEN  after the pause: exactly ONE caller (a Redis lock) may send a probe request.
             Success closes the circuit and resets the failure count; another 429/418 opens
             it again for longer. Everyone else keeps waiting: no request storm on recovery.

Every request is logged (endpoint, weight, status, duration, IP weight used, failures,
circuit state) and counted per minute in Redis for the System page.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import random
import re
import time
import weakref
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
# NEXUS's own budget per minute: a small share, because the egress IP is shared.
OWN_BUDGET = {"spot": 1200, "futures": 480}
WARN_AT = 0.5
STOP_AT = 0.8
WEIGHT_HEADER = "x-mbx-used-weight-1m"
BACKOFF_BASE = 30.0          # seconds, first failure
BACKOFF_MAX = 3600.0
PROBE_LOCK_SECONDS = 60
TOLD_JITTER = (1.0, 5.0)     # seconds added to Binance's own end time
STATE_TTL = 6 * 3600
STATS_TTL = 26 * 3600
EVENTS_KEEP = 200


class BinanceBackoff(Exception):
    """Raised instead of calling Binance while a back-off is active."""


def scope_for(url: str) -> str:
    return "futures" if "fapi." in url or "/fapi/" in url or "futures/data" in url else "spot"


def _key(scope: str) -> str:
    return f"nexus:binance:public_backoff:{scope}"


def _k(kind: str, scope: str) -> str:
    return f"nexus:binance:{kind}:{scope}"


# ---------------------------------------------------------------------- weights
SPOT_WEIGHTS = {"klines": 2, "depth": 5, "ticker/bookTicker": 2, "ticker/24hr": 2,
                "exchangeInfo": 20, "time": 1, "ping": 1, "account": 20, "myTrades": 20,
                "openOrders": 6, "order": 4, "avgPrice": 2}
FUTURES_WEIGHTS = {"premiumIndex": 1, "openInterest": 1, "openInterestHist": 1,
                   "fundingRate": 1, "globalLongShortAccountRatio": 1,
                   "topLongShortPositionRatio": 1, "takerlongshortRatio": 1, "depth": 5,
                   "exchangeInfo": 1, "time": 1}
CCXT_WEIGHTS = {"fetch_ohlcv": 2, "fetch_time": 1, "load_markets": 20, "fetch_balance": 20,
                "fetch_open_orders": 6, "fetch_my_trades": 20, "create_order": 1,
                "cancel_order": 1, "fetch_order": 4, "fetch_ticker": 2, "fetch_orders": 20,
                "fetch_order_book": 5}


def endpoint_weight(scope: str, endpoint: str, params: dict[str, Any] | None = None) -> int:
    """Binance's documented weight for an endpoint (conservative where it depends on
    parameters). Unknown endpoints count as 5."""
    params = params or {}
    name = endpoint.rsplit("/v1/", 1)[-1].rsplit("/v3/", 1)[-1].strip("/")
    limit = int(params.get("limit") or 100)
    if name.endswith("depth") or endpoint == "fetch_order_book":
        return 5 if limit <= 100 else 25 if limit <= 500 else 50
    if scope == "futures" and name.endswith("klines"):
        return 1 if limit < 100 else 2 if limit < 500 else 5 if limit <= 1000 else 10
    table = CCXT_WEIGHTS if endpoint.startswith(("fetch_", "load_", "create_", "cancel_")) \
        else FUTURES_WEIGHTS if scope == "futures" else SPOT_WEIGHTS
    for key, w in table.items():
        if name.endswith(key) or endpoint == key:
            return w
    return 5


def backoff_seconds(failures: int, *, rng: random.Random | None = None) -> float:
    """Exponential back-off with +-20% random jitter (so callers never retry in step)."""
    base = min(BACKOFF_MAX, BACKOFF_BASE * 2 ** max(0, failures - 1))
    r = (rng or random).uniform(0.8, 1.2)
    return float(base * r)


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


# ---------------------------------------------------------------------- circuit state
async def _open_until(redis: Any, scope: str) -> datetime | None:
    """The OPEN circuit's end, or None. Raises if Redis cannot be read."""
    raw = await redis.get(_key(scope))
    if not raw:
        return None
    t = datetime.fromisoformat(raw.decode() if isinstance(raw, bytes) else raw)
    return t if t > utcnow() else None


async def blocked(redis: Any, scope: str) -> datetime | None:
    """When the circuit is OPEN: its end. A peek only (never takes the probe)."""
    try:
        return await _open_until(redis, scope)
    except Exception:
        return utcnow() + DEFAULT_429                # cannot know: do not call Binance


async def record(redis: Any, scope: str, until: datetime) -> None:
    current = await blocked(redis, scope)
    if current is not None and current >= until:
        return
    seconds = max(1, int((until - utcnow()).total_seconds()))
    await redis.set(_key(scope), until.isoformat(), ex=seconds)


async def _event(redis: Any, scope: str, kind: str, **details: Any) -> None:
    try:
        await redis.lpush("nexus:binance:events", json.dumps(
            {"at": utcnow().isoformat(), "scope": scope, "event": kind, **details},
            default=str))
        await redis.ltrim("nexus:binance:events", 0, EVENTS_KEEP - 1)
    except Exception as exc:
        log.warning("binance_event_unwritable", error=type(exc).__name__)


async def open_circuit(redis: Any, scope: str, *, status: int | None, text: str,
                       retry_after: str | None, endpoint: str,
                       rng: random.Random | None = None) -> datetime:
    """Record a refusal: OPEN until max(Binance's time, jittered exponential back-off)."""
    now = utcnow()
    failures = int(await redis.incr(_k("failures", scope)))
    await redis.expire(_k("failures", scope), STATE_TTL)
    told = until_from(status, text, retry_after, now) or now + DEFAULT_429
    until = max(told + timedelta(seconds=random.uniform(*TOLD_JITTER)),  # noqa: S311
                now + timedelta(seconds=backoff_seconds(failures, rng=rng)))
    await record(redis, scope, until)
    await redis.set(_k("halfopen", scope), "1", ex=STATE_TTL)
    await redis.delete(_k("probe", scope))
    log.warning("binance_circuit_open", scope=scope, endpoint=endpoint, status=status,
                failures=failures, until=until.isoformat(), binance_until=told.isoformat())
    await _event(redis, scope, "circuit_open", endpoint=endpoint, status=status,
                 failures=failures, until=until.isoformat())
    return until


async def acquire(redis: Any, scope: str, endpoint: str, weight: int) -> bool:
    """Permission to send one request. Returns True when this request is the half-open
    probe. Raises BinanceBackoff when the request must not be sent."""
    if redis is None:
        return False
    try:
        try:
            until = await _open_until(redis, scope)
        except Exception as exc:
            # Say what happened: the state is unknown, which is not a Binance ban.
            raise BinanceBackoff(f"rate-limit state unreadable ({type(exc).__name__}); "
                                 "not calling Binance") from exc
        if until is not None:
            raise BinanceBackoff(f"Binance {scope} API paused until {until:%Y-%m-%d %H:%M:%S} UTC "
                                 "(rate limit / IP ban); not calling")
        probe = False
        if await redis.get(_k("halfopen", scope)):
            if not await redis.set(_k("probe", scope), endpoint, nx=True,
                                   ex=PROBE_LOCK_SECONDS):
                raise BinanceBackoff(f"Binance {scope} API recovering: one probe request "
                                     "is in flight; not calling")
            probe = True
        minute = int(time.time() // 60)
        wkey = f"nexus:binance:wt:{scope}:{minute}"
        used = int(await redis.incrby(wkey, weight))
        await redis.expire(wkey, 120)
        if used > OWN_BUDGET[scope]:
            await redis.decrby(wkey, weight)
            if probe:
                await redis.delete(_k("probe", scope))
            raise BinanceBackoff(f"NEXUS {scope} request budget for this minute is used "
                                 f"({OWN_BUDGET[scope]} weight); not calling")
        return probe
    except BinanceBackoff:
        raise
    except Exception as exc:                              # Redis unreadable: fail closed
        raise BinanceBackoff(f"rate-limit state unreadable ({type(exc).__name__}); "
                             "not calling Binance") from exc


async def report(redis: Any, scope: str, endpoint: str, weight: int, *, status: int | None,
                 headers: Any = None, text: str = "", ms: float | None = None,
                 probe: bool = False, retry_after: str | None = None) -> None:
    """Record the outcome of a request sent after ``acquire``."""
    if redis is None:
        return
    used = used_weight(headers)
    refused = status in (418, 429) or bool(_UNTIL.search(text or "")) or "-1003" in (text or "")
    try:
        minute = int(time.time() // 60)
        skey = f"nexus:binance:stats:{scope}:{minute}"
        await redis.hincrby(skey, "requests", 1)
        await redis.hincrby(skey, "weight", weight)
        await redis.hincrby(skey, f"status:{status if status is not None else 'error'}", 1)
        await redis.hincrby(skey, f"endpoint:{endpoint}", 1)
        await redis.expire(skey, STATS_TTL)
        await note_weight(redis, scope, used)
        failures = int(await redis.get(_k("failures", scope)) or 0)
        first = refused and not probe and bool(
            await redis.set(_k("halfopen", scope), "1", nx=True, ex=STATE_TTL))
        if refused and not probe and not first:
            # The circuit is already open (another request, sent at the same time, was
            # refused first): honour any later end time Binance gives, but this is not a
            # new failure, so the back-off does not escalate.
            told = until_from(status, text, retry_after)
            if told is not None:
                await record(redis, scope, told)
            state = "open"
        elif refused:
            await open_circuit(redis, scope, status=status, text=text,
                               retry_after=retry_after, endpoint=endpoint)
            state = "open"
        elif status is not None and status < 500 and probe:
            # Only the probe closes the circuit: a request sent before the
            # circuit opened may still come back fine and proves nothing.
            await redis.delete(_k("halfopen", scope), _k("failures", scope),
                               _k("probe", scope))
            log.warning("binance_circuit_closed", scope=scope, endpoint=endpoint,
                        after_failures=failures)
            await _event(redis, scope, "circuit_closed", endpoint=endpoint,
                         after_failures=failures)
            state = "closed"
        else:
            if probe:                                      # inconclusive (network error)
                await redis.delete(_k("probe", scope))
            state = "half_open" if await redis.get(_k("halfopen", scope)) else "closed"
    except Exception as exc:
        log.error("binance_report_failed", scope=scope, error=type(exc).__name__)
        return
    log.info("binance_request", scope=scope, endpoint=endpoint, weight=weight, status=status,
             ms=round(ms, 1) if ms is not None else None, ip_used_weight=used,
             failures=failures, circuit=state, probe=probe)


async def status(redis: Any) -> dict[str, Any]:
    """Circuit state, recent counts and events for the System page."""
    out: dict[str, Any] = {"scopes": {}, "events": []}
    minute = int(time.time() // 60)
    for scope in SCOPES:
        until = await blocked(redis, scope)
        halfopen = bool(await redis.get(_k("halfopen", scope)))
        counts: dict[str, int] = {}
        for m in range(minute - 59, minute + 1):
            for k, v in (await redis.hgetall(f"nexus:binance:stats:{scope}:{m}")).items():
                k = k.decode() if isinstance(k, bytes) else k
                counts[k] = counts.get(k, 0) + int(v)
        raw_w = await redis.get(_weight_key(scope))
        out["scopes"][scope] = {
            "circuit": "open" if until else "half_open" if halfopen else "closed",
            "paused_until": until.isoformat() if until else None,
            "consecutive_failures": int(await redis.get(_k("failures", scope)) or 0),
            "ip_used_weight": int(raw_w) if raw_w else None,
            "ip_weight_limit": WEIGHT_LIMITS[scope], "own_budget_per_minute": OWN_BUDGET[scope],
            "last_hour": counts}
    for raw in await redis.lrange("nexus:binance:events", 0, 49):
        out["events"].append(json.loads(raw))
    return out


# ---------------------------------------------------------------------- httpx helper
_inflight: dict[str, asyncio.Lock] = {}


def _cache_key(url: str, params: dict[str, Any]) -> str:
    h = hashlib.sha256(f"{url}?{sorted(params.items())}".encode()).hexdigest()[:32]
    return f"nexus:binance:cache:{h}"


async def guarded_get(client: Any, redis: Any, url: str, params: dict[str, Any], *,
                      cache_ttl: int | None = None) -> Any:
    """GET a Binance endpoint through the gate. With ``cache_ttl``, identical requests
    from any worker within that many seconds share one response (and one in-flight call
    per process), so services never fetch the same data independently."""
    import httpx

    scope = scope_for(url)
    ckey = _cache_key(url, params) if cache_ttl and redis is not None else None
    if ckey:
        lock = _inflight.setdefault(ckey, asyncio.Lock())
        async with lock:
            cached = await redis.get(ckey)
            if cached:
                return httpx.Response(200, content=cached, request=httpx.Request("GET", url),
                                      headers={"x-nexus-cache": "hit"})
            r = await _send(client, redis, scope, url, params)
            if r.status_code == 200:
                await redis.set(ckey, r.content, ex=cache_ttl)
            return r
    return await _send(client, redis, scope, url, params)


async def _send(client: Any, redis: Any, scope: str, url: str, params: dict[str, Any]) -> Any:
    endpoint = url.split("?")[0].rsplit("/", 1)[-1]
    weight = endpoint_weight(scope, url, params)
    probe = await acquire(redis, scope, endpoint, weight)
    started = time.monotonic()
    try:
        r = await client.get(url, params=params)
    except Exception:
        await report(redis, scope, endpoint, weight, status=None,
                     ms=(time.monotonic() - started) * 1000, probe=probe)
        raise
    await report(redis, scope, endpoint, weight, status=r.status_code,
                 headers=getattr(r, "headers", None),
                 text=r.text[:500] if r.status_code >= 400 else "",
                 ms=(time.monotonic() - started) * 1000, probe=probe,
                 retry_after=r.headers.get("retry-after") if hasattr(r, "headers") else None)
    return r


async def note_exception(redis: Any, scope: str, exc: BaseException) -> datetime | None:
    """Record a back-off from a library exception (e.g. ccxt DDoSProtection) if it is one.
    Kept for callers outside the gate; gated calls are reported by ``report``."""
    text = f"{exc} {exc.__cause__ or ''}"
    status_code = 418 if "418" in text else 429 if ("429" in text or "-1003" in text) else None
    until = until_from(status_code, text, None)
    if until is not None and redis is not None:
        if await blocked(redis, scope) is None:
            return await open_circuit(redis, scope, status=status_code, text=text,
                                      retry_after=None, endpoint="exception")
        return await blocked(redis, scope)
    return until


# ---------------------------------------------------------------------- weight header
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
    """Store the latest IP used weight; at STOP_AT of the limit pause every caller until
    the next minute. Returns the pause end when one was set."""
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


# ---------------------------------------------------------------------- ccxt adapters
class CcxtGate:
    """Hooks a ccxt-based adapter into the gate (``adapter.gate = CcxtGate(redis, scope)``)."""

    def __init__(self, redis: Any, scope: str) -> None:
        self.redis, self.scope = redis, scope

    def weight(self, method: str, args: tuple[Any, ...]) -> int:
        if method == "fetch_order_book":
            limit = args[1] if len(args) > 1 and isinstance(args[1], int) else 100
            return endpoint_weight(self.scope, "fetch_order_book", {"limit": limit})
        return endpoint_weight(self.scope, method)

    async def acquire(self, method: str, weight: int) -> bool:
        return await acquire(self.redis, self.scope, method, weight)

    async def report(self, method: str, weight: int, *, status: int | None, headers: Any,
                     text: str, ms: float, probe: bool) -> None:
        await report(self.redis, self.scope, method, weight, status=status, headers=headers,
                     text=text, ms=ms, probe=probe)


def attach_weight_hooks(adapters: Any, redis: Any) -> int:
    """Route every Binance spot adapter's calls through the gate."""
    n = 0
    for code, adapter in dict(adapters).items():
        if code == "binance" and hasattr(adapter, "gate"):
            adapter.gate = CcxtGate(redis, "spot")
            n += 1
    return n


# ---------------------------------------------------------------------- raw ccxt clients
_DEFAULT_REDIS: Any = None                 # registered outside any running event loop
_BY_LOOP: weakref.WeakKeyDictionary[asyncio.AbstractEventLoop, Any] = weakref.WeakKeyDictionary()


def _running_loop() -> asyncio.AbstractEventLoop | None:
    try:
        return asyncio.get_running_loop()
    except RuntimeError:
        return None


def set_default_redis(redis: Any) -> None:
    """Register the Redis client of the CURRENT event loop so request-time clients (API
    account views, the connection test) are gated without passing Redis through every
    call. Keyed by loop: the API and the background workers run on separate loops
    (threads), and an asyncio Redis client must only be used on the loop that made it."""
    global _DEFAULT_REDIS
    loop = _running_loop()
    if loop is None:
        _DEFAULT_REDIS = redis
    else:
        _BY_LOOP[loop] = redis


def default_redis() -> Any:
    loop = _running_loop()
    if loop is not None and loop in _BY_LOOP:
        return _BY_LOOP[loop]
    return _DEFAULT_REDIS


def status_of_exception(exc: BaseException) -> int | None:
    """418/429 for ccxt rate-limit errors, None when no answer arrived, else 400."""
    names = {c.__name__ for c in type(exc).__mro__}
    text = str(exc)
    if names & {"DDoSProtection", "RateLimitExceeded"}:
        return 418 if "418" in text else 429
    if "NetworkError" in names or isinstance(exc, (TimeoutError, ConnectionError)):
        return None
    return 400


class GatedClient:
    """Proxy for a raw ccxt client: every coroutine method goes through the gate."""

    def __init__(self, client: Any, redis: Any, scope: str) -> None:
        object.__setattr__(self, "_client", client)
        object.__setattr__(self, "_gate", CcxtGate(redis, scope))

    def __getattr__(self, name: str) -> Any:
        attr = getattr(self._client, name)
        if name == "close" or not asyncio.iscoroutinefunction(attr):
            return attr
        gate = self._gate

        async def call(*args: Any, **kwargs: Any) -> Any:
            weight = gate.weight(name, args)
            probe = await gate.acquire(name, weight)
            started = time.monotonic()
            try:
                result = await attr(*args, **kwargs)
            except Exception as exc:
                await gate.report(name, weight, status=status_of_exception(exc),
                                  headers=getattr(self._client, "last_response_headers", None),
                                  text=str(exc)[:500], ms=(time.monotonic() - started) * 1000,
                                  probe=probe)
                raise
            await gate.report(name, weight, status=200,
                              headers=getattr(self._client, "last_response_headers", None),
                              text="", ms=(time.monotonic() - started) * 1000, probe=probe)
            return result

        return call

    def __setattr__(self, name: str, value: Any) -> None:
        setattr(self._client, name, value)


def gate_client(client: Any, *, scope: str = "spot", environment: str = "production") -> Any:
    """Gate a production client (testnet/demo hosts have their own limits)."""
    redis = default_redis()
    if environment != "production" or redis is None:
        return client
    return GatedClient(client, redis, scope)
