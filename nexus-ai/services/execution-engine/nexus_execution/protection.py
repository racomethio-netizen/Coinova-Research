"""Restart-safe inputs for exit management and loss-streak protection.

Everything here is derived from the database (orders, closed positions), so a restart
or a lost cache can never cause a second partial exit or forget a losing streak. The
only Redis value is the owner's streak-reset time.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any

from nexus_db import models
from nexus_risk.streak import DEFAULT_POLICY, StreakPolicy, StreakState, record_trade
from sqlalchemy import select
from sqlalchemy.orm import Session

STREAK_RESET_KEY = "nexus:streak:reset_at"
PARTIAL_REASON = "partial_take_profit"


async def streak_reset_at(redis: Any) -> datetime | None:
    raw = await redis.get(STREAK_RESET_KEY)
    if not raw:
        return None
    return datetime.fromisoformat(raw.decode() if isinstance(raw, bytes) else raw)


def streak_state(s: Session, portfolio_id: int, since: datetime | None,
                 policy: StreakPolicy = DEFAULT_POLICY) -> StreakState:
    """Replay closed positions (oldest first) after the last owner reset."""
    q = (select(models.Position.realized_pnl)
         .where(models.Position.portfolio_id == portfolio_id,
                models.Position.status == "closed",
                models.Position.closed_at.is_not(None))
         .order_by(models.Position.closed_at, models.Position.id))
    if since is not None:
        q = q.where(models.Position.closed_at > since)
    state = StreakState()
    for pnl in s.scalars(q):
        state = record_trade(state, won=pnl > 0, policy=policy)
    return state


def partial_taken(s: Session, position_id: int) -> bool:
    """True once a partial take-profit exit has actually filled (any amount)."""
    rows = s.scalars(select(models.Order).where(
        models.Order.position_id == position_id, models.Order.purpose == "exit",
        models.Order.filled_amount > 0)).all()
    return any((o.details or {}).get("exit_reason") == PARTIAL_REASON for o in rows)


def partial_in_flight(s: Session, position_id: int) -> bool:
    """A partial exit order exists that has not finished; do not place another."""
    rows = s.scalars(select(models.Order).where(
        models.Order.position_id == position_id, models.Order.purpose == "exit",
        models.Order.status.not_in(("filled", "cancelled", "canceled", "rejected", "expired")))).all()
    return any((o.details or {}).get("exit_reason") == PARTIAL_REASON for o in rows)


def initial_stop(s: Session, pos: models.Position) -> Decimal | None:
    """The stop set at entry (risk unit for the partial target), not the trailed stop."""
    if pos.entry_order_id is not None:
        entry = s.get(models.Order, pos.entry_order_id)
        raw = (entry.details or {}).get("stop") if entry else None
        if raw is not None:
            return Decimal(str(raw))
    return pos.stop_loss
