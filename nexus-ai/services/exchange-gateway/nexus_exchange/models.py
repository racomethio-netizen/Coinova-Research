"""Exchange-neutral data types. All prices/amounts are ``Decimal``; all times UTC."""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from enum import StrEnum

from nexus_common.timeutil import ensure_utc

_UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$")


def new_client_order_id() -> str:
    """UUIDv4 string: accepted by Binance (<=36 chars), Kraken cl_ord_id and Coinbase."""
    return str(uuid.uuid4())


class Side(StrEnum):
    BUY = "buy"
    SELL = "sell"


class OrderType(StrEnum):
    LIMIT = "limit"
    MARKET = "market"


class TimeInForce(StrEnum):
    GTC = "GTC"
    IOC = "IOC"


class OrderStatus(StrEnum):
    OPEN = "open"
    PARTIALLY_FILLED = "partially_filled"
    FILLED = "filled"
    CANCELED = "canceled"
    REJECTED = "rejected"
    EXPIRED = "expired"

    @property
    def is_terminal(self) -> bool:
        return self in {OrderStatus.FILLED, OrderStatus.CANCELED, OrderStatus.REJECTED,
                        OrderStatus.EXPIRED}


@dataclass(frozen=True)
class MarketRules:
    """Precision and limits for one spot market, as reported by the exchange."""

    symbol: str            # unified, e.g. BTC/USDT
    exchange_symbol: str   # native id
    base: str
    quote: str
    price_tick: Decimal
    amount_step: Decimal
    min_amount: Decimal | None = None
    max_amount: Decimal | None = None
    min_notional: Decimal | None = None
    maker_fee: Decimal | None = None
    taker_fee: Decimal | None = None
    active: bool = True


@dataclass(frozen=True)
class Ticker:
    symbol: str
    bid: Decimal | None
    ask: Decimal | None
    last: Decimal | None
    timestamp: datetime


@dataclass(frozen=True)
class OrderBook:
    symbol: str
    bids: tuple[tuple[Decimal, Decimal], ...]  # (price, amount), best first
    asks: tuple[tuple[Decimal, Decimal], ...]
    timestamp: datetime

    @property
    def best_bid(self) -> Decimal | None:
        return self.bids[0][0] if self.bids else None

    @property
    def best_ask(self) -> Decimal | None:
        return self.asks[0][0] if self.asks else None

    @property
    def mid(self) -> Decimal | None:
        if self.best_bid is None or self.best_ask is None:
            return None
        return (self.best_bid + self.best_ask) / 2

    @property
    def spread_bps(self) -> Decimal | None:
        mid = self.mid
        if mid is None or mid == 0 or self.best_ask is None or self.best_bid is None:
            return None
        return (self.best_ask - self.best_bid) / mid * Decimal(10_000)

    @property
    def is_crossed(self) -> bool:
        return (self.best_bid is not None and self.best_ask is not None
                and self.best_bid >= self.best_ask)


@dataclass(frozen=True)
class Candle:
    open_time: datetime
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: Decimal

    def __post_init__(self) -> None:
        ensure_utc(self.open_time)
        if not (self.low <= min(self.open, self.close) and self.high >= max(self.open, self.close)):
            raise ValueError(f"inconsistent OHLC at {self.open_time.isoformat()}")
        if self.volume < 0:
            raise ValueError("negative volume")


@dataclass(frozen=True)
class AssetBalance:
    asset: str
    free: Decimal
    locked: Decimal

    @property
    def total(self) -> Decimal:
        return self.free + self.locked


@dataclass(frozen=True)
class OrderRequest:
    client_order_id: str
    symbol: str
    side: Side
    amount: Decimal
    price: Decimal | None
    order_type: OrderType = OrderType.LIMIT
    time_in_force: TimeInForce = TimeInForce.GTC
    post_only: bool = False

    def __post_init__(self) -> None:
        if not _UUID_RE.match(self.client_order_id):
            raise ValueError("client_order_id must be a lowercase UUIDv4 string")
        if self.amount <= 0:
            raise ValueError("amount must be > 0")
        if self.order_type is OrderType.LIMIT and (self.price is None or self.price <= 0):
            raise ValueError("limit orders require a positive price")
        if self.order_type is OrderType.MARKET and self.post_only:
            raise ValueError("market orders cannot be post-only")


@dataclass(frozen=True)
class OrderSnapshot:
    """The exchange's view of an order at a moment in time."""

    exchange_order_id: str
    client_order_id: str | None
    symbol: str
    side: Side
    order_type: OrderType
    status: OrderStatus
    price: Decimal | None
    amount: Decimal
    filled: Decimal
    average_price: Decimal | None
    fee_cost: Decimal | None
    fee_asset: str | None
    timestamp: datetime | None
    raw_status: str | None = None

    @property
    def remaining(self) -> Decimal:
        return self.amount - self.filled


@dataclass(frozen=True)
class Fill:
    trade_id: str
    exchange_order_id: str
    symbol: str
    side: Side
    price: Decimal
    amount: Decimal
    fee_cost: Decimal
    fee_asset: str | None
    is_maker: bool | None
    timestamp: datetime


@dataclass(frozen=True)
class HealthStatus:
    exchange: str
    ok: bool
    latency_ms: int | None
    checked_at: datetime
    detail: str | None = None


@dataclass(frozen=True)
class Capabilities:
    """Honest declaration of what an exchange/adapter supports.

    ``client_order_lookup``:
      * ``"native"``: the exchange can fetch an order by our client order ID.
      * ``"scan"``: no direct lookup; we search open + recent closed orders.
    ``withdrawal_permission_check``:
      * ``"api"``: key permissions can be read via API and verified automatically.
      * ``"manual"``: the owner must confirm withdrawals are disabled for the key.
    """

    exchange: str
    client_order_lookup: str
    sandbox_available: bool
    websocket_market_data: bool
    post_only: bool
    withdrawal_permission_check: str
    notes: tuple[str, ...] = field(default_factory=tuple)
