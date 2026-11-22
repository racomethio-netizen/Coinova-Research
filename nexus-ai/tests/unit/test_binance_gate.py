"""Central Binance gate: circuit breaker, single probe on recovery, jittered back-off,
per-minute budget, shared cache, statistics. No network, no real Redis."""

from __future__ import annotations

import asyncio
import random
from datetime import timedelta
from typing import Any

import httpx
import pytest
from nexus_common import binance_guard as g
from nexus_common.timeutil import utcnow

from tests.memredis import MemRedis

FAPI = "https://fapi.binance.com/fapi/v1/premiumIndex"


async def _expire_pause(r: MemRedis, scope: str) -> None:
    await r.set(g._key(scope), (utcnow() - timedelta(seconds=1)).isoformat())


def test_backoff_grows_exponentially_with_bounded_jitter_and_cap() -> None:
    rng = random.Random(1)
    for n, base in [(1, 30), (2, 60), (3, 120), (5, 480)]:
        values = [g.backoff_seconds(n, rng=rng) for _ in range(200)]
        assert min(values) >= base * 0.8 and max(values) <= base * 1.2
        assert len({round(v, 3) for v in values}) > 150          # really randomized
    assert g.backoff_seconds(30, rng=rng) <= g.BACKOFF_MAX * 1.2


def test_endpoint_weights_follow_binance_documentation() -> None:
    assert g.endpoint_weight("spot", "https://x/api/v3/klines") == 2
    assert g.endpoint_weight("spot", "https://x/api/v3/depth", {"limit": 50}) == 5
    assert g.endpoint_weight("spot", "https://x/api/v3/depth", {"limit": 500}) == 25
    assert g.endpoint_weight("spot", "https://x/api/v3/depth", {"limit": 1000}) == 50
    assert g.endpoint_weight("futures", "https://x/fapi/v1/klines", {"limit": 1000}) == 5
    assert g.endpoint_weight("futures", "https://x/fapi/v1/premiumIndex") == 1
    assert g.endpoint_weight("spot", "load_markets") == 20
    assert g.endpoint_weight("spot", "fetch_ohlcv") == 2
    assert g.endpoint_weight("spot", "something_new") == 5


async def test_full_circuit_cycle_open_half_open_single_probe_closed() -> None:
    r = MemRedis()
    assert await g.acquire(r, "spot", "klines", 2) is False          # closed: no probe
    await g.report(r, "spot", "klines", 2, status=429, text="Too Many Requests",
                   retry_after="30")
    assert (await g.status(r))["scopes"]["spot"]["circuit"] == "open"
    with pytest.raises(g.BinanceBackoff, match="paused until"):
        await g.acquire(r, "spot", "klines", 2)
    await _expire_pause(r, "spot")
    assert await g.acquire(r, "spot", "klines", 2) is True           # THE probe
    with pytest.raises(g.BinanceBackoff, match="one probe request is in flight"):
        await g.acquire(r, "spot", "depth", 5)                       # everyone else waits
    await g.report(r, "spot", "klines", 2, status=200, probe=True)
    st = (await g.status(r))["scopes"]["spot"]
    assert st["circuit"] == "closed" and st["consecutive_failures"] == 0
    assert await g.acquire(r, "spot", "depth", 5) is False
    events = [e["event"] for e in (await g.status(r))["events"]]
    assert events == ["circuit_closed", "circuit_open"]


async def test_failed_probe_reopens_for_longer() -> None:
    r = MemRedis()
    await g.report(r, "futures", "premiumIndex", 1, status=429, text="")
    first = await g.blocked(r, "futures")
    await _expire_pause(r, "futures")
    assert await g.acquire(r, "futures", "premiumIndex", 1) is True
    await g.report(r, "futures", "premiumIndex", 1, status=429, text="", probe=True)
    second = await g.blocked(r, "futures")
    assert first is not None and second is not None
    assert (await g.status(r))["scopes"]["futures"]["consecutive_failures"] == 2
    # Second back-off is at least the 60 s band (minus jitter), longer than the first's 30 s.
    assert (second - utcnow()).total_seconds() >= 60 * 0.8 - 2


