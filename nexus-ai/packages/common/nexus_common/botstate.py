"""Bot run state shared by the trader and the API.

* RUNNING: normal operation.
* PAUSED:  no new entries; open positions are still managed (stops, targets, trailing).
* STOPPED: no new entries and no new orders of any kind; open positions are NOT closed
           automatically. Resuming or closing positions is an explicit owner action.

The kill switch (nexus:kill_switch) is separate and stronger: it blocks everything.
"""

from __future__ import annotations

import json
from enum import StrEnum
from typing import Any

from nexus_common.timeutil import utcnow

KEY = "nexus:bot:state"


class BotState(StrEnum):
    RUNNING = "running"
    PAUSED = "paused"
    STOPPED = "stopped"


async def get_state(redis: Any) -> BotState:
    """Missing or unreadable state means STOPPED (fail closed)."""
    try:
        raw = await redis.get(KEY)
    except Exception:
        return BotState.STOPPED
    if raw is None:
        return BotState.STOPPED
    try:
        return BotState(json.loads(raw)["state"])
    except (ValueError, KeyError, TypeError):
        return BotState.STOPPED


async def set_state(redis: Any, state: BotState, *, actor: str, reason: str) -> None:
    await redis.set(KEY, json.dumps({"state": state.value, "actor": actor, "reason": reason,
                                     "at": utcnow().isoformat()}))
