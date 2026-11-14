from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from decimal import Decimal as D

import pytest
from nexus_db import repo
from nexus_db.repo import CandleRow
from sqlalchemy import Connection, text
from sqlalchemy.orm import Session

pytestmark = pytest.mark.db
T0 = datetime(2026, 1, 1, tzinfo=UTC)


@pytest.fixture
def session(conn: Connection) -> Iterator[Session]:
    s = Session(bind=conn, join_transaction_mode="create_savepoint")
    yield s
    s.close()


def _pair(session: Session, **kw: object) -> int:
    args = dict(exchange_id=repo.exchange_id(session, "binance"), symbol="BTC/USDT",
                exchange_symbol="BTCUSDT", base="BTC", quote="USDT", price_tick=D("0.01"),
                amount_step=D("0.00001"), min_amount=D("0.00001"), min_notional=D("5"),
                maker_fee=D("0.001"), taker_fee=D("0.001"), synced_at=T0)
    args.update(kw)
    return repo.upsert_trading_pair(session, **args)  # type: ignore[arg-type]


def test_pair_upsert_is_idempotent(session: Session) -> None:
    a = _pair(session)
    b = _pair(session, price_tick=D("0.1"))
    assert a == b
    assert repo.pair_id(session, "binance", "BTC/USDT") == a
    tick = session.execute(text("SELECT price_tick FROM trading_pairs WHERE id=:i"), {"i": a})
    assert tick.scalar_one() == D("0.1")
    with pytest.raises(LookupError):
        repo.pair_id(session, "kraken", "BTC/USDT")
    with pytest.raises(LookupError):
        repo.exchange_id(session, "ftx")


def test_candles_roundtrip_exact_decimals(session: Session) -> None:
    pid = _pair(session)
    rows = [CandleRow(T0 + timedelta(minutes=5 * i), D("100.123456789"), D("101"), D("99"),
                      D("100.5"), D("0.000000000000000001")) for i in range(3)]
    assert repo.upsert_candles(session, pid, "5m", rows) == 3
    assert repo.upsert_candles(session, pid, "5m", rows) == 3  # revision overwrite, no dupes
    loaded = repo.load_candles(session, pid, "5m")
    assert len(loaded) == 3 and loaded[0].open == D("100.123456789")
    assert loaded[0].volume == D("1E-18")
    assert repo.latest_candle_time(session, pid, "5m") == T0 + timedelta(minutes=10)
    assert [c.open_time for c in repo.load_candles(session, pid, "5m", limit=2)] == [
        T0 + timedelta(minutes=5), T0 + timedelta(minutes=10)]
    assert repo.load_candles(session, pid, "5m", start=T0 + timedelta(minutes=5),
                             end=T0 + timedelta(minutes=10))[0].open_time == T0 + timedelta(minutes=5)


def test_events(session: Session) -> None:
    repo.record_system_event(session, component="test", event_type="x", severity="info",
                             message="hello")
    repo.record_audit(session, actor="system:test", action="did")
    assert repo.recent_system_events(session, 1)[0].message == "hello"
    with pytest.raises(Exception):  # noqa: B017 - invalid severity rejected by CHECK
        repo.record_system_event(session, component="t", event_type="x", severity="loud",
                                 message="m")
