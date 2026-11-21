"""Emergency kill switch. Fails CLOSED: if its state cannot be read, trading stops.

Engaging is allowed from anywhere (risk engine, owner, monitor). Clearing requires an
explicit owner action with a reason and is audit-logged by the caller.

Durability: Redis holds the live flag; every engage/clear is also written to the
append-only audit log by the caller, and ``restore`` re-engages from that record when the
Redis key is lost. Callers that cannot read the durable record must treat the switch as
engaged for that cycle.
"""

from __future__ import annotations

import json
from typing import Any

from nexus_common import events
from nexus_common.logging import get_logger
from nexus_common.timeutil import utcnow

log = get_logger("nexus.risk.kill_switch")


class KillSwitch:
    def __init__(self, redis: Any) -> None:
        self._redis = redis

    async def engage(self, reason: str, *, actor: str) -> None:
        await self._redis.set(events.KILL_SWITCH_KEY, json.dumps(
            {"reason": reason, "actor": actor, "at": utcnow().isoformat()}))
        await self._redis.xadd(events.NOTIFY_STREAM, events.encode(
            {"type": "emergency_shutdown", "reason": reason, "actor": actor}),
            maxlen=events.STREAM_MAXLEN, approximate=True)
        log.critical("kill_switch_engaged", reason=reason, actor=actor)

    async def restore(self, durable: dict[str, Any] | None) -> bool:
        """Re-engage from the durable record if Redis lost the key (e.g. a Redis restart).
        Never clears: a cleared durable record leaves Redis as it is. Returns True when
        the key had to be restored."""
        if durable is None:
            return False
        if await self._redis.get(events.KILL_SWITCH_KEY) is not None:
            return False
        await self._redis.set(events.KILL_SWITCH_KEY, json.dumps(durable))
        log.critical("kill_switch_restored", reason=durable.get("reason"),
                     actor=durable.get("actor"))
        return True

    async def clear(self, *, actor: str, reason: str) -> None:
        if not actor.startswith("user:"):
            raise PermissionError("only the owner can clear the kill switch")
        if len(reason.strip()) < 5:
            raise ValueError("a reason is required to clear the kill switch")
        await self._redis.delete(events.KILL_SWITCH_KEY)
        log.warning("kill_switch_cleared", actor=actor, reason=reason)

    async def status(self) -> dict[str, Any] | None:
        """None = not engaged. Raises if Redis is unreachable (callers fail closed)."""
        raw = await self._redis.get(events.KILL_SWITCH_KEY)
        if raw is None:
            return None
        result: dict[str, Any] = json.loads(raw)
        return result

    async def is_engaged(self) -> bool:
        try:
            return await self.status() is not None
        except Exception as exc:  # fail closed on ANY error reading state
            log.error("kill_switch_unreadable", error=type(exc).__name__)
            return True
