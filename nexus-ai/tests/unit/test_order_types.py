"""Phase F1: post-only and stop-limit orders (model rules, precision, paper simulation,
Binance wire format)."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal as D

import ccxt
import pytest
from nexus_exchange.errors import InvalidOrder
from nexus_exchange.models import (
    MarketRules,
    OrderBook,
    OrderRequest,
    OrderStatus,
    OrderType,
    Side,
    TimeInForce,
    new_client_order_id,
)
from nexus_exchange.precision import normalize_order
from nexus_execution.brokers import PaperBroker
from nexus_execution.paper import simulate_stop, simulate_submit, would_trigger

FEE = dict(taker_fee=D("0.001"), extra_slippage=D(0))


def book(bid: str, ask: str, qty: str = "1") -> OrderBook:
    return OrderBook("BTC/USDT", ((D(bid), D(qty)),), ((D(ask), D(qty)),),
                     datetime(2026, 1, 1, tzinfo=UTC))


def req(side: Side = Side.SELL, *, price: str = "59000", stop: str | None = "59100",
        otype: OrderType = OrderType.STOP_LIMIT, post_only: bool = False,
        tif: TimeInForce = TimeInForce.GTC, amount: str = "0.5") -> OrderRequest:
    return OrderRequest(new_client_order_id(), "BTC/USDT", side, D(amount), D(price),
                        order_type=otype, time_in_force=tif, post_only=post_only,
                        stop_price=None if stop is None else D(stop))


# ------------------------------------------------------------------ model rules
class TestRequestRules:
    def test_stop_limit_needs_stop(self) -> None:
        with pytest.raises(ValueError, match="stop price"):
            req(stop=None)

    def test_stop_only_on_stop_limit(self) -> None:
        with pytest.raises(ValueError, match="only valid for stop-limit"):
            req(otype=OrderType.LIMIT)

    def test_limit_on_wrong_side_of_stop(self) -> None:
        with pytest.raises(ValueError, match="at or below its stop"):
            req(Side.SELL, price="59200", stop="59100")
        with pytest.raises(ValueError, match="at or above its stop"):
            req(Side.BUY, price="60900", stop="61000")

    def test_post_only_is_gtc_limit(self) -> None:
        with pytest.raises(ValueError, match="post-only"):
            req(otype=OrderType.LIMIT, stop=None, post_only=True, tif=TimeInForce.IOC)
        with pytest.raises(ValueError, match="post-only"):
            req(post_only=True)

    def test_stop_limit_is_gtc(self) -> None:
        with pytest.raises(ValueError, match="GTC"):
            req(tif=TimeInForce.IOC)


def test_precision_rounds_stop_conservatively() -> None:
    rules = MarketRules("BTC/USDT", "BTCUSDT", "BTC", "USDT", D("0.01"), D("0.00001"),
                        min_notional=D(5))
    sell = normalize_order(req(Side.SELL, price="59000.004", stop="59100.004"), rules)
    assert sell.stop_price == D("59100.01")      # a sell stop never triggers lower
    buy = normalize_order(req(Side.BUY, price="61100.009", stop="61000.009"), rules)
    assert buy.stop_price == D("61000.00")       # a buy stop never triggers higher
    with pytest.raises(InvalidOrder, match="notional"):
        normalize_order(req(amount="0.00005"), rules)   # 0.00005 x 59000 < 5


# ------------------------------------------------------------------ paper simulation
class TestPaperPostOnly:
    def test_refused_when_it_would_take(self) -> None:
        r = req(Side.BUY, price="100", stop=None, otype=OrderType.LIMIT, post_only=True)
        with pytest.raises(InvalidOrder, match="post-only"):
            simulate_submit(r, book("99", "100"), **FEE)

    def test_rests_when_passive(self) -> None:
        r = req(Side.BUY, price="99", stop=None, otype=OrderType.LIMIT, post_only=True)
        sim = simulate_submit(r, book("99", "100"), **FEE)
        assert sim.fills == () and sim.resting and sim.remaining == D("0.5")

    def test_sell_side(self) -> None:
        r = req(Side.SELL, price="99", stop=None, otype=OrderType.LIMIT, post_only=True)
        with pytest.raises(InvalidOrder):
            simulate_submit(r, book("99", "100"), **FEE)


class TestPaperStopLimit:
    def test_refused_if_it_would_trigger_immediately(self) -> None:
        with pytest.raises(InvalidOrder, match="trigger immediately"):
            simulate_submit(req(Side.SELL, price="98", stop="100"), book("100", "101"), **FEE)
        with pytest.raises(InvalidOrder, match="trigger immediately"):
            simulate_submit(req(Side.BUY, price="102", stop="101"), book("100", "101"), **FEE)

    def test_rests_untriggered(self) -> None:
        sim = simulate_submit(req(Side.SELL, price="98", stop="99"), book("100", "101"), **FEE)
        assert sim.fills == () and sim.resting and sim.triggered is False

    def test_trigger_then_fill_up_to_limit(self) -> None:
        # Bid falls to 98.5 (stop 99 reached), limit 98: fills at the bid (taker).
        sim = simulate_stop(Side.SELL, D(99), D(98), D("0.5"), book("98.5", "99"), **FEE)
        assert sim.triggered is True and sim.remaining == 0 and not sim.resting
        assert sim.fills[0].price == D("98.5") and sim.fills[0].is_maker is False

    def test_gap_through_limit_rests_unfilled(self) -> None:
        # Price gaps below the limit: triggered, but nothing fills; the order rests.
        sim = simulate_stop(Side.SELL, D(99), D(98), D("0.5"), book("97", "97.5"), **FEE)
        assert sim.triggered is True and sim.fills == () and sim.resting

    def test_not_reached(self) -> None:
        sim = simulate_stop(Side.SELL, D(99), D(98), D("0.5"), book("99.5", "100"), **FEE)
        assert sim.triggered is False and sim.fills == () and sim.resting

    def test_trigger_reference(self) -> None:
        assert would_trigger(Side.SELL, D(99), book("99", "100"))
        assert not would_trigger(Side.SELL, D(99), book("99.01", "100"))
        assert would_trigger(Side.BUY, D(100), book("99", "100"))
        assert not would_trigger(Side.BUY, D(100), book("99", "99.99"))


async def test_paper_broker_stop_lifecycle() -> None:
    books = {"b": book("100", "101")}

    async def source(exchange: str, symbol: str) -> OrderBook:
        return books["b"]

    pb = PaperBroker(source, taker_fee=D("0.001"), maker_fee=D("0.0005"),
                     extra_slippage=D(0))
    r = req(Side.SELL, price="98", stop="99")
    first = await pb.submit(r, exchange="binance")
    assert first.status is OrderStatus.OPEN and first.triggered is False
    kw = dict(exchange="binance", symbol="BTC/USDT", side=Side.SELL, price=D(98),
              amount=D("0.5"), filled=D(0), exchange_order_id=first.exchange_order_id,
              since=None, order_type=OrderType.STOP_LIMIT, stop_price=D(99))
    books["b"] = book("97", "97.5")                  # gaps through the limit
    second = await pb.refresh(**kw, triggered=False)
    assert second.status is OrderStatus.OPEN and second.triggered is True and not second.fills
    books["b"] = book("98.2", "98.6")               # recovers above the limit
    third = await pb.refresh(**kw, triggered=True)  # now a plain resting limit
    assert third.status is OrderStatus.FILLED and third.fills[0].price == D(98)
    assert third.fills[0].is_maker is True and third.triggered is True


# ------------------------------------------------------------------ Binance wire format
def _binance() -> ccxt.binance:
    ex = ccxt.binance()
    ex.set_markets([{
        "id": "BTCUSDT", "symbol": "BTC/USDT", "base": "BTC", "quote": "USDT",
        "baseId": "BTC", "quoteId": "USDT", "type": "spot", "spot": True, "margin": False,
        "swap": False, "future": False, "option": False, "contract": False, "linear": None,
        "inverse": None, "active": True, "precision": {"price": 0.01, "amount": 0.00001},
        "limits": {}, "info": {"orderTypes": ["LIMIT", "LIMIT_MAKER", "MARKET",
                                              "STOP_LOSS_LIMIT", "TAKE_PROFIT_LIMIT"]}}])
    return ex


def test_ccxt_builds_binance_stop_loss_limit() -> None:
    """What the adapter sends (limit + stopLossPrice) becomes Binance STOP_LOSS_LIMIT."""
    wire = _binance().create_order_request(
        "BTC/USDT", "limit", "sell", 0.001, 59000.0,
        {"stopLossPrice": 59100.0, "timeInForce": "GTC", "clientOrderId": "c1"})
    assert wire["type"] == "STOP_LOSS_LIMIT" and wire["stopPrice"] == "59100"
    assert wire["price"] == "59000" and wire["timeInForce"] == "GTC"
    assert wire["newClientOrderId"] == "c1"


def test_ccxt_builds_binance_limit_maker() -> None:
    wire = _binance().create_order_request("BTC/USDT", "limit", "buy", 0.001, 60000.0,
                                           {"postOnly": True, "clientOrderId": "c2"})
    assert wire["type"] == "LIMIT_MAKER"
