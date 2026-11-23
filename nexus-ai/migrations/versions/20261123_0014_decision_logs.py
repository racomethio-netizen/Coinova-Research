"""decision log: append-only record of every fusion decision

Protections as for the research ledgers (0008/0009): row-level security on, the Supabase
API roles revoked, the application role limited to SELECT/INSERT/DELETE plus its RLS
policy, and a trigger that blocks every UPDATE and any DELETE of a row younger than the
180-day retention period (so pruning old rows is possible, rewriting history is not).

Revision ID: 0014_decision_logs
Revises: 0013_stop_limit_orders
Create Date: 2026-11-23 05:38:53.601497+00:00
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = '0014_decision_logs'
down_revision: str | None = '0013_stop_limit_orders'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table('decision_logs',
    sa.Column('id', sa.BigInteger(), sa.Identity(always=True), nullable=False),
    sa.Column('mode', sa.String(length=8), nullable=False),
    sa.Column('exchange', sa.String(length=16), nullable=False),
    sa.Column('symbol', sa.String(length=32), nullable=False),
    sa.Column('bar_time', sa.DateTime(timezone=True), nullable=False),
    sa.Column('decided_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('snapshot_id', sa.String(length=160), nullable=False),
    sa.Column('decision', sa.String(length=10), nullable=False),
    sa.Column('strategy', sa.String(length=64), nullable=True),
    sa.Column('regime', sa.String(length=16), nullable=True),
    sa.Column('risk_state', sa.String(length=16), nullable=True),
    sa.Column('probability', sa.Numeric(precision=20, scale=10), nullable=True),
    sa.Column('expected_return', sa.Numeric(precision=20, scale=10), nullable=True),
    sa.Column('features', postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'{}'::jsonb"), nullable=False),
    sa.Column('models_used', postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'{}'::jsonb"), nullable=False),
    sa.Column('strategy_signals', postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'{}'::jsonb"), nullable=False),
    sa.Column('risk_checks', postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'{}'::jsonb"), nullable=False),
    sa.Column('reasons', postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'{}'::jsonb"), nullable=False),
    sa.Column('rejection_reason', sa.Text(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint("decision IN ('LONG', 'SHORT', 'WAIT', 'NO_TRADE')", name=op.f('ck_decision_logs_decision_valid')),
    sa.CheckConstraint("mode IN ('paper', 'live')", name=op.f('ck_decision_logs_mode_valid')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_decision_logs')),
    sa.UniqueConstraint('mode', 'exchange', 'symbol', 'bar_time', name='one_decision_per_bar')
    )
    op.create_index('ix_decision_logs_symbol_decided', 'decision_logs', ['symbol', 'decided_at'], unique=False)
    op.execute("ALTER TABLE decision_logs ENABLE ROW LEVEL SECURITY")
    op.execute("""
    CREATE OR REPLACE FUNCTION decision_logs_append_only() RETURNS trigger
    LANGUAGE plpgsql SET search_path = '' AS $$
    BEGIN
      IF TG_OP = 'DELETE' AND OLD.decided_at < now() - interval '180 days' THEN
        RETURN OLD;
      END IF;
      RAISE EXCEPTION 'decision_logs is append-only (rows older than 180 days may be deleted)';
    END $$;
    """)
    op.execute("""
    CREATE TRIGGER decision_logs_no_change BEFORE UPDATE OR DELETE ON decision_logs
    FOR EACH ROW EXECUTE FUNCTION decision_logs_append_only();
    """)
    op.execute("""
    DO $$
    BEGIN
      IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'anon') THEN
        EXECUTE 'REVOKE ALL ON decision_logs FROM anon';
      END IF;
      IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'authenticated') THEN
        EXECUTE 'REVOKE ALL ON decision_logs FROM authenticated';
      END IF;
      IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'nexus_app') THEN
        EXECUTE 'GRANT SELECT, INSERT, DELETE ON decision_logs TO nexus_app';
        EXECUTE 'CREATE POLICY nexus_app_access ON public.decision_logs FOR ALL TO nexus_app '
                'USING (true) WITH CHECK (true)';
      END IF;
    END $$;
    """)


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS decision_logs_no_change ON decision_logs")
    op.execute("DROP FUNCTION IF EXISTS decision_logs_append_only()")
    op.drop_index('ix_decision_logs_symbol_decided', table_name='decision_logs')
    op.drop_table('decision_logs')
