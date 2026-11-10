"""Candle ingestion: incremental updates, range backfills, gap tracking, prices.

Write rules (docs/nexus/SIGNAL_ENGINE.md §1):
- only final (closed) candles are stored;
- (pair, timeframe, open_time) is unique: re-ingesting is an idempotent upsert;
- a stored final candle is never changed: a different re-fetch is recorded in
  candle_revisions for review;
- missing intervals are recorded in candle_gaps, never interpolated.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Protocol

import psycopg

from ..config import Settings
from ..logs import log
from ..timeframes import last_closed_open_time, tf_delta
from .gaps import find_gaps
from .types import Candle, MarketDataError, PricePoint, SourceNotPermittedError
from .validation import Rejection, partition

logger = logging.getLogger("nexus.ingest")

MAX_PAGES_PER_RUN = 500
MAX_GAP_ATTEMPTS = 3


class CandleSource(Protocol):
    def fetch_klines(self, symbol: str, tf: str, start: datetime | None = None, end: datetime | None = None, limit: int = 1000) -> list[Candle]: ...

    def fetch_price(self, symbol: str) -> PricePoint: ...


@dataclass(frozen=True, slots=True)
class Source:
    id: int
    key: str
    enabled: bool
    terms_reviewed_at: datetime | None


@dataclass(frozen=True, slots=True)
class Pair:
    id: int
    symbol: str
    timeframes: tuple[str, ...]


@dataclass(slots=True)
class RunResult:
    upserted: int = 0
    rejected: int = 0
    revisions: int = 0
    gaps_recorded: int = 0
    rejections: list[Rejection] = field(default_factory=list)


def load_source(conn: psycopg.Connection, key: str) -> Source:
    row = conn.execute("select id, key, enabled, terms_reviewed_at from public.market_sources where key = %s", (key,)).fetchone()
    if row is None:
        raise SourceNotPermittedError(f"unknown market source {key!r}")
    return Source(id=row[0], key=row[1], enabled=row[2], terms_reviewed_at=row[3])


def ensure_permitted(source: Source, settings: Settings) -> None:
    """Gate G-4 (docs/nexus/COMPLIANCE.md)."""
    if not source.enabled:
        raise SourceNotPermittedError(f"market source {source.key!r} is disabled")
    if settings.is_production and source.terms_reviewed_at is None:
        raise SourceNotPermittedError(f"market source {source.key!r} has no recorded terms review; production ingestion is refused (gate G-4)")


def load_pairs(conn: psycopg.Connection, source_id: int) -> list[Pair]:
    rows = conn.execute("select id, symbol, timeframes from public.market_pairs where source_id = %s and enabled order by id", (source_id,)).fetchall()
    return [Pair(id=r[0], symbol=r[1], timeframes=tuple(r[2])) for r in rows]


def _candle_json(c: Candle | tuple[Any, ...]) -> dict[str, str | int | None]:
    if isinstance(c, Candle):
        vals: tuple[Any, ...] = (c.open, c.high, c.low, c.close, c.volume, c.quote_volume, c.trade_count)
    else:
        vals = c
    keys = ("open", "high", "low", "close", "volume", "quote_volume", "trade_count")
    return {k: (None if v is None else (v if isinstance(v, int) else str(v))) for k, v in zip(keys, vals, strict=True)}


def store_candles(conn: psycopg.Connection, pair_id: int, tf: str, candles: Sequence[Candle], run_id: int) -> tuple[int, int]:
    """Upsert final candles. Returns (rows written, revisions recorded). Caller owns the transaction."""
    finals = [c for c in candles if c.is_final]
    if not finals:
        return 0, 0
    existing = {
        r[0]: r[1:]
        for r in conn.execute(
            "select open_time, open, high, low, close, volume, quote_volume, trade_count from public.market_candles "
            "where pair_id = %s and timeframe = %s and is_final and open_time = any(%s)",
            (pair_id, tf, [c.open_time for c in finals]),
        ).fetchall()
    }
    revisions = 0
    to_write: list[Candle] = []
    for c in finals:
        old = existing.get(c.open_time)
        if old is None:
            to_write.append(c)
            continue
        new = (c.open, c.high, c.low, c.close, c.volume, c.quote_volume, c.trade_count)
        if tuple(old) != new:
            conn.execute(
                "insert into public.candle_revisions (pair_id, timeframe, open_time, original, revised, ingest_run_id) values (%s, %s, %s, %s, %s, %s)",
                (pair_id, tf, c.open_time, json.dumps(_candle_json(tuple(old))), json.dumps(_candle_json(c)), run_id),
            )
            revisions += 1
    written = 0
    with conn.cursor() as cur:
        for c in to_write:
            cur.execute(
                "insert into public.market_candles (pair_id, timeframe, open_time, close_time, open, high, low, close, volume, "
                "quote_volume, trade_count, is_final, ingest_run_id) values (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,true,%s) "
                "on conflict (pair_id, timeframe, open_time) do update set close_time = excluded.close_time, open = excluded.open, "
                "high = excluded.high, low = excluded.low, close = excluded.close, volume = excluded.volume, "
                "quote_volume = excluded.quote_volume, trade_count = excluded.trade_count, is_final = true, "
                "ingest_run_id = excluded.ingest_run_id, ingested_at = now() where public.market_candles.is_final = false",
                (pair_id, tf, c.open_time, c.close_time, c.open, c.high, c.low, c.close, c.volume, c.quote_volume, c.trade_count, run_id),
            )
            written += cur.rowcount
    return written, revisions


def record_gaps(conn: psycopg.Connection, pair_id: int, tf: str, start: datetime, end: datetime) -> int:
    """Record missing intervals in [start, end] (aligned open times). Returns new gap rows."""
    stored = [
        r[0]
        for r in conn.execute(
            "select open_time from public.market_candles where pair_id = %s and timeframe = %s and open_time between %s and %s",
            (pair_id, tf, start, end),
        ).fetchall()
    ]
    created = 0
    for gap_start, gap_end in find_gaps(stored, tf, start, end):
        cur = conn.execute(
            "insert into public.candle_gaps (pair_id, timeframe, gap_start, gap_end) values (%s, %s, %s, %s) "
            "on conflict (pair_id, timeframe, gap_start) do nothing",
            (pair_id, tf, gap_start, gap_end),
        )
        created += cur.rowcount
    return created


class Ingestor:
    def __init__(
        self,
        conn: psycopg.Connection,
        source: Source,
        client: CandleSource,
        settings: Settings,
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        ensure_permitted(source, settings)
        self.conn = conn
        self.source = source
        self.client = client
        self.settings = settings
        self.now = now

    # ---------------------------------------------------------------- runs
    def _start_run(self, kind: str, pair: Pair | None, tf: str | None) -> int:
        row = self.conn.execute(
            "insert into public.ingest_runs (source_id, pair_id, timeframe, kind) values (%s, %s, %s, %s) returning id",
            (self.source.id, pair.id if pair else None, tf, kind),
        ).fetchone()
        self.conn.commit()
        assert row is not None
        return int(row[0])

    def _finish_run(self, run_id: int, result: RunResult, error: Exception | None) -> None:
        if error is not None:
            self.conn.rollback()
        status = "failed" if error is not None else ("partial" if result.rejected else "succeeded")
        err = None
        if error is not None:
            err = json.dumps({"type": type(error).__name__, "message": str(error)[:500]})
        elif result.rejections:
            err = json.dumps({"rejections": [{"open_time": r.open_time.isoformat(), "reasons": list(r.reasons)} for r in result.rejections[:50]]})
        self.conn.execute(
            "update public.ingest_runs set status = %s, candles_upserted = %s, candles_rejected = %s, revisions_detected = %s, "
            "error = %s, finished_at = now() where id = %s",
            (status, result.upserted, result.rejected, result.revisions, err, run_id),
        )
        self.conn.commit()

    def _ingest_range(self, run_id: int, pair: Pair, tf: str, start: datetime, end: datetime, result: RunResult) -> None:
        """Fetch and store final candles with open time in [start, end], page by page."""
        cursor = start
        for _ in range(MAX_PAGES_PER_RUN):
            if cursor > end:
                return
            batch = self.client.fetch_klines(pair.symbol, tf, start=cursor, end=end)
            if not batch:
                return
            valid, rejected = partition(batch, tf, self.now())
            valid = [c for c in valid if start <= c.open_time <= end]
            result.rejected += len(rejected)
            result.rejections.extend(rejected)
            written, revisions = store_candles(self.conn, pair.id, tf, valid, run_id)
            self.conn.commit()
            result.upserted += written
            result.revisions += revisions
            last = max(c.open_time for c in batch)
            if last < cursor:
                return  # source went backwards; stop rather than loop
            cursor = last + tf_delta(tf)
        log(logger, logging.WARNING, "ingest_page_limit", pair=pair.symbol, timeframe=tf)

    # ---------------------------------------------------------------- public
    def ingest_incremental(self, pair: Pair, tf: str) -> RunResult:
        """Bring (pair, tf) up to the most recent closed candle."""
        now = self.now()
        latest_closed = last_closed_open_time(now, tf)
        row = self.conn.execute("select max(open_time) from public.market_candles where pair_id = %s and timeframe = %s and is_final", (pair.id, tf)).fetchone()
        last = row[0] if row else None
        start = latest_closed - tf_delta(tf) * (self.settings.ingest_initial_bars - 1) if last is None else last + tf_delta(tf)
        self.conn.commit()
        result = RunResult()
        if start > latest_closed:
            return result
        run_id = self._start_run("incremental", pair, tf)
        try:
            self._ingest_range(run_id, pair, tf, start, latest_closed, result)
            result.gaps_recorded = record_gaps(self.conn, pair.id, tf, last or start, latest_closed)
            self.conn.commit()
        except Exception as e:
            self._finish_run(run_id, result, e)
            raise
        self._finish_run(run_id, result, None)
        log(
            logger,
            logging.INFO,
            "ingest_incremental",
            pair=pair.symbol,
            timeframe=tf,
            upserted=result.upserted,
            rejected=result.rejected,
            revisions=result.revisions,
            gaps=result.gaps_recorded,
        )
        return result

    def backfill(self, pair: Pair, tf: str, start: datetime, end: datetime) -> RunResult:
        """Fetch a historical range [start, end] of open times (clamped to closed candles)."""
        end = min(end, last_closed_open_time(self.now(), tf))
        result = RunResult()
        if start > end:
            return result
        run_id = self._start_run("backfill", pair, tf)
        try:
            self._ingest_range(run_id, pair, tf, start, end, result)
            result.gaps_recorded = record_gaps(self.conn, pair.id, tf, start, end)
            self.conn.commit()
        except Exception as e:
            self._finish_run(run_id, result, e)
            raise
        self._finish_run(run_id, result, None)
        return result

    def import_candles(self, pair: Pair, tf: str, candles: Sequence[Candle], kind: str = "archive") -> RunResult:
        """Store candles obtained elsewhere (e.g. a verified archive), with the same validation."""
        result = RunResult()
        run_id = self._start_run(kind, pair, tf)
        try:
            valid, rejected = partition(candles, tf, self.now())
            result.rejected, result.rejections = len(rejected), rejected
            result.upserted, result.revisions = store_candles(self.conn, pair.id, tf, valid, run_id)
            if valid:
                result.gaps_recorded = record_gaps(self.conn, pair.id, tf, valid[0].open_time, valid[-1].open_time)
            self.conn.commit()
        except Exception as e:
            self._finish_run(run_id, result, e)
            raise
        self._finish_run(run_id, result, None)
        return result

    def resolve_gaps(self, pair: Pair, tf: str, limit: int = 10) -> tuple[int, int]:
        """Retry open gaps. Returns (filled, marked unrecoverable)."""
        gaps = self.conn.execute(
            "select id, gap_start, gap_end, attempts from public.candle_gaps where pair_id = %s and timeframe = %s "
            "and status = 'open' order by gap_start limit %s",
            (pair.id, tf, limit),
        ).fetchall()
        self.conn.commit()
        filled = unrecoverable = 0
        for gap_id, gap_start, gap_end, attempts in gaps:
            last_open = gap_end - tf_delta(tf)
            run_id = self._start_run("backfill", pair, tf)
            result = RunResult()
            try:
                self._ingest_range(run_id, pair, tf, gap_start, last_open, result)
            except (MarketDataError, psycopg.Error) as e:
                self._finish_run(run_id, result, e)
                self.conn.execute(
                    "update public.candle_gaps set attempts = attempts + 1, last_error = %s where id = %s",
                    (f"{type(e).__name__}"[:500], gap_id),
                )
                self.conn.commit()
                continue
            self._finish_run(run_id, result, None)
            remaining = find_gaps(
                [
                    r[0]
                    for r in self.conn.execute(
                        "select open_time from public.market_candles where pair_id = %s and timeframe = %s and open_time between %s and %s",
                        (pair.id, tf, gap_start, last_open),
                    ).fetchall()
                ],
                tf,
                gap_start,
                last_open,
            )
            if not remaining:
                self.conn.execute("update public.candle_gaps set status = 'backfilled', resolved_at = now(), attempts = attempts + 1 where id = %s", (gap_id,))
                filled += 1
            elif attempts + 1 >= MAX_GAP_ATTEMPTS:
                self.conn.execute(
                    "update public.candle_gaps set status = 'unrecoverable', resolved_at = now(), attempts = attempts + 1, "
                    "last_error = 'source returned no data for part of this interval' where id = %s",
                    (gap_id,),
                )
                unrecoverable += 1
            else:
                self.conn.execute("update public.candle_gaps set attempts = attempts + 1 where id = %s", (gap_id,))
            self.conn.commit()
        return filled, unrecoverable

    def update_price(self, pair: Pair) -> PricePoint:
        point = self.client.fetch_price(pair.symbol)
        self.conn.execute(
            "insert into public.latest_prices (pair_id, price, source_time, fetched_at) values (%s, %s, %s, now()) "
            "on conflict (pair_id) do update set price = excluded.price, source_time = excluded.source_time, fetched_at = now()",
            (pair.id, point.price, point.source_time),
        )
        self.conn.commit()
        return point

    def heartbeat(self, component: str, details: dict[str, Any]) -> None:
        self.conn.execute(
            "insert into public.engine_heartbeats (component, last_seen, details) values (%s, now(), %s) "
            "on conflict (component) do update set last_seen = now(), details = excluded.details",
            (component, json.dumps(details, default=str)),
        )
        self.conn.commit()
