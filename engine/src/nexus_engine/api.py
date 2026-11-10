"""FastAPI surface (ADR-001): /health for the platform, and token-protected
/internal/* for operators. No user data; never exposed to browsers or apps."""

from __future__ import annotations

import hmac
from typing import Annotated, Any

from fastapi import Depends, FastAPI, Header, HTTPException

from . import __version__
from .config import Settings, get_settings
from .db import connect


def require_internal_token(
    settings: Annotated[Settings, Depends(get_settings)],
    authorization: Annotated[str | None, Header()] = None,
) -> None:
    token = settings.engine_internal_token
    if token is None:
        raise HTTPException(status_code=404)  # internal API disabled
    supplied = authorization[7:] if authorization and authorization.lower().startswith("bearer ") else ""
    if not hmac.compare_digest(supplied.encode(), token.get_secret_value().encode()):
        raise HTTPException(status_code=401, detail="unauthorized")


def create_app() -> FastAPI:
    app = FastAPI(title="NEXUS engine", version=__version__, docs_url=None, redoc_url=None, openapi_url=None)

    @app.get("/health")
    def health(settings: Annotated[Settings, Depends(get_settings)]) -> dict[str, str]:
        if settings.database_url is None:
            return {"status": "degraded", "db": "not_configured", "version": __version__}
        try:
            with connect(settings) as conn:
                conn.execute("select 1")
        except Exception:  # noqa: BLE001 - health reports, never raises
            return {"status": "degraded", "db": "error", "version": __version__}
        return {"status": "ok", "db": "ok", "version": __version__}

    @app.get("/internal/status", dependencies=[Depends(require_internal_token)])
    def status(settings: Annotated[Settings, Depends(get_settings)]) -> dict[str, Any]:
        with connect(settings) as conn:
            beats = conn.execute("select component, last_seen, details from public.engine_heartbeats order by component").fetchall()
            latest = conn.execute(
                "select p.symbol, c.timeframe, max(c.open_time) from public.market_candles c "
                "join public.market_pairs p on p.id = c.pair_id group by p.symbol, c.timeframe order by 1, 2"
            ).fetchall()
            gaps = conn.execute("select count(*) from public.candle_gaps where status = 'open'").fetchone()
        return {
            "heartbeats": [{"component": b[0], "last_seen": b[1].isoformat(), "details": b[2]} for b in beats],
            "latest_candles": [{"symbol": r[0], "timeframe": r[1], "open_time": r[2].isoformat()} for r in latest],
            "open_gaps": gaps[0] if gaps else 0,
            "gates": {"signals_visibility": settings.signals_visibility, "live_execution_enabled": False},
        }

    return app
