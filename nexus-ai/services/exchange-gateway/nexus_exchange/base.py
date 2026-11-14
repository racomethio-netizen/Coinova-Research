"""The common exchange interface every adapter implements.

Order placement is split in two so the execution engine can persist intent first:

1. ``prepare_order`` rounds to exchange precision and validates (no I/O except
   cached market metadata).
2. The engine stores the prepared order with status ``pending_submit``.
3. ``submit_order`` sends it exactly once. On ambiguity it raises
   :class:`OrderStateUnknown`; the engine then calls ``find_order_by_client_id``.
   It never resubmits automatically.

There are deliberately no margin, futures, leverage, borrow, transfer or withdrawal
methods. NEXUS AI is spot-only by construction.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime

from nexus_config import Timeframe

from nexus_exchange.errors import InvalidOrder, UnsupportedFeature
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
    Ticker,
)
from nexus_exchange.precision import normalize_order, validate_order


class ExchangeAdapter(ABC):
    code: str
    capabilities: Capabilities

    def __init__(self) -> None:
        self._markets: dict[str, MarketRules] = {}

    # ---------------------------------------------------------------- metadata
    @abstractmethod
    async def load_markets(self, *, reload: bool = False) -> dict[str, MarketRules]:
        """Load spot markets only. Must populate ``self._markets``."""

    async def market_rules(self, symbol: str) -> MarketRules:
        if not self._markets:
            await self.load_markets()
        rules = self._markets.get(symbol)
        if rules is None:
            raise UnsupportedFeature(f"{self.code} has no spot market {symbol}", exchange=self.code)
        return rules

    # ---------------------------------------------------------------- market data
    @abstractmethod
    async def fetch_ticker(self, symbol: str) -> Ticker: ...

    @abstractmethod
    async def fetch_order_book(self, symbol: str, *, depth: int = 20) -> OrderBook: ...

    @abstractmethod
    async def fetch_closed_candles(
        self, symbol: str, timeframe: Timeframe, *, since: datetime | None = None,
        limit: int = 500,
    ) -> list[Candle]:
        """OHLCV, oldest first, containing ONLY candles that have fully closed."""

    # ---------------------------------------------------------------- account
    @abstractmethod
    async def fetch_balances(self) -> dict[str, AssetBalance]: ...

    # ---------------------------------------------------------------- orders
    async def prepare_order(self, request: OrderRequest) -> OrderRequest:
        return normalize_order(request, await self.market_rules(request.symbol))

    async def submit_order(self, request: OrderRequest) -> OrderSnapshot:
        """Submit once. Re-validates; never rounds (that was ``prepare_order``'s job)."""
        rules = await self.market_rules(request.symbol)
        validate_order(request, rules)
        if request.post_only and not self.capabilities.post_only:
            raise InvalidOrder(f"{self.code} adapter does not support post-only orders")
        return await self._submit(request)

    @abstractmethod
    async def _submit(self, request: OrderRequest) -> OrderSnapshot: ...

    @abstractmethod
    async def cancel_order(self, exchange_order_id: str, symbol: str) -> None: ...

    @abstractmethod
    async def fetch_order(self, exchange_order_id: str, symbol: str) -> OrderSnapshot: ...

    @abstractmethod
    async def find_order_by_client_id(
        self, client_order_id: str, symbol: str
    ) -> OrderSnapshot | None:
        """Return the order or None if the exchange has no such order.

        Must raise (not return None) if the lookup itself failed, so callers never
        mistake "could not check" for "does not exist".
        """

    @abstractmethod
    async def fetch_open_orders(self, symbol: str | None = None) -> list[OrderSnapshot]: ...

    @abstractmethod
    async def fetch_fills(
        self, symbol: str, *, since: datetime | None = None, limit: int = 100
    ) -> list[Fill]: ...

    # ---------------------------------------------------------------- operations
    @abstractmethod
    async def health_check(self) -> HealthStatus: ...

    @abstractmethod
    async def close(self) -> None: ...
