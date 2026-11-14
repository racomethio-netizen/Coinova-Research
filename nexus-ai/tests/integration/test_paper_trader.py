"""End-to-end paper trading cycle on real Postgres + Redis with a fake exchange book."""

from __future__ import annotations

import json
import os
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from decimal import Decimal as D
from typing import Any

import pytest
import redis.asyncio as aioredis
from nexus_common import events
from nexus_common.timeutil import utcnow
from nexus_config import ExchangeCode, Settings
from nexus_db import make_session_factory, models, repo, transaction
from nexus_exchange import CAPABILITIES
from nexus_exchange.ccxt_adapter import CcxtSpotAdapter
from nexus_execution.brokers import PaperBroker
from nexus_execution.trader import BookCache, TradingEngine
from nexus_risk.kill_switch import KillSwitch
from nexus_strategies import Signal
from sqlalchemy import Engine, select, text

from tests.exchange.fakes import FakeCcxtClient
from tests.integration.conftest import truncate

pytestmark = [pytest.mark.db, pytest.mark.redis]


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


@pytest.fixture
async def engine_setup(migrated_engine: Engine, redis: Any) -> AsyncIterator[tuple[Any, ...]]:
    factory = make_session_factory(migrated_engine)
    with transaction(factory) as s:
        s.execute(text("INSERT INTO users (email, password_hash, role) VALUES "
                       "('o@example.test','x','owner')"))
        repo.upsert_trading_pair(s, exchange_id=repo.exchange_id(s, "binance"), symbol="BTC/USDT",
                                 exchange_symbol="BTCUSDT", base="BTC", quote="USDT",
                                 price_tick=D("0.01"), amount_step=D("0.00001"),
                                 min_amount=D("0.00001"), min_notional=D(5), maker_fee=D("0.001"),
                                 taker_fee=D("0.001"), synced_at=utcnow())
    client = FakeCcxtClient()
    state = {"bid": 59_999.0, "ask": 60_000.0}
    client.responses["fetch_order_book"] = lambda *a: {
        "bids": [[state["bid"], 2.0], [state["bid"] - 10, 5.0]],
        "asks": [[state["ask"], 2.0], [state["ask"] + 10, 5.0]],
        "timestamp": int(utcnow().timestamp() * 1000)}
    adapter = CcxtSpotAdapter("binance", client, CAPABILITIES[ExchangeCode.BINANCE])
    await adapter.load_markets()
    await redis.set(events.health_key("binance"), json.dumps({"ok": True}))
    settings = Settings(enabled_exchanges=["binance"], trading_pairs=["BTC/USDT"])
    books = BookCache({"binance": adapter}, ttl=0)
    broker = PaperBroker(books.get, taker_fee=D("0.001"), maker_fee=D("0.001"),
                         extra_slippage=D("0.0005"))
    eng = TradingEngine(settings, factory, redis, {"binance": adapter}, broker, books)
    yield eng, factory, state, redis
    truncate(migrated_engine, "trades, orders, positions, balances, risk_events, "
                              "performance_snapshots, trading_signals, portfolios, trading_pairs, "
                              "users, audit_logs")


def signal(entry: float = 60_000, stop: float = 59_000, target: float = 62_000) -> Signal:
    return Signal("momentum", "BTC/USDT", "15m", datetime(2026, 6, 1, 12, tzinfo=UTC), entry,
                  stop, target, 0.6, "trend_up", {"params": {}})


