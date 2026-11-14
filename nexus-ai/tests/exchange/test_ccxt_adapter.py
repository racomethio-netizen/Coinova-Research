from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from decimal import Decimal as D

import pytest
from ccxt.base import errors as cx
from nexus_config import ExchangeCode, Timeframe
from nexus_exchange import CAPABILITIES, build_adapter
from nexus_exchange.ccxt_adapter import CcxtSpotAdapter, decimal_to_wire, map_ccxt_error
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
from nexus_exchange.models import OrderRequest, OrderStatus, Side, new_client_order_id

from tests.exchange.fakes import FakeCcxtClient, ccxt_order


def adapter(code: ExchangeCode = ExchangeCode.BINANCE) -> tuple[CcxtSpotAdapter, FakeCcxtClient]:
    client = FakeCcxtClient()
    return CcxtSpotAdapter(code.value, client, CAPABILITIES[code]), client


def order_req(amount: str = "0.001", price: str = "60000", cid: str | None = None) -> OrderRequest:
    return OrderRequest(client_order_id=cid or new_client_order_id(), symbol="BTC/USDT",
                        side=Side.BUY, amount=D(amount), price=D(price))


# ------------------------------------------------------------------ markets
async def test_load_markets_spot_only_and_precision_required() -> None:
    a, _ = adapter()
    markets = await a.load_markets()
    assert set(markets) == {"BTC/USDT", "ETH/USDT"}  # swap and no-precision market excluded
    assert "XRP/USDT" in a.excluded_markets
    btc = markets["BTC/USDT"]
    assert btc.price_tick == D("0.01") and btc.amount_step == D("0.00001")
    assert btc.min_notional == D("5")


async def test_decimal_places_precision_mode() -> None:
    a, client = adapter()
    client.precisionMode = 2
    client.markets = {"BTC/USDT": {**client.markets["BTC/USDT"],
                                   "precision": {"price": 2, "amount": 5}}}
    rules = (await a.load_markets())["BTC/USDT"]
    assert rules.price_tick == D("0.01") and rules.amount_step == D("0.00001")


async def test_unknown_symbol_is_unsupported() -> None:
    a, _ = adapter()
    with pytest.raises(UnsupportedFeature):
        await a.market_rules("DOGE/USDT")


# ------------------------------------------------------------------ market data
async def test_closed_candles_exclude_forming_bar_and_convert_decimals() -> None:
    a, client = adapter()
    t0 = datetime(2026, 1, 1, tzinfo=UTC)
    ms = int(t0.timestamp() * 1000)
    client.responses["fetch_ohlcv"] = [
        [ms, 100.1, 101.0, 99.5, 100.7, 12.3],
        [ms + 300_000, 100.7, 102.0, 100.0, 101.2, 5.0],
        [ms + 600_000, 101.2, 101.3, 101.0, 101.1, 0.4],  # still forming at `now`
    ]
    now = t0 + timedelta(minutes=14)
    candles = await a.fetch_closed_candles("BTC/USDT", Timeframe.M5, now=now)
    assert [c.open_time for c in candles] == [t0, t0 + timedelta(minutes=5)]
    assert candles[0].open == D("100.1") and candles[0].volume == D("12.3")


async def test_candles_non_monotonic_rejected() -> None:
    a, client = adapter()
    client.responses["fetch_ohlcv"] = [[600_000, 1, 1, 1, 1, 1], [300_000, 1, 1, 1, 1, 1]]
    with pytest.raises(ExchangeError, match="non-monotonic"):
        await a.fetch_closed_candles("BTC/USDT", Timeframe.M5,
                                     now=datetime(2027, 1, 1, tzinfo=UTC))


async def test_inconsistent_ohlc_rejected() -> None:
    a, client = adapter()
    client.responses["fetch_ohlcv"] = [[0, 10, 9, 8, 9.5, 1]]  # high < open
    with pytest.raises(ValueError, match="inconsistent OHLC"):
        await a.fetch_closed_candles("BTC/USDT", Timeframe.M5,
                                     now=datetime(2027, 1, 1, tzinfo=UTC))


