"""Run the market-data and paper-trader workers next to the API on one small server.

Enabled by NEXUS_RUN_WORKERS=true, and only when NEXUS_TRADING_MODE is paper: this module
never starts a live trader. Each worker is a child process; one that exits is restarted
after a delay (capped back-off) and every exit is logged. Shutdown sends SIGTERM and
waits, then kills.
"""

from __future__ import annotations

import asyncio
import contextlib
import sys
from collections.abc import Sequence

from nexus_common.logging import get_logger
from nexus_config import Settings, TradingMode

log = get_logger("nexus.api.workers")

WORKERS: dict[str, list[str]] = {
    "market-data": ["-m", "nexus_market.runner"],
    "trader": ["-m", "nexus_execution.trader"],
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
    def from_settings(cls, settings: Settings) -> WorkerSupervisor | None:
        if not settings.run_workers:
            return None
        if settings.trading_mode is not TradingMode.PAPER:
            log.warning("workers_not_started", reason="NEXUS_RUN_WORKERS only runs paper mode")
            return None
        return cls({name: [sys.executable, *args] for name, args in WORKERS.items()})

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
