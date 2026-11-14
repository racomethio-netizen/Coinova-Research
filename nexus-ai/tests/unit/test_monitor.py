from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from nexus_notify.formatting import format_event
from nexus_notify.monitor import Check, Monitor, heartbeat_checks, memory_usage

NOW = datetime(2026, 6, 1, 12, tzinfo=UTC)


def test_memory_parsing() -> None:
    assert memory_usage("MemTotal: 1000 kB\nMemAvailable: 250 kB\n") == 0.75
    assert memory_usage("garbage") is None


def test_heartbeats() -> None:
    beats = {"market-data:candles": (NOW - timedelta(seconds=30)).isoformat(),
             "notifier": None, "trader:paper": (NOW - timedelta(minutes=10)).isoformat()}
    res = {c.name: c.ok for c in heartbeat_checks(beats, NOW, "paper")}
    assert res == {"heartbeat:market-data:candles": True, "heartbeat:notifier": False,
                   "heartbeat:trader:paper": False}


class FakeRedis:
    def __init__(self) -> None:
        self.stream: list[Any] = []

    async def xadd(self, name: str, fields: Any, **_: Any) -> None:
        self.stream.append(fields)


async def test_alerts_only_on_transitions() -> None:
    r = FakeRedis()
    m = Monitor(r, None, None, "paper")
    m.factory = None  # DB recording failures are tolerated (DB may be what's down)
    assert await m.report([Check("disk", True, "40%")]) == []          # first OK: silent
    assert len(await m.report([Check("disk", False, "91%")])) == 1      # problem
    assert await m.report([Check("disk", False, "92%")]) == []          # still bad: no spam
    assert len(await m.report([Check("disk", True, "60%")])) == 1      # recovered
    assert len(r.stream) == 2
    assert "PROBLEM" in (format_event({"type": "system_alert", "check": "disk", "ok": False,
                                       "detail": "91%"}) or "")