async def test_order_book_and_crossed_book() -> None:
    a, client = adapter()
    client.responses["fetch_order_book"] = {"bids": [[100.0, 1.0], [99.5, 2.0]],
                                            "asks": [[100.5, 1.5]], "timestamp": 1_000}
    book = await a.fetch_order_book("BTC/USDT")
    assert book.best_bid == D("100.0") and book.best_ask == D("100.5")
    assert book.spread_bps is not None and round(book.spread_bps, 2) == D("49.88")
    client.responses["fetch_order_book"] = {"bids": [[101.0, 1.0]], "asks": [[100.0, 1.0]]}
    with pytest.raises(ExchangeError, match="crossed"):
        await a.fetch_order_book("BTC/USDT")


# ------------------------------------------------------------------ balances
async def test_balances() -> None:
    a, client = adapter()
    client.responses["fetch_balance"] = {
        "free": {"USDT": 1000.5, "BTC": 0.0, "ETH": 0.1},
        "used": {"USDT": 0.5, "BTC": 0.0, "ETH": 0.0},
        "total": {"USDT": 1001.0, "BTC": 0.0, "ETH": 0.1},
    }
    bal = await a.fetch_balances()
    assert set(bal) == {"USDT", "ETH"}
    assert bal["USDT"].free == D("1000.5") and bal["USDT"].total == D("1001.0")


async def test_incomplete_balance_refused() -> None:
    a, client = adapter()
    client.responses["fetch_balance"] = {"free": {"USDT": None}, "used": {"USDT": None},
                                         "total": {"USDT": 50.0}}
    with pytest.raises(ExchangeError, match="incomplete balance"):
        await a.fetch_balances()


# ------------------------------------------------------------------ orders
async def test_prepare_then_submit_sends_client_id_once() -> None:
    a, client = adapter()
    r = await a.prepare_order(order_req(amount="0.0012345", price="60000.009"))
    assert r.amount == D("0.00123") and r.price == D("60000.00")
    client.responses["create_order"] = lambda *args: ccxt_order(
        clientOrderId=args[5]["clientOrderId"], amount=0.00123, price=60000.0)
    snap = await a.submit_order(r)
    assert client.count("create_order") == 1
    _, args = client.calls[-1]
    assert args[:5] == ("BTC/USDT", "limit", "buy", 0.00123, 60000.0)
    assert args[5] == {"clientOrderId": r.client_order_id}
    assert snap.client_order_id == r.client_order_id
    assert snap.status is OrderStatus.OPEN and snap.amount == D("0.00123")


async def test_submit_refuses_unprepared_order() -> None:
    a, client = adapter()
    with pytest.raises(InvalidOrder):
        await a.submit_order(order_req(amount="0.0012345"))
    assert client.count("create_order") == 0


@pytest.mark.parametrize("exc", [cx.RequestTimeout("t"), cx.NetworkError("reset"),
                                 cx.ExchangeNotAvailable("502"), TimeoutError(),
                                 cx.BadResponse("html"), cx.DDoSProtection("cf"),
                                 RuntimeError("bug")])
async def test_ambiguous_submit_raises_unknown_and_never_retries(exc: BaseException) -> None:
    a, client = adapter()
    client.errors["create_order"] = exc
    r = order_req()
    with pytest.raises(OrderStateUnknown) as info:
        await a.submit_order(r)
    assert info.value.client_order_id == r.client_order_id
    assert client.count("create_order") == 1  # exactly one attempt


@pytest.mark.parametrize(("exc", "expected"), [
    (cx.InsufficientFunds("x"), InsufficientFunds),
    (cx.InvalidOrder("x"), InvalidOrder),
    (cx.ExchangeError("x"), OrderRejected),
    (cx.AuthenticationError("x"), AuthenticationFailed),
    (cx.PermissionDenied("x"), PermissionDenied),
])
async def test_definitive_rejections(exc: BaseException, expected: type[Exception]) -> None:
    a, client = adapter()
    client.errors["create_order"] = exc
    with pytest.raises(expected):
        await a.submit_order(order_req())
    assert client.count("create_order") == 1


async def test_cancelled_task_during_submit_is_unknown() -> None:
    a, client = adapter()
    client.errors["create_order"] = asyncio.CancelledError()
    with pytest.raises(OrderStateUnknown):
        await a.submit_order(order_req())


