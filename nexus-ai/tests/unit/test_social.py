"""X sentiment module: read-only client, budget, rate limits, analysis and block-only gate."""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

import httpx
import pytest
from nexus_config import Settings
from nexus_social import analysis
from nexus_social.client import BudgetExceeded, RateLimited, XClient, XError
from nexus_social.quality import assess, dedupe
from nexus_social.service import RISK, SNAPSHOT, STATUS, refresh_once
from nexus_social.text import detect_coins, detect_events, search_query, sentiment

NOW = datetime(2026, 11, 21, 12, 0, tzinfo=UTC)
TOKEN = "AAAA-SECRET-BEARER-TOKEN"


class FakeRedis:
    def __init__(self) -> None:
        self.kv: dict[str, Any] = {}
        self.h: dict[str, dict[str, str]] = {}
        self.lists: dict[str, list[str]] = {}
        self.sets: dict[str, set[str]] = {}
        self.stream: list[dict[str, Any]] = []

    async def get(self, k: str) -> Any:
        return self.kv.get(k)

    async def set(self, k: str, v: Any, ex: int | None = None, **_: Any) -> bool:
        self.kv[k] = v
        return True

    async def incrbyfloat(self, k: str, v: float) -> float:
        self.kv[k] = str(float(self.kv.get(k) or 0) + v)
        return float(self.kv[k])

    async def expire(self, k: str, s: int) -> bool:
        return True

    async def hset(self, k: str, f: str, v: str) -> None:
        self.h.setdefault(k, {})[f] = v

    async def hgetall(self, k: str) -> dict[str, str]:
        return dict(self.h.get(k, {}))

    async def hdel(self, k: str, f: str) -> None:
        self.h.get(k, {}).pop(f, None)

    async def rpush(self, k: str, v: str) -> None:
        self.lists.setdefault(k, []).append(v)

    async def ltrim(self, k: str, a: int, b: int) -> None:
        self.lists[k] = self.lists.get(k, [])[a:]

    async def lrange(self, k: str, a: int, b: int) -> list[str]:
        return self.lists.get(k, [])[a:]

    async def sadd(self, k: str, v: str) -> None:
        self.sets.setdefault(k, set()).add(v)

    async def sismember(self, k: str, v: str) -> bool:
        return v in self.sets.get(k, set())

    async def xadd(self, stream: str, fields: dict[str, Any], **_: Any) -> None:
        self.stream.append(fields)


def iso(dt: datetime) -> str:
    return dt.isoformat().replace("+00:00", "Z")


def user(uid: str, name: str, followers: int = 50_000, age_days: int = 2000,
         following: int = 300, verified: bool = False) -> dict[str, Any]:
    return {"id": uid, "username": name, "name": name, "verified": verified,
            "created_at": iso(NOW - timedelta(days=age_days)),
            "public_metrics": {"followers_count": followers, "following_count": following,
                               "tweet_count": 5000, "listed_count": 100},
            "profile_image_url": "https://pbs.twimg.com/profile_images/1/x.jpg"}


def post(pid: str, uid: str, text: str, hours_ago: float = 1, likes: int = 10) -> dict[str, Any]:
    return {"id": pid, "author_id": uid, "text": text,
            "created_at": iso(NOW - timedelta(hours=hours_ago)),
            "public_metrics": {"like_count": likes, "retweet_count": 1, "reply_count": 0,
                               "quote_count": 0}}


def client(handler: Any, redis: FakeRedis, budget: str = "1.00") -> XClient:
    return XClient(TOKEN, redis, daily_budget=Decimal(budget),
                   transport=httpx.MockTransport(handler))


# ------------------------------------------------------------------ client
def test_client_only_reads_allowlisted_endpoints_with_get() -> None:
    seen: list[httpx.Request] = []

    def handler(req: httpx.Request) -> httpx.Response:
        seen.append(req)
        return httpx.Response(200, json={"data": [], "meta": {"result_count": 0}})

    async def go() -> None:
        r = FakeRedis()
        c = client(handler, r)
        await c.search_recent("($BTC) lang:en")
        with pytest.raises(XError, match="not allowed"):
            await c._get("/2/tweets", {}, max_cost=Decimal(0))       # post creation path
        with pytest.raises(XError, match="not allowed"):
            await c._get("/2/users/1/likes", {}, max_cost=Decimal(0))
        await c.close()
    asyncio.run(go())
    assert [q.method for q in seen] == ["GET"]
    assert seen[0].url.path == "/2/tweets/search/recent"
    assert seen[0].headers["authorization"] == f"Bearer {TOKEN}"
    for name in ("post", "create", "like", "follow", "delete", "send", "retweet"):
        assert not any(name in m for m in dir(XClient) if not m.startswith("_"))


