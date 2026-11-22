"""End-to-end paper trading cycle on real Postgres + Redis with a fake exchange book."""

from __future__ import annotations

import json
import os
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
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
    from nexus_news.service import KEY as NEWS_KEY

    await redis.set(NEWS_KEY, json.dumps({"score": 0.1, "unknown_sources": []}))
    settings = Settings(enabled_exchanges=["binance"], trading_pairs=["BTC/USDT"])
    books = BookCache({"binance": adapter}, ttl=0)
    broker = PaperBroker(books.get, taker_fee=D("0.001"), maker_fee=D("0.001"),
                         extra_slippage=D("0.0005"))
    with transaction(factory) as s:   # validated in trend_up only (Phase 2 regime gate)
        for st in s.scalars(select(models.Strategy)):
            st.parameters = {"validation": {"passed": True, "allowed_regimes": ["trend_up"]}}
    eng = TradingEngine(settings, factory, redis, {"binance": adapter}, broker, books)
    # These tests cover order lifecycle, not market analysis: the analysis gate is bypassed
    # here. test_decision_gate_* exercises the real gate, which must refuse without history.

    async def _bypass_gate(*_: Any, **__: Any) -> tuple[bool, str]:
        return True, "test fixture: analysis gate bypassed"

    eng._decision_gate = _bypass_gate  # type: ignore[method-assign]
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
        # Risk 0.25% of 10,000 = 25 USDT over a 1,000 USDT stop plus fees and slippage
        # (1,178.5 per BTC) = 0.02121 BTC, below the 25% concentration cap (0.0416 BTC).
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
        assert D(20) < loss < D(28)          # ~ 0.02121 BTC x ~1,100 USDT stop distance
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
    eng, factory, _state, _ = engine_setup
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


async def test_news_block_stops_entries(engine_setup: tuple[Any, ...]) -> None:
    from nexus_news.service import KEY as NEWS_KEY

    eng, factory, _, redis = engine_setup
    eng.bootstrap()
    v = await eng.valuation()
    await redis.set(NEWS_KEY, json.dumps({"score": 0.95, "unknown_sources": []}))
    await eng.handle_signal(1, signal(), v, v.equity, v.equity, v.equity)
    await redis.set(NEWS_KEY, json.dumps({"score": None, "unknown_sources": ["sec"]}))
    # Each bar is recorded once (idempotent), so each test signal needs its own bar.
    from dataclasses import replace as _replace

    base = signal()
    await eng.handle_signal(1, _replace(base, bar_time=base.bar_time + timedelta(hours=1)),
                            v, v.equity, v.equity, v.equity)
    await redis.delete(NEWS_KEY)
    await eng.handle_signal(1, _replace(base, bar_time=base.bar_time + timedelta(hours=2)),
                            v, v.equity, v.equity, v.equity)
    with transaction(factory) as s:
        reasons = [x.rejection_reason or "" for x in s.scalars(select(models.TradingSignal)).all()]
        assert s.scalar(select(models.Order)) is None
    assert any("high-impact news" in r for r in reasons)
    assert any("news risk unknown (no data from sec)" in r for r in reasons)
    assert any("news risk unknown (monitor" in r for r in reasons)


async def test_decision_gate_refuses_without_candle_history(engine_setup: tuple[Any, ...]) -> None:
    """The real gate (not the fixture bypass) must refuse to trade on missing analysis data."""
    eng, _factory, _, _ = engine_setup
    eng.bootstrap()
    v = await eng.valuation()
    book = await eng.books.get("binance", "BTC/USDT")
    proceed, explanation = await TradingEngine._decision_gate(
        eng, signal(), v, v.equity, v.equity, book, None, None)
    assert proceed is False
    assert "insufficient candle history" in explanation


async def _enter(eng: Any, factory: Any) -> Any:
    eng.bootstrap()
    v = await eng.valuation()
    day0, week0, peak = eng.period_starts(v.equity, utcnow())
    await eng.handle_signal(1, signal(), v, day0, week0, peak)
    with transaction(factory) as s:
        pos = s.scalar(select(models.Position))
        assert pos is not None and pos.status == "open"
        return pos.id, pos.quantity, pos.avg_entry_price


