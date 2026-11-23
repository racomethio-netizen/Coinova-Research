"""Order lifecycle on real PostgreSQL with a scripted broker."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import timedelta
from decimal import Decimal as D
from typing import Any

import pytest
from nexus_common.timeutil import utcnow
from nexus_config import LIVE_TRADING_ACK_PHRASE, Settings
from nexus_db import make_session_factory, models, repo, transaction
from nexus_exchange.errors import InsufficientFunds, OrderStateUnknown
from nexus_exchange.models import OrderStatus, Side
from nexus_execution.brokers import BrokerFill, BrokerUpdate, LiveBroker
from nexus_execution.live_gate import GATE_TOKEN, build_live_broker, live_gate_problems
from nexus_execution.order_manager import (
    DuplicateOrder,
    InsufficientBalance,
    OrderIntent,
    OrderManager,
    fund_paper_portfolio,
)
from sqlalchemy import Engine, func, select, text
from sqlalchemy.orm import Session, sessionmaker

from tests.integration.conftest import truncate

pytestmark = pytest.mark.db


class ScriptedBroker:
    mode = "paper"

    def __init__(self) -> None:
        self.submits: list[Any] = []
        self.submit_result: BrokerUpdate | Exception = BrokerUpdate(OrderStatus.OPEN, "X1")
        self.refresh_result: BrokerUpdate = BrokerUpdate(OrderStatus.OPEN, "X1")
        self.find_result: BrokerUpdate | Exception | None = None

    async def submit(self, req: Any, *, exchange: str) -> BrokerUpdate:
        self.submits.append(req)
        if isinstance(self.submit_result, Exception):
            raise self.submit_result
        return self.submit_result

    async def refresh(self, **_: Any) -> BrokerUpdate:
        return self.refresh_result

    async def find(self, *_: Any, **__: Any) -> BrokerUpdate | None:
        if isinstance(self.find_result, Exception):
            raise self.find_result
        return self.find_result

    async def cancel(self, **_: Any) -> None:
        return None


def fill(tid: str, price: str, amount: str, fee: str = "0") -> BrokerFill:
    return BrokerFill(tid, D(price), D(amount), D(fee), "USDT", False, utcnow())


@pytest.fixture
def factory(migrated_engine: Engine) -> Iterator[sessionmaker[Session]]:
    f = make_session_factory(migrated_engine)
    with transaction(f) as s:
        uid = s.execute(text("INSERT INTO users (email, password_hash, role) VALUES "
                             "('o@example.test','x','owner') RETURNING id")).scalar_one()
        ex = repo.exchange_id(s, "binance")
        repo.upsert_trading_pair(s, exchange_id=ex, symbol="BTC/USDT", exchange_symbol="BTCUSDT",
                                 base="BTC", quote="USDT", price_tick=D("0.01"),
                                 amount_step=D("0.00001"), min_amount=None, min_notional=None,
                                 maker_fee=None, taker_fee=None, synced_at=utcnow())
        p = models.Portfolio(user_id=uid, name="main", mode="paper", initial_capital=D(10_000))
        s.add(p)
        s.flush()
        fund_paper_portfolio(s, p.id, ex, "USDT", D(10_000))
    yield f
    truncate(migrated_engine, "trades, orders, positions, balances, risk_events, portfolios, "
                              "trading_pairs, users, audit_logs, exchange_credentials")


def pid(f: sessionmaker[Session]) -> int:
    with transaction(f) as s:
        return int(s.scalar(select(models.Portfolio.id)) or 0)


def intent(f: sessionmaker[Session], **kw: Any) -> OrderIntent:
    base: dict[str, Any] = dict(portfolio_id=pid(f), mode="paper", exchange="binance",
                                symbol="BTC/USDT", side=Side.BUY, amount=D("0.1"),
                                price=D(60_000), purpose="entry",
                                details={"stop": "59000", "target": "63000"})
    base.update(kw)
    return OrderIntent(**base)


def balances(f: sessionmaker[Session]) -> dict[str, tuple[D, D]]:
    with transaction(f) as s:
        return {b.asset: (b.free, b.locked) for b in s.scalars(select(models.Balance))}


def order(f: sessionmaker[Session], oid: int) -> models.Order:
    with transaction(f) as s:
        o = s.get(models.Order, oid)
        assert o is not None
        return o


async def test_fill_creates_position_and_settles_ledger(factory: sessionmaker[Session]) -> None:
    b = ScriptedBroker()
    b.submit_result = BrokerUpdate(OrderStatus.FILLED, "X1", (fill("t1", "60000", "0.1", "6"),))
    oid = await OrderManager(factory, b).place(intent(factory))
    o = order(factory, oid)
    assert o.status == "filled" and o.filled_amount == D("0.1") and o.closed_at is not None
    usdt, btc = balances(factory)["USDT"], balances(factory)["BTC"]
    assert usdt == (D(10_000) - D(6000) - D(6), D(0))      # reserve fully released
    assert btc == (D("0.1"), D(0))
    with transaction(factory) as s:
        pos = s.scalar(select(models.Position))
        assert pos is not None and pos.quantity == D("0.1") and pos.cost_basis == D(6006)
        assert pos.stop_loss == D(59000) and pos.take_profit == D(63000)
        assert pos.mode == "paper" and o.position_id == pos.id


async def test_duplicate_active_order_blocked_by_database(factory: sessionmaker[Session]) -> None:
    b = ScriptedBroker()
    om = OrderManager(factory, b)
    await om.place(intent(factory))  # stays open
    with pytest.raises(DuplicateOrder):
        await om.place(intent(factory))
    assert len(b.submits) == 1


async def test_timeout_goes_unknown_never_resubmits_then_reconciles(
        factory: sessionmaker[Session]) -> None:
    b = ScriptedBroker()
    om = OrderManager(factory, b, settle_seconds=0)
    b.submit_result = OrderStateUnknown("timeout", client_order_id="c")
    oid = await om.place(intent(factory))
    assert order(factory, oid).status == "unknown" and len(b.submits) == 1
    with transaction(factory) as s:
        assert s.scalar(select(models.RiskEvent.event_type)) == "order_state_unknown"
    # Lookup fails: stays unknown, no resubmission.
    from nexus_exchange.errors import ExchangeUnavailable
    b.find_result = ExchangeUnavailable("down")
    await om.reconcile_unknown()
    assert order(factory, oid).status == "unknown" and len(b.submits) == 1
    # Found at the exchange, filled: adopt it.
    b.find_result = BrokerUpdate(OrderStatus.FILLED, "X9", (fill("t9", "60000", "0.1"),))
    assert await om.reconcile_unknown() == 1
    o = order(factory, oid)
    assert o.status == "filled" and o.exchange_order_id == "X9" and len(b.submits) == 1


async def test_unknown_not_found_after_settle_is_rejected_and_released(
        factory: sessionmaker[Session]) -> None:
    b = ScriptedBroker()
    b.submit_result = OrderStateUnknown("timeout", client_order_id="c")
    om = OrderManager(factory, b, settle_seconds=0)
    oid = await om.place(intent(factory))
    b.find_result = None
    await om.reconcile_unknown()
    assert order(factory, oid).status == "rejected"
    assert balances(factory)["USDT"] == (D(10_000), D(0))


async def test_definitive_rejection_releases_reserve(factory: sessionmaker[Session]) -> None:
    b = ScriptedBroker()
    b.submit_result = InsufficientFunds("nope")
    oid = await OrderManager(factory, b).place(intent(factory))
    o = order(factory, oid)
    assert o.status == "rejected" and "InsufficientFunds" in (o.last_error or "")
    assert balances(factory)["USDT"] == (D(10_000), D(0))


async def test_partial_fills_idempotent_across_refreshes(factory: sessionmaker[Session]) -> None:
    b = ScriptedBroker()
    om = OrderManager(factory, b)
    b.submit_result = BrokerUpdate(OrderStatus.PARTIALLY_FILLED, "X1",
                                   (fill("t1", "60000", "0.04"),))
    oid = await om.place(intent(factory))
    b.refresh_result = BrokerUpdate(OrderStatus.PARTIALLY_FILLED, "X1",
                                    (fill("t1", "60000", "0.04"), fill("t2", "59990", "0.03")))
    await om.refresh_open()
    await om.refresh_open()  # same fills again: no double counting
    o = order(factory, oid)
    assert o.status == "partially_filled" and o.filled_amount == D("0.07")
    exact = (D(60000) * D("0.04") + D(59990) * D("0.03")) / D("0.07")
    assert o.average_fill_price == exact.quantize(D("1e-18"))  # NUMERIC(38,18)
    # Exchange says filled but last fill not yet visible: stay partially_filled.
    b.refresh_result = BrokerUpdate(OrderStatus.FILLED, "X1", ())
    await om.refresh_open()
    assert order(factory, oid).status == "partially_filled"
    b.refresh_result = BrokerUpdate(OrderStatus.FILLED, "X1", (fill("t3", "60010", "0.03"),))
    await om.refresh_open()
    assert order(factory, oid).status == "filled"
    with transaction(factory) as s:
        assert s.scalar(select(func.count()).select_from(models.Trade)) == 3


async def test_exit_closes_position_with_exact_pnl(factory: sessionmaker[Session]) -> None:
    b = ScriptedBroker()
    om = OrderManager(factory, b)
    b.submit_result = BrokerUpdate(OrderStatus.FILLED, "E", (fill("e1", "60000", "0.1", "6"),))
    oid = await om.place(intent(factory))
    pos_id = order(factory, oid).position_id
    b.submit_result = BrokerUpdate(OrderStatus.FILLED, "S", (fill("s1", "62000", "0.1", "6.2"),))
    await om.place(intent(factory, side=Side.SELL, price=D(62000), purpose="exit",
                          position_id=pos_id, details={"exit_reason": "take_profit"}))
    with transaction(factory) as s:
        pos = s.get(models.Position, pos_id)
        assert pos is not None and pos.status == "closed" and pos.quantity == 0
        assert pos.realized_pnl == D(6200) - D("6.2") - D(6006)
        assert pos.exit_reason == "take_profit"
    assert balances(factory)["USDT"][0] == D(10_000) - D(6006) + D(6200) - D("6.2")
    assert balances(factory)["BTC"] == (D(0), D(0))


async def test_insufficient_balance_creates_no_order(factory: sessionmaker[Session]) -> None:
    with pytest.raises(InsufficientBalance):
        await OrderManager(factory, ScriptedBroker()).place(intent(factory, amount=D(1)))
    with transaction(factory) as s:
        assert s.scalar(select(func.count()).select_from(models.Order)) == 0


async def test_mode_mismatch_refused(factory: sessionmaker[Session]) -> None:
    with pytest.raises(PermissionError):
        await OrderManager(factory, ScriptedBroker()).place(intent(factory, mode="live"))


class FakeRedis:
    def __init__(self) -> None:
        self.d: dict[str, Any] = {}

    async def get(self, k: str) -> Any:
        return self.d.get(k)


async def test_live_gate(factory: sessionmaker[Session]) -> None:
    with pytest.raises(PermissionError):
        LiveBroker({}, _gate_token=object())
    ok_settings = Settings(app_env="production", credential_encryption_keys="k1:" + "A" * 43,
                           session_secret="s" * 40, trading_mode="live",
                           live_trading_enabled=True, live_trading_ack=LIVE_TRADING_ACK_PHRASE)
    redis = FakeRedis()
    with transaction(factory) as s:
        problems = await live_gate_problems(Settings(), s, redis, ["binance"])
        assert "no owner approval recorded" in problems
        assert any("no active production API credential" in p for p in problems)
        assert "trading_mode is not 'live'" in problems
        with pytest.raises(PermissionError):
            await build_live_broker(Settings(), s, redis, {})
    with transaction(factory) as s:
        uid = s.scalar(select(models.User.id))
        repo.record_audit(s, actor=f"user:{uid}", action="live_trading_approved", user_id=uid)
        s.add(models.ExchangeCredential(
            user_id=uid, exchange_id=repo.exchange_id(s, "binance"), label="main",
            api_key_encrypted=b"x", api_secret_encrypted=b"x", key_fingerprint="f",
            encryption_key_id="k1", withdrawal_disabled_verified=True))
    with transaction(factory) as s:
        assert await live_gate_problems(ok_settings, s, redis, ["binance"]) == []
        broker = await build_live_broker(ok_settings, s, redis, {})
        assert broker.mode == "live"
        redis.d["nexus:kill_switch"] = "{}"
        assert "kill switch engaged" in await live_gate_problems(ok_settings, s, redis, ["binance"])
    with transaction(factory) as s:
        repo.record_audit(s, actor=f"user:{uid}", action="live_trading_revoked", user_id=uid)
    with transaction(factory) as s:
        redis.d.clear()
        assert "owner approval was revoked" in await live_gate_problems(
            ok_settings, s, redis, ["binance"])
    assert GATE_TOKEN is not None
    _ = timedelta


# ---------------------------------------------------------------- Phase A6: restarts
def _age_orders(f: sessionmaker[Session], minutes: int = 5) -> None:
    with transaction(f) as s:
        s.execute(text("UPDATE orders SET created_at = now() - make_interval(mins => :m)"),
                  {"m": minutes})


async def test_crash_after_submit_before_recording_is_recovered_by_client_id(
        factory: sessionmaker[Session]) -> None:
    """The exchange accepted and filled the order, then the process died before the
    answer was stored. After restart: looked up by client order ID, fills applied once,
    never resubmitted."""
    b = ScriptedBroker()
    b.submit_result = BrokerUpdate(OrderStatus.FILLED, "X1", (fill("t1", "60000", "0.1", "6"),))
    om = OrderManager(factory, b)

    def crash(*_: Any) -> None:
        raise RuntimeError("process killed")

    om._apply = crash  # type: ignore[method-assign]
    with pytest.raises(RuntimeError):
        await om.place(intent(factory))
    oid = int(order_ids(factory)[0])
    assert order(factory, oid).status == "pending_submit"
    # Restart: a fresh manager. A recent pending order is left alone (could be in flight).
    fresh = OrderManager(factory, b, settle_seconds=60)
    assert await fresh.recover_pending() == 0
    _age_orders(factory)
    assert await fresh.recover_pending() == 1
    assert order(factory, oid).status == "unknown"
    b.find_result = b.submit_result
    assert await fresh.reconcile_unknown() == 1
    await fresh.reconcile_unknown()                       # idempotent
    o = order(factory, oid)
    assert o.status == "filled" and len(b.submits) == 1
    with transaction(factory) as s:
        assert s.scalar(select(func.count()).select_from(models.Trade)) == 1
        pos = s.scalar(select(models.Position))
        assert pos is not None and pos.quantity == D("0.1")
    assert balances(factory)["BTC"] == (D("0.1"), D(0))


async def test_crash_before_submit_is_rejected_and_funds_released(
        factory: sessionmaker[Session]) -> None:
    """The intent was saved but the process died before anything was sent: after the
    settle delay the exchange has no such order, so it is rejected and the reserve freed."""
    b = ScriptedBroker()
    b.submit_result = RuntimeError("process killed")
    om = OrderManager(factory, b, settle_seconds=60)
    with pytest.raises(RuntimeError):
        await om.place(intent(factory))
    oid = int(order_ids(factory)[0])
    assert balances(factory)["USDT"][1] > 0                  # still reserved
    _age_orders(factory)
    await om.recover_pending()
    b.find_result = None
    await om.reconcile_unknown()
    assert order(factory, oid).status == "rejected" and len(b.submits) == 1
    assert balances(factory)["USDT"] == (D(10_000), D(0))
    # The pair is free again (the stuck order no longer blocks new entries).
    b.submit_result = BrokerUpdate(OrderStatus.OPEN, "X2")
    await om.place(intent(factory))


async def test_failure_inside_fill_application_leaves_no_partial_state(
        factory: sessionmaker[Session]) -> None:
    """Fill, ledger and position are one transaction: a failure in the middle writes
    nothing, and the next refresh applies the fill exactly once."""
    b = ScriptedBroker()
    om = OrderManager(factory, b)
    oid = await om.place(intent(factory))                  # open, no fills
    before = balances(factory)
    original = om._update_position
    calls = {"n": 0}

    def flaky(*a: Any, **kw: Any) -> Any:
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("crash between fill and position update")
        return original(*a, **kw)

    om._update_position = flaky  # type: ignore[method-assign]
    b.refresh_result = BrokerUpdate(OrderStatus.FILLED, "X1", (fill("t1", "60000", "0.1"),))
    with pytest.raises(RuntimeError):
        await om.refresh_open()
    with transaction(factory) as s:
        assert s.scalar(select(func.count()).select_from(models.Trade)) == 0
        assert s.scalar(select(models.Position)) is None
    assert balances(factory) == before
    await om.refresh_open()
    with transaction(factory) as s:
        assert s.scalar(select(func.count()).select_from(models.Trade)) == 1
    assert order(factory, oid).status == "filled"


def order_ids(f: sessionmaker[Session]) -> list[int]:
    with transaction(f) as s:
        return list(s.scalars(select(models.Order.id).order_by(models.Order.id)))


# ---------------------------------------------------------------- Phase A3: live ledger
def _live_portfolio(f: sessionmaker[Session], reported_free: D, age_min: int = 0) -> int:
    with transaction(f) as s:
        uid = s.scalar(select(models.User.id))
        p = models.Portfolio(user_id=uid, name="main", mode="live")
        s.add(p)
        s.flush()
        s.add(models.Balance(portfolio_id=p.id, mode="live",
                             exchange_id=repo.exchange_id(s, "binance"), asset="USDT",
                             free=reported_free, locked=D(0), source="exchange",
                             synced_at=utcnow() - timedelta(minutes=age_min)))
        return int(p.id)


def test_live_ledger_funding_is_bounded_by_the_exchange_report(
        factory: sessionmaker[Session]) -> None:
    from nexus_execution.order_manager import fund_live_ledger

    live = _live_portfolio(factory, D(100))
    with transaction(factory) as s:
        ex = repo.exchange_id(s, "binance")
        assert fund_live_ledger(s, live, ex, "USDT", D(60)) == D(60)
    with transaction(factory) as s, pytest.raises(ValueError, match="at most 40"):
        fund_live_ledger(s, live, ex, "USDT", D(50))
    with transaction(factory) as s:
        assert fund_live_ledger(s, live, ex, "USDT", D(40)) == D(100)
    with transaction(factory) as s, pytest.raises(PermissionError):
        fund_live_ledger(s, pid(factory), ex, "USDT", D(1))      # a paper portfolio
    with transaction(factory) as s, pytest.raises(ValueError, match="positive"):
        fund_live_ledger(s, live, ex, "USDT", D(0))


def test_live_ledger_funding_refuses_a_stale_report(factory: sessionmaker[Session]) -> None:
    from nexus_execution.order_manager import fund_live_ledger

    live = _live_portfolio(factory, D(100), age_min=30)
    with transaction(factory) as s, pytest.raises(ValueError, match="no recent"):
        fund_live_ledger(s, live, repo.exchange_id(s, "binance"), "USDT", D(10))


# ------------------------------------------------------------------ Phase F1 order types
async def test_paper_stop_limit_protective_exit_lifecycle(
        factory: sessionmaker[Session]) -> None:
    """Entry fills; a protective sell stop-limit rests untriggered; the market gaps through
    the stop (triggered, nothing fills); a NEW order manager (restart) still knows it was
    triggered, and it fills at its limit once the price comes back."""
    from datetime import UTC, datetime

    from nexus_exchange.models import OrderBook, OrderType
    from nexus_execution.brokers import PaperBroker

    state = {"bid": D(60_000), "ask": D("60001")}

    async def books(exchange: str, symbol: str) -> OrderBook:
        return OrderBook(symbol, ((state["bid"], D(5)),), ((state["ask"], D(5)),),
                         datetime.now(UTC))

    def broker() -> PaperBroker:
        return PaperBroker(books, taker_fee=D("0.001"), maker_fee=D("0.001"),
                           extra_slippage=D(0))

    om = OrderManager(factory, broker())
    entry = await om.place(intent(factory, price=D("60001")))
    assert order(factory, entry).status == "filled"
    with transaction(factory) as s:
        pos_id = s.scalar(select(models.Position.id))
    stop = await om.place(intent(factory, side=Side.SELL, amount=D("0.1"), price=D(58_900),
                                 stop_price=D(59_000), order_type=OrderType.STOP_LIMIT,
                                 purpose="exit", position_id=pos_id,
                                 details={"exit_reason": "stop_loss"}))
    o = order(factory, stop)
    assert (o.status, o.order_type, o.stop_price) == ("open", "stop_limit", D(59_000))
    assert balances(factory)["BTC"][1] == D("0.1")           # base locked for the stop

    state.update(bid=D(58_000), ask=D("58010"))              # gap below the limit
    await om.refresh_open()
    o = order(factory, stop)
    assert o.status == "open" and o.details.get("triggered") is True and o.filled_amount == 0

    state.update(bid=D(58_950), ask=D(58_960))               # back above the limit
    await OrderManager(factory, broker()).refresh_open()     # fresh process: state from DB
    o = order(factory, stop)
    assert o.status == "filled" and o.average_fill_price == D(58_900)
    with transaction(factory) as s:
        pos = s.get(models.Position, pos_id)
        assert pos is not None and pos.status == "closed" and pos.exit_reason == "stop_loss"


async def test_paper_post_only_that_would_take_is_rejected(
        factory: sessionmaker[Session]) -> None:
    from datetime import UTC, datetime

    from nexus_exchange.models import OrderBook
    from nexus_execution.brokers import PaperBroker

    async def books(exchange: str, symbol: str) -> OrderBook:
        return OrderBook(symbol, ((D(59_999), D(5)),), ((D(60_000), D(5)),), datetime.now(UTC))

    om = OrderManager(factory, PaperBroker(books, taker_fee=D("0.001"), maker_fee=D("0.001"),
                                           extra_slippage=D(0)))
    oid = await om.place(intent(factory, price=D(60_000), post_only=True))
    o = order(factory, oid)
    assert o.status == "rejected" and "post-only" in (o.last_error or "")
    assert balances(factory)["USDT"] == (D(10_000), D(0))   # reserve released
    oid = await om.place(intent(factory, price=D(59_990), post_only=True))
    o = order(factory, oid)
    assert o.status == "open" and o.details.get("post_only") is True


def test_stop_limit_row_needs_a_stop_price(factory: sessionmaker[Session]) -> None:
    from sqlalchemy.exc import IntegrityError

    with pytest.raises(IntegrityError, match="stop_limit_complete"), transaction(factory) as s:
        pair = s.scalar(select(models.TradingPair))
        assert pair is not None
        s.add(models.Order(portfolio_id=pid(factory), mode="paper", exchange_id=pair.exchange_id,
                           pair_id=pair.id, client_order_id="00000000-0000-4000-8000-000000000001",
                           side="sell", order_type="stop_limit", price=D(1), amount=D(1),
                           status="open", purpose="exit"))
        s.flush()
