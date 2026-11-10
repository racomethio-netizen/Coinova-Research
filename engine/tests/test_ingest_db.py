"""Ingestion against real PostgreSQL + migrations, connected as `nexus_engine`.
Synthetic candles only (test data, never shown to users)."""

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import psycopg
import pytest
from conftest import synthetic_candles

from nexus_engine.config import Settings
from nexus_engine.marketdata.ingest import Ingestor, Pair, load_pairs, load_source
from nexus_engine.marketdata.types import Candle, PricePoint, SourceNotPermittedError

pytestmark = pytest.mark.db
H = timedelta(hours=1)
T0 = datetime(2026, 1, 1, tzinfo=UTC)
NOW = T0 + 10 * H + timedelta(minutes=5)  # candles 00:00..09:00 are closed


class FakeSource:
    def __init__(self, candles: list[Candle], missing: set[datetime] | None = None) -> None:
        self.candles = {c.open_time: c for c in candles}
        self.missing = missing or set()
        self.calls = 0

    def fetch_klines(self, symbol: str, tf: str, start: datetime | None = None, end: datetime | None = None, limit: int = 1000) -> list[Candle]:
        self.calls += 1
        out = [c for t, c in sorted(self.candles.items()) if (start is None or t >= start) and (end is None or t <= end) and t not in self.missing]
        return out[:limit]

    def fetch_price(self, symbol: str) -> PricePoint:
        return PricePoint(Decimal("123.45"), NOW)


def setup(conn: psycopg.Connection, source: FakeSource, **settings: str) -> tuple[Ingestor, Pair]:
    src = load_source(conn, "binance_spot")
    pair = next(p for p in load_pairs(conn, src.id) if p.symbol == "BTCUSDT")
    ing = Ingestor(conn, src, source, Settings(ingest_initial_bars=10, **settings), now=lambda: NOW)  # type: ignore[arg-type]
    return ing, pair


def count(conn: psycopg.Connection, sql: str, *args: object) -> int:
    row = conn.execute(sql, args).fetchone()
    conn.commit()
    assert row is not None
    return int(row[0])


def test_incremental_ingest_is_complete_and_idempotent(engine_conn: psycopg.Connection) -> None:
    ing, pair = setup(engine_conn, FakeSource(synthetic_candles(T0, 12)))  # includes 2 candles in the future
    r = ing.ingest_incremental(pair, "1h")
    assert (r.upserted, r.rejected, r.gaps_recorded) == (10, 0, 0)
    assert count(engine_conn, "select count(*) from public.market_candles where pair_id = %s", pair.id) == 10
    assert ing.ingest_incremental(pair, "1h").upserted == 0  # nothing new: no duplicates
    run = engine_conn.execute("select kind, status, candles_upserted from public.ingest_runs order by id limit 1").fetchone()
    assert run == ("incremental", "succeeded", 10)


def test_revised_final_candle_is_recorded_not_applied(engine_conn: psycopg.Connection) -> None:
    candles = synthetic_candles(T0, 10)
    ing, pair = setup(engine_conn, FakeSource(candles))
    ing.backfill(pair, "1h", T0, T0 + 9 * H)
    revised = replace(candles[3], close=candles[3].close + Decimal("0.5"))
    r = ing.import_candles(pair, "1h", [revised], kind="backfill")
    assert (r.upserted, r.revisions) == (0, 1)
    stored = engine_conn.execute("select close from public.market_candles where pair_id = %s and open_time = %s", (pair.id, candles[3].open_time)).fetchone()
    assert stored is not None and stored[0] == candles[3].close
    rev = engine_conn.execute("select original->>'close', revised->>'close' from public.candle_revisions").fetchone()
    assert rev == (str(candles[3].close), str(revised.close))


def test_invalid_candles_are_rejected_and_reported(engine_conn: psycopg.Connection) -> None:
    candles = synthetic_candles(T0, 10)
    candles[4] = replace(candles[4], high=candles[4].open - 1)  # inconsistent OHLC
    ing, pair = setup(engine_conn, FakeSource(candles))
    r = ing.backfill(pair, "1h", T0, T0 + 9 * H)
    assert (r.upserted, r.rejected, r.gaps_recorded) == (9, 1, 1)
    run = engine_conn.execute("select status, error->'rejections'->0->'reasons' from public.ingest_runs").fetchone()
    assert run == ("partial", ["inconsistent_ohlc"])


def test_missing_candles_become_gaps_and_unrecoverable_after_retries(engine_conn: psycopg.Connection) -> None:
    missing = {T0 + 4 * H, T0 + 5 * H}
    src = FakeSource(synthetic_candles(T0, 10), missing)
    ing, pair = setup(engine_conn, src)
    assert ing.backfill(pair, "1h", T0, T0 + 9 * H).gaps_recorded == 1
    gap = engine_conn.execute("select gap_start, gap_end, status from public.candle_gaps").fetchone()
    assert gap == (T0 + 4 * H, T0 + 6 * H, "open")
    for _ in range(3):
        ing.resolve_gaps(pair, "1h")
    gap = engine_conn.execute("select status, attempts from public.candle_gaps").fetchone()
    assert gap == ("unrecoverable", 3)
    # Never interpolated: the hours are still absent.
    assert count(engine_conn, "select count(*) from public.market_candles where open_time = any(%s)", list(missing)) == 0


def test_gap_is_filled_when_the_source_has_the_data(engine_conn: psycopg.Connection) -> None:
    src = FakeSource(synthetic_candles(T0, 10), {T0 + 2 * H})
    ing, pair = setup(engine_conn, src)
    ing.backfill(pair, "1h", T0, T0 + 9 * H)
    src.missing.clear()
    assert ing.resolve_gaps(pair, "1h") == (1, 0)
    assert count(engine_conn, "select count(*) from public.market_candles where pair_id = %s", pair.id) == 10


def test_prices_and_heartbeats(engine_conn: psycopg.Connection) -> None:
    ing, pair = setup(engine_conn, FakeSource([]))
    ing.update_price(pair)
    ing.update_price(pair)
    assert count(engine_conn, "select count(*) from public.latest_prices") == 1
    ing.heartbeat("marketdata.worker", {"ok": True})
    assert count(engine_conn, "select count(*) from public.engine_heartbeats") == 1


def test_gate_g4_production_refuses_unreviewed_sources(engine_conn: psycopg.Connection, superuser: psycopg.Connection) -> None:
    src = load_source(engine_conn, "binance_spot")
    prod = Settings(app_env="production", database_url="postgres://x@localhost/y")  # type: ignore[arg-type]
    with pytest.raises(SourceNotPermittedError, match="G-4"):
        Ingestor(engine_conn, src, FakeSource([]), prod)
    superuser.execute("update public.market_sources set terms_reviewed_at = now() where key = 'binance_spot'")
    Ingestor(engine_conn, load_source(engine_conn, "binance_spot"), FakeSource([]), prod)
    superuser.execute("update public.market_sources set enabled = false where key = 'binance_spot'")
    with pytest.raises(SourceNotPermittedError, match="disabled"):
        Ingestor(engine_conn, load_source(engine_conn, "binance_spot"), FakeSource([]), Settings())


def test_engine_role_is_least_privilege(engine_conn: psycopg.Connection) -> None:
    for sql in ("select 1 from public.profiles", "delete from public.market_candles", "update public.market_sources set enabled = false"):
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            engine_conn.execute(sql)
        engine_conn.rollback()