async def test_partial_take_profit_once_then_trailing_stop(engine_setup: tuple[Any, ...]) -> None:
    from nexus_execution import protection

    eng, factory, state, _ = engine_setup
    pid, qty0, entry = await _enter(eng, factory)
    one_r = entry - D(59_000)
    # +1R reached (below the 62,000 target): sell half once, stop to breakeven.
    state.update(bid=float(entry + one_r + 50), ask=float(entry + one_r + 51))
    await eng.cycle()
    await eng.cycle()  # must not sell a second partial
    with transaction(factory) as s:
        pos = s.get(models.Position, pid)
        partials = [o for o in s.scalars(select(models.Order).where(models.Order.purpose == "exit"))
                    if o.details.get("exit_reason") == protection.PARTIAL_REASON]
        assert len(partials) == 1 and partials[0].status == "filled"
        assert pos is not None and pos.status == "open" and pos.quantity < qty0
        assert pos.stop_loss is not None and pos.stop_loss >= pos.avg_entry_price
        breakeven = pos.stop_loss

    # Trailing: with ATR 100 and a new high of 61,900 the stop rises to ~61,700.
    eng._atr = lambda symbol: D(100)  # type: ignore[method-assign]
    state.update(bid=61_900.0, ask=61_901.0)
    await eng.cycle()
    with transaction(factory) as s:
        pos = s.get(models.Position, pid)
        assert pos is not None and pos.status == "open"
        assert pos.stop_loss is not None and pos.stop_loss > breakeven
        assert D(61_690) <= pos.stop_loss <= D(61_700)
    # The trailed stop is never loosened, and a pullback through it exits at a profit.
    state.update(bid=61_650.0, ask=61_651.0)
    await eng.cycle()
    with transaction(factory) as s:
        pos = s.get(models.Position, pid)
        assert pos is not None and pos.status == "closed" and pos.exit_reason == "stop_loss"
        assert pos.realized_pnl > 0


def _closed_loss(factory: Any, portfolio_id: int, minutes_ago: int) -> None:
    from datetime import timedelta as _td
    with transaction(factory) as s:
        pair = s.scalar(select(models.TradingPair))
        assert pair is not None
        t = utcnow() - _td(minutes=minutes_ago)
        s.add(models.Position(
            portfolio_id=portfolio_id, mode="paper", exchange_id=pair.exchange_id,
            pair_id=pair.id, base_asset="BTC", status="closed", quantity=D(0),
            avg_entry_price=D(60_000), cost_basis=D(0), realized_pnl=D(-50),
            opened_at=t, closed_at=t, exit_reason="stop_loss"))


async def test_loss_streak_reduces_then_pauses_until_owner_reset(
        engine_setup: tuple[Any, ...]) -> None:
    from dataclasses import replace as _replace

    from nexus_execution.protection import STREAK_RESET_KEY

    eng, factory, _, redis = engine_setup
    eng._cooldown_block = lambda _symbol: None  # type: ignore[method-assign]  # streak only
    pid = eng.bootstrap()
    v = await eng.valuation()
    for m in (40, 30):
        _closed_loss(factory, pid, m)
    base = signal()
    await eng.handle_signal(1, base, v, v.equity, v.equity, v.equity)
    with transaction(factory) as s:
        order = s.scalar(select(models.Order).where(models.Order.purpose == "entry"))
        assert order is not None and order.details["risk_multiplier"] == "0.5"
        assert "2 consecutive losses" in order.details["streak_note"]
        s.execute(text("UPDATE positions SET status='closed', quantity=0, realized_pnl=-10, "
                       "closed_at=now() - interval '20 minutes' WHERE status='open'"))
    _closed_loss(factory, pid, 10)  # four losses in a row now
    await eng.handle_signal(1, _replace(base, bar_time=base.bar_time + timedelta(hours=1)),
                            v, v.equity, v.equity, v.equity)
    with transaction(factory) as s:
        last = s.scalars(select(models.TradingSignal).order_by(models.TradingSignal.id.desc())).first()
        assert last is not None and last.status == "rejected"
        assert "entries paused after 4 consecutive losses" in (last.rejection_reason or "")
    await redis.set(STREAK_RESET_KEY, utcnow().isoformat())
    await eng.handle_signal(1, _replace(base, bar_time=base.bar_time + timedelta(hours=2)),
                            v, v.equity, v.equity, v.equity)
    with transaction(factory) as s:
        last = s.scalars(select(models.TradingSignal).order_by(models.TradingSignal.id.desc())).first()
        assert last is not None and last.status == "executed"


