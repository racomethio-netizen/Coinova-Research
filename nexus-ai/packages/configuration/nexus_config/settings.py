"""Typed settings loaded from environment variables (prefix ``NEXUS_``).

Design rules:

* Secrets are ``SecretStr`` and never appear in ``repr``/logs/API responses.
* Risk limits have hard ceilings taken from the project mandate. Configuration may
  make them *stricter*, never looser. Exceeding a ceiling is a startup error.
* Leverage, futures, short selling and martingale are not configurable at all:
  the system is spot-only by construction.
* ``trading_mode=live`` alone never enables live trading. See
  :meth:`Settings.live_trading_config_problems` and ``docs/SECURITY.md``.
"""

from __future__ import annotations

from decimal import Decimal
from enum import StrEnum
from functools import lru_cache
from typing import Annotated, Final

from pydantic import BaseModel, Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


class AppEnv(StrEnum):
    DEVELOPMENT = "development"
    TEST = "test"
    STAGING = "staging"
    PRODUCTION = "production"


class TradingMode(StrEnum):
    PAPER = "paper"
    LIVE = "live"


class ExchangeCode(StrEnum):
    BINANCE = "binance"
    KRAKEN = "kraken"
    COINBASE = "coinbase"


class Timeframe(StrEnum):
    M5 = "5m"
    M15 = "15m"
    H1 = "1h"
    H4 = "4h"

    @property
    def seconds(self) -> int:
        return {"5m": 300, "15m": 900, "1h": 3600, "4h": 14400}[self.value]


# Exact phrase the owner must set to acknowledge live-trading risk. This is one of
# several independent gates; it is not sufficient on its own.
LIVE_TRADING_ACK_PHRASE: Final = "I accept that live trading can lose real money"

# Mandated ceilings (fractions of equity). Config may only go lower.
MAX_RISK_PER_TRADE: Final = Decimal("0.0025")
MAX_DAILY_LOSS: Final = Decimal("0.01")
MAX_WEEKLY_LOSS: Final = Decimal("0.02")
MAX_DRAWDOWN: Final = Decimal("0.05")
MAX_OPEN_POSITIONS: Final = 2
MAX_PORTFOLIO_EXPOSURE: Final = Decimal("0.50")


