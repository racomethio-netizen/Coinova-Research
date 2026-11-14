"""Build adapters by exchange code."""

from __future__ import annotations

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
