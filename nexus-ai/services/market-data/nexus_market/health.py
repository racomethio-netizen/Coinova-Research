"""Exchange health monitoring: records transitions, publishes current state to Redis."""

from __future__ import annotations

import asyncio
import json
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
                 redis: Any, *, failures_before_down: int = 2) -> None:
        self._adapters = adapters
        self._factory = factory
        self._redis = redis
        self._failures: dict[str, int] = {a.code: 0 for a in adapters}
        self.state: dict[str, bool | None] = {a.code: None for a in adapters}
        self._threshold = failures_before_down

    async def check_once(self) -> dict[str, HealthStatus]:
        results = await asyncio.gather(*(a.health_check() for a in self._adapters))
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
        await self._redis.xadd(events.NOTIFY_STREAM, events.encode({
            "type": "exchange_connected" if healthy else "exchange_disconnected",
            "exchange": code, "detail": status.detail}),
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
