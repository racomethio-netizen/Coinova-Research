"""SQLAlchemy models. The Alembic migrations in ``migrations/`` are the source of truth;
``tests/integration/test_migrations.py`` fails if these models and the migrated schema
drift apart.

Paper/live isolation: every trading record carries ``mode`` and references its parent
through a composite foreign key ``(parent_id, mode)``. PostgreSQL therefore rejects,
for example, a live order in a paper portfolio or a paper fill on a live order.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    LargeBinary,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import INET
from sqlalchemy.orm import Mapped, mapped_column

from nexus_db.base import TRADING_MODES, Base, Ratio, bigint_pk, created_at, in_list, updated_at

TIMEFRAMES = ("5m", "15m", "1h", "4h")
SIDES = ("buy", "sell")
SEVERITIES = ("debug", "info", "warning", "error", "critical")
ORDER_STATUSES = (
    "pending_submit",  # row written BEFORE the exchange call (duplicate prevention)
    "submitted",       # sent; awaiting acknowledgement
    "open",
    "partially_filled",
    "filled",
    "canceled",
    "rejected",
    "expired",
    "unknown",         # timeout/ambiguous; must be reconciled by client order id
)
ORDER_TYPES = ("limit", "market")
MODE_CHECK = in_list("mode", TRADING_MODES)


# --------------------------------------------------------------------------- identity
class User(Base):
    __tablename__ = "users"
    __table_args__ = (
        CheckConstraint("email = lower(email)", name="email_lowercase"),
        CheckConstraint(in_list("role", ("owner", "viewer")), name="role_valid"),
    )

    id: Mapped[bigint_pk]
    email: Mapped[str] = mapped_column(String(320), unique=True)
    password_hash: Mapped[str] = mapped_column(Text)
    role: Mapped[str] = mapped_column(String(16), server_default="viewer")
    totp_secret_encrypted: Mapped[bytes | None] = mapped_column(LargeBinary)
    is_active: Mapped[bool] = mapped_column(Boolean, server_default=text("true"))
    failed_login_count: Mapped[int] = mapped_column(Integer, server_default="0")
    locked_until: Mapped[datetime | None]
    last_login_at: Mapped[datetime | None]
    created_at: Mapped[created_at]
    updated_at: Mapped[updated_at]


# --------------------------------------------------------------------------- exchanges
class Exchange(Base):
    __tablename__ = "exchanges"

    id: Mapped[int] = mapped_column(SmallInteger, primary_key=True)
    code: Mapped[str] = mapped_column(String(32), unique=True)
    name: Mapped[str] = mapped_column(String(64))
    is_enabled: Mapped[bool] = mapped_column(Boolean, server_default=text("false"))
    # Honest per-exchange feature support (see services/exchange-gateway).
    capabilities: Mapped[dict[str, Any]] = mapped_column(server_default=text("'{}'::jsonb"))
    created_at: Mapped[created_at]
    updated_at: Mapped[updated_at]


class ExchangeCredential(Base):
    __tablename__ = "exchange_credentials"
    __table_args__ = (
        CheckConstraint(in_list("environment", ("production", "sandbox")), name="environment_valid"),
        UniqueConstraint("user_id", "exchange_id", "label"),
        # At most one usable credential per user/exchange/environment.
        Index(
            "uq_exchange_credentials_active",
            "user_id", "exchange_id", "environment",
            unique=True,
            postgresql_where=text("revoked_at IS NULL"),
        ),
    )

    id: Mapped[bigint_pk]
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    exchange_id: Mapped[int] = mapped_column(ForeignKey("exchanges.id", ondelete="RESTRICT"))
    label: Mapped[str] = mapped_column(String(64))
    environment: Mapped[str] = mapped_column(String(16), server_default="production")
    # AES-256-GCM ciphertexts (nexus_security.crypto). Plaintext is never stored.
    api_key_encrypted: Mapped[bytes] = mapped_column(LargeBinary)
    api_secret_encrypted: Mapped[bytes] = mapped_column(LargeBinary)
    passphrase_encrypted: Mapped[bytes | None] = mapped_column(LargeBinary)
    key_fingerprint: Mapped[str] = mapped_column(String(16))
    encryption_key_id: Mapped[str] = mapped_column(String(32))
    # Result of the last permission probe; trading is refused unless withdrawals are
    # verified disabled where the exchange lets us check.
    permissions: Mapped[dict[str, Any]] = mapped_column(server_default=text("'{}'::jsonb"))
    withdrawal_disabled_verified: Mapped[bool] = mapped_column(Boolean, server_default=text("false"))
    last_verified_at: Mapped[datetime | None]
    created_at: Mapped[created_at]
    rotated_at: Mapped[datetime | None]
    revoked_at: Mapped[datetime | None]


class TradingPair(Base):
    __tablename__ = "trading_pairs"
    __table_args__ = (
        UniqueConstraint("exchange_id", "symbol"),
        CheckConstraint("price_tick IS NULL OR price_tick > 0", name="price_tick_positive"),
        CheckConstraint("amount_step IS NULL OR amount_step > 0", name="amount_step_positive"),
    )

    id: Mapped[bigint_pk]
    exchange_id: Mapped[int] = mapped_column(ForeignKey("exchanges.id", ondelete="RESTRICT"))
    symbol: Mapped[str] = mapped_column(String(32))            # unified, e.g. BTC/USDT
    exchange_symbol: Mapped[str] = mapped_column(String(64))   # native, e.g. BTCUSDT
    base_asset: Mapped[str] = mapped_column(String(16))
    quote_asset: Mapped[str] = mapped_column(String(16))
    price_tick: Mapped[Decimal | None]
    amount_step: Mapped[Decimal | None]
    min_amount: Mapped[Decimal | None]
    min_notional: Mapped[Decimal | None]
    maker_fee: Mapped[Decimal | None] = mapped_column(Ratio)
    taker_fee: Mapped[Decimal | None] = mapped_column(Ratio)
    is_active: Mapped[bool] = mapped_column(Boolean, server_default=text("true"))
    metadata_synced_at: Mapped[datetime | None]
    created_at: Mapped[created_at]
    updated_at: Mapped[updated_at]


# --------------------------------------------------------------------------- market data
class MarketCandle(Base):
    __tablename__ = "market_candles"
    __table_args__ = (
        CheckConstraint(in_list("timeframe", TIMEFRAMES), name="timeframe_valid"),
        CheckConstraint(
            "high >= low AND high >= open AND high >= close AND low <= open AND low <= close",
            name="ohlc_consistent",
        ),
        CheckConstraint("volume >= 0", name="volume_non_negative"),
    )

    pair_id: Mapped[int] = mapped_column(
        ForeignKey("trading_pairs.id", ondelete="CASCADE"), primary_key=True
    )
    timeframe: Mapped[str] = mapped_column(String(4), primary_key=True)
    open_time: Mapped[datetime] = mapped_column(primary_key=True)
    open: Mapped[Decimal]
    high: Mapped[Decimal]
    low: Mapped[Decimal]
    close: Mapped[Decimal]
    volume: Mapped[Decimal]
    # Only closed candles may feed features/backtests (look-ahead protection).
    is_closed: Mapped[bool] = mapped_column(Boolean, server_default=text("false"))
    source: Mapped[str] = mapped_column(String(16), server_default="rest")
    ingested_at: Mapped[created_at]


class OrderbookSnapshot(Base):
    __tablename__ = "orderbook_snapshots"
    __table_args__ = (
        Index("ix_orderbook_snapshots_pair_time", "pair_id", "captured_at"),
        CheckConstraint("best_ask IS NULL OR best_bid IS NULL OR best_ask >= best_bid",
                        name="not_crossed"),
    )

    id: Mapped[bigint_pk]
    pair_id: Mapped[int] = mapped_column(ForeignKey("trading_pairs.id", ondelete="CASCADE"))
    captured_at: Mapped[datetime]
    best_bid: Mapped[Decimal | None]
    best_ask: Mapped[Decimal | None]
    spread_bps: Mapped[Decimal | None] = mapped_column(Ratio)
    bid_depth_quote: Mapped[Decimal | None]  # quote value within N bps of mid
    ask_depth_quote: Mapped[Decimal | None]
    bids: Mapped[list[Any]]  # [[price, amount], ...] as strings
    asks: Mapped[list[Any]]


class Indicator(Base):
    __tablename__ = "indicators"
    __table_args__ = (CheckConstraint(in_list("timeframe", TIMEFRAMES), name="timeframe_valid"),)

    pair_id: Mapped[int] = mapped_column(
        ForeignKey("trading_pairs.id", ondelete="CASCADE"), primary_key=True
    )
    timeframe: Mapped[str] = mapped_column(String(4), primary_key=True)
    open_time: Mapped[datetime] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(64), primary_key=True)  # e.g. ema_21
    value: Mapped[Decimal | None]
    computed_at: Mapped[created_at]


# --------------------------------------------------------------------------- AI
class AiModel(Base):
    __tablename__ = "ai_models"
    __table_args__ = (
        UniqueConstraint("name", "version"),
        CheckConstraint(
            in_list("status", ("training", "candidate", "validated", "rejected", "active", "retired")),
            name="status_valid",
        ),
        # Only one active version per model name.
        Index("uq_ai_models_one_active", "name", unique=True,
              postgresql_where=text("status = 'active'")),
    )

    id: Mapped[bigint_pk]
    name: Mapped[str] = mapped_column(String(64))
    version: Mapped[str] = mapped_column(String(32))
    algorithm: Mapped[str] = mapped_column(String(32))
    status: Mapped[str] = mapped_column(String(16), server_default="training")
    feature_set: Mapped[dict[str, Any]]
    hyperparameters: Mapped[dict[str, Any]]
    training_start: Mapped[datetime | None]
    training_end: Mapped[datetime | None]
    artifact_uri: Mapped[str | None] = mapped_column(Text)
    artifact_sha256: Mapped[str | None] = mapped_column(String(64))
    code_version: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[created_at]
    promoted_at: Mapped[datetime | None]


class AiPrediction(Base):
    __tablename__ = "ai_predictions"
    __table_args__ = (
        UniqueConstraint("model_id", "pair_id", "timeframe", "as_of"),
        CheckConstraint(in_list("timeframe", TIMEFRAMES), name="timeframe_valid"),
        CheckConstraint("probability >= 0 AND probability <= 1", name="probability_range"),
        CheckConstraint(
            "calibrated_probability IS NULL OR "
            "(calibrated_probability >= 0 AND calibrated_probability <= 1)",
            name="calibrated_range",
        ),
        Index("ix_ai_predictions_pair_asof", "pair_id", "as_of"),
    )

    id: Mapped[bigint_pk]
    model_id: Mapped[int] = mapped_column(ForeignKey("ai_models.id", ondelete="RESTRICT"))
    pair_id: Mapped[int] = mapped_column(ForeignKey("trading_pairs.id", ondelete="CASCADE"))
    timeframe: Mapped[str] = mapped_column(String(4))
    as_of: Mapped[datetime]  # open_time of the last CLOSED candle used as input
    horizon_bars: Mapped[int] = mapped_column(SmallInteger)
    predicted_class: Mapped[str] = mapped_column(String(16))
    probability: Mapped[Decimal] = mapped_column(Ratio)
    calibrated_probability: Mapped[Decimal | None] = mapped_column(Ratio)
    regime: Mapped[str | None] = mapped_column(String(32))
    features_hash: Mapped[str] = mapped_column(String(64))
    # Filled in later to measure live accuracy/drift; never used as a feature.
    realized_outcome: Mapped[str | None] = mapped_column(String(16))
    created_at: Mapped[created_at]


class ModelEvaluation(Base):
    __tablename__ = "model_evaluations"
    __table_args__ = (
        CheckConstraint(
            in_list("evaluation_type",
                    ("validation", "out_of_sample", "walk_forward", "paper", "live_drift")),
            name="type_valid",
        ),
        CheckConstraint("period_end > period_start", name="period_ordered"),
        Index("ix_model_evaluations_model_created", "model_id", "created_at"),
    )

    id: Mapped[bigint_pk]
    model_id: Mapped[int] = mapped_column(ForeignKey("ai_models.id", ondelete="CASCADE"))
    backtest_run_id: Mapped[int | None] = mapped_column(
        ForeignKey("backtest_runs.id", ondelete="SET NULL")
    )
    evaluation_type: Mapped[str] = mapped_column(String(16))
    period_start: Mapped[datetime]
    period_end: Mapped[datetime]
    metrics: Mapped[dict[str, Any]]
    passed: Mapped[bool] = mapped_column(Boolean)
    failure_reasons: Mapped[list[Any]] = mapped_column(server_default=text("'[]'::jsonb"))
    created_at: Mapped[created_at]


# --------------------------------------------------------------------------- strategies
class Strategy(Base):
    __tablename__ = "strategies"
    __table_args__ = (
        CheckConstraint(
            in_list("code", ("trend_following", "momentum", "breakout", "mean_reversion")),
            name="code_valid",
        ),
    )

    id: Mapped[int] = mapped_column(SmallInteger, primary_key=True)
    code: Mapped[str] = mapped_column(String(32), unique=True)
    name: Mapped[str] = mapped_column(String(64))
    version: Mapped[str] = mapped_column(String(16), server_default="0.1.0")
    parameters: Mapped[dict[str, Any]] = mapped_column(server_default=text("'{}'::jsonb"))
    is_enabled: Mapped[bool] = mapped_column(Boolean, server_default=text("false"))
    created_at: Mapped[created_at]
    updated_at: Mapped[updated_at]


class TradingSignal(Base):
    __tablename__ = "trading_signals"
    __table_args__ = (
        CheckConstraint(MODE_CHECK, name="mode_valid"),
        CheckConstraint(in_list("side", SIDES), name="side_valid"),
        CheckConstraint(in_list("timeframe", TIMEFRAMES), name="timeframe_valid"),
        CheckConstraint(
            in_list("status", ("new", "approved", "rejected", "expired", "executed")),
            name="status_valid",
        ),
        CheckConstraint("confidence >= 0 AND confidence <= 1", name="confidence_range"),
        # Long-only spot: a buy signal's stop must be below entry and target above.
        CheckConstraint(
            "side <> 'buy' OR stop_loss IS NULL OR stop_loss < entry_price",
            name="buy_stop_below_entry",
        ),
        CheckConstraint(
            "side <> 'buy' OR take_profit IS NULL OR take_profit > entry_price",
            name="buy_target_above_entry",
        ),
        UniqueConstraint("id", "mode"),
        # One signal per strategy/pair/timeframe/bar/mode (idempotent generation).
        UniqueConstraint("strategy_id", "pair_id", "timeframe", "bar_time", "mode"),
        Index("ix_trading_signals_status_generated", "status", "generated_at"),
    )

    id: Mapped[bigint_pk]
    mode: Mapped[str] = mapped_column(String(8))
    strategy_id: Mapped[int] = mapped_column(ForeignKey("strategies.id", ondelete="RESTRICT"))
    pair_id: Mapped[int] = mapped_column(ForeignKey("trading_pairs.id", ondelete="RESTRICT"))
    prediction_id: Mapped[int | None] = mapped_column(
        ForeignKey("ai_predictions.id", ondelete="SET NULL")
    )
    side: Mapped[str] = mapped_column(String(4))
    timeframe: Mapped[str] = mapped_column(String(4))
    bar_time: Mapped[datetime]
    generated_at: Mapped[created_at]
    entry_price: Mapped[Decimal]
    stop_loss: Mapped[Decimal | None]
    take_profit: Mapped[Decimal | None]
    confidence: Mapped[Decimal] = mapped_column(Ratio)
    status: Mapped[str] = mapped_column(String(16), server_default="new")
    rejection_reason: Mapped[str | None] = mapped_column(Text)
    details: Mapped[dict[str, Any]] = mapped_column(server_default=text("'{}'::jsonb"))
    expires_at: Mapped[datetime | None]


# --------------------------------------------------------------------------- portfolio
class Portfolio(Base):
    __tablename__ = "portfolios"
    __table_args__ = (
        CheckConstraint(MODE_CHECK, name="mode_valid"),
        UniqueConstraint("id", "mode"),  # target of composite FKs
        UniqueConstraint("user_id", "name", "mode"),
    )

    id: Mapped[bigint_pk]
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"))
    name: Mapped[str] = mapped_column(String(64))
    mode: Mapped[str] = mapped_column(String(8))
    quote_currency: Mapped[str] = mapped_column(String(16), server_default="USDT")
    # Paper only: starting virtual capital. NULL for live (equity comes from exchanges).
    initial_capital: Mapped[Decimal | None]
    is_active: Mapped[bool] = mapped_column(Boolean, server_default=text("true"))
    created_at: Mapped[created_at]


def _portfolio_fk() -> ForeignKeyConstraint:
    return ForeignKeyConstraint(
        ["portfolio_id", "mode"], ["portfolios.id", "portfolios.mode"], ondelete="RESTRICT"
    )


class Balance(Base):
    __tablename__ = "balances"
    __table_args__ = (
        CheckConstraint(MODE_CHECK, name="mode_valid"),
        CheckConstraint("free >= 0 AND locked >= 0", name="non_negative"),
        CheckConstraint(in_list("source", ("internal", "exchange")), name="source_valid"),
        _portfolio_fk(),
        UniqueConstraint("portfolio_id", "exchange_id", "asset", "source"),
    )

    id: Mapped[bigint_pk]
    portfolio_id: Mapped[int]
    mode: Mapped[str] = mapped_column(String(8))
    exchange_id: Mapped[int] = mapped_column(ForeignKey("exchanges.id", ondelete="RESTRICT"))
    asset: Mapped[str] = mapped_column(String(16))
    free: Mapped[Decimal]
    locked: Mapped[Decimal]
    # 'internal' = our ledger; 'exchange' = last value reported by the exchange.
    source: Mapped[str] = mapped_column(String(8))
    synced_at: Mapped[datetime]


class Order(Base):
    __tablename__ = "orders"
    __table_args__ = (
        CheckConstraint(MODE_CHECK, name="mode_valid"),
        CheckConstraint(in_list("side", SIDES), name="side_valid"),
        CheckConstraint(in_list("order_type", ORDER_TYPES), name="type_valid"),
        CheckConstraint(in_list("status", ORDER_STATUSES), name="status_valid"),
        CheckConstraint("amount > 0", name="amount_positive"),
        CheckConstraint("price IS NULL OR price > 0", name="price_positive"),
        CheckConstraint("order_type <> 'limit' OR price IS NOT NULL", name="limit_has_price"),
        CheckConstraint("filled_amount >= 0 AND filled_amount <= amount", name="fill_bounded"),
        CheckConstraint("fee_amount >= 0", name="fee_non_negative"),
        _portfolio_fk(),
        ForeignKeyConstraint(
            ["signal_id", "mode"], ["trading_signals.id", "trading_signals.mode"],
            ondelete="RESTRICT",
        ),
        UniqueConstraint("id", "mode"),
        UniqueConstraint("exchange_id", "exchange_order_id"),
        Index("ix_orders_open", "status", "exchange_id",
              postgresql_where=text(
                  "status IN ('pending_submit','submitted','open','partially_filled','unknown')"
              )),
        Index("ix_orders_portfolio_created", "portfolio_id", "created_at"),
    )

    id: Mapped[bigint_pk]
    portfolio_id: Mapped[int]
    mode: Mapped[str] = mapped_column(String(8))
    exchange_id: Mapped[int] = mapped_column(ForeignKey("exchanges.id", ondelete="RESTRICT"))
    pair_id: Mapped[int] = mapped_column(ForeignKey("trading_pairs.id", ondelete="RESTRICT"))
    signal_id: Mapped[int | None]
    # Generated by us before submission; the idempotency key at the exchange.
    client_order_id: Mapped[str] = mapped_column(String(36), unique=True)
    exchange_order_id: Mapped[str | None] = mapped_column(String(128))
    side: Mapped[str] = mapped_column(String(4))
    order_type: Mapped[str] = mapped_column(String(8))
    time_in_force: Mapped[str | None] = mapped_column(String(8))
    price: Mapped[Decimal | None]
    amount: Mapped[Decimal]
    filled_amount: Mapped[Decimal] = mapped_column(server_default="0")
    average_fill_price: Mapped[Decimal | None]
    fee_amount: Mapped[Decimal] = mapped_column(server_default="0")
    fee_asset: Mapped[str | None] = mapped_column(String(16))
    # Reference price at decision time, for slippage measurement.
    expected_price: Mapped[Decimal | None]
    status: Mapped[str] = mapped_column(String(20), server_default="pending_submit")
    last_error: Mapped[str | None] = mapped_column(Text)
    submitted_at: Mapped[datetime | None]
    last_checked_at: Mapped[datetime | None]
    closed_at: Mapped[datetime | None]
    created_at: Mapped[created_at]
    updated_at: Mapped[updated_at]


class Trade(Base):
    """An individual fill (execution) of an order."""

    __tablename__ = "trades"
    __table_args__ = (
        CheckConstraint(MODE_CHECK, name="mode_valid"),
        CheckConstraint("price > 0 AND amount > 0", name="positive"),
        CheckConstraint("fee >= 0", name="fee_non_negative"),
        CheckConstraint(in_list("liquidity", ("maker", "taker", "unknown")), name="liquidity_valid"),
        ForeignKeyConstraint(["order_id", "mode"], ["orders.id", "orders.mode"], ondelete="RESTRICT"),
        UniqueConstraint("order_id", "exchange_trade_id"),
        Index("ix_trades_executed_at", "executed_at"),
    )

    id: Mapped[bigint_pk]
    order_id: Mapped[int]
    mode: Mapped[str] = mapped_column(String(8))
    exchange_trade_id: Mapped[str] = mapped_column(String(128))
    price: Mapped[Decimal]
    amount: Mapped[Decimal]
    fee: Mapped[Decimal] = mapped_column(server_default="0")
    fee_asset: Mapped[str | None] = mapped_column(String(16))
    liquidity: Mapped[str] = mapped_column(String(8), server_default="unknown")
    executed_at: Mapped[datetime]
    created_at: Mapped[created_at]


class Position(Base):
    __tablename__ = "positions"
    __table_args__ = (
        CheckConstraint(MODE_CHECK, name="mode_valid"),
        CheckConstraint(in_list("status", ("open", "closing", "closed")), name="status_valid"),
        CheckConstraint("quantity >= 0", name="quantity_non_negative"),  # long-only spot
        CheckConstraint("status <> 'closed' OR closed_at IS NOT NULL", name="closed_has_time"),
        _portfolio_fk(),
        ForeignKeyConstraint(["entry_order_id", "mode"], ["orders.id", "orders.mode"],
                             ondelete="RESTRICT"),
        # No duplicate exposure to the same base asset across exchanges.
        Index("uq_positions_open_asset", "portfolio_id", "base_asset", unique=True,
              postgresql_where=text("status <> 'closed'")),
        Index("ix_positions_portfolio_status", "portfolio_id", "status"),
    )

    id: Mapped[bigint_pk]
    portfolio_id: Mapped[int]
    mode: Mapped[str] = mapped_column(String(8))
    exchange_id: Mapped[int] = mapped_column(ForeignKey("exchanges.id", ondelete="RESTRICT"))
    pair_id: Mapped[int] = mapped_column(ForeignKey("trading_pairs.id", ondelete="RESTRICT"))
    base_asset: Mapped[str] = mapped_column(String(16))
    strategy_id: Mapped[int | None] = mapped_column(ForeignKey("strategies.id", ondelete="SET NULL"))
    entry_order_id: Mapped[int | None]
    status: Mapped[str] = mapped_column(String(8), server_default="open")
    quantity: Mapped[Decimal]
    avg_entry_price: Mapped[Decimal]
    cost_basis: Mapped[Decimal]  # quote spent including fees
    stop_loss: Mapped[Decimal | None]
    take_profit: Mapped[Decimal | None]
    realized_pnl: Mapped[Decimal] = mapped_column(server_default="0")
    fees_paid: Mapped[Decimal] = mapped_column(server_default="0")
    exit_reason: Mapped[str | None] = mapped_column(String(32))
    opened_at: Mapped[datetime]
    closed_at: Mapped[datetime | None]
    updated_at: Mapped[updated_at]


class PerformanceSnapshot(Base):
    __tablename__ = "performance_snapshots"
    __table_args__ = (
        CheckConstraint(MODE_CHECK, name="mode_valid"),
        CheckConstraint("drawdown >= 0 AND drawdown <= 1", name="drawdown_range"),
        _portfolio_fk(),
        UniqueConstraint("portfolio_id", "captured_at"),
    )

    id: Mapped[bigint_pk]
    portfolio_id: Mapped[int]
    mode: Mapped[str] = mapped_column(String(8))
    captured_at: Mapped[datetime]
    equity: Mapped[Decimal]
    cash: Mapped[Decimal]
    exposure: Mapped[Decimal]
    realized_pnl: Mapped[Decimal]
    unrealized_pnl: Mapped[Decimal]
    fees_paid: Mapped[Decimal]
    peak_equity: Mapped[Decimal]
    drawdown: Mapped[Decimal] = mapped_column(Ratio)
    open_positions: Mapped[int] = mapped_column(SmallInteger)


# --------------------------------------------------------------------------- research
class BacktestRun(Base):
    __tablename__ = "backtest_runs"
    __table_args__ = (
        CheckConstraint(in_list("status", ("queued", "running", "succeeded", "failed")),
                        name="status_valid"),
        CheckConstraint("data_end > data_start", name="period_ordered"),
        CheckConstraint("fee_bps >= 0 AND slippage_bps >= 0", name="costs_non_negative"),
    )

    id: Mapped[bigint_pk]
    strategy_id: Mapped[int | None] = mapped_column(ForeignKey("strategies.id", ondelete="SET NULL"))
    model_id: Mapped[int | None] = mapped_column(ForeignKey("ai_models.id", ondelete="SET NULL"))
    status: Mapped[str] = mapped_column(String(16), server_default="queued")
    is_walk_forward: Mapped[bool] = mapped_column(Boolean, server_default=text("false"))
    config: Mapped[dict[str, Any]]
    data_start: Mapped[datetime]
    data_end: Mapped[datetime]
    fee_bps: Mapped[Decimal] = mapped_column(Ratio)
    slippage_bps: Mapped[Decimal] = mapped_column(Ratio)
    metrics: Mapped[dict[str, Any] | None]
    error: Mapped[str | None] = mapped_column(Text)
    code_version: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[created_at]
    completed_at: Mapped[datetime | None]


# --------------------------------------------------------------------------- operations
class RiskEvent(Base):
    __tablename__ = "risk_events"
    __table_args__ = (
        CheckConstraint(MODE_CHECK, name="mode_valid"),
        CheckConstraint(in_list("severity", SEVERITIES), name="severity_valid"),
        ForeignKeyConstraint(["portfolio_id", "mode"], ["portfolios.id", "portfolios.mode"],
                             ondelete="RESTRICT"),
        Index("ix_risk_events_created", "created_at"),
    )

    id: Mapped[bigint_pk]
    mode: Mapped[str] = mapped_column(String(8))
    portfolio_id: Mapped[int | None]
    event_type: Mapped[str] = mapped_column(String(48))  # e.g. daily_loss_limit, kill_switch
    severity: Mapped[str] = mapped_column(String(8))
    details: Mapped[dict[str, Any]] = mapped_column(server_default=text("'{}'::jsonb"))
    created_at: Mapped[created_at]
    resolved_at: Mapped[datetime | None]


class SystemEvent(Base):
    __tablename__ = "system_events"
    __table_args__ = (
        CheckConstraint(in_list("severity", SEVERITIES), name="severity_valid"),
        Index("ix_system_events_component_created", "component", "created_at"),
    )

    id: Mapped[bigint_pk]
    component: Mapped[str] = mapped_column(String(48))
    event_type: Mapped[str] = mapped_column(String(48))
    severity: Mapped[str] = mapped_column(String(8))
    message: Mapped[str] = mapped_column(Text)
    details: Mapped[dict[str, Any]] = mapped_column(server_default=text("'{}'::jsonb"))
    created_at: Mapped[created_at]


class AuditLog(Base):
    """Append-only (UPDATE/DELETE blocked by a trigger in the initial migration)."""

    __tablename__ = "audit_logs"
    __table_args__ = (Index("ix_audit_logs_created", "created_at"),)

    id: Mapped[bigint_pk]
    user_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="SET NULL")
    )
    actor: Mapped[str] = mapped_column(String(64))  # 'user:<id>', 'system:execution', ...
    action: Mapped[str] = mapped_column(String(64))
    target_type: Mapped[str | None] = mapped_column(String(48))
    target_id: Mapped[str | None] = mapped_column(String(64))
    details: Mapped[dict[str, Any]] = mapped_column(server_default=text("'{}'::jsonb"))
    ip_address: Mapped[str | None] = mapped_column(INET)
    created_at: Mapped[created_at]
