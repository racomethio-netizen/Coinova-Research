"""Brokers translate order intents into fills: ``LiveBroker`` (real exchange via the
adapter) and ``PaperBroker`` (simulated against real order books). The order manager
treats both identically, so paper trading exercises the same lifecycle code as live.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import Any, Protocol

from nexus_common.decimals import ZERO
from nexus_common.timeutil import utcnow
from nexus_exchange.base import ExchangeAdapter
from nexus_exchange.models import (
    OrderBook,
    OrderRequest,
    OrderSnapshot,
    OrderStatus,
    OrderType,
    Side,
)

from nexus_execution.paper import SimResult, simulate_resting, simulate_stop, simulate_submit


@dataclass(frozen=True)
class BrokerFill:
    trade_id: str
    price: Decimal
    amount: Decimal
    fee: Decimal
    fee_asset: str | None
    is_maker: bool | None
    timestamp: datetime


@dataclass(frozen=True)
class BrokerUpdate:
    status: OrderStatus
    exchange_order_id: str | None
    fills: tuple[BrokerFill, ...] = field(default_factory=tuple)  # may repeat known fills
    triggered: bool | None = None   # stop-limit: True once the stop was reached (paper)


class Broker(Protocol):
    mode: str

    async def submit(self, req: OrderRequest, *, exchange: str) -> BrokerUpdate: ...

    async def refresh(self, *, exchange: str, symbol: str, side: Side, price: Decimal,
                      amount: Decimal, filled: Decimal, exchange_order_id: str | None,
                      since: datetime | None, order_type: OrderType = OrderType.LIMIT,
                      stop_price: Decimal | None = None,
                      triggered: bool = False) -> BrokerUpdate: ...

    async def find(self, client_order_id: str, *, exchange: str, symbol: str,
                   since: datetime | None) -> BrokerUpdate | None: ...

    async def cancel(self, *, exchange: str, symbol: str,
                     exchange_order_id: str | None) -> None: ...


# ---------------------------------------------------------------------- live
class LiveBroker:
    """Real orders. Construct only through ``live_gate.build_live_broker`` which checks
    every activation gate first."""

    mode = "live"

    def __init__(self, adapters: dict[str, ExchangeAdapter], *, _gate_token: object) -> None:
        from nexus_execution.live_gate import GATE_TOKEN

        if _gate_token is not GATE_TOKEN:
            raise PermissionError("LiveBroker must be created via live_gate.build_live_broker")
        self._adapters = adapters

    async def _fills(self, adapter: ExchangeAdapter, symbol: str, order_id: str,
                     since: datetime | None) -> tuple[BrokerFill, ...]:
        rows = await adapter.fetch_fills(symbol, since=since, limit=200)
        return tuple(BrokerFill(f.trade_id, f.price, f.amount, f.fee_cost, f.fee_asset,
                                f.is_maker, f.timestamp)
                     for f in rows if f.exchange_order_id == order_id)

    async def _update(self, adapter: ExchangeAdapter, snap: OrderSnapshot,
                      since: datetime | None) -> BrokerUpdate:
        fills = await self._fills(adapter, snap.symbol, snap.exchange_order_id, since) \
            if snap.filled > 0 else ()
        return BrokerUpdate(snap.status, snap.exchange_order_id, fills)

    async def submit(self, req: OrderRequest, *, exchange: str) -> BrokerUpdate:
        adapter = self._adapters[exchange]
        snap = await adapter.submit_order(req)
        return await self._update(adapter, snap, None)

    async def refresh(self, *, exchange: str, symbol: str, side: Side, price: Decimal,
                      amount: Decimal, filled: Decimal, exchange_order_id: str | None,
                      since: datetime | None, order_type: OrderType = OrderType.LIMIT,
                      stop_price: Decimal | None = None,
                      triggered: bool = False) -> BrokerUpdate:
        # The exchange tracks triggers itself; the order type and stop are not needed.
        if exchange_order_id is None:
            raise ValueError("cannot refresh an order without an exchange id")
        adapter = self._adapters[exchange]
        return await self._update(adapter, await adapter.fetch_order(exchange_order_id, symbol),
                                  since)

    async def find(self, client_order_id: str, *, exchange: str, symbol: str,
                   since: datetime | None) -> BrokerUpdate | None:
        adapter = self._adapters[exchange]
        snap = await adapter.find_order_by_client_id(client_order_id, symbol)
        return None if snap is None else await self._update(adapter, snap, since)

    async def cancel(self, *, exchange: str, symbol: str, exchange_order_id: str | None) -> None:
        if exchange_order_id is not None:
            await self._adapters[exchange].cancel_order(exchange_order_id, symbol)


# ---------------------------------------------------------------------- paper
BookSource = Callable[[str, str], Awaitable[OrderBook | None]]


class PaperBroker:
    mode = "paper"

    def __init__(self, books: BookSource, *, taker_fee: Decimal, maker_fee: Decimal,
                 extra_slippage: Decimal, quote_asset: str = "USDT",
                 latency_ms: int = 0, participation: Decimal = Decimal(1),
                 max_book_age_s: float | None = None, outage_rate: float = 0.0,
                 rng: Any = None) -> None:
        import random

        self._books = books
        self.taker_fee, self.maker_fee = taker_fee, maker_fee
        self.extra_slippage = extra_slippage
        self.quote_asset = quote_asset
        self.latency_ms, self.participation = latency_ms, participation
        self.max_book_age_s, self.outage_rate = max_book_age_s, outage_rate
        self._rng = rng or random.Random()  # noqa: S311  (simulation only, not security)
        self._known: dict[str, BrokerUpdate] = {}   # client id -> last state (process-local)

    async def _book(self, exchange: str, symbol: str) -> OrderBook:
        from nexus_exchange.errors import ExchangeUnavailable

        book = await self._books(exchange, symbol)
        if book is None or book.best_bid is None or book.best_ask is None:
            # No fresh real prices: refuse rather than invent a fill.
            raise ExchangeUnavailable(f"no fresh order book for {exchange} {symbol}")
        if self.max_book_age_s is not None and book.timestamp is not None:
            age = (utcnow() - book.timestamp).total_seconds()
            if age > self.max_book_age_s:
                raise ExchangeUnavailable(f"order book {age:.0f}s old (simulated outage: "
                                          f"no fill on stale prices)")
        return book

    def _to_update(self, sim: SimResult, prior_filled: Decimal, amount: Decimal,
                   order_id: str) -> BrokerUpdate:
        filled = prior_filled + sum((f.amount for f in sim.fills), ZERO)
        if filled >= amount:
            status = OrderStatus.FILLED
        elif sim.resting:
            status = OrderStatus.PARTIALLY_FILLED if filled > 0 else OrderStatus.OPEN
        else:
            status = OrderStatus.CANCELED  # IOC remainder (fills, if any, are kept)
        now = utcnow()
        fills = tuple(BrokerFill(f.trade_id, f.price, f.amount, f.fee, self.quote_asset,
                                 f.is_maker, now) for f in sim.fills)
        return BrokerUpdate(status, order_id, fills, triggered=sim.triggered)

    async def submit(self, req: OrderRequest, *, exchange: str) -> BrokerUpdate:
        import asyncio

        from nexus_exchange.errors import ExchangeUnavailable, OrderStateUnknown

        outage = self.outage_rate > 0 and self._rng.random() < self.outage_rate
        if outage and self._rng.random() < 0.5:
            raise ExchangeUnavailable("simulated exchange outage: order refused")
        if self.latency_ms:
            await asyncio.sleep(self.latency_ms / 1000)    # the market moves meanwhile
        book = await self._book(exchange, req.symbol)
        sim = simulate_submit(req, book, taker_fee=self.taker_fee,
                              extra_slippage=self.extra_slippage,
                              participation=self.participation)
        update = self._to_update(sim, ZERO, req.amount, f"paper-{req.client_order_id}")
        self._known[req.client_order_id] = update
        if outage:   # it executed, but the reply was lost: must be reconciled, not resent
            raise OrderStateUnknown("simulated timeout after sending",
                                    client_order_id=req.client_order_id)
        return update

    async def refresh(self, *, exchange: str, symbol: str, side: Side, price: Decimal,
                      amount: Decimal, filled: Decimal, exchange_order_id: str | None,
                      since: datetime | None, order_type: OrderType = OrderType.LIMIT,
                      stop_price: Decimal | None = None,
                      triggered: bool = False) -> BrokerUpdate:
        book = await self._book(exchange, symbol)
        if order_type is OrderType.STOP_LIMIT and not triggered:
            if stop_price is None:
                raise ValueError("stop-limit order without a stop price")
            sim = simulate_stop(side, stop_price, price, amount - filled, book,
                                taker_fee=self.taker_fee, extra_slippage=self.extra_slippage,
                                participation=self.participation)
        else:
            sim = simulate_resting(side, price, amount - filled, book, maker_fee=self.maker_fee)
            if order_type is OrderType.STOP_LIMIT:
                sim = SimResult(sim.fills, sim.remaining, sim.resting, triggered=True)
        return self._to_update(sim, filled, amount, exchange_order_id or "paper-unknown")

    async def find(self, client_order_id: str, *, exchange: str, symbol: str,
                   since: datetime | None) -> BrokerUpdate | None:
        return self._known.get(client_order_id)

    async def cancel(self, *, exchange: str, symbol: str, exchange_order_id: str | None) -> None:
        return None
