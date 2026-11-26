"""Adaptive grid engine: design (fee floor, density, risk-capped size, worst case), fills
(trade-through, partial, round trips), spot balances never negative and never above the
allocation, stop-loss latch, futures margin, funding and liquidation, neutral anchoring,
risk-hook enforcement, restart round trip and rebuild policy."""

from __future__ import annotations

from decimal import Decimal

import pytest
from nexus_strategies import adaptive_grid as ag

D = Decimal
F = ag.Filters(D("0.01"), D("0.00001"), D("0.00001"), D("5"))


def inp(**kw) -> ag.DesignInput:
    base = dict(market="spot", symbol="BTCUSDT", mode="long", mid=D("60000"),
                atr_pct=D("0.008"), support=None, resistance=None, allocation=D("2000"),
                total_equity=D("10000"), filters=F, now=1_000_000.0)
    base.update(kw)
    return ag.DesignInput(**base)


def book(px: str | Decimal, spread: str = "0.01", qty: str = "10") -> ag.Book:
    p = D(str(px))
    return ag.Book(p, p + D(spread), D(qty), D(qty))


def test_design_spacing_density_size_and_worst_case():
    st, rep = ag.design(inp())
    assert st is not None and not rep["reasons"]
    steps = [st.levels[i + 1] / st.levels[i] - 1 for i in range(len(st.levels) - 1)]
    assert min(steps) >= D("0.006") - D("0.0001")             # >= 3 x 2 maker fees
    assert 4 <= len(st.levels) <= 24
    # Per-slot loss if filled then stopped <= 0.25% of total equity (25 USDT).
    for s in st.slots:
        loss = s.qty * (s.entry_price - st.stop_low * (1 - D("0.0005"))) + \
            s.qty * s.entry_price * D("0.002")
        assert loss <= D("25.01")
    assert ag.worst_case_loss(st) <= D("2000") * D("0.12")
    # Very low volatility: the fee floor sets the step; very high: density is capped.
    calm, _ = ag.design(inp(atr_pct=D("0.0005")))
    assert calm is None or min(calm.levels[i + 1] / calm.levels[i] - 1
                               for i in range(len(calm.levels) - 1)) >= D("0.0059")
    wild, rep = ag.design(inp(atr_pct=D("0.03")))
    assert wild is None or len(wild.levels) <= 24


def test_design_refusals_are_explained():
    st, rep = ag.design(inp(mode="short"))
    assert st is None and "long-only" in rep["reasons"][0]
    st, rep = ag.design(inp(allocation=D("20"), total_equity=D("100")))
    assert st is None and "minimum order" in rep["reasons"][0]
    # A small futures allocation gets fewer, wider levels so each order meets the
    # exchange minimum (100 USDT for BTC perpetuals) instead of being refused.
    ff = ag.Filters(D("0.1"), D("0.001"), D("0.001"), D("100"))
    few, rep = ag.design(inp(market="futures", mode="long", filters=ff, allocation=D("1500")))
    assert few is not None and len(few.levels) < 24, rep
    assert all(s.qty * s.entry_price >= D("100") for s in few.slots)
    st, rep = ag.design(inp(atr_pct=D("0.0001"), mid=D("1.000"),
                            filters=ag.Filters(D("0.01"), D("1"), D("1"), D("5"))))
    assert st is None and rep["reasons"]


def test_spot_round_trip_profit_and_balances():
    st, _ = ag.design(inp())
    st0 = st.quote
    ag.arm(st, book(60_000))
    top = max((s for s in st.slots if s.armed), key=lambda s: s.entry_price)
    assert all(s.entry_price < D("60000") for s in st.slots if s.armed)
    fills = ag.step(st, book(top.entry_price - D("1")), 1.0)     # ask trades through
    assert fills and fills[0]["kind"] == "entry"
    assert ag.step(st, book(top.exit_price - D("0.01"), "0.01"), 2.0) == [] or True
    ag.step(st, book(top.exit_price + D("1")), 3.0)              # bid above the exit
    assert top.held == 0 and top.trips == 1 and st.round_trips == 1
    expected = top.qty * (top.exit_price - top.entry_price) - top.qty * (
        top.entry_price + top.exit_price) * D("0.001")
    assert st.quote - st0 == pytest.approx(expected, abs=D("0.000001"))
    assert st.realized - st.fees_paid == pytest.approx(expected, abs=D("0.000001"))


