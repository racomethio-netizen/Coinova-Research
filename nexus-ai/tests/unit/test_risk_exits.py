from __future__ import annotations

from decimal import Decimal as D

from nexus_risk.correlation import correlated_exposure_ok
from nexus_risk.exits import ExitState, partial_take_profit, stop_hit, trailing_stop
from nexus_risk.streak import StreakPolicy, StreakState, record_trade, risk_multiplier


def test_trailing_stop_only_rises() -> None:
    s = ExitState(entry=D(100), stop=D(98), highest=D(100), quantity=D(1))
    s = trailing_stop(s, last=D(110), atr=D(2))
    assert s.stop == D(106) and s.highest == D(110)
    s = trailing_stop(s, last=D(104), atr=D(2))  # price pulled back: stop must not fall
    assert s.stop == D(106)
    assert stop_hit(s, low=D(105))


def test_partial_take_profit_once_then_breakeven() -> None:
    s = ExitState(entry=D(100), stop=D(98), highest=D(100), quantity=D("1.0"))
    qty, s2 = partial_take_profit(s, last=D(101), amount_step=D("0.0001"))
    assert qty == D(0)  # not yet at +1R
    qty, s2 = partial_take_profit(s, last=D(102), amount_step=D("0.0001"))
    assert qty == D("0.5") and s2.quantity == D("0.5") and s2.stop == D(100)
    assert s2.partial_taken
    qty2, _ = partial_take_profit(s2, last=D(110), amount_step=D("0.0001"))
    assert qty2 == D(0)  # only once


def test_losses_reduce_risk_and_pause_never_increase() -> None:
    st = StreakState()
    assert risk_multiplier(st) == D(1)
    st = record_trade(st, won=False)
    st = record_trade(st, won=False)
    assert risk_multiplier(st) == D("0.5")
    st = record_trade(st, won=True)  # one win is not enough to restore full size
    assert risk_multiplier(st) == D("0.5")
    st = record_trade(st, won=True)
    assert risk_multiplier(st) == D(1)
    st = StreakState()
    for _ in range(4):
        st = record_trade(st, won=False)
    assert st.paused and risk_multiplier(st) == D(0)
    st = record_trade(st, won=True)  # a win does not clear a pause (owner review does)
    assert st.paused


def test_correlated_exposure_counts_as_one_risk() -> None:
    corr = {("ETH/USDT", "BTC/USDT"): 0.9}
    ok, _ = correlated_exposure_ok(new_asset="ETH/USDT", new_notional=D(2100), equity=D(10000),
                                   held={"BTC/USDT": D(1500)}, correlations=corr)
    assert not ok  # 3600 of 10000 is above the 35% group limit (3500)
    ok, _ = correlated_exposure_ok(new_asset="ETH/USDT", new_notional=D(2000), equity=D(10000),
                                   held={"BTC/USDT": D(1500)}, correlations=corr)
    assert ok  # exactly at the limit is allowed
    ok, _ = correlated_exposure_ok(new_asset="SOL/USDT", new_notional=D(2000), equity=D(10000),
                                   held={"BTC/USDT": D(1500)}, correlations=corr)
    assert ok  # uncorrelated: not grouped
    assert StreakPolicy().reduce_after < StreakPolicy().pause_after
