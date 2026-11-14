"""Dynamic position sizing (Decimal).

Quantity is chosen so that a stop-out costs at most ``risk_per_trade`` of equity
INCLUDING entry/exit fees and assumed slippage, then capped by remaining portfolio
exposure, per-asset concentration and available cash, and floored to the exchange
amount step. If the result is below exchange minimums the trade is rejected (we never
round up into more risk).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal

from nexus_common.decimals import ZERO, floor_to_step


@dataclass(frozen=True)
class SizingInput:
    equity: Decimal
    cash_available: Decimal          # free quote on the chosen venue minus reservations
    entry_price: Decimal
    stop_price: Decimal
    risk_per_trade: Decimal          # fraction of equity, e.g. 0.0025
    fee_rate: Decimal                # taker fee fraction per side (conservative)
    slippage_rate: Decimal           # assumed adverse slippage fraction per side
    exposure_now: Decimal            # quote value of open positions + pending buys
    max_exposure: Decimal            # fraction of equity, e.g. 0.50
    asset_exposure_now: Decimal      # quote value already in this base asset
    max_asset_concentration: Decimal
    amount_step: Decimal
    min_amount: Decimal | None = None
    min_notional: Decimal | None = None


@dataclass(frozen=True)
class SizingResult:
    quantity: Decimal
    notional: Decimal
    risk_amount: Decimal             # worst-case loss at the stop incl. costs
    binding_constraint: str
    rejected_reason: str | None = None
    notes: tuple[str, ...] = field(default_factory=tuple)

    @property
    def approved(self) -> bool:
        return self.rejected_reason is None and self.quantity > 0


def _reject(reason: str) -> SizingResult:
    return SizingResult(ZERO, ZERO, ZERO, "none", reason)


def size_position(i: SizingInput) -> SizingResult:
    if i.equity <= 0:
        return _reject("non-positive equity")
    if i.entry_price <= 0 or i.stop_price <= 0:
        return _reject("invalid prices")
    if i.stop_price >= i.entry_price:
        return _reject("stop must be below entry for a long")
    cost_rate = i.fee_rate + i.slippage_rate
    # Loss per unit if stopped: price distance + costs on both legs.
    per_unit_loss = (i.entry_price - i.stop_price) + i.entry_price * cost_rate \
        + i.stop_price * cost_rate
    budget = i.equity * i.risk_per_trade
    candidates: dict[str, Decimal] = {"risk": budget / per_unit_loss}

    effective_entry = i.entry_price * (1 + cost_rate)
    exposure_room = i.equity * i.max_exposure - i.exposure_now
    candidates["exposure"] = max(ZERO, exposure_room) / effective_entry
    asset_room = i.equity * i.max_asset_concentration - i.asset_exposure_now
    candidates["concentration"] = max(ZERO, asset_room) / effective_entry
    candidates["cash"] = max(ZERO, i.cash_available) / effective_entry

    binding = min(candidates, key=lambda k: candidates[k])
    qty = floor_to_step(candidates[binding], i.amount_step)
    if qty <= 0:
        return _reject(f"no room: {binding} constraint leaves zero size")
    notional = qty * i.entry_price
    if i.min_amount is not None and qty < i.min_amount:
        return _reject(f"size {qty} below exchange minimum {i.min_amount} ({binding}-bound)")
    if i.min_notional is not None and notional < i.min_notional:
        return _reject(f"notional {notional} below exchange minimum {i.min_notional}")
    return SizingResult(qty, notional, qty * per_unit_loss, binding)
