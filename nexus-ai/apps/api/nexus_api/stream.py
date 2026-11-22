"""Owner-only WebSocket push for the dashboard: top of book, kill switch and bot state
every 2 seconds. Read-only; the browser can send nothing that changes state.

Security: the session cookie is checked exactly like HTTP routes, and the Origin header
must be this site (or a configured CORS origin) to stop cross-site WebSocket hijacking.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from nexus_common import events
from nexus_common.timeutil import utcnow

ws_router = APIRouter()
INTERVAL_S = 2.0


def origin_allowed(origin: str | None, host: str | None, allowed: list[str]) -> bool:
    if not origin:
        return False
    if host and origin.rstrip("/") in (f"https://{host}", f"http://{host}"):
        return True
    return origin.rstrip("/") in {o.rstrip("/") for o in allowed}


@ws_router.websocket("/api/v1/ws/stream")
async def stream(ws: WebSocket) -> None:
    from fastapi import HTTPException
    from nexus_common.botstate import get_state
    from nexus_db import make_session_factory
    from nexus_risk.kill_switch import KillSwitch

    from nexus_api.deps import SESSION_COOKIE, viewer_from_token

    app: Any = ws.app
    settings = app.state.settings
    redis = app.state.redis
    if not origin_allowed(ws.headers.get("origin"), ws.headers.get("host"),
                          list(settings.cors_origins)):
        await ws.close(code=1008)
        return
    try:
        factory = make_session_factory(app.state.engine)
        with factory() as s:
            viewer = await viewer_from_token(ws.cookies.get(SESSION_COOKIE), redis, s)
        if viewer.user.role != "owner":
            raise HTTPException(403, "owner only")
    except HTTPException:
        await ws.close(code=1008)
        return
    await ws.accept()
    try:
        while True:
            books = []
            for ex in settings.enabled_exchanges:
                for sym in settings.trading_pairs:
                    raw = await redis.get(events.book_key(ex.value, sym))
                    books.append({"exchange": ex.value, "symbol": sym,
                                  "book": json.loads(raw) if raw else None,
                                  "source": "websocket" if raw else None})
            await ws.send_json({"type": "tick", "at": utcnow().isoformat(), "books": books,
                                "kill_switch": await KillSwitch(redis).is_engaged(),
                                "bot_state": (await get_state(redis)).value})
            await asyncio.sleep(INTERVAL_S)
    except (WebSocketDisconnect, RuntimeError):
        return
