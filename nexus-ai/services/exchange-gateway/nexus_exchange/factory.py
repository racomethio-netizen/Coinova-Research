"""Build adapters by exchange code."""

from __future__ import annotations

from nexus_config import ExchangeCode

from nexus_exchange.base import ExchangeAdapter
from nexus_exchange.capabilities import CAPABILITIES
from nexus_exchange.ccxt_adapter import CcxtSpotAdapter, create_ccxt_client
from nexus_exchange.errors import UnsupportedFeature


def build_adapter(
    code: ExchangeCode, *, api_key: str | None = None, secret: str | None = None,
    sandbox: bool = False, timeout_ms: int = 10_000,
) -> ExchangeAdapter:
    caps = CAPABILITIES[code]
    if sandbox and not caps.sandbox_available:
        raise UnsupportedFeature(f"{code.value} has no usable sandbox", exchange=code.value)
    client = create_ccxt_client(code.value, api_key=api_key, secret=secret, sandbox=sandbox,
                                timeout_ms=timeout_ms)
    return CcxtSpotAdapter(code.value, client, caps)
