import pytest

from nexus_engine.config import ConfigError, Settings, load_settings


def make(**env: str) -> Settings:
    return load_settings(**env)


def test_defaults_are_the_safest_values() -> None:
    s = make()
    assert (s.signals_visibility, s.paid_signals_enabled, s.live_execution_enabled) == ("staff", "false", "false")
    assert s.binance_base_url == "https://data-api.binance.vision"


def test_live_execution_cannot_be_enabled() -> None:
    with pytest.raises(ConfigError, match="LIVE_EXECUTION_ENABLED"):
        make(live_execution_enabled="true")


def test_paid_signals_require_public_visibility() -> None:
    with pytest.raises(ConfigError, match="PAID_SIGNALS_ENABLED"):
        make(paid_signals_enabled="true")
    assert make(paid_signals_enabled="true", signals_visibility="public").paid_signals_enabled == "true"


def test_ambiguous_flags_are_rejected() -> None:
    with pytest.raises(ConfigError, match="LIVE_EXECUTION_ENABLED"):
        make(live_execution_enabled="yes")


def test_production_requires_database_and_https() -> None:
    with pytest.raises(ConfigError, match="DATABASE_URL"):
        make(app_env="production")
    with pytest.raises(ConfigError, match="BINANCE_BASE_URL"):
        make(app_env="staging", binance_base_url="http://example.test")


def test_internal_token_minimum_length_and_secret_repr() -> None:
    with pytest.raises(ConfigError, match="ENGINE_INTERNAL_TOKEN"):
        make(engine_internal_token="short")
    s = make(engine_internal_token="x" * 40, database_url="postgres://u:pw@localhost/db")
    assert "pw@" not in repr(s) and "x" * 40 not in repr(s)


def test_error_messages_never_contain_values() -> None:
    with pytest.raises(ConfigError) as e:
        make(engine_internal_token="supersecret-short", database_url="postgres://u:hunter2@h/db", live_execution_enabled="maybe-hunter3")
    assert "supersecret" not in str(e.value) and "hunter" not in str(e.value)
    assert "LIVE_EXECUTION_ENABLED" in str(e.value)
