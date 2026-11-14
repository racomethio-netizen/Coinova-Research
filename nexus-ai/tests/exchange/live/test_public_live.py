"""Read-only checks against the REAL exchange APIs (public endpoints, no keys).

Run explicitly:  pytest -m live_exchange tests/exchange/live
These never place orders and need no credentials.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import timedelta
from itertools import pairwise

import pytest
from nexus_common.timeutil import utcnow
from nexus_config import ExchangeCode, Settings, Timeframe
from nexus_exchange import ExchangeAdapter
from nexus_exchange.factory import build_from_settings

pytestmark = pytest.mark.live_exchange

EXCHANGES = [ExchangeCode.BINANCE, ExchangeCode.KRAKEN, ExchangeCode.COINBASE]
PAIRS = ["BTC/USDT", "ETH/USDT"]


@pytest.fixture(params=EXCHANGES, ids=lambda c: c.value)
async def adapter(request: pytest.FixtureRequest) -> AsyncIterator[ExchangeAdapter]:
    a = build_from_settings(request.param, Settings())
    try:
        await a.load_markets()
        yield a
    finally:
        await a.close()


async def test_markets_have_precision(adapter: ExchangeAdapter) -> None:
    for pair in PAIRS:
        rules = await adapter.market_rules(pair)
        assert rules.base == pair.split("/")[0] and rules.quote == "USDT"
        assert rules.price_tick > 0 and rules.amount_step > 0
        print(adapter.code, pair, "tick", rules.price_tick, "step", rules.amount_step,
              "min_amount", rules.min_amount, "min_notional", rules.min_notional)


async def test_ticker_and_order_book(adapter: ExchangeAdapter) -> None:
    for pair in PAIRS:
        book = await adapter.fetch_order_book(pair, depth=20)
        assert book.best_bid and book.best_ask and book.best_bid < book.best_ask
        assert book.spread_bps is not None and book.spread_bps < 100
        ticker = await adapter.fetch_ticker(pair)
        assert ticker.last is not None and ticker.last > 0


@pytest.mark.parametrize("tf", list(Timeframe))
async def test_closed_candles(adapter: ExchangeAdapter, tf: Timeframe) -> None:
    now = utcnow()
    candles = await adapter.fetch_closed_candles("BTC/USDT", tf, limit=100)
    assert len(candles) >= 50
    step = timedelta(seconds=tf.seconds)
    assert all(c.open_time + step <= now for c in candles)  # no forming bar
    assert all(b.open_time > a.open_time for a, b in pairwise(candles))
    assert candles[-1].open_time >= now - 3 * step  # fresh


async def test_health(adapter: ExchangeAdapter) -> None:
    status = await adapter.health_check()
    assert status.ok, status.detail
