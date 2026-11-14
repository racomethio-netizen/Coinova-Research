"""Test double for a ccxt async client. Lives only under tests/; never used in production."""

from __future__ import annotations

from typing import Any


def spot_market(symbol: str = "BTC/USDT", **overrides: Any) -> dict[str, Any]:
    base, quote = symbol.split("/")
    m: dict[str, Any] = {
        "id": f"{base}{quote}", "symbol": symbol, "base": base, "quote": quote,
        "type": "spot", "spot": True, "active": True,
        "precision": {"price": 0.01, "amount": 0.00001},
        "limits": {"amount": {"min": 0.00001, "max": 9000.0}, "cost": {"min": 5.0}},
        "maker": 0.001, "taker": 0.001,
    }
    m.update(overrides)
    return m


class FakeCcxtClient:
    precisionMode = 4  # TICK_SIZE

    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple[Any, ...]]] = []
        self.markets: dict[str, Any] = {
            "BTC/USDT": spot_market("BTC/USDT"),
            "ETH/USDT": spot_market("ETH/USDT", precision={"price": 0.01, "amount": 0.0001}),
            "BTC/USDT:USDT": {**spot_market("BTC/USDT"), "symbol": "BTC/USDT:USDT",
                              "type": "swap", "spot": False},
            "XRP/USDT": spot_market("XRP/USDT", precision={"price": None, "amount": 1}),
        }
        self.responses: dict[str, Any] = {}
        self.errors: dict[str, BaseException] = {}
        self.closed = False

    async def _do(self, name: str, *args: Any) -> Any:
        self.calls.append((name, args))
        if name in self.errors:
            raise self.errors[name]
        resp = self.responses.get(name)
        return resp(*args) if callable(resp) else resp

    def count(self, name: str) -> int:
        return sum(1 for n, _ in self.calls if n == name)

    async def load_markets(self, reload: bool = False) -> Any:
        self.calls.append(("load_markets", (reload,)))
        if "load_markets" in self.errors:
            raise self.errors["load_markets"]
        return self.markets

    async def fetch_ticker(self, *a: Any) -> Any: return await self._do("fetch_ticker", *a)
    async def fetch_order_book(self, *a: Any) -> Any: return await self._do("fetch_order_book", *a)
    async def fetch_ohlcv(self, *a: Any) -> Any: return await self._do("fetch_ohlcv", *a)
    async def fetch_balance(self, *a: Any) -> Any: return await self._do("fetch_balance", *a)
    async def create_order(self, *a: Any) -> Any: return await self._do("create_order", *a)
    async def cancel_order(self, *a: Any) -> Any: return await self._do("cancel_order", *a)
    async def fetch_order(self, *a: Any) -> Any: return await self._do("fetch_order", *a)
    async def fetch_open_orders(self, *a: Any) -> Any:
        return await self._do("fetch_open_orders", *a)
    async def fetch_closed_orders(self, *a: Any) -> Any:
        return await self._do("fetch_closed_orders", *a)
    async def fetch_my_trades(self, *a: Any) -> Any: return await self._do("fetch_my_trades", *a)
    async def fetch_time(self, *a: Any) -> Any: return await self._do("fetch_time", *a)

    async def close(self) -> None:
        self.closed = True


def ccxt_order(**overrides: Any) -> dict[str, Any]:
    o: dict[str, Any] = {
        "id": "123", "clientOrderId": None, "symbol": "BTC/USDT", "side": "buy",
        "type": "limit", "status": "open", "price": 60000.0, "amount": 0.001,
        "filled": 0.0, "average": None, "fee": None, "fees": [], "timestamp": 1_767_225_600_000,
    }
    o.update(overrides)
    return o
