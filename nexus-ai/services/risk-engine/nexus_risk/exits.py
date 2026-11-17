"""Exit management for long positions: trailing stop, partial profit taking, breakeven.

Stops only ever move up for a long (never loosened). Partial exits are sized from the
remaining quantity, and the stop moves to breakeven once the first partial is taken.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from nexus_common.decimals import ZERO, floor_to_step


@dataclass(frozen=True)
class ExitState:
    entry: Decimal
    stop: Decimal
    highest: Decimal          # highest price seen since entry
    quantity: Decimal
    partial_taken: bool = False


def trailing_stop(state: ExitState, *, last: Decimal, atr: Decimal,
                  trail_atr: Decimal = Decimal("2")) -> ExitState:
    """Raise the stop to ``highest - trail_atr * ATR``; never lower it."""
    highest = max(state.highest, last)
    candidate = highest - trail_atr * atr
    stop = max(state.stop, candidate)
    return ExitState(state.entry, stop, highest, state.quantity, state.partial_taken)


def partial_take_profit(state: ExitState, *, last: Decimal, amount_step: Decimal,
                        r_multiple: Decimal = Decimal("1"),
                        fraction: Decimal = Decimal("0.5")) -> tuple[Decimal, ExitState]:
    """At ``r_multiple`` times the initial risk, sell ``fraction`` of the position once and
    move the stop to breakeven. Returns (quantity_to_sell, new_state)."""
    if state.partial_taken or state.quantity <= 0:
        return ZERO, state
    initial_risk = state.entry - state.stop
    if initial_risk <= 0:
        return ZERO, state
    if last < state.entry + r_multiple * initial_risk:
        return ZERO, state
    qty = floor_to_step(state.quantity * fraction, amount_step)
    if qty <= 0 or qty >= state.quantity:
        return ZERO, state
    new = ExitState(state.entry, max(state.stop, state.entry), state.highest,
                    state.quantity - qty, partial_taken=True)
    return qty, new


def stop_hit(state: ExitState, *, low: Decimal) -> bool:
    return low <= state.stop