async def test_kill_switch_restored_from_audit_log_after_redis_loss(
        engine_setup: tuple[Any, ...]) -> None:
    eng, factory, _, redis = engine_setup
    eng.bootstrap()
    with transaction(factory) as s:
        repo.record_audit(s, actor="user:1", action="kill_switch_engaged",
                          details={"reason": "manual stop"})
    assert not await KillSwitch(redis).is_engaged()          # Redis lost the key
    await eng.cycle()
    st = await KillSwitch(redis).status()
    assert st is not None and st["reason"] == "manual stop" and st["restored"] is True
    # A later clear in the audit log does not auto-clear Redis (only the owner clears).
    with transaction(factory) as s:
        repo.record_audit(s, actor="user:1", action="kill_switch_cleared",
                          details={"reason": "reviewed"})
    await eng.cycle()
    assert await KillSwitch(redis).is_engaged()


async def test_unreadable_kill_switch_record_blocks_entries(
        engine_setup: tuple[Any, ...], monkeypatch: pytest.MonkeyPatch) -> None:
    eng, factory, _, _ = engine_setup
    eng.bootstrap()

    def broken(_s: Any) -> Any:
        raise RuntimeError("db down")

    monkeypatch.setattr(repo, "kill_switch_db_state", broken)
    await eng._sync_kill_switch()
    assert await eng._kill_active() is True
    v = await eng.valuation()
    day0, week0, peak = eng.period_starts(v.equity, utcnow())
    await eng.handle_signal(1, signal(), v, day0, week0, peak)
    with transaction(factory) as s:
        assert s.scalar(select(models.Order)) is None


async def test_kill_switch_close_all_positions(engine_setup: tuple[Any, ...]) -> None:
    from nexus_execution.trader import close_all_key

    eng, factory, _, redis = engine_setup
    await _enter(eng, factory)
    await KillSwitch(redis).engage("emergency", actor="user:1")
    with transaction(factory) as s:
        repo.record_audit(s, actor="user:1", action="kill_switch_engaged",
                          details={"reason": "emergency"})
    await redis.set(close_all_key("paper"), "user:1")
    await eng.cycle()
    with transaction(factory) as s:
        pos = s.scalar(select(models.Position))
        assert pos is not None and pos.status == "closed"
        exits = s.scalars(select(models.Order).where(models.Order.purpose == "exit")).all()
        assert [o.details["exit_reason"] for o in exits] == ["kill_switch_close"]
    await eng.cycle()                                         # nothing left: flag cleared
    assert await redis.get(close_all_key("paper")) is None
    with transaction(factory) as s:
        actions = s.scalars(select(models.AuditLog.action)).all()
        assert "kill_switch_positions_closed" in actions



async def _decision(factory: Any) -> Any:
    with transaction(factory) as s:
        sig = s.scalars(select(models.TradingSignal).order_by(models.TradingSignal.id.desc())
                        ).first()
        assert sig is not None
        return sig.status, sig.rejection_reason or ""


async def test_regime_outside_validated_regimes_is_rejected(
        engine_setup: tuple[Any, ...]) -> None:
    eng, factory, _, _ = engine_setup
    eng.bootstrap()
    v = await eng.valuation()
    day0, week0, peak = eng.period_starts(v.equity, utcnow())
    sig = Signal("momentum", "BTC/USDT", "15m", datetime(2026, 6, 1, 12, tzinfo=UTC), 60_000,
                 59_000, 62_000, 0.6, "range", {"params": {}})
    await eng.handle_signal(1, sig, v, day0, week0, peak)
    status, reason = await _decision(factory)
    assert status == "rejected" and "outside the strategy's validated regimes" in reason
    with transaction(factory) as s:          # no regime record at all -> rejected too
        st = s.get(models.Strategy, 1)
        assert st is not None
        st.parameters = {"validation": {"passed": True}}
    sig2 = Signal("momentum", "BTC/USDT", "15m", datetime(2026, 6, 1, 13, tzinfo=UTC), 60_000,
                  59_000, 62_000, 0.6, "trend_up", {"params": {}})
    await eng.handle_signal(1, sig2, v, day0, week0, peak)
    status, reason = await _decision(factory)
    assert status == "rejected" and "no validated regime record" in reason


async def test_cooldown_after_stop_loss_blocks_reentry(engine_setup: tuple[Any, ...]) -> None:
    eng, factory, state, _ = engine_setup
    await _enter(eng, factory)
    state.update(bid=58_900.0, ask=58_901.0)
    await eng.cycle()                                    # stop-loss exit
    state.update(bid=59_999.0, ask=60_000.0)
    v = await eng.valuation()
    day0, week0, peak = eng.period_starts(v.equity, utcnow())
    later = Signal("momentum", "BTC/USDT", "15m", datetime(2026, 6, 1, 14, tzinfo=UTC), 60_000,
                   59_000, 62_000, 0.6, "trend_up", {"params": {}})
    await eng.handle_signal(1, later, v, day0, week0, peak)
    status, reason = await _decision(factory)
    assert status == "rejected" and reason.startswith("cooldown: losing exit on BTC/USDT")


