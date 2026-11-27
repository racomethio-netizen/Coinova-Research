"""strategy modules: registry, activation and forward observation records

The six strategy modules of the unified brief (Phase 3). ``strategy_modules`` holds each
module's settings, its test result and the owner's activation switch (OFF / OBSERVE /
PAPER); PAPER is refused by a constraint unless the module passed its test. Nothing in
these tables places an order. ``strategy_module_signals`` is the forward record: each
signal a module produced on new data while OBSERVE was on, and what it would have done
(no duplicates per module, symbol and signal bar). Row-level security is enabled with no
policies, and the Supabase API roles get no access, as for every other table.

Revision ID: 0024_strategy_modules
Revises: 0023_history_chunks
Create Date: 2026-11-27
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0024_strategy_modules"
down_revision: str | None = "0023_history_chunks"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

MONEY = sa.Numeric(38, 18)
RATIO = sa.Numeric(20, 10)


def upgrade() -> None:
    op.create_table(
        "strategy_modules",
        sa.Column("code", sa.String(40), nullable=False),
        sa.Column("name", sa.String(80), nullable=False),
        sa.Column("market", sa.String(16), nullable=False),
        sa.Column("style", sa.String(16), nullable=False),
        sa.Column("evidence", sa.String(16), nullable=False),
        sa.Column("activation", sa.String(16), server_default="OFF", nullable=False),
        sa.Column("config", postgresql.JSONB(astext_type=sa.Text()),
                  server_default=sa.text("'{}'::jsonb"), nullable=False),
        sa.Column("validation", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("activated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_by", sa.String(64), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(),
                  nullable=False),
        sa.CheckConstraint("market IN ('forex', 'crypto')",
                           name="ck_strategy_modules_market_valid"),
        sa.CheckConstraint("style IN ('scalping', 'intraday', 'swing')",
                           name="ck_strategy_modules_style_valid"),
        sa.CheckConstraint("evidence IN ('new', 'earlier_fail', 'earlier_pass')",
                           name="ck_strategy_modules_evidence_valid"),
        sa.CheckConstraint("activation IN ('OFF', 'OBSERVE', 'PAPER')",
                           name="ck_strategy_modules_activation_valid"),
        sa.CheckConstraint("activation <> 'PAPER' OR "
                           "coalesce((validation ->> 'passed')::boolean, false)",
                           name="ck_strategy_modules_paper_needs_pass"),
        sa.PrimaryKeyConstraint("code", name="pk_strategy_modules"),
    )
    op.create_table(
        "strategy_module_signals",
        sa.Column("id", sa.BigInteger, sa.Identity(always=True), nullable=False),
        sa.Column("module_code", sa.String(40), nullable=False),
        sa.Column("symbol", sa.String(32), nullable=False),
        sa.Column("signal_time", sa.DateTime(timezone=True), nullable=False),
        sa.Column("side", sa.SmallInteger, nullable=False),
        sa.Column("stop", MONEY, nullable=False),
        sa.Column("rr", RATIO, nullable=False),
        sa.Column("max_bars", sa.Integer, nullable=False),
        sa.Column("exit_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("spread", MONEY, nullable=True),
        sa.Column("status", sa.String(12), server_default="open", nullable=False),
        sa.Column("entry_time", sa.DateTime(timezone=True), nullable=True),
        sa.Column("entry", MONEY, nullable=True),
        sa.Column("target", MONEY, nullable=True),
        sa.Column("risk", MONEY, nullable=True),
        sa.Column("exit_time", sa.DateTime(timezone=True), nullable=True),
        sa.Column("exit_price", MONEY, nullable=True),
        sa.Column("reason", sa.String(16), nullable=True),
        sa.Column("net_r", RATIO, nullable=True),
        sa.Column("note", sa.Text, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(),
                  nullable=False),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("side IN (-1, 1)", name="ck_strategy_module_signals_side_valid"),
        sa.CheckConstraint("status IN ('open', 'closed', 'dropped')",
                           name="ck_strategy_module_signals_status_valid"),
        sa.ForeignKeyConstraint(["module_code"], ["strategy_modules.code"], ondelete="CASCADE",
                                name="fk_strategy_module_signals_module_code_strategy_modules"),
        sa.UniqueConstraint("module_code", "symbol", "signal_time",
                            name="uq_strategy_module_signals_module_code_symbol_signal_time"),
        sa.PrimaryKeyConstraint("id", name="pk_strategy_module_signals"),
    )
    op.create_index("ix_strategy_module_signals_status", "strategy_module_signals",
                    ["status", "module_code"])
    for table in ("strategy_modules", "strategy_module_signals"):
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
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
            EXECUTE 'GRANT SELECT, INSERT, UPDATE ON {table} TO nexus_app';
          END IF;
        END $$;
        """)


def downgrade() -> None:
    op.drop_table("strategy_module_signals")
    op.drop_table("strategy_modules")