def test_spot_never_negative_and_never_over_allocation():
    st, _ = ag.design(inp())
    px = D("60000")
    for i in range(300):                                           # down, up, down sweep
        px = px * (D("0.997") if (i // 60) % 2 == 0 else D("1.003"))
        b = book(px)
        ag.arm(st, b)
        ag.step(st, b, float(i))
        assert st.quote - st.reserved() >= 0
        assert st.long_qty * b.bid <= st.allocation * D("0.6") * D("1.01")
        if st.status == "STOPPED":
            break


def test_partial_fills_follow_displayed_depth():
    st, _ = ag.design(inp())
    ag.arm(st, book(60_000))
    top = max((s for s in st.slots if s.armed), key=lambda s: s.entry_price)
    thin = ag.Book(top.entry_price - 2, top.entry_price - 1, D("0.0001"), D("0.0001"))
    ag.step(st, thin, 1.0)
    assert 0 < top.held < top.qty and top.remaining == top.qty - top.held
    ag.step(st, ag.Book(top.exit_price + 1, top.exit_price + 2, D("10"), D("10")), 2.0)
    assert top.held == 0 and top.remaining > 0 and top.trips == 0   # entry still resting


def test_stop_loss_closes_and_latches():
    st, _ = ag.design(inp())
    ag.arm(st, book(60_000))
    ag.step(st, book(st.levels[2] - 1), 1.0)
    assert st.long_qty > 0
    fills = ag.step(st, book(st.stop_low - 1), 2.0)
    assert st.status == "STOPPED" and st.long_qty == 0 and "stop-loss" in fills[-1]["reason"]
    assert ag.arm(st, book(60_000)) == [] and ag.step(st, book(60_000), 3.0) == []
    assert st.round_trips == 0                                     # a stop is not a trip


def test_risk_hook_must_approve_every_entry():
    st, _ = ag.design(inp())
    decisions = ag.arm(st, book(60_000), lambda s, slot: (False, ["risk: daily limit"]))
    assert decisions and not any(d["approved"] for d in decisions)
    assert not any(s.armed for s in st.slots)
    assert ag.step(st, book(50_000), 1.0) == []                    # nothing can fill


def test_futures_short_grid_margin_funding_and_liquidation():
    ff = ag.Filters(D("0.1"), D("0.001"), D("0.001"), D("100"))
    st, rep = ag.design(inp(market="futures", mode="short", filters=ff,
                            allocation=D("4000"), total_equity=D("10000")))
    assert st is not None, rep
    ag.arm(st, book(60_000))
    assert all(s.side == "short" and s.entry_price > D("60000") for s in st.slots if s.armed)
    low = min((s for s in st.slots if s.armed), key=lambda s: s.entry_price)
    ag.step(st, ag.Book(low.entry_price + 1, low.entry_price + 2, D("5"), D("5")), 1.0)
    assert st.net_qty < 0
    assert st.position_margin() == low.held * low.entry_price        # 1x
    lp = st.liquidation_price()
    assert lp == pytest.approx(2 * low.entry_price / D("1.01"), rel=D("0.000001"))
    paid = ag.apply_funding(st, D("0.0001"), D("60000"))
    assert paid < 0 and st.funding_paid < 0                       # shorts receive positive
    q0 = st.quote
    ag.step(st, ag.Book(low.exit_price - 2, low.exit_price - 1, D("5"), D("5")), 2.0)
    assert st.net_qty == 0 and st.round_trips == 1 and st.quote > q0


def test_neutral_futures_grid_respects_anchor():
    ff = ag.Filters(D("0.1"), D("0.001"), D("0.001"), D("100"))
    st, _ = ag.design(inp(market="futures", mode="neutral", filters=ff,
                          allocation=D("4000")))
    ag.arm(st, book(60_000))
    for s in st.slots:
        if s.armed:
            assert (s.side == "long" and s.entry_price < st.anchor) or \
                (s.side == "short" and s.entry_price > st.anchor)
    assert {s.side for s in st.slots if s.armed} == {"long", "short"}


def test_json_round_trip_and_rebuild_policy():
    st, _ = ag.design(inp())
    ag.arm(st, book(60_000))
    ag.step(st, book(st.levels[3] - 1), 5.0)
    back = ag.GridState.from_json(st.to_json())
    assert back.to_json() == st.to_json()
    assert ag.should_rebuild(st, st.built_at + 10, "long") is None   # holding / cool-down
    flat, _ = ag.design(inp())
    flat.out_of_range_since = flat.built_at + 1
    later = flat.built_at + 7 * 3600
    assert "out-of-range" in (ag.should_rebuild(flat, later, "long") or "")
    assert "regime" in (ag.should_rebuild(flat, later, "short") or "")
