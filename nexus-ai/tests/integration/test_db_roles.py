"""The application role cannot tamper with audit logs, schema or reference data."""

from __future__ import annotations

import pytest
from sqlalchemy import Engine, create_engine, text
from sqlalchemy.exc import DBAPIError

pytestmark = pytest.mark.db


@pytest.fixture
def app_engine(migrated_engine: Engine, db_url: str) -> Engine:
    with migrated_engine.connect() as c:
        if not c.execute(text("SELECT 1 FROM pg_roles WHERE rolname='nexus_app'")).scalar():
            pytest.skip("nexus_app role not present on this server")
    url = db_url.replace("//nexus:nexus_dev_only@", "//nexus_app:app_dev_only@")
    return create_engine(url)


def denied(engine: Engine, sql: str) -> None:
    with engine.connect() as c, pytest.raises(DBAPIError) as info:
        c.execute(text(sql))
    assert info.value.orig.sqlstate == "42501", info.value.orig  # type: ignore[union-attr]


def test_app_role_can_do_dml(app_engine: Engine) -> None:
    with app_engine.begin() as c:
        c.execute(text("INSERT INTO system_events (component, event_type, severity, message) "
                       "VALUES ('test','x','info','app role write')"))
        c.execute(text("INSERT INTO audit_logs (actor, action) VALUES ('test', 'app_insert')"))
        assert c.execute(text("SELECT count(*) FROM audit_logs")).scalar() >= 1
        c.execute(text("DELETE FROM system_events WHERE message = 'app role write'"))


@pytest.mark.parametrize("sql", [
    "UPDATE audit_logs SET action = 'x'",
    "DELETE FROM audit_logs",
    "TRUNCATE audit_logs",
    "TRUNCATE orders",
    "ALTER TABLE audit_logs DISABLE TRIGGER audit_logs_append_only",
    "DROP TABLE orders",
    "CREATE TABLE evil (id int)",
    "INSERT INTO exchanges (id, code, name) VALUES (99, 'evil', 'Evil')",
    "DELETE FROM strategies",
    "UPDATE alembic_version SET version_num = 'x'",
])
def test_app_role_denied(app_engine: Engine, sql: str) -> None:
    denied(app_engine, sql)


def test_grant_script_is_idempotent_and_keeps_ledgers_append_only(
        migrated_engine: Engine, app_engine: Engine) -> None:
    from pathlib import Path

    sql = (Path(__file__).parents[2] / "scripts" / "grant_app_role.sql").read_text()
    for _ in range(2):
        raw = migrated_engine.raw_connection()
        try:
            raw.cursor().execute(sql)   # no parameters: '%I' reaches Postgres untouched
            raw.commit()
        finally:
            raw.close()
    with migrated_engine.connect() as c:
        def can(table: str, priv: str) -> bool:
            return bool(c.execute(text("SELECT has_table_privilege('nexus_app', :t, :p)"),
                                  {"t": table, "p": priv}).scalar())
        assert can("orders", "UPDATE") and can("audit_logs", "INSERT")
        assert not can("audit_logs", "UPDATE") and not can("audit_logs", "DELETE")
        assert can("carry_paper_events", "INSERT")
        assert not can("carry_paper_events", "UPDATE")
        assert not can("forward_test_events", "DELETE")
        assert not can("strategies", "DELETE") and not can("alembic_version", "UPDATE")
    denied(app_engine, "DELETE FROM forward_test_events")


def test_app_role_can_write_rls_protected_ledgers(migrated_engine: Engine,
                                                  app_engine: Engine) -> None:
    """RLS without policies used to lock nexus_app out (migration 0010 fixes that)."""
    try:
        _write_ledger_row(app_engine)
    finally:  # append-only for the app; the owner clears test rows
        with migrated_engine.begin() as c:
            c.execute(text("TRUNCATE forward_test_events"))


def _write_ledger_row(app_engine: Engine) -> None:
    with app_engine.begin() as c:
        c.execute(text("INSERT INTO forward_test_events (run, symbol, kind, key, at) VALUES "
                       "('t-rls', 'BTCUSDT', 'gap', 'k-rls', now())"))
        assert c.execute(text("SELECT count(*) FROM forward_test_events WHERE run='t-rls'")
                         ).scalar() == 1
    denied(app_engine, "UPDATE forward_test_events SET key='x' WHERE run='t-rls'")
