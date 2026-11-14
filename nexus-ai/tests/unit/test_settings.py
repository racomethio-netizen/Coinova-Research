from __future__ import annotations

from decimal import Decimal

import pytest
from nexus_config import LIVE_TRADING_ACK_PHRASE, RiskSettings, Settings, Timeframe, TradingMode
from pydantic import ValidationError


def test_defaults_are_safe() -> None:
    s = Settings()
    assert s.trading_mode is TradingMode.PAPER
    assert s.live_trading_enabled is False
    assert s.trading_pairs == ["BTC/USDT", "ETH/USDT"]
    assert [t.value for t in s.timeframes] == ["5m", "15m", "1h", "4h"]
    assert s.risk.risk_per_trade == Decimal("0.0025")
    assert s.risk.daily_loss_limit == Decimal("0.01")
    assert s.risk.weekly_loss_limit == Decimal("0.02")
    assert s.risk.max_drawdown == Decimal("0.05")
    assert s.risk.max_open_positions == 2
    assert s.risk.max_portfolio_exposure == Decimal("0.50")
    assert s.live_trading_config_problems()  # live not permitted


def test_env_parsing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NEXUS_TRADING_PAIRS", "btc/usdt, eth/usdt")
    monkeypatch.setenv("NEXUS_ENABLED_EXCHANGES", "binance,kraken")
    monkeypatch.setenv("NEXUS_RISK__RISK_PER_TRADE", "0.001")
    s = Settings()
    assert s.trading_pairs == ["BTC/USDT", "ETH/USDT"]
    assert [e.value for e in s.enabled_exchanges] == ["binance", "kraken"]
    assert s.risk.risk_per_trade == Decimal("0.001")


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("risk_per_trade", "0.003"),
        ("daily_loss_limit", "0.02"),
        ("weekly_loss_limit", "0.03"),
        ("max_drawdown", "0.10"),
        ("max_open_positions", 3),
        ("max_portfolio_exposure", "0.6"),
        ("risk_per_trade", "0"),
        ("risk_per_trade", "-0.001"),
    ],
)
def test_risk_limits_cannot_be_loosened(field: str, value: object) -> None:
    with pytest.raises(ValidationError):
        RiskSettings(**{field: value})


def test_risk_limits_can_be_tightened() -> None:
    r = RiskSettings(risk_per_trade="0.001", daily_loss_limit="0.005", max_open_positions=1)
    assert r.max_open_positions == 1


def test_inconsistent_loss_limits_rejected() -> None:
    with pytest.raises(ValidationError):
        RiskSettings(daily_loss_limit="0.01", weekly_loss_limit="0.005")


@pytest.mark.parametrize("pair", ["BTCUSDT", "BTC/USDT/X", "BTC-USDT", "BTC/"])
def test_invalid_pairs_rejected(pair: str) -> None:
    with pytest.raises(ValidationError):
        Settings(trading_pairs=[pair])


def test_duplicate_pairs_rejected() -> None:
    with pytest.raises(ValidationError):
        Settings(trading_pairs=["BTC/USDT", "btc/usdt"])


def test_live_enabled_requires_live_mode() -> None:
    with pytest.raises(ValidationError):
        Settings(live_trading_enabled=True, trading_mode="paper")


def test_production_requires_secrets() -> None:
    with pytest.raises(ValidationError, match="production requires"):
        Settings(app_env="production")


def _prod_live(**overrides: object) -> Settings:
    base: dict[str, object] = dict(
        app_env="production", credential_encryption_keys="k1:" + "A" * 43,
        session_secret="x" * 40, trading_mode="live", live_trading_enabled=True,
        live_trading_ack=LIVE_TRADING_ACK_PHRASE,
    )
    base.update(overrides)
    return Settings(**base)


def test_live_gate_requires_every_condition() -> None:
    assert _prod_live().live_trading_config_problems() == []
    assert _prod_live(live_trading_ack="yes").live_trading_config_problems() == [
        "live_trading_ack phrase not set exactly"
    ]
    assert "live_trading_enabled is false" in _prod_live(
        live_trading_enabled=False).live_trading_config_problems()
    staging = Settings(trading_mode="live", live_trading_enabled=True,
                       live_trading_ack=LIVE_TRADING_ACK_PHRASE, app_env="staging")
    assert staging.live_trading_config_problems() == ["app_env is not 'production'"]


def test_live_cannot_use_sandbox_in_production() -> None:
    with pytest.raises(ValidationError):
        _prod_live(use_exchange_sandbox=True)


def test_secrets_not_in_repr_or_summary() -> None:
    s = Settings(telegram_bot_token="123:SUPERSECRET", session_secret="topsecretvalue")
    assert "SUPERSECRET" not in repr(s)
    assert "topsecretvalue" not in repr(s)
    summary = str(s.safe_summary())
    assert "SUPERSECRET" not in summary and "nexus:nexus" not in summary
    assert s.safe_summary()["spot_only"] is True
    assert s.safe_summary()["leverage_enabled"] is False


def test_spot_only_flags_are_not_configurable(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NEXUS_LEVERAGE_ENABLED", "true")
    monkeypatch.setenv("NEXUS_SHORT_SELLING_ENABLED", "true")
    summary = Settings().safe_summary()
    assert summary["leverage_enabled"] is False
    assert summary["short_selling_enabled"] is False


def test_timeframe_seconds() -> None:
    assert Timeframe("4h").seconds == 14400
    assert Timeframe("5m").seconds == 300
