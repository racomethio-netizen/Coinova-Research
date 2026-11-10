"""Shared fixtures. DB tests build a disposable `nexus_engine_test` database from
the Supabase shim plus every migration, then run as the `nexus_engine` role.
They refuse non-local hosts, mirroring tests/db/global-setup.ts."""

from __future__ import annotations

import os
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from urllib.parse import urlparse

import psycopg
import pytest

from nexus_engine.marketdata.types import Candle
from nexus_engine.timeframes import close_time_for, tf_delta

ROOT = Path(__file__).resolve().parents[2]
DB_NAME = "nexus_engine_test"


def admin_url(db: str = "postgres") -> str:
    base = os.environ.get("TEST_DATABASE_URL", f"postgres://postgres@127.0.0.1:{os.environ.get('TEST_DB_PORT', '54329')}/postgres")
    u = urlparse(base)
    if u.hostname not in ("127.0.0.1", "localhost", "::1"):
        raise RuntimeError(f"Refusing to run destructive DB tests against non-local host {u.hostname}")
    return u._replace(path=f"/{db}").geturl()


def synthetic_candles(start: datetime, count: int, tf: str = "1h", base: Decimal = Decimal("100")) -> list[Candle]:
    """Deterministic, obviously synthetic candles for tests only (never shown to users)."""
    out: list[Candle] = []
    for i in range(count):
        t = start + tf_delta(tf) * i
        o = base + i
        out.append(
            Candle(
                open_time=t,
                close_time=close_time_for(t, tf),
                open=o,
                high=o + 2,
                low=o - 1,
                close=o + 1,
                volume=Decimal("10.5"),
                quote_volume=Decimal("1050.25"),
                trade_count=42,
                is_final=True,
            )
        )
    return out


@pytest.fixture
def t0() -> datetime:
    return datetime(2026, 1, 1, tzinfo=UTC)


@pytest.fixture(scope="session")
def engine_db_url() -> Iterator[str]:
    try:
        admin = psycopg.connect(admin_url(), autocommit=True, connect_timeout=3)
    except psycopg.OperationalError:
        pytest.skip("test PostgreSQL not running (scripts/db-test.sh up)")
    with admin:
        admin.execute(f"drop database if exists {DB_NAME} with (force)")
        admin.execute(f"create database {DB_NAME}")
    with psycopg.connect(admin_url(DB_NAME), autocommit=True) as db:
        db.execute((ROOT / "tests/db/supabase-shim.sql").read_text())  # type: ignore[arg-type]
        for f in sorted((ROOT / "supabase/migrations").glob("*.sql")):
            try:
                db.execute(f.read_text())  # type: ignore[arg-type]
            except psycopg.Error as e:
                raise RuntimeError(f"Migration {f.name} failed: {e}") from e
    # Every engine connection runs as the least-privilege role, as in production.
    yield admin_url(DB_NAME) + "?options=-c%20role%3Dnexus_engine"


@pytest.fixture
def superuser(engine_db_url: str) -> Iterator[psycopg.Connection]:
    with psycopg.connect(admin_url(DB_NAME), autocommit=True) as c:
        yield c


@pytest.fixture
def engine_conn(engine_db_url: str, superuser: psycopg.Connection) -> Iterator[psycopg.Connection]:
    # Fresh market-data state per test (TRUNCATE as superuser; the engine cannot delete candles).
    superuser.execute(
        "truncate public.market_candles, public.candle_revisions, public.candle_gaps, public.latest_prices, "
        "public.engine_heartbeats, public.ingest_runs restart identity cascade"
    )
    superuser.execute("update public.market_sources set terms_reviewed_at = null, enabled = true")
    with psycopg.connect(engine_db_url) as c:
        yield c


def utc(*args: int) -> datetime:
    return datetime(*args, tzinfo=UTC)  # type: ignore[misc]


__all__ = ["synthetic_candles", "timedelta", "utc"]
