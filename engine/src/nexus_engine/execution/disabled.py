"""The only ExecutionAdapter that exists. It cannot place orders.

Future activation requires every item in docs/nexus/COMPLIANCE.md §3 and new,
reviewed code. There is no credential storage and no exchange order endpoint
anywhere in this package (enforced by tests/test_no_execution.py).
"""

from __future__ import annotations

from typing import Protocol


class LiveExecutionDisabledError(RuntimeError):
    pass


class ExecutionAdapter(Protocol):
    def place_order(self, *args: object, **kwargs: object) -> None: ...


class DisabledExecutionAdapter:
    def place_order(self, *args: object, **kwargs: object) -> None:
        raise LiveExecutionDisabledError("Live execution is disabled (LIVE_EXECUTION_ENABLED=false) and not implemented.")
