"""X sentiment worker: ``python -m nexus_social.service`` or inline in the API process.

Each refresh (default every 2 h) makes at most two recent-search calls (coins, watched
accounts) using ``since_id`` so no post is bought twice, plus trends every few hours.
Posts are kept in Redis for 24 h; the snapshot, history, block list and status are
written to Redis for the API and the trader. All of it is bounded by the daily budget.
"""

from __future__ import annotations

import asyncio
import json
import signal
from datetime import datetime, timedelta
from typing import Any

from nexus_common import events
from nexus_common.logging import configure_logging, get_logger
from nexus_common.redisutil import connect as connect_redis
from nexus_common.timeutil import utcnow
from nexus_config import Settings, get_settings

from nexus_social import analysis
from nexus_social.client import XClient, XError
from nexus_social.text import search_query

log = get_logger("nexus.social")

POSTS = "nexus:x:posts"
USERS = "nexus:x:users"
SINCE = "nexus:x:since:{name}"
SNAPSHOT = "nexus:x:snapshot"
HISTORY = "nexus:x:history"
RISK = "nexus:x:risk"
STATUS = "nexus:x:status"
TRENDS = "nexus:x:trends"
NOTIFIED = "nexus:x:notified"


def accounts_query(accounts: list[str]) -> str | None:
    names = [a.lstrip("@") for a in accounts if a.strip()]
    return f"({' OR '.join(f'from:{a}' for a in names)}) -is:retweet" if names else None


async def _store(redis: Any, data: dict[str, Any]) -> int:
    posts = data.get("data") or []
    users = (data.get("includes") or {}).get("users") or []
    for u in users:
        await redis.hset(USERS, u["id"], json.dumps(u))
    for p in posts:
        await redis.hset(POSTS, p["id"], json.dumps(p))
    return len(posts)


async def _prune(redis: Any, now: datetime) -> None:
    cutoff = (now - timedelta(hours=analysis.WINDOW_HOURS)).isoformat()
    for pid, raw in (await redis.hgetall(POSTS)).items():
        if (json.loads(raw).get("created_at") or "").replace("Z", "+00:00") < cutoff:
            await redis.hdel(POSTS, pid)


async def load_posts(redis: Any) -> tuple[list[dict[str, Any]], dict[str, dict[str, Any]]]:
    posts = [json.loads(v) for v in (await redis.hgetall(POSTS)).values()]
    users = {json.loads(v)["id"]: json.loads(v) for v in (await redis.hgetall(USERS)).values()}
    return posts, users


async def build_snapshot(redis: Any, settings: Settings, now: datetime,
                         usage: dict[str, Any] | None = None) -> dict[str, Any]:
    coins = [c.upper() for c in settings.x_coins]
    trusted = {a.lstrip("@").lower() for a in settings.x_watch_accounts}
    posts, users = await load_posts(redis)
    enriched = analysis.enrich(posts, users, coins, trusted, now)
    per_coin = [analysis.coin_sentiment(c, enriched, now) for c in coins]
    alerts = analysis.news_alerts(enriched, now)
    raw_trends = await redis.get(TRENDS)
    x_trends = json.loads(raw_trends) if raw_trends else []
    return {"generated_at": now.isoformat(), "coins": per_coin, "alerts": alerts[:50],
            "trending": analysis.trending(per_coin, x_trends, coins),
            "blocks": analysis.entry_blocks(alerts, now), "posts_in_window": len(enriched),
            "policy": analysis.POLICY, "usage": usage}


