from __future__ import annotations

import logging
import os
from typing import Any

import httpx
import pytest
import redis.asyncio as aioredis
from nexus_common import events
from nexus_notify.formatting import format_event
from nexus_notify.service import Notifier
from nexus_notify.telegram import SendResult, TelegramClient

TOKEN = "123456:SECRET-BOT-TOKEN"


def test_messages_state_mode_and_escape_html() -> None:
    msg = format_event({"type": "signal", "mode": "paper", "strategy": "<b>x</b>",
                        "symbol": "BTC/USDT", "entry": 1, "stop": 0.9, "target": 1.2,
                        "status": "rejected", "reason": "a & b"})
    assert msg is not None and msg.startswith("📡 [PAPER]")
    assert "&lt;b&gt;x&lt;/b&gt;" in msg and "a &amp; b" in msg
    for kind in ("order_submitted", "stop_loss", "take_profit", "risk_warning",
                 "exchange_disconnected", "exchange_connected", "emergency_shutdown",
                 "order_state_unknown", "daily_report"):
        assert format_event({"type": kind, "mode": "live"}) is not None, kind
    assert "[LIVE]" in (format_event({"type": "stop_loss", "mode": "live"}) or "")
    assert format_event({"type": "nonsense"}) is None


def client(handler: Any) -> TelegramClient:
    return TelegramClient(TOKEN, "42", client=httpx.AsyncClient(transport=httpx.MockTransport(handler)))


async def test_telegram_success_and_payload() -> None:
    seen: dict[str, Any] = {}

    def handler(req: httpx.Request) -> httpx.Response:
        seen["url"], seen["body"] = str(req.url), req.content
        return httpx.Response(200, json={"ok": True})

    res = await client(handler).send("hello")
    assert res.ok and seen["url"].endswith("/sendMessage") and b'"parse_mode":"HTML"' in seen["body"]


async def test_telegram_errors_and_token_never_exposed(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.DEBUG)
    rate = await client(lambda r: httpx.Response(429, json={"parameters": {"retry_after": 7}})).send("x")
    assert not rate.ok and rate.retry_after == 7 and not rate.permanent
    forbidden = await client(lambda r: httpx.Response(403, json={"description": "bot blocked"})).send("x")
    assert forbidden.permanent and forbidden.error == "bot blocked"

    def boom(r: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("down", request=r)

    down = await client(boom).send("x")
    assert down.error == "transport: ConnectError"
    everything = caplog.text + repr(client(boom)) + str(rate) + str(forbidden) + str(down)
    assert "SECRET-BOT-TOKEN" not in everything


class ScriptedSender:
    def __init__(self, results: list[SendResult]) -> None:
        self.results, self.texts = results, []

    async def send(self, text: str) -> SendResult:
        self.texts.append(text)
        return self.results.pop(0) if self.results else SendResult(True)


async def _nosleep(_: float) -> None:
    return None


async def test_retries_transient_then_succeeds() -> None:
    s = ScriptedSender([SendResult(False, retry_after=1), SendResult(False, error="5xx")])
    assert await Notifier(None, s, sleep=_nosleep).deliver({"type": "exchange_connected",
                                                            "exchange": "kraken"})
    assert len(s.texts) == 3


async def test_permanent_failure_not_retried_and_disabled_mode() -> None:
    s = ScriptedSender([SendResult(False, permanent=True)])
    assert await Notifier(None, s, sleep=_nosleep).deliver({"type": "emergency_shutdown"})
    assert len(s.texts) == 1
    assert await Notifier(None, None).deliver({"type": "emergency_shutdown"})


@pytest.mark.redis
async def test_stream_consumer_acks_and_redelivers_after_crash() -> None:
    url = os.environ.get("NEXUS_TEST_REDIS_URL")
    if not url:
        pytest.skip("NEXUS_TEST_REDIS_URL not set")
    r = aioredis.from_url(url)
    await r.flushdb()
    try:
        await r.xadd(events.NOTIFY_STREAM, events.encode({"type": "exchange_disconnected",
                                                          "exchange": "binance"}))
        crashed = Notifier(r, ScriptedSender([]), sleep=_nosleep)
        await crashed.ensure_group()
        # Simulate a crash: read without acking.
        await r.xreadgroup("notifier", "notifier-1", {events.NOTIFY_STREAM: ">"}, count=10)
        sender = ScriptedSender([])
        n = Notifier(r, sender, sleep=_nosleep)
        await n.ensure_group()  # idempotent
        assert await n.process_once(block_ms=10) == 1
        assert "unreachable" in sender.texts[0]
        pending = await r.xpending(events.NOTIFY_STREAM, "notifier")
        assert pending["pending"] == 0
        assert await n.process_once(block_ms=10) == 0
    finally:
        await r.flushdb()
        await r.aclose()