async def test_response_of_a_request_sent_before_the_pause_does_not_close_it() -> None:
    r = MemRedis()
    await g.acquire(r, "spot", "klines", 2)                           # in flight
    await g.report(r, "spot", "depth", 5, status=418, text="banned")  # someone got 418
    await g.report(r, "spot", "klines", 2, status=200)                # the in-flight one
    assert (await g.status(r))["scopes"]["spot"]["circuit"] == "open"


async def test_banned_until_from_binance_is_respected_plus_jitter() -> None:
    r = MemRedis()
    ms = int((utcnow() + timedelta(minutes=20)).timestamp() * 1000)
    await g.report(r, "spot", "klines", 2, status=418,
                   text=f'{{"code":-1003,"msg":"IP banned until {ms}."}}')
    until = await g.blocked(r, "spot")
    assert until is not None
    assert 20 * 60 <= (until - utcnow()).total_seconds() <= 20 * 60 + 6


async def test_own_budget_per_minute_refuses_before_sending() -> None:
    r = MemRedis()
    for _ in range(g.OWN_BUDGET["futures"] // 5):
        await g.acquire(r, "futures", "depth", 5)
    with pytest.raises(g.BinanceBackoff, match="budget"):
        await g.acquire(r, "futures", "depth", 5)
    assert await g.blocked(r, "futures") is None        # a budget stop is not a ban


async def test_unreadable_redis_means_do_not_call() -> None:
    with pytest.raises(g.BinanceBackoff, match="not calling"):
        await g.acquire(MemRedis(broken=True), "spot", "klines", 2)


async def test_statistics_per_endpoint_and_status() -> None:
    r = MemRedis()
    await g.report(r, "spot", "klines", 2, status=200, headers={"X-MBX-USED-WEIGHT-1M": "40"})
    await g.report(r, "spot", "klines", 2, status=200)
    await g.report(r, "spot", "depth", 5, status=None)
    st = (await g.status(r))["scopes"]["spot"]
    h = st["last_hour"]
    assert h["requests"] == 3 and h["weight"] == 9
    assert h["endpoint:klines"] == 2 and h["status:200"] == 2 and h["status:error"] == 1
    assert st["ip_used_weight"] == 40


async def test_shared_cache_turns_concurrent_identical_requests_into_one() -> None:
    r = MemRedis()
    sent: list[str] = []

    def handler(req: httpx.Request) -> httpx.Response:
        sent.append(str(req.url))
        return httpx.Response(200, json={"markPrice": "1"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as c:
        got = await asyncio.gather(*(g.guarded_get(c, r, FAPI, {"symbol": "BTCUSDT"},
                                                   cache_ttl=60) for _ in range(5)))
        await g.guarded_get(c, r, FAPI, {"symbol": "ETHUSDT"}, cache_ttl=60)
    assert len(sent) == 2                                # one per distinct request
    assert all(x.json() == {"markPrice": "1"} for x in got)


async def test_gated_raw_client_proxy() -> None:
    r = MemRedis()

    class Raw:
        calls = 0

        def __init__(self) -> None:
            self.last_response_headers: dict[str, str] = {}
            self.options: dict[str, Any] = {}

        async def fetch_balance(self) -> dict[str, Any]:
            Raw.calls += 1
            return {"free": {}}

        async def close(self) -> None:
            return None

    client = g.GatedClient(Raw(), r, "spot")
    client.options["x"] = 1                                # plain attributes pass through
    assert await client.fetch_balance() == {"free": {}}
    await r.set(g._key("spot"), (utcnow() + timedelta(minutes=5)).isoformat())
    with pytest.raises(g.BinanceBackoff):
        await client.fetch_balance()
    assert Raw.calls == 1
    assert (await g.status(r))["scopes"]["spot"]["last_hour"]["endpoint:fetch_balance"] == 1
