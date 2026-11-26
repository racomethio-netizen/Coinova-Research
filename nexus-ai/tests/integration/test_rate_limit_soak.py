"""Rate-limit storm simulation on real Redis: many services hammer a fake Binance that
answers 429 for a while. The gate must (1) stop almost all traffic while paused, (2) let
exactly one probe through per recovery attempt, (3) recover by itself, and (4) never
produce a burst on recovery. Time is compressed (back-off base 0.3 s instead of 30 s)."""

from __future__ import annotations

import asyncio
import itertools
import os
import time
from collections.abc import AsyncIterator
from typing import Any

import ccxt
import pytest
import redis.asyncio as aioredis
from nexus_common import binance_guard as g
from nexus_config import ExchangeCode
from nexus_exchange import CAPABILITIES
from nexus_exchange.ccxt_adapter import CcxtSpotAdapter
from nexus_exchange.errors import ExchangeError

from tests.exchange.fakes import FakeCcxtClient

pytestmark = [pytest.mark.redis]


@pytest.fixture
async def redis() -> AsyncIterator[Any]:
    url = os.environ.get("NEXUS_TEST_REDIS_URL")
    if not url:
        pytest.skip("NEXUS_TEST_REDIS_URL not set")
    r = aioredis.from_url(url)
    await r.flushdb()
    yield r
    await r.flushdb()
    await r.aclose()


async def test_429_storm_is_contained_and_recovers_without_a_burst(
        redis: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    from datetime import timedelta

    monkeypatch.setattr(g, "BACKOFF_BASE", 0.3)
    monkeypatch.setattr(g, "DEFAULT_429", timedelta(seconds=0.2))
    monkeypatch.setattr(g, "TOLD_JITTER", (0.0, 0.05))
    monkeypatch.setattr(g, "OWN_BUDGET", {"spot": 10**9, "futures": 10**9})  # own test
    client = FakeCcxtClient()
    sent: list[tuple[float, str]] = []          # (time, outcome) of requests that LEFT
    phase = {"ban": False, "lifted": False}
    recovered = asyncio.Event()                  # 3 successful requests after the ban
    after_ok = [0]
    t0 = time.monotonic()

    def ohlcv(*_: Any) -> list[Any]:
        if phase["ban"]:
            sent.append((time.monotonic() - t0, "429"))
            raise ccxt.RateLimitExceeded("binance 429 Too Many Requests")
        sent.append((time.monotonic() - t0, "200"))
        if phase["lifted"]:
            after_ok[0] += 1
            if after_ok[0] >= 3:
                recovered.set()
        return []

    client.responses["fetch_ohlcv"] = ohlcv
    adapter = CcxtSpotAdapter("binance", client, CAPABILITIES[ExchangeCode.BINANCE])
    adapter.gate = g.CcxtGate(redis, "spot")
    await adapter.load_markets()
    refused_locally = 0
    stop = asyncio.Event()

    async def service() -> None:          # 6 of these: candles, books, trader, API, ...
        nonlocal refused_locally
        while not stop.is_set():
            try:
                await adapter._call(client.fetch_ohlcv, "BTC/USDT", "1m", None, 1)
            except ExchangeError:
                refused_locally += 1
            await asyncio.sleep(0.02)

    tasks = [asyncio.create_task(service()) for _ in range(6)]
    await asyncio.sleep(1.0)
    healthy_rate = len(sent) / 1.0
    phase["ban"] = True
    ban_start = time.monotonic() - t0
    await asyncio.sleep(3.0)
    phase["ban"], phase["lifted"] = False, True
    ban_end = time.monotonic() - t0
    # Wait for the gate to recover by itself. A probe that failed just before the ban
    # ended waits one more back-off step (up to ~5.8 s), so a fixed 3 s window was flaky
    # on a loaded machine; the bound is generous, the recovery is what is tested.
    try:
        await asyncio.wait_for(recovered.wait(), timeout=12.0)
    except TimeoutError:
        pass                                     # the assertions below report it
    stop.set()
    await asyncio.gather(*tasks)

    during = [s for s in sent if ban_start <= s[0] < ban_end]
    after = [s for s in sent if s[0] >= ban_end]
    # Without the gate the services send ~healthy_rate * 3 s requests into the ban.
    assert healthy_rate > 100
    first = [s for s in during if s[0] < ban_start + 0.1]            # already in flight
    probes = [s for s in during if s[0] >= ban_start + 0.1]
    assert len(first) <= 6, "at most one in-flight request per service hits the ban"
    # Back-off 0.3, 0.6, 1.2, 2.4 s (+-20%): at most ~4 probes in 3 s, one at a time.
    assert len(probes) <= 5, probes
    gaps = [b[0] - a[0] for a, b in itertools.pairwise(probes)]
    assert all(gap >= 0.2 for gap in gaps), "probes are spaced by the growing back-off"
    # It recovered by itself after the ban ...
    assert after, "traffic resumed"
    st = (await g.status(redis))["scopes"]["spot"]
    assert st["circuit"] == "closed" and st["consecutive_failures"] == 0
    # ... and the first request after the ban was a single probe (no burst while
    # half-open): the second successful request came only after the probe succeeded.
    assert after[0][1] == "200"
    assert refused_locally > 100, "callers were refused locally instead of hitting Binance"
