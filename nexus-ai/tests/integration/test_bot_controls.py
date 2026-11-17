"""Bot START / PAUSE / STOP: state gating, owner-only, audited, fail-closed."""

from __future__ import annotations

import os
from collections.abc import Iterator

import pytest
import redis as sync_redis
from fastapi.testclient import TestClient
from nexus_api.main import create_app
from nexus_config import Settings
from nexus_db import make_session_factory, models, transaction
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
    with transaction(make_session_factory(migrated_engine)) as s:
        s.add(models.User(email="owner@example.test", password_hash=hash_password(PASSWORD), role="owner"))
        s.add(models.User(email="viewer@example.test", password_hash=hash_password(PASSWORD), role="viewer"))
    with TestClient(create_app(Settings(database_url=db_url, redis_url=url))) as c:
        yield c
    sync_redis.from_url(url).flushdb()
    truncate(migrated_engine, "users, audit_logs")


def login(c: TestClient, email: str = "owner@example.test") -> None:
    assert c.post("/api/v1/auth/login", json={"email": email, "password": PASSWORD},
                  headers=CSRF).status_code == 200


def test_missing_state_is_stopped_and_actions_are_owner_only(client: TestClient) -> None:
    login(client)
    assert client.get("/api/v1/bot").json()["state"] == "stopped"
    client.post("/api/v1/auth/logout", headers=CSRF)
    login(client, "viewer@example.test")
    assert client.post("/api/v1/bot/start", json={"reason": "viewer try"}, headers=CSRF).status_code == 403


def test_start_pause_stop_transitions_and_audit(client: TestClient, migrated_engine: Engine) -> None:
    login(client)
    assert client.post("/api/v1/bot/start", json={"reason": "begin paper session"},
                       headers=CSRF).json()["state"] == "running"
    assert client.post("/api/v1/bot/pause", json={"reason": "news review"},
                       headers=CSRF).json()["state"] == "paused"
    assert client.post("/api/v1/bot/stop", json={"reason": "end of day"},
                       headers=CSRF).json()["state"] == "stopped"
    assert client.get("/api/v1/bot").json()["details"]["reason"] == "end of day"
    with transaction(make_session_factory(migrated_engine)) as s:
        actions = s.scalars(select(models.AuditLog.action)).all()
    assert {"bot_start", "bot_pause", "bot_stop"} <= set(actions)


def test_reason_is_required(client: TestClient) -> None:
    login(client)
    assert client.post("/api/v1/bot/start", json={"reason": "x"}, headers=CSRF).status_code == 422
