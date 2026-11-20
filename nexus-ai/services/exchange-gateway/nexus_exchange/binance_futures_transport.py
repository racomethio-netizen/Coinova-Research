"""Production transport: CCXT's signed Binance USD-M methods (fapi*), nothing else.

Credentials are passed only to the ccxt client and are never logged or repr'd.
"""

from __future__ import annotations

from typing import Any

import ccxt.async_support as ccxt_async


class CcxtFuturesTransport:
    def __init__(
        self, api_key: str, secret: str, *, timeout_ms: int = 10_000, cafile: str | None = None,
        testnet: bool = False,
    ) -> None:
        config: dict[str, Any] = {
            "apiKey": api_key,
            "secret": secret,
            "enableRateLimit": True,
            "timeout": timeout_ms,
            "options": {"defaultType": "future", "fetchMarkets": ["linear"], "adjustForTimeDifference": True},
            # Honour HTTPS_PROXY when one is configured (no effect otherwise).
            "aiohttp_trust_env": True,
        }
        if cafile:
            config["cafile"] = cafile  # verification stays on
        self._client = ccxt_async.binanceusdm(config)
        if testnet:
            # testnet.binancefuture.com: real signed API, test funds only. ccxt's
            # set_sandbox_mode() refuses futures since Binance announced Demo Trading as the
            # successor, so the fapi hosts are pointed at the testnet directly.
            urls = self._client.urls
            for key, url in urls["test"].items():
                if key.startswith("fapi"):
                    urls["api"][key] = url

    def __repr__(self) -> str:
        return "CcxtFuturesTransport(<credentials hidden>)"

    async def call(self, name: str, params: dict[str, Any]) -> Any:
        if not name.startswith("fapi"):
            raise PermissionError(f"only fapi endpoints are allowed, got {name}")
        method = getattr(self._client, name)
        return await method(params)

    async def close(self) -> None:
        await self._client.close()
