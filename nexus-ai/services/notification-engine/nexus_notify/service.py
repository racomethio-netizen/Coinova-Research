"""Notifier process: ``python -m nexus_notify.service``.

Consumes ``NOTIFY_STREAM`` with a Redis consumer group (at-least-once: an entry is ACKed
only after delivery, a permanent failure, or exhausting retries). Also sends a daily
performance report shortly after 00:00 UTC. Without Telegram configuration, events are
logged and acknowledged (nothing is silently lost: the log records each one).
"""

from __future__ import annotations

import asyncio
import signal
from datetime import datetime, timedelta
from typing import Any, Protocol

from nexus_common import events
from nexus_common.logging import configure_logging, get_logger
from nexus_common.redisutil import connect as connect_redis
from nexus_common.timeutil import utcnow
from nexus_config import get_settings
from nexus_db import make_engine, make_session_factory, models, transaction
from nexus_execution.reports import portfolio_report
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from nexus_notify.formatting import format_event
from nexus_notify.telegram import SendResult, TelegramClient

log = get_logger("nexus.notify")
GROUP, CONSUMER = "notifier", "notifier-1"
MAX_ATTEMPTS = 5
MAX_PER_MINUTE = 20


class Sender(Protocol):
    async def send(self, text: str) -> SendResult: ...


class Notifier:
    def __init__(self, redis: Any, sender: Sender | None, *,
                 sleep: Any = asyncio.sleep) -> None:
        self.redis, self.sender, self._sleep = redis, sender, sleep
        self._sent: list[float] = []
        self.stop = asyncio.Event()

    async def ensure_group(self) -> None:
        try:
            await self.redis.xgroup_create(events.NOTIFY_STREAM, GROUP, id="0", mkstream=True)
        except Exception as exc:  # BUSYGROUP: already exists
            if "BUSYGROUP" not in str(exc):
                raise

    async def _throttle(self) -> None:
        now = asyncio.get_running_loop().time()
        self._sent = [t for t in self._sent if now - t < 60]
        if len(self._sent) >= MAX_PER_MINUTE:
            await self._sleep(60 - (now - self._sent[0]))
        self._sent.append(asyncio.get_running_loop().time())

    async def deliver(self, payload: dict[str, Any]) -> bool:
        """True when the entry can be acknowledged."""
        text = format_event(payload)
        if text is None:
            log.warning("notify_unknown_type", type=payload.get("type"))
            return True
        if self.sender is None:
            log.info("notify_disabled", type=payload.get("type"), text=text[:300])
            return True
        for attempt in range(1, MAX_ATTEMPTS + 1):
            await self._throttle()
            res = await self.sender.send(text)
            if res.ok:
                return True
            log.warning("notify_failed", type=payload.get("type"), attempt=attempt,
                        error=res.error, permanent=res.permanent)
            if res.permanent:
                return True
            await self._sleep(res.retry_after or min(60, 2 ** attempt))
        log.error("notify_gave_up", type=payload.get("type"))
        return True

    async def process_once(self, block_ms: int = 2000) -> int:
        # Pending (delivered but unacked, e.g. after a crash) first, then new entries.
        handled = 0
        for start in ("0", ">"):
            resp = await self.redis.xreadgroup(GROUP, CONSUMER, {events.NOTIFY_STREAM: start},
                                               count=20, block=None if start == "0" else block_ms)
            for _stream, entries in resp or []:
                for entry_id, fields in entries:
                    if not fields:
                        await self.redis.xack(events.NOTIFY_STREAM, GROUP, entry_id)
                        continue
                    if await self.deliver(events.decode(fields)):
                        await self.redis.xack(events.NOTIFY_STREAM, GROUP, entry_id)
                        handled += 1
        return handled

    async def run(self) -> None:
        await self.ensure_group()
        while not self.stop.is_set():
            try:
                await self.process_once()
            except Exception as exc:  # keep running; log the failure
                log.error("notify_loop_error", error=f"{type(exc).__name__}: {exc}"[:300])
                await asyncio.sleep(5)
            await self.redis.set(events.heartbeat_key("notifier"), utcnow().isoformat(), ex=300)


async def daily_reports(factory: sessionmaker[Session], redis: Any, now: datetime) -> int:
    """Queue one daily report per portfolio, once per UTC day (idempotent via Redis)."""
    day = (now - timedelta(days=1)).date().isoformat()
    queued = 0
    with transaction(factory) as s:
        reports = [(p.mode, portfolio_report(s, p.id))
                   for p in s.scalars(select(models.Portfolio).where(models.Portfolio.is_active))]
    for mode, report in reports:
        if await redis.set(f"nexus:daily_report:{mode}:{day}", "1", nx=True, ex=3 * 86400):
            await redis.xadd(events.NOTIFY_STREAM, events.encode(
                {"type": "daily_report", "mode": mode, "date": day, "report": report}),
                maxlen=events.STREAM_MAXLEN, approximate=True)
            queued += 1
    return queued


async def main() -> None:
    settings = get_settings()
    configure_logging(settings.log_level, json=settings.log_json)
    redis = connect_redis(settings.redis_url.get_secret_value())
    engine = make_engine(settings.database_url.get_secret_value(), pool_size=2)
    factory = make_session_factory(engine)
    sender = None
    if settings.telegram_bot_token and settings.telegram_chat_id:
        sender = TelegramClient(settings.telegram_bot_token.get_secret_value(),
                                settings.telegram_chat_id)
    else:
        log.warning("telegram_not_configured")
    notifier = Notifier(redis, sender)
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, notifier.stop.set)

    async def reporter() -> None:
        while not notifier.stop.is_set():
            if utcnow().hour == 0 and utcnow().minute >= 5:
                await daily_reports(factory, redis, utcnow())
            await asyncio.sleep(60)

    task = asyncio.create_task(reporter())
    try:
        await notifier.run()
    finally:
        task.cancel()
        if isinstance(sender, TelegramClient):
            await sender.close()
        await redis.aclose()
        engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
