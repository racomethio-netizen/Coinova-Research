"""Redis key and stream names shared across services (one place, no typos)."""

from __future__ import annotations

import json
from typing import Any

CANDLE_STREAM = "nexus:stream:candles"          # closed-candle notifications
NOTIFY_STREAM = "nexus:stream:notifications"    # consumed by notification-engine
KILL_SWITCH_KEY = "nexus:kill_switch"           # set => no new orders anywhere
STREAM_MAXLEN = 10_000


def book_key(exchange: str, symbol: str) -> str:
    return f"nexus:book:{exchange}:{symbol}"


def health_key(exchange: str) -> str:
    return f"nexus:health:{exchange}"


def heartbeat_key(component: str) -> str:
    return f"nexus:heartbeat:{component}"


def encode(payload: dict[str, Any]) -> dict[str, str]:
    """Redis stream entries are flat string maps; nest everything as one JSON field."""
    return {"data": json.dumps(payload, default=str, separators=(",", ":"))}


def decode(fields: dict[Any, Any]) -> dict[str, Any]:
    raw = fields.get("data", fields.get(b"data"))
    if isinstance(raw, bytes):
        raw = raw.decode()
    result: dict[str, Any] = json.loads(raw)
    return result
