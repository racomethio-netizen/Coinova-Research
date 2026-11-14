from __future__ import annotations

import os

import pytest


@pytest.fixture(autouse=True)
def _isolate_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Unit tests must not pick up the developer's NEXUS_* settings, except the
    connection URLs that integration tests rely on."""
    keep = {"NEXUS_TEST_DATABASE_URL", "NEXUS_TEST_REDIS_URL"}
    for key in list(os.environ):
        if key.startswith("NEXUS_") and key not in keep:
            monkeypatch.delenv(key, raising=False)
    from nexus_config import get_settings

    get_settings.cache_clear()
