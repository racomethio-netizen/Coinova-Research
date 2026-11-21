import asyncio
from datetime import UTC, datetime, timedelta

from nexus_api import binance_ban
from nexus_api.binance_ban import ban_until

NOW = datetime(2026, 11, 21, 10, 0, tzinfo=UTC)


def test_parses_binance_ban_timestamp_ms():
    text = ('binance {"code":-1003,"msg":"Way too many requests; IP(74.220.51.139) banned '
            'until 1795415938291. Please use the websocket for live updates to avoid bans."}')
    assert ban_until(text, now=NOW) == datetime(2026, 11, 23, 6, 38, 58, 291000, tzinfo=UTC)


def test_rate_limit_without_end_backs_off_five_minutes():
    assert ban_until('{"code":-1003,"msg":"Too many requests"}', now=NOW) == \
        NOW + timedelta(minutes=5)


def test_unrelated_and_expired_errors_are_not_bans():
    assert ban_until("authentication failed (Binance -2015: Invalid API-key)", now=NOW) is None
    assert ban_until("IP banned until 1700000000000", now=NOW) is None  # in the past


def test_absurd_ban_end_is_capped():
    assert ban_until("banned until 9999999999999", now=NOW) == NOW + binance_ban.MAX_BAN


class FakeRedis:
    def __init__(self):
        self.d = {}

    async def get(self, k):
        return self.d.get(k)

    async def set(self, k, v, ex=None):
        self.d[k] = v.encode()


def test_record_then_active_per_environment():
    r = FakeRedis()
    future_ms = int((datetime.now(UTC) + timedelta(hours=2)).timestamp() * 1000)

    async def go():
        until = await binance_ban.record_ban(r, "production", f"banned until {future_ms}")
        assert until is not None
        assert await binance_ban.active_ban(r, "production") == until
        assert await binance_ban.active_ban(r, "demo") is None
        assert await binance_ban.record_ban(r, "production", "network error") is None
    asyncio.run(go())
