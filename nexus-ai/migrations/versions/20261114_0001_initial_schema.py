"""initial schema

Revision ID: 0001_initial
Revises: (none)
Create Date: 2026-11-14 06:22:22.790325+00:00
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = '0001_initial'
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

MODE_TABLES = (
    "trading_signals", "portfolios", "balances", "orders", "trades", "positions",
    "performance_snapshots", "risk_events",
)


def upgrade() -> None:
    op.create_table('ai_models',
    sa.Column('id', sa.BigInteger(), sa.Identity(always=True), nullable=False),
    sa.Column('name', sa.String(length=64), nullable=False),
    sa.Column('version', sa.String(length=32), nullable=False),
    sa.Column('algorithm', sa.String(length=32), nullable=False),
    sa.Column('status', sa.String(length=16), server_default='training', nullable=False),
    sa.Column('feature_set', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('hyperparameters', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('training_start', sa.DateTime(timezone=True), nullable=True),
    sa.Column('training_end', sa.DateTime(timezone=True), nullable=True),
    sa.Column('artifact_uri', sa.Text(), nullable=True),
    sa.Column('artifact_sha256', sa.String(length=64), nullable=True),
    sa.Column('code_version', sa.String(length=64), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('promoted_at', sa.DateTime(timezone=True), nullable=True),
    sa.CheckConstraint("status IN ('training', 'candidate', 'validated', 'rejected', 'active', 'retired')", name=op.f('ck_ai_models_status_valid')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_ai_models')),
    sa.UniqueConstraint('name', 'version', name=op.f('uq_ai_models_name_version'))
    )
    op.create_index('uq_ai_models_one_active', 'ai_models', ['name'], unique=True, postgresql_where=sa.text("status = 'active'"))
    op.create_table('exchanges',
    sa.Column('id', sa.SmallInteger(), nullable=False),
    sa.Column('code', sa.String(length=32), nullable=False),
    sa.Column('name', sa.String(length=64), nullable=False),
    sa.Column('is_enabled', sa.Boolean(), server_default=sa.text('false'), nullable=False),
    sa.Column('capabilities', postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'{}'::jsonb"), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_exchanges')),
    sa.UniqueConstraint('code', name=op.f('uq_exchanges_code'))
    )
    op.create_table('strategies',
    sa.Column('id', sa.SmallInteger(), nullable=False),
    sa.Column('code', sa.String(length=32), nullable=False),
    sa.Column('name', sa.String(length=64), nullable=False),
    sa.Column('version', sa.String(length=16), server_default='0.1.0', nullable=False),
    sa.Column('parameters', postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'{}'::jsonb"), nullable=False),
    sa.Column('is_enabled', sa.Boolean(), server_default=sa.text('false'), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint("code IN ('trend_following', 'momentum', 'breakout', 'mean_reversion')", name=op.f('ck_strategies_code_valid')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_strategies')),
    sa.UniqueConstraint('code', name=op.f('uq_strategies_code'))
    )
    op.create_table('system_events',
    sa.Column('id', sa.BigInteger(), sa.Identity(always=True), nullable=False),
    sa.Column('component', sa.String(length=48), nullable=False),
    sa.Column('event_type', sa.String(length=48), nullable=False),
    sa.Column('severity', sa.String(length=8), nullable=False),
    sa.Column('message', sa.Text(), nullable=False),
    sa.Column('details', postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'{}'::jsonb"), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint("severity IN ('debug', 'info', 'warning', 'error', 'critical')", name=op.f('ck_system_events_severity_valid')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_system_events'))
    )
    op.create_index('ix_system_events_component_created', 'system_events', ['component', 'created_at'], unique=False)
    op.create_table('users',
    sa.Column('id', sa.BigInteger(), sa.Identity(always=True), nullable=False),
    sa.Column('email', sa.String(length=320), nullable=False),
    sa.Column('password_hash', sa.Text(), nullable=False),
    sa.Column('role', sa.String(length=16), server_default='viewer', nullable=False),
    sa.Column('totp_secret_encrypted', sa.LargeBinary(), nullable=True),
    sa.Column('is_active', sa.Boolean(), server_default=sa.text('true'), nullable=False),
    sa.Column('failed_login_count', sa.Integer(), server_default='0', nullable=False),
    sa.Column('locked_until', sa.DateTime(timezone=True), nullable=True),
    sa.Column('last_login_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint("role IN ('owner', 'viewer')", name=op.f('ck_users_role_valid')),
    sa.CheckConstraint('email = lower(email)', name=op.f('ck_users_email_lowercase')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_users')),
    sa.UniqueConstraint('email', name=op.f('uq_users_email'))
    )
    op.create_table('audit_logs',
    sa.Column('id', sa.BigInteger(), sa.Identity(always=True), nullable=False),
    sa.Column('user_id', sa.BigInteger(), nullable=True),
    sa.Column('actor', sa.String(length=64), nullable=False),
    sa.Column('action', sa.String(length=64), nullable=False),
    sa.Column('target_type', sa.String(length=48), nullable=True),
    sa.Column('target_id', sa.String(length=64), nullable=True),
    sa.Column('details', postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'{}'::jsonb"), nullable=False),
    sa.Column('ip_address', postgresql.INET(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], name=op.f('fk_audit_logs_user_id_users'), ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_audit_logs'))
    )
    op.create_index('ix_audit_logs_created', 'audit_logs', ['created_at'], unique=False)
    op.create_table('backtest_runs',
    sa.Column('id', sa.BigInteger(), sa.Identity(always=True), nullable=False),
    sa.Column('strategy_id', sa.SmallInteger(), nullable=True),
    sa.Column('model_id', sa.BigInteger(), nullable=True),
    sa.Column('status', sa.String(length=16), server_default='queued', nullable=False),
    sa.Column('is_walk_forward', sa.Boolean(), server_default=sa.text('false'), nullable=False),
    sa.Column('config', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('data_start', sa.DateTime(timezone=True), nullable=False),
    sa.Column('data_end', sa.DateTime(timezone=True), nullable=False),
    sa.Column('fee_bps', sa.Numeric(precision=20, scale=10), nullable=False),
    sa.Column('slippage_bps', sa.Numeric(precision=20, scale=10), nullable=False),
    sa.Column('metrics', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    sa.Column('error', sa.Text(), nullable=True),
    sa.Column('code_version', sa.String(length=64), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('completed_at', sa.DateTime(timezone=True), nullable=True),
    sa.CheckConstraint("status IN ('queued', 'running', 'succeeded', 'failed')", name=op.f('ck_backtest_runs_status_valid')),
    sa.CheckConstraint('data_end > data_start', name=op.f('ck_backtest_runs_period_ordered')),
    sa.CheckConstraint('fee_bps >= 0 AND slippage_bps >= 0', name=op.f('ck_backtest_runs_costs_non_negative')),
    sa.ForeignKeyConstraint(['model_id'], ['ai_models.id'], name=op.f('fk_backtest_runs_model_id_ai_models'), ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['strategy_id'], ['strategies.id'], name=op.f('fk_backtest_runs_strategy_id_strategies'), ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_backtest_runs'))
    )
    op.create_table('exchange_credentials',
    sa.Column('id', sa.BigInteger(), sa.Identity(always=True), nullable=False),
    sa.Column('user_id', sa.BigInteger(), nullable=False),
    sa.Column('exchange_id', sa.SmallInteger(), nullable=False),
    sa.Column('label', sa.String(length=64), nullable=False),
    sa.Column('environment', sa.String(length=16), server_default='production', nullable=False),
    sa.Column('api_key_encrypted', sa.LargeBinary(), nullable=False),
    sa.Column('api_secret_encrypted', sa.LargeBinary(), nullable=False),
    sa.Column('passphrase_encrypted', sa.LargeBinary(), nullable=True),
    sa.Column('key_fingerprint', sa.String(length=16), nullable=False),
    sa.Column('encryption_key_id', sa.String(length=32), nullable=False),
    sa.Column('permissions', postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'{}'::jsonb"), nullable=False),
    sa.Column('withdrawal_disabled_verified', sa.Boolean(), server_default=sa.text('false'), nullable=False),
    sa.Column('last_verified_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('rotated_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('revoked_at', sa.DateTime(timezone=True), nullable=True),
    sa.CheckConstraint("environment IN ('production', 'sandbox')", name=op.f('ck_exchange_credentials_environment_valid')),
    sa.ForeignKeyConstraint(['exchange_id'], ['exchanges.id'], name=op.f('fk_exchange_credentials_exchange_id_exchanges'), ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], name=op.f('fk_exchange_credentials_user_id_users'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_exchange_credentials')),
    sa.UniqueConstraint('user_id', 'exchange_id', 'label', name=op.f('uq_exchange_credentials_user_id_exchange_id_label'))
    )
    op.create_index(op.f('ix_exchange_credentials_user_id'), 'exchange_credentials', ['user_id'], unique=False)
    op.create_index('uq_exchange_credentials_active', 'exchange_credentials', ['user_id', 'exchange_id', 'environment'], unique=True, postgresql_where=sa.text('revoked_at IS NULL'))
    op.create_table('portfolios',
    sa.Column('id', sa.BigInteger(), sa.Identity(always=True), nullable=False),
    sa.Column('user_id', sa.BigInteger(), nullable=False),
    sa.Column('name', sa.String(length=64), nullable=False),
    sa.Column('mode', sa.String(length=8), nullable=False),
    sa.Column('quote_currency', sa.String(length=16), server_default='USDT', nullable=False),
    sa.Column('initial_capital', sa.Numeric(precision=38, scale=18), nullable=True),
    sa.Column('is_active', sa.Boolean(), server_default=sa.text('true'), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint("mode IN ('paper', 'live')", name=op.f('ck_portfolios_mode_valid')),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], name=op.f('fk_portfolios_user_id_users'), ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_portfolios')),
    sa.UniqueConstraint('id', 'mode', name=op.f('uq_portfolios_id_mode')),
    sa.UniqueConstraint('user_id', 'name', 'mode', name=op.f('uq_portfolios_user_id_name_mode'))
    )
    op.create_table('trading_pairs',
    sa.Column('id', sa.BigInteger(), sa.Identity(always=True), nullable=False),
    sa.Column('exchange_id', sa.SmallInteger(), nullable=False),
    sa.Column('symbol', sa.String(length=32), nullable=False),
    sa.Column('exchange_symbol', sa.String(length=64), nullable=False),
    sa.Column('base_asset', sa.String(length=16), nullable=False),
    sa.Column('quote_asset', sa.String(length=16), nullable=False),
    sa.Column('price_tick', sa.Numeric(precision=38, scale=18), nullable=True),
    sa.Column('amount_step', sa.Numeric(precision=38, scale=18), nullable=True),
    sa.Column('min_amount', sa.Numeric(precision=38, scale=18), nullable=True),
    sa.Column('min_notional', sa.Numeric(precision=38, scale=18), nullable=True),
    sa.Column('maker_fee', sa.Numeric(precision=20, scale=10), nullable=True),
    sa.Column('taker_fee', sa.Numeric(precision=20, scale=10), nullable=True),
    sa.Column('is_active', sa.Boolean(), server_default=sa.text('true'), nullable=False),
    sa.Column('metadata_synced_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint('amount_step IS NULL OR amount_step > 0', name=op.f('ck_trading_pairs_amount_step_positive')),
    sa.CheckConstraint('price_tick IS NULL OR price_tick > 0', name=op.f('ck_trading_pairs_price_tick_positive')),
    sa.ForeignKeyConstraint(['exchange_id'], ['exchanges.id'], name=op.f('fk_trading_pairs_exchange_id_exchanges'), ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_trading_pairs')),
    sa.UniqueConstraint('exchange_id', 'symbol', name=op.f('uq_trading_pairs_exchange_id_symbol'))
    )
    op.create_table('ai_predictions',
    sa.Column('id', sa.BigInteger(), sa.Identity(always=True), nullable=False),
    sa.Column('model_id', sa.BigInteger(), nullable=False),
    sa.Column('pair_id', sa.BigInteger(), nullable=False),
    sa.Column('timeframe', sa.String(length=4), nullable=False),
    sa.Column('as_of', sa.DateTime(timezone=True), nullable=False),
    sa.Column('horizon_bars', sa.SmallInteger(), nullable=False),
    sa.Column('predicted_class', sa.String(length=16), nullable=False),
    sa.Column('probability', sa.Numeric(precision=20, scale=10), nullable=False),
    sa.Column('calibrated_probability', sa.Numeric(precision=20, scale=10), nullable=True),
    sa.Column('regime', sa.String(length=32), nullable=True),
    sa.Column('features_hash', sa.String(length=64), nullable=False),
    sa.Column('realized_outcome', sa.String(length=16), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint("timeframe IN ('5m', '15m', '1h', '4h')", name=op.f('ck_ai_predictions_timeframe_valid')),
    sa.CheckConstraint('calibrated_probability IS NULL OR (calibrated_probability >= 0 AND calibrated_probability <= 1)', name=op.f('ck_ai_predictions_calibrated_range')),
    sa.CheckConstraint('probability >= 0 AND probability <= 1', name=op.f('ck_ai_predictions_probability_range')),
    sa.ForeignKeyConstraint(['model_id'], ['ai_models.id'], name=op.f('fk_ai_predictions_model_id_ai_models'), ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['pair_id'], ['trading_pairs.id'], name=op.f('fk_ai_predictions_pair_id_trading_pairs'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_ai_predictions')),
    sa.UniqueConstraint('model_id', 'pair_id', 'timeframe', 'as_of', name=op.f('uq_ai_predictions_model_id_pair_id_timeframe_as_of'))
    )
    op.create_index('ix_ai_predictions_pair_asof', 'ai_predictions', ['pair_id', 'as_of'], unique=False)
    op.create_table('balances',
    sa.Column('id', sa.BigInteger(), sa.Identity(always=True), nullable=False),
    sa.Column('portfolio_id', sa.Integer(), nullable=False),
    sa.Column('mode', sa.String(length=8), nullable=False),
    sa.Column('exchange_id', sa.SmallInteger(), nullable=False),
    sa.Column('asset', sa.String(length=16), nullable=False),
    sa.Column('free', sa.Numeric(precision=38, scale=18), nullable=False),
    sa.Column('locked', sa.Numeric(precision=38, scale=18), nullable=False),
    sa.Column('source', sa.String(length=8), nullable=False),
    sa.Column('synced_at', sa.DateTime(timezone=True), nullable=False),
    sa.CheckConstraint("mode IN ('paper', 'live')", name=op.f('ck_balances_mode_valid')),
    sa.CheckConstraint("source IN ('internal', 'exchange')", name=op.f('ck_balances_source_valid')),
    sa.CheckConstraint('free >= 0 AND locked >= 0', name=op.f('ck_balances_non_negative')),
    sa.ForeignKeyConstraint(['exchange_id'], ['exchanges.id'], name=op.f('fk_balances_exchange_id_exchanges'), ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['portfolio_id', 'mode'], ['portfolios.id', 'portfolios.mode'], name=op.f('fk_balances_portfolio_id_mode_portfolios'), ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_balances')),
    sa.UniqueConstraint('portfolio_id', 'exchange_id', 'asset', 'source', name=op.f('uq_balances_portfolio_id_exchange_id_asset_source'))
    )
    op.create_table('indicators',
    sa.Column('pair_id', sa.BigInteger(), nullable=False),
    sa.Column('timeframe', sa.String(length=4), nullable=False),
    sa.Column('open_time', sa.DateTime(timezone=True), nullable=False),
    sa.Column('name', sa.String(length=64), nullable=False),
    sa.Column('value', sa.Numeric(precision=38, scale=18), nullable=True),
    sa.Column('computed_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint("timeframe IN ('5m', '15m', '1h', '4h')", name=op.f('ck_indicators_timeframe_valid')),
    sa.ForeignKeyConstraint(['pair_id'], ['trading_pairs.id'], name=op.f('fk_indicators_pair_id_trading_pairs'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('pair_id', 'timeframe', 'open_time', 'name', name=op.f('pk_indicators'))
    )
    op.create_table('market_candles',
    sa.Column('pair_id', sa.BigInteger(), nullable=False),
    sa.Column('timeframe', sa.String(length=4), nullable=False),
    sa.Column('open_time', sa.DateTime(timezone=True), nullable=False),
    sa.Column('open', sa.Numeric(precision=38, scale=18), nullable=False),
    sa.Column('high', sa.Numeric(precision=38, scale=18), nullable=False),
    sa.Column('low', sa.Numeric(precision=38, scale=18), nullable=False),
    sa.Column('close', sa.Numeric(precision=38, scale=18), nullable=False),
    sa.Column('volume', sa.Numeric(precision=38, scale=18), nullable=False),
    sa.Column('is_closed', sa.Boolean(), server_default=sa.text('false'), nullable=False),
    sa.Column('source', sa.String(length=16), server_default='rest', nullable=False),
    sa.Column('ingested_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint("timeframe IN ('5m', '15m', '1h', '4h')", name=op.f('ck_market_candles_timeframe_valid')),
    sa.CheckConstraint('high >= low AND high >= open AND high >= close AND low <= open AND low <= close', name=op.f('ck_market_candles_ohlc_consistent')),
    sa.CheckConstraint('volume >= 0', name=op.f('ck_market_candles_volume_non_negative')),
    sa.ForeignKeyConstraint(['pair_id'], ['trading_pairs.id'], name=op.f('fk_market_candles_pair_id_trading_pairs'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('pair_id', 'timeframe', 'open_time', name=op.f('pk_market_candles'))
    )
    op.create_table('model_evaluations',
    sa.Column('id', sa.BigInteger(), sa.Identity(always=True), nullable=False),
    sa.Column('model_id', sa.BigInteger(), nullable=False),
    sa.Column('backtest_run_id', sa.BigInteger(), nullable=True),
    sa.Column('evaluation_type', sa.String(length=16), nullable=False),
    sa.Column('period_start', sa.DateTime(timezone=True), nullable=False),
    sa.Column('period_end', sa.DateTime(timezone=True), nullable=False),
    sa.Column('metrics', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('passed', sa.Boolean(), nullable=False),
    sa.Column('failure_reasons', postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'[]'::jsonb"), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint("evaluation_type IN ('validation', 'out_of_sample', 'walk_forward', 'paper', 'live_drift')", name=op.f('ck_model_evaluations_type_valid')),
    sa.CheckConstraint('period_end > period_start', name=op.f('ck_model_evaluations_period_ordered')),
    sa.ForeignKeyConstraint(['backtest_run_id'], ['backtest_runs.id'], name=op.f('fk_model_evaluations_backtest_run_id_backtest_runs'), ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['model_id'], ['ai_models.id'], name=op.f('fk_model_evaluations_model_id_ai_models'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_model_evaluations'))
    )
    op.create_index('ix_model_evaluations_model_created', 'model_evaluations', ['model_id', 'created_at'], unique=False)
    op.create_table('orderbook_snapshots',
    sa.Column('id', sa.BigInteger(), sa.Identity(always=True), nullable=False),
    sa.Column('pair_id', sa.BigInteger(), nullable=False),
    sa.Column('captured_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('best_bid', sa.Numeric(precision=38, scale=18), nullable=True),
    sa.Column('best_ask', sa.Numeric(precision=38, scale=18), nullable=True),
    sa.Column('spread_bps', sa.Numeric(precision=20, scale=10), nullable=True),
    sa.Column('bid_depth_quote', sa.Numeric(precision=38, scale=18), nullable=True),
    sa.Column('ask_depth_quote', sa.Numeric(precision=38, scale=18), nullable=True),
    sa.Column('bids', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('asks', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.CheckConstraint('best_ask IS NULL OR best_bid IS NULL OR best_ask >= best_bid', name=op.f('ck_orderbook_snapshots_not_crossed')),
    sa.ForeignKeyConstraint(['pair_id'], ['trading_pairs.id'], name=op.f('fk_orderbook_snapshots_pair_id_trading_pairs'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_orderbook_snapshots'))
    )
    op.create_index('ix_orderbook_snapshots_pair_time', 'orderbook_snapshots', ['pair_id', 'captured_at'], unique=False)
    op.create_table('performance_snapshots',
    sa.Column('id', sa.BigInteger(), sa.Identity(always=True), nullable=False),
    sa.Column('portfolio_id', sa.Integer(), nullable=False),
    sa.Column('mode', sa.String(length=8), nullable=False),
    sa.Column('captured_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('equity', sa.Numeric(precision=38, scale=18), nullable=False),
    sa.Column('cash', sa.Numeric(precision=38, scale=18), nullable=False),
    sa.Column('exposure', sa.Numeric(precision=38, scale=18), nullable=False),
    sa.Column('realized_pnl', sa.Numeric(precision=38, scale=18), nullable=False),
    sa.Column('unrealized_pnl', sa.Numeric(precision=38, scale=18), nullable=False),
    sa.Column('fees_paid', sa.Numeric(precision=38, scale=18), nullable=False),
    sa.Column('peak_equity', sa.Numeric(precision=38, scale=18), nullable=False),
    sa.Column('drawdown', sa.Numeric(precision=20, scale=10), nullable=False),
    sa.Column('open_positions', sa.SmallInteger(), nullable=False),
    sa.CheckConstraint("mode IN ('paper', 'live')", name=op.f('ck_performance_snapshots_mode_valid')),
    sa.CheckConstraint('drawdown >= 0 AND drawdown <= 1', name=op.f('ck_performance_snapshots_drawdown_range')),
    sa.ForeignKeyConstraint(['portfolio_id', 'mode'], ['portfolios.id', 'portfolios.mode'], name=op.f('fk_performance_snapshots_portfolio_id_mode_portfolios'), ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_performance_snapshots')),
    sa.UniqueConstraint('portfolio_id', 'captured_at', name=op.f('uq_performance_snapshots_portfolio_id_captured_at'))
    )
    op.create_table('risk_events',
    sa.Column('id', sa.BigInteger(), sa.Identity(always=True), nullable=False),
    sa.Column('mode', sa.String(length=8), nullable=False),
    sa.Column('portfolio_id', sa.Integer(), nullable=True),
    sa.Column('event_type', sa.String(length=48), nullable=False),
    sa.Column('severity', sa.String(length=8), nullable=False),
    sa.Column('details', postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'{}'::jsonb"), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('resolved_at', sa.DateTime(timezone=True), nullable=True),
    sa.CheckConstraint("mode IN ('paper', 'live')", name=op.f('ck_risk_events_mode_valid')),
    sa.CheckConstraint("severity IN ('debug', 'info', 'warning', 'error', 'critical')", name=op.f('ck_risk_events_severity_valid')),
    sa.ForeignKeyConstraint(['portfolio_id', 'mode'], ['portfolios.id', 'portfolios.mode'], name=op.f('fk_risk_events_portfolio_id_mode_portfolios'), ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_risk_events'))
    )
    op.create_index('ix_risk_events_created', 'risk_events', ['created_at'], unique=False)
    op.create_table('trading_signals',
    sa.Column('id', sa.BigInteger(), sa.Identity(always=True), nullable=False),
    sa.Column('mode', sa.String(length=8), nullable=False),
    sa.Column('strategy_id', sa.SmallInteger(), nullable=False),
    sa.Column('pair_id', sa.BigInteger(), nullable=False),
    sa.Column('prediction_id', sa.BigInteger(), nullable=True),
    sa.Column('side', sa.String(length=4), nullable=False),
    sa.Column('timeframe', sa.String(length=4), nullable=False),
    sa.Column('bar_time', sa.DateTime(timezone=True), nullable=False),
    sa.Column('generated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('entry_price', sa.Numeric(precision=38, scale=18), nullable=False),
    sa.Column('stop_loss', sa.Numeric(precision=38, scale=18), nullable=True),
    sa.Column('take_profit', sa.Numeric(precision=38, scale=18), nullable=True),
    sa.Column('confidence', sa.Numeric(precision=20, scale=10), nullable=False),
    sa.Column('status', sa.String(length=16), server_default='new', nullable=False),
    sa.Column('rejection_reason', sa.Text(), nullable=True),
    sa.Column('details', postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'{}'::jsonb"), nullable=False),
    sa.Column('expires_at', sa.DateTime(timezone=True), nullable=True),
    sa.CheckConstraint("mode IN ('paper', 'live')", name=op.f('ck_trading_signals_mode_valid')),
    sa.CheckConstraint("side <> 'buy' OR stop_loss IS NULL OR stop_loss < entry_price", name=op.f('ck_trading_signals_buy_stop_below_entry')),
    sa.CheckConstraint("side <> 'buy' OR take_profit IS NULL OR take_profit > entry_price", name=op.f('ck_trading_signals_buy_target_above_entry')),
    sa.CheckConstraint("side IN ('buy', 'sell')", name=op.f('ck_trading_signals_side_valid')),
    sa.CheckConstraint("status IN ('new', 'approved', 'rejected', 'expired', 'executed')", name=op.f('ck_trading_signals_status_valid')),
    sa.CheckConstraint("timeframe IN ('5m', '15m', '1h', '4h')", name=op.f('ck_trading_signals_timeframe_valid')),
    sa.CheckConstraint('confidence >= 0 AND confidence <= 1', name=op.f('ck_trading_signals_confidence_range')),
    sa.ForeignKeyConstraint(['pair_id'], ['trading_pairs.id'], name=op.f('fk_trading_signals_pair_id_trading_pairs'), ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['prediction_id'], ['ai_predictions.id'], name=op.f('fk_trading_signals_prediction_id_ai_predictions'), ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['strategy_id'], ['strategies.id'], name=op.f('fk_trading_signals_strategy_id_strategies'), ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_trading_signals')),
    sa.UniqueConstraint('id', 'mode', name=op.f('uq_trading_signals_id_mode')),
    sa.UniqueConstraint('strategy_id', 'pair_id', 'timeframe', 'bar_time', 'mode', name=op.f('uq_trading_signals_strategy_id_pair_id_timeframe_bar_time_mode'))
    )
    op.create_index('ix_trading_signals_status_generated', 'trading_signals', ['status', 'generated_at'], unique=False)
    op.create_table('orders',
    sa.Column('id', sa.BigInteger(), sa.Identity(always=True), nullable=False),
    sa.Column('portfolio_id', sa.Integer(), nullable=False),
    sa.Column('mode', sa.String(length=8), nullable=False),
    sa.Column('exchange_id', sa.SmallInteger(), nullable=False),
    sa.Column('pair_id', sa.BigInteger(), nullable=False),
    sa.Column('signal_id', sa.Integer(), nullable=True),
    sa.Column('client_order_id', sa.String(length=36), nullable=False),
    sa.Column('exchange_order_id', sa.String(length=128), nullable=True),
    sa.Column('side', sa.String(length=4), nullable=False),
    sa.Column('order_type', sa.String(length=8), nullable=False),
    sa.Column('time_in_force', sa.String(length=8), nullable=True),
    sa.Column('price', sa.Numeric(precision=38, scale=18), nullable=True),
    sa.Column('amount', sa.Numeric(precision=38, scale=18), nullable=False),
    sa.Column('filled_amount', sa.Numeric(precision=38, scale=18), server_default='0', nullable=False),
    sa.Column('average_fill_price', sa.Numeric(precision=38, scale=18), nullable=True),
    sa.Column('fee_amount', sa.Numeric(precision=38, scale=18), server_default='0', nullable=False),
    sa.Column('fee_asset', sa.String(length=16), nullable=True),
    sa.Column('expected_price', sa.Numeric(precision=38, scale=18), nullable=True),
    sa.Column('status', sa.String(length=20), server_default='pending_submit', nullable=False),
    sa.Column('last_error', sa.Text(), nullable=True),
    sa.Column('submitted_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('last_checked_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('closed_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint("mode IN ('paper', 'live')", name=op.f('ck_orders_mode_valid')),
    sa.CheckConstraint("order_type <> 'limit' OR price IS NOT NULL", name=op.f('ck_orders_limit_has_price')),
    sa.CheckConstraint("order_type IN ('limit', 'market')", name=op.f('ck_orders_type_valid')),
    sa.CheckConstraint("side IN ('buy', 'sell')", name=op.f('ck_orders_side_valid')),
    sa.CheckConstraint("status IN ('pending_submit', 'submitted', 'open', 'partially_filled', 'filled', 'canceled', 'rejected', 'expired', 'unknown')", name=op.f('ck_orders_status_valid')),
    sa.CheckConstraint('amount > 0', name=op.f('ck_orders_amount_positive')),
    sa.CheckConstraint('fee_amount >= 0', name=op.f('ck_orders_fee_non_negative')),
    sa.CheckConstraint('filled_amount >= 0 AND filled_amount <= amount', name=op.f('ck_orders_fill_bounded')),
    sa.CheckConstraint('price IS NULL OR price > 0', name=op.f('ck_orders_price_positive')),
    sa.ForeignKeyConstraint(['exchange_id'], ['exchanges.id'], name=op.f('fk_orders_exchange_id_exchanges'), ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['pair_id'], ['trading_pairs.id'], name=op.f('fk_orders_pair_id_trading_pairs'), ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['portfolio_id', 'mode'], ['portfolios.id', 'portfolios.mode'], name=op.f('fk_orders_portfolio_id_mode_portfolios'), ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['signal_id', 'mode'], ['trading_signals.id', 'trading_signals.mode'], name=op.f('fk_orders_signal_id_mode_trading_signals'), ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_orders')),
    sa.UniqueConstraint('client_order_id', name=op.f('uq_orders_client_order_id')),
    sa.UniqueConstraint('exchange_id', 'exchange_order_id', name=op.f('uq_orders_exchange_id_exchange_order_id')),
    sa.UniqueConstraint('id', 'mode', name=op.f('uq_orders_id_mode'))
    )
    op.create_index('ix_orders_open', 'orders', ['status', 'exchange_id'], unique=False, postgresql_where=sa.text("status IN ('pending_submit','submitted','open','partially_filled','unknown')"))
    op.create_index('ix_orders_portfolio_created', 'orders', ['portfolio_id', 'created_at'], unique=False)
    op.create_table('positions',
    sa.Column('id', sa.BigInteger(), sa.Identity(always=True), nullable=False),
    sa.Column('portfolio_id', sa.Integer(), nullable=False),
    sa.Column('mode', sa.String(length=8), nullable=False),
    sa.Column('exchange_id', sa.SmallInteger(), nullable=False),
    sa.Column('pair_id', sa.BigInteger(), nullable=False),
    sa.Column('base_asset', sa.String(length=16), nullable=False),
    sa.Column('strategy_id', sa.SmallInteger(), nullable=True),
    sa.Column('entry_order_id', sa.Integer(), nullable=True),
    sa.Column('status', sa.String(length=8), server_default='open', nullable=False),
    sa.Column('quantity', sa.Numeric(precision=38, scale=18), nullable=False),
    sa.Column('avg_entry_price', sa.Numeric(precision=38, scale=18), nullable=False),
    sa.Column('cost_basis', sa.Numeric(precision=38, scale=18), nullable=False),
    sa.Column('stop_loss', sa.Numeric(precision=38, scale=18), nullable=True),
    sa.Column('take_profit', sa.Numeric(precision=38, scale=18), nullable=True),
    sa.Column('realized_pnl', sa.Numeric(precision=38, scale=18), server_default='0', nullable=False),
    sa.Column('fees_paid', sa.Numeric(precision=38, scale=18), server_default='0', nullable=False),
    sa.Column('exit_reason', sa.String(length=32), nullable=True),
    sa.Column('opened_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('closed_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint("mode IN ('paper', 'live')", name=op.f('ck_positions_mode_valid')),
    sa.CheckConstraint("status <> 'closed' OR closed_at IS NOT NULL", name=op.f('ck_positions_closed_has_time')),
    sa.CheckConstraint("status IN ('open', 'closing', 'closed')", name=op.f('ck_positions_status_valid')),
    sa.CheckConstraint('quantity >= 0', name=op.f('ck_positions_quantity_non_negative')),
    sa.ForeignKeyConstraint(['entry_order_id', 'mode'], ['orders.id', 'orders.mode'], name=op.f('fk_positions_entry_order_id_mode_orders'), ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['exchange_id'], ['exchanges.id'], name=op.f('fk_positions_exchange_id_exchanges'), ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['pair_id'], ['trading_pairs.id'], name=op.f('fk_positions_pair_id_trading_pairs'), ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['portfolio_id', 'mode'], ['portfolios.id', 'portfolios.mode'], name=op.f('fk_positions_portfolio_id_mode_portfolios'), ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['strategy_id'], ['strategies.id'], name=op.f('fk_positions_strategy_id_strategies'), ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_positions'))
    )
    op.create_index('ix_positions_portfolio_status', 'positions', ['portfolio_id', 'status'], unique=False)
    op.create_index('uq_positions_open_asset', 'positions', ['portfolio_id', 'base_asset'], unique=True, postgresql_where=sa.text("status <> 'closed'"))
    op.create_table('trades',
    sa.Column('id', sa.BigInteger(), sa.Identity(always=True), nullable=False),
    sa.Column('order_id', sa.Integer(), nullable=False),
    sa.Column('mode', sa.String(length=8), nullable=False),
    sa.Column('exchange_trade_id', sa.String(length=128), nullable=False),
    sa.Column('price', sa.Numeric(precision=38, scale=18), nullable=False),
    sa.Column('amount', sa.Numeric(precision=38, scale=18), nullable=False),
    sa.Column('fee', sa.Numeric(precision=38, scale=18), server_default='0', nullable=False),
    sa.Column('fee_asset', sa.String(length=16), nullable=True),
    sa.Column('liquidity', sa.String(length=8), server_default='unknown', nullable=False),
    sa.Column('executed_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint("liquidity IN ('maker', 'taker', 'unknown')", name=op.f('ck_trades_liquidity_valid')),
    sa.CheckConstraint("mode IN ('paper', 'live')", name=op.f('ck_trades_mode_valid')),
    sa.CheckConstraint('fee >= 0', name=op.f('ck_trades_fee_non_negative')),
    sa.CheckConstraint('price > 0 AND amount > 0', name=op.f('ck_trades_positive')),
    sa.ForeignKeyConstraint(['order_id', 'mode'], ['orders.id', 'orders.mode'], name=op.f('fk_trades_order_id_mode_orders'), ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_trades')),
    sa.UniqueConstraint('order_id', 'exchange_trade_id', name=op.f('uq_trades_order_id_exchange_trade_id'))
    )
    op.create_index('ix_trades_executed_at', 'trades', ['executed_at'], unique=False)

    # --- Hand-written: audit log is append-only --------------------------------
    op.execute("""
        CREATE FUNCTION audit_logs_block_mutation() RETURNS trigger
        LANGUAGE plpgsql SET search_path = '' AS $$
        BEGIN
            RAISE EXCEPTION 'audit_logs is append-only (% blocked)', TG_OP
                USING ERRCODE = 'insufficient_privilege';
        END;
        $$
    """)
    op.execute("""
        CREATE TRIGGER audit_logs_append_only
        BEFORE UPDATE OR DELETE ON audit_logs
        FOR EACH ROW EXECUTE FUNCTION audit_logs_block_mutation()
    """)
    op.execute("""
        CREATE TRIGGER audit_logs_no_truncate
        BEFORE TRUNCATE ON audit_logs
        FOR EACH STATEMENT EXECUTE FUNCTION audit_logs_block_mutation()
    """)

    # --- Hand-written: a row's trading mode can never change -------------------
    op.execute("""
        CREATE FUNCTION block_mode_change() RETURNS trigger
        LANGUAGE plpgsql SET search_path = '' AS $$
        BEGIN
            IF NEW.mode IS DISTINCT FROM OLD.mode THEN
                RAISE EXCEPTION '%.mode is immutable', TG_TABLE_NAME
                    USING ERRCODE = 'check_violation';
            END IF;
            RETURN NEW;
        END;
        $$
    """)
    for table in MODE_TABLES:
        op.execute(
            f"CREATE TRIGGER {table}_mode_immutable BEFORE UPDATE OF mode ON {table} "
            f"FOR EACH ROW EXECUTE FUNCTION block_mode_change()"
        )

    # --- Reference data (no market data, no balances: nothing fabricated) ------
    op.execute("""
        INSERT INTO exchanges (id, code, name, is_enabled) VALUES
            (1, 'binance',  'Binance',                  false),
            (2, 'kraken',   'Kraken',                   false),
            (3, 'coinbase', 'Coinbase Advanced Trade',  false)
    """)
    op.execute("""
        INSERT INTO strategies (id, code, name, is_enabled) VALUES
            (1, 'trend_following', 'Trend following', false),
            (2, 'momentum',        'Momentum',        false),
            (3, 'breakout',        'Breakout',        false),
            (4, 'mean_reversion',  'Mean reversion',  false)
    """)


def downgrade() -> None:
    for table in MODE_TABLES:
        op.execute(f"DROP TRIGGER IF EXISTS {table}_mode_immutable ON {table}")
    op.execute("DROP FUNCTION IF EXISTS block_mode_change()")
    op.execute("DROP TRIGGER IF EXISTS audit_logs_no_truncate ON audit_logs")
    op.execute("DROP TRIGGER IF EXISTS audit_logs_append_only ON audit_logs")
    op.execute("DROP FUNCTION IF EXISTS audit_logs_block_mutation()")
    op.drop_index('ix_trades_executed_at', table_name='trades')
    op.drop_table('trades')
    op.drop_index('uq_positions_open_asset', table_name='positions', postgresql_where=sa.text("status <> 'closed'"))
    op.drop_index('ix_positions_portfolio_status', table_name='positions')
    op.drop_table('positions')
    op.drop_index('ix_orders_portfolio_created', table_name='orders')
    op.drop_index('ix_orders_open', table_name='orders', postgresql_where=sa.text("status IN ('pending_submit','submitted','open','partially_filled','unknown')"))
    op.drop_table('orders')
    op.drop_index('ix_trading_signals_status_generated', table_name='trading_signals')
    op.drop_table('trading_signals')
    op.drop_index('ix_risk_events_created', table_name='risk_events')
    op.drop_table('risk_events')
    op.drop_table('performance_snapshots')
    op.drop_index('ix_orderbook_snapshots_pair_time', table_name='orderbook_snapshots')
    op.drop_table('orderbook_snapshots')
    op.drop_index('ix_model_evaluations_model_created', table_name='model_evaluations')
    op.drop_table('model_evaluations')
    op.drop_table('market_candles')
    op.drop_table('indicators')
    op.drop_table('balances')
    op.drop_index('ix_ai_predictions_pair_asof', table_name='ai_predictions')
    op.drop_table('ai_predictions')
    op.drop_table('trading_pairs')
    op.drop_table('portfolios')
    op.drop_index('uq_exchange_credentials_active', table_name='exchange_credentials', postgresql_where=sa.text('revoked_at IS NULL'))
    op.drop_index(op.f('ix_exchange_credentials_user_id'), table_name='exchange_credentials')
    op.drop_table('exchange_credentials')
    op.drop_table('backtest_runs')
    op.drop_index('ix_audit_logs_created', table_name='audit_logs')
    op.drop_table('audit_logs')
    op.drop_table('users')
    op.drop_index('ix_system_events_component_created', table_name='system_events')
    op.drop_table('system_events')
    op.drop_table('strategies')
    op.drop_table('exchanges')
    op.drop_index('uq_ai_models_one_active', table_name='ai_models', postgresql_where=sa.text("status = 'active'"))
    op.drop_table('ai_models')
