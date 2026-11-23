"""Market precision / limit validation. Pure functions, no I/O.

Rounding is always in the conservative direction:

* amount: rounded DOWN to the step (never trade more than risk sizing allowed);
* buy limit price: rounded DOWN to the tick (never pay more than intended);
* sell limit price: rounded UP to the tick (never sell for less than intended).
"""

from __future__ import annotations

from dataclasses import replace
from decimal import Decimal

from nexus_common.decimals import ceil_to_step, floor_to_step, is_multiple_of

from nexus_exchange.errors import InvalidOrder
from nexus_exchange.models import MarketRules, OrderRequest, OrderType, Side


def round_amount(amount: Decimal, rules: MarketRules) -> Decimal:
    return floor_to_step(amount, rules.amount_step)


def round_price(price: Decimal, side: Side, rules: MarketRules) -> Decimal:
    if side is Side.BUY:
        return floor_to_step(price, rules.price_tick)
    return ceil_to_step(price, rules.price_tick)


def normalize_order(request: OrderRequest, rules: MarketRules) -> OrderRequest:
    """Round an order to exchange precision, then validate it. Raises InvalidOrder."""
    amount = round_amount(request.amount, rules)
    price = request.price
    if price is not None:
        price = round_price(price, request.side, rules)
    # Trigger rounded the same conservative way: a protective sell stop never triggers
    # lower than intended, a buy stop never higher.
    stop = request.stop_price
    if stop is not None:
        stop = round_price(stop, request.side, rules)
        if stop <= 0:
            raise InvalidOrder(f"stop price {request.stop_price} rounds to zero")
    if amount <= 0:
        raise InvalidOrder(f"amount {request.amount} rounds to zero at step {rules.amount_step}")
    if price is not None and price <= 0:
        raise InvalidOrder(f"price {request.price} rounds to zero at tick {rules.price_tick}")
    try:
        normalized = replace(request, amount=amount, price=price, stop_price=stop)
    except ValueError as exc:   # rounding moved the limit across the stop
        raise InvalidOrder(str(exc)) from exc
    validate_order(normalized, rules)
    return normalized


def validate_order(
    request: OrderRequest, rules: MarketRules, *, reference_price: Decimal | None = None
) -> None:
    """Validate an already-rounded order against market rules. Raises InvalidOrder."""
    if request.symbol != rules.symbol:
        raise InvalidOrder(f"order symbol {request.symbol} does not match market {rules.symbol}")
    if not rules.active:
        raise InvalidOrder(f"market {rules.symbol} is not active")
    if not is_multiple_of(request.amount, rules.amount_step):
        raise InvalidOrder(f"amount {request.amount} is not a multiple of {rules.amount_step}")
    if request.price is not None and not is_multiple_of(request.price, rules.price_tick):
        raise InvalidOrder(f"price {request.price} is not a multiple of {rules.price_tick}")
    if request.stop_price is not None and not is_multiple_of(request.stop_price,
                                                             rules.price_tick):
        raise InvalidOrder(f"stop price {request.stop_price} is not a multiple of "
                           f"{rules.price_tick}")
    if rules.min_amount is not None and request.amount < rules.min_amount:
        raise InvalidOrder(f"amount {request.amount} below minimum {rules.min_amount}")
    if rules.max_amount is not None and request.amount > rules.max_amount:
        raise InvalidOrder(f"amount {request.amount} above maximum {rules.max_amount}")

    price = request.price if request.order_type is not OrderType.MARKET else reference_price
    if rules.min_notional is not None:
        if price is None:
            raise InvalidOrder("cannot check minimum notional without a price")
        notional = request.amount * price
        if notional < rules.min_notional:
            raise InvalidOrder(f"notional {notional} below minimum {rules.min_notional}")
