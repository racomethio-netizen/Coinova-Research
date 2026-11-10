"""Execution model scenarios (ADR-006), hand-built bars."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal as D

from nexus_engine.signals.execution import Bar, Plan, resolve, trade_result
from nexus_engine.signals.risk import CostModel

T = datetime(2026, 1, 1, tzinfo=UTC)
H = timedelta(hours=1)
ZERO = CostModel(D(0), D(0))
BUY = Plan("BUY", D(99), D(100), D(95), D(110), entry_bars=3, horizon_bars=4, costs=ZERO)
SELL = Plan("SELL", D(100), D(101), D(105), D(90), entry_bars=3, horizon_bars=4, costs=ZERO)


def bars(*ohlc: tuple[str, str, str, str]) -> list[Bar]:
    return [Bar(T + H * i, D(o), D(h), D(lo), D(c)) for i, (o, h, lo, c) in enumerate(ohlc)]


def run(plan: Plan, *ohlc: tuple[str, str, str, str]):
    return resolve(plan, bars(*ohlc), "1h", T)


def types(r) -> list[str]:
    return [e.type for e in r.events]


def test_entry_at_open_inside_range_then_target() -> None:
    r = run(BUY, ("99.5", "100", "99", "99.8"), ("100", "111", "99.5", "110"))
    assert types(r) == ["entered", "tp1_hit"] and r.outcome == "win"
    assert (r.entry_price, r.exit_price) == (D("99.50000000"), D("110.00000000"))
    assert r.closed_at == T + 2 * H


def test_entry_is_limited_at_entry_high_when_price_falls_into_range() -> None:
    r = run(BUY, ("102", "103", "99.9", "100"), ("100", "101", "96", "100"))
    assert r.entry_price == D("100.00000000") and r.outcome is None


def test_no_entry_within_window_expires() -> None:
    r = run(BUY, ("105", "106", "104", "105"), ("105", "107", "104", "106"), ("106", "108", "105", "107"), ("107", "108", "99", "100"))
    assert r.outcome == "expired_no_entry" and r.entry_price is None and r.closed_at == T + 3 * H


def test_gap_through_stop_before_entry_invalidates() -> None:
    r = run(BUY, ("94", "96", "93", "95"))
    assert r.outcome == "invalidated" and types(r) == ["invalidated"]


def test_stop_on_entry_bar_and_no_target_credit_on_entry_bar() -> None:
    r = run(BUY, ("100", "120", "94", "110"))  # target and stop both traded in the entry bar
    assert r.outcome == "loss" and r.exit_price == D("95.00000000")


def test_stop_wins_when_stop_and_target_hit_in_the_same_bar() -> None:
    r = run(BUY, ("99.5", "100", "99", "99.5"), ("100", "111", "94", "105"))
    assert r.outcome == "loss" and r.exit_price == D("95.00000000")


def test_gap_through_stop_fills_at_the_open() -> None:
    r = run(BUY, ("99.5", "100", "99", "99.5"), ("90", "92", "89", "91"))
    assert r.outcome == "loss" and r.exit_price == D("90.00000000")
    assert r.r_multiple == D("-2.1111")  # (90 - 99.5) / (99.5 - 95)


def test_horizon_exit_at_close() -> None:
    r = run(BUY, ("99.5", "100", "99", "99.5"), *[("100", "101", "98", "101")] * 4)
    assert r.outcome == "expired_closed" and r.exit_price == D("101.00000000") and types(r)[-1] == "horizon_reached"


def test_sell_mirrors_buy() -> None:
    win = run(SELL, ("100.5", "101", "100", "100.2"), ("100", "100.5", "89", "90"))
    assert win.outcome == "win" and win.exit_price == D("90.00000000")
    gap = run(SELL, ("100.5", "101", "100", "100.2"), ("107", "108", "106", "107"))
    assert gap.outcome == "loss" and gap.exit_price == D("107.00000000")


def test_missing_candle_stops_resolution_until_backfilled() -> None:
    b = bars(("99.5", "100", "99", "99.5"), ("100", "111", "99.5", "110"))
    gapped = [b[0], Bar(b[1].open_time + H, b[1].open, b[1].high, b[1].low, b[1].close)]
    r = resolve(BUY, gapped, "1h", T)
    assert r.outcome is None and types(r) == ["entered"]


def test_costs_reduce_returns_on_both_sides() -> None:
    gross, _ = trade_result("BUY", D(100), D(110), D(95), ZERO)
    net, r = trade_result("BUY", D(100), D(110), D(95), CostModel())
    assert gross == D("0.10000000") and net < gross
    assert net == D("0.09670494")  # 110*0.9995*0.999 / (100*1.0005*1.001) - 1, rounded to 8 dp
    assert r < 2