async def test_ack_without_status_reads_back_once() -> None:
    a, client = adapter(ExchangeCode.KRAKEN)
    client.responses["create_order"] = {"id": "OABC", "status": None}
    client.responses["fetch_order"] = ccxt_order(id="OABC", clientOrderId=None, filled=0.0005)
    r = order_req()
    snap = await a.submit_order(r)
    assert snap.status is OrderStatus.PARTIALLY_FILLED
    assert snap.client_order_id == r.client_order_id


async def test_ack_without_status_and_failed_readback_is_unknown() -> None:
    a, client = adapter(ExchangeCode.KRAKEN)
    client.responses["create_order"] = {"id": "OABC"}
    client.errors["fetch_order"] = cx.RequestTimeout("t")
    with pytest.raises(OrderStateUnknown):
        await a.submit_order(order_req())
    assert client.count("create_order") == 1


async def test_ack_without_id_is_unknown() -> None:
    a, client = adapter()
    client.responses["create_order"] = {}
    with pytest.raises(OrderStateUnknown):
        await a.submit_order(order_req())


async def test_post_only_and_ioc_params() -> None:
    from nexus_exchange.models import TimeInForce
    a, client = adapter()
    client.responses["create_order"] = lambda *args: ccxt_order()
    r = OrderRequest(client_order_id=new_client_order_id(), symbol="BTC/USDT", side=Side.BUY,
                     amount=D("0.001"), price=D("60000"), post_only=True,
                     time_in_force=TimeInForce.IOC)
    await a.submit_order(r)
    params = client.calls[-1][1][5]
    assert params["postOnly"] is True and params["timeInForce"] == "IOC"


# ------------------------------------------------------------------ lookup by client id
async def test_native_lookup_binance() -> None:
    a, client = adapter(ExchangeCode.BINANCE)
    cid = new_client_order_id()
    client.responses["fetch_order"] = ccxt_order(clientOrderId=cid, status="closed",
                                                 filled=0.001, average=59990.0,
                                                 fee={"cost": 0.06, "currency": "USDT"})
    snap = await a.find_order_by_client_id(cid, "BTC/USDT")
    assert snap is not None and snap.status is OrderStatus.FILLED
    assert snap.fee_cost == D("0.06") and snap.average_price == D("59990.0")
    assert client.calls[-1] == ("fetch_order", (None, "BTC/USDT", {"clientOrderId": cid}))


async def test_native_lookup_not_found_returns_none() -> None:
    a, client = adapter(ExchangeCode.BINANCE)
    client.errors["fetch_order"] = cx.OrderNotFound("no")
    assert await a.find_order_by_client_id(new_client_order_id(), "BTC/USDT") is None


async def test_lookup_failure_raises_not_none() -> None:
    a, client = adapter(ExchangeCode.BINANCE)
    client.errors["fetch_order"] = cx.RequestTimeout("t")
    with pytest.raises(ExchangeUnavailable):
        await a.find_order_by_client_id(new_client_order_id(), "BTC/USDT")


@pytest.mark.parametrize("code", [ExchangeCode.KRAKEN, ExchangeCode.COINBASE])
async def test_scan_lookup(code: ExchangeCode) -> None:
    a, client = adapter(code)
    cid = new_client_order_id()
    client.responses["fetch_open_orders"] = [ccxt_order(id="1", clientOrderId="other")]
    client.responses["fetch_closed_orders"] = [
        ccxt_order(id="2", clientOrderId=cid, status="canceled", filled=0.0004)]
    now = datetime(2026, 6, 1, tzinfo=UTC)
    snap = await a.find_order_by_client_id(cid, "BTC/USDT", now=now)
    assert snap is not None and snap.exchange_order_id == "2"
    assert snap.status is OrderStatus.CANCELED and snap.filled == D("0.0004")
    since = client.calls[-1][1][1]
    assert since == int((now - timedelta(hours=48)).timestamp() * 1000)
    client.responses["fetch_closed_orders"] = []
    assert await a.find_order_by_client_id(cid, "BTC/USDT", now=now) is None


