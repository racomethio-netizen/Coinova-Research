"""Validated, environment-based configuration (pydantic-settings).

Compliance kill switches mirror the web app (src/lib/env.ts) and are read
only from the environment (ADR-008). Secrets are SecretStr so they never
appear in reprs or logs.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Any, Literal

from pydantic import Field, SecretStr, ValidationError, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class ConfigError(ValueError):
    """Raised when the environment is invalid. Messages name variables, never values."""


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=None, extra="ignore", case_sensitive=False)

    app_env: Literal["development", "test", "staging", "production"] = "development"
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"

    database_url: SecretStr | None = None
    engine_internal_token: SecretStr | None = None

    market_source: str = Field(default="binance_spot", pattern=r"^[a-z][a-z0-9_]{1,39}$")
    binance_base_url: str = "https://data-api.binance.vision"
    binance_archive_url: str = "https://data.binance.vision"
    market_data_timeout_s: float = Field(default=10.0, ge=1.0, le=60.0)
    # Conservative self-imposed request budget, far below the source's documented limits.
    market_data_requests_per_minute: int = Field(default=300, ge=1, le=1200)
    ingest_close_delay_s: int = Field(default=20, ge=0, le=600)
    ingest_initial_bars: int = Field(default=1000, ge=10, le=100_000)
    ticker_interval_s: int = Field(default=15, ge=5, le=3600)
    worker_tick_s: float = Field(default=5.0, ge=0.5, le=300)

    # Compliance gates (docs/nexus/COMPLIANCE.md). Strings, exactly like the web app.
    signals_visibility: Literal["staff", "beta", "public"] = "staff"
    paid_signals_enabled: Literal["true", "false"] = "false"
    live_execution_enabled: Literal["true", "false"] = "false"

    @model_validator(mode="after")
    def _rules(self) -> Settings:
        issues: list[str] = []
        if self.live_execution_enabled == "true":
            issues.append("LIVE_EXECUTION_ENABLED: live execution is not implemented and must stay false (docs/nexus/COMPLIANCE.md section 3)")
        if self.paid_signals_enabled == "true" and self.signals_visibility != "public":
            issues.append("PAID_SIGNALS_ENABLED: requires SIGNALS_VISIBILITY=public (and legal approval)")
        if self.engine_internal_token is not None and len(self.engine_internal_token.get_secret_value()) < 32:
            issues.append("ENGINE_INTERNAL_TOKEN: must be at least 32 characters")
        for name in ("binance_base_url", "binance_archive_url"):
            url: str = getattr(self, name)
            if self.app_env in ("staging", "production") and not url.startswith("https://"):
                issues.append(f"{name.upper()}: must use https outside development/test")
        if self.app_env == "production" and self.database_url is None:
            issues.append("DATABASE_URL: required in production")
        if issues:
            raise ValueError("\n- ".join(issues))
        return self

    @property
    def is_production(self) -> bool:
        return self.app_env == "production"


def load_settings(**overrides: Any) -> Settings:
    """Build settings; on failure raise ConfigError naming variables only.

    Pydantic's own error text includes the offending input values, which may be
    secrets, so it is never surfaced.
    """
    try:
        return Settings(**overrides)
    except ValidationError as e:
        lines = []
        for err in e.errors(include_input=False, include_url=False):
            loc = ".".join(str(p).upper() for p in err["loc"])
            msg = str(err["msg"]).removeprefix("Value error, ")
            lines.append(f"{loc}: {msg}" if loc else msg)
        raise ConfigError("Invalid engine environment:\n- " + "\n- ".join(lines)) from None


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return load_settings()
