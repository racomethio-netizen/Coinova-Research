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


def _sharpe(s: Session, portfolio_id: int, min_days: int = 30) -> dict[str, Any]:
    """Annualised Sharpe of daily equity returns (risk-free 0). Reported only with at least
    ``min_days`` daily points, and always with a caveat."""
    import math
    from itertools import pairwise

    snaps = s.execute(select(models.PerformanceSnapshot.captured_at,
                             models.PerformanceSnapshot.equity)
                      .where(models.PerformanceSnapshot.portfolio_id == portfolio_id)
                      .order_by(models.PerformanceSnapshot.captured_at)).all()
    daily: dict[Any, float] = {}
    for at, eq in snaps:
        daily[at.date()] = float(eq)                       # last snapshot of each day
    vals = list(daily.values())
    rets = [b / a - 1 for a, b in pairwise(vals) if a > 0]
    caveat = ("Sharpe needs many independent periods; under ~90 days it is very noisy, "
              "and paper fills are simulated.")
    if len(rets) < min_days:
        return {"sharpe": None, "sharpe_days": len(rets),
                "sharpe_note": f"needs {min_days}+ days of history ({len(rets)} so far). {caveat}"}
    mean = sum(rets) / len(rets)
    sd = math.sqrt(sum((r - mean) ** 2 for r in rets) / (len(rets) - 1))
    return {"sharpe": None if sd == 0 else str(round(mean / sd * math.sqrt(365), 2)),
            "sharpe_days": len(rets), "sharpe_note": caveat}


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
                for side, exp, avg in slippage
                if exp is not None and avg is not None and exp > 0]

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
        "avg_win": str(gross_win / len(wins)) if wins else None,
        "avg_loss": str(-gross_loss / len(losses)) if losses else None,
        "expectancy": (str(sum((x.realized_pnl for x in closed), ZERO) / len(closed))
                       if closed else None),
        **_sharpe(s, p.id),
        "as_of": last.captured_at.isoformat() if last else None,
    }


def execution_quality(s: Session, portfolio_id: int) -> dict[str, Any]:
    """Execution quality from recorded orders only: fill rate, partial and missed fills,
    rejections, unresolved states and slippage versus the price expected at submission
    (positive bps = worse than expected), split by entry and exit."""
    from decimal import Decimal

    rows = s.execute(select(models.Order.purpose, models.Order.side, models.Order.status,
                            models.Order.amount, models.Order.filled_amount,
                            models.Order.expected_price, models.Order.average_fill_price)
                     .where(models.Order.portfolio_id == portfolio_id)).all()
    out: dict[str, Any] = {}
    for purpose in ("entry", "exit"):
        mine = [r for r in rows if r.purpose == purpose]
        sent = [r for r in mine if r.status not in ("pending_submit",)]
        filled_any = [r for r in sent if (r.filled_amount or 0) > 0]
        partial = [r for r in filled_any if r.filled_amount < r.amount]
        missed = [r for r in sent if r.status in ("canceled", "expired")
                  and not (r.filled_amount or 0)]
        rejected = [r for r in sent if r.status == "rejected"]
        unknown = [r for r in sent if r.status == "unknown"]
        slips: list[Decimal] = []
        for r in filled_any:
            if r.expected_price and r.average_fill_price:
                d = (r.average_fill_price - r.expected_price) / r.expected_price * 10_000
                slips.append(d if r.side == "buy" else -d)
        slips.sort()
        amount = sum((r.amount for r in sent), ZERO)
        filled = sum((r.filled_amount or ZERO for r in sent), ZERO)
        out[purpose] = {
            "orders": len(sent), "with_fills": len(filled_any), "partial_fills": len(partial),
            "missed": len(missed), "rejected": len(rejected), "unknown": len(unknown),
            "fill_rate": None if amount == 0 else str((filled / amount).quantize(
                Decimal("0.0001"))),
            "slippage_bps_avg": str(round(sum(slips, ZERO) / len(slips), 2)) if slips else None,
            "slippage_bps_p95": str(round(slips[min(len(slips) - 1,
                                                    int(len(slips) * 0.95))], 2))
            if slips else None,
            "slippage_bps_worst": str(round(slips[-1], 2)) if slips else None,
        }
    return out
