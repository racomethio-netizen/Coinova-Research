"""Brokers translate order intents into fills: ``LiveBroker`` (real exchange via the
adapter) and ``PaperBroker`` (simulated against real order books). The order manager
treats both identically, so paper trading exercises the same lifecycle code as live.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import Protocol

from nexus_common.decimals import ZERO
from nexus_common.timeutil import utcnow
from nexus_exchange.base import ExchangeAdapter
from nexus_exchange.models import OrderBook, OrderRequest, OrderSnapshot, OrderStatus, Side

from nexus_execution.paper import SimResult, simulate_resting, simulate_submit


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


class Broker(Protocol):
    mode: str

    async def submit(self, req: OrderRequest, *, exchange: str) -> BrokerUpdate: ...

    async def refresh(self, *, exchange: str, symbol: str, side: Side, price: Decimal,
                      amount: Decimal, filled: Decimal, exchange_order_id: str | None,
                      since: datetime | None) -> BrokerUpdate: ...

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
                      since: datetime | None) -> BrokerUpdate:
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
                 extra_slippage: Decimal, quote_asset: str = "USDT") -> None:
        self._books = books
        self.taker_fee, self.maker_fee = taker_fee, maker_fee
        self.extra_slippage = extra_slippage
        self.quote_asset = quote_asset
        self._known: dict[str, BrokerUpdate] = {}   # client id -> last state (process-local)

    async def _book(self, exchange: str, symbol: str) -> OrderBook:
        book = await self._books(exchange, symbol)
        if book is None or book.best_bid is None or book.best_ask is None:
            # No fresh real prices: refuse rather than invent a fill.
            from nexus_exchange.errors import ExchangeUnavailable
            raise ExchangeUnavailable(f"no fresh order book for {exchange} {symbol}")
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
        return BrokerUpdate(status, order_id, fills)

    async def submit(self, req: OrderRequest, *, exchange: str) -> BrokerUpdate:
        book = await self._book(exchange, req.symbol)
        sim = simulate_submit(req, book, taker_fee=self.taker_fee,
                              extra_slippage=self.extra_slippage)
        update = self._to_update(sim, ZERO, req.amount, f"paper-{req.client_order_id}")
        self._known[req.client_order_id] = update
        return update

    async def refresh(self, *, exchange: str, symbol: str, side: Side, price: Decimal,
                      amount: Decimal, filled: Decimal, exchange_order_id: str | None,
                      since: datetime | None) -> BrokerUpdate:
        book = await self._book(exchange, symbol)
        sim = simulate_resting(side, price, amount - filled, book, maker_fee=self.maker_fee)
        return self._to_update(sim, filled, amount, exchange_order_id or "paper-unknown")

    async def find(self, client_order_id: str, *, exchange: str, symbol: str,
                   since: datetime | None) -> BrokerUpdate | None:
        return self._known.get(client_order_id)

    async def cancel(self, *, exchange: str, symbol: str, exchange_order_id: str | None) -> None:
        return None
