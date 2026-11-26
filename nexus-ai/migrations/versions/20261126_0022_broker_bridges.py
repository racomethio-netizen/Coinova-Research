"""broker bridges: access tokens for read-only broker bridges (Exness MT5 first)

A bridge runs next to the owner's MetaTrader 5 terminal and pushes market data and an
account summary to NEXUS. Only a SHA-256 hash of each token is stored. A token can be
revoked; revoked tokens are kept for the record. Row-level security is enabled with no
policies, and the Supabase API roles get no access, as for every other table.

Revision ID: 0022_broker_bridges
Revises: 0021_multi_asset_instruments
Create Date: 2026-11-26
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0022_broker_bridges"
down_revision: str | None = "0021_multi_asset_instruments"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "broker_bridges",
        sa.Column("id", sa.BigInteger, sa.Identity(always=True), nullable=False),
        sa.Column("name", sa.String(64), nullable=False),
        sa.Column("venue", sa.String(32), nullable=False),
        sa.Column("token_hash", sa.String(64), nullable=False),
        sa.Column("created_by", sa.BigInteger, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(),
                  nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("venue IN ('exness_mt5')", name="ck_broker_bridges_venue_valid"),
        sa.CheckConstraint("length(token_hash) = 64", name="ck_broker_bridges_hash_len"),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"], ondelete="RESTRICT",
                                name="fk_broker_bridges_created_by_users"),
        sa.UniqueConstraint("token_hash", name="uq_broker_bridges_token_hash"),
        sa.PrimaryKeyConstraint("id", name="pk_broker_bridges"),
    )
    op.execute("ALTER TABLE broker_bridges ENABLE ROW LEVEL SECURITY")
    op.execute("""
    DO $$
    BEGIN
      IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'anon') THEN
        EXECUTE 'REVOKE ALL ON broker_bridges FROM anon';
      END IF;
      IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'authenticated') THEN
        EXECUTE 'REVOKE ALL ON broker_bridges FROM authenticated';
      END IF;
      IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'nexus_app') THEN
        EXECUTE 'GRANT SELECT, INSERT, UPDATE ON broker_bridges TO nexus_app';
      END IF;
    END $$;
    """)


def downgrade() -> None:
    op.drop_table("broker_bridges")
