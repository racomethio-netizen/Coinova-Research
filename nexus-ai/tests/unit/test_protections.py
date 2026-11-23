"""Freqtrade-style protections and the owner's research risk profile (stage S2)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from nexus_risk.protections import (
    ClosedTrade,
    ProtectionConfig,
    blocking,
    cooldowns,
    evaluate,
    low_profit_pairs,
    max_drawdown,
    stoploss_guard,
)
from nexus_risk.spot_rules import RESEARCH_PROFILE, BookState, SpotRiskConfig, entry_checks

T0 = datetime(2026, 11, 1, tzinfo=UTC)
CFG = ProtectionConfig()


def trade(h: float, pnl: float, kind: str = "stop", sym: str = "BTCUSDT",
          strat: str = "s1", ret: float | None = None) -> ClosedTrade:
    return ClosedTrade(sym, strat, T0 + timedelta(hours=h), pnl,
                       pnl / 1000 if ret is None else ret, kind)


def test_stoploss_guard_needs_n_losing_stops_in_the_lookback() -> None:
    three = [trade(h, -10) for h in (1, 2, 3)]
    assert stoploss_guard(three, T0 + timedelta(hours=4), CFG) is None
    four = [*three, trade(4, -10)]
    lock = stoploss_guard(four, T0 + timedelta(hours=5), CFG)
    assert lock is not None and lock.scope == "global"
    assert lock.until == T0 + timedelta(hours=4) + CFG.stoploss_lock
    # Target exits and old stops do not count; the lock expires by itself.
    assert stoploss_guard([*three, trade(4, 15, "tp2")], T0 + timedelta(hours=5), CFG) is None
    assert stoploss_guard(four, T0 + timedelta(hours=40), CFG) is None


def test_low_profit_locks_only_that_symbol_and_strategy() -> None:
    ts = [trade(h, -5, "tp2" if h % 2 else "stop") for h in range(4)]
    locks = low_profit_pairs(ts, T0 + timedelta(hours=5), CFG)
    assert len(locks) == 1 and locks[0].symbol == "BTCUSDT" and locks[0].strategy == "s1"
    assert blocking(locks, "BTCUSDT", "s1")
    assert not blocking(locks, "BTCUSDT", "s2") and not blocking(locks, "ETHUSDT", "s1")
    winners = [trade(h, 8, "tp2") for h in range(4)]
    assert low_profit_pairs(winners, T0 + timedelta(hours=5), CFG) == []


def test_max_drawdown_within_lookback() -> None:
    ts = [trade(1, 100, "tp2"), trade(2, -200), trade(3, -200)]
    lock = max_drawdown(ts, T0 + timedelta(hours=4), CFG, 10_000)
    assert lock is not None and "3.9" in lock.reason        # 400 / 10,100
    assert max_drawdown(ts[:2], T0 + timedelta(hours=4), CFG, 10_000) is None   # 2 trades
    small = [trade(1, -50), trade(2, -50), trade(3, -50)]
    assert max_drawdown(small, T0 + timedelta(hours=4), CFG, 10_000) is None


def test_cooldown_after_close_is_per_symbol() -> None:
    locks = cooldowns([trade(1, 5, "tp2")], T0 + timedelta(hours=1, minutes=30), CFG)
    assert blocking(locks, "BTCUSDT", "any") and not blocking(locks, "ETHUSDT", "any")
    assert cooldowns([trade(1, 5, "tp2")], T0 + timedelta(hours=3), CFG) == []


def test_evaluate_reconstructs_window_start_equity() -> None:
    ts = [trade(1, 100, "tp2"), trade(2, -200, "tp2"), trade(3, -200, "tp2")]
    # Equity now 9,700 -> window start 10,000 -> drawdown 400 / 10,100 > 3%.
    locks = evaluate(ts, T0 + timedelta(hours=4), CFG, equity_now=9_700)
    assert any("drawdown" in lk.reason for lk in locks)


def test_research_profile_is_the_owner_mandate_and_within_ceilings() -> None:
    p = RESEARCH_PROFILE
    assert (p.risk_per_trade, p.max_risk_per_trade, p.max_daily_drawdown, p.max_drawdown) == (
        0.005, 0.01, 0.02, 0.05)
    assert p.max_weekly_drawdown == 0.04
    for bad in ({"max_risk_per_trade": 0.02, "risk_per_trade": 0.01},
                {"max_daily_drawdown": 0.03}, {"max_weekly_drawdown": 0.05},
                {"max_drawdown": 0.2}):
        with pytest.raises(ValueError):
            SpotRiskConfig(**bad)  # type: ignore[arg-type]


def test_entry_checks_portfolio_drawdown_and_locks() -> None:
    st = BookState(9_400, 9_400, [], 9_400, 9_400, peak_equity=10_000)
    reasons = entry_checks(st, "BTCUSDT", 100, 98, T0, RESEARCH_PROFILE)
    assert any("drawdown limit (5%)" in r for r in reasons)
    st = BookState(9_600, 9_600, [], 9_600, 9_600, peak_equity=10_000)
    assert entry_checks(st, "BTCUSDT", 100, 98, T0, RESEARCH_PROFILE) == []
    st.locks = cooldowns([trade(-0.5, 5, "tp2")], T0, CFG)
    assert entry_checks(st, "BTCUSDT", 100, 98, T0, RESEARCH_PROFILE, strategy="s1")
    assert entry_checks(st, "ETHUSDT", 100, 98, T0, RESEARCH_PROFILE, strategy="s1") == []
