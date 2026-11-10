"""Long-running scheduler: ingest each (pair, timeframe) shortly after its candle
closes, refresh latest prices, retry open gaps, and write heartbeats.

`Scheduler.tick(now)` is deterministic and unit-tested; `run_forever` adds the
loop, sleeping, and error isolation (one failing pair never stops the others).
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from functools import partial
from typing import Protocol

from .logs import log
from .marketdata.ingest import Ingestor, Pair
from .marketdata.types import MarketDataError, SourceBlockedError
from .timeframes import floor_to

logger = logging.getLogger("nexus.worker")

GAP_RETRY_INTERVAL = timedelta(minutes=10)


class SignalRunner(Protocol):
    def evaluate(self, pair: Pair, tf: str, as_of: datetime | None = None) -> object: ...

    def resolve_open(self) -> int: ...


@dataclass
class Scheduler:
    ingestor: Ingestor
    pairs: list[Pair]
    close_delay: timedelta
    ticker_interval: timedelta
    signals: SignalRunner | None = None
    jobs: Callable[[], int] | None = None  # e.g. one queued backtest per tick
    last_bucket: dict[tuple[int, str], datetime] = field(default_factory=dict)
    last_ticker: datetime | None = None
    last_gap_retry: datetime | None = None
    blocked: str | None = None
    errors: int = 0

    def due_ingestions(self, now: datetime) -> list[tuple[Pair, str]]:
        due: list[tuple[Pair, str]] = []
        for pair in self.pairs:
            for tf in pair.timeframes:
                bucket = floor_to(now, tf)
                if now < bucket + self.close_delay:
                    continue  # give the source time to finalize the candle that just closed
                if self.last_bucket.get((pair.id, tf)) != bucket:
                    due.append((pair, tf))
        return due

    def tick(self, now: datetime) -> dict[str, int]:
        stats = {"ingested": 0, "evaluated": 0, "resolved": 0, "prices": 0, "gaps_filled": 0, "errors": 0}
        if self.blocked:
            self._heartbeat(now, stats)
            return stats
        ingested: list[tuple[Pair, str]] = []
        for pair, tf in self.due_ingestions(now):
            if self.blocked:
                break
            if self._guard(partial(self.ingestor.ingest_incremental, pair, tf), stats, pair=pair.symbol, timeframe=tf):
                self.last_bucket[(pair.id, tf)] = floor_to(now, tf)
                stats["ingested"] += 1
                ingested.append((pair, tf))
        if self.signals is not None:
            # Evaluate only after every due timeframe is ingested, so higher-timeframe data is current.
            for pair, tf in ingested:
                if self._guard(partial(self.signals.evaluate, pair, tf, floor_to(now, tf)), stats, pair=pair.symbol, timeframe=tf, task="evaluate"):
                    stats["evaluated"] += 1
            resolved = [0]

            def resolve(out: list[int] = resolved) -> None:
                assert self.signals is not None
                out[0] = self.signals.resolve_open()

            if self._guard(resolve, stats, task="resolve"):
                stats["resolved"] += resolved[0]
        if self.jobs is not None:
            done = [0]

            def run_jobs(out: list[int] = done) -> None:
                assert self.jobs is not None
                out[0] = self.jobs()

            if self._guard(run_jobs, stats, task="jobs"):
                stats["jobs"] = done[0]
        if self.last_ticker is None or now - self.last_ticker >= self.ticker_interval:
            for pair in self.pairs:
                if self.blocked:
                    break
                if self._guard(partial(self.ingestor.update_price, pair), stats, pair=pair.symbol, task="price"):
                    stats["prices"] += 1
            self.last_ticker = now
        if self.last_gap_retry is None or now - self.last_gap_retry >= GAP_RETRY_INTERVAL:
            for pair in self.pairs:
                for tf in pair.timeframes:
                    if self.blocked:
                        break
                    filled = [0]

                    def retry(p: Pair = pair, t: str = tf, out: list[int] = filled) -> None:
                        out[0] = self.ingestor.resolve_gaps(p, t)[0]

                    if self._guard(retry, stats, pair=pair.symbol, timeframe=tf, task="gaps"):
                        stats["gaps_filled"] += filled[0]
            self.last_gap_retry = now
        self._heartbeat(now, stats)
        return stats

    def _guard(self, fn: Callable[[], object], stats: dict[str, int], **ctx: str) -> bool:
        try:
            fn()
            return True
        except SourceBlockedError as e:
            # 418/451: stop all source traffic and alert. Never rotate IPs or evade.
            self.blocked = str(e)
            log(logger, logging.ERROR, "source_blocked", reason=str(e), **ctx)
        except MarketDataError as e:
            log(logger, logging.WARNING, "ingest_error", error=type(e).__name__, detail=str(e)[:200], **ctx)
        except Exception as e:  # noqa: BLE001 - isolate failures: one pair must never stop the worker
            log(logger, logging.ERROR, "ingest_unexpected_error", error=type(e).__name__, **ctx)
        stats["errors"] += 1
        self.errors += 1
        try:
            self.ingestor.conn.rollback()
        except Exception:  # noqa: S110, BLE001 - best effort on a broken connection
            pass
        return False

    def _heartbeat(self, now: datetime, stats: dict[str, int]) -> None:
        try:
            self.ingestor.heartbeat(
                "marketdata.worker",
                {"source": self.ingestor.source.key, "blocked": self.blocked, "last_tick": now.isoformat(), **stats},
            )
        except Exception as e:  # noqa: BLE001 - a heartbeat failure must not stop ingestion
            log(logger, logging.WARNING, "heartbeat_failed", error=type(e).__name__)


def run_forever(scheduler: Scheduler, tick_s: float, now: Callable[[], datetime] = lambda: datetime.now(UTC)) -> None:
    log(logger, logging.INFO, "worker_started", pairs=[p.symbol for p in scheduler.pairs])
    while True:
        scheduler.tick(now())
        time.sleep(tick_s)
