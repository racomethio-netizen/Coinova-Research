"""seed the pullback and liquidity-sweep strategies (disabled)

Both strategies exist in code but had no row, so they could never be validated into
the strategies table or enabled. They start disabled like every strategy.

Revision ID: 0005_seed_new_strategies
Revises: 0004_timeframe_1d
Create Date: 2026-11-20
"""
from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0005_seed_new_strategies"
down_revision: str | None = "0004_timeframe_1d"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


OLD = "code IN ('trend_following', 'momentum', 'breakout', 'mean_reversion')"
NEW = ("code IN ('trend_following', 'momentum', 'breakout', 'mean_reversion', "
       "'pullback_support', 'liquidity_sweep_reversal')")


# Raw SQL: Alembic's naming convention would prefix the constraint name a second time.
def _check(expr: str) -> None:
    op.execute("ALTER TABLE strategies DROP CONSTRAINT ck_strategies_code_valid")
    op.execute(f"ALTER TABLE strategies ADD CONSTRAINT ck_strategies_code_valid CHECK ({expr})")


def upgrade() -> None:
    _check(NEW)
    op.execute("""
        INSERT INTO strategies (id, code, name, is_enabled) VALUES
            (5, 'pullback_support',         'Pullback to support',      false),
            (6, 'liquidity_sweep_reversal', 'Liquidity sweep reversal', false)
        ON CONFLICT (code) DO NOTHING
    """)


def downgrade() -> None:
    op.execute("DELETE FROM strategies WHERE code IN ('pullback_support', "
               "'liquidity_sweep_reversal')")
    _check(OLD)
