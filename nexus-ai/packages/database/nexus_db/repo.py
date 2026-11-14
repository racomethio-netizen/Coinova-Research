"""Small repository layer: typed reads/writes used by the services.

All functions take a Session and never commit; callers own the transaction
(``nexus_db.transaction``) so multi-step changes are atomic.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from nexus_db import models as m


# ------------------------------------------------------------------ reference
def exchange_id(session: Session, code: str) -> int:
    ex_id = session.scalar(select(m.Exchange.id).where(m.Exchange.code == code))
    if ex_id is None:
        raise LookupError(f"unknown exchange {code!r}")
    return ex_id


def upsert_trading_pair(session: Session, *, exchange_id: int, symbol: str, exchange_symbol: str,
                        base: str, quote: str, price_tick: Decimal, amount_step: Decimal,
                        min_amount: Decimal | None, min_notional: Decimal | None,
                        maker_fee: Decimal | None, taker_fee: Decimal | None,
                        synced_at: datetime) -> int:
    values = dict(exchange_id=exchange_id, symbol=symbol, exchange_symbol=exchange_symbol,
                  base_asset=base, quote_asset=quote, price_tick=price_tick,
                  amount_step=amount_step, min_amount=min_amount, min_notional=min_notional,
                  maker_fee=maker_fee, taker_fee=taker_fee, is_active=True,
                  metadata_synced_at=synced_at)
    ins = insert(m.TradingPair).values(**values)
    stmt = ins.on_conflict_do_update(
        constraint="uq_trading_pairs_exchange_id_symbol",
        set_={k: ins.excluded[k] for k in values if k not in ("exchange_id", "symbol")}
        | {"updated_at": func.now()},
    ).returning(m.TradingPair.id)
    return int(session.execute(stmt).scalar_one())


def pair_id(session: Session, exchange_code: str, symbol: str) -> int:
    pid = session.scalar(
        select(m.TradingPair.id).join(m.Exchange, m.Exchange.id == m.TradingPair.exchange_id)
        .where(m.Exchange.code == exchange_code, m.TradingPair.symbol == symbol))
    if pid is None:
        raise LookupError(f"pair {symbol} on {exchange_code} not synced")
    return pid


# ------------------------------------------------------------------ candles
@dataclass(frozen=True)
class CandleRow:
    open_time: datetime
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: Decimal


def upsert_candles(session: Session, pair_id: int, timeframe: str, candles: Iterable[Any],
                   *, source: str = "rest") -> int:
    """Insert closed candles; exchange revisions of an existing bar overwrite it.
    Returns the number of rows written (inserted or updated)."""
    rows = [dict(pair_id=pair_id, timeframe=timeframe, open_time=c.open_time, open=c.open,
                 high=c.high, low=c.low, close=c.close, volume=c.volume, is_closed=True,
                 source=source) for c in candles]
    if not rows:
        return 0
    for i in range(0, len(rows), 1000):
        stmt = insert(m.MarketCandle).values(rows[i:i + 1000])
        stmt = stmt.on_conflict_do_update(
            index_elements=["pair_id", "timeframe", "open_time"],
            set_={k: stmt.excluded[k] for k in ("open", "high", "low", "close", "volume",
                                                "is_closed", "source")},
        )
        session.execute(stmt)
    return len(rows)


def latest_candle_time(session: Session, pair_id: int, timeframe: str) -> datetime | None:
    return session.scalar(select(func.max(m.MarketCandle.open_time)).where(
        m.MarketCandle.pair_id == pair_id, m.MarketCandle.timeframe == timeframe,
        m.MarketCandle.is_closed.is_(True)))


def load_candles(session: Session, pair_id: int, timeframe: str, *,
                 start: datetime | None = None, end: datetime | None = None,
                 limit: int | None = None) -> list[CandleRow]:
    """Closed candles, oldest first. ``limit`` takes the most recent N."""
    q = select(m.MarketCandle).where(m.MarketCandle.pair_id == pair_id,
                                     m.MarketCandle.timeframe == timeframe,
                                     m.MarketCandle.is_closed.is_(True))
    if start is not None:
        q = q.where(m.MarketCandle.open_time >= start)
    if end is not None:
        q = q.where(m.MarketCandle.open_time < end)
    if limit is not None:
        q = q.order_by(m.MarketCandle.open_time.desc()).limit(limit)
        rows = list(reversed(session.scalars(q).all()))
    else:
        rows = list(session.scalars(q.order_by(m.MarketCandle.open_time)).all())
    return [CandleRow(r.open_time, r.open, r.high, r.low, r.close, r.volume) for r in rows]


# ------------------------------------------------------------------ events
def record_system_event(session: Session, *, component: str, event_type: str, severity: str,
                        message: str, details: dict[str, Any] | None = None) -> int:
    ev = m.SystemEvent(component=component, event_type=event_type, severity=severity,
                       message=message, details=details or {})
    session.add(ev)
    session.flush()
    return ev.id


def record_audit(session: Session, *, actor: str, action: str, user_id: int | None = None,
                 target_type: str | None = None, target_id: str | None = None,
                 details: dict[str, Any] | None = None, ip_address: str | None = None) -> None:
    session.add(m.AuditLog(user_id=user_id, actor=actor, action=action, target_type=target_type,
                           target_id=target_id, details=details or {}, ip_address=ip_address))
    session.flush()


def record_risk_event(session: Session, *, mode: str, event_type: str, severity: str,
                      portfolio_id: int | None = None,
                      details: dict[str, Any] | None = None) -> int:
    ev = m.RiskEvent(mode=mode, portfolio_id=portfolio_id, event_type=event_type,
                     severity=severity, details=details or {})
    session.add(ev)
    session.flush()
    return ev.id


def recent_system_events(session: Session, limit: int = 100) -> Sequence[m.SystemEvent]:
    return session.scalars(select(m.SystemEvent).order_by(m.SystemEvent.id.desc())
                           .limit(limit)).all()
