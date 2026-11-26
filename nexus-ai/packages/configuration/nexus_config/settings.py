"""Typed settings loaded from environment variables (prefix ``NEXUS_``).

Design rules:

* Secrets are ``SecretStr`` and never appear in ``repr``/logs/API responses.
* Risk limits have hard ceilings taken from the project mandate. Configuration may
  make them *stricter*, never looser. Exceeding a ceiling is a startup error.
* Leverage is capped at 1x and only on Binance futures; martingale is never configurable:
  spot trading is always available; futures is off until validated.
* ``trading_mode=live`` alone never enables live trading. See
  :meth:`Settings.live_trading_config_problems` and ``docs/SECURITY.md``.
"""

from __future__ import annotations

from decimal import Decimal
from enum import StrEnum
from functools import lru_cache
from typing import Annotated, Final, Literal

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
    M1 = "1m"
    M3 = "3m"
    M5 = "5m"
    M15 = "15m"
    M30 = "30m"
    H1 = "1h"
    H2 = "2h"
    H4 = "4h"
    H6 = "6h"
    H12 = "12h"
    D1 = "1d"

    @property
    def seconds(self) -> int:
        return TIMEFRAME_SECONDS[self.value]


TIMEFRAME_SECONDS = {"1m": 60, "3m": 180, "5m": 300, "15m": 900, "30m": 1800, "1h": 3600,
                     "2h": 7200, "4h": 14400, "6h": 21600, "12h": 43200, "1d": 86400}
# Timeframes built from stored CLOSED bars instead of extra exchange calls: a bar is
# written only when every constituent source bar exists (never padded or estimated).
DERIVED_TIMEFRAMES = {"3m": "1m", "30m": "15m", "2h": "1h", "6h": "1h", "12h": "1h"}


# Exact phrase the owner must set to acknowledge live-trading risk. This is one of
# several independent gates; it is not sufficient on its own.
LIVE_TRADING_ACK_PHRASE: Final = "I accept that live trading can lose real money"

# Mandated ceilings (fractions of equity). Config may only go lower.
# Owner mandate 2026-11-20: risk 0.5% default / 1% max, daily loss 2%, max drawdown 10%,
# futures 2x default / 5x max, isolated margin. Weekly 4% keeps daily <= weekly <= drawdown.
# Owner mandate 2026-11-21 (initial paper profile): defaults 0.25% risk per trade, 1% daily,
# 3% weekly, 3 open positions, 1x (spot only; futures stay disabled). The absolute
# ceilings above are unchanged except max open positions (2 -> 3). Total open risk at the
# new defaults is 3 x 0.25% = 0.75%, lower than the previous 2 x 0.5% = 1%.
MAX_RISK_PER_TRADE: Final = Decimal("0.01")
# Owner mandate 2026-11-23 (master directive, Part 8): 0.25% per trade, 1% daily, 3% weekly,
# 5% drawdown, 1% aggregate open risk, 3 positions, 30% spot allocation, futures 1x.
DEFAULT_RISK_PER_TRADE: Final = Decimal("0.0025")
MAX_DAILY_LOSS: Final = Decimal("0.02")
DEFAULT_DAILY_LOSS: Final = Decimal("0.01")
MAX_WEEKLY_LOSS: Final = Decimal("0.04")
DEFAULT_WEEKLY_LOSS: Final = Decimal("0.03")
MAX_DRAWDOWN: Final = Decimal("0.10")
DEFAULT_DRAWDOWN: Final = Decimal("0.05")
MAX_OPEN_POSITIONS: Final = 3
DEFAULT_OPEN_POSITIONS: Final = 3
MAX_PORTFOLIO_EXPOSURE: Final = Decimal("0.50")
DEFAULT_PORTFOLIO_EXPOSURE: Final = Decimal("0.30")    # owner: max 30% of equity in spot
# Sum of open positions' risk to their stops plus the new trade's risk (owner directive
# 2026-11-23, Part 8: 1% of equity). Ceiling: three positions at the 1% per-trade ceiling.
MAX_OPEN_RISK: Final = Decimal("0.03")
DEFAULT_OPEN_RISK: Final = Decimal("0.01")
MIN_COOLDOWN_AFTER_STOP_MINUTES: Final = 30
# Futures: Binance only, disabled by default, isolated margin. Leverage never changes
# position size (size comes from risk); it only sets margin use.
FUTURES_LEVERAGE_OPTIONS: Final = (1, 2, 3, 4, 5)   # owner 2026-11-23: start at 1x
MAX_FUTURES_LEVERAGE: Final = Decimal("5")
MAX_FUTURES_RISK_PER_TRADE: Final = Decimal("0.01")
DEFAULT_FUTURES_RISK_PER_TRADE: Final = Decimal("0.0025")
FUTURES_EXCHANGES: Final = ("binance",)


