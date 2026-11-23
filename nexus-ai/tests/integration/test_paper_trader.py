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
from nexus_config import ExchangeCode, RiskSettings, Settings
from nexus_db import make_session_factory, models, repo, transaction
from nexus_exchange import CAPABILITIES
from nexus_exchange.ccxt_adapter import CcxtSpotAdapter
from nexus_execution.brokers import PaperBroker
from nexus_execution.trader import BookCache, TradingEngine
from nexus_risk.kill_switch import KillSwitch
from nexus_strategies import Signal
from sqlalchemy import Engine, func, select, text

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
    # Pinned to the 2026-11-21 profile these scenarios were written for (0.25% / 1% daily);
    # the 2026-11-23 defaults are covered by test_settings.
    settings = Settings(enabled_exchanges=["binance"], trading_pairs=["BTC/USDT"],
                        risk=RiskSettings(risk_per_trade=D("0.0025"),
                                          daily_loss_limit=D("0.01"), max_drawdown=D("0.10")))
    books = BookCache({"binance": adapter}, ttl=0)
    broker = PaperBroker(books.get, taker_fee=D("0.001"), maker_fee=D("0.001"),
                         extra_slippage=D("0.0005"))
    with transaction(factory) as s:   # validated in trend_up only (Phase 2 regime gate)
        for st in s.scalars(select(models.Strategy)):
            st.parameters = {"validation": {
                "passed": True, "allowed_regimes": ["trend_up"],
                "regime_edges": {"trend_up": {"n": 60, "expectancy": 0.004, "std": 0.01,
                                              "profit_factor": 1.5, "win_rate": 0.45}}}}
    eng = TradingEngine(settings, factory, redis, {"binance": adapter}, broker, books)
    # These tests cover order lifecycle, not market analysis: the analysis gate is bypassed
    # here. test_decision_gate_* exercises the real gate, which must refuse without history.

    async def _bypass_gate(*_: Any, **__: Any) -> tuple[bool, str]:
        return True, "test fixture: analysis gate bypassed"

    eng._decision_gate = _bypass_gate  # type: ignore[method-assign]
    # Candle freshness is covered by test_stale_candles_put_risk_state_in_no_new_trades.
    eng._market_data_stale = lambda: False  # type: ignore[method-assign]
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


# ------------------------------------------------------------------ Phase 8: failure recovery
async def test_daily_loss_limit_blocks_new_entries(engine_setup: tuple[Any, ...]) -> None:
    eng, factory, _, _ = engine_setup
    eng.bootstrap()
    v = await eng.valuation()
    # The day started 1.2% higher than equity now: the 1% daily limit is breached.
    day0 = v.equity * D("1.012")
    await eng.handle_signal(1, signal(), v, day0, day0, day0)
    status, reason = await _decision(factory)
    assert status == "rejected" and "daily loss" in reason
    with transaction(factory) as s:
        assert s.scalar(select(models.Order)) is None


async def test_restart_with_open_position_keeps_protecting_it(
        engine_setup: tuple[Any, ...]) -> None:
    from nexus_execution.trader import TradingEngine

    eng, factory, state, redis = engine_setup
    await _enter(eng, factory)
    # Simulated restart: a brand-new engine on the same database and exchange state.
    fresh = TradingEngine(eng.settings, factory, redis, eng.adapters, eng.orders.broker,
                          eng.books)
    assert fresh.bootstrap() == eng.portfolio_id
    state.update(bid=58_900.0, ask=58_901.0)          # stop is 59,000
    await fresh.cycle()
    with transaction(factory) as s:
        pos = s.scalar(select(models.Position))
        assert pos is not None and pos.status == "closed" and pos.exit_reason == "stop_loss"


async def test_crash_through_the_stop_exits_at_market_and_is_flagged(
        engine_setup: tuple[Any, ...]) -> None:
    from nexus_agents import review_trade

    eng, factory, state, _ = engine_setup
    await _enter(eng, factory)
    state.update(bid=50_000.0, ask=50_010.0)          # gap 15% below the 59,000 stop
    await eng.cycle()
    with transaction(factory) as s:
        pos = s.scalar(select(models.Position))
        assert pos is not None and pos.status == "closed"
        orders = s.scalars(select(models.Order).order_by(models.Order.id)).all()
        entry, exits = orders[0], [o for o in orders if o.purpose == "exit"]
        planned = entry.filled_amount * (entry.average_fill_price - D(59_000))
        assert -pos.realized_pnl > planned * 2          # gap loss exceeds the plan
        review = review_trade(
            {"id": pos.id, "realized_pnl": str(pos.realized_pnl), "exit_reason": pos.exit_reason},
            {"average_fill_price": str(entry.average_fill_price),
             "expected_price": str(entry.expected_price),
             "filled_amount": str(entry.filled_amount), "details": entry.details},
            [{"average_fill_price": str(x.average_fill_price),
              "expected_price": str(x.expected_price)} for x in exits],
            slippage_bps_assumed=D(5))
    assert "risk failure" in review["labels"]


