"""Prometheus text exposition for monitoring (Grafana or any Prometheus scraper).

``GET /metrics`` exists only when ``NEXUS_METRICS_TOKEN`` is set, and needs
``Authorization: Bearer <token>``. It exposes operational health only; no balances, keys
or positions.
* process up; database and Redis reachability;
* worker restarts per worker;
* whether the Binance rate-limit circuit is open (spot, futures);
* whether the kill switch is engaged;
* market-data age per traded pair and timeframe (latest CLOSED candle);
* how long ago the trading bot last scanned.
"""

from __future__ import annotations

import asyncio
import json
import secrets
from collections.abc import Iterable
from datetime import datetime
from typing import Any

from fastapi import FastAPI, HTTPException, Request, Response, status
from nexus_common.timeutil import utcnow
from nexus_config import TIMEFRAME_SECONDS, Settings

Sample = tuple[str, dict[str, str], float]


def render(samples: Iterable[Sample], help_text: dict[str, str]) -> str:
    """Prometheus text format (version 0.0.4)."""
    lines: list[str] = []
    seen: set[str] = set()
    for name, labels, value in samples:
        if name not in seen:
            seen.add(name)
            lines.append(f"# HELP {name} {help_text.get(name, name)}")
            lines.append(f"# TYPE {name} gauge")
        lab = ",".join(f'{k}="{v}"' for k, v in sorted(labels.items()))
        lines.append(f"{name}{{{lab}}} {value:g}" if lab else f"{name} {value:g}")
    return "\n".join(lines) + "\n"


HELP = {
    "nexus_up": "API process is serving",
    "nexus_dependency_up": "Dependency reachable (1) or not (0)",
    "nexus_worker_restarts": "Restarts of an inline worker since the process started",
    "nexus_binance_circuit_open": "Binance rate-limit circuit open (1) or closed (0)",
    "nexus_kill_switch_engaged": "Kill switch engaged (1) or not (0)",
    "nexus_market_data_age_seconds": "Seconds since the latest closed candle ended",
    "nexus_bot_last_scan_age_seconds": "Seconds since the trading bot's last scan",
}


async def collect(app: FastAPI, settings: Settings, now: datetime) -> list[Sample]:
    from nexus_common.binance_guard import blocked
    from nexus_db import ping, transaction
    from nexus_risk.kill_switch import KillSwitch
    from sqlalchemy import text

    out: list[Sample] = [("nexus_up", {}, 1.0)]
    try:
        await asyncio.wait_for(asyncio.to_thread(ping, app.state.engine), timeout=3)
        out.append(("nexus_dependency_up", {"dependency": "database"}, 1.0))
        db_ok = True
    except Exception:
        out.append(("nexus_dependency_up", {"dependency": "database"}, 0.0))
        db_ok = False
    redis = app.state.redis
    try:
        await asyncio.wait_for(redis.ping(), timeout=3)
        out.append(("nexus_dependency_up", {"dependency": "redis"}, 1.0))
    except Exception:
        out.append(("nexus_dependency_up", {"dependency": "redis"}, 0.0))
        return out
    workers = getattr(app.state, "workers", None)
    for name, n in sorted((getattr(workers, "restarts", None) or {}).items()):
        out.append(("nexus_worker_restarts", {"worker": name}, float(n)))
    for scope in ("spot", "futures"):
        out.append(("nexus_binance_circuit_open", {"scope": scope},
                    1.0 if await blocked(redis, scope) is not None else 0.0))
    out.append(("nexus_kill_switch_engaged", {},
                1.0 if await KillSwitch(redis).is_engaged() else 0.0))
    raw = await redis.get("nexus:bot:status")
    if raw:
        at = json.loads(raw).get("at")
        if at:
            out.append(("nexus_bot_last_scan_age_seconds", {},
                        (now - datetime.fromisoformat(at)).total_seconds()))
    if db_ok:
        def latest() -> list[Any]:
            with transaction(app.state.factory) as s:
                return list(s.execute(text(
                    "SELECT tp.symbol, c.timeframe, max(c.open_time) FROM market_candles c "
                    "JOIN trading_pairs tp ON tp.id = c.pair_id "
                    "WHERE c.is_closed AND tp.symbol = ANY(:syms) AND c.timeframe = ANY(:tfs) "
                    "GROUP BY tp.symbol, c.timeframe"),
                    {"syms": list(settings.trading_pairs),
                     "tfs": [t.value for t in settings.timeframes]}).all())

        for sym, tf, last in await asyncio.to_thread(latest):
            end = last.timestamp() + TIMEFRAME_SECONDS.get(tf, 0)
            out.append(("nexus_market_data_age_seconds", {"symbol": sym, "timeframe": tf},
                        max(0.0, now.timestamp() - end)))
    return out


def install(app: FastAPI, settings: Settings) -> None:
    if settings.metrics_token is None:
        return
    token = settings.metrics_token.get_secret_value()

    @app.get("/metrics", include_in_schema=False)
    async def metrics(request: Request) -> Response:
        given = request.headers.get("authorization", "")
        if not secrets.compare_digest(given, f"Bearer {token}"):
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "metrics token required")
        body = render(await collect(app, settings, utcnow()), HELP)
        return Response(body, media_type="text/plain; version=0.0.4")
