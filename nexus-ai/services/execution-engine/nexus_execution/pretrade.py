"""Pre-trade checks in the order manager, modelled on NautilusTrader's RiskEngine
(crates/risk/src/engine): the last gate between ANY strategy and the broker, independent
of the strategy code that built the order.

* trading state   REDUCING (kill switch engaged, owner floor NO_NEW_TRADES or stricter, or
                  the state unreadable) refuses orders that open or add to a position.
                  Exits always pass: refusing an exit can only increase risk.
* max notional    a buy may not exceed a fraction of the portfolio's quote balance
                  (fat-finger guard; the ledger lock separately refuses unaffordable buys).
* price band      an entry's limit price more than ``max_price_deviation`` from the
                  expected price (the market reference the caller saw) is refused.
* submit rate     at most ``max_entries_per_minute`` entry orders per portfolio.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from nexus_exchange.models import Side


class PreTradeDenied(Exception):
    """The order was refused before reaching the broker."""


@dataclass(frozen=True)
class PreTradeLimits:
    max_order_fraction: Decimal = Decimal("0.5")     # of the quote balance (free + locked)
    max_price_deviation: Decimal = Decimal("0.05")   # 5% from the expected price
    max_entries_per_minute: int = 10


REDUCING_FLOORS = frozenset({"NO_NEW_TRADES", "CLOSE_ONLY", "EMERGENCY_STOP"})


def check(*, purpose: str, side: Side, amount: Decimal, price: Decimal,
          expected_price: Decimal | None, quote_total: Decimal, entries_last_minute: int,
          kill_switch: bool | None, owner_floor: str | None,
          limits: PreTradeLimits) -> list[str]:
    """Reasons to refuse (empty = allowed). ``kill_switch`` None means unreadable."""
    reasons: list[str] = []
    if purpose != "entry":
        return reasons
    if expected_price is not None and expected_price > 0:
        dev = abs(price - expected_price) / expected_price
        if dev > limits.max_price_deviation:
            reasons.append(f"limit price {price} is {dev:.1%} from the expected "
                           f"{expected_price} (max {limits.max_price_deviation:.0%})")
    if kill_switch is None:
        reasons.append("REDUCING: kill switch state unreadable")
    elif kill_switch:
        reasons.append("REDUCING: kill switch engaged")
    if owner_floor in REDUCING_FLOORS:
        reasons.append(f"REDUCING: owner risk floor {owner_floor}")
    if side is Side.BUY:
        notional = amount * price
        cap = quote_total * limits.max_order_fraction
        if notional > cap:
            reasons.append(f"order notional {notional:.2f} above {limits.max_order_fraction:.0%} "
                           f"of the quote balance ({cap:.2f})")
    if entries_last_minute >= limits.max_entries_per_minute:
        reasons.append(f"entry submit rate limit ({limits.max_entries_per_minute}/min)")
    return reasons
