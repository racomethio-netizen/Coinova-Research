"""Shared, conservative execution model (ADR-006). Used by the outcome resolver now,
and by the backtester and paper trading in later phases, so results agree.

Rules (all documented in docs/nexus/SIGNAL_ENGINE.md §7-§8):
- Only bars that open at or after publication are used.
- Entry: BUY when a bar trades at or below entry_high, filled at min(open, entry_high);
  SELL when a bar trades at or above entry_low, filled at max(open, entry_low).
  If that fill is already beyond the stop, the signal is `invalidated`.
- On the entry bar only the stop is checked (we cannot know whether the target
  traded after the fill).
- Afterwards, per bar: a gap through the stop fills at the open; a stop touch fills
  at the stop; **if stop and target are both touched in one bar, the stop wins**;
  a gap through the target fills at the open; a target touch fills at the target.
- No entry within `entry_bars` -> `expired_no_entry`. No exit within `horizon_bars`
  after the entry bar -> exit at the last bar's close (`expired_closed`).
- Fees and slippage are applied on both sides. Bars must be contiguous: on a
  gap, resolution stops and waits (never assumes prices).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal

from ..timeframes import tf_delta
from .risk import CostModel, q_price

RESOLVER_VERSION = "1.0.0"
RET_Q = Decimal("1e-8")
R_Q = Decimal("1e-4")


@dataclass(frozen=True)
class Bar:
    open_time: datetime
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal


@dataclass(frozen=True)
class Plan:
    direction: str
    entry_low: Decimal
    entry_high: Decimal
    stop: Decimal
    target: Decimal
    entry_bars: int
    horizon_bars: int
    costs: CostModel = field(default_factory=CostModel)


@dataclass(frozen=True)
class Event:
    type: str
    occurred_at: datetime
    price: Decimal | None


@dataclass(frozen=True)
class Resolution:
    events: tuple[Event, ...]
    outcome: str | None = None  # None: still open
    entry_price: Decimal | None = None
    exit_price: Decimal | None = None
    net_return: Decimal | None = None
    r_multiple: Decimal | None = None
    closed_at: datetime | None = None


def unit_basis_and_pnl(direction: str, entry: Decimal, exit_: Decimal, costs: CostModel) -> tuple[Decimal, Decimal]:
    """Capital per unit at entry (incl. costs) and net P&L per unit (incl. costs on both sides)."""
    one, f, s = Decimal(1), costs.fee_rate, costs.slippage_rate
    if direction == "BUY":
        basis = entry * (one + s) * (one + f)
        return basis, exit_ * (one - s) * (one - f) - basis
    basis = entry * (one - s) * (one - f)
    return basis, basis - exit_ * (one + s) * (one + f)


def trade_result(direction: str, entry: Decimal, exit_: Decimal, stop: Decimal, costs: CostModel) -> tuple[Decimal, Decimal]:
    """(net return, R multiple) per unit after fees and slippage on both sides."""
    one, f, s = Decimal(1), costs.fee_rate, costs.slippage_rate
    if direction == "BUY":
        basis = entry * (one + s) * (one + f)
        pnl = exit_ * (one - s) * (one - f) - basis
        risk = basis - stop * (one - s) * (one - f)
    else:
        basis = entry * (one - s) * (one - f)
        pnl = basis - exit_ * (one + s) * (one + f)
        risk = stop * (one + s) * (one + f) - basis
    r = (pnl / risk) if risk > 0 else Decimal(0)
    return (pnl / basis).quantize(RET_Q), r.quantize(R_Q)


def resolve(plan: Plan, bars: list[Bar], tf: str, first_open: datetime) -> Resolution:
    """Resolve a signal from bars starting at `first_open` (the first bar opening after publication)."""
    step = tf_delta(tf)
    buy = plan.direction == "BUY"
    events: list[Event] = []
    expected = first_open
    entry: Decimal | None = None
    entry_idx = -1

    def close(outcome: str, bar: Bar, exit_px: Decimal, ev: str) -> Resolution:
        assert entry is not None
        events.append(Event(ev, bar.open_time, exit_px))
        net, r = trade_result(plan.direction, entry, exit_px, plan.stop, plan.costs)
        return Resolution(tuple(events), outcome, entry, q_price(exit_px), net, r, bar.open_time + step)

    for i, bar in enumerate(bars):
        if bar.open_time != expected:
            break  # missing candle: wait for a backfill, never assume prices
        expected += step
        if entry is None:
            if i >= plan.entry_bars:
                last = bars[plan.entry_bars - 1]
                events.append(Event("expired_no_entry", last.open_time, None))
                return Resolution(tuple(events), "expired_no_entry", closed_at=last.open_time + step)
            touched = bar.low <= plan.entry_high if buy else bar.high >= plan.entry_low
            if not touched:
                continue
            fill = min(bar.open, plan.entry_high) if buy else max(bar.open, plan.entry_low)
            if (buy and fill <= plan.stop) or (not buy and fill >= plan.stop):
                events.append(Event("invalidated", bar.open_time, q_price(fill)))
                return Resolution(tuple(events), "invalidated", closed_at=bar.open_time + step)
            entry, entry_idx = q_price(fill), i
            events.append(Event("entered", bar.open_time, entry))
            if (buy and bar.low <= plan.stop) or (not buy and bar.high >= plan.stop):
                return close("loss", bar, plan.stop, "sl_hit")
            continue
        # In position.
        if buy:
            if bar.open <= plan.stop:
                return close("loss", bar, bar.open, "sl_hit")
            if bar.low <= plan.stop:
                return close("loss", bar, plan.stop, "sl_hit")
            if bar.open >= plan.target:
                return close("win", bar, bar.open, "tp1_hit")
            if bar.high >= plan.target:
                return close("win", bar, plan.target, "tp1_hit")
        else:
            if bar.open >= plan.stop:
                return close("loss", bar, bar.open, "sl_hit")
            if bar.high >= plan.stop:
                return close("loss", bar, plan.stop, "sl_hit")
            if bar.open <= plan.target:
                return close("win", bar, bar.open, "tp1_hit")
            if bar.low <= plan.target:
                return close("win", bar, plan.target, "tp1_hit")
        if i - entry_idx >= plan.horizon_bars:
            return close("expired_closed", bar, bar.close, "horizon_reached")
    return Resolution(tuple(events), entry_price=entry)  # still open (entry_price set once entered)
