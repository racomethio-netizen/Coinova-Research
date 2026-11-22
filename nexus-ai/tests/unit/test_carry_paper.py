"""Forward carry paper test: delta neutrality, funding, rebalancing, costs, report."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal as D

from nexus_market.carry_paper import (
    CAPITAL,
    LEG,
    PERP_COST,
    SPOT_COST,
    Leg,
    Quote,
    apply_funding,
    enter,
    equity,
    needs_rebalance,
    rebalance,
    report,
)


def q(spot: str, perp: str, spread: str = "0") -> Quote:
    s, p, h = D(spot), D(perp), D(spread) / 2
    return Quote(s - h, s + h, p - h, p + h)


def test_entry_costs_and_exit_value() -> None:
    leg = enter(q("100", "100"), 0)
    assert leg.spot_qty == LEG / 100 and leg.perp_qty == LEG / 100
    entry_cost = LEG * (SPOT_COST + PERP_COST)
    exit_cost = LEG * (SPOT_COST + PERP_COST)
    assert leg.costs == entry_cost
    assert equity(leg, q("100", "100")) == CAPITAL / 2 - entry_cost - exit_cost


def test_delta_neutral_price_moves_cancel() -> None:
    leg = enter(q("100", "100"), 0)
    flat = equity(leg, q("100", "100"))
    up = equity(leg, q("130", "130"))
    down = equity(leg, q("70", "70"))
    # Only the exit-cost difference on a bigger/smaller notional changes equity.
    assert abs(up - flat) < LEG * D("0.3") * (SPOT_COST + PERP_COST) * 2
    assert abs(down - flat) < LEG * D("0.3") * (SPOT_COST + PERP_COST) * 2


def test_short_receives_positive_and_pays_negative_funding() -> None:
    leg = enter(q("100", "100"), 0)
    got = apply_funding(leg, D("0.0001"), D("100"), 1)
    assert got == D("0.25") and leg.funding == D("0.25")              # 25 x 100 x 0.0001
    paid = apply_funding(leg, D("-0.0002"), D("100"), 2)
    assert paid == D("-0.5") and leg.funding == D("-0.25") and leg.last_funding_ms == 2


def test_rebalance_restores_equal_notional_and_charges_turnover() -> None:
    leg = enter(q("100", "100"), 0)
    moved = q("120", "112")                       # basis widened: legs out of balance
    assert needs_rebalance(leg, moved)
    before = equity(leg, moved)
    cost = rebalance(leg, moved)
    assert abs(leg.spot_qty * moved.spot_mid - leg.perp_qty * moved.perp_mid) < D("0.000001")
    assert not needs_rebalance(leg, moved) and cost > 0
    after = equity(leg, moved)
    assert after < before and abs((before - after) - cost) < D("0.5")


def test_leg_json_round_trip() -> None:
    leg = enter(q("100", "101"), 123)
    assert Leg.from_json(leg.to_json()) == leg


def _events(days: int, daily_gain: str) -> list[dict]:
    start = datetime(2026, 11, 23, tzinfo=UTC)          # a Monday
    out, i = [], 0
    for sym in ("BTCUSDT", "ETHUSDT"):
        out.append({"id": i, "kind": "entry", "symbol": sym, "at": start,
                    "data": {"state": enter(q("100", "100"), 0).to_json()}})
        i += 1
    for h in range(days * 24):
        t = start + timedelta(hours=h)
        eq = D(5000) + D(daily_gain) * h / 24
        for sym in ("BTCUSDT", "ETHUSDT"):
            out.append({"id": i, "kind": "mark", "symbol": sym, "at": t,
                        "data": {"equity": str(eq), "state": enter(q("100", "100"), 0).to_json()}})
            i += 1
    return out


def test_report_annualises_only_after_a_week_and_counts_weeks() -> None:
    early = report(_events(3, "1"), datetime(2026, 11, 26, tzinfo=UTC))
    assert early["started"] and early["annualised"] is None and early["decision"] == "pending"
    later = report(_events(30, "1"), datetime(2026, 12, 23, tzinfo=UTC))
    assert later["complete_weeks"] >= 3 and later["positive_weeks"] == later["complete_weeks"]
    assert D(later["max_drawdown"]) == 0 and D(later["annualised"]) > 0
    assert report([], datetime(2026, 11, 23, tzinfo=UTC))["started"] is False
