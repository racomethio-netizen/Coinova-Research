"""Spot grid bot: levels, validation (fees, minimum order, bounded worst case), fills,
round-trip profit, no size increase, above-range behaviour, stop-loss and restore."""

from __future__ import annotations

from decimal import Decimal

import pytest
from nexus_strategies import grid

D = Decimal


def cfg(**kw):
    base = dict(symbol="BTC/USDT", lower=D("90000"), upper=D("99000"), levels=10,
                capital=D("1000"))
    base.update(kw)
    return grid.GridConfig(**base)


def test_geometric_levels_have_equal_percentage_steps():
    lv = grid.build_levels(cfg())
    assert lv[0] == D("90000") and lv[-1] == D("99000") and len(lv) == 10
    steps = [lv[i + 1] / lv[i] - 1 for i in range(9)]
    assert max(steps) - min(steps) < D("0.000001")


def test_validation_refuses_unprofitable_or_unbounded_grids():
    assert grid.validate(cfg()) == []
    tight = grid.validate(cfg(lower=D("98900"), upper=D("99000"), levels=20))
    assert any("fees" in r for r in tight)
    tiny = grid.validate(cfg(capital=D("20")))
    assert any("minimum order" in r for r in tiny)
    wide = grid.validate(cfg(lower=D("60000"), upper=D("99000"), stop_loss_pct=D("0.2")))
    assert any("worst-case loss" in r for r in wide)


def test_worst_case_is_the_loss_when_every_buy_fills_then_stop():
    c = cfg()
    lv = grid.build_levels(c)
    wc = grid.worst_case_loss(c, lv)
    # Replay it: price falls through every level, then to the stop.
    st = grid.start(c, D("99500"))
    for p in reversed(lv[:-1]):
        grid.step(st, p - 1, p - D("0.5"))
    grid.step(st, c.stop_price - 1, c.stop_price)
    assert st.status == "STOPPED"
    loss = -D(grid.mark(st, c.stop_price - 1)["pnl"])
    assert abs(loss - wc) / wc < D("0.01")                # replay matches the bound
    assert loss <= c.capital * c.max_loss_fraction


def test_start_places_buys_only_below_price_and_never_buys_at_market():
    st = grid.start(cfg(), D("94000"))
    assert st.base == 0 and st.buys == 0
    prices = [o.price for o in st.orders.values()]
    assert prices and all(o.side == "buy" for o in st.orders.values())
    assert max(prices) < D("94000")
    per = [o.qty * o.price for o in st.orders.values()]
    assert max(per) - min(per) < D("1")       # equal size, up to one 0.00001 BTC lot step


def test_round_trip_earns_one_step_minus_fees_and_rebuys():
    c = cfg()
    st = grid.start(c, D("94000"))
    lv = st.levels
    i = max(o.level for o in st.orders.values())          # highest buy
    fills = grid.step(st, lv[i] - 2, lv[i] - 1)            # ask trades through the buy
    assert [f.side for f in fills] == ["buy"] and ("sell", i + 1) in st.orders
    assert grid.step(st, lv[i + 1], lv[i + 1] + 1) == []   # touching is not a fill
    fills = grid.step(st, lv[i + 1] + 1, lv[i + 1] + 2)    # bid goes above the sell
    assert [f.side for f in fills] == ["sell"]
    qty = fills[0].qty
    expected = qty * (lv[i + 1] - lv[i]) - qty * lv[i] * c.maker_fee - qty * lv[i + 1] * c.maker_fee
    assert fills[0].profit == pytest.approx(expected, abs=D("0.000001"))
    assert fills[0].profit > 0 and st.round_trips == 1
    assert ("buy", i) in st.orders                         # the buy is placed again
    assert st.base == 0


def test_capital_is_never_exceeded_and_sizes_never_grow():
    c = cfg()
    st = grid.start(c, D("98000"))
    first = {k: o.qty * o.price for k, o in st.orders.items()}
    for p in [D(x) for x in range(97000, 89000, -250)]:
        grid.step(st, p - 1, p)
        m = grid.mark(st, p - 1)
        assert D(m["equity"]) <= c.capital * D("1.0001")    # no extra money appears
        assert st.quote >= 0
    for o in st.orders.values():
        if o.side == "buy":
            assert o.qty * o.price <= max(first.values()) * D("1.0001")


def test_above_range_places_no_new_buys_then_resumes():
    c = cfg()
    st = grid.start(c, D("98900"))
    n = len(st.orders)
    grid.step(st, D("99100"), D("99101"))
    assert st.status == "ABOVE_RANGE" and len(st.orders) == n
    grid.step(st, D("98000"), D("98001"))
    assert st.status == "RUNNING"


def test_stop_loss_sells_everything_and_stays_stopped():
    c = cfg()
    st = grid.start(c, D("95000"))
    grid.step(st, D("91000"), D("91001"))                  # some buys fill
    assert st.base > 0
    fills = grid.step(st, c.stop_price - 10, c.stop_price - 9)
    assert fills and fills[-1].reason == "stop-loss"
    assert st.status == "STOPPED" and st.base == 0 and not st.orders
    assert grid.step(st, D("95000"), D("95001")) == []    # no automatic restart


def test_state_round_trips_through_json():
    c = cfg()
    st = grid.start(c, D("94000"))
    grid.step(st, D("92000"), D("92001"))
    back = grid.GridState.from_json(c, st.to_json())
    assert back.to_json() == st.to_json()


def test_auto_range_bounds_width():
    lo, hi = grid.auto_range([D("100")], [D("99.9")], D("100"))
    assert hi / lo - 1 >= D("0.039")                       # widened to the minimum
    lo, hi = grid.auto_range([D("200")], [D("50")], D("100"))
    assert hi / lo - 1 <= D("0.151")                       # narrowed to the maximum
    assert lo < 100 < hi
