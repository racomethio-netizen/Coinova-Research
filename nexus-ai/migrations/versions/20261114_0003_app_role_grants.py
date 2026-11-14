"""least-privilege grants for the application role

Migrations run as the schema OWNER. Services connect as ``nexus_app`` (created by
infrastructure/docker/postgres-init/01-roles.sh), which:
* has DML on application tables but no TRUNCATE, no DDL, and does not own tables, so it
  cannot disable triggers;
* may only INSERT/SELECT ``audit_logs`` (append-only even if app credentials leak);
* cannot modify reference tables (exchanges) except ``is_enabled``/``capabilities``.
Skipped (no-op) if the role does not exist, e.g. on a developer database.

Revision ID: 0003_app_role_grants
Revises: 0002_order_intent
Create Date: 2026-11-14
"""
from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0003_app_role_grants"
down_revision: str | None = "0002_order_intent"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

GRANTS = """
DO $$
BEGIN
  IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'nexus_app')
     AND to_regclass('public.audit_logs') IS NOT NULL THEN
    EXECUTE 'GRANT USAGE ON SCHEMA public TO nexus_app';
    EXECUTE 'REVOKE ALL ON ALL TABLES IN SCHEMA public FROM nexus_app';
    EXECUTE 'GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO nexus_app';
    EXECUTE 'REVOKE UPDATE, DELETE ON audit_logs FROM nexus_app';
    EXECUTE 'REVOKE INSERT, DELETE ON exchanges, strategies FROM nexus_app';
    EXECUTE 'REVOKE ALL ON alembic_version FROM nexus_app';
    EXECUTE 'GRANT SELECT ON alembic_version TO nexus_app';
    EXECUTE 'GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO nexus_app';
  END IF;
END $$;
"""


def upgrade() -> None:
    op.execute(GRANTS)


def downgrade() -> None:
    op.execute("""
    DO $$
    BEGIN
      IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'nexus_app') THEN
        EXECUTE 'REVOKE ALL ON ALL TABLES IN SCHEMA public FROM nexus_app';
        EXECUTE 'REVOKE ALL ON ALL SEQUENCES IN SCHEMA public FROM nexus_app';
      END IF;
    END $$;
    """)
