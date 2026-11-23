"""Risk and position rules of the spot strategy engine (docs/SPOT_ENGINE_PLAN.md).

Pure functions shared by the backtester and the paper runner, so both apply exactly the
same rules. Floats are used for simulation. The paper runner's ledger and orders keep
using Decimal through the order manager.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime, timedelta

# Hard ceilings (nexus_config): weekly loss 4%. The brief asks 5% weekly; the stricter
# ceiling applies unless the owner explicitly mandates a change.
WEEKLY_CEILING = 0.04


@dataclass(frozen=True)
class SpotRiskConfig:
    risk_per_trade: float = 0.0025
    max_risk_per_trade: float = 0.005
    max_open_risk: float = 0.01          # sum of open positions' risk to their stops
    max_daily_drawdown: float = 0.02
    max_weekly_drawdown: float = 0.04    # brief: 5%; capped at the 4% ceiling
    max_positions: int = 3
    max_allocation: float = 0.20         # notional per position / equity
    loss_streak: int = 3
    cooldown: timedelta = timedelta(hours=24)
    max_correlated_positions: int = 2    # positions within one correlated cluster
    correlation_threshold: float = 0.8
    taker_fee: float = 0.001
    maker_fee: float = 0.001
    trailing_atr: float | None = None    # optional ATR trailing after TP1
    tp1_fraction: float = 0.5            # share closed at TP1 (partial exit)

    def __post_init__(self) -> None:
        if self.risk_per_trade > self.max_risk_per_trade:
            raise ValueError("risk_per_trade above max_risk_per_trade")
        if self.max_weekly_drawdown > WEEKLY_CEILING:
            raise ValueError(f"weekly drawdown above the {WEEKLY_CEILING:.0%} ceiling")


@dataclass
class Position:
    symbol: str
    strategy: str
    qty: float
    entry: float
    stop: float
    initial_stop: float
    tp1: float
    tp2: float
    opened_at: datetime
    regime: str | None = None
    tp1_done: bool = False
    highest: float = 0.0
    fees: float = 0.0
    realized: float = 0.0               # P&L of partial exits, after their fees

    @property
    def r_per_unit(self) -> float:
        return self.entry - self.initial_stop

    def open_risk(self) -> float:
        """Money lost if the stop is hit now (0 once the stop is at or above entry)."""
        return max(0.0, (self.entry - self.stop) * self.qty)


@dataclass
class BookState:
    equity: float
    cash: float
    positions: list[Position]
    day_start_equity: float
    week_start_equity: float
    losses_in_a_row: int = 0
    cooldown_until: datetime | None = None
    correlated: dict[str, set[str]] = field(default_factory=dict)   # symbol -> cluster


def entry_checks(st: BookState, symbol: str, entry: float, stop: float, now: datetime,
                 cfg: SpotRiskConfig, *, risk_mult: float = 1.0) -> list[str]:
    """Portfolio-level reasons to refuse a new entry (empty = allowed)."""
    reasons = []
    if st.equity <= 0:
        reasons.append("no equity")
    if st.equity < st.day_start_equity * (1 - cfg.max_daily_drawdown):
        reasons.append("daily drawdown limit reached")
    if st.equity < st.week_start_equity * (1 - cfg.max_weekly_drawdown):
        reasons.append("weekly drawdown limit reached")
    if st.cooldown_until is not None and now < st.cooldown_until:
        reasons.append(f"cooldown after {cfg.loss_streak} losses in a row")
    if len(st.positions) >= cfg.max_positions:
        reasons.append("maximum open positions")
    if any(p.symbol == symbol for p in st.positions):
        reasons.append("already holding this symbol (no averaging down)")
    cluster = st.correlated.get(symbol, {symbol})
    if sum(1 for p in st.positions if p.symbol in cluster) >= cfg.max_correlated_positions:
        reasons.append("correlated positions limit")
    new_risk = cfg.risk_per_trade * risk_mult * st.equity
    if sum(p.open_risk() for p in st.positions) + new_risk > cfg.max_open_risk * st.equity + 1e-9:
        reasons.append("aggregate open risk limit")
    if stop >= entry:
        reasons.append("stop not below entry")
    return reasons


def size(equity: float, cash: float, entry: float, stop: float, cfg: SpotRiskConfig, *,
         risk_mult: float = 1.0, slippage: float = 0.0, step: float = 0.0,
         min_notional: float = 0.0) -> tuple[float, str | None]:
    """Quantity from stop distance and account risk (never a fixed quantity).

    Risk counts the stop distance, both fees and slippage. Capped by the 20% allocation
    and by cash (no leverage or borrowing). Rounded DOWN to the step. Returns
    (qty, rejection reason)."""
    risk_budget = min(cfg.risk_per_trade * risk_mult, cfg.max_risk_per_trade) * equity
    per_unit = (entry - stop) + entry * cfg.taker_fee + stop * cfg.taker_fee \
        + (entry + stop) * slippage
    if per_unit <= 0:
        return 0.0, "invalid stop distance"
    qty = risk_budget / per_unit
    qty = min(qty, cfg.max_allocation * equity / entry, cash / (entry * (1 + cfg.taker_fee)))
    if step > 0:
        qty = math.floor(qty / step + 1e-9) * step
    if qty <= 0:
        return 0.0, "size rounds to zero"
    if qty * entry < min_notional:
        return 0.0, f"below minimum notional ({min_notional:g} USDT)"
    return qty, None


@dataclass(frozen=True)
class Fill:
    kind: str          # stop | tp1 | tp2 | emergency
    price: float       # before slippage
    qty: float


def manage_bar(p: Position, o: float, h: float, low: float, c: float, atr: float | None,
               cfg: SpotRiskConfig, *, slippage: float = 0.0) -> list[Fill]:
    """Exits for one bar, conservatively ordered: the stop is checked first (a gap through
    it fills at the open); then TP1 (partial) and TP2. After TP1 the stop moves to break
    even INCLUDING fees and slippage, and optionally trails by ATR. The stop never
    moves down."""
    fills: list[Fill] = []
    if o <= p.stop:
        return [Fill("stop", o, p.qty)]
    if low <= p.stop:
        return [Fill("stop", p.stop, p.qty)]
    if not p.tp1_done and h >= p.tp1:
        part = p.qty * cfg.tp1_fraction
        fills.append(Fill("tp1", max(p.tp1, o), part))
        remaining = p.qty - part
        # A stop exit is a market (taker) order: break even after both fees and slippage.
        be = p.entry * (1 + cfg.taker_fee + slippage) / (1 - cfg.taker_fee - slippage)
        if h >= p.tp2:
            fills.append(Fill("tp2", max(p.tp2, o), remaining))
            return fills
        p.tp1_done = True
        p.stop = max(p.stop, be)                     # never widen
        p.qty = remaining
    elif p.tp1_done and h >= p.tp2:
        return [Fill("tp2", max(p.tp2, o), p.qty)]
    p.highest = max(p.highest, c)
    if p.tp1_done and cfg.trailing_atr and atr and not math.isnan(atr):
        p.stop = max(p.stop, p.highest - cfg.trailing_atr * atr)
    return fills
