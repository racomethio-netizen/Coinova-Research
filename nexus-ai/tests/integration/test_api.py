"""API startup and health checks against real PostgreSQL and Redis."""

from __future__ import annotations

import os

import pytest
from fastapi.testclient import TestClient
from nexus_api.main import create_app
from nexus_config import Settings
from sqlalchemy import Engine

pytestmark = [pytest.mark.db, pytest.mark.redis]


@pytest.fixture
def redis_url() -> str:
    url = os.environ.get("NEXUS_TEST_REDIS_URL")
    if not url:
        pytest.skip("NEXUS_TEST_REDIS_URL not set")
    return url


def test_app_starts_and_is_ready(migrated_engine: Engine, db_url: str, redis_url: str) -> None:
    app = create_app(Settings(database_url=db_url, redis_url=redis_url, log_json=True))
    with TestClient(app) as client:
        assert client.get("/health/live").json() == {"status": "ok"}
        ready = client.get("/health/ready")
        assert ready.status_code == 200, ready.text
        assert ready.json()["checks"] == {"database": "ok", "redis": "ok"}
        cfg = client.get("/api/v1/system/config").json()
        assert cfg["trading_mode"] == "paper"
        assert cfg["live_trading_permitted_by_config"] is False
        assert "database_url" not in cfg
        headers = client.get("/health/live").headers
        assert headers["x-content-type-options"] == "nosniff"
        assert headers["cache-control"] == "no-store"


def test_ready_reports_failure_honestly(db_url: str) -> None:
    app = create_app(Settings(database_url=db_url, redis_url="redis://127.0.0.1:1/0"))
    with TestClient(app) as client:
        ready = client.get("/health/ready")
        assert ready.status_code == 503
        body = ready.json()
        assert body["status"] == "degraded"
        assert body["checks"]["redis"].startswith("error")


def test_production_hides_docs(db_url: str, redis_url: str) -> None:
    s = Settings(app_env="production", database_url=db_url, redis_url=redis_url,
                 credential_encryption_keys="k1:" + "A" * 43, session_secret="s" * 40)
    with TestClient(create_app(s)) as client:
        assert client.get("/docs").status_code == 404
        assert client.get("/openapi.json").status_code == 404
