"""seed the spot strategy engine's strategies (disabled, paper validation only)

trend_pullback and breakout_retest_v2 (docs/research/2026-11-23-spot-engine-preregistration.md).
They start disabled. The main trader never runs them; only the separate paper runner
records their signals and paper trades. Live use would need a passed validation and the
owner's activation.

Revision ID: 0016_spot_engine_strategies
Revises: 0015_forward_day_events
Create Date: 2026-11-23
"""
from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0016_spot_engine_strategies"
down_revision: str | None = "0015_forward_day_events"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

OLD = ("code IN ('trend_following', 'momentum', 'breakout', 'mean_reversion', "
       "'pullback_support', 'liquidity_sweep_reversal', 'vwap_reclaim', "
       "'volatility_expansion', 'mtf_confluence', 'trend_supertrend', 'breakout_retest', "
       "'momentum_rv', 'mean_reversion_vwap', 'vwap_trend', 'sweep_reclaim', "
       "'market_structure')")
NEW = OLD[:-1] + ", 'trend_pullback', 'breakout_retest_v2')"


def _check(expr: str) -> None:
    op.execute("ALTER TABLE strategies DROP CONSTRAINT ck_strategies_code_valid")
    op.execute(f"ALTER TABLE strategies ADD CONSTRAINT ck_strategies_code_valid CHECK ({expr})")


def upgrade() -> None:
    _check(NEW)
    op.execute("""
        INSERT INTO strategies (id, code, name, is_enabled, parameters) VALUES
            (17, 'trend_pullback',     'Spot engine: Trend Pullback',     false,
             '{"spot_engine": true}'::jsonb),
            (18, 'breakout_retest_v2', 'Spot engine: Breakout + Retest', false,
             '{"spot_engine": true}'::jsonb)
        ON CONFLICT (code) DO NOTHING
    """)


def downgrade() -> None:
    op.execute("DELETE FROM strategies WHERE code IN ('trend_pullback', 'breakout_retest_v2')")
    _check(OLD)
