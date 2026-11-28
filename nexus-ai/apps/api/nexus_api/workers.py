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
import json
import sys
import threading
from collections.abc import Awaitable, Callable, Sequence
from datetime import datetime, timedelta
from typing import Any

from nexus_common.logging import get_logger
from nexus_common.timeutil import utcnow
from nexus_config import Settings, TradingMode

log = get_logger("nexus.api.workers")

# Inline workers start one by one, this many seconds apart (in WORKERS order, market data
# and the trader first), so a deploy does not open every Redis and database connection
# at the same moment while the old instance still holds its own (see
# nexus_common.redisutil).
STAGGER_SECONDS = 1.0

# Each worker's state, published every PUBLISH_SECONDS for the Guardian
# (nexus_api.guardian_worker): running or restarting, restarts in the last hour, last error.
STATUS_KEY = "nexus:workers:status"
PUBLISH_SECONDS = 30.0

Publish = Callable[[dict[str, Any]], Awaitable[None]]


class WorkerStates:
    """What the supervisors know about their workers (no secrets: error text is cut)."""

    def __init__(self, names: Sequence[str]) -> None:
        self.state: dict[str, dict[str, Any]] = {
            n: {"state": "waiting", "since": None, "last_error": None, "restarts": 0}
            for n in names}
        self.crashes: dict[str, list[datetime]] = {n: [] for n in names}

    def started(self, name: str) -> None:
        self.state[name].update(state="running", since=utcnow().isoformat())

    def stopped(self, name: str, error: str | None) -> None:
        now = utcnow()
        self.crashes[name] = [t for t in self.crashes[name] if now - t < timedelta(hours=1)]
        self.crashes[name].append(now)
        self.state[name].update(state="restarting", since=now.isoformat(),
                                last_error=(error or "exited")[:200],
                                restarts=self.state[name]["restarts"] + 1)

    def snapshot(self) -> dict[str, Any]:
        now = utcnow()
        return {"at": now.isoformat(), "workers": {
            n: {**w, "restarts_1h": sum(1 for t in self.crashes[n]
                                        if now - t < timedelta(hours=1))}
            for n, w in self.state.items()}}


def redis_publisher(url: str) -> Publish:
    """Writes the snapshot to Redis (one connection, opened on first use, in the loop
    that publishes)."""
    conn: list[Any] = []

    async def publish(doc: dict[str, Any]) -> None:
        if not conn:
            from nexus_common.redisutil import connect as connect_redis

            conn.append(connect_redis(url, max_connections=1))
        await conn[0].set(STATUS_KEY, json.dumps(doc), ex=180)

    return publish


async def publish_loop(states: WorkerStates, publish: Publish, stop: asyncio.Event,
                       every: float = PUBLISH_SECONDS) -> None:
    while not stop.is_set():
        try:
            await publish(states.snapshot())
        except Exception as exc:  # Redis may be down: the Guardian reports a stale status
            log.warning("worker_status_publish_failed", error=type(exc).__name__)
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(stop.wait(), timeout=every)

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
    "grid-paper": ["-m", "nexus_execution.grid_paper"],
    "adaptive-grid": ["-m", "nexus_execution.adaptive_grid_paper"],
    "rebalance-paper": ["-m", "nexus_backtest.rebalance_paper"],
    "voltarget-paper": ["-m", "nexus_backtest.voltarget_paper"],
    "multiasset-research": ["-m", "nexus_backtest.multiasset_paper"],
    "module-observer": ["-m", "nexus_backtest.module_observer"],
    "orchestrator": ["-m", "nexus_api.orchestrator_worker"],
    "guardian": ["-m", "nexus_api.guardian_worker"],
    "x-sentiment": ["-m", "nexus_social.service"],
}


# Live mode on one server: no paper forward tests, no paper bot (paper ledgers stay frozen).
LIVE_WORKERS = ("market-data", "trader", "notifier", "news", "derivatives", "decision-log",
                "guardian")


