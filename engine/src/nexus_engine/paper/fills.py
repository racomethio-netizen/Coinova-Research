"""Pure, Decimal-only fill and position math for paper trading.

Market orders fill at the latest price with slippage against the trader; limit
orders fill only when marketable and never worse than their limit. Fees apply
to every fill. Positions use average cost; realized P/L is net of fees.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_HALF_EVEN, Decimal

from ..signals.risk import CostModel

MONEY = Decimal("1e-8")
QTY = Decimal("1e-12")


def _m(x: Decimal) -> Decimal:
    return x.quantize(MONEY, rounding=ROUND_HALF_EVEN)


class FillRejectedError(ValueError):
    """The order cannot be filled (reason is a short code stored on the order)."""


@dataclass(frozen=True)
class Position:
    quantity: Decimal = Decimal(0)
    cost_basis: Decimal = Decimal(0)
    realized_pnl: Decimal = Decimal(0)

    @property
    def avg_entry(self) -> Decimal | None:
        return _m(self.cost_basis / self.quantity) if self.quantity > 0 else None


@dataclass(frozen=True)
class Fill:
    side: str
    price: Decimal
    quantity: Decimal
    fee: Decimal
    realized_pnl: Decimal | None


def fill_price(side: str, order_type: str, latest: Decimal, limit: Decimal | None, costs: CostModel) -> Decimal | None:
    """Execution price, or None if a limit order is not marketable at `latest`."""
    s = costs.slippage_rate
    if side == "buy":
        px = latest * (1 + s)
        if order_type == "limit":
            assert limit is not None
            return _m(min(px, limit)) if latest <= limit else None
        return _m(px)
    px = latest * (1 - s)
    if order_type == "limit":
        assert limit is not None
        return _m(max(px, limit)) if latest >= limit else None
    return _m(px)


def apply_buy(cash: Decimal, pos: Position, price: Decimal, qty: Decimal, costs: CostModel) -> tuple[Decimal, Position, Fill]:
    notional = price * qty
    fee = _m(notional * costs.fee_rate)
    cost = _m(notional) + fee
    if cost > cash:
        raise FillRejectedError("insufficient_funds")
    new = Position(pos.quantity + qty, pos.cost_basis + cost, pos.realized_pnl)
    return cash - cost, new, Fill("buy", price, qty, fee, None)


def apply_sell(cash: Decimal, pos: Position, price: Decimal, qty: Decimal, costs: CostModel) -> tuple[Decimal, Position, Fill]:
    if qty > pos.quantity or pos.quantity <= 0:
        raise FillRejectedError("insufficient_position")
    notional = price * qty
    fee = _m(notional * costs.fee_rate)
    proceeds = _m(notional) - fee
    portion = pos.cost_basis if qty == pos.quantity else _m(pos.cost_basis * qty / pos.quantity)
    realized = proceeds - portion
    remaining = pos.quantity - qty
    new = Position(remaining, Decimal(0) if remaining == 0 else pos.cost_basis - portion, pos.realized_pnl + realized)
    return cash + proceeds, new, Fill("sell", price, qty, fee, realized)


def protection_trigger(latest: Decimal, stop_loss: Decimal | None, take_profit: Decimal | None) -> str | None:
    if stop_loss is not None and latest <= stop_loss:
        return "stop_loss"
    if take_profit is not None and latest >= take_profit:
        return "take_profit"
    return None
