"""adaptive grid system: universe scans and the append-only grid event ledger (PAPER ONLY)

Row-level security is enabled with no policies and the Supabase API roles (anon,
authenticated) get no access, matching every other table. The application role may only
INSERT and SELECT, and triggers block UPDATE/DELETE so results cannot be edited.

Revision ID: 0020_adaptive_grid
Revises: 0019_grid_paper
Create Date: 2026-11-26
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0020_adaptive_grid"
down_revision: str | None = "0019_grid_paper"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

KINDS = ("decision", "regime", "grid_start", "grid_refused", "arm", "fill", "funding",
         "suspend", "resume", "stop", "retire", "risk", "control", "gap", "snapshot")


def _protect(table: str) -> None:
    # ``table`` is one of the two constant names below, never user input.
    op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
    op.execute(f"""
    CREATE OR REPLACE FUNCTION {table}_append_only() RETURNS trigger
    LANGUAGE plpgsql SET search_path = '' AS $$
    BEGIN
      RAISE EXCEPTION '{table} is append-only';
    END $$;
    """)
    op.execute(f"""
    CREATE TRIGGER {table}_no_change BEFORE UPDATE OR DELETE ON {table}
    FOR EACH ROW EXECUTE FUNCTION {table}_append_only();
    """)
    op.execute(f"""
    DO $$
    BEGIN
      IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'anon') THEN
        EXECUTE 'REVOKE ALL ON {table} FROM anon';
      END IF;
      IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'authenticated') THEN
        EXECUTE 'REVOKE ALL ON {table} FROM authenticated';
      END IF;
      IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'nexus_app') THEN
        EXECUTE 'GRANT SELECT, INSERT ON {table} TO nexus_app';
      END IF;
    END $$;
    """)  # noqa: S608 - ``table`` is one of two constant names, never user input


def upgrade() -> None:
    op.create_table(
        "grid_universe_scans",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), nullable=False),
        sa.Column("market", sa.String(length=8), nullable=False),
        sa.Column("at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("data", postgresql.JSONB(astext_type=sa.Text()),
                  server_default=sa.text("'{}'::jsonb"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"),
                  nullable=False),
        sa.CheckConstraint("market IN ('spot', 'futures')",
                           name=op.f("ck_grid_universe_scans_market_valid")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_grid_universe_scans")),
    )
    op.create_index("ix_grid_universe_scans_market_at", "grid_universe_scans",
                    ["market", "at"], unique=False)
    op.create_table(
        "adaptive_grid_events",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), nullable=False),
        sa.Column("seq", sa.BigInteger(), nullable=False),
        sa.Column("at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("account", sa.String(length=8), nullable=False),
        sa.Column("grid_id", sa.String(length=64), nullable=True),
        sa.Column("symbol", sa.String(length=24), nullable=True),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("data", postgresql.JSONB(astext_type=sa.Text()),
                  server_default=sa.text("'{}'::jsonb"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"),
                  nullable=False),
        sa.CheckConstraint("kind IN (" + ", ".join(f"'{k}'" for k in KINDS) + ")",
                           name=op.f("ck_adaptive_grid_events_kind_valid")),
        sa.CheckConstraint("account IN ('spot', 'futures', 'system')",
                           name=op.f("ck_adaptive_grid_events_account_valid")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_adaptive_grid_events")),
        sa.UniqueConstraint("seq", name="agrid_one_event_per_seq"),
    )
    op.create_index("ix_adaptive_grid_events_kind_id", "adaptive_grid_events",
                    ["kind", "id"], unique=False)
    op.create_index("ix_adaptive_grid_events_grid", "adaptive_grid_events", ["grid_id"],
                    unique=False)
    _protect("grid_universe_scans")
    _protect("adaptive_grid_events")


def downgrade() -> None:
    for t in ("adaptive_grid_events", "grid_universe_scans"):
        op.execute(f"DROP TRIGGER IF EXISTS {t}_no_change ON {t}")
        op.execute(f"DROP FUNCTION IF EXISTS {t}_append_only()")
    op.drop_index("ix_adaptive_grid_events_grid", table_name="adaptive_grid_events")
    op.drop_index("ix_adaptive_grid_events_kind_id", table_name="adaptive_grid_events")
    op.drop_table("adaptive_grid_events")
    op.drop_index("ix_grid_universe_scans_market_at", table_name="grid_universe_scans")
    op.drop_table("grid_universe_scans")
