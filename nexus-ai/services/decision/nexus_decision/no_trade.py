"""NO TRADE intelligence: explicit reasons not to trade.

Each check returns a human-readable reason when it blocks trading, and nothing otherwise.
Thresholds are configuration, never hidden inside the check.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal


@dataclass(frozen=True)
class NoTradeInputs:
    now: datetime
    data_time: datetime | None          # time of the newest candle/book used
    exchange_healthy: bool
    spread_bps: Decimal | None
    depth_quote: Decimal | None         # visible liquidity near the touch (quote)
    order_quote: Decimal                # intended order value
    atr_percentile: float | None        # 0..1 rank of current ATR in its history
    news_risk: float | None             # 0..1 from the news module; None = unknown
    daily_loss_limit_reached: bool
    weekly_loss_limit_reached: bool
    kill_switch: bool
    reward_risk: float | None           # reward / risk of the proposed trade
    confirmations_conflict: bool        # long and short signals both firing
    regime: str | None                  # trend_up/trend_down/range/high_vol
    setup_present: bool                 # a named setup (BOS, sweep, zone) exists


@dataclass(frozen=True)
class Thresholds:
    max_spread_bps: Decimal = Decimal(25)
    min_depth_multiple: Decimal = Decimal(4)   # depth must be >= 4x the order
    max_data_age_seconds: float = 90.0
    max_atr_percentile: float = 0.95            # abnormal volatility
    max_news_risk: float = 0.7
    min_reward_risk: float = 2.0                # mandate: at least 1:2


DEFAULT = Thresholds()


def no_trade_reasons(x: NoTradeInputs, t: Thresholds = DEFAULT) -> list[str]:
    reasons: list[str] = []
    if x.kill_switch:
        reasons.append("kill switch engaged")
    if x.daily_loss_limit_reached:
        reasons.append("daily loss limit reached")
    if x.weekly_loss_limit_reached:
        reasons.append("weekly loss limit reached")
    if not x.exchange_healthy:
        reasons.append("exchange unhealthy or unreachable")
    if x.data_time is None:
        reasons.append("market data missing")
    elif (x.now - x.data_time).total_seconds() > t.max_data_age_seconds:
        age = (x.now - x.data_time).total_seconds()
        reasons.append(f"market data stale ({age:.0f}s old)")
    if x.spread_bps is None:
        reasons.append("spread unknown")
    elif x.spread_bps > t.max_spread_bps:
        reasons.append(f"spread too wide ({x.spread_bps} bps > {t.max_spread_bps})")
    if x.depth_quote is None or x.depth_quote < x.order_quote * t.min_depth_multiple:
        reasons.append("insufficient liquidity for the order size")
    if x.atr_percentile is not None and x.atr_percentile > t.max_atr_percentile:
        reasons.append("abnormal volatility (ATR above its 95th percentile)")
    if x.regime == "high_vol":
        reasons.append("high-volatility regime")
    if x.news_risk is None:
        reasons.append("news risk unknown")
    elif x.news_risk > t.max_news_risk:
        reasons.append(f"high-impact news window (risk {x.news_risk:.2f})")
    if x.reward_risk is None:
        reasons.append("no valid target for risk/reward")
    elif x.reward_risk < t.min_reward_risk:
        reasons.append(f"risk/reward {x.reward_risk:.2f} below minimum {t.min_reward_risk:.1f}")
    if x.confirmations_conflict:
        reasons.append("conflicting signals (long and short both firing)")
    if x.regime == "range" and not x.setup_present:
        reasons.append("sideways market without a valid setup")
    if not x.setup_present:
        reasons.append("no named setup (BOS, sweep, zone or pullback)")
    return reasons
