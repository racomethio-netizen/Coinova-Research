"""grid paper events: append-only ledger of the paper grid bot (owner request 2026-11-23)

Row-level security is enabled with no policies and the Supabase API roles (anon,
authenticated) get no access, matching every other table. The application role may only
INSERT and SELECT, and a trigger blocks UPDATE/DELETE so results cannot be edited.

Revision ID: 0019_grid_paper
Revises: 0018_bot_strategy
Create Date: 2026-11-23
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0019_grid_paper"
down_revision: str | None = "0018_bot_strategy"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "grid_paper_events",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), nullable=False),
        sa.Column("run", sa.String(length=32), nullable=False),
        sa.Column("seq", sa.Integer(), nullable=False),
        sa.Column("symbol", sa.String(length=16), nullable=False),
        sa.Column("kind", sa.String(length=12), nullable=False),
        sa.Column("at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("data", postgresql.JSONB(astext_type=sa.Text()),
                  server_default=sa.text("'{}'::jsonb"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"),
                  nullable=False),
        sa.CheckConstraint("kind IN ('start', 'fill', 'mark', 'stop', 'restart', 'gap')",
                           name=op.f("ck_grid_paper_events_kind_valid")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_grid_paper_events")),
        sa.UniqueConstraint("run", "seq", name="one_event_per_seq"),
    )
    op.create_index("ix_grid_paper_events_run_at", "grid_paper_events", ["run", "at"],
                    unique=False)
    op.execute("ALTER TABLE grid_paper_events ENABLE ROW LEVEL SECURITY")
    op.execute("""
    CREATE OR REPLACE FUNCTION grid_paper_events_append_only() RETURNS trigger
    LANGUAGE plpgsql SET search_path = '' AS $$
    BEGIN
      RAISE EXCEPTION 'grid_paper_events is append-only';
    END $$;
    """)
    op.execute("""
    CREATE TRIGGER grid_paper_events_no_change BEFORE UPDATE OR DELETE ON grid_paper_events
    FOR EACH ROW EXECUTE FUNCTION grid_paper_events_append_only();
    """)
    op.execute("""
    DO $$
    BEGIN
      IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'anon') THEN
        EXECUTE 'REVOKE ALL ON grid_paper_events FROM anon';
      END IF;
      IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'authenticated') THEN
        EXECUTE 'REVOKE ALL ON grid_paper_events FROM authenticated';
      END IF;
      IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'nexus_app') THEN
        EXECUTE 'GRANT SELECT, INSERT ON grid_paper_events TO nexus_app';
      END IF;
    END $$;
    """)


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS grid_paper_events_no_change ON grid_paper_events")
    op.execute("DROP FUNCTION IF EXISTS grid_paper_events_append_only()")
    op.drop_index("ix_grid_paper_events_run_at", table_name="grid_paper_events")
    op.drop_table("grid_paper_events")
