"""Redis clients: connection set-up is retried with back-off (a deploy briefly exceeds the
Key Value connection limit), commands are never retried, and the scheme's class is kept."""

from __future__ import annotations

import asyncio

import pytest
from nexus_common import redisutil
from redis.asyncio.connection import Connection, SSLConnection
from redis.exceptions import AuthenticationError
from redis.exceptions import ConnectionError as RedisConnectionError
from redis.exceptions import TimeoutError as RedisTimeoutError


def test_pool_uses_retrying_connections_of_the_scheme_class():
    plain = redisutil.connect("redis://localhost:6379/0")
    tls = redisutil.connect("rediss://example.com:6380/0")
    assert plain.connection_pool.connection_class is redisutil.RetryConnection
    assert tls.connection_pool.connection_class is redisutil.RetrySSLConnection
    assert issubclass(redisutil.RetrySSLConnection, SSLConnection)
    assert plain.connection_pool.timeout == redisutil.POOL_WAIT
    assert plain.connection_pool.max_connections == redisutil.WORKER_MAX_CONNECTIONS
    conn = plain.connection_pool.make_connection()
    assert conn.retry.get_retries() == 0          # commands: never sent twice


@pytest.fixture
def no_sleep(monkeypatch: pytest.MonkeyPatch) -> list[float]:
    waits: list[float] = []

    async def fake_sleep(s: float) -> None:
        waits.append(s)

    monkeypatch.setattr("redis.asyncio.retry.sleep", fake_sleep)
    return waits


def flaky(monkeypatch: pytest.MonkeyPatch, errors: list[Exception]) -> list[int]:
    calls = [0]

    async def connect(self: Connection) -> None:
        calls[0] += 1
        if errors:
            raise errors.pop(0)

    monkeypatch.setattr(Connection, "connect", connect)
    return calls


def test_connect_waits_out_a_short_burst(monkeypatch, no_sleep):
    calls = flaky(monkeypatch, [RedisTimeoutError("Timeout connecting to server"),
                                RedisConnectionError("max number of clients reached"),
                                RedisConnectionError("Error 104 connecting")])
    conn = redisutil.RetryConnection(host="localhost")
    asyncio.run(conn.connect())
    assert calls[0] == 4 and len(no_sleep) == 3
    assert all(0 <= w <= redisutil.CONNECT_BACKOFF_CAP for w in no_sleep)


def test_connect_gives_up_after_the_last_attempt(monkeypatch, no_sleep):
    calls = flaky(monkeypatch, [RedisTimeoutError("t")] * 20)
    with pytest.raises(RedisTimeoutError):
        asyncio.run(redisutil.RetryConnection(host="localhost").connect())
    assert calls[0] == redisutil.CONNECT_ATTEMPTS


def test_wrong_password_is_not_retried(monkeypatch, no_sleep):
    calls = flaky(monkeypatch, [AuthenticationError("invalid password")] * 3)
    with pytest.raises(AuthenticationError):
        asyncio.run(redisutil.RetryConnection(host="localhost").connect())
    assert calls[0] == 1 and no_sleep == []
