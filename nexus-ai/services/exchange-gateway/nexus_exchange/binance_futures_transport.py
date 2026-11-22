"""Production transport: CCXT's signed Binance USD-M methods (fapi*), nothing else.

Credentials are passed only to the ccxt client and are never logged or repr'd.
"""

from __future__ import annotations

import time
from typing import Any

import ccxt.async_support as ccxt_async


class CcxtFuturesTransport:
    def __init__(
        self, api_key: str, secret: str, *, timeout_ms: int = 10_000, cafile: str | None = None,
        environment: str = "production",
    ) -> None:
        """``environment``: "production", "testnet" (testnet.binancefuture.com) or "demo"
        (Binance Demo Trading, demo-fapi.binance.com). Test funds only outside production."""
        if environment not in ("production", "testnet", "demo"):
            raise ValueError(f"unknown environment {environment!r}")
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
        if environment == "production":
            from nexus_common.binance_guard import CcxtGate, default_redis

            if default_redis() is not None:
                self.gate = CcxtGate(default_redis(), "futures")
        if environment == "demo":
            self._client.enable_demo_trading(True)
        elif environment == "testnet":
            # testnet.binancefuture.com: real signed API, test funds only. ccxt's
            # set_sandbox_mode() refuses futures since Binance announced Demo Trading as the
            # successor, so the fapi hosts are pointed at the testnet directly.
            urls = self._client.urls
            for key, url in urls["test"].items():
                if key.startswith("fapi"):
                    urls["api"][key] = url

    # Central Binance rate-limit gate (nexus_common.binance_guard.CcxtGate) or None.
    gate: Any = None

    def __repr__(self) -> str:
        return "CcxtFuturesTransport(<credentials hidden>)"

    async def call(self, name: str, params: dict[str, Any]) -> Any:
        if not name.startswith("fapi"):
            raise PermissionError(f"only fapi endpoints are allowed, got {name}")
        method = getattr(self._client, name)
        if self.gate is None:
            return await method(params)
        from nexus_common.binance_guard import endpoint_weight, status_of_exception

        weight = endpoint_weight("futures", name, params)
        probe = await self.gate.acquire(name, weight)   # BinanceBackoff: nothing is sent
        started = time.monotonic()
        try:
            result = await method(params)
        except Exception as exc:
            await self.gate.report(name, weight, status=status_of_exception(exc),
                                   headers=self._client.last_response_headers,
                                   text=str(exc)[:500], ms=(time.monotonic() - started) * 1000,
                                   probe=probe)
            raise
        await self.gate.report(name, weight, status=200,
                               headers=self._client.last_response_headers, text="",
                               ms=(time.monotonic() - started) * 1000, probe=probe)
        return result

    async def close(self) -> None:
        await self._client.close()
