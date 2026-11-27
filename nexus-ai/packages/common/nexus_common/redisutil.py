"""One place to create Redis clients with explicit, consistent timeouts.

``socket_timeout`` must exceed any blocking read (XREADGROUP block) or idle waits raise
TimeoutError; ``health_check_interval`` revives connections dropped by the server.
"""

from __future__ import annotations

from typing import Any

import redis.asyncio as aioredis
from redis.asyncio.connection import Connection, SSLConnection, UnixDomainSocketConnection
from redis.asyncio.retry import Retry
from redis.backoff import ExponentialWithJitterBackoff
from redis.exceptions import AuthenticationError, AuthorizationError
from redis.exceptions import ConnectionError as RedisConnectionError
from redis.exceptions import TimeoutError as RedisTimeoutError

SOCKET_TIMEOUT = 30.0
MAX_BLOCK_MS = 10_000  # blocking reads must stay well below SOCKET_TIMEOUT


# Render's free Key Value allows 50 connections in total. A deploy briefly runs the old
# and the new process side by side (2 x 17 workers + the API), which reached the limit:
# every deploy logged "Timeout connecting to server" / "No connection available" for a
# few seconds and the workers restarted. Three measures:
# * each client keeps a small BLOCKING pool (a burst waits for a free connection
#   instead of opening more);
# * opening a connection is retried with jittered exponential back-off (about 20 s in
#   all), so a worker waits out the overlap instead of crashing. Only connection set-up
#   is retried, never a command: a command that may have reached the server is never
#   sent twice;
# * the inline workers start one by one (workers.STAGGER_SECONDS).
WORKER_MAX_CONNECTIONS = 3
POOL_WAIT = 30.0           # wait for a free pool slot; covers the connect retries below
CONNECT_ATTEMPTS = 6
CONNECT_BACKOFF_BASE = 0.5
CONNECT_BACKOFF_CAP = 8.0


def connect_retry() -> Retry:
    """A fresh policy per connection attempt (a back-off object may keep state)."""
    return Retry(ExponentialWithJitterBackoff(cap=CONNECT_BACKOFF_CAP,
                                              base=CONNECT_BACKOFF_BASE),
                 CONNECT_ATTEMPTS - 1,
                 supported_errors=(RedisConnectionError, RedisTimeoutError))  # OSError is wrapped


class _RetryConnect:
    """Mixin: retry establishing the connection. The connection's own ``retry`` (used for
    commands) stays at zero retries."""

    async def connect(self) -> None:
        parent = super()
        await connect_retry().call_with_retry(
            lambda: parent.connect(),  # type: ignore[attr-defined]
            lambda _error: self.disconnect(),  # type: ignore[attr-defined]
            is_retryable=lambda error: not isinstance(
                error, AuthenticationError | AuthorizationError))   # wrong password: fail now


class RetryConnection(_RetryConnect, Connection):
    pass


class RetrySSLConnection(_RetryConnect, SSLConnection):
    pass


class RetryUnixConnection(_RetryConnect, UnixDomainSocketConnection):
    pass


_RETRYING: dict[type[Any], type[Any]] = {
    Connection: RetryConnection, SSLConnection: RetrySSLConnection,
    UnixDomainSocketConnection: RetryUnixConnection}


def connect(url: str, *, max_connections: int = WORKER_MAX_CONNECTIONS) -> Any:
    pool = aioredis.BlockingConnectionPool.from_url(
        url, max_connections=max_connections, timeout=POOL_WAIT,
        socket_timeout=SOCKET_TIMEOUT, socket_connect_timeout=5,
        health_check_interval=30, retry_on_timeout=False)
    # from_url picks the class from the scheme (redis://, rediss://, unix://); swap in
    # the retrying subclass of the same class. Connections are created lazily, so none
    # exists yet.
    pool.connection_class = _RETRYING.get(pool.connection_class, pool.connection_class)
    return aioredis.Redis(connection_pool=pool)
