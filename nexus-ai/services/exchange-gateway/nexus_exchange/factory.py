"""Build adapters by exchange code."""

from __future__ import annotations

from typing import Any

from nexus_config import ExchangeCode, Settings

from nexus_exchange.base import ExchangeAdapter
from nexus_exchange.capabilities import CAPABILITIES
from nexus_exchange.ccxt_adapter import CcxtSpotAdapter, create_ccxt_client
from nexus_exchange.errors import UnsupportedFeature


def build_adapter(
    code: ExchangeCode, *, api_key: str | None = None, secret: str | None = None,
    sandbox: bool = False, timeout_ms: int = 10_000, public_api_url: str | None = None,
    ca_file: str | None = None,
) -> ExchangeAdapter:
    caps = CAPABILITIES[code]
    if sandbox and not caps.sandbox_available:
        raise UnsupportedFeature(f"{code.value} has no usable sandbox", exchange=code.value)
    if public_api_url and code is not ExchangeCode.BINANCE:
        raise UnsupportedFeature("public_api_url override is only supported for binance")
    client = create_ccxt_client(code.value, api_key=api_key, secret=secret, sandbox=sandbox,
                                timeout_ms=timeout_ms, public_api_url=public_api_url,
                                ca_file=ca_file)
    return CcxtSpotAdapter(code.value, client, caps)


def build_from_settings(code: ExchangeCode, settings: Settings) -> ExchangeAdapter:
    """Public-data adapter (no credentials) configured from settings."""
    url = settings.binance_public_api_url if code is ExchangeCode.BINANCE else None
    return build_adapter(code, sandbox=settings.use_exchange_sandbox, public_api_url=url,
                         ca_file=settings.extra_ca_file)


def build_ws_client(code: ExchangeCode, settings: Settings) -> Any:
    """ccxt.pro client for PUBLIC market-data streams (no credentials)."""
    import ccxt.pro as ccxt_pro

    config: dict[str, Any] = {"enableRateLimit": True, "options": {"defaultType": "spot"}}
    if settings.extra_ca_file:
        config["cafile"] = settings.extra_ca_file
    if code is ExchangeCode.BINANCE:
        config["options"]["fetchMarkets"] = ["spot"]
        config["options"]["fetchCurrencies"] = False
    client = getattr(ccxt_pro, code.value)(config)
    if code is ExchangeCode.BINANCE:
        # Order book streams also fetch a REST depth snapshot: use the same public mirror.
        if settings.binance_public_ws_url:
            client.urls["api"]["ws"]["spot"] = settings.binance_public_ws_url
        if settings.binance_public_api_url:
            client.urls["api"]["public"] = settings.binance_public_api_url
    return client
