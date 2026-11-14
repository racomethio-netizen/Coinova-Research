"""API authentication, authorization, CSRF and sensitive-action tests (real DB + Redis)."""

from __future__ import annotations

import os
from collections.abc import Iterator
from typing import Any

import pytest
import redis as sync_redis
from fastapi.testclient import TestClient
from nexus_api.main import create_app
from nexus_config import LIVE_TRADING_ACK_PHRASE, Settings
from nexus_db import make_session_factory, models, transaction
from nexus_security import generate_key_entry, totp
from nexus_security.passwords import hash_password
from sqlalchemy import Engine, select

from tests.integration.conftest import truncate

pytestmark = [pytest.mark.db, pytest.mark.redis]
PASSWORD = "correct-horse-battery-9"
CSRF = {"x-nexus-csrf": "1"}


@pytest.fixture
def client(migrated_engine: Engine, db_url: str) -> Iterator[TestClient]:
    url = os.environ.get("NEXUS_TEST_REDIS_URL")
    if not url:
        pytest.skip("NEXUS_TEST_REDIS_URL not set")
    sync_redis.from_url(url).flushdb()
    factory = make_session_factory(migrated_engine)
    with transaction(factory) as s:
        s.add(models.User(email="owner@example.test", password_hash=hash_password(PASSWORD),
                          role="owner"))
        s.add(models.User(email="viewer@example.test", password_hash=hash_password(PASSWORD),
                          role="viewer"))
    settings = Settings(database_url=db_url, redis_url=url,
                        credential_encryption_keys=generate_key_entry("k1"))
    with TestClient(create_app(settings)) as c:
        yield c
    sync_redis.from_url(url).flushdb()
    truncate(migrated_engine, "users, audit_logs, exchange_credentials, risk_events, "
                              "ai_models, model_evaluations")
    with migrated_engine.begin() as conn:
        from sqlalchemy import text
        conn.execute(text("UPDATE strategies SET is_enabled=false, parameters='{}'"))


def login(c: TestClient, email: str = "owner@example.test", **kw: Any) -> Any:
    return c.post("/api/v1/auth/login", json={"email": email, "password": PASSWORD, **kw},
                  headers=CSRF)


GET_ROUTES = ["/api/v1/overview", "/api/v1/portfolio", "/api/v1/exchanges", "/api/v1/signals",
              "/api/v1/positions", "/api/v1/orders", "/api/v1/trades", "/api/v1/performance",
              "/api/v1/strategies", "/api/v1/models", "/api/v1/risk", "/api/v1/system",
              "/api/v1/security", "/api/v1/markets/books", "/api/v1/auth/me"]


@pytest.mark.parametrize("path", GET_ROUTES)
def test_every_route_requires_auth(client: TestClient, path: str) -> None:
    assert client.get(path).status_code == 401


def test_all_routes_work_when_signed_in(client: TestClient) -> None:
    assert login(client).status_code == 200
    for path in GET_ROUTES:
        r = client.get(path)
        assert r.status_code == 200, (path, r.text)
    over = client.get("/api/v1/overview").json()
    assert over["mode"] == "paper" and over["config"]["spot_only"] is True


def test_session_cookie_flags_and_logout(client: TestClient) -> None:
    r = login(client)
    cookie = r.headers["set-cookie"].lower()
    assert "httponly" in cookie and "samesite=strict" in cookie
    assert client.post("/api/v1/auth/logout", headers=CSRF).status_code == 200
    assert client.get("/api/v1/auth/me").status_code == 401


def test_csrf_header_and_origin_required(client: TestClient) -> None:
    assert client.post("/api/v1/auth/login",
                       json={"email": "owner@example.test", "password": PASSWORD}).status_code == 403
    r = client.post("/api/v1/auth/login", json={"email": "owner@example.test",
                                                "password": PASSWORD},
                    headers={**CSRF, "origin": "https://evil.example"})
    assert r.status_code == 403
    login(client)
    r = client.post("/api/v1/risk/kill-switch", json={"reason": "csrf test"})
    assert r.status_code == 403


def test_wrong_password_generic_error_and_lockout(client: TestClient) -> None:
    for _ in range(5):
        r = client.post("/api/v1/auth/login", json={"email": "owner@example.test",
                                                    "password": "wrong-password"}, headers=CSRF)
        assert r.status_code == 401 and r.json()["detail"] == "invalid email or password"
    unknown = client.post("/api/v1/auth/login", json={"email": "nobody@example.test",
                                                      "password": "x"}, headers=CSRF)
    assert unknown.json()["detail"] == "invalid email or password"  # no user enumeration
    assert login(client).status_code == 423  # locked even with the right password


def test_ip_rate_limit(client: TestClient) -> None:
    codes = [client.post("/api/v1/auth/login", json={"email": f"x{i}@example.test",
                                                     "password": "x"}, headers=CSRF).status_code
             for i in range(22)]
    assert codes[-1] == 429


