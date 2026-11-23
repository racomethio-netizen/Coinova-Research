"""Decision log on real PostgreSQL: idempotent writes, heartbeat, append-only rules."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import timedelta

import pytest
from nexus_api.decision_log import prune, store
from nexus_db import make_session_factory, models, transaction
from sqlalchemy import Engine, func, select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session, sessionmaker

from tests.integration.conftest import truncate
from tests.unit.test_decision_log import T, sig

pytestmark = pytest.mark.db


def _clear(engine: Engine) -> None:
    with engine.begin() as c:   # the trigger allows only old rows to be deleted
        c.execute(text("ALTER TABLE decision_logs DISABLE TRIGGER decision_logs_no_change"))
    truncate(engine, "decision_logs")
    with engine.begin() as c:
        c.execute(text("ALTER TABLE decision_logs ENABLE TRIGGER decision_logs_no_change"))


@pytest.fixture
def factory(migrated_engine: Engine) -> Iterator[sessionmaker[Session]]:
    _clear(migrated_engine)
    yield make_session_factory(migrated_engine)
    _clear(migrated_engine)


def count(f: sessionmaker[Session]) -> int:
    with transaction(f) as s:
        return int(s.scalar(select(func.count()).select_from(models.DecisionLog)) or 0)


def at(minutes: int) -> dict[str, object]:
    t = T + timedelta(minutes=minutes)
    return {"exchange": "binance", "timeframe": "15m", "bar_open": t.isoformat(),
            "book_at": None}


def test_store_change_and_heartbeat(factory: sessionmaker[Session]) -> None:
    assert store(factory, [sig()], T) == 1
    assert store(factory, [sig()], T) == 0                         # same bar again
    assert store(factory, [sig(snapshot=at(15))], T) == 0          # unchanged
    assert store(factory, [sig(snapshot=at(30), decision="WAIT")], T) == 1   # changed
    assert store(factory, [sig(snapshot=at(45), decision="WAIT")], T) == 0
    assert store(factory, [sig(snapshot=at(90), decision="WAIT")], T) == 1   # hourly
    eth = sig(symbol="ETH/USDT")
    assert store(factory, [eth], T) == 1                           # pairs are independent
    assert count(factory) == 4
    with transaction(factory) as s:
        row = s.scalar(select(models.DecisionLog).where(models.DecisionLog.decision == "WAIT")
                       .order_by(models.DecisionLog.bar_time).limit(1))
        assert row is not None and row.snapshot_id.startswith("binance:BTC/USDT:15m@")
        assert row.strategy_signals["strategies"]["trend_supertrend"]["eligible"] is False


def test_append_only_and_retention(factory: sessionmaker[Session]) -> None:
    store(factory, [sig()], T)
    with pytest.raises(DBAPIError, match="append-only"), transaction(factory) as s:
        s.execute(text("UPDATE decision_logs SET decision='LONG'"))
    with pytest.raises(DBAPIError, match="append-only"), transaction(factory) as s:
        s.execute(text("DELETE FROM decision_logs"))
    # A row past the 180-day retention can be pruned; recent rows stay.
    store(factory, [sig(symbol="ETH/USDT")], T - timedelta(days=200))
    assert count(factory) == 2
    assert prune(factory, T) == 1 and count(factory) == 1
