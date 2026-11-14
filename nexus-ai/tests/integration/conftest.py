"""Integration fixtures. Require NEXUS_TEST_DATABASE_URL (a DISPOSABLE database: it is
dropped and recreated schema-wise) and NEXUS_TEST_REDIS_URL. Skipped when unset."""

from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import Engine, create_engine, text

ROOT = Path(__file__).resolve().parents[2]


def _db_url() -> str:
    url = os.environ.get("NEXUS_TEST_DATABASE_URL")
    if not url:
        pytest.skip("NEXUS_TEST_DATABASE_URL not set")
    if "test" not in url.rsplit("/", 1)[-1]:
        pytest.fail("refusing to run destructive tests on a database without 'test' in its name")
    return url


def alembic_config(url: str) -> Config:
    cfg = Config(str(ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(ROOT / "migrations"))
    os.environ["NEXUS_DATABASE_URL"] = url
    return cfg


@pytest.fixture(scope="session")
def db_url() -> str:
    return _db_url()


@pytest.fixture(scope="session")
def migrated_engine(db_url: str) -> Iterator[Engine]:
    engine = create_engine(db_url)
    with engine.begin() as conn:
        conn.execute(text("DROP SCHEMA public CASCADE; CREATE SCHEMA public;"))
    prev = os.environ.get("NEXUS_DATABASE_URL")
    command.upgrade(alembic_config(db_url), "head")
    if prev is None:
        os.environ.pop("NEXUS_DATABASE_URL", None)
    yield engine
    engine.dispose()


@pytest.fixture
def conn(migrated_engine: Engine) -> Iterator[object]:
    """A connection inside a transaction that is always rolled back."""
    with migrated_engine.connect() as connection:
        trans = connection.begin()
        try:
            yield connection
        finally:
            trans.rollback()


def truncate(engine: Engine, tables: str) -> None:
    """Test-only cleanup. audit_logs blocks TRUNCATE (append-only), and users cascades to
    it, so the guard trigger is disabled just for this statement by the table owner."""
    with engine.begin() as c:
        c.execute(text("ALTER TABLE audit_logs DISABLE TRIGGER audit_logs_no_truncate"))
        c.execute(text(f"TRUNCATE {tables} RESTART IDENTITY CASCADE"))
        c.execute(text("ALTER TABLE audit_logs ENABLE TRIGGER audit_logs_no_truncate"))
