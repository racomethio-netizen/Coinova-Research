from __future__ import annotations

from decimal import Decimal as D
from typing import Any

import pytest
from nexus_api.ordertest import run_round_trip
from nexus_exchange.binance_futures import FuturesPosition, FuturesRules


class FakeExchange:
    """Stateful stand-in for the futures adapter: fills market orders at the mark."""

    def __init__(self, *, existing: D = D(0), fail_positions_after_entry: bool = False) -> None:
        self.qty = existing
        self.orders: dict[str, dict[str, Any]] = {}
        self.calls: list[str] = []
        self.fail = fail_positions_after_entry
        self._n = 0

    def _id(self, p: str) -> str:
        self._n += 1
        return f"{p}-{self._n}"

    async def hedge_mode_enabled(self) -> bool:
        return False

    async def positions(self, symbol: str | None = None) -> list[FuturesPosition]:
        if self.fail and self.qty > 0 and "verify" not in self.calls:
            self.calls.append("verify")
            raise RuntimeError("network blip")
        return [FuturesPosition("BTCUSDT", self.qty, D(60000), D(60000), D(30000) if self.qty else None,
                                2, "isolated", D(0))]

    async def ensure_isolated(self, symbol: str) -> None:
        self.calls.append("isolated")

    async def set_leverage(self, symbol: str, leverage: int) -> None:
        self.calls.append(f"leverage{leverage}")

    async def rules(self, symbol: str) -> FuturesRules:
        return FuturesRules("BTCUSDT", D("0.1"), D("0.001"), D("0.001"), D(100), D("0.001"))

    async def mark(self, symbol: str) -> dict[str, Any]:
        return {"mark_price": D(60000)}

    async def submit_entry(self, *, symbol: str, side: str, quantity: D, **_: Any) -> str:
        self.calls.append(f"entry {side} {quantity}")
        self.qty += quantity
        return self._id("ent")

    async def order_status(self, symbol: str, cid: str) -> dict[str, Any]:
        return {"executed_qty": self.qty}

    async def submit_protective(self, *, symbol: str, close_side: str, kind: str,
                                trigger_price: D) -> str:
        cid = self._id(kind)
        self.orders[cid] = {"client_order_id": cid, "type": kind.upper()}
        return cid

    async def open_orders(self, symbol: str | None = None) -> list[dict[str, Any]]:
        return list(self.orders.values())

    async def submit_reduce_only_market(self, *, symbol: str, close_side: str, quantity: D,
                                        **_: Any) -> str:
        self.calls.append(f"reduce {close_side} {quantity}")
        self.qty -= quantity
        return self._id("red")

    async def cancel(self, symbol: str, cid: str) -> None:
        self.orders.pop(cid, None)


async def test_round_trip_confirms_each_step_and_ends_flat() -> None:
    ex = FakeExchange()
    out = await run_round_trip(ex)  # type: ignore[arg-type]
    assert out["ok"], out["steps"]
    names = [s["step"] for s in out["steps"]]
    assert names[-2:] == ["position closed (reduce-only)", "protective orders cancelled"]
    assert ex.qty == 0 and ex.orders == {}
    assert "leverage2" in ex.calls and "entry BUY 0.002" in ex.calls  # 100 USDT min x1.05 / 60k


async def test_refuses_when_a_position_already_exists() -> None:
    ex = FakeExchange(existing=D("0.5"))
    out = await run_round_trip(ex)  # type: ignore[arg-type]
    assert not out["ok"] and not [c for c in ex.calls if c.startswith("entry")]
    assert ex.qty == D("0.5")  # never touched


async def test_cleanup_runs_even_when_verification_fails() -> None:
    ex = FakeExchange(fail_positions_after_entry=True)
    with pytest.raises(RuntimeError):
        await run_round_trip(ex)  # type: ignore[arg-type]
    assert ex.qty == 0 and ex.orders == {}


class FakeSpot:
    """ccxt-shaped spot client: market orders fill at 60,000; fee taken in BTC."""

    def __init__(self, btc: float = 0.5) -> None:
        import ccxt

        self.precisionMode = ccxt.TICK_SIZE
        self.btc = btc
        self.orders: dict[str, dict[str, Any]] = {}
        self.log: list[tuple[str, float]] = []
        self._n = 0

    async def load_markets(self) -> dict[str, Any]:
        return {"BTC/USDT": {"spot": True, "type": "spot", "active": True, "symbol": "BTC/USDT",
                             "id": "BTCUSDT", "base": "BTC", "quote": "USDT",
                             "precision": {"price": 0.01, "amount": 0.00001},
                             "limits": {"amount": {"min": 0.00001}, "cost": {"min": 5}}}}

    async def fetch_ticker(self, symbol: str) -> dict[str, Any]:
        return {"last": 60000.0}

    async def fetch_balance(self) -> dict[str, Any]:
        return {"free": {"BTC": self.btc}}

    async def create_order(self, symbol: str, type_: str, side: str, amount: float,
                           price: float | None, params: dict[str, Any]) -> dict[str, Any]:
        self._n += 1
        oid = str(self._n)
        self.log.append((f"{type_} {side}", amount))
        if type_ == "market":
            self.btc += amount * 0.999 if side == "buy" else -amount
            self.orders[oid] = {"id": oid, "status": "closed", "filled": amount, "average": 60000}
        else:
            self.orders[oid] = {"id": oid, "status": "open", "filled": 0}
        return {"id": oid}

    async def fetch_order(self, oid: str, symbol: str) -> dict[str, Any]:
        return self.orders[oid]

    async def fetch_open_orders(self, symbol: str) -> list[dict[str, Any]]:
        return [o for o in self.orders.values() if o["status"] == "open"]

    async def cancel_order(self, oid: str, symbol: str) -> None:
        self.orders[oid]["status"] = "canceled"


async def test_spot_round_trip_sells_back_only_what_it_bought() -> None:
    from nexus_api.ordertest import run_spot_round_trip

    ex = FakeSpot(btc=0.5)
    out = await run_spot_round_trip(ex)
    assert out["ok"], out["steps"]
    assert [s["step"] for s in out["steps"]][-2:] == ["stop-loss cancelled",
                                                      "sold back what the test bought"]
    assert not [o for o in ex.orders.values() if o["status"] == "open"]
    assert 0.5 - 1e-5 <= ex.btc <= 0.5 + 1e-5  # the pre-existing 0.5 BTC is untouched
    assert ex.log[1][0] == "STOP_LOSS_LIMIT sell"
