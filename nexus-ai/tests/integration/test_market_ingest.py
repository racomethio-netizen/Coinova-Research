"""Backfill and health monitoring against real Postgres/Redis with a fake exchange."""

from __future__ import annotations

import json
import os
from collections.abc import AsyncIterator, Iterator
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal as D
from typing import Any

import pytest
import redis.asyncio as aioredis
from ccxt.base import errors as cx
from nexus_common import events
from nexus_config import ExchangeCode, Timeframe
from nexus_db import make_session_factory, repo, transaction
from nexus_exchange import CAPABILITIES
from nexus_exchange.ccxt_adapter import CcxtSpotAdapter
from nexus_market.health import HealthMonitor
from nexus_market.ingest import backfill, sync_pairs
from sqlalchemy import Engine, text
from sqlalchemy.orm import Session, sessionmaker

from tests.exchange.fakes import FakeCcxtClient

pytestmark = [pytest.mark.db, pytest.mark.redis]
T0 = datetime(2026, 3, 1, tzinfo=UTC)


@pytest.fixture
def factory(migrated_engine: Engine) -> Iterator[sessionmaker[Session]]:
    f = make_session_factory(migrated_engine)
    yield f
    with migrated_engine.begin() as c:
        c.execute(text("TRUNCATE market_candles, orderbook_snapshots, system_events, "
                       "trading_pairs RESTART IDENTITY CASCADE"))


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


def fake_ohlcv(missing: set[int]) -> Any:
    """Serve 5m bars from T0 with some missing, honoring `since` and `limit`."""
    def serve(symbol: str, tf: str, since: int | None, limit: int) -> list[list[float]]:
        start = since or int(T0.timestamp() * 1000)
        rows = []
        i = (start - int(T0.timestamp() * 1000)) // 300_000
        while len(rows) < limit and i < 100:
            if i not in missing:
                rows.append([int(T0.timestamp() * 1000) + i * 300_000, 100, 101, 99, 100.5, 2])
            i += 1
        return rows
    return serve


async def test_sync_and_backfill_paginates_and_reports_gaps(
        factory: sessionmaker[Session]) -> None:
    client = FakeCcxtClient()
    client.responses["fetch_ohlcv"] = fake_ohlcv({10, 11, 50})
    adapter = CcxtSpotAdapter("binance", client,
                               replace(CAPABILITIES[ExchangeCode.BINANCE], ohlcv_page_limit=30))
    synced = await sync_pairs(adapter, factory, ["BTC/USDT", "DOGE/USDT"])
    assert list(synced) == ["BTC/USDT"]

    now = T0 + timedelta(minutes=5 * 100)
    res = await backfill(adapter, factory, pair_id=synced["BTC/USDT"], symbol="BTC/USDT",
                         timeframe=Timeframe.M5, start=T0, now=now)
    assert res.stored == 97
    assert res.pages == 4  # 30+30+30+7; stops at "now" without an extra request
    assert [g[2] for g in res.gaps] == [2, 1]
    # Second run is incremental: nothing new.
    again = await backfill(adapter, factory, pair_id=synced["BTC/USDT"], symbol="BTC/USDT",
                           timeframe=Timeframe.M5, start=T0, now=now)
    assert again.stored == 0
    with transaction(factory) as s:
        kinds = [(e.event_type, e.severity) for e in repo.recent_system_events(s)]
        candles = repo.load_candles(s, synced["BTC/USDT"], "5m")
    assert ("candle_gaps", "warning") in kinds
    assert ("pair_unavailable", "warning") in kinds
    assert len(candles) == 97 and candles[0].close == D("100.5")


async def test_health_transitions(factory: sessionmaker[Session], redis: Any) -> None:
    client = FakeCcxtClient()
    client.responses["fetch_time"] = 1
    adapter = CcxtSpotAdapter("kraken", client, CAPABILITIES[ExchangeCode.KRAKEN])
    mon = HealthMonitor([adapter], factory, redis, failures_before_down=2)
    await mon.check_once()
    assert mon.state["kraken"] is True
    client.errors["fetch_time"] = cx.ExchangeNotAvailable("down")
    await mon.check_once()
    assert mon.state["kraken"] is True  # a single failure is tolerated
    await mon.check_once()
    assert mon.state["kraken"] is False
    del client.errors["fetch_time"]
    await mon.check_once()
    assert mon.state["kraken"] is True
    cached = json.loads(await redis.get(events.health_key("kraken")))
    assert cached["ok"] is True
    notes = [events.decode(f)["type"] for _, f in await redis.xrange(events.NOTIFY_STREAM)]
    assert notes == ["exchange_disconnected", "exchange_connected"]
    with transaction(factory) as s:
        kinds = [e.event_type for e in repo.recent_system_events(s)]
    assert kinds[:2] == ["exchange_up", "exchange_down"]
