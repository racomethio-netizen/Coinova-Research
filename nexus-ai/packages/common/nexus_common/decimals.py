"""Decimal helpers. All money/quantity math in NEXUS AI uses ``Decimal``, never float."""

from __future__ import annotations

from decimal import ROUND_CEILING, ROUND_FLOOR, Decimal, InvalidOperation, localcontext

ZERO = Decimal(0)


def to_decimal(value: object, *, field: str = "value") -> Decimal:
    """Convert exchange/user input to Decimal without going through binary float.

    Floats (as returned by some client libraries) are converted via ``str`` so that
    ``0.1`` becomes ``Decimal('0.1')`` rather than its binary expansion.
    """
    if isinstance(value, Decimal):
        result = value
    elif isinstance(value, bool):
        raise TypeError(f"{field}: bool is not a number")
    elif isinstance(value, int | str):
        try:
            result = Decimal(str(value).strip())
        except InvalidOperation as exc:
            raise ValueError(f"{field}: not a number: {value!r}") from exc
    elif isinstance(value, float):
        result = Decimal(repr(value))
    else:
        raise TypeError(f"{field}: unsupported type {type(value).__name__}")
    if not result.is_finite():
        raise ValueError(f"{field}: must be finite, got {value!r}")
    return result


def to_decimal_or_none(value: object, *, field: str = "value") -> Decimal | None:
    return None if value is None else to_decimal(value, field=field)


def floor_to_step(value: Decimal, step: Decimal) -> Decimal:
    """Largest multiple of ``step`` that is <= ``value``."""
    if step <= 0:
        raise ValueError("step must be > 0")
    with localcontext() as ctx:
        ctx.prec = 50
        units = (value / step).to_integral_value(rounding=ROUND_FLOOR)
        return (units * step).quantize(step)


def ceil_to_step(value: Decimal, step: Decimal) -> Decimal:
    """Smallest multiple of ``step`` that is >= ``value``."""
    if step <= 0:
        raise ValueError("step must be > 0")
    with localcontext() as ctx:
        ctx.prec = 50
        units = (value / step).to_integral_value(rounding=ROUND_CEILING)
        return (units * step).quantize(step)


def is_multiple_of(value: Decimal, step: Decimal) -> bool:
    if step <= 0:
        raise ValueError("step must be > 0")
    with localcontext() as ctx:
        ctx.prec = 50
        return (value % step) == 0
