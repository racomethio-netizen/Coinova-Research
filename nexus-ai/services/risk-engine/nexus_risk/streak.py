"""Consecutive-loss protection. Losses reduce risk; they never increase it.

* After ``reduce_after`` consecutive losing trades, risk per trade is multiplied by
  ``reduced_factor`` (default 0.5).
* After ``pause_after`` consecutive losses, new entries pause until a manual review
  resets the streak (the owner clears it; nothing resets it automatically).
* A winning trade does NOT restore full risk at once: it takes ``recovery_wins`` wins in a
  row to return to full size. Martingale-style size increases are never produced.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal


@dataclass(frozen=True)
class StreakState:
    losses: int = 0
    wins_since_reduction: int = 0
    paused: bool = False


@dataclass(frozen=True)
class StreakPolicy:
    reduce_after: int = 2
    pause_after: int = 4
    reduced_factor: Decimal = Decimal("0.5")
    recovery_wins: int = 2


DEFAULT_POLICY = StreakPolicy()


def record_trade(state: StreakState, *, won: bool,
                 policy: StreakPolicy = DEFAULT_POLICY) -> StreakState:
    if won:
        wins = state.wins_since_reduction + 1 if state.losses >= policy.reduce_after else 0
        losses = 0 if wins >= policy.recovery_wins else state.losses
        return StreakState(losses, 0 if losses == 0 else wins, state.paused)
    losses = state.losses + 1
    paused = state.paused or losses >= policy.pause_after
    return StreakState(losses, 0, paused)


def risk_multiplier(state: StreakState, policy: StreakPolicy = DEFAULT_POLICY) -> Decimal:
    if state.paused:
        return Decimal("0")
    return policy.reduced_factor if state.losses >= policy.reduce_after else Decimal("1")
