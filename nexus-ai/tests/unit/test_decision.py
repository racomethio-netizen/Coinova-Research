"""Decision engine and no-trade intelligence: every gate must be able to block alone."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal as D

import pytest
from nexus_decision import MarketSnapshot, NoTradeInputs, decide, no_trade_reasons

NOW = datetime(2026, 11, 17, 12, tzinfo=UTC)


def clean_inputs(**kw: object) -> NoTradeInputs:
    base = dict(now=NOW, data_time=NOW - timedelta(seconds=10), exchange_healthy=True,
                spread_bps=D(2), depth_quote=D(100_000), order_quote=D(1_000),
                atr_percentile=0.5, news_risk=0.1, daily_loss_limit_reached=False,
                weekly_loss_limit_reached=False, kill_switch=False, reward_risk=None,
                confirmations_conflict=False, regime="trend_up", setup_present=True)
    base.update(kw)
    return NoTradeInputs(**base)  # type: ignore[arg-type]


def strong_long(**kw: object) -> MarketSnapshot:
    base = dict(side="long", entry=D(100), stop=D(98), target=D(106), higher_trend="up",
                structure_event="bos_up", at_level=True, sweep=True, volume_z=2.0,
                book_imbalance=0.3, btc_corr=0.9, btc_trend="up")
    base.update(kw)
    return MarketSnapshot(**base)  # type: ignore[arg-type]


def test_fully_confirmed_clean_setup_enters() -> None:
    d = decide(strong_long(), clean_inputs())
    assert d.action == "ENTER" and d.score == 100
    assert d.reward_risk == pytest.approx(3.0)
    assert d.missed == () and d.reasons == () and "ENTER long" in d.explanation


def test_score_components_are_explained() -> None:
    weak = strong_long(higher_trend="down", sweep=False, volume_z=0.2)
    d = decide(weak, clean_inputs())
    assert "trend_alignment" in d.missed and "liquidity_sweep" in d.missed
    assert "volume_expansion" in d.missed and "structure_event" in d.earned
    assert d.score == 100 - 20 - 15 - 10


@pytest.mark.parametrize(("snap", "x", "fragment"), [
    (strong_long(target=D("99")), clean_inputs(), "no valid target"),
    (strong_long(target=D("101")), clean_inputs(), "risk/reward"),
    (strong_long(), clean_inputs(data_time=NOW - timedelta(minutes=10)), "stale"),
    (strong_long(), clean_inputs(data_time=None), "market data missing"),
    (strong_long(), clean_inputs(spread_bps=D(40)), "spread too wide"),
    (strong_long(), clean_inputs(depth_quote=D(500)), "insufficient liquidity"),
    (strong_long(), clean_inputs(atr_percentile=0.99), "abnormal volatility"),
    (strong_long(), clean_inputs(regime="high_vol"), "high-volatility"),
    (strong_long(), clean_inputs(news_risk=0.9), "news window"),
    (strong_long(), clean_inputs(news_risk=None), "news risk unknown"),
    (strong_long(), clean_inputs(daily_loss_limit_reached=True), "daily loss"),
    (strong_long(), clean_inputs(weekly_loss_limit_reached=True), "weekly loss"),
    (strong_long(), clean_inputs(kill_switch=True), "kill switch"),
    (strong_long(), clean_inputs(exchange_healthy=False), "unhealthy"),
    (strong_long(), clean_inputs(confirmations_conflict=True), "conflicting"),
    (strong_long(structure_event=None, sweep=False, at_level=False),
     clean_inputs(regime="range", setup_present=False), "sideways"),
])
def test_each_no_trade_gate_blocks_on_its_own(snap: MarketSnapshot, x: NoTradeInputs,
                                              fragment: str) -> None:
    d = decide(snap, x)
    assert d.action == "NO_TRADE", d
    assert any(fragment in r for r in d.reasons), d.reasons
    assert d.explanation.startswith("NO TRADE")


def test_low_score_is_no_trade_even_when_gates_pass() -> None:
    d = decide(strong_long(higher_trend="down", structure_event=None, at_level=False,
                           sweep=False, volume_z=0.0, book_imbalance=0.0),
               clean_inputs(), min_score=60)
    assert d.action == "NO_TRADE" and any("below the minimum" in r for r in d.reasons)


def test_short_side_is_symmetric() -> None:
    short = MarketSnapshot(side="short", entry=D(100), stop=D(102), target=D(94),
                           higher_trend="down", structure_event="bos_down", at_level=True,
                           sweep=True, volume_z=2.0, book_imbalance=-0.3, btc_corr=0.9,
                           btc_trend="down")
    d = decide(short, clean_inputs())
    assert d.action == "ENTER" and d.score == 100 and d.reward_risk == pytest.approx(3.0)


def test_invariant_every_no_trade_has_reasons() -> None:
    for snap in (strong_long(target=None), strong_long(higher_trend="down")):
        for x in (clean_inputs(), clean_inputs(kill_switch=True), clean_inputs(setup_present=False)):
            d = decide(snap, x)
            assert (d.action == "ENTER") == (len(d.reasons) == 0)
            assert d.explanation


def test_no_trade_reasons_empty_for_clean_inputs() -> None:
    assert no_trade_reasons(clean_inputs(reward_risk=2.5)) == []
