"""carry paper events: append-only ledger of the forward funding-carry paper test

Row-level security is enabled with no policies and the Supabase API roles (anon,
authenticated) get no access, matching every other table. The application role may only
INSERT and SELECT (append-only, like audit_logs), and a trigger blocks UPDATE/DELETE so
results cannot be edited after the fact.

Revision ID: 0008_carry_paper
Revises: 0007_phase6_strategies
Create Date: 2026-11-22 11:55:06.575010+00:00
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = '0008_carry_paper'
down_revision: str | None = '0007_phase6_strategies'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table('carry_paper_events',
    sa.Column('id', sa.BigInteger(), sa.Identity(always=True), nullable=False),
    sa.Column('run', sa.String(length=32), nullable=False),
    sa.Column('symbol', sa.String(length=16), nullable=False),
    sa.Column('kind', sa.String(length=12), nullable=False),
    sa.Column('at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('data', postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'{}'::jsonb"), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint("kind IN ('entry', 'funding', 'rebalance', 'mark', 'gap')", name=op.f('ck_carry_paper_events_kind_valid')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_carry_paper_events')),
    sa.UniqueConstraint('run', 'symbol', 'kind', 'at', name='one_event_per_time')
    )
    op.create_index('ix_carry_paper_events_run_at', 'carry_paper_events', ['run', 'at'], unique=False)
    op.execute("ALTER TABLE carry_paper_events ENABLE ROW LEVEL SECURITY")
    op.execute("""
    CREATE OR REPLACE FUNCTION carry_paper_events_append_only() RETURNS trigger
    LANGUAGE plpgsql SET search_path = '' AS $$
    BEGIN
      RAISE EXCEPTION 'carry_paper_events is append-only';
    END $$;
    """)
    op.execute("""
    CREATE TRIGGER carry_paper_events_no_change BEFORE UPDATE OR DELETE ON carry_paper_events
    FOR EACH ROW EXECUTE FUNCTION carry_paper_events_append_only();
    """)
    op.execute("""
    DO $$
    BEGIN
      IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'anon') THEN
        EXECUTE 'REVOKE ALL ON carry_paper_events FROM anon';
      END IF;
      IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'authenticated') THEN
        EXECUTE 'REVOKE ALL ON carry_paper_events FROM authenticated';
      END IF;
      IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'nexus_app') THEN
        EXECUTE 'GRANT SELECT, INSERT ON carry_paper_events TO nexus_app';
      END IF;
    END $$;
    """)


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS carry_paper_events_no_change ON carry_paper_events")
    op.execute("DROP FUNCTION IF EXISTS carry_paper_events_append_only()")
    op.drop_index('ix_carry_paper_events_run_at', table_name='carry_paper_events')
    op.drop_table('carry_paper_events')
