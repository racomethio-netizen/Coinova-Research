"""Binance USD-M adapter: exact request parameters and every safety path, with a recording
transport. These tests make no network calls."""

from __future__ import annotations

from decimal import Decimal as D
from typing import Any

import pytest
from nexus_exchange.binance_futures import (
    BinanceFuturesAdapter,
    ProtectionFailed,
    new_client_order_id,
    open_protected_position,
)
from nexus_exchange.errors import ExchangeError, InvalidOrder, OrderStateUnknown


class Recorder:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.responses: dict[str, Any] = {}
        self.errors: dict[str, list[BaseException]] = {}

    async def call(self, name: str, params: dict[str, Any]) -> Any:
        self.calls.append((name, dict(params)))
        if self.errors.get(name):
            raise self.errors[name].pop(0)
        resp = self.responses.get(name)
        return resp(params) if callable(resp) else resp

    def last(self, name: str) -> dict[str, Any]:
        return [p for n, p in self.calls if n == name][-1]

    def count(self, name: str) -> int:
        return sum(1 for n, _ in self.calls if n == name)


EXCHANGE_INFO = {
    "symbols": [
        {
            "symbol": "BTCUSDT",
            "contractType": "PERPETUAL",
            "quoteAsset": "USDT",
            "status": "TRADING",
            "filters": [
                {"filterType": "PRICE_FILTER", "tickSize": "0.10"},
                {"filterType": "LOT_SIZE", "stepSize": "0.001", "minQty": "0.001"},
                {"filterType": "MARKET_LOT_SIZE", "stepSize": "0.001"},
                {"filterType": "MIN_NOTIONAL", "notional": "5"},
            ],
        }
    ]
}


@pytest.fixture
def rec() -> Recorder:
    r = Recorder()
    r.responses["fapiPrivateGetPositionSideDual"] = {"dualSidePosition": False}
    r.responses["fapiPublicGetExchangeInfo"] = EXCHANGE_INFO
    return r


@pytest.fixture
def ad(rec: Recorder) -> BinanceFuturesAdapter:
    return BinanceFuturesAdapter(rec)


async def test_rules_parsed_as_exact_decimals(ad: BinanceFuturesAdapter) -> None:
    rules = await ad.rules("BTC/USDT:USDT")
    assert rules.tick_size == D("0.10") and rules.step_size == D("0.001")
    assert rules.min_notional == D("5")


async def test_rules_refuse_non_perpetual_or_non_usdt(rec: Recorder, ad: BinanceFuturesAdapter) -> None:
    bad = {"symbols": [{**EXCHANGE_INFO["symbols"][0], "contractType": "CURRENT_QUARTER"}]}
    rec.responses["fapiPublicGetExchangeInfo"] = bad
    with pytest.raises(InvalidOrder):
        await ad.rules("BTC/USDT:USDT")


async def test_isolated_margin_and_hedge_mode_refusal(rec: Recorder, ad: BinanceFuturesAdapter) -> None:
    await ad.ensure_isolated("BTC/USDT:USDT")
    assert rec.last("fapiPrivatePostMarginType") == {"symbol": "BTCUSDT", "marginType": "ISOLATED"}
    # "already isolated" (-4046) is not an error
    rec.errors["fapiPrivatePostMarginType"] = [ExchangeError("binance -4046 No need to change margin type")]
    await ad.ensure_isolated("BTC/USDT:USDT")
    rec.responses["fapiPrivateGetPositionSideDual"] = {"dualSidePosition": True}
    with pytest.raises(InvalidOrder, match="one-way"):
        await ad.ensure_isolated("BTC/USDT:USDT")


async def test_leverage_only_approved_tiers_and_never_automatic(
    rec: Recorder, ad: BinanceFuturesAdapter
) -> None:
    await ad.set_leverage("BTC/USDT:USDT", 2)
    assert rec.last("fapiPrivatePostLeverage") == {"symbol": "BTCUSDT", "leverage": 2}
    for bad in (1, 6, 10, 20, 50):  # only the 2-5x tiers are approved
        with pytest.raises(InvalidOrder):
            await ad.set_leverage("BTC/USDT:USDT", bad)
    assert rec.count("fapiPrivatePostLeverage") == 1


