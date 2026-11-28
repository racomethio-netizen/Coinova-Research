"""Execution intents (unified brief, section G): approved proposals on their way to a
PAPER or DEMO broker.

Lifecycle (``execution_intents.status``):
* **pending:** created by the orchestrator after the portfolio risk engine approved it.
  This happens only with the market's switch at PAPER, the operating mode placing paper
  orders, and the kill switch clear.
* **sent:** handed to the broker exactly once. A timeout is never retried; the intent is
  reconciled by its client ID.
* **filled:** the broker filled it. The fill price and slippage are recorded.
* **protected:** the stop-loss was verified on the broker's position (a missing stop is
  restored; a position that stays unprotected is closed).
* **closed:** the position is gone (stop, target, time or owner).
* **rejected / expired:** not executed, with the reason in ``events``.

Duplicate prevention:
* each intent has its own client ID (``nxO`` + 20 hex characters), never reused;
* the database allows at most one active intent per venue and instrument;
* the Exness demo trader also refuses a client ID it finds in its order history.

Adapters:
* the crypto paper trader (``trader.Trader.execute_intents``, through the order manager);
* the Exness demo sync (``nexus_api.mt5_demo``).
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta
from decimal import ROUND_DOWN, Decimal
from typing import Any

PREFIX = "nxO"
EXPIRY = {"crypto_paper": timedelta(minutes=10), "exness_demo": timedelta(minutes=15)}
ACTIVE = ("pending", "sent", "filled", "protected")
STOP_FIX_ATTEMPTS = 2          # syncs with a missing stop before the position is closed


def new_client_id() -> str:
    return PREFIX + uuid.uuid4().hex[:20]


def is_intent_id(client_id: str) -> bool:
    return client_id.startswith(PREFIX)


def add_event(intent: Any, kind: str, detail: str, now: datetime) -> None:
    """Append to the intent's event list (a new list, so the JSON change is saved)."""
    intent.events = [*(intent.events or []), {"at": now.isoformat(), "kind": kind,
                                               "detail": detail}]


def create(s: Any, *, venue: str, market: str, symbol: str, side: int, kind: str,
           module: str, now: datetime, quantity: Decimal | None = None,
           notional_usd: Decimal | None = None, entry_ref: Decimal | None = None,
           stop: Decimal | None = None, target: Decimal | None = None,
           note: str = "") -> Any | None:
    """A new pending intent, or None if one is already active for this venue and
    instrument (no duplicate order)."""
    from nexus_db import models
    from sqlalchemy import select
    from sqlalchemy.exc import IntegrityError

    active = s.scalar(select(models.ExecutionIntent.id).where(
        models.ExecutionIntent.venue == venue, models.ExecutionIntent.symbol == symbol,
        models.ExecutionIntent.status.in_(ACTIVE)))
    if active is not None:
        return None
    it = models.ExecutionIntent(
        client_id=new_client_id(), venue=venue, market=market, symbol=symbol, side=side,
        kind=kind, module=module, status="pending", quantity=quantity,
        notional_usd=notional_usd, entry_ref=entry_ref, stop=stop, target=target,
        expires_at=now + EXPIRY[venue], events=[])
    add_event(it, "created", note or "approved by the portfolio risk engine", now)
    try:
        with s.begin_nested():
            s.add(it)
            s.flush()
    except IntegrityError:          # another worker created it at the same moment
        return None
    return it


def slippage(side: int, expected: Decimal, fill: Decimal) -> tuple[Decimal, Decimal]:
    """(adverse price difference, the same in basis points). Positive = paid more than
    expected (buy) or received less (sell)."""
    adverse = side * (fill - expected)
    bps = adverse / expected * 10_000 if expected > 0 else Decimal(0)
    return adverse, bps


def stop_ok(side: int, planned: Decimal | None, actual: Decimal | None,
            tolerance: Decimal) -> bool:
    """The broker's stop is set, on the loss side, and within ``tolerance`` of plan."""
    if planned is None or actual is None or actual <= 0:
        return False
    return abs(actual - planned) <= tolerance


def lots(units: Decimal, contract_size: Decimal, step: Decimal, vmin: Decimal,
         vmax: Decimal) -> Decimal:
    """Units -> lots, rounded DOWN to the broker's step (never more risk); 0 when below
    the minimum lot."""
    if contract_size <= 0 or step <= 0:
        return Decimal(0)
    raw = units / contract_size
    v = (raw / step).to_integral_value(rounding=ROUND_DOWN) * step
    v = min(v, vmax)
    return v if v >= vmin else Decimal(0)


def demo_open(it: Any, broker_symbol: str, volume: Decimal) -> dict[str, Any]:
    return {"action": "open", "symbol": broker_symbol, "side": "buy" if it.side > 0 else "sell",
            "volume": float(volume), "sl": float(it.stop), "tp": float(it.target),
            "client_id": it.client_id, "reason": f"orchestrator: {it.module}"}


def demo_manage(it: Any, pos: dict[str, Any], bid: float, ask: float, point: float,
                volume_step: float, volume_min: float) -> tuple[list[dict[str, Any]], str]:
    """Instructions for a filled orchestrator position on the demo account, and what
    happened ("" = nothing to do):
    * stop missing or wrong: restore it; after ``STOP_FIX_ATTEMPTS`` tries, close the
      position (never leave a position unprotected);
    * +1R reached: close half (rounded down to the lot step, if at least the minimum
      lot) and move the stop to the entry (break-even), once.
    """
    side = 1 if pos["side"] == "buy" else -1
    planned = Decimal(str(it.stop)) if it.stop is not None else None
    tol = Decimal(str(point)) * 2
    tries = sum(1 for e in (it.events or []) if e.get("kind") == "stop_fix")
    if not it.partial_taken and not stop_ok(side, planned, Decimal(str(pos.get("sl") or 0)),
                                            tol):
        if tries >= STOP_FIX_ATTEMPTS:
            return [{"action": "close", "symbol": pos["symbol"],
                     "side": "sell" if side > 0 else "buy", "volume": pos["volume"],
                     "ticket": pos["ticket"], "client_id": f"{it.client_id}x",
                     "reason": "stop-loss could not be verified: position closed"}], "unprotected"
        return [{"action": "modify", "symbol": pos["symbol"], "ticket": pos["ticket"],
                 "sl": float(planned or 0), "tp": float(it.target or 0),
                 "client_id": f"{it.client_id}s{tries + 1}",
                 "reason": "restore the planned stop-loss"}], "stop_fix"
    if it.partial_taken or it.fill_price is None or planned is None:
        return [], ""
    entry = float(it.fill_price)
    risk = abs(entry - float(planned))
    favour = (bid - entry) if side > 0 else (entry - ask)
    if risk <= 0 or favour < risk:
        return [], ""
    half = int((pos["volume"] / 2) / volume_step) * volume_step
    out = []
    if half >= volume_min:
        out.append({"action": "close", "symbol": pos["symbol"],
                    "side": "sell" if side > 0 else "buy", "volume": round(half, 8),
                    "ticket": pos["ticket"], "client_id": f"{it.client_id}p",
                    "reason": "partial exit at +1R"})
    out.append({"action": "modify", "symbol": pos["symbol"], "ticket": pos["ticket"],
                "sl": entry, "tp": float(it.target or 0), "client_id": f"{it.client_id}b",
                "reason": "stop to break-even after +1R"})
    return out, "partial"