async def test_high_volatility_regime_is_never_traded(engine_setup: tuple[Any, ...]) -> None:
    eng, factory, _, _ = engine_setup
    eng.bootstrap()
    v = await eng.valuation()
    day0, week0, peak = eng.period_starts(v.equity, utcnow())
    wild = Signal("momentum", "BTC/USDT", "15m", datetime(2026, 6, 1, 12, tzinfo=UTC), 60_000,
                  59_000, 62_000, 0.6, "high_vol", {"params": {}})
    await eng.handle_signal(1, wild, v, day0, week0, peak)
    status, reason = await _decision(factory)
    assert status == "rejected" and "high_vol" in reason


async def test_database_outage_mid_run_is_survived_without_orders(
        engine_setup: tuple[Any, ...], monkeypatch: pytest.MonkeyPatch) -> None:
    import asyncio

    from sqlalchemy.exc import OperationalError

    eng, factory, _, _ = engine_setup
    eng.bootstrap()
    calls = {"n": 0}
    real_cycle = eng.cycle

    async def flaky_cycle() -> None:
        calls["n"] += 1
        if calls["n"] <= 2:
            raise OperationalError("SELECT 1", {}, Exception("connection refused"))
        await real_cycle()
        eng.stop.set()

    monkeypatch.setattr(eng, "cycle", flaky_cycle)
    await asyncio.wait_for(eng.run(0.01), timeout=10)
    assert calls["n"] == 3                              # kept running, then recovered
    with transaction(factory) as s:
        assert s.scalar(select(models.Order)) is None


async def test_redis_outage_fails_closed(engine_setup: tuple[Any, ...]) -> None:
    eng, factory, _, _ = engine_setup
    eng.bootstrap()
    v = await eng.valuation()
    day0, week0, peak = eng.period_starts(v.equity, utcnow())

    class Down:
        def __getattr__(self, name: str) -> Any:
            async def fail(*a: Any, **k: Any) -> Any:
                raise ConnectionError("redis down")
            return fail

    eng.redis = Down()  # type: ignore[assignment]
    eng.kill._redis = eng.redis
    assert await eng._kill_active() is True            # kill switch unreadable = engaged
    with pytest.raises(ConnectionError):
        await eng.handle_signal(1, signal(), v, day0, week0, peak)
    with transaction(factory) as s:
        assert s.scalar(select(models.Order)) is None   # nothing was sent


# ------------------------------------------------------------------ forward carry paper test
async def test_carry_paper_step_entry_funding_idempotence_gap_and_restart(
        migrated_engine: Engine) -> None:
    import httpx
    from nexus_market import carry_paper as cp

    factory = make_session_factory(migrated_engine)
    state = {"down": False, "funding": []}
    t0 = datetime(2026, 11, 22, 12, 5, tzinfo=UTC)

    def handler(req: httpx.Request) -> httpx.Response:
        if state["down"] and "fapi" in str(req.url):
            return httpx.Response(451, text="Service unavailable from a restricted location")
        if req.url.path.endswith("/ticker/bookTicker"):
            px = "60000" if "BTC" in str(req.url) else "3000"
            return httpx.Response(200, json={"bidPrice": px, "askPrice": px})
        if req.url.path.endswith("/fundingRate"):
            start = int(req.url.params["startTime"])
            return httpx.Response(200, json=[f for f in state["funding"]
                                             if f["fundingTime"] >= start])
        return httpx.Response(404)

    try:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            ledger = cp.Ledger(factory)
            await cp.step(ledger, client, t0)
            t_f = int((t0 + timedelta(hours=4)).timestamp() * 1000)
            state["funding"] = [{"fundingTime": t_f, "fundingRate": "0.0001",
                                 "markPrice": "60000"}]
            await cp.step(cp.Ledger(factory), client, t0 + timedelta(hours=5))   # "restart"
            await cp.step(cp.Ledger(factory), client, t0 + timedelta(hours=6))   # same funding again
            state["down"] = True
            await cp.step(ledger, client, t0 + timedelta(hours=7))
        with transaction(factory) as s:
            rows = s.scalars(select(models.CarryPaperEvent)
                             .order_by(models.CarryPaperEvent.id)).all()
            kinds = [(r.symbol, r.kind) for r in rows]
        assert kinds.count(("BTCUSDT", "entry")) == 1 and kinds.count(("ETHUSDT", "entry")) == 1
        assert kinds.count(("BTCUSDT", "funding")) == 1          # applied once only
        assert kinds.count(("BTCUSDT", "gap")) == 1 and kinds.count(("ETHUSDT", "gap")) == 1
        latest = cp.Ledger(factory).latest()
        assert latest["BTCUSDT"].funding == D("0.25")             # 2500/60000 BTC x 60000 x 0.0001
        rep = cp.report([{"id": r.id, "kind": r.kind, "symbol": r.symbol, "at": r.at,
                          "data": r.data} for r in rows], t0 + timedelta(hours=8))
        assert rep["started"] and rep["gap_hours"] == 2 and D(rep["funding_received"]) > 0
    finally:
        with migrated_engine.begin() as c:
            c.execute(text("TRUNCATE carry_paper_events"))


