"""Portfolio-level risk checks. Pure: the caller supplies a snapshot; the result lists
every reason a new trade is blocked and whether the drawdown shutdown must engage.

Loss limits use equity (realized + unrealized) versus the equity at the start of the
UTC day/ISO week. Drawdown is measured from the running peak equity.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal

from nexus_config import RiskSettings

MAX_SPREAD_BPS = Decimal(25)
MAX_DATA_AGE_SECONDS = 60


@dataclass(frozen=True)
class RiskSnapshot:
    equity: Decimal
    day_start_equity: Decimal
    week_start_equity: Decimal
    peak_equity: Decimal
    open_positions: int
    pending_entries: int
    exposure: Decimal                 # quote value of positions + pending buys
    held_assets: frozenset[str]       # base assets with open/pending exposure (any venue)
    kill_switch: bool
    unreconciled_orders: int          # orders in 'unknown' state
    balances_reconciled: bool
    as_of: datetime


@dataclass(frozen=True)
class MarketCheck:
    base_asset: str
    exchange_healthy: bool
    spread_bps: Decimal | None
    price_age_seconds: float | None


@dataclass(frozen=True)
class RiskDecision:
    approved: bool
    reasons: tuple[str, ...]
    shutdown: bool = False            # engage kill switch (drawdown breach)
    events: tuple[tuple[str, str], ...] = field(default_factory=tuple)  # (type, severity)


def loss_fraction(start: Decimal, now: Decimal) -> Decimal:
    if start <= 0:
        return Decimal(0)
    return (start - now) / start


def evaluate_portfolio(s: RiskSnapshot, limits: RiskSettings) -> RiskDecision:
    """Checks that apply regardless of the specific trade."""
    reasons: list[str] = []
    events: list[tuple[str, str]] = []
    shutdown = False
    drawdown = loss_fraction(s.peak_equity, s.equity)
    if drawdown >= limits.max_drawdown:
        reasons.append(f"max drawdown {drawdown:.4%} >= {limits.max_drawdown:.2%}: shutdown")
        events.append(("max_drawdown_shutdown", "critical"))
        shutdown = True
    elif drawdown >= limits.max_drawdown * Decimal("0.8"):
        events.append(("drawdown_warning", "warning"))
    daily = loss_fraction(s.day_start_equity, s.equity)
    if daily >= limits.daily_loss_limit:
        reasons.append(f"daily loss {daily:.4%} >= {limits.daily_loss_limit:.2%}")
        events.append(("daily_loss_limit", "error"))
    elif daily >= limits.daily_loss_limit * Decimal("0.8"):
        events.append(("daily_loss_warning", "warning"))
    weekly = loss_fraction(s.week_start_equity, s.equity)
    if weekly >= limits.weekly_loss_limit:
        reasons.append(f"weekly loss {weekly:.4%} >= {limits.weekly_loss_limit:.2%}")
        events.append(("weekly_loss_limit", "error"))
    elif weekly >= limits.weekly_loss_limit * Decimal("0.8"):
        events.append(("weekly_loss_warning", "warning"))
    if s.kill_switch:
        reasons.append("kill switch engaged")
    if s.unreconciled_orders:
        reasons.append(f"{s.unreconciled_orders} order(s) in unknown state")
    if not s.balances_reconciled:
        reasons.append("balances not reconciled with exchange")
    if s.open_positions + s.pending_entries >= limits.max_open_positions:
        reasons.append(f"max open positions ({limits.max_open_positions}) reached")
    if s.exposure >= s.equity * limits.max_portfolio_exposure:
        reasons.append("max portfolio exposure reached")
    return RiskDecision(not reasons, tuple(reasons), shutdown, tuple(events))


def evaluate_trade(s: RiskSnapshot, m: MarketCheck, limits: RiskSettings) -> RiskDecision:
    base = evaluate_portfolio(s, limits)
    reasons = list(base.reasons)
    if m.base_asset in s.held_assets:
        reasons.append(f"already exposed to {m.base_asset} (no duplicate exposure)")
    if not m.exchange_healthy:
        reasons.append("exchange unhealthy")
    if m.price_age_seconds is None or m.price_age_seconds > MAX_DATA_AGE_SECONDS:
        reasons.append("market data stale or missing")
    if m.spread_bps is None or m.spread_bps > MAX_SPREAD_BPS:
        reasons.append(f"spread too wide or unknown ({m.spread_bps} bps)")
    return RiskDecision(not reasons, tuple(reasons), base.shutdown, base.events)
