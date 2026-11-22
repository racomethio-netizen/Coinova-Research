"""forward test events: append-only ledger of pre-registered forward paper tests

Same protections as carry_paper_events: row-level security on with no policies, the
Supabase API roles revoked, the application role limited to INSERT/SELECT, and a trigger
that blocks UPDATE/DELETE.

Revision ID: 0009_forward_tests
Revises: 0008_carry_paper
Create Date: 2026-11-22 12:05:33.914703+00:00
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = '0009_forward_tests'
down_revision: str | None = '0008_carry_paper'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table('forward_test_events',
    sa.Column('id', sa.BigInteger(), sa.Identity(always=True), nullable=False),
    sa.Column('run', sa.String(length=32), nullable=False),
    sa.Column('symbol', sa.String(length=16), nullable=False),
    sa.Column('kind', sa.String(length=12), nullable=False),
    sa.Column('key', sa.String(length=40), nullable=False),
    sa.Column('at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('data', postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'{}'::jsonb"), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint("kind IN ('trade', 'gap')", name=op.f('ck_forward_test_events_kind_valid')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_forward_test_events')),
    sa.UniqueConstraint('run', 'symbol', 'kind', 'key', name='one_event_per_key')
    )
    op.create_index('ix_forward_test_events_run_at', 'forward_test_events', ['run', 'at'], unique=False)
    op.execute("ALTER TABLE forward_test_events ENABLE ROW LEVEL SECURITY")
    op.execute("""
    CREATE OR REPLACE FUNCTION forward_test_events_append_only() RETURNS trigger
    LANGUAGE plpgsql SET search_path = '' AS $$
    BEGIN
      RAISE EXCEPTION 'forward_test_events is append-only';
    END $$;
    """)
    op.execute("""
    CREATE TRIGGER forward_test_events_no_change BEFORE UPDATE OR DELETE ON forward_test_events
    FOR EACH ROW EXECUTE FUNCTION forward_test_events_append_only();
    """)
    op.execute("""
    DO $$
    BEGIN
      IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'anon') THEN
        EXECUTE 'REVOKE ALL ON forward_test_events FROM anon';
      END IF;
      IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'authenticated') THEN
        EXECUTE 'REVOKE ALL ON forward_test_events FROM authenticated';
      END IF;
      IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'nexus_app') THEN
        EXECUTE 'GRANT SELECT, INSERT ON forward_test_events TO nexus_app';
      END IF;
    END $$;
    """)


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS forward_test_events_no_change ON forward_test_events")
    op.execute("DROP FUNCTION IF EXISTS forward_test_events_append_only()")
    op.drop_index('ix_forward_test_events_run_at', table_name='forward_test_events')
    op.drop_table('forward_test_events')
