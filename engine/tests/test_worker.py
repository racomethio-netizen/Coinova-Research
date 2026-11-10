from datetime import UTC, datetime, timedelta
from typing import Any

from nexus_engine.marketdata.ingest import Pair
from nexus_engine.marketdata.types import SourceBlockedError, SourceUnavailableError
from nexus_engine.worker import Scheduler


class FakeConn:
    def rollback(self) -> None:
        pass


class FakeSource:
    key = "fake"


class FakeIngestor:
    def __init__(self, fail: dict[str, Exception] | None = None) -> None:
        self.calls: list[tuple[str, ...]] = []
        self.fail = fail or {}
        self.conn = FakeConn()
        self.source = FakeSource()

    def ingest_incremental(self, pair: Pair, tf: str) -> None:
        self.calls.append(("ingest", pair.symbol, tf))
        if f"{pair.symbol}:{tf}" in self.fail:
            raise self.fail[f"{pair.symbol}:{tf}"]

    def update_price(self, pair: Pair) -> None:
        self.calls.append(("price", pair.symbol))

    def resolve_gaps(self, pair: Pair, tf: str) -> tuple[int, int]:
        self.calls.append(("gaps", pair.symbol, tf))
        return 0, 0

    def heartbeat(self, component: str, details: dict[str, Any]) -> None:
        self.calls.append(("heartbeat", component))


class FakeSignals:
    def __init__(self, ing: FakeIngestor) -> None:
        self.ing = ing

    def evaluate(self, pair: Pair, tf: str, as_of: datetime | None = None) -> None:
        self.ing.calls.append(("evaluate", pair.symbol, tf))

    def resolve_open(self) -> int:
        self.ing.calls.append(("resolve",))
        return 0


PAIRS = [Pair(1, "BTCUSDT", ("15m", "1h")), Pair(2, "ETHUSDT", ("15m",))]


def sched(ing: FakeIngestor) -> Scheduler:
    return Scheduler(ingestor=ing, pairs=PAIRS, close_delay=timedelta(seconds=20), ticker_interval=timedelta(seconds=15))  # type: ignore[arg-type]


def ingests(ing: FakeIngestor) -> list[tuple[str, ...]]:
    return [c for c in ing.calls if c[0] == "ingest"]


def test_waits_for_close_delay_then_ingests_each_bucket_once() -> None:
    ing = FakeIngestor()
    s = sched(ing)
    s.last_bucket = {
        (1, "15m"): datetime(2026, 1, 1, 0, 0, tzinfo=UTC),
        (1, "1h"): datetime(2026, 1, 1, 0, 0, tzinfo=UTC),
        (2, "15m"): datetime(2026, 1, 1, 0, 0, tzinfo=UTC),
    }
    s.tick(datetime(2026, 1, 1, 0, 15, 10, tzinfo=UTC))  # 15m candle closed 10 s ago: too early
    assert ingests(ing) == []
    s.tick(datetime(2026, 1, 1, 0, 15, 25, tzinfo=UTC))
    assert ingests(ing) == [("ingest", "BTCUSDT", "15m"), ("ingest", "ETHUSDT", "15m")]
    s.tick(datetime(2026, 1, 1, 0, 16, tzinfo=UTC))
    assert len(ingests(ing)) == 2  # same buckets: nothing new


def test_one_failing_pair_does_not_stop_others_and_is_retried() -> None:
    ing = FakeIngestor({"BTCUSDT:15m": SourceUnavailableError("down")})
    s = sched(ing)
    stats = s.tick(datetime(2026, 1, 1, 0, 15, 30, tzinfo=UTC))
    assert ("ingest", "ETHUSDT", "15m") in ing.calls and stats["errors"] == 1
    ing.fail.clear()
    s.tick(datetime(2026, 1, 1, 0, 15, 40, tzinfo=UTC))
    assert ingests(ing).count(("ingest", "BTCUSDT", "15m")) == 2


def test_block_stops_all_source_traffic_but_keeps_heartbeats() -> None:
    ing = FakeIngestor({"BTCUSDT:15m": SourceBlockedError("HTTP 451 from source")})
    s = sched(ing)
    s.tick(datetime(2026, 1, 1, 0, 15, 30, tzinfo=UTC))
    n = len([c for c in ing.calls if c[0] != "heartbeat"])
    s.tick(datetime(2026, 1, 1, 1, 0, 30, tzinfo=UTC))
    assert s.blocked == "HTTP 451 from source"
    assert len([c for c in ing.calls if c[0] != "heartbeat"]) == n
    assert ing.calls[-1] == ("heartbeat", "marketdata.worker")


def test_evaluates_only_after_all_due_timeframes_are_ingested_and_skips_failures() -> None:
    ing = FakeIngestor({"BTCUSDT:1h": SourceUnavailableError("down")})
    s = sched(ing)
    s.signals = FakeSignals(ing)
    s.tick(datetime(2026, 1, 1, 1, 0, 30, tzinfo=UTC))
    seq = [c for c in ing.calls if c[0] in ("ingest", "evaluate", "resolve")]
    last_ingest = max(i for i, c in enumerate(seq) if c[0] == "ingest")
    first_eval = min(i for i, c in enumerate(seq) if c[0] == "evaluate")
    assert last_ingest < first_eval
    assert ("evaluate", "BTCUSDT", "1h") not in seq  # its ingestion failed
    assert ("evaluate", "BTCUSDT", "15m") in seq and ("evaluate", "ETHUSDT", "15m") in seq
    assert seq[-1] == ("resolve",)


def test_nightly_runs_once_per_day_after_its_hour_even_while_blocked() -> None:
    ing = FakeIngestor()
    runs: list[int] = []
    s = sched(ing)
    s.nightly, s.nightly_hour = (lambda: runs.append(1)), 2
    day = datetime(2026, 11, 10, tzinfo=UTC)
    s.tick(day + timedelta(hours=1, minutes=59))
    assert runs == []
    s.blocked = "418 from source"
    s.tick(day + timedelta(hours=2))
    s.tick(day + timedelta(hours=5))
    assert runs == [1]
    s.tick(day + timedelta(days=1, hours=2))
    assert runs == [1, 1]


def test_a_failing_nightly_is_not_retried_every_tick() -> None:
    ing = FakeIngestor()
    calls: list[int] = []

    def boom() -> None:
        calls.append(1)
        raise RuntimeError("disk full")

    s = sched(ing)
    s.nightly = boom
    t = datetime(2026, 11, 10, 3, tzinfo=UTC)
    assert s.tick(t)["errors"] == 1
    s.tick(t + timedelta(seconds=5))
    assert calls == [1]
