"""seed strategy modules A-G of the deep-upgrade brief (disabled)

trend_supertrend, breakout_retest, momentum_rv, mean_reversion_vwap, vwap_trend,
sweep_reclaim and market_structure (pre-registered 2026-11-23). They start disabled; only
validation can enable a strategy.

Revision ID: 0012_strategies_a_g
Revises: 0011_more_timeframes
Create Date: 2026-11-23
"""
from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0012_strategies_a_g"
down_revision: str | None = "0011_more_timeframes"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

OLD = ("code IN ('trend_following', 'momentum', 'breakout', 'mean_reversion', "
       "'pullback_support', 'liquidity_sweep_reversal', 'vwap_reclaim', "
       "'volatility_expansion', 'mtf_confluence')")
NEW = ("code IN ('trend_following', 'momentum', 'breakout', 'mean_reversion', "
       "'pullback_support', 'liquidity_sweep_reversal', 'vwap_reclaim', "
       "'volatility_expansion', 'mtf_confluence', 'trend_supertrend', 'breakout_retest', "
       "'momentum_rv', 'mean_reversion_vwap', 'vwap_trend', 'sweep_reclaim', "
       "'market_structure')")


def _check(expr: str) -> None:
    op.execute("ALTER TABLE strategies DROP CONSTRAINT ck_strategies_code_valid")
    op.execute(f"ALTER TABLE strategies ADD CONSTRAINT ck_strategies_code_valid CHECK ({expr})")


def upgrade() -> None:
    _check(NEW)
    op.execute("""
        INSERT INTO strategies (id, code, name, is_enabled) VALUES
            (10, 'trend_supertrend',    'A. Trend following (Supertrend)',     false),
            (11, 'breakout_retest',     'B. Breakout with retest',             false),
            (12, 'momentum_rv',         'C. Momentum with relative volume',    false),
            (13, 'mean_reversion_vwap', 'D. Mean reversion to VWAP',           false),
            (14, 'vwap_trend',          'E. VWAP trend continuation',          false),
            (15, 'sweep_reclaim',       'F. Liquidity sweep reversal',         false),
            (16, 'market_structure',    'G. Market structure (BOS retest)',    false)
        ON CONFLICT (code) DO NOTHING
    """)


def downgrade() -> None:
    op.execute("DELETE FROM strategies WHERE code IN ('trend_supertrend', 'breakout_retest', "
               "'momentum_rv', 'mean_reversion_vwap', 'vwap_trend', 'sweep_reclaim', "
               "'market_structure')")
    _check(OLD)
