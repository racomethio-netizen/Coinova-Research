"""stop-limit orders: order_type 'stop_limit' and a stop_price column

Revision ID: 0013_stop_limit_orders
Revises: 0012_strategies_a_g
Create Date: 2026-11-23
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0013_stop_limit_orders"
down_revision: str | None = "0012_strategies_a_g"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


# Raw SQL for constraints: Alembic's naming convention would prefix the names twice.
def upgrade() -> None:
    op.alter_column("orders", "order_type", type_=sa.String(16),
                    existing_type=sa.String(8), existing_nullable=False)
    op.add_column("orders", sa.Column("stop_price", sa.Numeric(precision=38, scale=18), nullable=True))
    op.execute("ALTER TABLE orders DROP CONSTRAINT ck_orders_type_valid")
    op.execute("ALTER TABLE orders ADD CONSTRAINT ck_orders_type_valid "
               "CHECK (order_type IN ('limit', 'market', 'stop_limit'))")
    op.execute("ALTER TABLE orders ADD CONSTRAINT ck_orders_stop_limit_complete "
               "CHECK ((order_type = 'stop_limit') = (stop_price IS NOT NULL) "
               "AND (order_type <> 'stop_limit' OR price IS NOT NULL))")
    op.execute("ALTER TABLE orders ADD CONSTRAINT ck_orders_stop_price_positive "
               "CHECK (stop_price IS NULL OR stop_price > 0)")


def downgrade() -> None:
    op.execute("ALTER TABLE orders DROP CONSTRAINT ck_orders_stop_price_positive")
    op.execute("ALTER TABLE orders DROP CONSTRAINT ck_orders_stop_limit_complete")
    op.execute("ALTER TABLE orders DROP CONSTRAINT ck_orders_type_valid")
    op.execute("ALTER TABLE orders ADD CONSTRAINT ck_orders_type_valid "
               "CHECK (order_type IN ('limit', 'market'))")
    op.drop_column("orders", "stop_price")
    op.alter_column("orders", "order_type", type_=sa.String(8),
                    existing_type=sa.String(16), existing_nullable=False)
