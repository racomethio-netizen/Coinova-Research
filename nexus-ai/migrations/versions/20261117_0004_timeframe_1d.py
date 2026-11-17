"""accept the 1-day timeframe in every timeframe CHECK constraint

Revision ID: 0004_timeframe_1d
Revises: 0003_app_role_grants
Create Date: 2026-11-17
"""
from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0004_timeframe_1d"
down_revision: str | None = "0003_app_role_grants"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLES = ("market_candles", "indicators", "ai_predictions", "trading_signals")
OLD = "timeframe IN ('5m', '15m', '1h', '4h')"
NEW = "timeframe IN ('5m', '15m', '1h', '4h', '1d')"


# Raw SQL: Alembic's naming convention would prefix these names a second time.
def _replace(table: str, expr_from: str, expr_to: str) -> None:
    op.execute(f"ALTER TABLE {table} DROP CONSTRAINT ck_{table}_timeframe_valid")
    op.execute(f"ALTER TABLE {table} ADD CONSTRAINT ck_{table}_timeframe_valid CHECK ({expr_to})")


def upgrade() -> None:
    for t in TABLES:
        _replace(t, OLD, NEW)


def downgrade() -> None:
    for t in TABLES:
        op.execute(f"DELETE FROM {t} WHERE timeframe = '1d'")  # noqa: S608 (constant names)
        _replace(t, NEW, OLD)
