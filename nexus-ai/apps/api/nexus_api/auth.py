"""Authentication routes: login (rate limited, lockout), logout, me, TOTP setup."""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from nexus_common.timeutil import utcnow
from nexus_config import AppEnv, Settings
from nexus_db import models, repo
from nexus_security import totp
from nexus_security.passwords import DUMMY_HASH, verify_password
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from nexus_api.deps import (
    SESSION_COOKIE,
    SESSION_IDLE_SECONDS,
    Viewer,
    audit_ip,
    client_ip,
    create_session,
    csrf_guard,
    current_viewer,
    db,
    destroy_session,
    redis_dep,
    settings_dep,
)

router = APIRouter(prefix="/api/v1/auth", tags=["auth"], dependencies=[Depends(csrf_guard)])
MAX_FAILED = 5
LOCKOUT = timedelta(minutes=15)
IP_LIMIT = 20  # login attempts per IP per 15 minutes


class LoginBody(BaseModel):
    email: str = Field(min_length=3, max_length=320)
    password: str = Field(min_length=1, max_length=256)
    totp: str | None = Field(default=None, max_length=6)


class TotpBody(BaseModel):
    code: str = Field(min_length=6, max_length=6)


def _cipher(settings: Settings) -> Any:
    from nexus_security import CredentialCipher, KeyRing

    if settings.credential_encryption_keys is None:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE,
                            "encryption keys not configured; TOTP unavailable")
    return CredentialCipher(KeyRing.parse(settings.credential_encryption_keys.get_secret_value()))


def totp_secret(user: models.User, settings: Settings) -> str | None:
    if user.totp_secret_encrypted is None:
        return None
    result: str = _cipher(settings).decrypt(
        user.totp_secret_encrypted, context={"user_id": str(user.id), "field": "totp"})
    return result


async def check_totp(viewer: Viewer, code: str | None, settings: Settings, redis: Any) -> None:
    """Step-up verification for sensitive actions (replay-protected)."""
    secret = totp_secret(viewer.user, settings)
    if secret is None:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "enable two-factor authentication first")
    step = totp.verify(secret, code or "")
    if step is None:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "invalid two-factor code")
    if not await redis.set(f"nexus:totp_used:{viewer.user.id}:{step}", "1", nx=True, ex=120):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "two-factor code already used")


@router.post("/login")
async def login(body: LoginBody, request: Request, response: Response,
                s: Session = Depends(db), redis: Any = Depends(redis_dep),
                settings: Settings = Depends(settings_dep)) -> dict[str, Any]:
    ip = audit_ip(request)
    rl_ip = client_ip(request)
    rl_key = f"nexus:rl:login:{rl_ip}"
    attempts = await redis.incr(rl_key)
    if attempts == 1:
        await redis.expire(rl_key, 900)
    if attempts > IP_LIMIT:
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "too many attempts; try later")
    email = body.email.strip().lower()
    user = s.scalar(select(models.User).where(models.User.email == email))
    generic = HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid email or password")
    if user is None:
        verify_password(body.password, DUMMY_HASH)  # equalize timing
        raise generic
    now = utcnow()
    if user.locked_until and user.locked_until > now:
        raise HTTPException(status.HTTP_423_LOCKED, "account temporarily locked")
    if not user.is_active or not verify_password(body.password, user.password_hash):
        user.failed_login_count += 1
        if user.failed_login_count >= MAX_FAILED:
            user.locked_until, user.failed_login_count = now + LOCKOUT, 0
            repo.record_audit(s, actor="system:auth", action="account_locked", user_id=user.id,
                              ip_address=ip)
        repo.record_audit(s, actor="anonymous", action="login_failed", user_id=user.id,
                          ip_address=ip)
        s.commit()
        raise generic
    secret = totp_secret(user, settings)
    if secret is not None and totp.verify(secret, body.totp or "") is None:
        repo.record_audit(s, actor="anonymous", action="login_totp_failed", user_id=user.id,
                          ip_address=ip)
        s.commit()
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "two-factor code required")
    user.failed_login_count, user.locked_until, user.last_login_at = 0, None, now
    repo.record_audit(s, actor=f"user:{user.id}", action="login", user_id=user.id, ip_address=ip)
    token = await create_session(redis, user.id)
    response.set_cookie(SESSION_COOKIE, token, httponly=True, samesite="strict",
                        secure=settings.app_env in (AppEnv.PRODUCTION, AppEnv.STAGING),
                        max_age=SESSION_IDLE_SECONDS, path="/")
    return {"user": {"id": user.id, "email": user.email, "role": user.role,
                     "totp_enabled": secret is not None}}


@router.post("/logout")
async def logout(request: Request, response: Response,
                 redis: Any = Depends(redis_dep)) -> dict[str, bool]:
    await destroy_session(redis, request.cookies.get(SESSION_COOKIE))
    response.delete_cookie(SESSION_COOKIE, path="/")
    return {"ok": True}


@router.get("/me")
async def me(viewer: Viewer = Depends(current_viewer)) -> dict[str, Any]:
    u = viewer.user
    return {"id": u.id, "email": u.email, "role": u.role,
            "totp_enabled": u.totp_secret_encrypted is not None}


@router.post("/totp/setup")
async def totp_setup(viewer: Viewer = Depends(current_viewer),
                     s: Session = Depends(db), redis: Any = Depends(redis_dep),
                     settings: Settings = Depends(settings_dep)) -> dict[str, str]:
    if viewer.user.totp_secret_encrypted is not None:
        raise HTTPException(status.HTTP_409_CONFLICT, "two-factor already enabled")
    secret = totp.new_secret()
    _cipher(settings)  # fail early if encryption isn't configured
    await redis.set(f"nexus:totp_pending:{viewer.user.id}", secret, ex=600)
    return {"secret": secret, "uri": totp.provisioning_uri(secret, viewer.user.email)}


@router.post("/totp/enable")
async def totp_enable(body: TotpBody, viewer: Viewer = Depends(current_viewer),
                      s: Session = Depends(db), redis: Any = Depends(redis_dep),
                      settings: Settings = Depends(settings_dep)) -> dict[str, bool]:
    pending = await redis.get(f"nexus:totp_pending:{viewer.user.id}")
    if pending is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "start setup first")
    secret = pending.decode() if isinstance(pending, bytes) else pending
    if totp.verify(secret, body.code) is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "invalid code")
    user = s.get(models.User, viewer.user.id)
    assert user is not None
    user.totp_secret_encrypted = _cipher(settings).encrypt(
        secret, context={"user_id": str(user.id), "field": "totp"})
    repo.record_audit(s, actor=viewer.actor, action="totp_enabled", user_id=user.id)
    await redis.delete(f"nexus:totp_pending:{viewer.user.id}")
    return {"ok": True}
