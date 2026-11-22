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
from nexus_config import Settings, get_settings

from nexus_news.feeds import fetch_items
from nexus_news.scoring import score_items

log = get_logger("nexus.news")
KEY = "nexus:news:risk"
TTL_SECONDS = 900


async def refresh_once(redis: Any, client: httpx.AsyncClient) -> dict[str, Any]:
    items, errors = await fetch_items(client)
    now = utcnow()
    risk = score_items(items, now=now, errors=errors)
    from nexus_news.scoring import _weight

    recent = sorted((i for i in items if i.published is not None),
                    key=lambda i: i.published or now, reverse=True)[:40]
    payload = {"score": risk.score, "matched": list(risk.matched),
               "unknown_sources": list(risk.unknown_sources), "as_of": now.isoformat(),
               "items": [{"source": i.source, "title": i.title[:300], "link": i.link,
                          "published": i.published.isoformat() if i.published else None,
                          "impact": _weight(i.title)[0], "keyword": _weight(i.title)[1]}
                         for i in recent]}
    await redis.set(KEY, json.dumps(payload), ex=TTL_SECONDS)
    log.info("news_refreshed", score=risk.score, items=len(items), unknown=list(errors))
    return payload


async def serve(settings: Settings, stop: asyncio.Event) -> None:
    """Run until ``stop`` is set (used by main() and by the API's inline worker mode)."""
    redis = connect_redis(settings.redis_url.get_secret_value())
    try:
        async with httpx.AsyncClient(timeout=15, follow_redirects=True,
                                     trust_env=True) as client:
            while not stop.is_set():
                try:
                    await refresh_once(redis, client)
                except Exception as exc:  # keep running; a missing key already fails closed
                    log.error("news_refresh_failed", error=type(exc).__name__)
                try:
                    await asyncio.wait_for(stop.wait(),
                                           timeout=timedelta(minutes=5).total_seconds())
                except TimeoutError:
                    pass
    finally:
        await redis.aclose()


async def main() -> None:
    settings = get_settings()
    configure_logging(settings.log_level, json=settings.log_json)
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stop.set)
    await serve(settings, stop)


if __name__ == "__main__":
    asyncio.run(main())
