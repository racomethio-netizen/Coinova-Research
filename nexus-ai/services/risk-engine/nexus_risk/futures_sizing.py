"""USDT-M futures position sizing (isolated margin, long and short).

Size comes from the risk budget, never from leverage:

    qty = equity * risk / (stop distance + round-trip fees and slippage)

Leverage only decides how much margin the position consumes and how close its
liquidation price sits. Four checks can refuse a trade:

1. The stop must be on the protective side of entry.
2. Liquidation must be at least ``liquidation_buffer`` times further away than the stop,
   so a stop-out happens before liquidation. A higher leverage tier can therefore be
   refused; the engine never raises leverage to make a trade fit.
3. Margin and total futures exposure must stay within the account's caps.
4. The order must meet the exchange's minimum quantity and notional.

Liquidation uses the standard isolated-margin approximation, ignoring fees and funding:
    long:  liq = entry * (1 - 1/L + MMR)     short: liq = entry * (1 + 1/L - MMR)
The exchange-reported liquidation price (when available) must override this estimate.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Literal

from nexus_common.decimals import ZERO, floor_to_step

Side = Literal["long", "short"]
ALLOWED_LEVERAGE = (2, 5, 10, 20)
ONE = Decimal(1)


@dataclass(frozen=True)
class FuturesSizingInput:
    side: Side
    equity: Decimal                  # wallet balance plus unrealised P&L
    available_margin: Decimal        # free margin, not already locked
    entry: Decimal
    stop: Decimal
    leverage: int
    risk_fraction: Decimal           # e.g. 0.01 for 1%; never above the configured ceiling
    fee_rate: Decimal                # taker fee per side
    slippage_rate: Decimal           # expected adverse slippage per side
    maintenance_margin_rate: Decimal # exchange tier MMR, e.g. 0.004
    funding_rate_per_8h: Decimal     # signed rate, positive means longs pay
    expected_hold_hours: Decimal
    total_margin_in_use: Decimal     # margin of open positions
    asset_margin_in_use: Decimal     # margin of open positions in this asset
    max_total_margin_fraction: Decimal = Decimal("0.5")
    max_asset_margin_fraction: Decimal = Decimal("0.25")
    liquidation_buffer: Decimal = Decimal("1.5")
    qty_step: Decimal = Decimal("0.001")
    min_qty: Decimal | None = None
    min_notional: Decimal | None = None


@dataclass(frozen=True)
class FuturesSizing:
    approved: bool
    reason: str | None
    quantity: Decimal = ZERO
    notional: Decimal = ZERO
    margin: Decimal = ZERO
    leverage: int = 0
    liquidation_price: Decimal | None = None
    liquidation_distance: Decimal | None = None
    stop_distance: Decimal | None = None
    risk_amount: Decimal = ZERO
    funding_estimate: Decimal = ZERO
    binding: str = "none"
    notes: tuple[str, ...] = field(default_factory=tuple)


def liquidation_price(side: Side, entry: Decimal, leverage: int, mmr: Decimal) -> Decimal:
    inv = ONE / Decimal(leverage)
    if side == "long":
        return entry * (ONE - inv + mmr)
    return entry * (ONE + inv - mmr)


def _reject(reason: str) -> FuturesSizing:
    return FuturesSizing(approved=False, reason=reason)


def size_futures_position(i: FuturesSizingInput) -> FuturesSizing:
    if i.leverage not in ALLOWED_LEVERAGE:
        return _reject(f"leverage {i.leverage}x is not a selectable tier {ALLOWED_LEVERAGE}")
    if i.equity <= 0 or i.entry <= 0 or i.stop <= 0:
        return _reject("non-positive equity or prices")
    if i.risk_fraction <= 0 or i.risk_fraction > Decimal("0.01"):
        return _reject("risk per trade must be > 0 and at most 1%")
    if i.side == "long" and i.stop >= i.entry:
        return _reject("long stop must be below entry")
    if i.side == "short" and i.stop <= i.entry:
        return _reject("short stop must be above entry")
    if i.maintenance_margin_rate <= 0 or i.maintenance_margin_rate >= ONE / i.leverage:
        return _reject("maintenance margin rate invalid for this leverage")

    stop_dist = abs(i.entry - i.stop)
    stop_frac = stop_dist / i.entry
    liq = liquidation_price(i.side, i.entry, i.leverage, i.maintenance_margin_rate)
    liq_dist = abs(i.entry - liq) / i.entry
    if liq_dist < i.liquidation_buffer * stop_frac:
        return FuturesSizing(
            approved=False,
            reason=(f"liquidation at {liq:.2f} is too close to the stop at {i.stop} for {i.leverage}x: "
                    "a lower leverage tier is needed; leverage is never raised automatically"),
            liquidation_price=liq, liquidation_distance=liq_dist, stop_distance=stop_frac)

    cost_rate = i.fee_rate + i.slippage_rate
    per_unit_loss = stop_dist + (i.entry + i.stop) * cost_rate
    risk_amount = i.equity * i.risk_fraction
    qty_risk = risk_amount / per_unit_loss

    # Margin needed for a position of qty is qty * entry / leverage.
    total_room = max(ZERO, i.equity * i.max_total_margin_fraction - i.total_margin_in_use)
    asset_room = max(ZERO, i.equity * i.max_asset_margin_fraction - i.asset_margin_in_use)
    margin_room = min(total_room, asset_room, i.available_margin)
    qty_margin = margin_room * Decimal(i.leverage) / i.entry

    binding = "risk"
    qty = qty_risk
    if qty_margin < qty:
        qty, binding = qty_margin, "margin"
    qty = floor_to_step(qty, i.qty_step)
    if qty <= 0:
        return FuturesSizing(approved=False, reason=f"no margin room (binding: {binding})",
                             liquidation_price=liq, liquidation_distance=liq_dist,
                             stop_distance=stop_frac)
    notional = qty * i.entry
    if i.min_qty is not None and qty < i.min_qty:
        return _reject(f"quantity {qty} below exchange minimum {i.min_qty}")
    if i.min_notional is not None and notional < i.min_notional:
        return _reject(f"notional {notional} below exchange minimum {i.min_notional}")

    funding = notional * i.funding_rate_per_8h * (i.expected_hold_hours / Decimal(8))
    funding_cost = max(ZERO, funding if i.side == "long" else -funding)
    notes: list[str] = []
    if funding_cost > risk_amount * Decimal("0.5"):
        return FuturesSizing(approved=False, reason=(
            f"expected funding cost {funding_cost:.2f} exceeds half the risk budget"),
            liquidation_price=liq, liquidation_distance=liq_dist, stop_distance=stop_frac)
    if i.funding_rate_per_8h != ZERO:
        notes.append(f"expected funding {funding_cost:.4f} over {i.expected_hold_hours}h")

    return FuturesSizing(
        approved=True, reason=None, quantity=qty, notional=notional,
        margin=notional / Decimal(i.leverage), leverage=i.leverage,
        liquidation_price=liq, liquidation_distance=liq_dist, stop_distance=stop_frac,
        risk_amount=qty * per_unit_loss, funding_estimate=funding_cost, binding=binding,
        notes=tuple(notes))
