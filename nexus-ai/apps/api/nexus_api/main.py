"""NEXUS AI control-plane API (FastAPI). Run: ``uvicorn --factory nexus_api.main:create_app``.

Phase 1 exposes only health and a non-secret configuration summary. Authentication
(Phase 13 hardening, basic auth earlier) is required before any endpoint that reads
account data or changes state is added.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, Response, status
from nexus_common.logging import configure_logging, get_logger
from nexus_common.redisutil import connect as connect_redis
from nexus_common.timeutil import utcnow
from nexus_config import Settings, get_settings
from nexus_db import make_engine, make_session_factory, ping
from sqlalchemy import Engine

from nexus_api import __version__

log = get_logger("nexus.api")


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    configure_logging(settings.log_level, json=settings.log_json)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        app.state.engine = make_engine(settings.database_url.get_secret_value())
        app.state.factory = make_session_factory(app.state.engine)
        app.state.redis = connect_redis(settings.redis_url.get_secret_value())
        log.info("api_started", **settings.safe_summary())
        try:
            yield
        finally:
            await app.state.redis.aclose()
            app.state.engine.dispose()
            log.info("api_stopped")

    app = FastAPI(
        title="NEXUS AI",
        version=__version__,
        lifespan=lifespan,
        docs_url=None if settings.app_env.value == "production" else "/docs",
        redoc_url=None,
        openapi_url=None if settings.app_env.value == "production" else "/openapi.json",
    )

    app.state.settings = settings
    from nexus_api.auth import router as auth_router
    from nexus_api.routes import router as api_router

    app.include_router(auth_router)
    app.include_router(api_router)

    @app.middleware("http")
    async def security_headers(request: Any, call_next: Any) -> Any:
        response = await call_next(request)
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Referrer-Policy", "no-referrer")
        response.headers.setdefault("Cache-Control", "no-store")
        response.headers.setdefault(
            "Content-Security-Policy",
            "default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' "
            "'unsafe-inline'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; "
            "base-uri 'self'; form-action 'self'")
        response.headers.setdefault("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
        return response

    @app.get("/health/live")
    async def live() -> dict[str, str]:
        """Process is up. Used by Docker to restart a hung container."""
        return {"status": "ok"}

    @app.get("/health/ready")
    async def ready(response: Response) -> dict[str, Any]:
        """Dependencies reachable. Never reports ok if any check fails."""
        checks: dict[str, str] = {}
        engine: Engine = app.state.engine
        try:
            await asyncio.wait_for(asyncio.to_thread(ping, engine), timeout=3)
            checks["database"] = "ok"
        except Exception as exc:
            checks["database"] = f"error: {type(exc).__name__}"
        try:
            await asyncio.wait_for(app.state.redis.ping(), timeout=3)
            checks["redis"] = "ok"
        except Exception as exc:
            checks["redis"] = f"error: {type(exc).__name__}"
        healthy = all(v == "ok" for v in checks.values())
        if not healthy:
            response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
            log.warning("readiness_failed", checks=checks)
        return {"status": "ok" if healthy else "degraded", "checks": checks,
                "checked_at": utcnow().isoformat()}

    @app.get("/api/v1/system/config")
    async def config_summary() -> dict[str, Any]:
        """Non-secret configuration. Trading mode is labeled explicitly everywhere."""
        return settings.safe_summary()

    if settings.dashboard_dir:
        # Static Next.js export served same-origin (Nginx does this in production).
        from fastapi.staticfiles import StaticFiles

        app.mount("/", StaticFiles(directory=settings.dashboard_dir, html=True), name="dashboard")
    return app

