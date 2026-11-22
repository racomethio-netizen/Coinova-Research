"""seed the Phase 6 candle strategies (disabled)

vwap_reclaim, volatility_expansion and mtf_confluence (pre-registered 2026-11-22). They
start disabled; only validation can enable a strategy.

Revision ID: 0007_phase6_strategies
Revises: 0006_demo_environment
Create Date: 2026-11-22
"""
from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0007_phase6_strategies"
down_revision: str | None = "0006_demo_environment"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

OLD = ("code IN ('trend_following', 'momentum', 'breakout', 'mean_reversion', "
       "'pullback_support', 'liquidity_sweep_reversal')")
NEW = ("code IN ('trend_following', 'momentum', 'breakout', 'mean_reversion', "
       "'pullback_support', 'liquidity_sweep_reversal', 'vwap_reclaim', "
       "'volatility_expansion', 'mtf_confluence')")


def _check(expr: str) -> None:
    op.execute("ALTER TABLE strategies DROP CONSTRAINT ck_strategies_code_valid")
    op.execute(f"ALTER TABLE strategies ADD CONSTRAINT ck_strategies_code_valid CHECK ({expr})")


def upgrade() -> None:
    _check(NEW)
    op.execute("""
        INSERT INTO strategies (id, code, name, is_enabled) VALUES
            (7, 'vwap_reclaim',         'VWAP reclaim',          false),
            (8, 'volatility_expansion', 'Volatility expansion',  false),
            (9, 'mtf_confluence',       'Multi-timeframe confluence', false)
        ON CONFLICT (code) DO NOTHING
    """)


def downgrade() -> None:
    op.execute("DELETE FROM strategies WHERE code IN ('vwap_reclaim', "
               "'volatility_expansion', 'mtf_confluence')")
    _check(OLD)
