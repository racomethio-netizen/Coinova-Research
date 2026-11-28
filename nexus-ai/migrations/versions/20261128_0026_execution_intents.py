"""execution intents: approved proposals on their way to the paper/demo brokers

One row per trade NEXUS decided to place after the orchestrator proposed it and the
portfolio risk engine approved it. It carries its own client ID (sent to the broker, never
re-used), its lifecycle (pending -> sent -> filled -> protected -> closed, or rejected /
expired), the fill price and slippage, whether the stop-loss was verified on the broker,
and a list of events. At most one ACTIVE intent per venue and instrument (partial unique
index): a second order for the same instrument cannot be created. Paper and DEMO venues
only. Row-level security on; the Supabase API roles get no access.

Revision ID: 0026_execution_intents
Revises: 0025_orchestrator_decisions
Create Date: 2026-11-28
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0026_execution_intents"
down_revision: str | None = "0025_orchestrator_decisions"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

MONEY = sa.Numeric(38, 18)
RATIO = sa.Numeric(20, 10)


def upgrade() -> None:
    op.create_table(
        "execution_intents",
        sa.Column("id", sa.BigInteger, sa.Identity(always=True), nullable=False),
        sa.Column("client_id", sa.String(31), nullable=False),
        sa.Column("venue", sa.String(16), nullable=False),
        sa.Column("market", sa.String(16), nullable=False),
        sa.Column("symbol", sa.String(32), nullable=False),
        sa.Column("side", sa.SmallInteger, nullable=False),
        sa.Column("kind", sa.String(12), nullable=False),
        sa.Column("module", sa.String(40), nullable=False),
        sa.Column("status", sa.String(12), server_default="pending", nullable=False),
        sa.Column("quantity", MONEY, nullable=True),
        sa.Column("notional_usd", MONEY, nullable=True),
        sa.Column("entry_ref", MONEY, nullable=True),
        sa.Column("stop", MONEY, nullable=True),
        sa.Column("target", MONEY, nullable=True),
        sa.Column("order_id", sa.BigInteger, nullable=True),
        sa.Column("broker_ref", sa.String(40), nullable=True),
        sa.Column("fill_price", MONEY, nullable=True),
        sa.Column("slippage", MONEY, nullable=True),
        sa.Column("slippage_bps", RATIO, nullable=True),
        sa.Column("stop_verified", sa.Boolean, nullable=True),
        sa.Column("partial_taken", sa.Boolean, server_default=sa.text("false"), nullable=False),
        sa.Column("events", postgresql.JSONB(astext_type=sa.Text()),
                  server_default=sa.text("'[]'::jsonb"), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(),
                  nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(),
                  nullable=False),
        sa.CheckConstraint("venue IN ('crypto_paper', 'exness_demo')",
                           name="ck_execution_intents_venue_valid"),
        sa.CheckConstraint("market IN ('forex', 'crypto')",
                           name="ck_execution_intents_market_valid"),
        sa.CheckConstraint("side IN (-1, 1)", name="ck_execution_intents_side_valid"),
        sa.CheckConstraint("kind IN ('bracket', 'allocation')",
                           name="ck_execution_intents_kind_valid"),
        sa.CheckConstraint("status IN ('pending', 'sent', 'filled', 'protected', 'closed', "
                           "'rejected', 'expired')", name="ck_execution_intents_status_valid"),
        sa.CheckConstraint("kind = 'allocation' OR (stop IS NOT NULL AND target IS NOT NULL)",
                           name="ck_execution_intents_bracket_has_stop"),
        sa.UniqueConstraint("client_id", name="uq_execution_intents_client_id"),
        sa.PrimaryKeyConstraint("id", name="pk_execution_intents"),
    )
    op.create_index("ix_execution_intents_one_active", "execution_intents",
                    ["venue", "symbol"], unique=True,
                    postgresql_where=sa.text(
                        "status IN ('pending', 'sent', 'filled', 'protected')"))
    op.create_index("ix_execution_intents_status", "execution_intents", ["status", "venue"])
    op.execute("ALTER TABLE execution_intents ENABLE ROW LEVEL SECURITY")
    op.execute("""
    DO $$
    BEGIN
      IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'anon') THEN
        EXECUTE 'REVOKE ALL ON execution_intents FROM anon';
      END IF;
      IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'authenticated') THEN
        EXECUTE 'REVOKE ALL ON execution_intents FROM authenticated';
      END IF;
      IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'nexus_app') THEN
        EXECUTE 'GRANT SELECT, INSERT, UPDATE ON execution_intents TO nexus_app';
      END IF;
    END $$;
    """)


def downgrade() -> None:
    op.drop_table("execution_intents")
