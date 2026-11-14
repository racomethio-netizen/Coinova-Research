"""Request dependencies: DB/Redis access, session authentication, CSRF guard."""

from __future__ import annotations

import hashlib
import ipaddress
import json
import secrets
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Any

from fastapi import Depends, HTTPException, Request, status
from nexus_common.timeutil import utcnow
from nexus_config import Settings
from nexus_db import models
from sqlalchemy.orm import Session, sessionmaker

SESSION_COOKIE = "nexus_session"
SESSION_IDLE_SECONDS = 2 * 3600
SESSION_ABSOLUTE_SECONDS = 12 * 3600
CSRF_HEADER = "x-nexus-csrf"


def settings_dep(request: Request) -> Settings:
    s: Settings = request.app.state.settings
    return s


def db(request: Request) -> Iterator[Session]:
    factory: sessionmaker[Session] = request.app.state.factory
    s = factory()
    try:
        yield s
        s.commit()
    except BaseException:
        s.rollback()
        raise
    finally:
        s.close()


def redis_dep(request: Request) -> Any:
    return request.app.state.redis


def _key(token: str) -> str:
    return "nexus:session:" + hashlib.sha256(token.encode()).hexdigest()


async def create_session(redis: Any, user_id: int) -> str:
    token = secrets.token_urlsafe(32)
    now = utcnow().isoformat()
    await redis.set(_key(token), json.dumps({"user_id": user_id, "created": now,
                                             "totp_step": None}), ex=SESSION_IDLE_SECONDS)
    return token


async def destroy_session(redis: Any, token: str | None) -> None:
    if token:
        await redis.delete(_key(token))


@dataclass(frozen=True)
class Viewer:
    user: models.User
    token: str

    @property
    def actor(self) -> str:
        return f"user:{self.user.id}"


async def current_viewer(request: Request, redis: Any = Depends(redis_dep),
                         s: Session = Depends(db)) -> Viewer:
    token = request.cookies.get(SESSION_COOKIE)
    if not token:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "not signed in")
    raw = await redis.get(_key(token))
    if raw is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "session expired")
    data = json.loads(raw)
    created = datetime.fromisoformat(data["created"])
    if (utcnow() - created).total_seconds() > SESSION_ABSOLUTE_SECONDS:
        await redis.delete(_key(token))
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "session expired")
    user = s.get(models.User, data["user_id"])
    if user is None or not user.is_active:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "account disabled")
    await redis.expire(_key(token), SESSION_IDLE_SECONDS)  # sliding idle timeout
    return Viewer(user, token)


async def owner(viewer: Viewer = Depends(current_viewer)) -> Viewer:
    if viewer.user.role != "owner":
        raise HTTPException(status.HTTP_403_FORBIDDEN, "owner only")
    return viewer


def csrf_guard(request: Request, settings: Settings = Depends(settings_dep)) -> None:
    """Mutations must carry a custom header (impossible cross-site without CORS, which is
    disabled) and, when the browser sends an Origin, it must be an allowed origin."""
    if request.method in ("GET", "HEAD", "OPTIONS"):
        return
    if request.headers.get(CSRF_HEADER) != "1":
        raise HTTPException(status.HTTP_403_FORBIDDEN, "missing CSRF header")
    origin = request.headers.get("origin")
    if origin is None:
        return
    host = request.headers.get("host", "")
    allowed = {f"http://{host}", f"https://{host}", *settings.cors_origins}
    if origin not in allowed:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "cross-origin request refused")


def ser(value: Any) -> Any:
    """JSON-safe: Decimal -> string (exact), datetime -> ISO."""
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, ipaddress.IPv4Address | ipaddress.IPv6Address):
        return str(value)  # INET columns come back as ipaddress objects
    if isinstance(value, dict):
        return {k: ser(v) for k, v in value.items()}
    if isinstance(value, list | tuple):
        return [ser(v) for v in value]
    return value


def client_ip(request: Request) -> str:
    return request.client.host if request.client else "unknown"


def audit_ip(request: Request) -> str | None:
    """Client IP for the audit log's INET column; None if not a valid address."""
    try:
        return str(ipaddress.ip_address(client_ip(request)))
    except ValueError:
        return None
