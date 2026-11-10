"""Signal pipeline against real Postgres as `nexus_engine`: evaluate -> publish (hash chain)
-> resolve, plus tamper detection. Synthetic candles only."""

from dataclasses import replace
from datetime import UTC, datetime, timedelta

import psycopg
import pytest
from synthetic import T0, aggregate, random_walk
from test_strategy import BUY_SEED, scan

from nexus_engine.config import Settings
from nexus_engine.marketdata.ingest import load_pairs, load_source
from nexus_engine.signals import strategy as strat
from nexus_engine.signals.hashing import verify_chain
from nexus_engine.signals.ledger import StrategyChangedError, ensure_strategy, load_chain
from nexus_engine.signals.service import SignalService
from nexus_engine.signals.strategy import Params
from nexus_engine.timeframes import floor_to

pytestmark = pytest.mark.db
H = timedelta(hours=1)


def insert(conn: psycopg.Connection, pair_id: int, tf: str, candles: list) -> None:
    with conn.cursor() as cur:
        cur.executemany(
            "insert into public.market_candles (pair_id, timeframe, open_time, close_time, open, high, low, close, volume, is_final) "
            "values (%s, %s, %s, %s, %s, %s, %s, %s, %s, true)",
            [(pair_id, tf, c.open_time, c.close_time, c.open, c.high, c.low, c.close, c.volume) for c in candles],
        )


@pytest.fixture
def market(engine_conn: psycopg.Connection, superuser: psycopg.Connection):
    """Synthetic series placed so its BUY candle closed within the last few hours (published_at is the real DB time)."""
    i = int((next(d.as_of for d in scan(BUY_SEED) if d.decision == "BUY") - T0) / H) - 1  # index of the decision candle
    as_of = floor_to(datetime.now(UTC), "1h")
    while (as_of - (i + 1) * H).timestamp() % (4 * 3600):
        as_of -= H  # keep the series start 4h-aligned so HTF aggregation is valid
    candles = random_walk("1h", 1400, BUY_SEED, start=as_of - (i + 1) * H)
    pair = next(p for p in load_pairs(engine_conn, load_source(engine_conn, "binance_spot").id) if p.symbol == "BTCUSDT")
    past = candles[: i + 1]
    insert(superuser, pair.id, "1h", past)
    insert(superuser, pair.id, "4h", aggregate(past, "1h", "4h"))
    future = candles[i + 1 :]  # appended later to resolve the outcome
    return pair, as_of, lambda: insert(superuser, pair.id, "1h", future)


def service(conn: psycopg.Connection, at: datetime) -> SignalService:
    return SignalService(conn, Settings(), now=lambda: at + timedelta(seconds=30), max_publication_lag=timedelta(hours=5))


def test_publish_resolve_and_verify(engine_conn: psycopg.Connection, market) -> None:
    pair, as_of, add_future = market
    svc = service(engine_conn, as_of)
    d = svc.evaluate(pair, "1h", as_of)
    assert d is not None and d.decision == "BUY"
    assert svc.evaluate(pair, "1h", as_of) is None  # each close boundary is evaluated once
    hold = svc.evaluate(pair, "1h", as_of - 5 * H)
    assert hold is not None and hold.decision == "HOLD"

    rows = engine_conn.execute("select direction, visibility, confidence, confidence_band, prev_hash from public.signals").fetchall()
    assert rows == [("BUY", "staff", None, "not_calibrated", None)]  # shadow by default (gate G-1)
    assert engine_conn.execute("select count(*) from public.signal_evaluations").fetchone() == (2,)
    assert verify_chain(load_chain(engine_conn)) == []

    assert svc.resolve_open() == 0  # no candles after publication yet: stays open
    add_future()
    assert svc.resolve_open() == 1
    outcome = engine_conn.execute("select outcome, resolver_version from public.signal_outcomes").fetchone()
    assert outcome is not None and outcome[0] in {"win", "loss", "expired_closed", "expired_no_entry", "invalidated"}
    events = [r[0] for r in engine_conn.execute("select event_type from public.signal_events order by id").fetchall()]
    assert events and events[-1] in {"tp1_hit", "sl_hit", "horizon_reached", "expired_no_entry", "invalidated"}
    assert svc.resolve_open() == 0  # resolved once, never rewritten
    engine_conn.commit()


def test_open_signal_suppresses_a_duplicate(engine_conn: psycopg.Connection, market, monkeypatch: pytest.MonkeyPatch) -> None:
    pair, as_of, _ = market
    svc = service(engine_conn, as_of)
    first = svc.evaluate(pair, "1h", as_of)
    assert first is not None and first.decision == "BUY"
    monkeypatch.setattr(strat, "evaluate", lambda *a, **k: replace(first, as_of=as_of + H))
    second = svc.evaluate(pair, "1h", as_of + H)
    assert second is not None and second.decision == "HOLD" and second.reasons[0] == "open_signal_exists"
    assert engine_conn.execute("select count(*) from public.signals").fetchone() == (1,)


def test_tampering_is_detected(engine_conn: psycopg.Connection, superuser: psycopg.Connection, market) -> None:
    pair, as_of, _ = market
    service(engine_conn, as_of).evaluate(pair, "1h", as_of)
    engine_conn.commit()
    superuser.execute("alter table public.signals disable trigger signals_chain_guard")
    try:
        superuser.execute("update public.signals set stop_loss = stop_loss - 1")
    finally:
        superuser.execute("alter table public.signals enable trigger signals_chain_guard")
    problems = verify_chain(load_chain(engine_conn))
    assert len(problems) == 1 and "does not match its hash" in problems[0]


def test_engine_cannot_edit_or_delete_signals(engine_conn: psycopg.Connection, market) -> None:
    pair, as_of, _ = market
    service(engine_conn, as_of).evaluate(pair, "1h", as_of)
    engine_conn.commit()
    for sql in ("update public.signals set stop_loss = 1", "delete from public.signals", "delete from public.signal_evaluations"):
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            engine_conn.execute(sql)
        engine_conn.rollback()


def test_late_evaluations_are_never_published(engine_conn: psycopg.Connection, market) -> None:
    pair, as_of, _ = market
    late = SignalService(engine_conn, Settings(), now=lambda: as_of, max_publication_lag=timedelta(0))
    d = late.evaluate(pair, "1h", as_of)
    assert d is not None and d.decision == "HOLD" and d.reasons[0] == "publication_too_late"
    assert engine_conn.execute("select count(*) from public.signals").fetchone() == (0,)


def test_changed_parameters_require_a_new_version(engine_conn: psycopg.Connection) -> None:
    ensure_strategy(engine_conn, strat.KEY, strat.VERSION, strat.NAME, strat.DESCRIPTION, Params())
    with pytest.raises(StrategyChangedError):
        ensure_strategy(engine_conn, strat.KEY, strat.VERSION, strat.NAME, strat.DESCRIPTION, Params(min_net_rr=1.2))
