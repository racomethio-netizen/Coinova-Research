"""Performance reporting from recorded orders/fills/positions/snapshots (no estimates).

Every report carries ``mode``; paper reports are titled as simulations.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from nexus_common.decimals import ZERO
from nexus_db import models
from sqlalchemy import func, select
from sqlalchemy.orm import Session


def portfolio_report(s: Session, portfolio_id: int) -> dict[str, Any]:
    p = s.get(models.Portfolio, portfolio_id)
    if p is None:
        raise LookupError("portfolio not found")
    closed = s.scalars(select(models.Position).where(models.Position.portfolio_id == p.id,
                                                     models.Position.status == "closed")).all()
    wins = [x.realized_pnl for x in closed if x.realized_pnl > 0]
    losses = [x.realized_pnl for x in closed if x.realized_pnl <= 0]
    gross_win, gross_loss = sum(wins, ZERO), -sum(losses, ZERO)
    last = s.scalar(select(models.PerformanceSnapshot).where(
        models.PerformanceSnapshot.portfolio_id == p.id)
        .order_by(models.PerformanceSnapshot.captured_at.desc()).limit(1))
    max_dd = s.scalar(select(func.max(models.PerformanceSnapshot.drawdown)).where(
        models.PerformanceSnapshot.portfolio_id == p.id))
    orders: dict[str, int] = {str(st): int(n) for st, n in s.execute(
        select(models.Order.status, func.count()).where(models.Order.portfolio_id == p.id)
        .group_by(models.Order.status)).all()}
    slippage = s.execute(select(models.Order.side, models.Order.expected_price,
                                models.Order.average_fill_price).where(
        models.Order.portfolio_id == p.id, models.Order.average_fill_price.is_not(None),
        models.Order.expected_price.is_not(None))).all()
    slip_bps = [((avg - exp) / exp * 10_000) * (1 if side == "buy" else -1)
                for side, exp, avg in slippage if exp > 0]

    def opt(x: Decimal | None) -> str | None:
        return None if x is None else str(x)

    return {
        "mode": p.mode,
        "title": ("PAPER TRADING (simulated fills on real prices, not real profits)"
                  if p.mode == "paper" else "LIVE TRADING"),
        "portfolio_id": p.id,
        "initial_capital": opt(p.initial_capital),
        "equity": opt(last.equity) if last else None,
        "cash": opt(last.cash) if last else None,
        "exposure": opt(last.exposure) if last else None,
        "realized_pnl": str(sum((x.realized_pnl for x in closed), ZERO)),
        "unrealized_pnl": opt(last.unrealized_pnl) if last else None,
        "fees_paid": str(sum((x.fees_paid for x in closed), ZERO)),
        "closed_trades": len(closed),
        "win_rate": (str(Decimal(len(wins)) / len(closed)) if closed else None),
        "profit_factor": (str(gross_win / gross_loss) if gross_loss > 0 else None),
        "max_drawdown": opt(max_dd),
        "current_drawdown": opt(last.drawdown) if last else None,
        "orders_by_status": orders,
        "avg_slippage_bps": (str(sum(slip_bps, ZERO) / len(slip_bps)) if slip_bps else None),
        "as_of": last.captured_at.isoformat() if last else None,
    }