async def test_entry_uses_decimal_strings_and_our_client_id(rec: Recorder, ad: BinanceFuturesAdapter) -> None:
    cid = await ad.submit_entry(symbol="BTC/USDT:USDT", side="BUY", quantity=D("0.123"))
    p = rec.last("fapiPrivatePostOrder")
    assert p["type"] == "MARKET" and p["quantity"] == "0.123" and p["side"] == "BUY"
    assert p["newClientOrderId"] == cid and "price" not in p
    assert "positionSide" not in p  # one-way mode
    limit = await ad.submit_entry(symbol="BTC/USDT:USDT", side="SELL", quantity=D("1"), price=D("65000.5"))
    q = rec.last("fapiPrivatePostOrder")
    assert q["type"] == "LIMIT" and q["timeInForce"] == "GTC" and q["price"] == "65000.5"
    assert limit != cid


async def test_protective_stop_closes_whole_position_on_mark_price(
    rec: Recorder, ad: BinanceFuturesAdapter
) -> None:
    await ad.submit_protective(
        symbol="BTC/USDT:USDT", close_side="sell", kind="stop", trigger_price=D("63000")
    )
    p = rec.last("fapiPrivatePostOrder")
    assert p["type"] == "STOP_MARKET" and p["closePosition"] == "true"
    assert p["stopPrice"] == "63000" and p["workingType"] == "MARK_PRICE"
    assert "quantity" not in p and "reduceOnly" not in p  # closePosition covers the position
    assert p["side"] == "SELL"
    with pytest.raises(InvalidOrder):
        await ad.submit_protective(
            symbol="BTC/USDT:USDT", close_side="SELL", kind="trailing", trigger_price=D(1)
        )


async def test_timeout_on_submit_is_unknown_and_never_resubmitted(
    rec: Recorder, ad: BinanceFuturesAdapter
) -> None:
    rec.errors["fapiPrivatePostOrder"] = [TimeoutError("read timed out")]
    with pytest.raises(OrderStateUnknown) as info:
        await ad.submit_entry(symbol="BTC/USDT:USDT", side="BUY", quantity=D("0.1"))
    assert info.value.client_order_id
    assert rec.count("fapiPrivatePostOrder") == 1  # exactly one attempt


async def test_order_status_by_our_client_id(rec: Recorder, ad: BinanceFuturesAdapter) -> None:
    rec.responses["fapiPrivateGetOrder"] = {
        "status": "FILLED",
        "executedQty": "0.100",
        "avgPrice": "64000",
        "origQty": "0.100",
        "type": "MARKET",
        "clientOrderId": "x",
    }
    st = await ad.order_status("BTC/USDT:USDT", "x")
    assert rec.last("fapiPrivateGetOrder") == {"symbol": "BTCUSDT", "origClientOrderId": "x"}
    assert st is not None and st["status"] == "FILLED" and st["executed_qty"] == D("0.100")
    rec.errors["fapiPrivateGetOrder"] = [ExchangeError("binance -2013 Order does not exist.")]
    assert await ad.order_status("BTC/USDT:USDT", "gone") is None


async def test_positions_sign_liquidation_and_distance(rec: Recorder, ad: BinanceFuturesAdapter) -> None:
    rec.responses["fapiPrivateGetPositionRisk"] = [
        {
            "symbol": "BTCUSDT",
            "positionAmt": "-0.5",
            "entryPrice": "65000",
            "markPrice": "64000",
            "liquidationPrice": "71000",
            "leverage": "5",
            "marginType": "isolated",
            "unRealizedProfit": "500",
        },
        {
            "symbol": "ETHUSDT",
            "positionAmt": "0",
            "entryPrice": "0",
            "markPrice": "3000",
            "liquidationPrice": "0",
            "leverage": "2",
            "marginType": "isolated",
            "unRealizedProfit": "0",
        },
    ]
    pos = await ad.positions()
    assert len(pos) == 1  # flat positions are skipped
    p = pos[0]
    assert p.side == "short" and p.quantity == D("-0.5") and p.margin_type == "isolated"
    assert p.liquidation_distance == (D(71000) - D(64000)) / D(64000)


