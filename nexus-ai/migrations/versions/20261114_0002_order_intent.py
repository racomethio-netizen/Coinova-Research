"""orders: purpose, position link, intent details, one-active-order guard

Revision ID: 0002_order_intent
Revises: 0001_initial
Create Date: 2026-11-14
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0002_order_intent"
down_revision: str | None = "0001_initial"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

ACTIVE = "status IN ('pending_submit','submitted','open','partially_filled','unknown')"


def upgrade() -> None:
    op.add_column("orders", sa.Column("purpose", sa.String(length=8), server_default="entry",
                                      nullable=False))
    op.add_column("orders", sa.Column("position_id", sa.BigInteger(), nullable=True))
    op.add_column("orders", sa.Column("details", postgresql.JSONB(astext_type=sa.Text()),
                                      server_default=sa.text("'{}'::jsonb"), nullable=False))
    op.create_foreign_key(op.f("fk_orders_position_id_positions"), "orders", "positions",
                          ["position_id"], ["id"], ondelete="RESTRICT")
    op.create_check_constraint(op.f("ck_orders_purpose_valid"), "orders",
                               "purpose IN ('entry', 'exit')")
    op.create_index("uq_orders_one_active", "orders", ["portfolio_id", "pair_id", "side"],
                    unique=True, postgresql_where=sa.text(ACTIVE))


def downgrade() -> None:
    op.drop_index("uq_orders_one_active", table_name="orders", postgresql_where=sa.text(ACTIVE))
    op.drop_constraint(op.f("ck_orders_purpose_valid"), "orders", type_="check")
    op.drop_constraint(op.f("fk_orders_position_id_positions"), "orders", type_="foreignkey")
    op.drop_column("orders", "details")
    op.drop_column("orders", "position_id")
    op.drop_column("orders", "purpose")
