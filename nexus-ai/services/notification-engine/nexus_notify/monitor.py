"""Operations monitor: ``python -m nexus_notify.monitor``.

Every minute: database and Redis reachability, disk and memory usage, and service
heartbeat freshness. Problems are recorded as system events and alerted once per
transition (problem -> resolved), never spammed.
"""

from __future__ import annotations

import asyncio
import shutil
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

import redis.asyncio as aioredis
from nexus_common import events
from nexus_common.logging import configure_logging, get_logger
from nexus_common.timeutil import utcnow
from nexus_config import TradingMode, get_settings
from nexus_db import make_engine, make_session_factory, ping, repo, transaction

log = get_logger("nexus.monitor")
DISK_WARN, MEM_WARN = 0.85, 0.90
HEARTBEAT_MAX_AGE = {"market-data:candles": 180, "notifier": 600}


@dataclass(frozen=True)
class Check:
    name: str
    ok: bool
    detail: str


def memory_usage(meminfo: str | None = None) -> float | None:
    text = meminfo if meminfo is not None else (
        Path("/proc/meminfo").read_text() if Path("/proc/meminfo").exists() else "")
    vals = {}
    for line in text.splitlines():
        k, _, v = line.partition(":")
        if v.strip():
            vals[k] = int(v.split()[0])
    if "MemTotal" not in vals or "MemAvailable" not in vals:
        return None
    return 1 - vals["MemAvailable"] / vals["MemTotal"]


def disk_usage(path: str = "/") -> float:
    u = shutil.disk_usage(path)
    return u.used / u.total


def heartbeat_checks(beats: dict[str, str | None], now: datetime,
                     trader_mode: str) -> list[Check]:
    expected = {**HEARTBEAT_MAX_AGE, f"trader:{trader_mode}": 180}
    out = []
    for name, max_age in expected.items():
        raw = beats.get(name)
        if raw is None:
            out.append(Check(f"heartbeat:{name}", False, "missing"))
            continue
        age = (now - datetime.fromisoformat(raw)).total_seconds()
        out.append(Check(f"heartbeat:{name}", age <= max_age, f"{age:.0f}s old"))
    return out


class Monitor:
    def __init__(self, redis: Any, engine: Any, factory: Any, trader_mode: str) -> None:
        self.redis, self.engine, self.factory, self.trader_mode = redis, engine, factory, trader_mode
        self.state: dict[str, bool] = {}

    async def run_checks(self) -> list[Check]:
        checks: list[Check] = []
        try:
            await asyncio.wait_for(asyncio.to_thread(ping, self.engine), 5)
            checks.append(Check("database", True, "ok"))
        except Exception as exc:
            checks.append(Check("database", False, type(exc).__name__))
        try:
            await asyncio.wait_for(self.redis.ping(), 5)
            checks.append(Check("redis", True, "ok"))
            beats = {}
            for name in [*HEARTBEAT_MAX_AGE, f"trader:{self.trader_mode}"]:
                raw = await self.redis.get(events.heartbeat_key(name))
                beats[name] = raw.decode() if isinstance(raw, bytes) else raw
            checks += heartbeat_checks(beats, utcnow(), self.trader_mode)
        except Exception as exc:
            checks.append(Check("redis", False, type(exc).__name__))
        d = disk_usage()
        checks.append(Check("disk", d < DISK_WARN, f"{d:.0%} used"))
        m = memory_usage()
        if m is not None:
            checks.append(Check("memory", m < MEM_WARN, f"{m:.0%} used"))
        return checks

    async def report(self, checks: list[Check]) -> list[Check]:
        changed = []
        for c in checks:
            prev = self.state.get(c.name)
            self.state[c.name] = c.ok
            if (prev is None and c.ok) or prev == c.ok:
                continue
            changed.append(c)
        for c in changed:
            severity = "info" if c.ok else "error"
            log.warning("ops_check_changed", check=c.name, ok=c.ok, detail=c.detail)
            try:
                with transaction(self.factory) as s:
                    state = "recovered" if c.ok else "PROBLEM"
                    repo.record_system_event(s, component="monitor", event_type=c.name,
                                             severity=severity,
                                             message=f"{c.name}: {state} ({c.detail})")
            except Exception as exc:  # DB may be the failing component
                log.error("monitor_record_failed", error=type(exc).__name__)
            try:
                await self.redis.xadd(events.NOTIFY_STREAM, events.encode({
                    "type": "system_alert", "check": c.name, "ok": c.ok, "detail": c.detail}),
                    maxlen=events.STREAM_MAXLEN, approximate=True)
            except Exception as exc:
                log.error("monitor_notify_failed", error=type(exc).__name__)
        return changed


async def main() -> None:
    settings = get_settings()
    configure_logging(settings.log_level, json=settings.log_json)
    redis = aioredis.from_url(settings.redis_url.get_secret_value())
    engine = make_engine(settings.database_url.get_secret_value(), pool_size=1)
    mode = "live" if settings.trading_mode is TradingMode.LIVE else "paper"
    mon = Monitor(redis, engine, make_session_factory(engine), mode)
    while True:
        await mon.report(await mon.run_checks())
        try:
            await redis.set(events.heartbeat_key("monitor"), utcnow().isoformat(), ex=300)
        except Exception as exc:  # Redis may be the failing component
            log.error("monitor_heartbeat_failed", error=type(exc).__name__)
        await asyncio.sleep(60)


if __name__ == "__main__":
    asyncio.run(main())
