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


# ---------------------------------------------------------------- Phase B: derived TFs
def _pair(factory: sessionmaker[Session]) -> int:
    with transaction(factory) as s:
        return repo.upsert_trading_pair(
            s, exchange_id=repo.exchange_id(s, "binance"), symbol="BTC/USDT",
            exchange_symbol="BTCUSDT", base="BTC", quote="USDT", price_tick=D("0.01"),
            amount_step=D("0.00001"), min_amount=None, min_notional=None, maker_fee=None,
            taker_fee=None, synced_at=T0)


def _bars(factory: sessionmaker[Session], pid: int, tf: str, step_s: int, n: int,
          missing: set[int] = frozenset()) -> None:  # type: ignore[assignment]
    rows = [repo.CandleRow(T0 + timedelta(seconds=i * step_s), D(100 + i), D(101 + i),
                           D(99 + i), D("100.5") + i, D(1)) for i in range(n) if i not in missing]
    with transaction(factory) as s:
        repo.upsert_candles(s, pid, tf, rows)


def test_derived_timeframes_use_complete_closed_groups_only(
        factory: sessionmaker[Session]) -> None:
    from nexus_market.derived import derive

    pid = _pair(factory)
    _bars(factory, pid, "1h", 3600, 24, missing={7})            # 1 day of 1h, 07:00 missing
    now = T0 + timedelta(hours=24)
    with transaction(factory) as s:
        assert derive(s, pid, "6h", now=now) == 3                # 06-12 bucket incomplete
        c6 = repo.load_candles(s, pid, "6h")
    assert [c.open_time.hour for c in c6] == [0, 12, 18]
    first = c6[0]
    assert (first.open, first.high, first.low, first.close, first.volume) == (
        D(100), D(106), D(99), D("105.5"), D(6))
    with transaction(factory) as s:
        assert derive(s, pid, "2h", now=now) == 11               # 06-08 incomplete
        assert derive(s, pid, "12h", now=now) == 1               # 00-12 incomplete
        assert derive(s, pid, "6h", now=now) == 1                # incremental: last bucket only
        assert len(repo.load_candles(s, pid, "6h")) == 3         # idempotent, no duplicates
    # A still-forming bucket is never written.
    with transaction(factory) as s:
        assert derive(s, pid, "12h", now=T0 + timedelta(hours=23, minutes=59)) == 0


def test_three_minute_bars_from_one_minute_are_epoch_aligned(
        factory: sessionmaker[Session]) -> None:
    from nexus_market.derived import derive

    pid = _pair(factory)
    _bars(factory, pid, "1m", 60, 10)                            # 00:00..00:09
    with transaction(factory) as s:
        assert derive(s, pid, "3m", now=T0 + timedelta(minutes=10)) == 3
        bars = repo.load_candles(s, pid, "3m")
    assert [int(c.open_time.timestamp()) % 180 for c in bars] == [0, 0, 0]
    assert bars[-1].open_time == T0 + timedelta(minutes=6)


def test_data_quality_report_counts_gaps_and_staleness(factory: sessionmaker[Session]) -> None:
    from nexus_market.derived import quality_report

    pid = _pair(factory)
    _bars(factory, pid, "1h", 3600, 24, missing={5, 6, 7})
    with transaction(factory) as s:
        fresh = quality_report(s, [("binance", "BTC/USDT"), ("binance", "NOPE/USDT")],
                               ["1h", "15m"], now=T0 + timedelta(hours=24, minutes=1))
        late = quality_report(s, [("binance", "BTC/USDT")], ["1h"],
                              now=T0 + timedelta(hours=30))
    h1, m15 = fresh
    assert (h1.present, h1.window_bars, h1.missing, h1.largest_gap_bars) == (21, 24, 3, 3)
    assert h1.stale is False and h1.age_seconds == 60 and h1.coverage == round(21 / 24, 4)
    assert m15.newest is None and m15.stale is True               # no data = stale
    assert late[0].stale is True
    assert len(fresh) == 2                                        # unknown pair skipped


def test_minute_retention_prunes_only_old_minute_bars(factory: sessionmaker[Session]) -> None:
    from nexus_market.derived import prune_minutes

    pid = _pair(factory)
    _bars(factory, pid, "1m", 60, 5)
    _bars(factory, pid, "1h", 3600, 5)
    with transaction(factory) as s:
        assert prune_minutes(s, 30, now=T0 + timedelta(days=31)) == 5
        assert repo.load_candles(s, pid, "1m") == []
        assert len(repo.load_candles(s, pid, "1h")) == 5


async def test_minute_polling_is_gradual_and_limited_to_traded_pairs(
        factory: sessionmaker[Session], redis: Any) -> None:
    from nexus_config import Settings
    from nexus_market import runner

    client = FakeCcxtClient()
    calls: list[str] = []

    def serve(symbol: str, tf: str, since: int | None, limit: int) -> list[list[float]]:
        calls.append(tf)
        start = -(-(since or 0) // 60_000) * 60_000       # Binance: whole minutes >= since
        return [[start + i * 60_000, 1, 1, 1, 1, 1] for i in range(limit)]

    client.responses["fetch_ohlcv"] = serve
    adapter = CcxtSpotAdapter("binance", client, CAPABILITIES[ExchangeCode.BINANCE])
    await adapter.load_markets()
    svc = runner.MarketDataService.__new__(runner.MarketDataService)
    svc.settings = Settings(enabled_exchanges=["binance"], trading_pairs=["BTC/USDT"])
    svc.factory, svc.redis, svc.adapters = factory, redis, {"binance": adapter}
    svc.pairs = {("binance", "BTC/USDT"): _pair(factory)}
    stored = await svc.poll_minutes()
    assert calls == ["1m"] * runner.MINUTE_PAGES_PER_POLL       # a few pages, not 30 days
    assert stored == runner.MINUTE_PAGES_PER_POLL * CAPABILITIES[ExchangeCode.BINANCE].ohlcv_page_limit
