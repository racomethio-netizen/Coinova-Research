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
    mon = HealthMonitor([adapter], factory, redis, failures_before_down=2,
                        down_alert_after=0, up_alert_after=0)
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


# ---------------------------------------------------------------- rate-limit fix
async def test_flapping_venue_sends_one_alert_and_one_recovery_summary(
        factory: sessionmaker[Session], redis: Any) -> None:
    """The Telegram flood (unreachable/reconnected every few minutes) becomes one alert and
    one recovery message; the health key still follows every change at once."""
    from nexus_exchange.models import HealthStatus

    now = {"t": 0.0}
    seq: list[bool] = []

    async def passive(code: str) -> HealthStatus:
        ok = seq.pop(0)
        return HealthStatus(code, ok, None, T0, None if ok else "paused by Binance")

    client = FakeCcxtClient()
    adapter = CcxtSpotAdapter("binance", client, CAPABILITIES[ExchangeCode.BINANCE])
    mon = HealthMonitor([adapter], factory, redis, passive=passive, failures_before_down=1,
                        clock=lambda: now["t"])
    # 1 h of 60 s checks: down 3 min, up 2 min, repeated (like the screenshot), then stable.
    pattern = ([False] * 3 + [True] * 2) * 10 + [True] * 10
    seq.extend(pattern)
    health_changes = 0
    last = None
    for _ in pattern:
        await mon.check_once()
        ok = json.loads(await redis.get(events.health_key("binance")))["ok"]
        health_changes += ok != last
        last = ok
        now["t"] += 60
    notes = [events.decode(f) for _, f in await redis.xrange(events.NOTIFY_STREAM)]
    kinds = [n["type"] for n in notes]
    assert kinds == ["exchange_disconnected", "exchange_connected"]
    assert "10 interruption(s)" in notes[1]["summary"]
    assert health_changes > 15                                  # trading saw every change


async def test_live_stream_means_no_rest_for_just_closed_bars_and_passive_health(
        factory: sessionmaker[Session], redis: Any) -> None:
    from nexus_common import binance_guard as g
    from nexus_common.timeutil import utcnow
    from nexus_config import Settings
    from nexus_market import runner
    from nexus_market.binance_ws import STATE_KEY

    calls: list[str] = []
    client = FakeCcxtClient()

    def serve(symbol: str, tf: str, since: int | None, limit: int) -> list[list[float]]:
        calls.append(tf)
        return []

    client.responses["fetch_ohlcv"] = serve
    adapter = CcxtSpotAdapter("binance", client, CAPABILITIES[ExchangeCode.BINANCE])
    adapter.gate = g.CcxtGate(redis, "spot")
    await adapter.load_markets()
    svc = runner.MarketDataService.__new__(runner.MarketDataService)
    svc.settings = Settings(enabled_exchanges=["binance"], trading_pairs=["BTC/USDT"],
                            timeframes=["5m"], minute_candles=False)
    svc.factory, svc.redis, svc.adapters = factory, redis, {"binance": adapter}
    pid = _pair(factory)
    svc.pairs = {("binance", "BTC/USDT"): pid}
    now = utcnow()
    last_open = now.replace(second=0, microsecond=0) - timedelta(minutes=now.minute % 5 + 10)
    _bars(factory, pid, "5m", 300, 1)                           # old history start
    with transaction(factory) as s:                             # newest stored bar
        repo.upsert_candles(s, pid, "5m", [repo.CandleRow(last_open, D(1), D(1), D(1), D(1),
                                                          D(1))])
    # Stream live and fresh: the bar that closed < 90 s ago is left to the stream.
    await redis.set(STATE_KEY, json.dumps({"connected": True,
                                           "last_message_at": utcnow().isoformat()}))
    assert await svc.ws_fresh() is True
    horizon = await svc._rest_horizon()
    assert (utcnow() - horizon).total_seconds() >= 89
    st = await svc.passive_health("binance")
    assert st is not None and st.ok and st.detail is None
    # REST paused by Binance while the stream is fine: healthy, with an explanation.
    await redis.set(g._key("spot"), (utcnow() + timedelta(minutes=5)).isoformat())
    st = await svc.passive_health("binance")
    assert st is not None and st.ok and "live stream OK" in (st.detail or "")
    # Stream down and REST paused: unhealthy with the reason; no ping is sent.
    await redis.delete(STATE_KEY)
    st = await svc.passive_health("binance")
    assert st is not None and not st.ok and "paused this server's IP" in (st.detail or "")
    calls.clear()
    await svc.poll_candles()
    assert calls == []                                          # paused: nothing sent


