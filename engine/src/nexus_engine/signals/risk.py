"""Costs and risk gates (docs/nexus/SIGNAL_ENGINE.md §4). Decimal throughout."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from decimal import ROUND_HALF_EVEN, Decimal

PRICE_Q = Decimal("1e-8")
RATIO_Q = Decimal("1e-4")


def q_price(x: float | Decimal) -> Decimal:
    return Decimal(repr(x) if isinstance(x, float) else x).quantize(PRICE_Q, rounding=ROUND_HALF_EVEN)


def q_ratio(x: Decimal) -> Decimal:
    return x.quantize(RATIO_Q, rounding=ROUND_HALF_EVEN)


@dataclass(frozen=True)
class CostModel:
    """Visible assumptions: taker fee and slippage per side (defaults; configurable)."""

    fee_rate: Decimal = Decimal("0.001")
    slippage_rate: Decimal = Decimal("0.0005")

    def as_dict(self) -> dict[str, str]:
        return {k: str(v) for k, v in asdict(self).items()}


def net_reward_risk(direction: str, entry: Decimal, stop: Decimal, target: Decimal, costs: CostModel) -> Decimal:
    """Reward/risk per unit after fees and slippage on both sides of the trade."""
    f, s = costs.fee_rate, costs.slippage_rate
    one = Decimal(1)
    if direction == "BUY":
        cost = entry * (one + s) * (one + f)
        reward = target * (one - s) * (one - f) - cost
        risk = cost - stop * (one - s) * (one - f)
    elif direction == "SELL":
        proceeds = entry * (one - s) * (one - f)
        reward = proceeds - target * (one + s) * (one + f)
        risk = stop * (one + s) * (one + f) - proceeds
    else:
        raise ValueError("direction must be BUY or SELL")
    if risk <= 0:
        return Decimal(0)
    return reward / risk