class WorkerSupervisor:
    def __init__(self, commands: dict[str, Sequence[str]], *, base_delay: float = 5.0,
                 max_delay: float = 300.0, grace: float = 20.0,
                 publish: Publish | None = None) -> None:
        self.commands = commands
        self.base_delay, self.max_delay, self.grace = base_delay, max_delay, grace
        self.procs: dict[str, asyncio.subprocess.Process] = {}
        self.restarts: dict[str, int] = dict.fromkeys(commands, 0)
        self.states = WorkerStates(list(commands))
        self.publish = publish
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
            return cls({n: [sys.executable, *WORKERS[n]] for n in LIVE_WORKERS},
                       publish=redis_publisher(settings.redis_url.get_secret_value()))
        if settings.trading_mode is not TradingMode.PAPER:
            log.warning("workers_not_started", reason=f"no workers for mode "
                        f"{settings.trading_mode.value}")
            return None
        if settings.workers_mode == "inline":
            return InlineWorkers.for_settings(settings)
        return cls({name: [sys.executable, *args] for name, args in WORKERS.items()
                    if name != "x-sentiment" or settings.x_bearer_token is not None},
                   publish=redis_publisher(settings.redis_url.get_secret_value()))

    def start(self) -> None:
        self._tasks = [asyncio.create_task(self._keep(name)) for name in self.commands]
        if self.publish is not None:
            self._tasks.append(asyncio.create_task(
                publish_loop(self.states, self.publish, self._stop)))

    async def _keep(self, name: str) -> None:
        delay = self.base_delay
        while not self._stop.is_set():
            proc = await asyncio.create_subprocess_exec(*self.commands[name])
            self.procs[name] = proc
            log.info("worker_started", worker=name, pid=proc.pid)
            self.states.started(name)
            code = await proc.wait()
            if self._stop.is_set():
                return
            self.restarts[name] += 1
            self.states.stopped(name, f"exit code {code}")
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
                 max_delay: float = 300.0, grace: float = 20.0, threaded: bool = False,
                 stagger: float = 0.0, publish: Publish | None = None) -> None:
        self.workers = workers
        self.base_delay, self.max_delay, self.grace = base_delay, max_delay, grace
        self.stagger = stagger
        self.restarts: dict[str, int] = dict.fromkeys(workers, 0)
        self.states = WorkerStates(list(workers))
        self.publish = publish
        self.threaded = threaded
        self._stop = asyncio.Event()
        self._tasks: list[asyncio.Task[None]] = []
        self._thread: threading.Thread | None = None
        self._loop: asyncio.AbstractEventLoop | None = None

    @classmethod
    def for_settings(cls, settings: Settings, *,
                     only: Sequence[str] | None = None) -> InlineWorkers:
        from nexus_backtest import (
            forward_taker,
            library_forward,
            module_observer,
            multiasset_paper,
            rebalance_paper,
            trend_paper,
            voltarget_paper,
        )
        from nexus_execution import adaptive_grid_paper, bot, grid_paper, spot_paper, trader
        from nexus_market import carry_paper, derivatives, runner
        from nexus_news import service as news
        from nexus_notify import service as notifier
        from nexus_social import service as social

        from nexus_api import decision_log, guardian_worker, orchestrator_worker

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
            "bot": lambda stop: bot.serve(settings, stop),
            "grid-paper": lambda stop: grid_paper.serve(settings, stop),
            "adaptive-grid": lambda stop: adaptive_grid_paper.serve(settings, stop),
            "rebalance-paper": lambda stop: rebalance_paper.serve(settings, stop),
            "voltarget-paper": lambda stop: voltarget_paper.serve(settings, stop),
            "multiasset-research": lambda stop: multiasset_paper.serve(settings, stop),
            "module-observer": lambda stop: module_observer.serve(settings, stop),
            "orchestrator": lambda stop: orchestrator_worker.serve(settings, stop),
            "guardian": lambda stop: guardian_worker.serve(settings, stop)}
        if settings.x_bearer_token is not None:
            workers["x-sentiment"] = lambda stop: social.serve(settings, stop)
        if only is not None:
            workers = {n: w for n, w in workers.items() if n in only}
        return cls(workers, threaded=True, stagger=STAGGER_SECONDS,
                   publish=redis_publisher(settings.redis_url.get_secret_value()))

    def _status_task(self) -> list[asyncio.Task[None]]:
        if self.publish is None:
            return []
        return [asyncio.create_task(publish_loop(self.states, self.publish, self._stop))]

    def start(self) -> None:
        if not self.threaded:
            self._tasks = [asyncio.create_task(self._keep(n, i * self.stagger))
                           for i, n in enumerate(self.workers)] + self._status_task()
            return
        ready = threading.Event()
        self._thread = threading.Thread(target=self._thread_main, args=(ready,),
                                        name="nexus-workers", daemon=True)
        self._thread.start()
        ready.wait(timeout=10)

    def _thread_main(self, ready: threading.Event) -> None:
        async def main() -> None:
            self._loop = asyncio.get_running_loop()
            self._tasks = [asyncio.create_task(self._keep(n, i * self.stagger))
                           for i, n in enumerate(self.workers)] + self._status_task()
            ready.set()
            await self._stop.wait()
            await self._drain()

        try:
            asyncio.run(main())
        except Exception as exc:  # pragma: no cover - logged, the API keeps serving
            log.error("worker_thread_crashed", error=f"{type(exc).__name__}: {exc}"[:300])
        finally:
            ready.set()

    async def _keep(self, name: str, start_after: float = 0.0) -> None:
        if start_after > 0:                 # staggered start: see STAGGER_SECONDS
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(self._stop.wait(), timeout=start_after)
        delay = self.base_delay
        while not self._stop.is_set():
            log.info("worker_started", worker=name, mode="inline")
            self.states.started(name)
            error = None
            try:
                await self.workers[name](self._stop)
                if self._stop.is_set():
                    return
                log.error("worker_exited", worker=name, restart_in=delay)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                error = f"{type(exc).__name__}: {exc}"[:300]
                log.error("worker_crashed", worker=name, restart_in=delay, error=error)
            self.restarts[name] += 1
            self.states.stopped(name, error)
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
