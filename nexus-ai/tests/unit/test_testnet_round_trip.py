"""Testnet round trip on a stateful fake Binance (no network): connectivity, rules,
balances, candles, signal, risk sizing, buy and sell with deterministic client IDs, fill
reconciliation, the restart drill (no second order), a risk rejection, and a timeout that is
reconciled by client ID instead of resubmitted."""

from __future__ import annotations

from typing import Any

import ccxt.async_support as ccxt_async
import numpy as np
import pytest
from nexus_config import ExchangeCode
from nexus_exchange.capabilities import CAPABILITIES
from nexus_exchange.ccxt_adapter import CcxtSpotAdapter
from nexus_execution.testnet_run import client_id, round_trip, trend_signal
from nexus_strategies.frames import to_frame

from tests.exchange.fakes import FakeCcxtClient, ccxt_order

HOUR = 3_600_000


class FakeBinance:
    """Orders live in a dict keyed by client order ID; IOC limits fill fully."""

    def __init__(self, client: FakeCcxtClient, *, usdt: float = 10_000.0) -> None:
        self.orders: dict[str, dict[str, Any]] = {}
        self.creates = 0
        now = 1_795_000_000_000 // HOUR * HOUR
        rng = np.random.default_rng(3)
        closes = 60_000 * np.exp(np.cumsum(rng.normal(0.0008, 0.004, 330)))
        self.ohlcv = [[now - (330 - i) * HOUR, float(c), float(c) * 1.003, float(c) * 0.997,
                       float(c), 10.0]
                      for i, c in enumerate(closes)]
        last = float(closes[-1])
        client.responses.update({
            "fetch_time": 1, "fetch_ohlcv": lambda *a: self.ohlcv,
            "fetch_order_book": {"bids": [[last - 1, 5.0]], "asks": [[last + 1, 5.0]],
                                 "timestamp": now},
            "fetch_balance": {"total": {"USDT": usdt, "BTC": 1.0},
                              "free": {"USDT": usdt, "BTC": 1.0},
                              "used": {"USDT": 0.0, "BTC": 0.0}},
            "create_order": self.create, "fetch_order": self.fetch})

    def create(self, symbol: str, typ: str, side: str, amount: float, price: float,
               params: dict[str, Any]) -> dict[str, Any]:
        self.creates += 1
        cid = params["clientOrderId"]
        o = ccxt_order(id=str(len(self.orders) + 1), clientOrderId=cid, symbol=symbol,
                       side=side, type=typ, status="closed", price=price, amount=amount,
                       filled=amount, average=price,
                       fee={"cost": amount * price * 0.001, "currency": "USDT"})
        self.orders[cid] = o
        return o

    def fetch(self, oid: Any, symbol: str, params: dict[str, Any] | None = None
              ) -> dict[str, Any]:
        cid = (params or {}).get("clientOrderId")
        if cid not in self.orders:
            raise ccxt_async.OrderNotFound("Order does not exist.")
        return self.orders[cid]


def adapter(usdt: float = 10_000.0) -> tuple[CcxtSpotAdapter, FakeBinance]:
    client = FakeCcxtClient()
    fake = FakeBinance(client, usdt=usdt)
    return CcxtSpotAdapter("binance", client, CAPABILITIES[ExchangeCode.BINANCE]), fake


async def test_round_trip_fills_reconciles_and_never_duplicates() -> None:
    ad, fake = adapter()
    ev = await round_trip(ad, run_id="abc123")
    s = ev["steps"]
    assert s["connectivity"]["ok"] and s["candles"]["closed_bars"] >= 300
    assert s["signal"]["decision"] in ("LONG", "NO_TRADE") and "adx" in s["signal"]
    assert s["risk"]["approved"] and float(s["risk"]["risk_amount"]) <= 25.0 * 1.001
    assert s["buy"]["client_order_id"] == client_id("abc123", "buy") and s["buy"]["newly_placed"]
    assert s["sell"]["filled"] == s["buy"]["filled"]
    assert s["restart_drill"] == {"client_order_id": client_id("abc123", "buy"),
                                  "found_existing": True, "resubmitted": False}
    assert ev["result"] == "ROUND_TRIP_FILLED" and ev["duplicate_protection"] == "passed"
    assert fake.creates == 2                           # one buy, one sell: never a third
    # A second run with the SAME run id (a restart mid-run) places nothing new.
    ev2 = await round_trip(ad, run_id="abc123")
    assert fake.creates == 2 and ev2["steps"]["buy"]["newly_placed"] is False


async def test_risk_rejection_sends_nothing() -> None:
    ad, fake = adapter(usdt=1.0)                       # 0.25% of 1 USDT cannot buy min size
    ev = await round_trip(ad, run_id="tiny")
    assert ev["result"] == "REJECTED_BY_RISK" and fake.creates == 0


async def test_illiquid_book_stops_before_any_order() -> None:
    ad, fake = adapter()
    last = fake.ohlcv[-1][4]
    # A 2% spread: far beyond the 25 bps entry limit.
    ad._client.responses["fetch_order_book"] = {  # type: ignore[attr-defined]
        "bids": [[last * 0.99, 5.0]], "asks": [[last * 1.01, 5.0]], "timestamp": 0}
    ev = await round_trip(ad, run_id="thin")
    assert ev["result"] == "REJECTED_BY_LIQUIDITY" and fake.creates == 0
    assert any("spread" in r for r in ev["steps"]["execution_plan"]["reasons"])


async def test_timeout_is_reconciled_by_client_id_not_resubmitted() -> None:
    ad, fake = adapter()
    real = fake.create

    def create_then_time_out(*a: Any) -> Any:
        real(*a)                                       # Binance accepted it ...
        raise ccxt_async.RequestTimeout("timed out")   # ... but the reply was lost

    ad._client.responses["create_order"] = create_then_time_out  # type: ignore[attr-defined]
    ev = await round_trip(ad, run_id="tmo")
    assert ev["steps"]["buy"]["client_order_id"] == client_id("tmo", "buy")
    assert ev["steps"]["buy"]["filled"] != "0"
    assert list(fake.orders).count(client_id("tmo", "buy")) == 1


def test_signal_needs_history() -> None:
    assert trend_signal(to_frame([]))["decision"] == "NO_TRADE"


@pytest.fixture(autouse=True)
def _no_gate(monkeypatch: pytest.MonkeyPatch) -> None:
    """No Redis here: the Binance weight gate is not consulted."""
    from nexus_common import binance_guard

    monkeypatch.setattr(binance_guard, "_DEFAULT_REDIS", None, raising=False)


def test_client_ids_are_deterministic_uuid4() -> None:
    import uuid

    a, b = client_id("r1", "buy"), client_id("r1", "buy")
    assert a == b and uuid.UUID(a).version == 4 and a != client_id("r1", "sell")