async def test_funding_fees_summed_from_income(rec: Recorder, ad: BinanceFuturesAdapter) -> None:
    rec.responses["fapiPrivateGetIncome"] = [{"income": "-1.20"}, {"income": "0.30"}]
    assert await ad.funding_paid("BTC/USDT:USDT") == D("-0.90")
    assert rec.last("fapiPrivateGetIncome")["incomeType"] == "FUNDING_FEE"


async def test_protected_open_places_stop_and_target(rec: Recorder, ad: BinanceFuturesAdapter) -> None:
    alerts: list[str] = []

    async def alert(msg: str) -> None:
        alerts.append(msg)

    rec.responses["fapiPrivateGetOrder"] = {
        "status": "FILLED",
        "executedQty": "0.2",
        "avgPrice": "64000",
        "origQty": "0.2",
        "type": "MARKET",
        "clientOrderId": "e",
    }
    res = await open_protected_position(
        ad,
        symbol="BTC/USDT:USDT",
        side="long",
        quantity=D("0.2"),
        stop_price=D("63000"),
        take_profit_price=D("67000"),
        alert=alert,
    )
    assert res.state == "protected" and res.filled_qty == D("0.2")
    types = [p["type"] for n, p in rec.calls if n == "fapiPrivatePostOrder"]
    assert types == ["MARKET", "STOP_MARKET", "TAKE_PROFIT_MARKET"]
    assert alerts == []


async def test_stop_failure_closes_position_and_alerts(rec: Recorder, ad: BinanceFuturesAdapter) -> None:
    alerts: list[str] = []

    async def alert(msg: str) -> None:
        alerts.append(msg)

    rec.responses["fapiPrivateGetOrder"] = {
        "status": "FILLED",
        "executedQty": "0.2",
        "avgPrice": "64000",
        "origQty": "0.2",
        "type": "MARKET",
        "clientOrderId": "e",
    }
    # Entry accepted, then the stop placement is rejected.
    real_orders = [None, ExchangeError("binance -2021 Order would immediately trigger."), None]

    async def scripted(name: str, params: dict[str, Any]) -> Any:
        if name == "fapiPrivatePostOrder":
            rec.calls.append((name, dict(params)))
            if params["type"] == "STOP_MARKET":
                raise real_orders[1]  # type: ignore[misc]
            return {}
        return await rec.call(name, params)

    ad._t = type("T", (), {"call": staticmethod(scripted)})()  # route through scripted
    with pytest.raises(ProtectionFailed):
        await open_protected_position(
            ad,
            symbol="BTC/USDT:USDT",
            side="long",
            quantity=D("0.2"),
            stop_price=D("63000"),
            take_profit_price=None,
            alert=alert,
        )
    last = [p for n, p in rec.calls if n == "fapiPrivatePostOrder"][-1]
    assert last["type"] == "MARKET" and last["reduceOnly"] == "true" and last["quantity"] == "0.2"
    assert last["side"] == "SELL"  # closes the long
    assert alerts and "position closed" in alerts[0]


def test_client_order_ids_fit_binance_limit() -> None:
    for prefix in ("ent", "stp", "tp", "red"):
        cid = new_client_order_id(prefix)
        assert len(cid) <= 36 and cid.startswith(prefix)
    assert new_client_order_id() != new_client_order_id()


def test_transport_only_allows_fapi_and_hides_credentials() -> None:
    from nexus_exchange.binance_futures_transport import CcxtFuturesTransport

    t = CcxtFuturesTransport("KEY123456", "SECRET-VALUE-999")
    assert "SECRET" not in repr(t) and "KEY123456" not in repr(t)


async def test_transport_refuses_non_futures_calls() -> None:
    from nexus_exchange.binance_futures_transport import CcxtFuturesTransport

    t = CcxtFuturesTransport("k", "s")
    with pytest.raises(PermissionError):
        await t.call("privatePostOrder", {})
    await t.close()