def test_viewer_cannot_mutate(client: TestClient) -> None:
    login(client, email="viewer@example.test")
    assert client.post("/api/v1/risk/kill-switch", json={"reason": "viewer try"},
                       headers=CSRF).status_code == 403
    assert client.patch("/api/v1/strategies/momentum", json={"enabled": False},
                        headers=CSRF).status_code == 403
    assert client.get("/api/v1/security").status_code == 403


def test_cannot_enable_unvalidated_strategy(client: TestClient) -> None:
    login(client)
    r = client.patch("/api/v1/strategies/momentum", json={"enabled": True}, headers=CSRF)
    assert r.status_code == 409 and "validation" in r.json()["detail"]
    assert client.patch("/api/v1/strategies/momentum", json={"enabled": False},
                        headers=CSRF).status_code == 200


def _enable_totp(client: TestClient) -> str:
    secret = client.post("/api/v1/auth/totp/setup", headers=CSRF).json()["secret"]
    assert client.post("/api/v1/auth/totp/enable", json={"code": "000000"},
                       headers=CSRF).status_code == 400
    import time
    code = totp._code(secret, int(time.time() // 30))
    assert client.post("/api/v1/auth/totp/enable", json={"code": code},
                       headers=CSRF).status_code == 200
    return secret


def _code(secret: str, offset: int = 0) -> str:
    import time
    return totp._code(secret, int(time.time() // 30) + offset)


def test_kill_switch_engage_easy_clear_needs_totp(client: TestClient) -> None:
    login(client)
    assert client.post("/api/v1/risk/kill-switch", json={"reason": "manual stop"},
                       headers=CSRF).json() == {"engaged": True}
    assert client.get("/api/v1/risk").json()["kill_switch"]["actor"].startswith("user:")
    r = client.post("/api/v1/risk/kill-switch/clear", json={"reason": "resume"}, headers=CSRF)
    assert r.status_code == 403 and "two-factor" in r.json()["detail"]
    secret = _enable_totp(client)
    code = _code(secret, 1)
    ok = client.post("/api/v1/risk/kill-switch/clear", json={"reason": "resume after review",
                                                             "totp": code}, headers=CSRF)
    assert ok.status_code == 200
    replay = client.post("/api/v1/risk/kill-switch/clear", json={"reason": "again please",
                                                                 "totp": code}, headers=CSRF)
    assert replay.status_code == 403 and "already used" in replay.json()["detail"]


def test_login_requires_totp_once_enabled(client: TestClient) -> None:
    login(client)
    secret = _enable_totp(client)
    client.post("/api/v1/auth/logout", headers=CSRF)
    assert login(client).status_code == 401
    assert login(client, totp=_code(secret)).status_code == 200


def test_credentials_encrypted_and_never_returned(client: TestClient,
                                                  migrated_engine: Engine) -> None:
    login(client)
    secret = _enable_totp(client)
    body = {"exchange": "binance", "label": "main", "api_key": "AKIA-PLAINTEXT-KEY-123",
            "api_secret": "SUPER-SECRET-VALUE-456", "withdrawals_disabled_confirmed": False,
            "totp": _code(secret, -1)}
    assert client.post("/api/v1/security/credentials", json=body, headers=CSRF).status_code == 400
    body.update(withdrawals_disabled_confirmed=True, totp=_code(secret, 1))
    r = client.post("/api/v1/security/credentials", json=body, headers=CSRF)
    assert r.status_code == 200, r.text
    listing = client.get("/api/v1/exchanges").text
    assert "PLAINTEXT" not in listing and "SUPER-SECRET" not in listing
    assert r.json()["fingerprint"] in listing
    with transaction(make_session_factory(migrated_engine)) as s:
        cred = s.scalar(select(models.ExchangeCredential))
        assert cred is not None and b"SUPER-SECRET" not in cred.api_secret_encrypted
        actions = s.scalars(select(models.AuditLog.action)).all()
        assert "credential_added" in actions and "totp_enabled" in actions


def test_live_approval_needs_totp_and_exact_phrase(client: TestClient) -> None:
    login(client)
    secret = _enable_totp(client)
    bad = client.post("/api/v1/security/live-approval",
                      json={"approve": True, "acknowledgement": "yes", "totp": _code(secret, -1)},
                      headers=CSRF)
    assert bad.status_code == 400
    ok = client.post("/api/v1/security/live-approval",
                     json={"approve": True, "acknowledgement": LIVE_TRADING_ACK_PHRASE,
                           "totp": _code(secret, 1)}, headers=CSRF)
    assert ok.status_code == 200
    sec = client.get("/api/v1/security").json()
    assert "no owner approval recorded" not in sec["live_trading_blockers"]
    assert sec["live_trading_permitted"] is False  # config gates still block (paper mode)


def test_security_headers(client: TestClient) -> None:
    h = client.get("/health/live").headers
    assert "frame-ancestors 'none'" in h["content-security-policy"]
    assert h["x-frame-options"] == "DENY"
