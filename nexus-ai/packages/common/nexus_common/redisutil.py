"""One place to create Redis clients with explicit, consistent timeouts.

``socket_timeout`` must exceed any blocking read (XREADGROUP block) or idle waits raise
TimeoutError; ``health_check_interval`` revives connections dropped by the server.
"""

from __future__ import annotations

from typing import Any

import redis.asyncio as aioredis

SOCKET_TIMEOUT = 30.0
MAX_BLOCK_MS = 10_000  # blocking reads must stay well below SOCKET_TIMEOUT


def connect(url: str) -> Any:
    return aioredis.from_url(url, socket_timeout=SOCKET_TIMEOUT, socket_connect_timeout=5,
                             health_check_interval=30, retry_on_timeout=False)
