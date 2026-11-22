"""Exchange health monitoring: records transitions, publishes current state to Redis.

The health key (used by the trader: an unhealthy venue blocks entries) changes at once.
Telegram alerts are debounced so a flapping connection produces one clear message instead
of a stream: "down" is sent only after the venue has been down ``down_alert_after``
seconds, at most once per ``alert_cooldown``; "recovered" only after ``up_alert_after``
seconds of stable health, with a summary of the interruptions. Every transition is still
written to system events (System page), so nothing is hidden.

Health can be determined passively (``passive``: e.g. live stream freshness and the
rate-limit gate) so no ping requests are spent; the active check is the fallback.
"""

from __future__ import annotations

import asyncio
import json
import time
from collections.abc import Awaitable, Callable
from typing import Any

from nexus_common import events
from nexus_common.logging import get_logger
from nexus_db import repo, transaction
from nexus_exchange.base import ExchangeAdapter
from nexus_exchange.models import HealthStatus
from sqlalchemy.orm import Session, sessionmaker

log = get_logger("nexus.market.health")


class HealthMonitor:
    def __init__(self, adapters: list[ExchangeAdapter], factory: sessionmaker[Session],
                 redis: Any, *, failures_before_down: int = 2,
                 passive: Callable[[str], Awaitable[HealthStatus | None]] | None = None,
                 down_alert_after: float = 120.0, up_alert_after: float = 300.0,
                 alert_cooldown: float = 1800.0,
                 clock: Callable[[], float] = time.monotonic) -> None:
        self._adapters = adapters
        self._factory = factory
        self._redis = redis
        self._failures: dict[str, int] = {a.code: 0 for a in adapters}
        self.state: dict[str, bool | None] = {a.code: None for a in adapters}
        self._threshold = failures_before_down
        self._passive = passive
        self._down_after, self._up_after = down_alert_after, up_alert_after
        self._cooldown, self._clock = alert_cooldown, clock
        self._alert: dict[str, dict[str, Any]] = {
            a.code: {"down_since": None, "up_since": None, "alerted": False,
                     "last_alert": None, "interruptions": 0, "down_seconds": 0.0,
                     "detail": None} for a in adapters}

    async def _status(self, adapter: ExchangeAdapter) -> HealthStatus:
        if self._passive is not None:
            st = await self._passive(adapter.code)
            if st is not None:
                return st
        return await adapter.health_check()

    async def check_once(self) -> dict[str, HealthStatus]:
        results = await asyncio.gather(*(self._status(a) for a in self._adapters))
        out: dict[str, HealthStatus] = {}
        for adapter, status in zip(self._adapters, results, strict=True):
            out[adapter.code] = status
            await self._handle(adapter.code, status)
        return out

    async def _handle(self, code: str, status: HealthStatus) -> None:
        self._failures[code] = 0 if status.ok else self._failures[code] + 1
        previous = self.state[code]
        # One failed probe is noise; require consecutive failures before "down".
        healthy = status.ok or self._failures[code] < self._threshold
        if previous is None and not status.ok:
            healthy = False
        await self._redis.set(events.health_key(code), json.dumps({
            "ok": healthy, "latency_ms": status.latency_ms, "detail": status.detail,
            "checked_at": status.checked_at.isoformat()}), ex=180)
        await self._debounced_alert(code, healthy, status.detail)
        if previous is not None and previous == healthy:
            return
        self.state[code] = healthy
        if previous is None and healthy:
            return  # first observation healthy: nothing to report
        severity = "info" if healthy else "error"
        message = f"{code} {'reconnected' if healthy else 'unreachable'}"
        log.warning("exchange_health_change", exchange=code, healthy=healthy,
                    detail=status.detail)
        await asyncio.to_thread(self._record, code, healthy, severity, message, status)

    async def _debounced_alert(self, code: str, healthy: bool, detail: str | None) -> None:
        a, now = self._alert[code], self._clock()
        if not healthy:
            if a["down_since"] is None:
                a["down_since"], a["up_since"] = now, None
                a["interruptions"] += 1
            a["detail"] = detail
            cooled = a["last_alert"] is None or now - a["last_alert"] >= self._cooldown
            if not a["alerted"] and now - a["down_since"] >= self._down_after and cooled:
                a["alerted"], a["last_alert"] = True, now
                await self._notify({"type": "exchange_disconnected", "exchange": code,
                                    "detail": detail,
                                    "summary": f"down for {int(now - a['down_since'])} s; "
                                               "new entries are blocked until it recovers"})
            return
        if a["down_since"] is not None:
            a["down_seconds"] += now - a["down_since"]
            a["down_since"] = None
        if a["up_since"] is None:
            a["up_since"] = now
        if a["alerted"] and now - a["up_since"] >= self._up_after:
            a["alerted"] = False
            await self._notify({"type": "exchange_connected", "exchange": code,
                                "summary": f"stable for {int(now - a['up_since'])} s after "
                                           f"{a['interruptions']} interruption(s), "
                                           f"{int(a['down_seconds'])} s down in total; "
                                           f"last cause: {a['detail']}"})
            a["interruptions"], a["down_seconds"] = 0, 0.0
        elif not a["alerted"] and a["interruptions"] and now - a["up_since"] >= self._cooldown:
            a["interruptions"], a["down_seconds"] = 0, 0.0   # short blips, never alerted

    async def _notify(self, payload: dict[str, Any]) -> None:
        log.warning("exchange_alert", **{k: v for k, v in payload.items() if k != "type"},
                    alert=payload["type"])
        await self._redis.xadd(events.NOTIFY_STREAM, events.encode(payload),
                               maxlen=events.STREAM_MAXLEN, approximate=True)

    def _record(self, code: str, healthy: bool, severity: str, message: str,
                status: HealthStatus) -> None:
        with transaction(self._factory) as s:
            repo.record_system_event(
                s, component="exchange-health",
                event_type="exchange_up" if healthy else "exchange_down",
                severity=severity, message=message,
                details={"exchange": code, "detail": status.detail,
                         "latency_ms": status.latency_ms})