async def test_order_books_come_from_the_stream_not_rest(redis: Any) -> None:
    from nexus_common.timeutil import utcnow
    from nexus_execution.trader import BookCache
    from nexus_market import runner

    client = FakeCcxtClient()
    adapter = CcxtSpotAdapter("binance", client, CAPABILITIES[ExchangeCode.BINANCE])
    await adapter.load_markets()
    svc = runner.MarketDataService.__new__(runner.MarketDataService)
    svc.redis = redis
    await svc.publish_book("binance", "BTC/USDT", {
        "bids": [["100.5", "2"], ["100.4", "3"]], "asks": [["100.6", "1"]]})
    cache = BookCache({"binance": adapter}, ttl=0, redis=redis)
    book = await cache.get("binance", "BTC/USDT")
    assert book is not None and book.best_bid == D("100.5") and len(book.bids) == 2
    assert not any(n == "fetch_order_book" for n, _ in client.calls)
    # A stale stream book is not used.
    raw = json.loads(await redis.get(events.book_key("binance", "BTC/USDT")))
    raw["ts"] = (utcnow() - timedelta(seconds=30)).isoformat()
    await redis.set(events.book_key("binance", "BTC/USDT"), json.dumps(raw))
    assert await runner.read_ws_book(redis, "binance", "BTC/USDT", max_age=5) is None


async def test_startup_during_a_binance_pause_still_streams_known_pairs(
        factory: sessionmaker[Session], redis: Any) -> None:
    from nexus_common import binance_guard as g
    from nexus_common.timeutil import utcnow
    from nexus_config import Settings
    from nexus_market import runner

    pid = _pair(factory)                                          # known from an earlier start
    await redis.set(g._key("spot"), (utcnow() + timedelta(minutes=5)).isoformat())
    client = FakeCcxtClient()
    adapter = CcxtSpotAdapter("binance", client, CAPABILITIES[ExchangeCode.BINANCE])
    adapter.gate = g.CcxtGate(redis, "spot")
    svc = runner.MarketDataService.__new__(runner.MarketDataService)
    svc.settings = Settings(enabled_exchanges=["binance"], trading_pairs=["BTC/USDT"])
    svc.factory, svc.redis, svc.adapters = factory, redis, {"binance": adapter}
    svc.pairs = {}
    await svc.bootstrap()
    assert svc.pairs == {("binance", "BTC/USDT"): pid}
    assert client.count("load_markets") == 0                     # nothing sent during the pause
    assert svc.binance_stream().symbols == ["BTC/USDT"]



def test_derivation_is_chunked_and_crosses_long_gaps(factory: sessionmaker[Session],
                                                    monkeypatch: pytest.MonkeyPatch) -> None:
    from nexus_market import derived

    monkeypatch.setattr(derived, "MAX_SOURCE_ROWS", 10)            # tiny windows
    pid = _pair(factory)
    # 1h bars: day 1 complete, then a 3-day gap, then day 5 complete.
    _bars(factory, pid, "1h", 3600, 24)
    rows = [repo.CandleRow(T0 + timedelta(days=4, hours=h), D(1), D(2), D(1), D(1), D(1))
            for h in range(24)]
    with transaction(factory) as s:
        repo.upsert_candles(s, pid, "1h", rows)
    now = T0 + timedelta(days=6)
    written = 0
    for _ in range(30):                                            # repeated job runs
        with transaction(factory) as s:
            written += derived.derive(s, pid, "6h", now=now)
    with transaction(factory) as s:
        bars = repo.load_candles(s, pid, "6h")
    assert [b.open_time for b in bars] == (
        [T0 + timedelta(hours=6 * i) for i in range(4)]
        + [T0 + timedelta(days=4, hours=6 * i) for i in range(4)])
