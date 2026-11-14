from __future__ import annotations

import asyncio

from nexus_market.stream import ReconnectingStream


async def test_reconnects_after_errors_and_stale_and_reports_state() -> None:
    stop = asyncio.Event()
    script: list[object] = [ConnectionError("reset"), "a", "b", "STALL", "c"]
    received: list[str] = []
    states: list[tuple[bool, str | None]] = []
    sleeps: list[float] = []
    resets = 0

    async def watch() -> str:
        item = script.pop(0) if script else "STOP"
        if isinstance(item, Exception):
            raise item
        if item == "STALL":
            await asyncio.sleep(10)
        if item == "STOP":
            stop.set()
        return str(item)

    async def on_update(x: str) -> None:
        received.append(x)

    async def on_state(name: str, connected: bool, err: str | None) -> None:
        states.append((connected, err))

    async def reset() -> None:
        nonlocal resets
        resets += 1

    async def fake_sleep(s: float) -> None:
        sleeps.append(s)

    stream = ReconnectingStream("t", watch, on_update, on_state=on_state, reset=reset,
                                stale_after=0.05, sleep=fake_sleep)
    await asyncio.wait_for(stream.run(stop), timeout=2)
    assert received == ["a", "b", "c", "STOP"]
    assert stream.stats.reconnects == 2 and resets == 2
    assert [s[0] for s in states] == [True, False, True, False]
    assert states[1][1] is not None and states[1][1].startswith("stale")
    assert len(sleeps) == 2


async def test_initial_failure_reports_disconnected_once_connected() -> None:
    """No spurious 'disconnected' before the first connection, but failures still back off."""
    stop = asyncio.Event()
    calls = 0

    async def watch() -> int:
        nonlocal calls
        calls += 1
        if calls == 3:
            stop.set()
        raise RuntimeError("down")

    async def noop(*_: object) -> None:
        return None

    stream = ReconnectingStream("t", watch, noop, sleep=noop)
    await stream.run(stop)
    assert stream.stats.consecutive_failures == 3 and stream.stats.connected is False


def test_backoff_grows_and_is_capped() -> None:
    async def noop(*_: object) -> None:
        return None

    s = ReconnectingStream("t", noop, noop, backoff_min=1, backoff_max=8)  # type: ignore[arg-type]
    for failures, cap in [(1, 1), (2, 2), (3, 4), (4, 8), (10, 8)]:
        for _ in range(20):
            assert cap / 2 <= s.backoff(failures) <= cap
