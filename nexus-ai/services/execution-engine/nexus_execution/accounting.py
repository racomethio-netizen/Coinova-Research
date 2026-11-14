"""Position and ledger accounting from fills (pure Decimal functions).

Average-cost method. ``cost_basis`` includes buy fees, so realized P&L on a sell is
``proceeds - sell fee - cost_basis * (qty_sold / qty_held)``. Fees charged in the base
asset reduce the quantity received; fees in another asset are only recorded (their
quote value is unknown here and is never guessed).
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from decimal import Decimal

from nexus_common.decimals import ZERO


@dataclass(frozen=True)
class PositionState:
    quantity: Decimal = ZERO
    cost_basis: Decimal = ZERO      # quote spent incl. buy fees, for the held quantity
    realized_pnl: Decimal = ZERO
    fees_paid_quote: Decimal = ZERO

    @property
    def avg_entry_price(self) -> Decimal:
        return self.cost_basis / self.quantity if self.quantity > 0 else ZERO

    def unrealized(self, mark: Decimal) -> Decimal:
        return mark * self.quantity - self.cost_basis


@dataclass(frozen=True)
class FillEvent:
    side: str                       # buy | sell
    price: Decimal
    amount: Decimal
    fee: Decimal
    fee_asset: str | None


def fee_in_quote(f: FillEvent, *, base: str, quote: str) -> Decimal:
    if f.fee_asset in (None, quote):
        return f.fee
    if f.fee_asset == base:
        return f.fee * f.price
    return ZERO  # third-asset fee (e.g. BNB): recorded separately, not converted


def apply_fill(pos: PositionState, f: FillEvent, *, base: str, quote: str) -> PositionState:
    if f.amount <= 0 or f.price <= 0:
        raise ValueError("fill amount and price must be positive")
    fee_q = fee_in_quote(f, base=base, quote=quote)
    if f.side == "buy":
        received = f.amount - (f.fee if f.fee_asset == base else ZERO)
        spent = f.price * f.amount + (f.fee if f.fee_asset in (None, quote) else ZERO)
        return replace(pos, quantity=pos.quantity + received, cost_basis=pos.cost_basis + spent,
                       fees_paid_quote=pos.fees_paid_quote + fee_q)
    if f.side != "sell":
        raise ValueError(f"unknown side {f.side!r}")
    if f.amount > pos.quantity:
        raise ValueError(f"sell {f.amount} exceeds held {pos.quantity} (spot: no shorting)")
    share = f.amount / pos.quantity
    released = pos.cost_basis * share
    proceeds = f.price * f.amount - (f.fee if f.fee_asset in (None, quote) else ZERO)
    if f.fee_asset == base:
        proceeds -= f.fee * f.price  # base-asset fee reduces what is effectively sold
    remaining = pos.quantity - f.amount
    return PositionState(
        quantity=remaining,
        cost_basis=ZERO if remaining == 0 else pos.cost_basis - released,
        realized_pnl=pos.realized_pnl + proceeds - released,
        fees_paid_quote=pos.fees_paid_quote + fee_q)


@dataclass(frozen=True)
class Ledger:
    """Paper cash ledger per asset (free/locked) for one venue."""

    free: dict[str, Decimal]
    locked: dict[str, Decimal]

    def lock(self, asset: str, amount: Decimal) -> Ledger:
        if amount > self.free.get(asset, ZERO):
            raise ValueError(f"insufficient free {asset}: need {amount}")
        return Ledger({**self.free, asset: self.free.get(asset, ZERO) - amount},
                      {**self.locked, asset: self.locked.get(asset, ZERO) + amount})

    def unlock(self, asset: str, amount: Decimal) -> Ledger:
        amount = min(amount, self.locked.get(asset, ZERO))
        return Ledger({**self.free, asset: self.free.get(asset, ZERO) + amount},
                      {**self.locked, asset: self.locked.get(asset, ZERO) - amount})

    def settle_fill(self, f: FillEvent, *, base: str, quote: str,
                    from_locked: bool) -> Ledger:
        """Apply a fill. ``from_locked``: the paying asset was reserved at submission."""
        free, locked = dict(self.free), dict(self.locked)
        pay_asset, pay = ((quote, f.price * f.amount) if f.side == "buy" else (base, f.amount))
        get_asset, get = ((base, f.amount) if f.side == "buy" else (quote, f.price * f.amount))
        src = locked if from_locked else free
        if src.get(pay_asset, ZERO) < pay:
            raise ValueError(f"insufficient {'locked' if from_locked else 'free'} {pay_asset}")
        src[pay_asset] = src.get(pay_asset, ZERO) - pay
        free[get_asset] = free.get(get_asset, ZERO) + get
        fee_asset = f.fee_asset or quote
        bucket = free if not (from_locked and fee_asset == pay_asset) else locked
        if bucket.get(fee_asset, ZERO) < f.fee:
            bucket = free
        if bucket.get(fee_asset, ZERO) < f.fee:
            raise ValueError(f"insufficient {fee_asset} for fee")
        bucket[fee_asset] = bucket.get(fee_asset, ZERO) - f.fee
        return Ledger(free, locked)
