from fastapi.testclient import TestClient

from nexus_engine.api import create_app
from nexus_engine.config import Settings, get_settings


def app_with(settings: Settings) -> TestClient:
    app = create_app()
    app.dependency_overrides[get_settings] = lambda: settings
    return TestClient(app)


def test_health_without_database_is_degraded_not_an_error() -> None:
    r = app_with(Settings()).get("/health")
    assert r.status_code == 200 and r.json()["db"] == "not_configured"


def test_internal_api_is_hidden_without_a_token() -> None:
    assert app_with(Settings()).get("/internal/status").status_code == 404


def test_internal_api_requires_the_exact_token() -> None:
    c = app_with(Settings(engine_internal_token="t" * 40))  # type: ignore[arg-type]
    assert c.get("/internal/status").status_code == 401
    assert c.get("/internal/status", headers={"Authorization": "Bearer " + "t" * 39}).status_code == 401


def test_no_interactive_docs_exposed() -> None:
    c = app_with(Settings())
    assert c.get("/docs").status_code == 404 and c.get("/openapi.json").status_code == 404
