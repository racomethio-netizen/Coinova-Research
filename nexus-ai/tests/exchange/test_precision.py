from __future__ import annotations

from decimal import Decimal as D

import pytest
from nexus_exchange.errors import InvalidOrder
from nexus_exchange.models import MarketRules, OrderRequest, OrderType, Side, new_client_order_id
from nexus_exchange.precision import normalize_order, round_price, validate_order

RULES = MarketRules(
    symbol="BTC/USDT", exchange_symbol="BTCUSDT", base="BTC", quote="USDT",
    price_tick=D("0.01"), amount_step=D("0.00001"), min_amount=D("0.00001"),
    max_amount=D("9000"), min_notional=D("5"),
)


def req(side: Side = Side.BUY, amount: str = "0.001234567", price: str | None = "65000.019",
        **kw: object) -> OrderRequest:
    return OrderRequest(client_order_id=new_client_order_id(), symbol="BTC/USDT", side=side,
                        amount=D(amount), price=D(price) if price else None, **kw)  # type: ignore[arg-type]


def test_buy_rounds_amount_and_price_down() -> None:
    out = normalize_order(req(Side.BUY), RULES)
    assert out.amount == D("0.00123")
    assert out.price == D("65000.01")


def test_sell_rounds_price_up_amount_down() -> None:
    out = normalize_order(req(Side.SELL), RULES)
    assert out.amount == D("0.00123")
    assert out.price == D("65000.02")


def test_exact_values_unchanged() -> None:
    out = normalize_order(req(amount="0.001", price="65000.00"), RULES)
    assert (out.amount, out.price) == (D("0.001"), D("65000.00"))


def test_amount_rounding_to_zero_rejected() -> None:
    with pytest.raises(InvalidOrder, match="rounds to zero"):
        normalize_order(req(amount="0.000001"), RULES)


def test_min_notional() -> None:
    with pytest.raises(InvalidOrder, match="notional"):
        normalize_order(req(amount="0.00007", price="65000"), RULES)  # 4.55 < 5
    normalize_order(req(amount="0.00008", price="65000"), RULES)  # 5.2 ok


def test_max_amount() -> None:
    with pytest.raises(InvalidOrder, match="above maximum"):
        normalize_order(req(amount="9000.00001", price="1"), RULES)


def test_validate_rejects_unrounded() -> None:
    with pytest.raises(InvalidOrder, match="multiple"):
        validate_order(req(amount="0.001234", price="65000"), RULES)
    with pytest.raises(InvalidOrder, match="multiple"):
        validate_order(req(amount="0.001", price="65000.001"), RULES)


def test_symbol_mismatch_and_inactive() -> None:
    eth = OrderRequest(client_order_id=new_client_order_id(), symbol="ETH/USDT", side=Side.BUY,
                       amount=D("1"), price=D("3000"))
    with pytest.raises(InvalidOrder, match="does not match"):
        validate_order(eth, RULES)
    from dataclasses import replace
    with pytest.raises(InvalidOrder, match="not active"):
        validate_order(req(amount="0.001", price="65000"), replace(RULES, active=False))


def test_market_order_needs_reference_price_for_notional() -> None:
    m = req(amount="0.001", price=None, order_type=OrderType.MARKET)
    with pytest.raises(InvalidOrder, match="without a price"):
        validate_order(m, RULES)
    validate_order(m, RULES, reference_price=D("65000"))


def test_round_price_half_tick_tick() -> None:
    rules = MarketRules("X/USDT", "XUSDT", "X", "USDT", price_tick=D("0.5"), amount_step=D("1"))
    assert round_price(D("10.74"), Side.BUY, rules) == D("10.5")
    assert round_price(D("10.74"), Side.SELL, rules) == D("11.0")


class TestOrderRequestValidation:
    def test_client_order_id_must_be_uuid4(self) -> None:
        for bad in ("abc", "X" * 36, "00000000-0000-0000-0000-000000000000"):
            with pytest.raises(ValueError):
                OrderRequest(client_order_id=bad, symbol="BTC/USDT", side=Side.BUY,
                             amount=D(1), price=D(1))

    def test_limit_requires_price(self) -> None:
        with pytest.raises(ValueError):
            req(price=None)

    def test_positive_amount(self) -> None:
        with pytest.raises(ValueError):
            req(amount="0")

    def test_market_not_post_only(self) -> None:
        with pytest.raises(ValueError):
            req(price=None, order_type=OrderType.MARKET, post_only=True)
