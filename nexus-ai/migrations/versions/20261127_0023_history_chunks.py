"""history chunks: compressed broker price history for research (Exness MT5 first)

The read-only bridge can export intraday history (M5, H1) and daily history (D1) from
the owner's MetaTrader 5 terminal. Each chunk (symbol, timeframe, period) is stored as
gzip-compressed CSV, which keeps years of bars to a few megabytes. Row-level security is
enabled with no policies, and the Supabase API roles get no access, as for every other
table.

Revision ID: 0023_history_chunks
Revises: 0022_broker_bridges
Create Date: 2026-11-27
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0023_history_chunks"
down_revision: str | None = "0022_broker_bridges"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "history_chunks",
        sa.Column("id", sa.BigInteger, sa.Identity(always=True), nullable=False),
        sa.Column("venue", sa.String(32), nullable=False),
        sa.Column("symbol", sa.String(32), nullable=False),
        sa.Column("broker_symbol", sa.String(32), nullable=False),
        sa.Column("timeframe", sa.String(4), nullable=False),
        sa.Column("period", sa.String(16), nullable=False),
        sa.Column("bar_count", sa.Integer, nullable=False),
        sa.Column("first_open", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_open", sa.DateTime(timezone=True), nullable=False),
        sa.Column("server_utc_offset_hours", sa.SmallInteger, nullable=False),
        sa.Column("sha256", sa.String(64), nullable=False),
        sa.Column("data", sa.LargeBinary, nullable=False),
        sa.Column("bridge_id", sa.BigInteger, nullable=True),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("venue IN ('exness_mt5')", name="ck_history_chunks_venue_valid"),
        sa.CheckConstraint("timeframe IN ('M5', 'H1', 'D1')",
                           name="ck_history_chunks_timeframe_valid"),
        sa.CheckConstraint("bar_count > 0", name="ck_history_chunks_bar_count_positive"),
        sa.CheckConstraint("length(sha256) = 64", name="ck_history_chunks_sha256_len"),
        sa.ForeignKeyConstraint(["bridge_id"], ["broker_bridges.id"], ondelete="SET NULL",
                                name="fk_history_chunks_bridge_id_broker_bridges"),
        sa.UniqueConstraint("venue", "symbol", "timeframe", "period",
                            name="uq_history_chunks_venue_symbol_timeframe_period"),
        sa.PrimaryKeyConstraint("id", name="pk_history_chunks"),
    )
    op.execute("ALTER TABLE history_chunks ENABLE ROW LEVEL SECURITY")
    op.execute("""
    DO $$
    BEGIN
      IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'anon') THEN
        EXECUTE 'REVOKE ALL ON history_chunks FROM anon';
      END IF;
      IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'authenticated') THEN
        EXECUTE 'REVOKE ALL ON history_chunks FROM authenticated';
      END IF;
      IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'nexus_app') THEN
        EXECUTE 'GRANT SELECT, INSERT, UPDATE ON history_chunks TO nexus_app';
      END IF;
    END $$;
    """)


def downgrade() -> None:
    op.drop_table("history_chunks")