async def test_unrecognized_status_raises() -> None:
    a, client = adapter()
    client.responses["fetch_order"] = ccxt_order(status="weird")
    with pytest.raises(ExchangeError, match="unrecognized"):
        await a.fetch_order("123", "BTC/USDT")


async def test_mixed_fee_currencies_left_unknown() -> None:
    a, client = adapter()
    client.responses["fetch_order"] = ccxt_order(
        fees=[{"cost": 0.1, "currency": "USDT"}, {"cost": 0.0001, "currency": "BNB"}])
    snap = await a.fetch_order("123", "BTC/USDT")
    assert snap.fee_cost is None and snap.fee_asset is None


async def test_fills() -> None:
    a, client = adapter()
    client.responses["fetch_my_trades"] = [{
        "id": "t1", "order": "123", "symbol": "BTC/USDT", "side": "buy", "price": 60000.1,
        "amount": 0.0005, "fee": {"cost": 0.03, "currency": "USDT"}, "takerOrMaker": "maker",
        "timestamp": 1_767_225_600_000}]
    fills = await a.fetch_fills("BTC/USDT")
    assert fills[0].price == D("60000.1") and fills[0].is_maker is True
    assert fills[0].timestamp == datetime(2026, 1, 1, tzinfo=UTC)


# ------------------------------------------------------------------ health / misc
async def test_health_check() -> None:
    a, client = adapter()
    client.responses["fetch_time"] = 1
    assert (await a.health_check()).ok is True
    client.errors["fetch_time"] = cx.ExchangeNotAvailable("down")
    status = await a.health_check()
    assert status.ok is False and status.detail == "ExchangeUnavailable"


async def test_close_and_repr() -> None:
    a, client = adapter()
    await a.close()
    assert client.closed
    assert "secret" not in repr(a).lower()


def test_error_mapping_reads_vs_submit() -> None:
    assert isinstance(map_ccxt_error(cx.RateLimitExceeded("x"), exchange="b"), RateLimited)
    assert isinstance(map_ccxt_error(cx.OrderNotFound("x"), exchange="b"), OrderNotFound)
    assert isinstance(map_ccxt_error(cx.NotSupported("x"), exchange="b"), UnsupportedFeature)
    assert isinstance(map_ccxt_error(cx.RequestTimeout("x"), exchange="b"), ExchangeUnavailable)
    e = map_ccxt_error(cx.RequestTimeout("x"), exchange="b", during_submit=True,
                       client_order_id="c")
    assert isinstance(e, OrderStateUnknown)


def test_decimal_to_wire() -> None:
    assert decimal_to_wire(D("0.00123"), field="a") == 0.00123
    assert decimal_to_wire(D("60000.00"), field="p") == 60000.0
    with pytest.raises(InvalidOrder):
        decimal_to_wire(D("0.12345678901234567890123"), field="a")


# ------------------------------------------------------------------ factory
def test_factory_spot_config_and_credentials_not_leaked() -> None:
    a = build_adapter(ExchangeCode.BINANCE, api_key="KEY123", secret="SECRET456")
    client = a._client  # type: ignore[attr-defined]
    assert client.options["defaultType"] == "spot"
    assert client.enableRateLimit is True
    assert "SECRET456" not in repr(a)


def test_factory_refuses_missing_sandbox() -> None:
    with pytest.raises(UnsupportedFeature):
        build_adapter(ExchangeCode.KRAKEN, sandbox=True)
    with pytest.raises(UnsupportedFeature):
        build_adapter(ExchangeCode.COINBASE, sandbox=True)


def test_factory_binance_sandbox_uses_testnet() -> None:
    a = build_adapter(ExchangeCode.BINANCE, sandbox=True)
    urls = str(a._client.urls["api"])  # type: ignore[attr-defined]
    assert "testnet" in urls


def test_adapter_interface_has_no_margin_or_withdraw_methods() -> None:
    forbidden = ("withdraw", "transfer", "borrow", "leverage", "margin", "futures", "short")
    names = [n for n in dir(CcxtSpotAdapter) if not n.startswith("__")]
    assert not [n for n in names if any(f in n.lower() for f in forbidden)]
