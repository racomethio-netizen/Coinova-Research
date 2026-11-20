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
