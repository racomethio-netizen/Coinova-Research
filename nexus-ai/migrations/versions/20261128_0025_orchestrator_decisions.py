"""orchestrator decisions: the strategy orchestrator's BUY / SELL / NO_TRADE log

One row per instrument when its decision changes, and at least once an hour, with the
reasons, blockers and every strategy-module signal considered. Append-only (no UPDATE
grant); rows older than 180 days are pruned. Row-level security is enabled with no
policies, and the Supabase API roles get no access, as for every other table. Nothing in
this table places an order.

Revision ID: 0025_orchestrator_decisions
Revises: 0024_strategy_modules
Create Date: 2026-11-28
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0025_orchestrator_decisions"
down_revision: str | None = "0024_strategy_modules"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

MONEY = sa.Numeric(38, 18)
RATIO = sa.Numeric(20, 10)


def upgrade() -> None:
    op.create_table(
        "orchestrator_decisions",
        sa.Column("id", sa.BigInteger, sa.Identity(always=True), nullable=False),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("market", sa.String(16), nullable=False),
        sa.Column("symbol", sa.String(32), nullable=False),
        sa.Column("action", sa.String(10), nullable=False),
        sa.Column("module", sa.String(40), nullable=True),
        sa.Column("side", sa.SmallInteger, nullable=True),
        sa.Column("entry_ref", MONEY, nullable=True),
        sa.Column("stop", MONEY, nullable=True),
        sa.Column("target", MONEY, nullable=True),
        sa.Column("reward_risk", RATIO, nullable=True),
        sa.Column("expected_r", RATIO, nullable=True),
        sa.Column("regime", sa.String(20), nullable=True),
        sa.Column("reasons", postgresql.JSONB(astext_type=sa.Text()),
                  server_default=sa.text("'[]'::jsonb"), nullable=False),
        sa.Column("blockers", postgresql.JSONB(astext_type=sa.Text()),
                  server_default=sa.text("'[]'::jsonb"), nullable=False),
        sa.Column("candidates", postgresql.JSONB(astext_type=sa.Text()),
                  server_default=sa.text("'[]'::jsonb"), nullable=False),
        sa.Column("context", postgresql.JSONB(astext_type=sa.Text()),
                  server_default=sa.text("'{}'::jsonb"), nullable=False),
        sa.Column("fingerprint", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(),
                  nullable=False),
        sa.CheckConstraint("market IN ('forex', 'crypto')",
                           name="ck_orchestrator_decisions_market_valid"),
        sa.CheckConstraint("action IN ('BUY', 'SELL', 'NO_TRADE')",
                           name="ck_orchestrator_decisions_action_valid"),
        sa.CheckConstraint("side IS NULL OR side IN (-1, 1)",
                           name="ck_orchestrator_decisions_side_valid"),
        sa.CheckConstraint("action = 'NO_TRADE' OR (module IS NOT NULL AND side IS NOT NULL)",
                           name="ck_orchestrator_decisions_trade_has_source"),
        sa.PrimaryKeyConstraint("id", name="pk_orchestrator_decisions"),
    )
    op.create_index("ix_orchestrator_decisions_symbol_decided", "orchestrator_decisions",
                    ["symbol", "decided_at"])
    op.execute("ALTER TABLE orchestrator_decisions ENABLE ROW LEVEL SECURITY")
    op.execute("""
    DO $$
    BEGIN
      IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'anon') THEN
        EXECUTE 'REVOKE ALL ON orchestrator_decisions FROM anon';
      END IF;
      IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'authenticated') THEN
        EXECUTE 'REVOKE ALL ON orchestrator_decisions FROM authenticated';
      END IF;
      IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'nexus_app') THEN
        EXECUTE 'GRANT SELECT, INSERT, DELETE ON orchestrator_decisions TO nexus_app';
      END IF;
    END $$;
    """)


def downgrade() -> None:
    op.drop_table("orchestrator_decisions")
