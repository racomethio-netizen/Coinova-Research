"""seed the NEXUS bot strategy row (EMA/RSI/MACD 5m, owner request 2026-11-23)

The bot runner records its signals against this row. It starts disabled for the main
trader; the bot runs in monitor or paper mode, and live mode needs the owner's separate
activation.

Revision ID: 0018_bot_strategy
Revises: 0017_forward_signal_events
Create Date: 2026-11-23
"""
from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0018_bot_strategy"
down_revision: str | None = "0017_forward_signal_events"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

OLD = ("code IN ('trend_following', 'momentum', 'breakout', 'mean_reversion', "
       "'pullback_support', 'liquidity_sweep_reversal', 'vwap_reclaim', "
       "'volatility_expansion', 'mtf_confluence', 'trend_supertrend', 'breakout_retest', "
       "'momentum_rv', 'mean_reversion_vwap', 'vwap_trend', 'sweep_reclaim', "
       "'market_structure', 'trend_pullback', 'breakout_retest_v2')")
NEW = OLD[:-1] + ", 'bot_ema_rsi_macd')"


def _check(expr: str) -> None:
    op.execute("ALTER TABLE strategies DROP CONSTRAINT ck_strategies_code_valid")
    op.execute(f"ALTER TABLE strategies ADD CONSTRAINT ck_strategies_code_valid CHECK ({expr})")


def upgrade() -> None:
    _check(NEW)
    op.execute("""
        INSERT INTO strategies (id, code, name, is_enabled, parameters) VALUES
            (19, 'bot_ema_rsi_macd', 'NEXUS bot: EMA/RSI/MACD 5m', false,
             '{"bot": true}'::jsonb)
        ON CONFLICT (code) DO NOTHING
    """)


def downgrade() -> None:
    op.execute("DELETE FROM strategies WHERE code = 'bot_ema_rsi_macd'")
    _check(OLD)
