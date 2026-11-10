"""Calls the web app's authenticated cron endpoints on a fixed schedule (cron consolidation).

Replaces the separate Render cron services for the web jobs. The web side is
idempotent per time window (`background_jobs.idempotency_key`), so a duplicate or
late call is harmless. Each schedule entry runs in its own thread so a long job
(e.g. hourly scores) never delays ingestion or the per-minute notifications, and
an entry is skipped while its previous run is still going.

Only the response status is logged; the secret is never logged or echoed.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx

from ..logs import log

logger = logging.getLogger("nexus.webcron")
EPOCH = datetime(1970, 1, 1, tzinfo=UTC)


@dataclass(frozen=True)
class CronEntry:
    name: str
    jobs: tuple[str, ...]  # run in order; stops at the first failure
    period: timedelta
    offset: timedelta = timedelta(0)
    timeout_s: float = 120.0

    def slot(self, now: datetime) -> datetime:
        """Start of the most recent slot at or before `now` (slots are EPOCH + offset + k * period)."""
        k = (now - EPOCH - self.offset) // self.period
        return EPOCH + self.offset + k * self.period


# Same schedule as the former Render cron services (docs/DEPLOYMENT.md section 3).
SCHEDULE: tuple[CronEntry, ...] = (
    CronEntry("notifications", ("deliver-notifications",), timedelta(minutes=1), timeout_s=110),
    CronEntry("markets", ("refresh-markets", "evaluate-alerts"), timedelta(minutes=5), timeout_s=240),
    CronEntry("hourly", ("refresh-history", "refresh-fundamentals", "compute-scores"), timedelta(hours=1), timedelta(minutes=7), 1700),
    CronEntry("daily", ("cleanup",), timedelta(days=1), timedelta(hours=3, minutes=23), 600),
)


@dataclass
class EntryState:
    last_slot: datetime | None = None
    last_status: str = "never"  # ok | failed:<job>:<status> | skipped_running | never
    last_finished: datetime | None = None
    failures: int = 0
    thread: threading.Thread | None = None


@dataclass
class WebCron:
    base_url: str
    secret: str
    schedule: tuple[CronEntry, ...] = SCHEDULE
    transport: httpx.BaseTransport | None = None  # tests inject httpx.MockTransport
    inline: bool = False  # tests: run in the calling thread
    clock: Callable[[], datetime] = field(default=lambda: datetime.now(UTC))
    state: dict[str, EntryState] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.base_url = self.base_url.rstrip("/")
        self._lock = threading.Lock()
        for e in self.schedule:
            self.state.setdefault(e.name, EntryState())

    def due(self, now: datetime) -> list[CronEntry]:
        return [e for e in self.schedule if self.state[e.name].last_slot != e.slot(now)]

    def tick(self, now: datetime) -> list[str]:
        """Start every due entry; returns the names started."""
        started: list[str] = []
        for entry in self.due(now):
            st = self.state[entry.name]
            if st.thread is not None and st.thread.is_alive():
                with self._lock:
                    st.last_status = "skipped_running"
                continue  # retried next tick; the slot is only claimed when a run starts
            st.last_slot = entry.slot(now)
            started.append(entry.name)
            if self.inline:
                self._run(entry)
            else:
                st.thread = threading.Thread(target=self._run, args=(entry,), name=f"webcron-{entry.name}", daemon=True)
                st.thread.start()
        return started

    def _run(self, entry: CronEntry) -> None:
        status = "ok"
        try:
            with httpx.Client(timeout=entry.timeout_s, transport=self.transport, follow_redirects=False) as client:
                for job in entry.jobs:
                    try:
                        r = client.post(f"{self.base_url}/api/cron/{job}", headers={"Authorization": f"Bearer {self.secret}"})
                        code = str(r.status_code)
                        ok = r.is_success
                    except httpx.HTTPError as e:
                        code, ok = type(e).__name__, False
                    if not ok:
                        status = f"failed:{job}:{code}"
                        log(logger, logging.ERROR, "web_cron_failed", entry=entry.name, job=job, status=code)
                        break
        except Exception as e:  # noqa: BLE001 - a scheduler thread must never die silently
            status = f"failed:{type(e).__name__}"
            log(logger, logging.ERROR, "web_cron_error", entry=entry.name, error=type(e).__name__)
        with self._lock:
            st = self.state[entry.name]
            st.last_status = status
            st.last_finished = self.clock()
            st.failures = 0 if status == "ok" else st.failures + 1

    def status(self) -> dict[str, Any]:
        with self._lock:
            return {
                name: {
                    "last_slot": st.last_slot.isoformat() if st.last_slot else None,
                    "last_status": st.last_status,
                    "last_finished": st.last_finished.isoformat() if st.last_finished else None,
                    "consecutive_failures": st.failures,
                }
                for name, st in self.state.items()
            }