class RiskSettings(BaseModel):
    risk_per_trade: Decimal = MAX_RISK_PER_TRADE
    daily_loss_limit: Decimal = MAX_DAILY_LOSS
    weekly_loss_limit: Decimal = MAX_WEEKLY_LOSS
    max_drawdown: Decimal = MAX_DRAWDOWN
    max_open_positions: int = MAX_OPEN_POSITIONS
    max_portfolio_exposure: Decimal = MAX_PORTFOLIO_EXPOSURE
    # Max share of equity in a single base asset across all exchanges.
    max_asset_concentration: Decimal = Decimal("0.25")

    @model_validator(mode="after")
    def _enforce_ceilings(self) -> RiskSettings:
        checks: list[tuple[str, Decimal | int, Decimal | int]] = [
            ("risk_per_trade", self.risk_per_trade, MAX_RISK_PER_TRADE),
            ("daily_loss_limit", self.daily_loss_limit, MAX_DAILY_LOSS),
            ("weekly_loss_limit", self.weekly_loss_limit, MAX_WEEKLY_LOSS),
            ("max_drawdown", self.max_drawdown, MAX_DRAWDOWN),
            ("max_open_positions", self.max_open_positions, MAX_OPEN_POSITIONS),
            ("max_portfolio_exposure", self.max_portfolio_exposure, MAX_PORTFOLIO_EXPOSURE),
            ("max_asset_concentration", self.max_asset_concentration, MAX_PORTFOLIO_EXPOSURE),
        ]
        for name, value, ceiling in checks:
            if value <= 0:
                raise ValueError(f"{name} must be > 0")
            if value > ceiling:
                raise ValueError(f"{name}={value} exceeds the mandated ceiling {ceiling}")
        if self.daily_loss_limit > self.weekly_loss_limit:
            raise ValueError("daily_loss_limit cannot exceed weekly_loss_limit")
        if self.weekly_loss_limit > self.max_drawdown:
            raise ValueError("weekly_loss_limit cannot exceed max_drawdown")
        return self


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="NEXUS_",
        env_nested_delimiter="__",
        env_file=None,  # .env is loaded by Docker Compose / the shell, never implicitly
        extra="ignore",
        frozen=True,
    )

    app_env: AppEnv = AppEnv.DEVELOPMENT
    log_level: str = "INFO"
    log_json: bool = True

    database_url: SecretStr = SecretStr("postgresql+psycopg://nexus:nexus@localhost:5432/nexus")
    redis_url: SecretStr = SecretStr("redis://localhost:6379/0")

    # Base64 (urlsafe) 32-byte keys, as "<key_id>:<base64>" entries separated by commas.
    # The first entry encrypts; all entries can decrypt (key rotation).
    credential_encryption_keys: SecretStr | None = None
    session_secret: SecretStr | None = None

    # --- Trading ---------------------------------------------------------------
    trading_mode: TradingMode = TradingMode.PAPER
    live_trading_enabled: bool = False
    live_trading_ack: str = ""
    enabled_exchanges: Annotated[list[ExchangeCode], NoDecode] = Field(
        default_factory=lambda: [ExchangeCode.BINANCE, ExchangeCode.KRAKEN, ExchangeCode.COINBASE]
    )
    trading_pairs: Annotated[list[str], NoDecode] = Field(
        default_factory=lambda: ["BTC/USDT", "ETH/USDT"]
    )
    timeframes: Annotated[list[Timeframe], NoDecode] = Field(
        default_factory=lambda: [Timeframe.M5, Timeframe.M15, Timeframe.H1, Timeframe.H4]
    )
    quote_currency: str = "USDT"
    use_exchange_sandbox: bool = False

    risk: RiskSettings = Field(default_factory=RiskSettings)

    # Paper trading simulation parameters (basis points).
    paper_fee_bps: Decimal = Decimal("10")
    paper_slippage_bps: Decimal = Decimal("5")

    # --- Notifications ---------------------------------------------------------
    telegram_bot_token: SecretStr | None = None
    telegram_chat_id: str | None = None

    # --- API -------------------------------------------------------------------
    api_host: str = "127.0.0.1"
    api_port: int = 8000
    cors_origins: Annotated[list[str], NoDecode] = Field(default_factory=list)

    @field_validator("enabled_exchanges", "trading_pairs", "timeframes", "cors_origins",
                     mode="before")
    @classmethod
    def _split_csv(cls, value: object) -> object:
        if isinstance(value, str):
            return [part.strip() for part in value.split(",") if part.strip()]
        return value

    @field_validator("trading_pairs")
    @classmethod
    def _validate_pairs(cls, pairs: list[str]) -> list[str]:
        out: list[str] = []
        for pair in pairs:
            parts = pair.upper().split("/")
            if len(parts) != 2 or not all(p.isalnum() for p in parts):
                raise ValueError(f"invalid pair {pair!r}; expected BASE/QUOTE")
            out.append("/".join(parts))
        if len(set(out)) != len(out):
            raise ValueError("duplicate trading pairs")
        return out

    @field_validator("log_level")
    @classmethod
    def _validate_level(cls, level: str) -> str:
        level = level.upper()
        if level not in {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}:
            raise ValueError(f"invalid log level {level!r}")
        return level

    @field_validator("paper_fee_bps", "paper_slippage_bps")
    @classmethod
    def _non_negative(cls, value: Decimal) -> Decimal:
        if value < 0:
            raise ValueError("must be >= 0")
        return value

    @model_validator(mode="after")
    def _cross_checks(self) -> Settings:
        if self.app_env is AppEnv.PRODUCTION:
            missing = [
                name
                for name, value in (
                    ("credential_encryption_keys", self.credential_encryption_keys),
                    ("session_secret", self.session_secret),
                )
                if value is None or not value.get_secret_value()
            ]
            if missing:
                raise ValueError(f"production requires: {', '.join(missing)}")
            if self.use_exchange_sandbox and self.trading_mode is TradingMode.LIVE:
                raise ValueError("live trading cannot use exchange sandbox endpoints")
        if self.live_trading_enabled and self.trading_mode is not TradingMode.LIVE:
            raise ValueError("live_trading_enabled requires trading_mode=live")
        return self

    # ------------------------------------------------------------------------
    def live_trading_config_problems(self) -> list[str]:
        """Return every configuration reason live trading is NOT allowed.

        An empty list means only that configuration permits live trading. The
        execution engine additionally requires a recorded owner approval in the
        database and a cleared kill switch (later phases).
        """
        problems: list[str] = []
        if self.trading_mode is not TradingMode.LIVE:
            problems.append("trading_mode is not 'live'")
        if not self.live_trading_enabled:
            problems.append("live_trading_enabled is false")
        if self.live_trading_ack != LIVE_TRADING_ACK_PHRASE:
            problems.append("live_trading_ack phrase not set exactly")
        if self.app_env is not AppEnv.PRODUCTION:
            problems.append("app_env is not 'production'")
        if self.use_exchange_sandbox:
            problems.append("exchange sandbox endpoints are configured")
        return problems

    def safe_summary(self) -> dict[str, object]:
        """Non-secret view suitable for the dashboard and logs."""
        return {
            "app_env": self.app_env.value,
            "trading_mode": self.trading_mode.value,
            "live_trading_permitted_by_config": not self.live_trading_config_problems(),
            "enabled_exchanges": [e.value for e in self.enabled_exchanges],
            "trading_pairs": list(self.trading_pairs),
            "timeframes": [t.value for t in self.timeframes],
            "quote_currency": self.quote_currency,
            "risk": {k: str(v) for k, v in self.risk.model_dump().items()},
            "spot_only": True,
            "leverage_enabled": False,
            "short_selling_enabled": False,
            "martingale_enabled": False,
            "telegram_configured": self.telegram_bot_token is not None,
        }


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
