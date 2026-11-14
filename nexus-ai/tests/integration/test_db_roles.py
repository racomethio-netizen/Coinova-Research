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