async def refresh_once(redis: Any, client: XClient, settings: Settings) -> dict[str, Any]:
    now = utcnow()
    errors: list[str] = []
    fetched = 0
    coins = [c.upper() for c in settings.x_coins]
    queries = {"coins": search_query(coins), "accounts": accounts_query(settings.x_watch_accounts)}
    for name, q in queries.items():
        if not q:
            continue
        since = await redis.get(SINCE.format(name=name))
        try:
            data = await client.search_recent(
                q, max_results=settings.x_max_posts_per_query,
                since_id=(since.decode() if isinstance(since, bytes) else since) or None)
        except XError as exc:
            errors.append(f"{name}: {exc}")
            continue
        fetched += await _store(redis, data)
        newest = (data.get("meta") or {}).get("newest_id")
        if newest:
            await redis.set(SINCE.format(name=name), newest, ex=7 * 86400)
    if settings.x_trends_woeid is not None and not await redis.get(TRENDS + ":fresh"):
        try:
            t = await client.trends(settings.x_trends_woeid)
            await redis.set(TRENDS, json.dumps(t.get("data") or []), ex=2 * 86400)
            await redis.set(TRENDS + ":fresh", "1", ex=settings.x_trends_every_hours * 3600)
        except XError as exc:
            errors.append(f"trends: {exc}")
    await _prune(redis, now)
    u = await client.usage()
    usage = {"day": u.day, "spent_usd": str(round(u.spent_usd, 4)),
             "budget_usd": str(u.budget_usd), "rate_limits": client.last_rate_limit,
             "rate_limited_until": None if u.rate_limited_until is None
             else u.rate_limited_until.isoformat()}
    snap = await build_snapshot(redis, settings, now, usage)
    ttl = settings.x_refresh_minutes * 60 * 3
    await redis.set(SNAPSHOT, json.dumps(snap, default=str), ex=2 * 86400)
    await redis.set(RISK, json.dumps({"blocks": snap["blocks"], "as_of": now.isoformat()}),
                    ex=ttl)
    await redis.rpush(HISTORY, json.dumps({"t": now.isoformat(), **{
        c["coin"]: c["score"] for c in snap["coins"]}}))
    await redis.ltrim(HISTORY, -500, -1)
    status = {"last_attempt": now.isoformat(), "fetched": fetched, "errors": errors,
              "ok": not errors or fetched > 0}
    if status["ok"]:
        status["last_ok"] = now.isoformat()
    else:
        prev = await redis.get(STATUS)
        status["last_ok"] = json.loads(prev).get("last_ok") if prev else None
    await redis.set(STATUS, json.dumps(status), ex=7 * 86400)
    await _notify(redis, snap["alerts"])
    log.info("x_refreshed", fetched=fetched, errors=errors, spent=usage["spent_usd"])
    return snap


async def _notify(redis: Any, alerts: list[dict[str, Any]]) -> None:
    for a in alerts:
        if not (a["high_impact"] and a["status"] == "confirmed"):
            continue
        key = f"{a['coin']}|{a['category']}|{a['first_seen']}"
        if await redis.sismember(NOTIFIED, key):
            continue
        await redis.sadd(NOTIFIED, key)
        await redis.expire(NOTIFIED, 3 * 86400)
        await redis.xadd(events.NOTIFY_STREAM, events.encode({"type": "x_alert", **{
            k: a[k] for k in ("coin", "label", "status", "credible_sources", "headline",
                              "author", "url", "direction")}}),
            maxlen=events.STREAM_MAXLEN, approximate=True)


async def serve(settings: Settings, stop: asyncio.Event) -> None:
    if settings.x_bearer_token is None:
        log.warning("x_not_configured")
        await stop.wait()
        return
    redis = connect_redis(settings.redis_url.get_secret_value())
    client = XClient(settings.x_bearer_token.get_secret_value(), redis,
                     base_url=settings.x_api_base, daily_budget=settings.x_daily_budget_usd)
    try:
        while not stop.is_set():
            try:
                await refresh_once(redis, client, settings)
            except Exception as exc:  # keep running; status shows the failure
                log.error("x_refresh_failed", error=f"{type(exc).__name__}: {exc}"[:300])
                await redis.set(STATUS, json.dumps({
                    "last_attempt": utcnow().isoformat(), "ok": False,
                    "errors": [f"{type(exc).__name__}"]}), ex=7 * 86400)
            try:
                await asyncio.wait_for(stop.wait(), timeout=settings.x_refresh_minutes * 60)
            except TimeoutError:
                pass
    finally:
        await client.close()
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
