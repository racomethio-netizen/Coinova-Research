"""accept 1m, 3m, 30m, 2h, 6h and 12h in every timeframe CHECK constraint

Revision ID: 0011_more_timeframes
Revises: 0010_app_role_rls
Create Date: 2026-11-22
"""
from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0011_more_timeframes"
down_revision: str | None = "0010_app_role_rls"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLES = ("market_candles", "indicators", "ai_predictions", "trading_signals")
OLD = "timeframe IN ('5m', '15m', '1h', '4h', '1d')"
NEW = ("timeframe IN ('1m', '3m', '5m', '15m', '30m', '1h', '2h', '4h', '6h', '12h', "
       "'1d')")
ADDED = ("1m", "3m", "30m", "2h", "6h", "12h")


# Raw SQL: Alembic's naming convention would prefix these names a second time.
def _replace(table: str, expr: str) -> None:
    op.execute(f"ALTER TABLE {table} DROP CONSTRAINT ck_{table}_timeframe_valid")
    op.execute(f"ALTER TABLE {table} ADD CONSTRAINT ck_{table}_timeframe_valid CHECK ({expr})")


def upgrade() -> None:
    for t in TABLES:
        _replace(t, NEW)


def downgrade() -> None:
    added = ", ".join(f"'{a}'" for a in ADDED)
    for t in TABLES:
        op.execute(f"DELETE FROM {t} WHERE timeframe IN ({added})")  # noqa: S608 (constants)
        _replace(t, OLD)
