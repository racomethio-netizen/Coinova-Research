"""Risk-state derivation: every trigger maps to the documented state, the strictest wins,
the owner floor only adds restrictions, and AI is not an input."""

from __future__ import annotations

from dataclasses import replace
from decimal import Decimal as D

import pytest
from nexus_risk.state import (
    RiskInputs,
    RiskState,
    cancels_entries,
    derive,
    entries_allowed,
)

CALM = RiskInputs(kill_switch=False, drawdown_shutdown=False, daily_loss_hit=False,
                  weekly_loss_hit=False, loss_warning=False, streak_multiplier=D(1),
                  unknown_orders=0, positions_unmarkable=False, market_data_stale=False,
                  reconciliation="ok")


def test_calm_inputs_are_normal_at_full_risk() -> None:
    st = derive(CALM)
    assert st.state is RiskState.NORMAL and st.reasons == () and st.risk_multiplier == 1
    assert derive(replace(CALM, reconciliation="not_applicable")).state is RiskState.NORMAL


@pytest.mark.parametrize(("change", "expected"), [
    ({"kill_switch": True}, RiskState.EMERGENCY_STOP),
    ({"drawdown_shutdown": True}, RiskState.EMERGENCY_STOP),
    ({"reconciliation": "mismatch"}, RiskState.CLOSE_ONLY),
    ({"reconciliation": "unavailable"}, RiskState.NO_NEW_TRADES),
    ({"daily_loss_hit": True}, RiskState.NO_NEW_TRADES),
    ({"weekly_loss_hit": True}, RiskState.NO_NEW_TRADES),
    ({"unknown_orders": 1}, RiskState.NO_NEW_TRADES),
    ({"positions_unmarkable": True}, RiskState.NO_NEW_TRADES),
    ({"market_data_stale": True}, RiskState.NO_NEW_TRADES),
    ({"streak_multiplier": D(0)}, RiskState.NO_NEW_TRADES),
    ({"streak_multiplier": D("0.5")}, RiskState.REDUCED_RISK),
    ({"loss_warning": True}, RiskState.REDUCED_RISK),
])
def test_each_trigger(change: dict[str, object], expected: RiskState) -> None:
    st = derive(replace(CALM, **change))  # type: ignore[arg-type]
    assert st.state is expected
    assert st.reasons, "every non-normal state carries a reason"
    if not entries_allowed(st.state):
        assert st.risk_multiplier == 0


def test_strictest_condition_wins_and_all_reasons_are_kept() -> None:
    st = derive(replace(CALM, loss_warning=True, daily_loss_hit=True, kill_switch=True))
    assert st.state is RiskState.EMERGENCY_STOP
    assert len(st.reasons) == 3


def test_reduced_risk_multiplier_takes_the_smaller_reduction() -> None:
    assert derive(replace(CALM, loss_warning=True)).risk_multiplier == D("0.5")
    assert derive(replace(CALM, streak_multiplier=D("0.75"))).risk_multiplier == D("0.75")
    both = derive(replace(CALM, loss_warning=True, streak_multiplier=D("0.25")))
    assert both.risk_multiplier == D("0.25")


def test_owner_floor_adds_but_never_removes_restrictions() -> None:
    assert derive(replace(CALM, owner_floor=RiskState.CLOSE_ONLY)).state is RiskState.CLOSE_ONLY
    # A relaxed floor cannot lift a detected condition.
    st = derive(replace(CALM, daily_loss_hit=True, owner_floor=RiskState.NORMAL))
    assert st.state is RiskState.NO_NEW_TRADES
    assert derive(replace(CALM, owner_floor=RiskState.REDUCED_RISK)).risk_multiplier == D("0.5")


def test_entry_and_cancel_semantics() -> None:
    assert [s for s in RiskState if entries_allowed(s)] == [RiskState.NORMAL,
                                                           RiskState.REDUCED_RISK]
    assert [s for s in RiskState if cancels_entries(s)] == [RiskState.CLOSE_ONLY,
                                                           RiskState.EMERGENCY_STOP]
