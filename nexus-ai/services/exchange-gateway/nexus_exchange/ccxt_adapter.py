"""CCXT-backed spot adapter shared by Binance, Kraken and Coinbase Advanced Trade.

Conversions: CCXT returns floats; every number goes through ``to_decimal`` (via
``repr``) so values are not polluted by binary float expansion. Outgoing amounts are
already quantized ``Decimal``s; we refuse to send any value whose float form would
not round-trip exactly.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable
from dataclasses import replace
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any, TypeVar

import ccxt.async_support as ccxt_async
from ccxt.base import errors as cx
from nexus_common.decimals import to_decimal, to_decimal_or_none
from nexus_common.timeutil import ensure_utc, from_ms, to_ms, utcnow
from nexus_config import Timeframe

from nexus_exchange.base import ExchangeAdapter
from nexus_exchange.errors import (
    AuthenticationFailed,
    ExchangeError,
    ExchangeUnavailable,
    InsufficientFunds,
    InvalidOrder,
    OrderNotFound,
    OrderRejected,
    OrderStateUnknown,
    PermissionDenied,
    RateLimited,
    UnsupportedFeature,
)
from nexus_exchange.models import (
    AssetBalance,
    Candle,
    Capabilities,
    Fill,
    HealthStatus,
    MarketRules,
    OrderBook,
    OrderRequest,
    OrderSnapshot,
    OrderStatus,
    OrderType,
    Side,
    Ticker,
    TimeInForce,
)

T = TypeVar("T")

# ccxt precision modes (ccxt.base.decimal_to_precision)
_DECIMAL_PLACES = 2
_TICK_SIZE = 4

# How far back the "scan" client-order lookup searches closed orders.
SCAN_LOOKBACK = timedelta(hours=48)


def map_ccxt_error(
    exc: BaseException, *, exchange: str, during_submit: bool = False,
    client_order_id: str | None = None,
) -> ExchangeError:
    """Translate a ccxt/asyncio exception into a NEXUS error.

    During submission, any transport-level failure means the order may or may not
    exist, so it becomes OrderStateUnknown. Order matters: ccxt.OrderNotFound is a
    subclass of ccxt.InvalidOrder.
    """
    msg = f"{type(exc).__name__}: {str(exc)[:300]}"

    def unknown() -> ExchangeError:
        assert client_order_id is not None
        return OrderStateUnknown(msg, client_order_id=client_order_id, exchange=exchange)

    if isinstance(exc, cx.RateLimitExceeded | cx.DDoSProtection):
        return unknown() if during_submit else RateLimited(msg, exchange=exchange)
    if isinstance(exc, cx.NetworkError | asyncio.TimeoutError | TimeoutError | ConnectionError):
        # Includes RequestTimeout, ExchangeNotAvailable, OnMaintenance, InvalidNonce.
        return unknown() if during_submit else ExchangeUnavailable(msg, exchange=exchange)
    if isinstance(exc, cx.PermissionDenied | cx.AccountSuspended):
        return PermissionDenied(msg, exchange=exchange)
    if isinstance(exc, cx.AuthenticationError):
        return AuthenticationFailed(msg, exchange=exchange)
    if isinstance(exc, cx.InsufficientFunds):
        return InsufficientFunds(msg, exchange=exchange)
    if isinstance(exc, cx.OrderNotFound):
        return OrderNotFound(msg, exchange=exchange)
    if isinstance(exc, cx.InvalidOrder | cx.BadSymbol | cx.BadRequest | cx.ArgumentsRequired):
        return InvalidOrder(msg, exchange=exchange)
    if isinstance(exc, cx.NotSupported):
        return UnsupportedFeature(msg, exchange=exchange)
    if isinstance(exc, cx.BadResponse | cx.NullResponse):
        # We could not parse the reply: outcome unknown.
        return unknown() if during_submit else ExchangeUnavailable(msg, exchange=exchange)
    if isinstance(exc, cx.ExchangeError):
        # The exchange answered with an error code: the request was processed and refused.
        return OrderRejected(msg, exchange=exchange) if during_submit else ExchangeError(
            msg, exchange=exchange)
    # Anything else (bug, unexpected library error): never assume the order failed.
    return unknown() if during_submit else ExchangeError(msg, exchange=exchange)


def decimal_to_wire(value: Decimal, *, field: str) -> float:
    """CCXT's unified API takes floats. Refuse values that don't round-trip exactly."""
    f = float(value)
    if Decimal(repr(f)) != value:  # numeric comparison; trailing zeros are fine
        raise InvalidOrder(f"{field}={value} cannot be represented exactly for transmission")
    return f


_STATUS_MAP = {
    "open": OrderStatus.OPEN,
    "closed": OrderStatus.FILLED,
    "canceled": OrderStatus.CANCELED,
    "cancelled": OrderStatus.CANCELED,
    "expired": OrderStatus.EXPIRED,
    "rejected": OrderStatus.REJECTED,
}


def snapshot_from_ccxt(o: dict[str, Any], *, fallback: OrderRequest | None = None) -> OrderSnapshot:
    raw_status = o.get("status")
    if raw_status not in _STATUS_MAP:
        raise ExchangeError(f"unrecognized order status {raw_status!r}")
    filled = to_decimal(o.get("filled") or 0, field="filled")
    status = _STATUS_MAP[raw_status]
    if status is OrderStatus.OPEN and filled > 0:
        status = OrderStatus.PARTIALLY_FILLED

    amount_raw = o.get("amount")
    if amount_raw is None:
        if fallback is None:
            raise ExchangeError("order without amount")
        amount = fallback.amount
    else:
        amount = to_decimal(amount_raw, field="amount")

    side_raw = o.get("side") or (fallback.side.value if fallback else None)
    type_raw = o.get("type") or (fallback.order_type.value if fallback else None)
    if side_raw not in ("buy", "sell") or type_raw not in ("limit", "market"):
        raise ExchangeError(f"unsupported order side/type {side_raw!r}/{type_raw!r}")

    fee_cost: Decimal | None = None
    fee_asset: str | None = None
    fees = o.get("fees") or ([o["fee"]] if o.get("fee") else [])
    fees = [f for f in fees if f and f.get("cost") is not None]
    if fees and len({f.get("currency") for f in fees}) == 1:
        fee_cost = sum((to_decimal(f["cost"], field="fee") for f in fees), Decimal(0))
        fee_asset = fees[0].get("currency")

    ts = o.get("timestamp")
    return OrderSnapshot(
        exchange_order_id=str(o["id"]),
        client_order_id=o.get("clientOrderId"),
        symbol=o.get("symbol") or (fallback.symbol if fallback else ""),
        side=Side(side_raw),
        order_type=OrderType(type_raw),
        status=status,
        price=to_decimal_or_none(o.get("price"), field="price"),
        amount=amount,
        filled=filled,
        average_price=to_decimal_or_none(o.get("average"), field="average"),
        fee_cost=fee_cost,
        fee_asset=fee_asset,
        timestamp=from_ms(ts) if ts else None,
        raw_status=raw_status,
    )


def rules_from_ccxt(m: dict[str, Any], precision_mode: int) -> MarketRules | None:
    """Build MarketRules from a ccxt market; None if it is not tradable spot or lacks
    precision data (we never guess precision)."""
    if not m.get("spot") or m.get("type") != "spot" or m.get("active") is False:
        return None
    precision = m.get("precision") or {}
    p_price, p_amount = precision.get("price"), precision.get("amount")
    if p_price is None or p_amount is None:
        return None
    if precision_mode == _TICK_SIZE:
        tick, step = to_decimal(p_price), to_decimal(p_amount)
    elif precision_mode == _DECIMAL_PLACES:
        tick = Decimal(1).scaleb(-int(p_price))
        step = Decimal(1).scaleb(-int(p_amount))
    else:
        return None
    limits = m.get("limits") or {}
    amount_l = limits.get("amount") or {}
    cost_l = limits.get("cost") or {}
    return MarketRules(
        symbol=m["symbol"],
        exchange_symbol=str(m["id"]),
        base=m["base"],
        quote=m["quote"],
        price_tick=tick,
        amount_step=step,
        min_amount=to_decimal_or_none(amount_l.get("min")),
        max_amount=to_decimal_or_none(amount_l.get("max")),
        min_notional=to_decimal_or_none(cost_l.get("min")),
        # NOTE: ccxt market fees are public base-tier fees, not the account's actual tier.
        maker_fee=to_decimal_or_none(m.get("maker")),
        taker_fee=to_decimal_or_none(m.get("taker")),
        active=True,
    )


class CcxtSpotAdapter(ExchangeAdapter):
    def __init__(self, code: str, client: Any, capabilities: Capabilities) -> None:
        super().__init__()
        self.code = code
        self.capabilities = capabilities
        self._client = client
        self.excluded_markets: dict[str, str] = {}

    def __repr__(self) -> str:  # never expose the client (it holds credentials)
        return f"CcxtSpotAdapter(code={self.code!r})"

    async def _call(
        self, fn: Callable[..., Awaitable[T]], *args: Any, during_submit: bool = False,
        client_order_id: str | None = None, **kwargs: Any,
    ) -> T:
        try:
            return await fn(*args, **kwargs)
        except ExchangeError:
            raise
        except asyncio.CancelledError:
            if during_submit and client_order_id:
                raise OrderStateUnknown("submission cancelled in flight",
                                        client_order_id=client_order_id,
                                        exchange=self.code) from None
            raise
        except Exception as exc:
            raise map_ccxt_error(exc, exchange=self.code, during_submit=during_submit,
                                 client_order_id=client_order_id) from exc

    # ---------------------------------------------------------------- metadata
    async def load_markets(self, *, reload: bool = False) -> dict[str, MarketRules]:
        raw = await self._call(self._client.load_markets, reload)
        mode = int(getattr(self._client, "precisionMode", _TICK_SIZE))
        markets: dict[str, MarketRules] = {}
        excluded: dict[str, str] = {}
        for symbol, m in raw.items():
            if not m.get("spot"):
                continue
            try:
                rules = rules_from_ccxt(m, mode)
            except (ValueError, TypeError, KeyError) as exc:
                excluded[symbol] = f"unparseable market metadata: {exc}"
                continue
            if rules is None:
                excluded[symbol] = "inactive or missing precision"
                continue
            markets[symbol] = rules
        self._markets = markets
        self.excluded_markets = excluded
        return markets

    # ---------------------------------------------------------------- market data
    async def fetch_ticker(self, symbol: str) -> Ticker:
        t = await self._call(self._client.fetch_ticker, symbol)
        ts = t.get("timestamp")
        return Ticker(
            symbol=symbol,
            bid=to_decimal_or_none(t.get("bid")),
            ask=to_decimal_or_none(t.get("ask")),
            last=to_decimal_or_none(t.get("last")),
            timestamp=from_ms(ts) if ts else utcnow(),
        )

    async def fetch_order_book(self, symbol: str, *, depth: int = 20) -> OrderBook:
        ob = await self._call(self._client.fetch_order_book, symbol, depth)

        def levels(rows: list[list[Any]]) -> tuple[tuple[Decimal, Decimal], ...]:
            return tuple((to_decimal(r[0]), to_decimal(r[1])) for r in rows[:depth])

        ts = ob.get("timestamp")
        book = OrderBook(symbol=symbol, bids=levels(ob.get("bids") or []),
                         asks=levels(ob.get("asks") or []),
                         timestamp=from_ms(ts) if ts else utcnow())
        if book.is_crossed:
            raise ExchangeError(f"crossed order book for {symbol}", exchange=self.code)
        return book

    async def fetch_closed_candles(
        self, symbol: str, timeframe: Timeframe, *, since: datetime | None = None,
        limit: int = 500, now: datetime | None = None,
    ) -> list[Candle]:
        since_ms = to_ms(since) if since else None
        rows = await self._call(self._client.fetch_ohlcv, symbol, timeframe.value, since_ms, limit)
        cutoff = ensure_utc(now or utcnow())
        candles: list[Candle] = []
        prev: datetime | None = None
        for row in rows:
            open_time = from_ms(row[0])
            if open_time + timedelta(seconds=timeframe.seconds) > cutoff:
                continue  # still forming: excluding prevents look-ahead bias
            if prev is not None and open_time <= prev:
                raise ExchangeError(f"non-monotonic OHLCV for {symbol} {timeframe}",
                                    exchange=self.code)
            if row[1] is None or row[4] is None:
                continue  # exchange reported an empty bar; gap detection handles it
            candles.append(Candle(open_time, to_decimal(row[1]), to_decimal(row[2]),
                                  to_decimal(row[3]), to_decimal(row[4]),
                                  to_decimal(row[5] or 0)))
            prev = open_time
        return candles

    # ---------------------------------------------------------------- account
    async def fetch_balances(self) -> dict[str, AssetBalance]:
        raw = await self._call(self._client.fetch_balance)
        free, used, total = raw.get("free") or {}, raw.get("used") or {}, raw.get("total") or {}
        out: dict[str, AssetBalance] = {}
        for asset in set(free) | set(used) | set(total):
            f, u, t = free.get(asset), used.get(asset), total.get(asset)
            if f is None and u is None and t is None:
                continue
            if f is None or u is None:
                if t is not None and to_decimal(t) == 0:
                    continue
                # Uncertain account state: refuse rather than guess.
                raise ExchangeError(f"incomplete balance for {asset}", exchange=self.code)
            bal = AssetBalance(asset=asset, free=to_decimal(f), locked=to_decimal(u))
            if bal.total != 0:
                out[asset] = bal
        return out

    # ---------------------------------------------------------------- orders
    async def _submit(self, request: OrderRequest) -> OrderSnapshot:
        params: dict[str, Any] = {"clientOrderId": request.client_order_id}
        if request.post_only:
            params["postOnly"] = True
        if request.time_in_force is TimeInForce.IOC:
            params["timeInForce"] = "IOC"
        amount = decimal_to_wire(request.amount, field="amount")
        price = (decimal_to_wire(request.price, field="price")
                 if request.price is not None else None)
        o = await self._call(
            self._client.create_order, request.symbol, request.order_type.value,
            request.side.value, amount, price, params,
            during_submit=True, client_order_id=request.client_order_id,
        )
        if not o or not o.get("id"):
            raise OrderStateUnknown("exchange accepted request without an order id",
                                    client_order_id=request.client_order_id, exchange=self.code)
        if o.get("status") in _STATUS_MAP:
            snap = snapshot_from_ccxt(o, fallback=request)
        else:
            # Some exchanges (e.g. Kraken) acknowledge with only an id. Read it back once;
            # if the read fails, the caller reconciles via client order id.
            try:
                snap = await self.fetch_order(str(o["id"]), request.symbol)
            except ExchangeError as exc:
                raise OrderStateUnknown(f"accepted as {o['id']} but status unreadable: {exc}",
                                        client_order_id=request.client_order_id,
                                        exchange=self.code) from exc
        if snap.client_order_id in (None, ""):
            snap = _with_client_id(snap, request.client_order_id)
        return snap

    async def cancel_order(self, exchange_order_id: str, symbol: str) -> None:
        await self._call(self._client.cancel_order, exchange_order_id, symbol)

    async def fetch_order(self, exchange_order_id: str, symbol: str) -> OrderSnapshot:
        o = await self._call(self._client.fetch_order, exchange_order_id, symbol)
        return snapshot_from_ccxt(o)

    async def find_order_by_client_id(
        self, client_order_id: str, symbol: str, *, now: datetime | None = None,
    ) -> OrderSnapshot | None:
        if self.capabilities.client_order_lookup == "native":
            try:
                o = await self._call(self._client.fetch_order, None, symbol,
                                     {"clientOrderId": client_order_id})
            except OrderNotFound:
                return None
            return snapshot_from_ccxt(o)
        # Scan fallback: open orders, then recent closed/canceled orders.
        for order in await self._call(self._client.fetch_open_orders, symbol):
            if order.get("clientOrderId") == client_order_id:
                return snapshot_from_ccxt(order)
        since = to_ms(ensure_utc(now or utcnow()) - SCAN_LOOKBACK)
        for order in await self._call(self._client.fetch_closed_orders, symbol, since):
            if order.get("clientOrderId") == client_order_id:
                return snapshot_from_ccxt(order)
        return None

    async def fetch_open_orders(self, symbol: str | None = None) -> list[OrderSnapshot]:
        rows = await self._call(self._client.fetch_open_orders, symbol)
        return [snapshot_from_ccxt(o) for o in rows]

    async def fetch_fills(
        self, symbol: str, *, since: datetime | None = None, limit: int = 100
    ) -> list[Fill]:
        rows = await self._call(self._client.fetch_my_trades, symbol,
                                to_ms(since) if since else None, limit)
        fills: list[Fill] = []
        for t in rows:
            fee = t.get("fee") or {}
            tom = t.get("takerOrMaker")
            fills.append(Fill(
                trade_id=str(t["id"]),
                exchange_order_id=str(t["order"]),
                symbol=t.get("symbol") or symbol,
                side=Side(t["side"]),
                price=to_decimal(t["price"]),
                amount=to_decimal(t["amount"]),
                fee_cost=to_decimal(fee.get("cost") or 0),
                fee_asset=fee.get("currency"),
                is_maker=None if tom is None else tom == "maker",
                timestamp=from_ms(t["timestamp"]),
            ))
        return fills

    # ---------------------------------------------------------------- operations
    async def health_check(self) -> HealthStatus:
        started = time.monotonic()
        try:
            await self._call(self._client.fetch_time)
        except ExchangeError as exc:
            return HealthStatus(self.code, False, None, utcnow(), type(exc).__name__)
        return HealthStatus(self.code, True, int((time.monotonic() - started) * 1000), utcnow())

    async def close(self) -> None:
        await self._client.close()


def _with_client_id(snap: OrderSnapshot, client_order_id: str) -> OrderSnapshot:
    return replace(snap, client_order_id=client_order_id)


def create_ccxt_client(
    code: str, *, api_key: str | None = None, secret: str | None = None,
    sandbox: bool = False, timeout_ms: int = 10_000,
) -> Any:
    """Instantiate a ccxt async client configured for SPOT only."""
    cls = getattr(ccxt_async, code)
    config: dict[str, Any] = {
        "enableRateLimit": True,
        "timeout": timeout_ms,
        "options": {"defaultType": "spot", "adjustForTimeDifference": True},
    }
    if api_key and secret:
        config["apiKey"] = api_key
        config["secret"] = secret
    client = cls(config)
    if sandbox:
        client.set_sandbox_mode(True)
    return client
