from __future__ import annotations

from decimal import Decimal as D
from typing import Any

from nexus_risk.futures_sizing import FuturesSizingInput, liquidation_price, size_futures_position


def base(**kw: Any) -> FuturesSizingInput:
    d: dict[str, Any] = dict(
        side="long", equity=D(10_000), available_margin=D(10_000), entry=D(100), stop=D(98),
        leverage=2, risk_fraction=D("0.01"), fee_rate=D("0.0005"), slippage_rate=D("0.0002"),
        maintenance_margin_rate=D("0.004"), funding_rate_per_8h=D("0.0001"),
        expected_hold_hours=D(8), total_margin_in_use=D(0), asset_margin_in_use=D(0),
        qty_step=D("0.001"))
    d.update(kw)
    return FuturesSizingInput(**d)


def test_size_comes_from_risk_not_leverage() -> None:
    a = size_futures_position(base(leverage=2))
    b = size_futures_position(base(leverage=5))
    assert a.approved and b.approved
    assert a.quantity == b.quantity  # leverage changes margin, never size
    assert a.margin == a.notional / 2 and b.margin == b.notional / 5
    # per-unit loss = 2 (stop distance) + (100 + 98) * 0.07% (fees and slippage) = 2.1386
    # qty = 100 / 2.1386 = 46.759, floored to the 0.001 step.
    assert a.quantity == D("46.759") and a.risk_amount <= D(100)


def test_leverage_must_be_a_selectable_tier() -> None:
    assert not size_futures_position(base(leverage=3)).approved
    assert not size_futures_position(base(leverage=50)).approved


def test_liquidation_formulas_are_symmetric() -> None:
    assert liquidation_price("long", D(100), 10, D("0.004")) == D("90.4")
    assert liquidation_price("short", D(100), 10, D("0.004")) == D("109.6")


def test_short_side_mirrors_long() -> None:
    s = size_futures_position(base(side="short", stop=D(102)))
    assert s.approved and s.liquidation_price is not None and s.liquidation_price > D(100)


def test_stop_beyond_liquidation_is_refused_not_leveraged_up() -> None:
    # At 20x the liquidation sits ~5% away; a 6% stop must be refused, not fixed by raising leverage.
    r = size_futures_position(base(leverage=20, stop=D("94")))
    assert not r.approved and "too close to the stop" in (r.reason or "")
    assert "never raised automatically" in (r.reason or "")
    assert size_futures_position(base(leverage=2, stop=D("94"))).approved


def test_wrong_side_stop_refused() -> None:
    assert not size_futures_position(base(stop=D(101))).approved
    assert not size_futures_position(base(side="short", stop=D(99))).approved


def test_margin_caps_bind() -> None:
    r = size_futures_position(base(total_margin_in_use=D(4_900)))  # 50% cap nearly used
    assert r.approved and r.binding == "margin"
    assert r.margin <= D(100)  # only 100 of margin room remains
    refused = size_futures_position(base(total_margin_in_use=D(5_000)))
    assert not refused.approved


def test_risk_ceiling_is_enforced() -> None:
    assert not size_futures_position(base(risk_fraction=D("0.02"))).approved


def test_funding_that_eats_the_budget_is_refused() -> None:
    r = size_futures_position(base(funding_rate_per_8h=D("0.02"), expected_hold_hours=D(240)))
    assert not r.approved and "funding" in (r.reason or "")


def test_exchange_minimums() -> None:
    assert not size_futures_position(base(min_qty=D("100"))).approved
    assert not size_futures_position(base(min_notional=D("10000"))).approved
