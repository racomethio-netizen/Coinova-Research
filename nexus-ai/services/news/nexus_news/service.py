"""News monitor process: ``python -m nexus_news.service``.

Every ``interval`` seconds it fetches the public feeds, scores them and writes the result
to Redis (key ``nexus:news:risk``, short TTL). The trader reads that key before every
entry; a missing or stale key means unknown news risk, so entries are blocked.
"""

from __future__ import annotations

import asyncio
import json
import signal
from datetime import timedelta
from typing import Any

import httpx
from nexus_common.logging import configure_logging, get_logger
from nexus_common.redisutil import connect as connect_redis
from nexus_common.timeutil import utcnow
from nexus_config import get_settings

from nexus_news.feeds import fetch_items
from nexus_news.scoring import score_items

log = get_logger("nexus.news")
KEY = "nexus:news:risk"
TTL_SECONDS = 900


async def refresh_once(redis: Any, client: httpx.AsyncClient) -> dict[str, Any]:
    items, errors = await fetch_items(client)
    now = utcnow()
    risk = score_items(items, now=now, errors=errors)
    payload = {"score": risk.score, "matched": list(risk.matched),
               "unknown_sources": list(risk.unknown_sources), "as_of": now.isoformat()}
    await redis.set(KEY, json.dumps(payload), ex=TTL_SECONDS)
    log.info("news_refreshed", score=risk.score, items=len(items), unknown=list(errors))
    return payload


async def main() -> None:
    settings = get_settings()
    configure_logging(settings.log_level, json=settings.log_json)
    redis = connect_redis(settings.redis_url.get_secret_value())
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stop.set)
    async with httpx.AsyncClient(timeout=15, follow_redirects=True) as client:
        while not stop.is_set():
            try:
                await refresh_once(redis, client)
            except Exception as exc:  # keep running; a missing key already fails closed
                log.error("news_refresh_failed", error=type(exc).__name__)
            try:
                await asyncio.wait_for(stop.wait(), timeout=timedelta(minutes=5).total_seconds())
            except TimeoutError:
                pass
    await redis.aclose()


if __name__ == "__main__":
    asyncio.run(main())
