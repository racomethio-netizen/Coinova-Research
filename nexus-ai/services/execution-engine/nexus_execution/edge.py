"""Edge-decay monitor wiring: closed trades in R per (portfolio, strategy) from the
database, the persisted state (strategies.parameters.edge_monitor[portfolio_id]) and the
owner's audited reset. The decision logic is ``nexus_risk.edge_monitor`` (pure).

Reference mean R is 0 for every strategy: the monitor stops a strategy whose recent
results fall significantly below breakeven, whatever its backtest promised.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any

from nexus_common.timeutil import utcnow
from nexus_db import models, repo
from nexus_risk.edge_monitor import EdgeState, EdgeStatus, evaluate
from sqlalchemy import select
from sqlalchemy.orm import Session

KEY = "edge_monitor"


def closed_trade_r(s: Session, portfolio_id: int, strategy: str,
                   since: datetime | None = None) -> list[float]:
    """Net P&L / money at risk to the initial stop, per closed position, oldest first."""
    rows = s.execute(select(models.Position, models.Order)
                     .join(models.Order, models.Order.id == models.Position.entry_order_id)
                     .where(models.Position.portfolio_id == portfolio_id,
                            models.Position.status == "closed")
                     .order_by(models.Position.closed_at)).all()
    out = []
    for pos, entry in rows:
        det = entry.details or {}
        if det.get("strategy") != strategy or pos.closed_at is None:
            continue
        if since is not None and pos.closed_at <= since:
            continue
        stop = det.get("initial_stop") or det.get("stop")
        price = entry.average_fill_price or entry.price
        if stop is None or price is None or not entry.filled_amount:
            continue
        risk = (Decimal(str(price)) - Decimal(str(stop))) * entry.filled_amount
        if risk > 0:
            out.append(float((pos.realized_pnl or Decimal(0)) / risk))
    return out


def _record(row: models.Strategy) -> dict[str, Any]:
    return dict((row.parameters or {}).get(KEY) or {})


def update(s: Session, portfolio_id: int, strategy: str) -> EdgeStatus | None:
    """Re-evaluate and persist; an escalation is audited. None if the strategy row is
    unknown (nothing is gated)."""
    row = s.scalar(select(models.Strategy).where(models.Strategy.code == strategy))
    if row is None:
        return None
    rec = _record(row)
    mine = dict(rec.get(str(portfolio_id)) or {})
    prev = EdgeState[mine.get("state", "NORMAL")]
    since = datetime.fromisoformat(mine["reset_at"]) if mine.get("reset_at") else None
    status = evaluate(closed_trade_r(s, portfolio_id, strategy, since), previous=prev)
    if status.state > prev:
        repo.record_audit(s, actor="system:edge-monitor", action="edge_state_changed",
                          target_type="strategy", target_id=strategy,
                          details={"portfolio_id": portfolio_id, "from": prev.name,
                                   "to": status.state.name, "reasons": list(status.reasons)})
    rec[str(portfolio_id)] = {**status.to_json(), "updated_at": utcnow().isoformat(),
                              **({"reset_at": mine["reset_at"]} if mine.get("reset_at") else {})}
    row.parameters = {**(row.parameters or {}), KEY: rec}
    return status


def reset(s: Session, portfolio_id: int, strategy: str, *, actor: str, reason: str) -> None:
    """Owner-only (the API checks 2FA): back to NORMAL; only trades closed AFTER this
    moment count from now on."""
    if not actor.startswith("user:"):
        raise PermissionError("only the owner can reset the edge monitor")
    row = s.scalar(select(models.Strategy).where(models.Strategy.code == strategy))
    if row is None:
        raise LookupError(strategy)
    rec = _record(row)
    now = utcnow().isoformat()
    rec[str(portfolio_id)] = {"state": "NORMAL", "multiplier": 1.0, "reset_at": now,
                              "reset_by": actor, "reasons": [f"reset by owner: {reason}"]}
    row.parameters = {**(row.parameters or {}), KEY: rec}
    repo.record_audit(s, actor=actor, action="edge_monitor_reset", target_type="strategy",
                      target_id=strategy, details={"portfolio_id": portfolio_id,
                                                   "reason": reason})
