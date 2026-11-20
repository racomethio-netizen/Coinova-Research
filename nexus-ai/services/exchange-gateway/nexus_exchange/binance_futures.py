"""Binance USD-M perpetual futures adapter (native REST via a transport).

Why native endpoints: protective stops, reduce-only closes, margin type, leverage, mark
price, funding and liquidation fields have parameters that differ from spot and that
CCXT's unified order API does not expose reliably. Each request below is the documented
Binance USD-M endpoint; quantities and prices are sent as decimal strings, never floats.

Safety rules enforced here:
* One-way position mode only. Hedge mode is refused rather than changed silently.
* Isolated margin by default. Leverage must be one of the approved tiers and is only ever
  set explicitly; it is never raised automatically.
* Submissions are never retried after an ambiguous outcome. The caller reconciles by
  client order id (order_status), exactly as on spot.
* Protective stops use STOP_MARKET with closePosition, so they cover the whole position
  even after partial fills, and use MARK_PRICE as the trigger.

The transport is the only piece that talks to the network. Production uses CCXT's signed
fapi* methods; tests use a recording transport.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Protocol

from nexus_common.decimals import ZERO

from nexus_exchange.errors import (
    ExchangeError,
    ExchangeUnavailable,
    InvalidOrder,
    OrderNotFound,
    OrderStateUnknown,
)

APPROVED_LEVERAGE = (2, 3, 4, 5)
MAX_LEVERAGE = 20
ALERT_LIQUIDATION_DISTANCE = Decimal("0.10")  # alert when mark is within 10% of liquidation


class Transport(Protocol):
    async def call(self, name: str, params: dict[str, Any]) -> Any: ...


class ProtectionFailed(ExchangeError):
    """A position was opened but could not be protected; it has been reduced or closed."""


def new_client_order_id(prefix: str = "nx") -> str:
    """Binance accepts up to 36 characters: prefix + 32 hex characters."""
    return f"{prefix}-{uuid.uuid4().hex}"[:36]


def _d(value: Any) -> Decimal:
    return ZERO if value in (None, "") else Decimal(str(value))


@dataclass(frozen=True)
class FuturesRules:
    symbol: str
    tick_size: Decimal
    step_size: Decimal
    min_qty: Decimal
    min_notional: Decimal
    market_step_size: Decimal


@dataclass(frozen=True)
class FuturesPosition:
    symbol: str
    quantity: Decimal  # signed: positive long, negative short, zero flat
    entry_price: Decimal
    mark_price: Decimal
    liquidation_price: Decimal | None
    leverage: int
    margin_type: str
    unrealized_pnl: Decimal

    @property
    def side(self) -> str | None:
        return None if self.quantity == 0 else ("long" if self.quantity > 0 else "short")

    @property
    def liquidation_distance(self) -> Decimal | None:
        if self.liquidation_price is None or self.mark_price <= 0 or self.quantity == 0:
            return None
        return abs(self.mark_price - self.liquidation_price) / self.mark_price


class BinanceFuturesAdapter:
    code = "binance_futures"

    def __init__(
        self, transport: Transport, *, allowed_leverage: tuple[int, ...] = APPROVED_LEVERAGE
    ) -> None:
        self._t = transport
        self._allowed = tuple(sorted(set(allowed_leverage)))

    def __repr__(self) -> str:  # never expose the transport (it holds credentials)
        return "BinanceFuturesAdapter()"

    async def _call(
        self,
        name: str,
        params: dict[str, Any] | None = None,
        *,
        submit: bool = False,
        client_order_id: str | None = None,
    ) -> Any:
        try:
            return await self._t.call(name, dict(params or {}))
        except ExchangeError:
            raise
        except (TimeoutError, ConnectionError, OSError) as exc:
            if submit and client_order_id:
                raise OrderStateUnknown(
                    f"{type(exc).__name__} during submit", client_order_id=client_order_id, exchange=self.code
                ) from exc
            raise ExchangeUnavailable(f"{type(exc).__name__}", exchange=self.code) from exc
        except Exception as exc:
            if submit and client_order_id:
                raise OrderStateUnknown(
                    f"{type(exc).__name__}: {str(exc)[:200]}",
                    client_order_id=client_order_id,
                    exchange=self.code,
                ) from exc
            raise ExchangeError(f"{type(exc).__name__}: {str(exc)[:200]}", exchange=self.code) from exc

    # ------------------------------------------------------------------ market data
    async def ping(self) -> bool:
        await self._call("fapiPublicGetPing")
        return True

    async def rules(self, symbol: str) -> FuturesRules:
        info = await self._call("fapiPublicGetExchangeInfo")
        native = symbol.replace("/", "").replace(":USDT", "")
        for s in info.get("symbols", []):
            if s.get("symbol") != native:
                continue
            if (
                s.get("contractType") != "PERPETUAL"
                or s.get("quoteAsset") != "USDT"
                or s.get("status") != "TRADING"
            ):
                raise InvalidOrder(f"{native} is not a tradable USDT perpetual", exchange=self.code)
            f = {x["filterType"]: x for x in s.get("filters", [])}
            return FuturesRules(
                symbol=symbol,
                tick_size=_d(f["PRICE_FILTER"]["tickSize"]),
                step_size=_d(f["LOT_SIZE"]["stepSize"]),
                min_qty=_d(f["LOT_SIZE"]["minQty"]),
                min_notional=_d(f.get("MIN_NOTIONAL", {}).get("notional", 0)),
                market_step_size=_d(f.get("MARKET_LOT_SIZE", f["LOT_SIZE"])["stepSize"]),
            )
        raise InvalidOrder(f"no futures market {native}", exchange=self.code)

    async def mark(self, symbol: str) -> dict[str, Any]:
        """Mark price, index price, last funding rate and next funding time."""
        r = await self._call("fapiPublicGetPremiumIndex", {"symbol": _native(symbol)})
        return {
            "mark_price": _d(r["markPrice"]),
            "index_price": _d(r["indexPrice"]),
            "funding_rate": _d(r["lastFundingRate"]),
            "next_funding_time": int(r["nextFundingTime"]),
        }

    async def open_interest(self, symbol: str) -> Decimal:
        r = await self._call("fapiPublicGetOpenInterest", {"symbol": _native(symbol)})
        return _d(r["openInterest"])

    # ------------------------------------------------------------------ account
    async def account(self) -> dict[str, Decimal]:
        r = await self._call("fapiPrivateV2GetAccount")
        return {
            "wallet_balance": _d(r["totalWalletBalance"]),
            "available_balance": _d(r["availableBalance"]),
            "unrealized_pnl": _d(r["totalUnrealizedProfit"]),
            "initial_margin": _d(r["totalInitialMargin"]),
            "maintenance_margin": _d(r["totalMaintMargin"]),
        }

    async def hedge_mode_enabled(self) -> bool:
        r = await self._call("fapiPrivateGetPositionSideDual")
        return bool(r.get("dualSidePosition"))

    async def positions(self, symbol: str | None = None) -> list[FuturesPosition]:
        params = {"symbol": _native(symbol)} if symbol else {}
        rows = await self._call("fapiPrivateV2GetPositionRisk", params)
        out = []
        for r in rows:
            qty = _d(r.get("positionAmt"))
            if qty == 0 and not symbol:
                continue
            liq = _d(r.get("liquidationPrice"))
            out.append(
                FuturesPosition(
                    symbol=r["symbol"],
                    quantity=qty,
                    entry_price=_d(r.get("entryPrice")),
                    mark_price=_d(r.get("markPrice")),
                    liquidation_price=liq if liq > 0 else None,
                    leverage=int(r.get("leverage", 0)),
                    margin_type=str(r.get("marginType", "")).lower(),
                    unrealized_pnl=_d(r.get("unRealizedProfit")),
                )
            )
        return out

    async def funding_paid(self, symbol: str, since_ms: int | None = None) -> Decimal:
        """Sum of funding fees for the symbol (negative = paid, positive = received)."""
        params: dict[str, Any] = {"symbol": _native(symbol), "incomeType": "FUNDING_FEE", "limit": 1000}
        if since_ms is not None:
            params["startTime"] = since_ms
        rows = await self._call("fapiPrivateGetIncome", params)
        return sum((_d(r["income"]) for r in rows), ZERO)

    # ------------------------------------------------------------------ settings
    async def ensure_isolated(self, symbol: str) -> None:
        if await self.hedge_mode_enabled():
            raise InvalidOrder(
                "hedge (dual-side) mode is enabled; this system requires one-way mode", exchange=self.code
            )
        try:
            await self._call(
                "fapiPrivatePostMarginType", {"symbol": _native(symbol), "marginType": "ISOLATED"}
            )
        except ExchangeError as exc:
            if "-4046" not in str(exc):  # -4046: already isolated
                raise

    async def set_leverage(self, symbol: str, leverage: int) -> None:
        """Explicit, operator-requested change only. Must be an approved tier."""
        if leverage not in self._allowed or leverage > MAX_LEVERAGE:
            raise InvalidOrder(
                f"leverage {leverage}x is not an approved tier {self._allowed}", exchange=self.code
            )
        await self._call("fapiPrivatePostLeverage", {"symbol": _native(symbol), "leverage": leverage})

    # ------------------------------------------------------------------ orders
    async def submit_entry(
        self,
        *,
        symbol: str,
        side: str,
        quantity: Decimal,
        price: Decimal | None = None,
        client_order_id: str | None = None,
    ) -> str:
        """Market entry when price is None, otherwise a GTC limit. Returns the client order id."""
        cid = client_order_id or new_client_order_id("ent")
        params: dict[str, Any] = {
            "symbol": _native(symbol),
            "side": side.upper(),
            "quantity": str(quantity),
            "newClientOrderId": cid,
            "newOrderRespType": "RESULT",
        }
        if price is None:
            params["type"] = "MARKET"
        else:
            params.update(type="LIMIT", timeInForce="GTC", price=str(price))
        await self._call("fapiPrivatePostOrder", params, submit=True, client_order_id=cid)
        return cid

    async def submit_protective(
        self,
        *,
        symbol: str,
        close_side: str,
        kind: str,
        trigger_price: Decimal,
        client_order_id: str | None = None,
    ) -> str:
        """STOP_MARKET (kind='stop') or TAKE_PROFIT_MARKET (kind='take_profit'), closing the
        whole position (closePosition=true, no quantity), triggered on mark price."""
        if kind not in ("stop", "take_profit"):
            raise InvalidOrder("kind must be 'stop' or 'take_profit'", exchange=self.code)
        cid = client_order_id or new_client_order_id("stp" if kind == "stop" else "tp")
        params = {
            "symbol": _native(symbol),
            "side": close_side.upper(),
            "type": "STOP_MARKET" if kind == "stop" else "TAKE_PROFIT_MARKET",
            "stopPrice": str(trigger_price),
            "closePosition": "true",
            "workingType": "MARK_PRICE",
            "newClientOrderId": cid,
            "newOrderRespType": "RESULT",
        }
        await self._call("fapiPrivatePostOrder", params, submit=True, client_order_id=cid)
        return cid

    async def submit_reduce_only_market(
        self, *, symbol: str, close_side: str, quantity: Decimal, client_order_id: str | None = None
    ) -> str:
        cid = client_order_id or new_client_order_id("red")
        params = {
            "symbol": _native(symbol),
            "side": close_side.upper(),
            "type": "MARKET",
            "quantity": str(quantity),
            "reduceOnly": "true",
            "newClientOrderId": cid,
            "newOrderRespType": "RESULT",
        }
        await self._call("fapiPrivatePostOrder", params, submit=True, client_order_id=cid)
        return cid

    async def order_status(self, symbol: str, client_order_id: str) -> dict[str, Any] | None:
        """Look up an order by OUR client id. None means the exchange has no such order."""
        try:
            r = await self._call(
                "fapiPrivateGetOrder", {"symbol": _native(symbol), "origClientOrderId": client_order_id}
            )
        except ExchangeError as exc:
            if "-2013" in str(exc) or "Order does not exist" in str(exc):
                return None
            raise
        return {
            "status": r["status"],
            "executed_qty": _d(r["executedQty"]),
            "avg_price": _d(r.get("avgPrice")),
            "orig_qty": _d(r["origQty"]),
            "type": r.get("type"),
            "close_position": bool(r.get("closePosition")),
            "client_order_id": r.get("clientOrderId"),
        }

    async def open_orders(self, symbol: str) -> list[dict[str, Any]]:
        rows = await self._call("fapiPrivateGetOpenOrders", {"symbol": _native(symbol)})
        return [
            {
                "client_order_id": r["clientOrderId"],
                "type": r["type"],
                "status": r["status"],
                "close_position": bool(r.get("closePosition")),
            }
            for r in rows
        ]

    async def cancel(self, symbol: str, client_order_id: str) -> None:
        await self._call(
            "fapiPrivateDeleteOrder", {"symbol": _native(symbol), "origClientOrderId": client_order_id}
        )


def _native(symbol: str | None) -> str:
    assert symbol is not None
    return symbol.replace("/", "").replace(":USDT", "")


@dataclass(frozen=True)
class OpenResult:
    entry_id: str
    filled_qty: Decimal
    stop_id: str | None
    take_profit_id: str | None
    state: str  # "protected" | "closed_after_protection_failure"


async def open_protected_position(
    adapter: BinanceFuturesAdapter,
    *,
    symbol: str,
    side: str,
    quantity: Decimal,
    stop_price: Decimal,
    take_profit_price: Decimal | None,
    alert: Any,
) -> OpenResult:
    """Enter, confirm the actual fill, then protect. If protection fails, the position is
    closed immediately with a reduce-only market order and the operator is alerted."""
    close_side = "SELL" if side.lower() == "long" else "BUY"
    entry_id = await adapter.submit_entry(
        symbol=symbol, side="BUY" if side.lower() == "long" else "SELL", quantity=quantity
    )
    status = await adapter.order_status(symbol, entry_id)
    if status is None:
        raise OrderNotFound(f"entry {entry_id} not visible at the exchange", exchange=adapter.code)
    filled = status["executed_qty"]
    if filled <= 0:
        return OpenResult(entry_id, ZERO, None, None, "no_fill")
    try:
        stop_id = await adapter.submit_protective(
            symbol=symbol, close_side=close_side, kind="stop", trigger_price=stop_price
        )
    except ExchangeError as exc:
        await adapter.submit_reduce_only_market(symbol=symbol, close_side=close_side, quantity=filled)
        await alert(f"Stop-loss could not be placed on {symbol}; position closed. {exc}")
        raise ProtectionFailed(
            f"stop placement failed on {symbol}; position closed", exchange=adapter.code
        ) from exc
    tp_id = None
    if take_profit_price is not None:
        try:
            tp_id = await adapter.submit_protective(
                symbol=symbol, close_side=close_side, kind="take_profit", trigger_price=take_profit_price
            )
        except ExchangeError as exc:
            await alert(f"Take-profit could not be placed on {symbol}; stop remains in place. {exc}")
    return OpenResult(entry_id, filled, stop_id, tp_id, "protected")
