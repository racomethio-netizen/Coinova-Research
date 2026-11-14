"""Telegram Bot API client. The bot token is part of the URL, so request URLs are NEVER
logged (httpx's own request logging is silenced) and errors are reported without it."""

from __future__ import annotations

import logging
from dataclasses import dataclass

import httpx

logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)


@dataclass(frozen=True)
class SendResult:
    ok: bool
    retry_after: float | None = None
    error: str | None = None
    permanent: bool = False


class TelegramClient:
    def __init__(self, token: str, chat_id: str, *, client: httpx.AsyncClient | None = None,
                 base_url: str = "https://api.telegram.org") -> None:
        self._url = f"{base_url}/bot{token}/sendMessage"
        self._chat_id = chat_id
        self._client = client or httpx.AsyncClient(timeout=10)

    def __repr__(self) -> str:
        return "TelegramClient(token=[REDACTED])"

    async def send(self, text: str) -> SendResult:
        try:
            r = await self._client.post(self._url, json={
                "chat_id": self._chat_id, "text": text[:4000], "parse_mode": "HTML",
                "disable_web_page_preview": True})
        except httpx.HTTPError as exc:
            return SendResult(False, error=f"transport: {type(exc).__name__}")
        if r.status_code == 200:
            return SendResult(True)
        try:
            body = r.json()
        except ValueError:
            body = {}
        if r.status_code == 429:
            retry = (body.get("parameters") or {}).get("retry_after", 5)
            return SendResult(False, retry_after=float(retry), error="rate limited")
        desc = str(body.get("description", r.status_code))[:200]
        # 400/401/403 won't succeed on retry (bad chat id, revoked token, blocked bot).
        return SendResult(False, error=desc, permanent=r.status_code in (400, 401, 403, 404))

    async def close(self) -> None:
        await self._client.aclose()
