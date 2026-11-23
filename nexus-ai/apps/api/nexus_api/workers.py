"""Run the workers next to the API on one small server.

Enabled by NEXUS_RUN_WORKERS=true. NEXUS_WORKERS_MODE=process (default) runs them as child
processes; =inline runs them as tasks on one event loop in a dedicated thread (about half
the memory, for small servers). A worker that exits is restarted after a capped back-off
and every exit is logged.

* Paper mode: every worker (market data, paper trader, bot, forward tests, monitoring).
* Live mode: only LIVE_WORKERS. The trader still needs every live gate (live_gate.py:
  the owner's configuration, 2FA approval, kill switch clear, key test) before it can
  build a live broker. This module never bypasses the gate and cannot enable live
  trading by itself.
"""

from __future__ import annotations

import asyncio
import contextlib
import sys
import threading
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
    "decision-log": ["-m", "nexus_api.decision_log"],
    "trend-paper": ["-m", "nexus_backtest.trend_paper"],
    "spot-engine": ["-m", "nexus_execution.spot_paper"],
    "library-paper": ["-m", "nexus_backtest.library_forward"],
    "bot": ["-m", "nexus_execution.bot"],
    "x-sentiment": ["-m", "nexus_social.service"],
}


# Live mode on one server: no paper forward tests, no paper bot (paper ledgers stay frozen).
LIVE_WORKERS = ("market-data", "trader", "notifier", "news", "derivatives", "decision-log")


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
        if settings.trading_mode is TradingMode.LIVE:
            # Single server in live mode: market data, the LIVE trader and monitoring only.
            # The trader builds its broker through live_gate.build_live_broker, which
            # refuses (and the worker retries with back-off) until EVERY gate is met: the
            # owner's Render configuration, 2FA approval, kill switch clear, key test.
            # Nothing here can enable live trading by itself.
            log.warning("live_workers_starting", workers=list(LIVE_WORKERS))
            if settings.workers_mode == "inline":
                return InlineWorkers.for_settings(settings, only=LIVE_WORKERS)
            return cls({n: [sys.executable, *WORKERS[n]] for n in LIVE_WORKERS})
        if settings.trading_mode is not TradingMode.PAPER:
            log.warning("workers_not_started", reason=f"no workers for mode "
                        f"{settings.trading_mode.value}")
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
    """Workers as tasks in one event loop. A worker that raises or returns is restarted
    after a capped back-off; stop() sets the shared stop event and waits.

    ``threaded=True`` (production) runs that loop in its own thread. The workers make
    blocking database calls (the database is across the Atlantic, about 150 ms per round
    trip); on the API's loop each call froze every dashboard request. On their own loop
    they only delay each other, and the API stays responsive."""

    def __init__(self, workers: dict[str, Serve], *, base_delay: float = 5.0,
                 max_delay: float = 300.0, grace: float = 20.0, threaded: bool = False) -> None:
        self.workers = workers
        self.base_delay, self.max_delay, self.grace = base_delay, max_delay, grace
        self.restarts: dict[str, int] = dict.fromkeys(workers, 0)
        self.threaded = threaded
        self._stop = asyncio.Event()
        self._tasks: list[asyncio.Task[None]] = []
        self._thread: threading.Thread | None = None
        self._loop: asyncio.AbstractEventLoop | None = None

    @classmethod
    def for_settings(cls, settings: Settings, *,
                     only: Sequence[str] | None = None) -> InlineWorkers:
        from nexus_backtest import forward_taker, library_forward, trend_paper
        from nexus_execution import bot, spot_paper, trader
        from nexus_market import carry_paper, derivatives, runner
        from nexus_news import service as news
        from nexus_notify import service as notifier
        from nexus_social import service as social

        from nexus_api import decision_log

        workers: dict[str, Serve] = {
            "market-data": lambda stop: runner.serve(settings, stop),
            "trader": lambda stop: trader.serve(settings, stop),
            "notifier": lambda stop: notifier.serve(settings, stop),
            "news": lambda stop: news.serve(settings, stop),
            "derivatives": lambda stop: derivatives.serve(settings, stop),
            "carry-paper": lambda stop: carry_paper.serve(settings, stop),
            "forward-taker": lambda stop: forward_taker.serve(settings, stop),
            "decision-log": lambda stop: decision_log.serve(settings, stop),
            "trend-paper": lambda stop: trend_paper.serve(settings, stop),
            "spot-engine": lambda stop: spot_paper.serve(settings, stop),
            "library-paper": lambda stop: library_forward.serve(settings, stop),
            "bot": lambda stop: bot.serve(settings, stop)}
        if settings.x_bearer_token is not None:
            workers["x-sentiment"] = lambda stop: social.serve(settings, stop)
        if only is not None:
            workers = {n: w for n, w in workers.items() if n in only}
        return cls(workers, threaded=True)

    def start(self) -> None:
        if not self.threaded:
            self._tasks = [asyncio.create_task(self._keep(n)) for n in self.workers]
            return
        ready = threading.Event()
        self._thread = threading.Thread(target=self._thread_main, args=(ready,),
                                        name="nexus-workers", daemon=True)
        self._thread.start()
        ready.wait(timeout=10)

    def _thread_main(self, ready: threading.Event) -> None:
        async def main() -> None:
            self._loop = asyncio.get_running_loop()
            self._tasks = [asyncio.create_task(self._keep(n)) for n in self.workers]
            ready.set()
            await self._stop.wait()
            await self._drain()

        try:
            asyncio.run(main())
        except Exception as exc:  # pragma: no cover - logged, the API keeps serving
            log.error("worker_thread_crashed", error=f"{type(exc).__name__}: {exc}"[:300])
        finally:
            ready.set()

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
        if self.threaded:
            if self._loop is not None and self._thread is not None:
                self._loop.call_soon_threadsafe(self._stop.set)
                await asyncio.to_thread(self._thread.join, self.grace + 10)
                if self._thread.is_alive():
                    log.error("worker_thread_not_stopped", grace=self.grace)
            return
        self._stop.set()
        await self._drain()

    async def _drain(self) -> None:
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
