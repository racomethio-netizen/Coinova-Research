"""Optional fixed-IP egress for Binance traffic ONLY.

Binance rate limits and bans are per IP. Render's outbound IPs are shared by every service
in a region, so other customers' traffic counts against NEXUS. Routing Binance calls through
a static-IP HTTP proxy (for example QuotaGuard Static, which Render documents) gives NEXUS
its own IP that Binance sees, and lets the owner restrict the API key to it.

Configured by environment only, never in code or logs (the URL contains a password):
``NEXUS_BINANCE_PROXY_URL`` or, as QuotaGuard names it, ``QUOTAGUARDSTATIC_URL``. When
neither is set, nothing changes. Telegram, news, X and the database never use it.
"""

from __future__ import annotations

import os
from typing import Any
from urllib.parse import urlsplit

ENV_NAMES = ("NEXUS_BINANCE_PROXY_URL", "QUOTAGUARDSTATIC_URL")


def binance_proxy_url() -> str | None:
    """The proxy URL (http/https only), or None. Raises ValueError on a malformed URL so a
    typo fails loudly instead of silently sending traffic from the shared IP."""
    for name in ENV_NAMES:
        raw = os.environ.get(name, "").strip()
        if not raw:
            continue
        parts = urlsplit(raw)
        if parts.scheme not in ("http", "https") or not parts.hostname:
            raise ValueError(f"{name} must be an http(s)://[user:password@]host:port URL")
        return raw
    return None


def describe() -> str | None:
    """Host and port only (safe to log or show); never the credentials."""
    url = binance_proxy_url()
    if url is None:
        return None
    p = urlsplit(url)
    return f"{p.hostname}:{p.port}" if p.port else str(p.hostname)


def apply_to_ccxt(client: Any) -> Any:
    """Route a ccxt (REST and, for ccxt.pro, WebSocket) Binance client via the proxy."""
    url = binance_proxy_url()
    if url is not None:
        client.httpsProxy = url
        client.wssProxy = url
    return client


def httpx_kwargs() -> dict[str, Any]:
    """Extra ``httpx.AsyncClient`` arguments for a client that only calls Binance."""
    url = binance_proxy_url()
    return {"proxy": url} if url is not None else {}
