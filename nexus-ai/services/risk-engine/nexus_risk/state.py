"""Portfolio risk state: one explicit answer to "what may the trader do right now?".

States, from least to most restrictive:

* NORMAL          entries allowed at full configured risk.
* REDUCED_RISK    entries allowed at reduced risk (loss limits >= 80% used, or a losing
                  streak).
* NO_NEW_TRADES   no entries; open positions are managed normally (stops, targets,
                  trailing). Triggered by loss limits, unknown orders, stale or missing
                  data, unavailable balance reconciliation, a paused streak.
* CLOSE_ONLY      as NO_NEW_TRADES, and resting entry orders are cancelled. Triggered by
                  a balance MISMATCH with the exchange (the books are wrong: stop adding).
* EMERGENCY_STOP  the kill switch is engaged (or the drawdown shutdown fired): everything
                  stops; resting entries are cancelled; positions are closed only on the
                  owner's explicit request.

The state is DERIVED every cycle from durable inputs only (equity snapshots in Postgres,
the kill switch with its audit-log copy, the owner's floor in the audit log, order and
reconciliation state). A restart therefore cannot reset a daily loss limit or an emergency
lock. Every transition is written to the append-only audit log with its reasons.

Pure: no I/O here; AI output is not an input and cannot change the result.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum


class RiskState(StrEnum):
    NORMAL = "NORMAL"
    REDUCED_RISK = "REDUCED_RISK"
    NO_NEW_TRADES = "NO_NEW_TRADES"
    CLOSE_ONLY = "CLOSE_ONLY"
    EMERGENCY_STOP = "EMERGENCY_STOP"


SEVERITY = {s: i for i, s in enumerate(RiskState)}
REDUCED_MULTIPLIER = Decimal("0.5")
# States the OWNER may set as a floor (EMERGENCY_STOP is the kill switch, set elsewhere).
OWNER_FLOORS = (RiskState.NORMAL, RiskState.REDUCED_RISK, RiskState.NO_NEW_TRADES,
                RiskState.CLOSE_ONLY)


def stricter(a: RiskState, b: RiskState) -> RiskState:
    return a if SEVERITY[a] >= SEVERITY[b] else b


def entries_allowed(state: RiskState) -> bool:
    return state in (RiskState.NORMAL, RiskState.REDUCED_RISK)


def cancels_entries(state: RiskState) -> bool:
    return state in (RiskState.CLOSE_ONLY, RiskState.EMERGENCY_STOP)


@dataclass(frozen=True)
class RiskInputs:
    kill_switch: bool                    # engaged, or its durable record unreadable
    drawdown_shutdown: bool              # max drawdown breached this cycle
    daily_loss_hit: bool
    weekly_loss_hit: bool
    loss_warning: bool                   # any loss limit >= 80% used
    streak_multiplier: Decimal           # 1 = none, (0,1) reduced, 0 = paused
    unknown_orders: int
    positions_unmarkable: bool           # an open position has no usable price
    market_data_stale: bool
    reconciliation: str                  # ok | mismatch | unavailable | not_applicable
    owner_floor: RiskState = RiskState.NORMAL


@dataclass(frozen=True)
class RiskStatus:
    state: RiskState
    reasons: tuple[str, ...]
    risk_multiplier: Decimal             # applied to risk per trade when entries allowed

    def as_dict(self) -> dict[str, object]:
        return {"state": self.state.value, "reasons": list(self.reasons),
                "risk_multiplier": str(self.risk_multiplier),
                "entries_allowed": entries_allowed(self.state)}


def derive(i: RiskInputs) -> RiskStatus:
    state, reasons = RiskState.NORMAL, []

    def need(s: RiskState, why: str) -> None:
        nonlocal state
        state = stricter(state, s)
        reasons.append(why)

    if i.kill_switch:
        need(RiskState.EMERGENCY_STOP, "kill switch engaged")
    if i.drawdown_shutdown:
        need(RiskState.EMERGENCY_STOP, "maximum drawdown reached")
    if i.reconciliation == "mismatch":
        need(RiskState.CLOSE_ONLY, "exchange balances lower than the internal ledger")
    if i.reconciliation == "unavailable":
        need(RiskState.NO_NEW_TRADES, "exchange balances could not be checked")
    if i.daily_loss_hit:
        need(RiskState.NO_NEW_TRADES, "daily loss limit reached")
    if i.weekly_loss_hit:
        need(RiskState.NO_NEW_TRADES, "weekly loss limit reached")
    if i.unknown_orders:
        need(RiskState.NO_NEW_TRADES, f"{i.unknown_orders} order(s) in unknown state")
    if i.positions_unmarkable:
        need(RiskState.NO_NEW_TRADES, "an open position cannot be priced")
    if i.market_data_stale:
        need(RiskState.NO_NEW_TRADES, "market data stale")
    if i.streak_multiplier <= 0:
        need(RiskState.NO_NEW_TRADES, "losing streak: entries paused until owner reset")
    elif i.streak_multiplier < 1:
        need(RiskState.REDUCED_RISK, "losing streak: risk reduced")
    if i.loss_warning:
        need(RiskState.REDUCED_RISK, "a loss limit is at least 80% used")
    if i.owner_floor is not RiskState.NORMAL:
        need(i.owner_floor, f"owner set minimum state {i.owner_floor.value}")
    mult = Decimal(1)
    if state is RiskState.REDUCED_RISK:
        mult = min(REDUCED_MULTIPLIER if i.loss_warning or i.owner_floor is
                   RiskState.REDUCED_RISK else Decimal(1),
                   i.streak_multiplier if i.streak_multiplier > 0 else Decimal(1))
    elif not entries_allowed(state):
        mult = Decimal(0)
    return RiskStatus(state, tuple(reasons), mult)
