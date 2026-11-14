"""DB-backed order lifecycle: intent persisted BEFORE submission, exactly one submission,
idempotent fill application, reconciliation of ambiguous orders by client order ID.

State machine (orders.status):
  pending_submit -> submitted/open/partially_filled/filled/canceled/expired | rejected | unknown
  unknown -> (found) exchange state | (not found after settle delay) rejected
Each fill is inserted once (UNIQUE(order_id, exchange_trade_id)) and updates, in the same
transaction, the internal ledger and the position.
"""

from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass, field
from datetime import timedelta
from decimal import Decimal
from typing import Any

from nexus_common import events
from nexus_common.decimals import ZERO
from nexus_common.logging import get_logger
from nexus_common.timeutil import utcnow
from nexus_db import models, repo, transaction
from nexus_exchange.errors import ExchangeError, OrderStateUnknown
from nexus_exchange.models import OrderRequest, OrderStatus, Side, TimeInForce
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from nexus_execution.accounting import FillEvent, Ledger, PositionState, apply_fill
from nexus_execution.brokers import Broker, BrokerUpdate

log = get_logger("nexus.execution.orders")

ACTIVE = ("pending_submit", "submitted", "open", "partially_filled", "unknown")
REFRESHABLE = ("submitted", "open", "partially_filled")
STATUS_MAP = {OrderStatus.OPEN: "open", OrderStatus.PARTIALLY_FILLED: "partially_filled",
              OrderStatus.FILLED: "filled", OrderStatus.CANCELED: "canceled",
              OrderStatus.REJECTED: "rejected", OrderStatus.EXPIRED: "expired"}
TERMINAL = {"filled", "canceled", "rejected", "expired"}


class DuplicateOrder(Exception):
    pass


class InsufficientBalance(Exception):
    pass


@dataclass(frozen=True)
class OrderIntent:
    portfolio_id: int
    mode: str
    exchange: str
    symbol: str
    side: Side
    amount: Decimal                # already rounded to exchange precision
    price: Decimal                 # limit price, already rounded
    purpose: str                   # entry | exit
    expected_price: Decimal | None = None
    time_in_force: TimeInForce = TimeInForce.GTC
    signal_id: int | None = None
    position_id: int | None = None
    fee_buffer: Decimal = Decimal("0.003")   # reserve quote for fees on buys
    details: dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------- ledger rows
def _balance_row(s: Session, portfolio_id: int, mode: str, exchange_id: int,
                 asset: str) -> models.Balance:
    row = s.scalar(select(models.Balance).where(
        models.Balance.portfolio_id == portfolio_id, models.Balance.exchange_id == exchange_id,
        models.Balance.asset == asset, models.Balance.source == "internal").with_for_update())
    if row is None:
        row = models.Balance(portfolio_id=portfolio_id, mode=mode, exchange_id=exchange_id,
                             asset=asset, free=ZERO, locked=ZERO, source="internal",
                             synced_at=utcnow())
        s.add(row)
        s.flush()
    return row


def _ledger(rows: dict[str, models.Balance]) -> Ledger:
    return Ledger({a: r.free for a, r in rows.items()}, {a: r.locked for a, r in rows.items()})


def _store_ledger(rows: dict[str, models.Balance], ledger: Ledger) -> None:
    now = utcnow()
    for asset, row in rows.items():
        row.free, row.locked, row.synced_at = ledger.free[asset], ledger.locked[asset], now


def fund_paper_portfolio(s: Session, portfolio_id: int, exchange_id: int, asset: str,
                         amount: Decimal) -> None:
    """Credit virtual capital to a PAPER portfolio's internal ledger (never live)."""
    p = s.get(models.Portfolio, portfolio_id)
    if p is None or p.mode != "paper":
        raise PermissionError("only paper portfolios can be funded virtually")
    row = _balance_row(s, portfolio_id, "paper", exchange_id, asset)
    row.free += amount