async def test_correlated_exposure_blocks_new_entry(engine_setup: tuple[Any, ...]) -> None:
    eng, factory, _, _ = engine_setup
    pid = eng.bootstrap()
    with transaction(factory) as s:                       # an ETH position worth 30% equity
        ex = repo.exchange_id(s, "binance")
        eth = repo.upsert_trading_pair(
            s, exchange_id=ex, symbol="ETH/USDT", exchange_symbol="ETHUSDT", base="ETH",
            quote="USDT", price_tick=D("0.01"), amount_step=D("0.0001"),
            min_amount=D("0.0001"), min_notional=D(5), maker_fee=D("0.001"),
            taker_fee=D("0.001"), synced_at=utcnow())
        s.add(models.Position(portfolio_id=pid, mode="paper", exchange_id=ex,
                              pair_id=eth if isinstance(eth, int) else eth.id,
                              base_asset="ETH", status="open", quantity=D("0.06"),
                              avg_entry_price=D("60000"), cost_basis=D("3600"),
                              opened_at=utcnow()))
    v = await eng.valuation()
    day0, week0, peak = eng.period_starts(v.equity, utcnow())
    await eng.handle_signal(1, signal(), v, day0, week0, peak)
    status, reason = await _decision(factory)
    # The fake book marks ETH at ~60,000: 0.06 ETH = 3,600, equity 13,600. No candle history
    # -> correlation assumed 1: 3,600 + ~1,270 BTC > 35% x 13,600 = 4,760.
    assert status == "rejected" and "correlated exposure" in reason


async def test_timeout_after_send_is_reconciled_not_resubmitted(
        engine_setup: tuple[Any, ...]) -> None:
    eng, factory, _, _ = engine_setup
    broker = eng.orders.broker

    class Seq:
        def __init__(self) -> None:
            self.xs = [0.1, 0.9]

        def random(self) -> float:
            return self.xs.pop(0) if self.xs else 0.99

    broker.outage_rate, broker._rng = 0.5, Seq()
    eng.bootstrap()
    v = await eng.valuation()
    day0, week0, peak = eng.period_starts(v.equity, utcnow())
    await eng.handle_signal(1, signal(), v, day0, week0, peak)
    with transaction(factory) as s:
        orders = s.scalars(select(models.Order)).all()
        assert len(orders) == 1 and orders[0].status == "unknown"
    await eng.orders.reconcile_unknown()                 # look it up by client order id
    with transaction(factory) as s:
        orders = s.scalars(select(models.Order)).all()
        assert len(orders) == 1 and orders[0].status == "filled"   # one order, never resent
        pos = s.scalar(select(models.Position))
        assert pos is not None and pos.status == "open"


async def test_execution_quality_report(engine_setup: tuple[Any, ...]) -> None:
    from nexus_execution.reports import execution_quality

    eng, factory, state, _ = engine_setup
    await _enter(eng, factory)
    state.update(bid=58_900.0, ask=58_901.0)
    await eng.cycle()                                    # stop-loss exit
    with transaction(factory) as s:
        q = execution_quality(s, eng.portfolio_id)
    assert q["entry"]["orders"] == 1 and q["entry"]["fill_rate"] == "1.0000"
    assert q["exit"]["orders"] == 1 and q["exit"]["slippage_bps_avg"] is not None
    assert q["entry"]["missed"] == 0 and q["entry"]["unknown"] == 0


async def test_report_has_expectancy_averages_and_guarded_sharpe(
        engine_setup: tuple[Any, ...]) -> None:
    from nexus_execution.reports import portfolio_report

    eng, factory, state, _ = engine_setup
    await _enter(eng, factory)
    state.update(bid=58_900.0, ask=58_901.0)
    await eng.cycle()
    with transaction(factory) as s:
        r = portfolio_report(s, eng.portfolio_id)
    assert r["avg_loss"] is not None and D(r["avg_loss"]) < 0 and r["avg_win"] is None
    assert D(r["expectancy"]) == D(r["avg_loss"])
    assert r["sharpe"] is None and "30+" in r["sharpe_note"]   # too little history