class RiskSettings(BaseModel):
    risk_per_trade: Decimal = DEFAULT_RISK_PER_TRADE
    daily_loss_limit: Decimal = DEFAULT_DAILY_LOSS
    weekly_loss_limit: Decimal = DEFAULT_WEEKLY_LOSS
    max_drawdown: Decimal = DEFAULT_DRAWDOWN
    max_open_positions: int = DEFAULT_OPEN_POSITIONS
    max_portfolio_exposure: Decimal = DEFAULT_PORTFOLIO_EXPOSURE
    max_open_risk: Decimal = DEFAULT_OPEN_RISK
    # Max share of equity in a single base asset across all exchanges.
    max_asset_concentration: Decimal = Decimal("0.25")
    # Assets whose 1h return correlation is >= correlation_threshold count as one exposure.
    max_correlated_exposure: Decimal = Decimal("0.35")
    correlation_threshold: float = 0.7
    # No re-entry into a pair for this long after a losing exit (no revenge trading).
    cooldown_after_stop_minutes: int = 120

    @model_validator(mode="after")
    def _enforce_ceilings(self) -> RiskSettings:
        checks: list[tuple[str, Decimal | int, Decimal | int]] = [
            ("risk_per_trade", self.risk_per_trade, MAX_RISK_PER_TRADE),
            ("daily_loss_limit", self.daily_loss_limit, MAX_DAILY_LOSS),
            ("weekly_loss_limit", self.weekly_loss_limit, MAX_WEEKLY_LOSS),
            ("max_drawdown", self.max_drawdown, MAX_DRAWDOWN),
            ("max_open_positions", self.max_open_positions, MAX_OPEN_POSITIONS),
            ("max_portfolio_exposure", self.max_portfolio_exposure, MAX_PORTFOLIO_EXPOSURE),
            ("max_open_risk", self.max_open_risk, MAX_OPEN_RISK),
            ("max_asset_concentration", self.max_asset_concentration, MAX_PORTFOLIO_EXPOSURE),
            ("max_correlated_exposure", self.max_correlated_exposure, MAX_PORTFOLIO_EXPOSURE),
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
        # Absolute floors: these protections can be tightened, never switched off.
        if self.cooldown_after_stop_minutes < MIN_COOLDOWN_AFTER_STOP_MINUTES:
            raise ValueError(f"cooldown_after_stop_minutes must be >= "
                             f"{MIN_COOLDOWN_AFTER_STOP_MINUTES}")
        if not 0.5 <= self.correlation_threshold <= 0.9:
            raise ValueError("correlation_threshold must be between 0.5 and 0.9")
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

    @field_validator("database_url", mode="before")
    @classmethod
    def _normalize_database_url(cls, value: object) -> object:
        """Hosts such as Render give `postgresql://` URLs; the app's driver is psycopg 3."""
        raw = value.get_secret_value() if isinstance(value, SecretStr) else value
        if isinstance(raw, str):
            for prefix in ("postgresql://", "postgres://"):
                if raw.startswith(prefix):
                    return "postgresql+psycopg://" + raw[len(prefix):]
        return value

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
        default_factory=lambda: [Timeframe.M5, Timeframe.M15, Timeframe.H1, Timeframe.H4,
                                 Timeframe.D1]
    )
    quote_currency: str = "USDT"

    # --- Futures (disabled by default; see MAX_FUTURES_LEVERAGE) ----------------
    futures_enabled: bool = False
    futures_exchanges: Annotated[list[ExchangeCode], NoDecode] = Field(
        default_factory=lambda: [ExchangeCode.BINANCE])
    futures_default_leverage: int = 1
    futures_leverage_options: Annotated[list[int], NoDecode] = Field(
        default_factory=lambda: list(FUTURES_LEVERAGE_OPTIONS))
    futures_risk_per_trade: Decimal = DEFAULT_FUTURES_RISK_PER_TRADE
    futures_allow_short: bool = False
    futures_margin_mode: Literal["isolated", "cross"] = "isolated"
    futures_cross_margin_enabled: bool = False
    use_exchange_sandbox: bool = False
    # Binance blocks api.binance.com in some jurisdictions (HTTP 451). Public market
    # data is also served by this official mirror. Set to "" to use the main host.
    binance_public_api_url: str | None = "https://data-api.binance.vision/api/v3"
    binance_public_ws_url: str | None = "wss://data-stream.binance.vision/ws"
    # Candle history to backfill on first start (Kraken is capped at 720 bars anyway).
    market_backfill_days: int = 120
    # Daily bars are kept for longer: daily strategies need about 400 bars of warm-up (EMA200
    # plus a weekly EMA50). Real Binance klines only, filled by the gap repair (2 pages).
    daily_history_days: int = Field(default=1500, ge=120, le=3000)
    # 1-minute candles for the traded pairs (and 3m derived from them), kept this many days.
    minute_candles: bool = True
    # NEXUS bot (EMA/RSI/MACD on 5m): off | monitor (signals only) | paper (simulated fills
    # on live Binance order books). Live bot orders are not available in this build: real
    # orders go only through the main trader's live gate after the owner's activation.
    bot_mode: Literal["off", "monitor", "paper"] = "paper"
    # Paper grid bot (owner request 2026-11-23): simulated capital on the live order book.
    grid_bot_mode: Literal["off", "paper"] = "paper"
    grid_symbol: str = "BTC/USDT"
    grid_capital: Decimal = Field(default=Decimal("1000"), gt=0, le=Decimal("100000"))
    # Adaptive grid system (owner request 2026-11-26): PAPER ONLY, independent spot and
    # futures paper accounts. There is no live setting for this system.
    agrid_mode: Literal["off", "paper"] = "paper"
    agrid_spot_capital: Decimal = Field(default=Decimal("5000"), gt=0, le=Decimal("1000000"))
    agrid_futures_capital: Decimal = Field(default=Decimal("5000"), gt=0,
                                           le=Decimal("1000000"))
    # Bearer token for GET /metrics (Prometheus). Unset = the endpoint does not exist.
    metrics_token: SecretStr | None = None
    minute_retention_days: int = Field(default=30, ge=1, le=90)
    # Optional CA bundle replacing certifi's for exchange HTTPS (TLS-inspecting proxies).
    # Certificate verification is never disabled.
    extra_ca_file: str | None = None

    risk: RiskSettings = Field(default_factory=RiskSettings)

    # VIRTUAL capital for the paper portfolio (the real initial capital is undecided).
    paper_initial_capital: Decimal = Decimal("10000")
    # Paper realism: delay before filling against a fresh book, share of each visible
    # level a paper order may take, max book age (older = simulated outage, no fill), and
    # an optional injected outage rate for drills (0 keeps paper runs reproducible).
    paper_latency_ms: int = Field(default=250, ge=0, le=5000)
    paper_book_participation: Decimal = Field(default=Decimal("0.5"), gt=0, le=1)
    paper_max_book_age_seconds: float = Field(default=10.0, gt=0, le=120)
    paper_outage_rate: float = Field(default=0.0, ge=0, le=0.5)
    # Exchange whose candles drive signals (deepest, gap-free history; see EXCHANGES.md).
    signal_exchange: str = "binance"
    trader_interval_seconds: float = 30.0
    # Single-server hosting: the API process also supervises the market-data and trader
    # workers. Honoured only in paper mode (see nexus_api.workers).
    run_workers: bool = False
    # "process": one child process per worker (more memory, isolated).
    # "inline": workers run as tasks in the API's event loop (fits a 512 MB server).
    workers_mode: Literal["process", "inline"] = "process"
    # --- X (Twitter) sentiment: READ-ONLY, official API, app-only Bearer token ------------
    # Unset token = the X module is off. X bills per resource read, so every refresh is
    # bounded by x_daily_budget_usd (estimated from the documented prices).
    x_bearer_token: SecretStr | None = None
    x_api_base: str = "https://api.x.com"
    x_coins: Annotated[list[str], NoDecode] = Field(
        default_factory=lambda: ["BTC", "ETH", "SOL", "XRP"])
    # Accounts watched through one search query (from:...). They also count as trusted.
    x_watch_accounts: Annotated[list[str], NoDecode] = Field(
        default_factory=lambda: ["whale_alert", "binance", "coinbase", "krakenfx", "SECGov"])
    x_refresh_minutes: int = Field(default=120, ge=15, le=1440)
    x_max_posts_per_query: int = Field(default=15, ge=10, le=100)
    x_daily_budget_usd: Decimal = Field(default=Decimal("1.00"), ge=0, le=Decimal("100"))
    x_trends_woeid: int | None = 1          # 1 = worldwide; None disables trends
    x_trends_every_hours: int = Field(default=6, ge=1, le=48)
    # Optional: this service's own public URL. While workers run, it is requested every
    # 10 minutes so a hosting plan that sleeps idle web services keeps the workers up.
    keep_awake_url: str | None = None
    # Entries are paused while the news-risk score is above this (0..1).
    news_block_threshold: float = 0.7

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
    # Directory of the static dashboard build to serve from the API (dev/test only).
    dashboard_dir: str | None = None

    @field_validator("enabled_exchanges", "trading_pairs", "timeframes", "cors_origins",
                     "x_coins", "x_watch_accounts", mode="before")
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

    @field_validator("futures_exchanges", mode="before")
    @classmethod
    def _split_futures(cls, value: object) -> object:
        if isinstance(value, str):
            return [part.strip() for part in value.split(",") if part.strip()]
        return value

    @field_validator("futures_leverage_options", mode="before")
    @classmethod
    def _split_leverage(cls, value: object) -> object:
        if isinstance(value, str):
            return [int(part) for part in value.split(",") if part.strip()]
        return value

    @field_validator("futures_leverage_options")
    @classmethod
    def _leverage_options(cls, options: list[int]) -> list[int]:
        if not options or any(o not in FUTURES_LEVERAGE_OPTIONS for o in options):
            raise ValueError(f"leverage options must be chosen from {FUTURES_LEVERAGE_OPTIONS}")
        return sorted(set(options))

    @field_validator("futures_risk_per_trade")
    @classmethod
    def _futures_risk_ceiling(cls, value: Decimal) -> Decimal:
        if value <= 0 or value > MAX_FUTURES_RISK_PER_TRADE:
            raise ValueError(f"futures_risk_per_trade must be > 0 and <= {MAX_FUTURES_RISK_PER_TRADE}")
        return value

    @model_validator(mode="after")
    def _futures_defaults(self) -> Settings:
        if self.futures_default_leverage not in self.futures_leverage_options:
            raise ValueError("futures_default_leverage must be one of the leverage options")
        if self.futures_cross_margin_enabled is False and self.futures_margin_mode == "cross":
            raise ValueError("cross margin is disabled; set futures_cross_margin_enabled to use it")
        return self

    @model_validator(mode="after")
    def _cross_checks(self) -> Settings:
        unsupported = [e.value for e in self.futures_exchanges if e.value not in FUTURES_EXCHANGES]
        if unsupported:
            raise ValueError(f"futures not supported on: {', '.join(unsupported)}")
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
            "spot_enabled": True,
            "futures_enabled": self.futures_enabled,
            "futures_exchanges": [e.value for e in self.futures_exchanges],
            "futures_default_leverage": self.futures_default_leverage,
            "futures_max_leverage": max(self.futures_leverage_options),
            "futures_margin_mode": self.futures_margin_mode,
            "futures_risk_per_trade": str(self.futures_risk_per_trade),
            "short_selling_enabled": self.futures_enabled and self.futures_allow_short,
            "martingale_enabled": False,
            "telegram_configured": self.telegram_bot_token is not None,
            "x_configured": self.x_bearer_token is not None,
        }


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