def test_budget_refuses_before_calling_and_charges_actual_reads() -> None:
    calls = 0

    def handler(req: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(200, json={
            "data": [{"id": str(i), "text": "x"} for i in range(10)],
            "includes": {"users": [{"id": "u1"}, {"id": "u2"}]}})

    async def go() -> None:
        r = FakeRedis()
        c = client(handler, r, budget="0.25")
        await c.search_recent("q", max_results=10)          # max cost 0.15 <= 0.25
        u = await c.usage()
        assert u.spent_usd == Decimal("0.07")               # 10 posts x .005 + 2 users x .01
        await c.search_recent("q", max_results=10)          # 0.07 + 0.15 <= 0.25
        with pytest.raises(BudgetExceeded):
            await c.search_recent("q", max_results=10)      # 0.14 + 0.15 > 0.25
        await c.close()
    asyncio.run(go())
    assert calls == 2


def test_429_pauses_all_calls_until_reset_and_errors_hide_the_token() -> None:
    calls = 0
    reset = int((datetime.now(UTC) + timedelta(minutes=10)).timestamp())

    def handler(req: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(429, headers={"x-rate-limit-reset": str(reset),
                                            "x-rate-limit-remaining": "0"},
                              json={"title": "Too Many Requests"})

    async def go() -> None:
        r = FakeRedis()
        c = client(handler, r)
        with pytest.raises(RateLimited):
            await c.search_recent("q")
        with pytest.raises(RateLimited) as e:
            await c.search_recent("q")
        assert TOKEN not in str(e.value) and TOKEN not in repr(c)
        await c.close()
    asyncio.run(go())
    assert calls == 1


def test_auth_failure_and_cached_trends_are_not_charged_twice() -> None:
    def handler(req: httpx.Request) -> httpx.Response:
        if "trends" in req.url.path:
            return httpx.Response(200, json={"data": [{"trend_name": "#Bitcoin"}]})
        return httpx.Response(401, json={"title": "Unauthorized"})

    async def go() -> None:
        r = FakeRedis()
        c = client(handler, r)
        with pytest.raises(XError, match="HTTP 401") as e:
            await c.search_recent("q")
        assert TOKEN not in str(e.value)
        await c.trends(1)
        await c.trends(1)
        assert (await c.usage()).spent_usd == Decimal("0.01")
        await c.close()
    asyncio.run(go())


# ------------------------------------------------------------------ text and quality
def test_coin_detection_ignores_ambiguous_plain_words() -> None:
    coins = ["BTC", "ETH", "SOL", "XRP"]
    assert detect_coins("Bitcoin and $SOL ripping", coins) == {"BTC", "SOL"}
    assert detect_coins("a link to the sol festival", coins) == set()   # plain ambiguous words
    assert detect_coins("Ripple wins case, #XRP up", coins) == {"XRP"}
    q = search_query(coins)
    assert "$BTC" in q and "bitcoin" in q and " sol " not in q and len(q) <= 512


def test_sentiment_lexicon_with_negation_and_emoji() -> None:
    assert sentiment("BTC breakout, very bullish 🚀")[0] > 0.5
    assert sentiment("ETH crash, exchange hacked")[0] < -0.5
    assert sentiment("this is not bullish at all")[0] < 0
    assert sentiment("weather is nice today") == (0.0, 0)


def test_event_detection() -> None:
    codes = lambda t: {e.code for e in detect_events(t)}  # noqa: E731
    assert "hack" in codes("BREAKING: exchange hacked, $40M drained")
    assert "breaking" in codes("BREAKING: exchange hacked, $40M drained")
    assert "regulatory" in codes("SEC sues exchange")
    assert "whale" in codes("🚨 1,500 #BTC transferred from unknown wallet to Binance")
    assert "manipulation" in codes("rumor: insider pump and dump planned")


def test_spam_bots_and_trusted_accounts() -> None:
    spam = assess(post("1", "u", "Giveaway! DM me for 100x signals $BTC"), user("u", "x"),
                  NOW, set())
    assert spam.spam and spam.excluded
    bot = assess(post("2", "b", "BTC up"), user("b", "crypto84736251", followers=3,
                                                 following=900, age_days=5), NOW, set())
    assert bot.bot_score >= 0.6 and bot.excluded and bot.credibility < 0.1
    trusted = assess(post("3", "w", "1,000 #BTC moved"), user("w", "whale_alert"), NOW,
                     {"whale_alert"})
    assert trusted.trusted and trusted.credibility == 0.95 and not trusted.excluded


def test_duplicate_and_near_duplicate_posts() -> None:
    texts = ["BTC to 200k soon!! https://t.co/a", "btc to 200k soon https://t.co/b",
             "ETH looks strong today", "BTC to 200k soon!! @friend"]
    d = dedupe(texts)
    assert d[0] is None and d[1] == 0 and d[2] is None and d[3] == 0


# ------------------------------------------------------------------ analysis
def _enriched(posts: list[dict[str, Any]], users: list[dict[str, Any]],
              trusted: set[str] | None = None) -> list[dict[str, Any]]:
    return analysis.enrich(posts, {u["id"]: u for u in users}, ["BTC", "ETH"],
                           trusted or set(), NOW)


def test_coin_sentiment_score_range_confidence_and_exclusions() -> None:
    users = [user(f"u{i}", f"trader{i}") for i in range(8)] + [
        user("bot", "pump84736251", followers=2, following=900, age_days=3)]
    phrases = ["Bitcoin rally looks strong", "very bullish on bitcoin here",
               "$BTC breakout confirmed", "bitcoin inflows keep rising, bullish",
               "BTC surge to a new ath", "accumulating more bitcoin, uptrend intact",
               "Bitcoin bounce from support, strong", "$BTC buying pressure, rally on"]
    posts = [post(str(i), f"u{i}", phrases[i], hours_ago=i) for i in range(8)]
    posts += [post("90", "bot", "$BTC crash dump scam", 0.5),
              post("91", "u1", "Giveaway DM me $BTC crash", 0.5)]
    s = analysis.coin_sentiment("BTC", _enriched(posts, users), NOW)
    assert 50 <= s["score"] <= 100 and s["label"] in ("bullish", "very bullish")
    assert s["excluded_spam_bot"] == 2 and s["posts_used"] == 8
    assert 0 < s["confidence"] <= 1 and s["unique_authors"] == 8
    empty = analysis.coin_sentiment("ETH", _enriched(posts, users), NOW)
    assert empty["score"] is None and empty["label"] == "insufficient data"
    assert any("usable posts" in w for w in empty["warnings"])


def test_single_low_credibility_rumor_is_unconfirmed_and_never_blocks() -> None:
    users = [user("n", "newacct1", followers=15, age_days=40, following=100)]
    posts = [post("1", "n", "BREAKING: Binance hacked, $BTC withdrawals halted!!")]
    alerts = analysis.news_alerts(_enriched(posts, users), NOW)
    hack = next(a for a in alerts if a["category"] == "hack")
    assert hack["status"] == "unconfirmed" and hack["fake_news_risk"] in ("high", "medium")
    assert any("rumor" in w for w in hack["warnings"])
    assert analysis.entry_blocks(alerts, NOW) == {}


def test_confirmed_negative_high_impact_news_blocks_only_that_coin() -> None:
    users = [user("a", "reporter_a", followers=400_000, verified=True),
             user("b", "reporter_b", followers=250_000, verified=True)]
    posts = [post("1", "a", "Exchange hacked: $ETH drained from hot wallet", 1),
             post("2", "b", "Confirmed: hot wallet exploit, ETH stolen", 0.5)]
    alerts = analysis.news_alerts(_enriched(posts, users), NOW)
    blocks = analysis.entry_blocks(alerts, NOW)
    assert set(blocks) == {"ETH"} and "confirmed" in blocks["ETH"]
    # Positive or manipulation news never blocks, and nothing ever creates an entry.
    good = [post("3", "a", "SEC approved the $ETH ETF, bullish", 1),
            post("4", "b", "ETH ETF approval confirmed, rally", 1)]
    assert analysis.entry_blocks(analysis.news_alerts(_enriched(good, users), NOW), NOW) == {}


def test_combine_flags_divergence_and_crowded_longs() -> None:
    sent = {"score": 70, "confidence": 0.8}
    market = {"direction": "short", "hypotheses": [{"score": 65}], "funding_rate": 0.001,
              "atr_percentile": 0.95, "order_book": {"imbalance": -0.2}}
    c = analysis.combine(sent, market)
    assert c["alignment"].startswith("divergent")
    assert any("crowded longs" in r for r in c["risk_flags"])
    assert any("volatility" in r for r in c["risk_flags"]) and "never" in c["policy"]
    assert analysis.combine(sent, None)["available"] is False


# ------------------------------------------------------------------ service
def test_refresh_once_end_to_end_with_mock_x_api() -> None:
    users = [user("a", "reporter_a", followers=400_000, verified=True),
             user("b", "reporter_b", followers=250_000, verified=True),
             user("w", "whale_alert")]
    payload = {"data": [post("1", "a", "Exchange hacked: $ETH drained", 1),
                        post("2", "b", "Hot wallet exploit confirmed, ETH stolen", 0.5),
                        post("3", "w", "🚨 2,000 #BTC transferred to Coinbase", 0.2)],
               "includes": {"users": users}, "meta": {"newest_id": "3"}}
    queries: list[str] = []

    def handler(req: httpx.Request) -> httpx.Response:
        if "trends" in req.url.path:
            return httpx.Response(200, json={"data": [{"trend_name": "#Bitcoin",
                                                       "tweet_count": 9000},
                                                      {"trend_name": "#Football"}]})
        queries.append(req.url.params["query"])
        return httpx.Response(200, json=payload)

    settings = Settings(database_url="postgresql+psycopg://a:b@h/d", redis_url="redis://h",
                        x_coins="BTC,ETH", x_watch_accounts="whale_alert")

    async def go() -> FakeRedis:
        r = FakeRedis()
        c = client(handler, r, budget="5")
        import nexus_social.service as svc
        orig = svc.utcnow
        svc.utcnow = lambda: NOW  # type: ignore[assignment]
        try:
            await refresh_once(r, c, settings)
            await refresh_once(r, c, settings)          # second run uses since_id
        finally:
            svc.utcnow = orig  # type: ignore[assignment]
        await c.close()
        return r
    r = asyncio.run(go())
    assert len(queries) == 4 and "from:whale_alert" in queries[1]
    snap = json.loads(r.kv[SNAPSHOT])
    assert {c["coin"] for c in snap["coins"]} == {"BTC", "ETH"}
    assert snap["trending"]["x_trends"] == [{"trend_name": "#Bitcoin", "tweet_count": 9000}]
    assert json.loads(r.kv[RISK])["blocks"].keys() == {"ETH"}
    assert json.loads(r.kv[STATUS])["ok"] is True
    assert any(json.loads(e["data"])["type"] == "x_alert" for e in r.stream)
    assert len(r.stream) == len({e["data"] for e in r.stream})    # notified once each


def test_trader_x_gate_blocks_only_listed_coins_and_ignores_missing_data() -> None:
    from nexus_execution.trader import TradingEngine

    eng = TradingEngine.__new__(TradingEngine)
    eng.redis = FakeRedis()  # type: ignore[assignment]

    async def go() -> tuple[Any, Any, Any]:
        none = await eng._x_block("ETH/USDT")
        await eng.redis.set(RISK, json.dumps({"blocks": {"ETH": "X: confirmed hack"}}))
        return none, await eng._x_block("ETH/USDT"), await eng._x_block("BTC/USDT")
    none, eth, btc = asyncio.run(go())
    assert none is None and eth == "X: confirmed hack" and btc is None


def test_x_alert_telegram_format() -> None:
    from nexus_notify.formatting import format_event

    text = format_event({"type": "x_alert", "coin": "ETH", "label": "Hack / exploit",
                         "status": "confirmed", "credible_sources": 2, "headline": "<b>x</b>",
                         "author": "a", "url": "https://x.com/a/status/1", "direction": -1})
    assert text and "never trades" in text and "&lt;b&gt;" in text and "▼" in text
