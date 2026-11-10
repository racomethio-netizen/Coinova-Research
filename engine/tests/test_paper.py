"""Paper-trading math (hand-computed) and invariants."""

from decimal import Decimal as D

import pytest
from hypothesis import given
from hypothesis import strategies as st

from nexus_engine.paper.fills import FillRejectedError, Position, apply_buy, apply_sell, fill_price, protection_trigger
from nexus_engine.signals.risk import CostModel

C = CostModel()  # 0.1% fee, 0.05% slippage per side


def test_fill_prices() -> None:
    assert fill_price("buy", "market", D(100), None, C) == D("100.05000000")
    assert fill_price("sell", "market", D(100), None, C) == D("99.95000000")
    assert fill_price("buy", "limit", D(101), D(100), C) is None  # not marketable
    assert fill_price("buy", "limit", D("99.99"), D(100), C) == D("100.00000000")  # never worse than the limit
    assert fill_price("buy", "limit", D(90), D(100), C) == D("90.04500000")
    assert fill_price("sell", "limit", D(99), D(100), C) is None
    assert fill_price("sell", "limit", D(110), D(100), C) == D("109.94500000")


def test_buy_then_stop_out_hand_computed() -> None:
    cash, pos, f = apply_buy(D(10_000), Position(), D("100.05"), D(10), C)
    assert (f.fee, cash, pos.cost_basis, pos.avg_entry) == (D("1.00050000"), D("8998.49950000"), D("1001.50050000"), D("100.15005000"))
    cash, pos, f = apply_sell(cash, pos, D("93.953"), D(10), C)
    assert f.fee == D("0.93953000") and f.realized_pnl == D("-62.91003000")
    assert cash == D("9937.08997000") and pos == Position(D(0), D(0), D("-62.91003000"))


def test_partial_sell_uses_average_cost() -> None:
    cash, pos, _ = apply_buy(D(10_000), Position(), D(100), D(4), CostModel(D(0), D(0)))
    _, pos, _ = apply_buy(cash, pos, D(200), D(4), CostModel(D(0), D(0)))
    assert pos.avg_entry == D("150.00000000")
    _, pos2, f = apply_sell(D(0), pos, D(180), D(2), CostModel(D(0), D(0)))
    assert f.realized_pnl == D("60.00000000") and pos2.quantity == D(6) and pos2.cost_basis == D("900.00000000")


def test_rejections() -> None:
    with pytest.raises(FillRejectedError, match="insufficient_funds"):
        apply_buy(D(100), Position(), D(100), D(1), C)
    with pytest.raises(FillRejectedError, match="insufficient_position"):
        apply_sell(D(0), Position(D(1), D(100), D(0)), D(100), D(2), C)


def test_protection_triggers() -> None:
    assert protection_trigger(D(95), D(95), D(120)) == "stop_loss"
    assert protection_trigger(D(121), D(95), D(120)) == "take_profit"
    assert protection_trigger(D(100), D(95), D(120)) is None and protection_trigger(D(1), None, None) is None


@given(st.decimals(min_value=D("0.01"), max_value=D(100_000), places=8), st.decimals(min_value=D("0.000001"), max_value=D(10), places=6))
def test_round_trip_at_the_same_price_costs_exactly_fees_and_never_overdraws(price: D, qty: D) -> None:
    start = D(1_000_000_000)
    cash, pos, buy = apply_buy(start, Position(), price, qty, C)
    assert cash >= 0
    cash, pos, sell = apply_sell(cash, pos, price, qty, C)
    assert pos.quantity == 0 and pos.cost_basis == 0
    assert start - cash == buy.fee + sell.fee and pos.realized_pnl == -(buy.fee + sell.fee)
