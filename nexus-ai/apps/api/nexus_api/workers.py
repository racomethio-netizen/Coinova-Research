"""Run the market-data and paper-trader workers next to the API on one small server.

Enabled by NEXUS_RUN_WORKERS=true. NEXUS_WORKERS_MODE=process (default) runs them as
child processes; =inline runs them as tasks in the API's event loop (about half the
memory, for 512 MB servers). Either way it is, and only when NEXUS_TRADING_MODE is paper: this module
never starts a live trader. Each worker is a child process; one that exits is restarted
after a delay (capped back-off) and every exit is logged. Shutdown sends SIGTERM and
waits, then kills.
"""

from __future__ import annotations

import asyncio
import contextlib
import sys
from collections.abc import Awaitable, Callable, Sequence

from nexus_common.logging import get_logger
from nexus_config import Settings, TradingMode

log = get_logger("nexus.api.workers")

WORKERS: dict[str, list[str]] = {
    "market-data": ["-m", "nexus_market.runner"],
    "trader": ["-m", "nexus_execution.trader"],
    "notifier": ["-m", "nexus_notify.service"],
    "news": ["-m", "nexus_news.service"],
    "derivatives": ["-m", "nexus_market.derivatives"],
    "carry-paper": ["-m", "nexus_market.carry_paper"],
    "forward-taker": ["-m", "nexus_backtest.forward_taker"],
    "x-sentiment": ["-m", "nexus_social.service"],
}


class WorkerSupervisor:
    def __init__(self, commands: dict[str, Sequence[str]], *, base_delay: float = 5.0,
                 max_delay: float = 300.0, grace: float = 20.0) -> None:
        self.commands = commands
        self.base_delay, self.max_delay, self.grace = base_delay, max_delay, grace
        self.procs: dict[str, asyncio.subprocess.Process] = {}
        self.restarts: dict[str, int] = dict.fromkeys(commands, 0)
        self._stop = asyncio.Event()
        self._tasks: list[asyncio.Task[None]] = []

    @classmethod
    def from_settings(cls, settings: Settings) -> WorkerSupervisor | InlineWorkers | None:
        if not settings.run_workers:
            return None
        if settings.trading_mode is not TradingMode.PAPER:
            log.warning("workers_not_started", reason="NEXUS_RUN_WORKERS only runs paper mode")
            return None
        if settings.workers_mode == "inline":
            return InlineWorkers.for_settings(settings)
        return cls({name: [sys.executable, *args] for name, args in WORKERS.items()
                    if name != "x-sentiment" or settings.x_bearer_token is not None})

    def start(self) -> None:
        self._tasks = [asyncio.create_task(self._keep(name)) for name in self.commands]

    async def _keep(self, name: str) -> None:
        delay = self.base_delay
        while not self._stop.is_set():
            proc = await asyncio.create_subprocess_exec(*self.commands[name])
            self.procs[name] = proc
            log.info("worker_started", worker=name, pid=proc.pid)
            code = await proc.wait()
            if self._stop.is_set():
                return
            self.restarts[name] += 1
            log.error("worker_exited", worker=name, code=code, restart_in=delay)
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(self._stop.wait(), timeout=delay)
            delay = min(delay * 2, self.max_delay)

    async def stop(self) -> None:
        self._stop.set()
        for proc in self.procs.values():
            if proc.returncode is None:
                proc.terminate()
        for name, proc in self.procs.items():
            try:
                await asyncio.wait_for(proc.wait(), timeout=self.grace)
            except TimeoutError:
                log.error("worker_killed", worker=name)
                proc.kill()
                await proc.wait()
        for t in self._tasks:
            t.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)


Serve = Callable[[asyncio.Event], Awaitable[None]]


class InlineWorkers:
    """Workers as tasks in the current event loop. A worker that raises or returns is
    restarted after a capped back-off; stop() sets the shared stop event and waits."""

    def __init__(self, workers: dict[str, Serve], *, base_delay: float = 5.0,
                 max_delay: float = 300.0, grace: float = 20.0) -> None:
        self.workers = workers
        self.base_delay, self.max_delay, self.grace = base_delay, max_delay, grace
        self.restarts: dict[str, int] = dict.fromkeys(workers, 0)
        self._stop = asyncio.Event()
        self._tasks: list[asyncio.Task[None]] = []

    @classmethod
    def for_settings(cls, settings: Settings) -> InlineWorkers:
        from nexus_backtest import forward_taker
        from nexus_execution import trader
        from nexus_market import carry_paper, derivatives, runner
        from nexus_news import service as news
        from nexus_notify import service as notifier
        from nexus_social import service as social

        workers: dict[str, Serve] = {
            "market-data": lambda stop: runner.serve(settings, stop),
            "trader": lambda stop: trader.serve(settings, stop),
            "notifier": lambda stop: notifier.serve(settings, stop),
            "news": lambda stop: news.serve(settings, stop),
            "derivatives": lambda stop: derivatives.serve(settings, stop),
            "carry-paper": lambda stop: carry_paper.serve(settings, stop),
            "forward-taker": lambda stop: forward_taker.serve(settings, stop)}
        if settings.x_bearer_token is not None:
            workers["x-sentiment"] = lambda stop: social.serve(settings, stop)
        return cls(workers)

    def start(self) -> None:
        self._tasks = [asyncio.create_task(self._keep(n)) for n in self.workers]

    async def _keep(self, name: str) -> None:
        delay = self.base_delay
        while not self._stop.is_set():
            log.info("worker_started", worker=name, mode="inline")
            try:
                await self.workers[name](self._stop)
                if self._stop.is_set():
                    return
                log.error("worker_exited", worker=name, restart_in=delay)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                log.error("worker_crashed", worker=name, restart_in=delay,
                          error=f"{type(exc).__name__}: {exc}"[:300])
            self.restarts[name] += 1
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(self._stop.wait(), timeout=delay)
            delay = min(delay * 2, self.max_delay)

    async def stop(self) -> None:
        self._stop.set()
        if not self._tasks:
            return
        _, pending = await asyncio.wait(self._tasks, timeout=self.grace)
        for t in pending:
            log.error("worker_cancelled", task=t.get_name())
            t.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)


KEEP_AWAKE_SECONDS = 600


async def keep_awake(url: str, stop: asyncio.Event, *,
                     every: float = KEEP_AWAKE_SECONDS) -> None:
    """Request ``url``/health/live until ``stop`` is set. Failures are logged, not raised."""
    import httpx

    target = url.rstrip("/") + "/health/live"
    async with httpx.AsyncClient(timeout=20) as client:
        while not stop.is_set():
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(stop.wait(), timeout=every)
            if stop.is_set():
                return
            try:
                r = await client.get(target)
                if r.status_code != 200:
                    log.warning("keep_awake_status", status=r.status_code)
            except httpx.HTTPError as exc:
                log.warning("keep_awake_failed", error=type(exc).__name__)