async def test_paper_entry_stop_exit_cycle(engine_setup: tuple[Any, ...]) -> None:
    eng, factory, state, _ = engine_setup
    pid = eng.bootstrap()
    v = await eng.valuation()
    assert v.equity == D(10_000) and v.complete
    day0, week0, peak = eng.period_starts(v.equity, utcnow())
    with transaction(factory) as s:
        strat_id = s.scalar(select(models.Strategy.id).where(models.Strategy.code == "momentum"))
    await eng.handle_signal(strat_id, signal(), v, day0, week0, peak)

    with transaction(factory) as s:
        sig = s.scalar(select(models.TradingSignal))
        order = s.scalar(select(models.Order))
        pos = s.scalar(select(models.Position))
        assert sig is not None and sig.status == "executed" and sig.mode == "paper"
        assert order is not None and order.status == "filled" and order.mode == "paper"
        assert order.purpose == "entry" and order.time_in_force == "IOC"
        assert pos is not None and pos.status == "open"
        # Risk 0.25% of 10,000 = 25 USDT; per-unit loss = 1000 stop distance + 0.15% costs
        # on both legs (60000*0.0015 + 59000*0.0015) = 1178.5 -> 0.02121 BTC.
        assert pos.quantity == D("0.02121")
        assert pos.stop_loss == D(59000) and pos.take_profit == D(62000)
        trade_ids = s.scalars(select(models.Trade.exchange_trade_id)).all()
        assert trade_ids and all(t.startswith("paper-") for t in trade_ids)

    # Price falls through the stop: next cycle exits.
    state.update(bid=58_900.0, ask=58_901.0)
    await eng.cycle()
    with transaction(factory) as s:
        pos = s.scalar(select(models.Position))
        assert pos is not None and pos.status == "closed" and pos.exit_reason == "stop_loss"
        loss = -pos.realized_pnl
        assert D(20) < loss < D(35)          # ~ the 25 USDT risk budget, plus the gap
        snap = s.scalar(select(models.PerformanceSnapshot))
        assert snap is not None and snap.mode == "paper" and snap.portfolio_id == pid
        exits = s.scalars(select(models.Order).where(models.Order.purpose == "exit")).all()
        assert len(exits) == 1 and exits[0].details["exit_reason"] == "stop_loss"
    from nexus_execution.reports import portfolio_report
    with transaction(factory) as s:
        rep = portfolio_report(s, pid)
    assert rep["mode"] == "paper" and rep["title"].startswith("PAPER TRADING")
    assert rep["closed_trades"] == 1 and rep["win_rate"] == "0"
    assert D(rep["realized_pnl"]) < 0 and rep["orders_by_status"] == {"filled": 2}
    assert rep["avg_slippage_bps"] is not None
    # Regression: no session may be left idle-in-transaction after a cycle.
    with transaction(factory) as s:
        leaked = s.execute(text(
            "SELECT count(*) FROM pg_stat_activity WHERE datname = current_database() "
            "AND state = 'idle in transaction' AND pid <> pg_backend_pid()")).scalar_one()
    assert leaked == 0


async def test_kill_switch_and_duplicate_signals(engine_setup: tuple[Any, ...]) -> None:
    eng, factory, _, redis = engine_setup
    eng.bootstrap()
    v = await eng.valuation()
    day0, week0, peak = eng.period_starts(v.equity, utcnow())
    await KillSwitch(redis).engage("test", actor="user:1")
    await eng.handle_signal(1, signal(), v, day0, week0, peak)
    await eng.handle_signal(1, signal(), v, day0, week0, peak)  # same bar: idempotent
    with transaction(factory) as s:
        sigs = s.scalars(select(models.TradingSignal)).all()
        assert len(sigs) == 1 and sigs[0].status == "rejected"
        assert "kill switch" in (sigs[0].rejection_reason or "")
        assert s.scalar(select(models.Order)) is None


async def test_stale_or_missing_book_blocks_trading(engine_setup: tuple[Any, ...]) -> None:
    eng, factory, state, _ = engine_setup
    eng.bootstrap()
    v = await eng.valuation()
    eng.books.adapters["binance"]._client.errors["fetch_order_book"] = RuntimeError("down")
    await eng.handle_signal(1, signal(), v, v.equity, v.equity, v.equity)
    with transaction(factory) as s:
        sig = s.scalar(select(models.TradingSignal))
        assert sig is not None and sig.status == "rejected"
        assert "stale or missing" in (sig.rejection_reason or "")


async def test_no_enabled_strategies_means_no_trades(engine_setup: tuple[Any, ...]) -> None:
    eng, factory, _, _ = engine_setup
    eng.bootstrap()
    await eng.cycle()
    with transaction(factory) as s:
        assert s.scalar(select(models.TradingSignal)) is None
        assert s.scalar(select(models.Order)) is None


async def test_daily_report_queued_once_per_day(engine_setup: tuple[Any, ...]) -> None:
    from nexus_notify.service import daily_reports
    eng, factory, _, redis = engine_setup
    eng.bootstrap()
    now = datetime(2026, 6, 2, 0, 6, tzinfo=UTC)
    assert await daily_reports(factory, redis, now) == 1
    assert await daily_reports(factory, redis, now) == 0
    entries = await redis.xrange(events.NOTIFY_STREAM)
    payload = events.decode(entries[-1][1])
    assert payload["type"] == "daily_report" and payload["mode"] == "paper"
    assert payload["date"] == "2026-06-01" and payload["report"]["title"].startswith("PAPER")


async def test_signal_decisions_are_notified(engine_setup: tuple[Any, ...]) -> None:
    eng, _, _, redis = engine_setup
    eng.bootstrap()
    v = await eng.valuation()
    await eng.handle_signal(1, signal(), v, v.equity, v.equity, v.equity)
    kinds = [events.decode(f)["type"] for _, f in await redis.xrange(events.NOTIFY_STREAM)]
    assert "signal" in kinds and "order_submitted" in kinds