async def test_forward_taker_records_trades_once_and_gaps(
        migrated_engine: Engine, redis: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    import httpx
    from nexus_backtest import forward_taker as ft

    factory = make_session_factory(migrated_engine)
    trade = {"signal_time": "2026-11-23T10:00:00+00:00", "entry_time": "2026-11-23T11:00:00+00:00",
             "exit_time": "2026-11-23T15:00:00+00:00", "entry": 100.0, "exit": 102.0,
             "stop": 95.0, "target": 103.0, "ret": 0.0185, "outcome": "target",
             "regime": "range"}

    async def fake_load(client: Any, symbol: str, now: datetime,
                        redis: Any = None) -> dict[str, Any]:
        import pandas as pd
        idx = pd.DatetimeIndex([pd.Timestamp("2026-11-23T16:00:00+00:00")])
        f = pd.DataFrame({"open": [1.0], "high": [1.0], "low": [1.0], "close": [1.0],
                          "volume": [1.0]}, index=idx)
        return {"5m": f, "1h": f, "4h": f}

    monkeypatch.setattr(ft, "load", fake_load)
    monkeypatch.setattr(ft, "evaluate", lambda frames: ([trade], []))
    try:
        async with httpx.AsyncClient() as client:
            await ft.step(factory, redis, client)
            await ft.step(factory, redis, client)            # same trade again: not duplicated

            async def refused(*a: Any, **k: Any) -> Any:
                raise PermissionError("Binance refuses this server's location")
            monkeypatch.setattr(ft, "load", refused)
            st = await ft.step(factory, redis, client)
        assert st["symbols"]["BTCUSDT"]["ok"] is False
        with transaction(factory) as s:
            rows = s.scalars(select(models.ForwardTestEvent)).all()
        kinds = sorted((r.symbol, r.kind) for r in rows)
        assert kinds == [("BTCUSDT", "gap"), ("BTCUSDT", "trade"),
                         ("ETHUSDT", "gap"), ("ETHUSDT", "trade")]
        assert await redis.get(ft.STATUS_KEY) is not None
    finally:
        with migrated_engine.begin() as c:
            c.execute(text("TRUNCATE forward_test_events"))


# ---------------------------------------------------------------- Phase A: risk state
async def _risk_state(redis: Any) -> dict[str, Any]:
    raw = await redis.get("nexus:risk:state:paper")
    assert raw is not None
    return dict(json.loads(raw))


def _transitions(factory: Any) -> list[dict[str, Any]]:
    with transaction(factory) as s:
        rows = s.scalars(select(models.AuditLog).where(
            models.AuditLog.action == "risk_state_changed").order_by(models.AuditLog.id)).all()
        return [dict(r.details) for r in rows]


async def test_stale_candles_put_risk_state_in_no_new_trades(
        engine_setup: tuple[Any, ...]) -> None:
    eng, factory, _, redis = engine_setup
    del eng._market_data_stale                     # use the real freshness check
    eng.bootstrap()
    await eng.cycle()                              # no candles at all = stale
    st = await _risk_state(redis)
    assert st["state"] == "NO_NEW_TRADES" and "market data stale" in st["reasons"]
    assert not st["entries_allowed"]
    with transaction(factory) as s:
        pid = repo.pair_id(s, "binance", "BTC/USDT")
        bar = utcnow().replace(second=0, microsecond=0) - timedelta(minutes=10)
        repo.upsert_candles(s, pid, "5m", [repo.CandleRow(bar, D(1), D(1), D(1), D(1), D(1))])
    await eng.cycle()
    assert (await _risk_state(redis))["state"] == "NORMAL"
    await eng.cycle()                              # unchanged: no extra audit row
    assert [(t["from"], t["to"]) for t in _transitions(factory)] == [
        (None, "NO_NEW_TRADES"), ("NO_NEW_TRADES", "NORMAL")]


async def test_risk_state_blocks_signals_and_survives_restart(
        engine_setup: tuple[Any, ...]) -> None:
    eng, factory, _, redis = engine_setup
    eng.bootstrap()
    with transaction(factory) as s:               # owner floor, durable in the audit log
        repo.record_audit(s, actor="user:1", action="risk_state_floor_set",
                          details={"mode": "paper", "state": "CLOSE_ONLY", "reason": "test"})
    await eng.cycle()
    assert (await _risk_state(redis))["state"] == "CLOSE_ONLY"
    await eng.handle_signal(1, signal(), await eng.valuation(), D(10_000), D(10_000), D(10_000))
    status, reason = await _decision(factory)
    assert status == "rejected" and "risk state CLOSE_ONLY" in reason
    # A restarted trader re-derives the same state and does not log a fake transition.
    fresh = TradingEngine(eng.settings, factory, redis, eng.adapters, eng.broker, eng.books)
    fresh._market_data_stale = lambda: False  # type: ignore[method-assign]
    fresh.bootstrap()
    await fresh.cycle()
    assert fresh.risk_status.state.value == "CLOSE_ONLY"
    assert len(_transitions(factory)) == 1
    # A floor written by a non-user actor is ignored (only the owner sets it).
    with transaction(factory) as s:
        repo.record_audit(s, actor="system:x", action="risk_state_floor_set",
                          details={"mode": "paper", "state": "NORMAL"})
    await fresh.cycle()
    assert fresh.risk_status.state.value == "CLOSE_ONLY"


async def test_reconciliation_mismatch_means_close_only_and_cancels_entries(
        engine_setup: tuple[Any, ...]) -> None:
    from nexus_execution.reconcile import ReconResult, ReconStatus

    eng, _factory, _, redis = engine_setup
    eng.bootstrap()

    async def mismatch(*_: Any, **__: Any) -> ReconResult:
        return ReconResult(ReconStatus.MISMATCH, utcnow())

    cancelled: list[str] = []

    async def record_cancel(reason: str) -> None:
        cancelled.append(reason)

    eng.reconciler.run = mismatch  # type: ignore[method-assign]
    eng._cancel_entries = record_cancel  # type: ignore[method-assign]
    await eng.cycle()
    st = await _risk_state(redis)
    assert st["state"] == "CLOSE_ONLY"
    assert "exchange balances lower than the internal ledger" in st["reasons"]
    assert cancelled == ["risk state CLOSE_ONLY"]


class _FakeAccount:
    def __init__(self) -> None:
        self.balances: dict[str, Any] = {}
        self.fail = False
        self.calls = 0

    async def fetch_balances(self) -> dict[str, Any]:
        from nexus_exchange.errors import ExchangeError

        self.calls += 1
        if self.fail:
            raise ExchangeError("timeout")
        return self.balances


async def test_live_balance_reconciliation(engine_setup: tuple[Any, ...]) -> None:
    from nexus_exchange.models import AssetBalance
    from nexus_execution.reconcile import BalanceReconciler, ReconStatus

    _, factory, _, redis = engine_setup
    with transaction(factory) as s:
        owner = s.scalar(select(models.User.id))
        p = models.Portfolio(user_id=owner, name="main", mode="live", quote_currency="USDT")
        s.add(p)
        s.flush()
        pid = p.id
        s.add(models.Balance(portfolio_id=pid, mode="live",
                             exchange_id=repo.exchange_id(s, "binance"), asset="USDT",
                             free=D(100), locked=D(0), source="internal", synced_at=utcnow()))
    acct = _FakeAccount()
    rec = BalanceReconciler(factory, redis, "live", {"binance": acct},  # type: ignore[dict-item]
                            interval=60)

    acct.balances = {"USDT": AssetBalance("USDT", D(120), D(0)),
                     "BNB": AssetBalance("BNB", D("0.5"), D(0))}
    ok = await rec.run(pid)
    assert ok.status is ReconStatus.OK and ok.unmanaged == {"binance:USDT": "20",
                                                            "binance:BNB": "0.5"}
    assert (await rec.run(pid)) is ok and acct.calls == 1      # throttled between runs

    acct.balances = {"USDT": AssetBalance("USDT", D(60), D(0))}
    bad = await rec.run(pid, force=True)
    assert bad.status is ReconStatus.MISMATCH and bad.blocks_entries
    assert bad.mismatches[0].asset == "USDT" and D(bad.mismatches[0].shortfall) == D(40)

    acct.fail = True
    down = await rec.run(pid, force=True)
    assert down.status is ReconStatus.UNAVAILABLE and down.blocks_entries

    acct.fail = False
    await redis.set("nexus:binance:ban:production", "2026-11-23T06:38:00+00:00")
    banned = await rec.run(pid, force=True)
    assert banned.status is ReconStatus.UNAVAILABLE and "banned" in (banned.detail or "")
    assert acct.calls == 3                                     # no call while banned

    with transaction(factory) as s:
        internal = s.scalar(select(models.Balance.free).where(
            models.Balance.portfolio_id == pid, models.Balance.source == "internal"))
        reported = dict(s.execute(select(models.Balance.asset, models.Balance.free).where(
            models.Balance.portfolio_id == pid, models.Balance.source == "exchange")).all())
        actions = list(s.scalars(select(models.AuditLog.action).where(
            models.AuditLog.actor == "system:reconcile").order_by(models.AuditLog.id)))
    assert internal == D(100), "reconciliation never edits the ledger"
    assert reported == {"USDT": D(60), "BNB": D(0)}
    # One audit row per status CHANGE (unavailable -> unavailable is not repeated).
    assert actions == ["balance_reconciliation_ok", "balance_reconciliation_mismatch",
                       "balance_reconciliation_unavailable"]
    assert json.loads(await redis.get("nexus:reconcile:live"))["status"] == "unavailable"


async def test_paper_reconciliation_is_not_applicable(engine_setup: tuple[Any, ...]) -> None:
    eng, _, _, _redis = engine_setup
    eng.bootstrap()
    res = await eng.reconciler.run(eng.portfolio_id)
    assert res.status.value == "not_applicable" and not res.blocks_entries


# ---------------------------------------------------------------- Phase A3: operating modes
def _set_mode(factory: Any, mode: str, actor: str = "user:1") -> None:
    with transaction(factory) as s:
        repo.record_audit(s, actor=actor, action="operating_mode_set", details={"mode": mode})


async def test_signals_only_mode_records_verdict_without_orders(
        engine_setup: tuple[Any, ...]) -> None:
    eng, factory, _, _ = engine_setup
    eng.bootstrap()
    _set_mode(factory, "SIGNALS_ONLY")
    await eng.cycle()
    assert eng.operating_mode.value == "SIGNALS_ONLY"
    await eng.handle_signal(1, signal(), await eng.valuation(), D(10_000), D(10_000), D(10_000))
    status, reason = await _decision(factory)
    assert status == "approved" and reason.startswith("SIGNALS_ONLY: passed all checks")
    with transaction(factory) as s:
        assert s.scalar(select(func.count()).select_from(models.Order)) == 0


async def test_analysis_only_mode_does_not_evaluate_entries_and_bad_values_fail_closed(
        engine_setup: tuple[Any, ...]) -> None:
    eng, factory, _, redis = engine_setup
    eng.bootstrap()
    from nexus_common.botstate import BotState, set_state

    await set_state(redis, BotState.RUNNING, actor="user:1", reason="test")
    calls: list[int] = []

    async def spy(*_: Any) -> None:
        calls.append(1)

    eng.evaluate_entries = spy  # type: ignore[method-assign]
    await eng.cycle()
    assert calls == [1]                                    # default PAPER_TRADING evaluates
    _set_mode(factory, "ANALYSIS_ONLY")
    await eng.cycle()
    assert calls == [1] and eng.operating_mode.value == "ANALYSIS_ONLY"
    _set_mode(factory, "PAPER_TRADING", actor="system:x")  # not the owner: ignored
    await eng.cycle()
    assert calls == [1]
    _set_mode(factory, "bogus")                            # unrecognised: fail closed
    await eng.cycle()
    assert calls == [1] and eng.operating_mode.value == "ANALYSIS_ONLY"


async def test_crash_between_submit_and_record_in_trader_cycle_recovers(
        engine_setup: tuple[Any, ...]) -> None:
    """End to end on the paper broker: a placed order left pending_submit by a crash is
    recovered by a fresh trader without placing it twice."""
    eng, factory, _, redis = engine_setup
    eng.bootstrap()
    original = eng.orders._apply

    def crash(*_: Any) -> None:
        raise RuntimeError("killed")

    eng.orders._apply = crash  # type: ignore[method-assign]
    with pytest.raises(RuntimeError):
        await eng.handle_signal(1, signal(), await eng.valuation(), D(10_000), D(10_000),
                                D(10_000))
    eng.orders._apply = original  # type: ignore[method-assign]
    with transaction(factory) as s:
        s.execute(text("UPDATE orders SET created_at = now() - interval '5 minutes'"))
    fresh = TradingEngine(eng.settings, factory, redis, eng.adapters, eng.broker, eng.books)
    fresh._market_data_stale = lambda: False  # type: ignore[method-assign]
    fresh.bootstrap()
    await fresh.cycle()
    with transaction(factory) as s:
        orders = s.scalars(select(models.Order)).all()
        assert len(orders) == 1 and orders[0].status == "filled"
        assert s.scalar(select(func.count()).select_from(models.Trade)) >= 1


# ---------------------------------------------------------------- rate-limit fix
async def test_binance_pause_never_trades_and_recovery_has_no_duplicate_orders(
        engine_setup: tuple[Any, ...]) -> None:
    from nexus_common import binance_guard as g

    eng, factory, _, redis = engine_setup
    eng.bootstrap()
    eng.books.redis = redis                     # no stream book in Redis: REST path, gated
    await redis.set(g._key("spot"), (utcnow() + timedelta(minutes=5)).isoformat())
    v = await eng.valuation()
    await eng.handle_signal(1, signal(), v, D(10_000), D(10_000), D(10_000))
    status, reason = await _decision(factory)
    assert status == "rejected" and "market data stale or missing" in reason

    def n_orders() -> int:
        with transaction(factory) as s:
            return int(s.scalar(select(func.count()).select_from(models.Order)) or 0)

    assert n_orders() == 0
    await redis.delete(g._key("spot"))          # Binance answers again
    later = Signal("momentum", "BTC/USDT", "15m", datetime(2026, 6, 1, 12, 15, tzinfo=UTC),
                   60_000, 59_000, 62_000, 0.6, "trend_up", {"params": {}})
    await eng.handle_signal(1, later, await eng.valuation(), D(10_000), D(10_000), D(10_000))
    await eng.handle_signal(1, later, await eng.valuation(), D(10_000), D(10_000), D(10_000))
    await eng.cycle()
    assert n_orders() == 1                      # the repeated signal placed nothing new


async def test_selector_rejects_a_passed_strategy_with_thin_regime_evidence(
        engine_setup: tuple[Any, ...]) -> None:
    eng, factory, _, _ = engine_setup
    eng.bootstrap()
    with transaction(factory) as s:
        for st in s.scalars(select(models.Strategy)):
            st.parameters = {"validation": {
                "passed": True, "allowed_regimes": ["trend_up"],
                "regime_edges": {"trend_up": {"n": 12, "expectancy": 0.01, "std": 0.01,
                                              "profit_factor": 2.0}}}}
    await eng.handle_signal(1, signal(), await eng.valuation(), D(10_000), D(10_000), D(10_000))
    status, reason = await _decision(factory)
    assert status == "rejected" and "strategy selector" in reason and "12 out-of-sample" in reason
    with transaction(factory) as s:
        assert s.scalar(select(func.count()).select_from(models.Order)) == 0


async def test_trend_paper_records_days_once_and_flags_divergence(
        migrated_engine: Engine, redis: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    import httpx
    from nexus_backtest import trend_paper as tp

    from tests.unit.test_trend_paper import bars

    factory = make_session_factory(migrated_engine)
    data = bars(10)

    async def fake_fetch(client: Any, symbol: str, now: datetime, redis: Any = None) -> Any:
        return data[symbol]

    monkeypatch.setattr(tp, "fetch", fake_fetch)
    try:
        async with httpx.AsyncClient() as client:
            st = await tp.step(factory, redis, client)
            assert st["ok"] and st["new_days"] == 10
            st = await tp.step(factory, redis, client)
            assert st["new_days"] == 0 and st["divergences"] == 0     # idempotent
            # Data revised for a recorded day: the record stays, a divergence is logged.
            changed = {k: v.copy() for k, v in data.items()}
            for v in changed.values():
                v.loc[v.index[-1], "close"] *= 1.5
            monkeypatch.setattr(tp, "fetch", lambda c, s, n, redis=None: _ret(changed[s]))
            st = await tp.step(factory, redis, client)
            assert st["divergences"] == 1 and st["new_days"] == 0   # flagged, never rewritten

            async def refused(*a: Any, **k: Any) -> Any:
                raise PermissionError("Binance refuses this server's location")
            monkeypatch.setattr(tp, "fetch", refused)
            st = await tp.step(factory, redis, client)
            assert st["ok"] is False
        with transaction(factory) as s:
            rows = s.scalars(select(models.ForwardTestEvent).where(
                models.ForwardTestEvent.run == tp.RUN)).all()
        kinds = [r.kind for r in rows]
        assert kinds.count("day") == 10 and kinds.count("gap") == 1
    finally:
        with migrated_engine.begin() as c:
            c.execute(text("TRUNCATE forward_test_events"))


async def _ret(v: Any) -> Any:
    return v


# ---------------------------------------------------------------- spot strategy engine
async def test_spot_engine_paper_runner_end_to_end(engine_setup: tuple[Any, ...],
                                                   monkeypatch: pytest.MonkeyPatch) -> None:
    """Entry through the real OrderManager and PaperBroker, every decision recorded,
    a stop exit on a later bar, and the live-mode refusal."""
    import numpy as np
    import pandas as pd
    from nexus_execution import spot_paper as sp

    eng, factory, state, redis = engine_setup
    # A full warm-up of closed 15m candles around 60,000 ending at the last full quarter hour.
    end = utcnow().replace(second=0, microsecond=0)
    end -= timedelta(minutes=end.minute % 15)
    idx = pd.date_range(end=end - timedelta(minutes=15), periods=sp.WARMUP_BARS, freq="15min",
                        tz="UTC")
    rng = np.random.default_rng(0)
    close = 60_000 * np.exp(np.cumsum(rng.normal(0, 0.0005, len(idx))))
    close *= 60_000 / close[-1]
    with transaction(factory) as s:
        pid = repo.pair_id(s, "binance", "BTC/USDT")
        rows = [repo.CandleRow(t.to_pydatetime(), D(str(round(c, 2))),
                               D(str(round(c * 1.001, 2))), D(str(round(c * 0.999, 2))),
                               D(str(round(c, 2))), D(100))
                for t, c in zip(idx, close, strict=True)]
        repo.upsert_candles(s, pid, "15m", rows)
    runner = sp.SpotPaperRunner(eng.settings, factory, redis, eng.adapters, eng.books, eng.broker)
    runner.bootstrap()
    now = end + timedelta(minutes=1)
    candidate = {"strategy": "trend_pullback", "timeframe": "15m", "bar": idx[-1], "entry": True,
                 "candidate": True, "reason": None, "stop": 59_400.0, "r": 600.0, "tp1": 60_900.0,
                 "tp2": 61_800.0, "score": 1.0, "chk_volume_confirmed": True}

    def fake_latest(sym: str, m15: Any, btc_h4: Any) -> Any:
        reg, _ = real_latest(sym, m15, btc_h4)
        return reg, [candidate]

    real_latest = sp.latest_candidates
    monkeypatch.setattr(sp, "latest_candidates", fake_latest)
    status = await runner.cycle(now)
    assert status["live_trading"] == "DISABLED" and status["symbols"]["BTC/USDT"]["fresh"]
    rec = status["signals"][0]
    assert rec["status"] == "executed", rec
    with transaction(factory) as s:
        sig = s.scalar(select(models.TradingSignal))
        assert sig is not None and sig.status == "executed" and sig.details["engine"] == "spot"
        pos = s.scalar(select(models.Position).where(models.Position.status == "open"))
        assert pos is not None and pos.stop_loss == D("59400")
        entry = s.get(models.Order, pos.entry_order_id)
        assert entry is not None and entry.details["tp1"] and entry.details["initial_stop"]
        risk = (float(pos.avg_entry_price) - 59_400) * float(pos.quantity)
        assert risk <= 0.0025 * 10_000 * 1.01          # sized from the stop: 0.25% of equity
    # The same bar again: no duplicate signal or order.
    await runner.cycle(now)
    with transaction(factory) as s:
        assert s.scalar(select(func.count()).select_from(models.TradingSignal)) == 1
    # A new bar trades through the stop: the position is closed by a stop exit.
    monkeypatch.setattr(sp, "latest_candidates", lambda sym, m15, b: (real_latest(sym, m15, b)[0], []))
    with transaction(factory) as s:
        repo.upsert_candles(s, pid, "15m", [repo.CandleRow(end, D(60_000), D(60_010), D(59_000),
                                                           D(59_100), D(100))])
    state.update(bid=59_090.0, ask=59_110.0)
    await runner.cycle(end + timedelta(minutes=16))
    with transaction(factory) as s:
        closed = s.scalar(select(models.Position).where(models.Position.status == "closed"))
        assert closed is not None and closed.exit_reason == "stop"
    # Paper only.
    live = eng.settings.model_copy(update={"trading_mode": "live"})
    with pytest.raises(PermissionError):
        sp.SpotPaperRunner(live, factory, redis, eng.adapters, eng.books, eng.broker)


async def test_trading_bot_pipeline_entry_exits_guards_and_restart(
        engine_setup: tuple[Any, ...], monkeypatch: pytest.MonkeyPatch) -> None:
    """NEXUS bot (5m) through the real OrderManager and PaperBroker:
    * entry sized at 0.25% of equity, with stop and 2R target persisted;
    * no duplicate entry on the same bar;
    * an invalidation exit at market;
    * after a restart (a new runner) the history is intact and nothing is re-ordered;
    * monitor mode records signals but never orders;
    * the 3% drawdown halt blocks entries."""
    import numpy as np
    import pandas as pd
    from nexus_execution import bot
    from nexus_execution import spot_paper as sp

    eng, factory, _state, redis = engine_setup
    end = utcnow().replace(second=0, microsecond=0)
    end -= timedelta(minutes=end.minute % 5)
    idx = pd.date_range(end=end - timedelta(minutes=5), periods=2500, freq="5min", tz="UTC")
    rng = np.random.default_rng(1)
    close = 60_000 * np.exp(np.cumsum(rng.normal(0, 0.0004, len(idx))))
    close *= 60_000 / close[-1]
    with transaction(factory) as s:
        pid = repo.pair_id(s, "binance", "BTC/USDT")
        repo.upsert_candles(s, pid, "5m", [
            repo.CandleRow(t.to_pydatetime(), D(str(round(c, 2))), D(str(round(c * 1.001, 2))),
                           D(str(round(c * 0.999, 2))), D(str(round(c, 2))), D(100))
            for t, c in zip(idx, close, strict=True)])
    flags = {"entry": True, "exit": False}

    def fake(sym: str, m5: Any, _b: Any = None) -> Any:
        reg = pd.DataFrame({"regime": ["TRADEABLE"], "trend": ["up"], "btc_risk_off": [False],
                            "risk_mult": [1.0]}, index=[m5.index[-1]])
        return reg, [{"strategy": "bot_ema_rsi_macd", "timeframe": "5m", "bar": m5.index[-1],
                      "entry": flags["entry"], "candidate": flags["entry"],
                      "reason": None if flags["entry"] else "volume not met",
                      "stop": 59_400.0, "r": 600.0, "tp1": 61_200.0, "tp2": 61_200.0,
                      "score": 1.0, "exit": flags["exit"], "record": True,
                      "chk_volume": flags["entry"]}]

    def runner(monitor: bool = False) -> Any:
        prof = bot.profile(monitor)
        prof = sp.EngineProfile(**{**prof.__dict__, "candidates": fake})
        r = sp.SpotPaperRunner(eng.settings, factory, redis, eng.adapters, eng.books,
                               eng.broker, cfg=bot.BOT_RISK, profile=prof)
        r.bootstrap()
        return r

    r1 = runner()
    now = end + timedelta(minutes=1)
    status = await r1.cycle(now)
    assert status["engine"] == "bot" and status["mode"] == "paper"
    rec = status["signals"][0]
    assert rec["status"] == "executed", rec
    with transaction(factory) as s:
        pos = s.scalar(select(models.Position).where(models.Position.status == "open",
                                                     models.Position.portfolio_id
                                                     == r1.portfolio_id))
        assert pos is not None and pos.stop_loss == D("59400")
        entry = s.get(models.Order, pos.entry_order_id)
        assert entry is not None and entry.status == "filled"
        assert D(entry.details["target"]) > pos.avg_entry_price
        risk = (float(pos.avg_entry_price) - 59_400) * float(pos.quantity)
        assert risk <= 0.0025 * 10_000 * 1.01                  # 0.25% of equity
    await r1.cycle(now)                                        # same bar: no duplicate
    with transaction(factory) as s:
        assert s.scalar(select(func.count()).select_from(models.Order).where(
            models.Order.portfolio_id == r1.portfolio_id)) == 1
    # Restart: a fresh runner sees the open position and does not buy again.
    r2 = runner()
    assert r2.portfolio_id == r1.portfolio_id
    await r2.cycle(now)
    # Invalidation on the next bar: exit at market.
    flags.update(entry=False, exit=True)
    with transaction(factory) as s:
        repo.upsert_candles(s, pid, "5m", [repo.CandleRow(end, D(60_000), D(60_020),
                                                          D(59_980), D(60_000), D(100))])
    await r2.cycle(end + timedelta(minutes=6))
    with transaction(factory) as s:
        closed = s.scalar(select(models.Position).where(
            models.Position.portfolio_id == r1.portfolio_id, models.Position.status == "closed"))
        assert closed is not None and closed.exit_reason == "invalidation"
        assert s.scalar(select(func.count()).select_from(models.Order).where(
            models.Order.portfolio_id == r1.portfolio_id)) == 2
    # Monitor mode: a qualifying signal is recorded but no order is placed.
    flags.update(entry=True, exit=False)
    with transaction(factory) as s:
        repo.upsert_candles(s, pid, "5m", [repo.CandleRow(end + timedelta(minutes=5), D(60_000),
                                                          D(60_020), D(59_980), D(60_000),
                                                          D(100))])
    st = await runner(monitor=True).cycle(end + timedelta(minutes=11))
    assert st["mode"] == "monitor"
    assert any("monitor mode" in x for x in st["signals"][0]["reasons"])
    with transaction(factory) as s:
        assert s.scalar(select(func.count()).select_from(models.Order).where(
            models.Order.portfolio_id == r1.portfolio_id)) == 2
    # Drawdown halt: a recorded 4% loss puts equity below the 3% limit from the peak.
    with transaction(factory) as s:
        closed = s.scalar(select(models.Position).where(
            models.Position.portfolio_id == r1.portfolio_id, models.Position.status == "closed"))
        assert closed is not None
        closed.realized_pnl = D(-400)
        bal = s.scalar(select(models.Balance).where(
            models.Balance.portfolio_id == r1.portfolio_id, models.Balance.asset == "USDT"))
        assert bal is not None
        bal.free = D(9_550)
    with transaction(factory) as s:
        repo.upsert_candles(s, pid, "5m", [repo.CandleRow(end + timedelta(minutes=10), D(60_000),
                                                          D(60_020), D(59_980), D(60_000),
                                                          D(100))])
    st = await runner().cycle(end + timedelta(minutes=16))
    assert any("drawdown limit (3%)" in x for x in st["signals"][0]["reasons"]), st["signals"]


async def test_owner_approved_bot_strategy_runs_in_the_main_trader(
        engine_setup: tuple[Any, ...]) -> None:
    """The bot strategy failed no validation because it never had one: the main trader
    ignores it until the OWNER approves it (actor user:...). With approval it is listed,
    passes the regime gate, and a signal becomes a sized order; a system actor cannot
    approve; disabling removes it again."""
    eng, factory, _, _ = engine_setup
    eng.bootstrap()
    with transaction(factory) as s:
        row = s.scalar(select(models.Strategy).where(models.Strategy.code == "bot_ema_rsi_macd"))
        assert row is not None
        bot_id = row.id
        for st in s.scalars(select(models.Strategy).where(models.Strategy.id != bot_id)):
            st.is_enabled = False
        row.is_enabled = True                       # enabled but not approved: ignored
    assert eng.enabled_strategies() == []
    with transaction(factory) as s:
        row = s.get(models.Strategy, bot_id)
        assert row is not None
        row.parameters = {**(row.parameters or {}),
                          "owner_override": {"active": True, "actor": "system:x"}}
    assert eng.enabled_strategies() == []           # only the owner can approve
    with transaction(factory) as s:
        row = s.get(models.Strategy, bot_id)
        assert row is not None
        row.parameters = {**(row.parameters or {}),
                          "owner_override": {"active": True, "actor": "user:1"}}
    (sid, strat), = eng.enabled_strategies()
    assert sid == bot_id and strat.code == "bot_ema_rsi_macd" and strat.history_bars == 3000
    sig = Signal("bot_ema_rsi_macd", "BTC/USDT", "5m", datetime(2026, 6, 1, 12, tzinfo=UTC),
                 60_000, 59_400, 61_200, 1.0, "trend_up", {"params": {}})
    assert eng._regime_block(bot_id, sig) is None
    v = await eng.valuation()
    day0, week0, peak = eng.period_starts(v.equity, utcnow())
    await eng.handle_signal(bot_id, sig, v, day0, week0, peak)
    status, reason = await _decision(factory)
    assert status == "executed", reason
    with transaction(factory) as s:
        order = s.scalar(select(models.Order).order_by(models.Order.id.desc()))
        assert order is not None and order.mode == "paper" and order.purpose == "entry"
        row = s.get(models.Strategy, bot_id)
        assert row is not None
        row.parameters = {**(row.parameters or {}),
                          "owner_override": {"active": False, "actor": "user:1"}}
    assert eng.enabled_strategies() == []
