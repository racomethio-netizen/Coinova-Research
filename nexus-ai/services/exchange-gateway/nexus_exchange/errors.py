"""Normalized exchange errors. Adapters translate library/native errors into these.

The critical distinction for execution safety:

* :class:`OrderRejected` / :class:`InsufficientFunds` / :class:`InvalidOrder`:
  the exchange definitively did NOT accept the order.
* :class:`OrderStateUnknown`: we cannot tell whether the order exists (timeout,
  dropped connection, 5xx after sending). The caller MUST reconcile using the
  client order ID and MUST NOT resubmit blindly.
"""

from __future__ import annotations


class ExchangeError(Exception):
    """Base class. ``retryable`` means a *read* may be retried; never a submission."""

    retryable: bool = False

    def __init__(self, message: str, *, exchange: str | None = None) -> None:
        super().__init__(message)
        self.exchange = exchange


class ExchangeUnavailable(ExchangeError):
    retryable = True


class RateLimited(ExchangeUnavailable):
    pass


class AuthenticationFailed(ExchangeError):
    pass


class PermissionDenied(ExchangeError):
    pass


class UnsupportedFeature(ExchangeError):
    """The exchange (or our adapter for it) does not support the requested operation."""


class OrderRejected(ExchangeError):
    """The exchange definitively refused the order."""


class InsufficientFunds(OrderRejected):
    pass


class InvalidOrder(OrderRejected):
    """Includes local pre-validation failures (precision, minimums, spot-only rules)."""


class OrderNotFound(ExchangeError):
    pass


class OrderStateUnknown(ExchangeError):
    """Submission outcome is ambiguous. Reconcile by client order ID; do not resubmit."""

    def __init__(self, message: str, *, client_order_id: str, exchange: str | None = None) -> None:
        super().__init__(message, exchange=exchange)
        self.client_order_id = client_order_id
