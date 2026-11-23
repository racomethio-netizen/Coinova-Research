"""One place to create Redis clients with explicit, consistent timeouts.

``socket_timeout`` must exceed any blocking read (XREADGROUP block) or idle waits raise
TimeoutError; ``health_check_interval`` revives connections dropped by the server.
"""

from __future__ import annotations

from typing import Any

import redis.asyncio as aioredis

SOCKET_TIMEOUT = 30.0
MAX_BLOCK_MS = 10_000  # blocking reads must stay well below SOCKET_TIMEOUT


# Render's free Key Value allows 50 connections in total. A deploy briefly runs the old
# and the new process side by side (2 x 14 workers + the API), which reached 49 on
# 2026-11-23 and caused connection timeouts. Each client therefore keeps a small BLOCKING
# pool: a burst waits up to POOL_WAIT seconds for a free connection instead of opening
# more.
WORKER_MAX_CONNECTIONS = 3
POOL_WAIT = 5.0


def connect(url: str, *, max_connections: int = WORKER_MAX_CONNECTIONS) -> Any:
    pool = aioredis.BlockingConnectionPool.from_url(
        url, max_connections=max_connections, timeout=POOL_WAIT,
        socket_timeout=SOCKET_TIMEOUT, socket_connect_timeout=5,
        health_check_interval=30, retry_on_timeout=False)
    return aioredis.Redis(connection_pool=pool)
