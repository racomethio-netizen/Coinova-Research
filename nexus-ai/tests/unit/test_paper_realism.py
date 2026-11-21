"""Paper realism: participation partial fills, latency against a fresh book, stale books,
injected outages (refused / timeout after sending)."""

from __future__ import annotations

import asyncio
from datetime import timedelta
from decimal import Decimal as D

import pytest
from nexus_common.timeutil import utcnow
from nexus_exchange.errors import ExchangeUnavailable, OrderStateUnknown
from nexus_exchange.models import OrderBook, OrderRequest, OrderStatus, Side, TimeInForce
from nexus_execution.brokers import PaperBroker
from nexus_execution.paper import simulate_submit


def book(ask: str = "100", age_s: float = 0) -> OrderBook:
    a = D(ask)
    return OrderBook("BTC/USDT", bids=((a - 1, D(10)),), asks=((a, D(2)), (a + 1, D(5))),
                     timestamp=utcnow() - timedelta(seconds=age_s))


CIDS = {"c1": "11111111-1111-4111-8111-111111111111",
        "c2": "22222222-2222-4222-8222-222222222222",
        "c3": "33333333-3333-4333-8333-333333333333"}


def req(amount: str = "3", price: str = "102", cid: str = "c1") -> OrderRequest:
    return OrderRequest(client_order_id=CIDS[cid], symbol="BTC/USDT", side=Side.BUY,
                        amount=D(amount), price=D(price), time_in_force=TimeInForce.IOC)


def test_participation_cap_creates_partial_fills() -> None:
    full = simulate_submit(req("4"), book(), taker_fee=D(0), extra_slippage=D(0))
    assert full.remaining == 0
    half = simulate_submit(req("4"), book(), taker_fee=D(0), extra_slippage=D(0),
                           participation=D("0.5"))
    assert [f.amount for f in half.fills] == [D(1), D("2.5")] and half.remaining == D("0.5")


class Seq:
    def __init__(self, *xs: float) -> None:
        self.xs = list(xs)

    def random(self) -> float:
        return self.xs.pop(0)


def broker(books: list[OrderBook], **kw: object) -> PaperBroker:
    async def get(_e: str, _s: str) -> OrderBook:
        return books.pop(0) if len(books) > 1 else books[0]
    return PaperBroker(get, taker_fee=D("0.001"), maker_fee=D("0.001"),
                       extra_slippage=D(0), **kw)  # type: ignore[arg-type]


def test_latency_fills_against_the_fresh_book_or_misses() -> None:
    # Price jumps from 100 to 103 during the delay: the IOC limit 102 misses entirely.
    b = broker([book("103")], latency_ms=10)
    up = asyncio.run(b.submit(req(), exchange="binance"))
    assert up.status is OrderStatus.CANCELED and up.fills == ()


def test_stale_book_is_an_outage_not_a_fill() -> None:
    b = broker([book(age_s=30)], max_book_age_s=10)
    with pytest.raises(ExchangeUnavailable, match="old"):
        asyncio.run(b.submit(req(), exchange="binance"))


def test_injected_outage_refused_and_timeout_after_send() -> None:
    refused = broker([book()], outage_rate=0.5, rng=Seq(0.1, 0.2))
    with pytest.raises(ExchangeUnavailable, match="refused"):
        asyncio.run(refused.submit(req(), exchange="binance"))
    lost = broker([book()], outage_rate=0.5, rng=Seq(0.1, 0.9))
    with pytest.raises(OrderStateUnknown):
        asyncio.run(lost.submit(req(cid="c2"), exchange="binance"))
    found = asyncio.run(lost.find(CIDS["c2"], exchange="binance", symbol="BTC/USDT", since=None))
    assert found is not None and found.status is OrderStatus.FILLED   # it did execute
    calm = broker([book()], outage_rate=0.5, rng=Seq(0.9))
    assert asyncio.run(calm.submit(req(cid="c3"), exchange="binance")).fills