# ---------------------------------------------------------------------- manager
class OrderManager:
    def __init__(self, factory: sessionmaker[Session], broker: Broker, *, redis: Any = None,
                 settle_seconds: int = 60) -> None:
        self.factory, self.broker, self.redis = factory, broker, redis
        self.settle = timedelta(seconds=settle_seconds)

    # ------------------------------------------------------------------ place
    async def place(self, intent: OrderIntent) -> int:
        if intent.mode != self.broker.mode:
            raise PermissionError(f"{intent.mode} intent cannot use the {self.broker.mode} broker")
        order_id, req = await asyncio.to_thread(self._persist_intent, intent)
        try:
            update = await self.broker.submit(req, exchange=intent.exchange)
        except OrderStateUnknown as exc:
            await asyncio.to_thread(self._mark_unknown, order_id, str(exc))
            await self._notify({"type": "order_state_unknown", "order_id": order_id,
                                "client_order_id": req.client_order_id, "mode": intent.mode})
            return order_id
        except ExchangeError as exc:
            # Definitive: the order was refused, or never left (pre-submission failure).
            await asyncio.to_thread(self._reject, order_id, f"{type(exc).__name__}: {exc}")
            return order_id
        await asyncio.to_thread(self._apply, order_id, update, True)
        return order_id

    def _persist_intent(self, intent: OrderIntent) -> tuple[int, OrderRequest]:
        cid = str(uuid.uuid4())
        req = OrderRequest(client_order_id=cid, symbol=intent.symbol, side=intent.side,
                           amount=intent.amount, price=intent.price,
                           time_in_force=intent.time_in_force)
        try:
            with transaction(self.factory) as s:
                pair = s.scalar(select(models.TradingPair).join(models.Exchange).where(
                    models.Exchange.code == intent.exchange,
                    models.TradingPair.symbol == intent.symbol))
                if pair is None:
                    raise ValueError(f"{intent.symbol} not synced on {intent.exchange}")
                active = s.scalar(select(models.Order.id).where(
                    models.Order.portfolio_id == intent.portfolio_id,
                    models.Order.pair_id == pair.id, models.Order.side == intent.side.value,
                    models.Order.status.in_(ACTIVE)))
                if active is not None:  # (the unique index below also guards races)
                    raise DuplicateOrder(f"order {active} is already active for "
                                         f"{intent.side.value} {intent.symbol}")
                assets = (pair.quote_asset, pair.base_asset)
                rows = {a: _balance_row(s, intent.portfolio_id, intent.mode, pair.exchange_id, a)
                        for a in assets}
                if intent.side is Side.BUY:
                    reserve_asset = pair.quote_asset
                    reserve = intent.amount * intent.price * (1 + intent.fee_buffer)
                else:
                    reserve_asset, reserve = pair.base_asset, intent.amount
                try:
                    ledger = _ledger(rows).lock(reserve_asset, reserve)
                except ValueError as exc:
                    raise InsufficientBalance(str(exc)) from exc
                _store_ledger(rows, ledger)
                order = models.Order(
                    portfolio_id=intent.portfolio_id, mode=intent.mode,
                    exchange_id=pair.exchange_id, pair_id=pair.id, signal_id=intent.signal_id,
                    client_order_id=cid, side=intent.side.value, order_type="limit",
                    time_in_force=intent.time_in_force.value, price=intent.price,
                    amount=intent.amount, expected_price=intent.expected_price,
                    status="pending_submit", purpose=intent.purpose,
                    position_id=intent.position_id,
                    details={**intent.details, "reserve_asset": reserve_asset,
                             "reserved": str(reserve)})
                s.add(order)
                s.flush()
                return order.id, req
        except IntegrityError as exc:
            if "uq_orders_one_active" in str(exc.orig):
                raise DuplicateOrder(f"an active {intent.side.value} order already exists for "
                                     f"{intent.symbol}") from exc
            raise

    # ------------------------------------------------------------------ transitions
    def _release(self, s: Session, order: models.Order) -> None:
        """Unlock whatever is still reserved for a finished order."""
        reserved = Decimal(order.details.get("reserved", "0"))
        used = Decimal(order.details.get("reserve_used", "0"))
        left = reserved - used
        if left > 0:
            asset = order.details["reserve_asset"]
            row = _balance_row(s, order.portfolio_id, order.mode, order.exchange_id, asset)
            ledger = Ledger({asset: row.free}, {asset: row.locked}).unlock(asset, left)
            row.free, row.locked = ledger.free[asset], ledger.locked[asset]
            order.details = {**order.details, "reserve_used": str(reserved)}

    def _reject(self, order_id: int, error: str) -> None:
        with transaction(self.factory) as s:
            order = s.get(models.Order, order_id, with_for_update=True)
            assert order is not None
            order.status, order.last_error, order.closed_at = "rejected", error[:2000], utcnow()
            self._release(s, order)
        log.warning("order_rejected", order_id=order_id, error=error[:300])

    def _mark_unknown(self, order_id: int, error: str) -> None:
        with transaction(self.factory) as s:
            order = s.get(models.Order, order_id, with_for_update=True)
            assert order is not None
            order.status, order.last_error, order.submitted_at = "unknown", error[:2000], utcnow()
            repo.record_risk_event(s, mode=order.mode, portfolio_id=order.portfolio_id,
                                   event_type="order_state_unknown", severity="error",
                                   details={"order_id": order_id,
                                            "client_order_id": order.client_order_id})
        log.error("order_state_unknown", order_id=order_id)

    def _apply(self, order_id: int, update: BrokerUpdate, first: bool) -> None:
        with transaction(self.factory) as s:
            order = s.get(models.Order, order_id, with_for_update=True)
            assert order is not None
            pair = s.get(models.TradingPair, order.pair_id)
            assert pair is not None
            if first:
                order.submitted_at = utcnow()
            if update.exchange_order_id:
                order.exchange_order_id = update.exchange_order_id
            known = set(s.scalars(select(models.Trade.exchange_trade_id).where(
                models.Trade.order_id == order.id)).all())
            new = [f for f in update.fills if f.trade_id not in known]
            if new:
                self._apply_fills(s, order, pair, new)
            filled = s.scalar(select(func.coalesce(func.sum(models.Trade.amount), 0)).where(
                models.Trade.order_id == order.id)) or ZERO
            status = STATUS_MAP.get(update.status, "open")
            if status == "filled" and filled < order.amount:
                status = "partially_filled"  # exchange says filled; wait for the fills
            if status in ("canceled", "expired") and filled >= order.amount:
                status = "filled"
            order.status = status
            order.last_checked_at = utcnow()
            if status in TERMINAL:
                order.closed_at = order.closed_at or utcnow()
                self._release(s, order)

    def _apply_fills(self, s: Session, order: models.Order, pair: models.TradingPair,
                     fills: list[Any]) -> None:
        base, quote = pair.base_asset, pair.quote_asset
        rows = {a: _balance_row(s, order.portfolio_id, order.mode, order.exchange_id, a)
                for a in {base, quote} | {f.fee_asset for f in fills if f.fee_asset}}
        ledger = _ledger(rows)
        position = s.get(models.Position, order.position_id, with_for_update=True) \
            if order.position_id else None
        used = Decimal(order.details.get("reserve_used", "0"))
        for f in fills:
            ev = FillEvent(order.side, f.price, f.amount, f.fee, f.fee_asset)
            ledger = ledger.settle_fill(ev, base=base, quote=quote, from_locked=True)
            used += (f.price * f.amount + (f.fee if f.fee_asset in (None, quote) else ZERO)) \
                if order.side == "buy" else f.amount
            s.add(models.Trade(order_id=order.id, mode=order.mode, exchange_trade_id=f.trade_id,
                               price=f.price, amount=f.amount, fee=f.fee, fee_asset=f.fee_asset,
                               liquidity="unknown" if f.is_maker is None
                               else ("maker" if f.is_maker else "taker"),
                               executed_at=f.timestamp))
            position = self._update_position(s, order, pair, position, ev)
        _store_ledger(rows, ledger)
        order.details = {**order.details, "reserve_used": str(used)}
        order.fee_asset = order.fee_asset or next((f.fee_asset for f in fills if f.fee_asset), None)
        s.flush()
        # Totals always recomputed from the fills table (no incremental rounding drift).
        amt, notional, fees = s.execute(select(
            func.coalesce(func.sum(models.Trade.amount), 0),
            func.coalesce(func.sum(models.Trade.amount * models.Trade.price), 0),
            func.coalesce(func.sum(models.Trade.fee), 0)).where(
                models.Trade.order_id == order.id)).one()
        order.filled_amount, order.fee_amount = amt, fees
        order.average_fill_price = notional / amt if amt > 0 else None

    def _update_position(self, s: Session, order: models.Order, pair: models.TradingPair,
                         position: models.Position | None, ev: FillEvent) -> models.Position:
        if position is None:
            if order.side != "buy":
                raise ValueError("sell fill without a position (spot: no shorting)")
            position = models.Position(
                portfolio_id=order.portfolio_id, mode=order.mode, exchange_id=order.exchange_id,
                pair_id=order.pair_id, base_asset=pair.base_asset,
                strategy_id=order.details.get("strategy_id"), entry_order_id=order.id,
                status="open", quantity=ZERO, avg_entry_price=ZERO, cost_basis=ZERO,
                stop_loss=_dec(order.details.get("stop")),
                take_profit=_dec(order.details.get("target")), opened_at=utcnow())
            s.add(position)
            s.flush()
            order.position_id = position.id
        state = PositionState(position.quantity, position.cost_basis, position.realized_pnl,
                              position.fees_paid)
        state = apply_fill(state, ev, base=pair.base_asset, quote=pair.quote_asset)
        position.quantity, position.cost_basis = state.quantity, state.cost_basis
        position.realized_pnl, position.fees_paid = state.realized_pnl, state.fees_paid_quote
        if state.quantity > 0:
            position.avg_entry_price = state.avg_entry_price
        if order.side == "sell" and state.quantity == 0:
            position.status, position.closed_at = "closed", utcnow()
            position.exit_reason = order.details.get("exit_reason")
        return position

    # ------------------------------------------------------------------ maintenance
    async def refresh_open(self) -> int:
        rows = await asyncio.to_thread(self._load, REFRESHABLE)
        for r in rows:
            try:
                update = await self.broker.refresh(
                    exchange=r["exchange"], symbol=r["symbol"], side=Side(r["side"]),
                    price=r["price"], amount=r["amount"], filled=r["filled"],
                    exchange_order_id=r["exchange_order_id"], since=r["submitted_at"])
            except ExchangeError as exc:
                log.warning("order_refresh_failed", order_id=r["id"], error=str(exc)[:200])
                continue
            await asyncio.to_thread(self._apply, r["id"], update, False)
        return len(rows)

    async def reconcile_unknown(self) -> int:
        rows = await asyncio.to_thread(self._load, ("unknown",))
        resolved = 0
        for r in rows:
            try:
                found = await self.broker.find(r["client_order_id"], exchange=r["exchange"],
                                               symbol=r["symbol"], since=r["created_at"])
            except ExchangeError as exc:
                log.error("reconcile_lookup_failed", order_id=r["id"], error=str(exc)[:200])
                continue  # stays unknown; risk engine keeps blocking new orders
            if found is not None:
                await asyncio.to_thread(self._apply, r["id"], found, False)
                resolved += 1
            elif utcnow() - r["created_at"] > self.settle:
                await asyncio.to_thread(self._reject, r["id"],
                                        "not found at exchange by client order id after timeout")
                resolved += 1
        return resolved

    async def cancel(self, order_id: int, reason: str) -> None:
        rows = [r for r in await asyncio.to_thread(self._load, REFRESHABLE) if r["id"] == order_id]
        if not rows:
            return
        r = rows[0]
        await self.broker.cancel(exchange=r["exchange"], symbol=r["symbol"],
                                 exchange_order_id=r["exchange_order_id"])
        if self.broker.mode == "paper":
            await asyncio.to_thread(self._apply, order_id,
                                    BrokerUpdate(OrderStatus.CANCELED, None), False)
        else:
            await self.refresh_open()
        log.info("order_cancel_requested", order_id=order_id, reason=reason)

    async def cancel_stale(self, older_than: timedelta) -> int:
        rows = await asyncio.to_thread(self._load, REFRESHABLE)
        cutoff = utcnow() - older_than
        stale = [r for r in rows if r["purpose"] == "entry" and r["created_at"] < cutoff]
        for r in stale:
            await self.cancel(r["id"], "stale entry order")
        return len(stale)

    def _load(self, statuses: tuple[str, ...]) -> list[dict[str, Any]]:
        with transaction(self.factory) as s:
            q = (select(models.Order, models.Exchange.code, models.TradingPair.symbol)
                 .join(models.Exchange, models.Exchange.id == models.Order.exchange_id)
                 .join(models.TradingPair, models.TradingPair.id == models.Order.pair_id)
                 .where(models.Order.status.in_(statuses),
                        models.Order.mode == self.broker.mode))
            return [{"id": o.id, "exchange": code, "symbol": sym, "side": o.side,
                     "price": o.price, "amount": o.amount, "filled": o.filled_amount,
                     "exchange_order_id": o.exchange_order_id, "submitted_at": o.submitted_at,
                     "created_at": o.created_at, "client_order_id": o.client_order_id,
                     "purpose": o.purpose} for o, code, sym in s.execute(q).all()]

    async def _notify(self, payload: dict[str, Any]) -> None:
        if self.redis is not None:
            await self.redis.xadd(events.NOTIFY_STREAM, events.encode(payload),
                                  maxlen=events.STREAM_MAXLEN, approximate=True)


def _dec(v: Any) -> Decimal | None:
    return None if v is None else Decimal(str(v))

