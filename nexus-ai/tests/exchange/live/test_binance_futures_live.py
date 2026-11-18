"""LIVE read-only checks against Binance USD-M futures. Skipped unless explicitly enabled.

Enable only from an account-eligible region with:  NEXUS_FUTURES_LIVE=1
These calls are read-only (ping, exchange info, mark price, open interest). Order tests
are NOT here: they need a testnet or an approved live session.
"""

from __future__ import annotations

import os

import pytest
from nexus_exchange.binance_futures import BinanceFuturesAdapter

pytestmark = pytest.mark.live_exchange


@pytest.fixture
async def adapter():  # type: ignore[no-untyped-def]
    if os.environ.get("NEXUS_FUTURES_LIVE") != "1":
        pytest.skip("set NEXUS_FUTURES_LIVE=1 from an eligible region to run")
    from nexus_exchange.binance_futures_transport import CcxtFuturesTransport

    t = CcxtFuturesTransport("", "", cafile=os.environ.get("NEXUS_EXTRA_CA_FILE"))
    try:
        yield BinanceFuturesAdapter(t)
    finally:
        await t.close()


async def test_ping_and_rules(adapter: BinanceFuturesAdapter) -> None:
    assert await adapter.ping()
    rules = await adapter.rules("BTC/USDT:USDT")
    assert rules.step_size > 0 and rules.tick_size > 0


async def test_mark_funding_and_open_interest(adapter: BinanceFuturesAdapter) -> None:
    m = await adapter.mark("BTC/USDT:USDT")
    assert m["mark_price"] > 0 and m["index_price"] > 0
    assert await adapter.open_interest("BTC/USDT:USDT") > 0
