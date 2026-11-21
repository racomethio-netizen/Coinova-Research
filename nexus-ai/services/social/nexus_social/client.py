"""Read-only X API v2 client (app-only Bearer token).

Safety properties, all enforced here and tested:
* GET only, and only to an allowlist of read endpoints. There is no method for posting,
  liking, following, messaging or deleting.
* Every call is priced before it is made (X pay-per-use: per resource returned) and
  refused when it could exceed the daily budget; actual cost is recorded afterwards.
* X's rate-limit headers are honoured; after a 429 no call is made until the reset time.
* The token is never logged or returned; errors carry only status and X's error title.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import httpx
from nexus_common.logging import get_logger
from nexus_common.timeutil import utcnow

log = get_logger("nexus.social.x")

# Documented pay-per-use prices (docs.x.com/x-api/getting-started/pricing, 2026-11-21).
PRICE_POST_READ = Decimal("0.005")
PRICE_USER_READ = Decimal("0.010")
PRICE_COUNTS = Decimal("0.005")
PRICE_TRENDS = Decimal("0.010")

ALLOWED = (
    re.compile(r"^/2/tweets/search/recent$"),
    re.compile(r"^/2/tweets/counts/recent$"),
    re.compile(r"^/2/trends/by/woeid/\d+$"),
    re.compile(r"^/2/users/by/username/[A-Za-z0-9_]{1,15}$"),
)

TWEET_FIELDS = "created_at,public_metrics,lang,author_id,entities"
USER_FIELDS = "created_at,public_metrics,verified,verified_type,description,profile_image_url"


class XError(Exception):
    """A refused or failed X request (message is safe to show)."""


class BudgetExceeded(XError):
    pass


class RateLimited(XError):
    pass


@dataclass(frozen=True)
class Usage:
    day: str
    spent_usd: Decimal
    budget_usd: Decimal
    rate_limited_until: datetime | None


def _day(now: datetime) -> str:
    return now.astimezone(UTC).date().isoformat()


class XClient:
    SPEND_KEY = "nexus:x:spend:{day}"
    RL_KEY = "nexus:x:ratelimited_until"
    CACHE_KEY = "nexus:x:cache:{key}"

    def __init__(self, token: str, redis: Any, *, base_url: str = "https://api.x.com",
                 daily_budget: Decimal = Decimal("1.00"),
                 transport: httpx.AsyncBaseTransport | None = None) -> None:
        self._redis = redis
        self._budget = daily_budget
        self._http = httpx.AsyncClient(
            base_url=base_url, timeout=20, transport=transport, trust_env=True,
            headers={"Authorization": f"Bearer {token}", "User-Agent": "nexus-sentiment/1.0"})
        self.last_rate_limit: dict[str, Any] = {}

    def __repr__(self) -> str:  # never expose the token
        return "XClient()"

    async def close(self) -> None:
        await self._http.aclose()

    # ------------------------------------------------------------------ budget
    async def usage(self) -> Usage:
        now = utcnow()
        raw = await self._redis.get(self.SPEND_KEY.format(day=_day(now)))
        spent = Decimal(raw.decode() if isinstance(raw, bytes) else raw) if raw else Decimal(0)
        return Usage(day=_day(now), spent_usd=spent, budget_usd=self._budget,
                     rate_limited_until=await self._rate_limited_until())

    async def _charge(self, amount: Decimal) -> None:
        if amount <= 0:
            return
        key = self.SPEND_KEY.format(day=_day(utcnow()))
        await self._redis.incrbyfloat(key, float(amount))
        await self._redis.expire(key, 3 * 86400)

    async def _rate_limited_until(self) -> datetime | None:
        raw = await self._redis.get(self.RL_KEY)
        if not raw:
            return None
        until = datetime.fromisoformat(raw.decode() if isinstance(raw, bytes) else raw)
        return until if until > utcnow() else None

    # ------------------------------------------------------------------ core GET
    async def _get(self, path: str, params: dict[str, Any], *, max_cost: Decimal,
                   cache_seconds: int = 0) -> tuple[dict[str, Any], bool]:
        """(response, served_from_cache). Cached responses cost nothing."""
        if not any(p.match(path) for p in ALLOWED):
            raise XError(f"endpoint not allowed (read-only allowlist): {path}")
        cache_key = self.CACHE_KEY.format(key=f"{path}?{json.dumps(params, sort_keys=True)}")
        if cache_seconds:
            hit = await self._redis.get(cache_key)
            if hit:
                return dict(json.loads(hit)), True
        until = await self._rate_limited_until()
        if until is not None:
            raise RateLimited(f"X rate limit: waiting until {until.isoformat()}")
        u = await self.usage()
        if u.spent_usd + max_cost > self._budget:
            raise BudgetExceeded(f"daily X budget reached (${u.spent_usd:.3f} of "
                                 f"${self._budget:.2f}); next refresh after 00:00 UTC")
        try:
            r = await self._http.get(path, params=params)
        except httpx.HTTPError as exc:
            raise XError(f"network error reaching X ({type(exc).__name__})") from None
        self._note_rate_limit(path, r)
        if r.status_code == 429:
            reset = r.headers.get("x-rate-limit-reset")
            until_dt = (datetime.fromtimestamp(int(reset), tz=UTC) if reset and reset.isdigit()
                        else utcnow().replace(microsecond=0))
            ttl = max(1, int((until_dt - utcnow()).total_seconds()))
            await self._redis.set(self.RL_KEY, until_dt.isoformat(), ex=ttl)
            raise RateLimited(f"X rate limit reached; resets {until_dt.isoformat()}")
        if r.status_code in (401, 403):
            raise XError(f"X refused the request (HTTP {r.status_code}: {_title(r)}). "
                         "Check the Bearer token and that the app has API credits.")
        if r.status_code == 402:
            raise XError("X API credits exhausted (HTTP 402). Add credits in the X "
                         "Developer Console.")
        if r.status_code >= 400:
            raise XError(f"X API error HTTP {r.status_code}: {_title(r)}")
        data: dict[str, Any] = r.json()
        if cache_seconds:
            await self._redis.set(cache_key, json.dumps(data), ex=cache_seconds)
        return data, False

    def _note_rate_limit(self, path: str, r: httpx.Response) -> None:
        rem, lim = r.headers.get("x-rate-limit-remaining"), r.headers.get("x-rate-limit-limit")
        if rem is not None:
            self.last_rate_limit[path.split("/by/")[0]] = {
                "remaining": int(rem) if rem.isdigit() else rem,
                "limit": int(lim) if lim and lim.isdigit() else lim,
                "reset": r.headers.get("x-rate-limit-reset")}

    # ------------------------------------------------------------------ endpoints
    async def search_recent(self, query: str, *, max_results: int = 15,
                            since_id: str | None = None) -> dict[str, Any]:
        """Recent search (last 7 days). Billed per post returned (and per user expanded)."""
        if len(query) > 512:
            raise XError("query longer than 512 characters")
        n = max(10, min(100, max_results))
        params: dict[str, Any] = {"query": query, "max_results": n,
                                  "tweet.fields": TWEET_FIELDS, "expansions": "author_id",
                                  "user.fields": USER_FIELDS}
        if since_id:
            params["since_id"] = since_id
        data, _ = await self._get("/2/tweets/search/recent", params,
                                  max_cost=n * (PRICE_POST_READ + PRICE_USER_READ))
        posts = len(data.get("data") or [])
        users = len((data.get("includes") or {}).get("users") or [])
        await self._charge(posts * PRICE_POST_READ + users * PRICE_USER_READ)
        return data

    async def trends(self, woeid: int, *, max_trends: int = 30) -> dict[str, Any]:
        data, cached = await self._get(f"/2/trends/by/woeid/{int(woeid)}",
                                       {"max_trends": max(1, min(50, max_trends)),
                                        "trend.fields": "trend_name,tweet_count"},
                                       max_cost=PRICE_TRENDS, cache_seconds=1800)
        if not cached:
            await self._charge(PRICE_TRENDS)
        return data

    async def user(self, username: str) -> dict[str, Any]:
        data, cached = await self._get(f"/2/users/by/username/{username}",
                                       {"user.fields": USER_FIELDS}, max_cost=PRICE_USER_READ,
                                       cache_seconds=86400)
        if not cached:
            await self._charge(PRICE_USER_READ)
        return data


def _title(r: httpx.Response) -> str:
    try:
        body = r.json()
    except ValueError:
        return "no details"
    t = body.get("title") or body.get("detail") or (body.get("errors") or [{}])[0].get("message")
    return str(t)[:160] if t else "no details"
