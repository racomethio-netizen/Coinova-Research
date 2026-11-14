"""Reconnecting WebSocket consumer with staleness detection.

``watch`` is any coroutine factory returning the next update (e.g. ccxt.pro's
``watch_order_book``). Failures and silences longer than ``stale_after`` trigger a
reconnect with exponential backoff and jitter. State changes are reported so the
health monitor/execution can stop using stale prices.
"""

from __future__ import annotations

import asyncio
import random
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any, Generic, TypeVar

from nexus_common.logging import get_logger
from nexus_common.timeutil import utcnow

T = TypeVar("T")
log = get_logger("nexus.market.stream")


@dataclass
class StreamStats:
    updates: int = 0
    reconnects: int = 0
    consecutive_failures: int = 0
    connected: bool = False
    last_error: str | None = None


class ReconnectingStream(Generic[T]):
    def __init__(
        self, name: str, watch: Callable[[], Awaitable[T]],
        on_update: Callable[[T], Awaitable[None]], *,
        on_state: Callable[[str, bool, str | None], Awaitable[None]] | None = None,
        reset: Callable[[], Awaitable[None]] | None = None,
        stale_after: float = 30.0, backoff_min: float = 1.0, backoff_max: float = 60.0,
        sleep: Callable[[float], Awaitable[Any]] = asyncio.sleep,
    ) -> None:
        self.name = name
        self._watch, self._on_update, self._on_state, self._reset = watch, on_update, on_state, reset
        self.stale_after, self.backoff_min, self.backoff_max = stale_after, backoff_min, backoff_max
        self._sleep = sleep
        self.stats = StreamStats()

    def backoff(self, failures: int) -> float:
        base = min(self.backoff_max, self.backoff_min * 2 ** max(0, failures - 1))
        return float(base * (0.5 + random.random() / 2))  # noqa: S311 (jitter, not crypto)

    async def _set_state(self, connected: bool, error: str | None) -> None:
        if connected == self.stats.connected:
            return
        self.stats.connected = connected
        log.info("stream_state", stream=self.name, connected=connected, error=error,
                 at=utcnow().isoformat())
        if self._on_state:
            await self._on_state(self.name, connected, error)

    async def run(self, stop: asyncio.Event) -> None:
        while not stop.is_set():
            try:
                update = await asyncio.wait_for(self._watch(), timeout=self.stale_after)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                err = ("stale: no update in "
                       f"{self.stale_after:.0f}s" if isinstance(exc, TimeoutError)
                       else f"{type(exc).__name__}: {str(exc)[:200]}")
                self.stats.consecutive_failures += 1
                self.stats.last_error = err
                if not self.stats.connected:
                    # Already down (or never connected): still make the failure visible.
                    log.warning("stream_failure", stream=self.name, error=err,
                                failures=self.stats.consecutive_failures)
                await self._set_state(False, err)
                if self._reset is not None:
                    try:
                        await self._reset()
                    except Exception as reset_exc:  # reset failure is non-fatal; retry later
                        log.warning("stream_reset_failed", stream=self.name,
                                    error=type(reset_exc).__name__)
                self.stats.reconnects += 1
                await self._sleep(self.backoff(self.stats.consecutive_failures))
                continue
            self.stats.consecutive_failures = 0
            self.stats.updates += 1
            await self._set_state(True, None)
            await self._on_update(update)
        await self._set_state(False, "stopped")
