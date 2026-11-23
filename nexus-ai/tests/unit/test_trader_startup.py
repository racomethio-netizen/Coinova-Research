"""Trader start-up waits for a Binance pause instead of crashing and restarting."""

from __future__ import annotations

import asyncio
from datetime import timedelta

from nexus_common import binance_guard as g
from nexus_common.timeutil import utcnow
from nexus_exchange.errors import RateLimited
from nexus_execution.trader import load_markets_when_allowed

from tests.memredis import MemRedis


class Adapter:
    def __init__(self, failures: int) -> None:
        self.failures, self.calls = failures, 0

    async def load_markets(self) -> None:
        self.calls += 1
        if self.calls <= self.failures:
            raise RateLimited("Binance spot API paused; not calling")


async def test_waits_until_the_pause_ends_then_loads() -> None:
    r = MemRedis()
    await g.record(r, "spot", utcnow() + timedelta(seconds=1))
    a = Adapter(failures=1)
    ok = await load_markets_when_allowed({"binance": a}, r, asyncio.Event(), retry_s=0.05)
    assert ok and a.calls == 2


async def test_stop_while_waiting_returns_false() -> None:
    stop = asyncio.Event()
    a = Adapter(failures=100)

    async def stopper() -> None:
        await asyncio.sleep(0.1)
        stop.set()

    task = asyncio.create_task(stopper())
    loop = asyncio.get_running_loop()
    started = loop.time()
    ok = await load_markets_when_allowed({"binance": a}, MemRedis(), stop, retry_s=30)
    await task
    # The stop interrupts the 30 s wait at once; nothing is retried after it.
    assert ok is False and a.calls == 1 and loop.time() - started < 5


async def test_other_errors_still_raise() -> None:
    class Broken:
        async def load_markets(self) -> None:
            raise ValueError("bug")

    import pytest
    with pytest.raises(ValueError):
        await load_markets_when_allowed({"x": Broken()}, MemRedis(), asyncio.Event())
