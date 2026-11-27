"""Forex/gold intraday bars from the MT5 bridge: validation before storage (no repairs,
no guesses), backfill requests, and health measured over FX-session hours only."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from nexus_market import fx_bars as fx

NOW = datetime(2026, 11, 25, 12, 2, tzinfo=UTC)          # a Wednesday
OFF = 2                                                  # broker server time UTC+2


def row(open_utc: datetime, o=1.1, h=1.2, lo=1.0, c=1.15, v=10.0) -> list[float]:
    return [int(open_utc.timestamp()) + OFF * 3600, o, h, lo, c, v]


def test_check_bars_converts_time_and_counts_every_rejection():
    t = datetime(2026, 11, 25, 11, 0, tzinfo=UTC)
    rows = [row(t), row(t + timedelta(minutes=5)),
            row(t + timedelta(minutes=5)),                          # duplicate
            row(t + timedelta(minutes=10), h=1.05),                 # high below close
            row(t + timedelta(minutes=15), lo=0.0),                 # non-positive
            [row(t)[0], 1.1, float("nan"), 1.0, 1.1, 1.0],          # not finite
            [row(t)[0], 1.1],                                       # too short
            row(t + timedelta(minutes=2)),                          # not on the 5 m grid
            row(datetime(2026, 11, 25, 12, 0, tzinfo=UTC)),         # forming: skipped
            row(NOW + timedelta(hours=1))]                          # in the future
    out = fx.check_bars(rows, OFF, NOW, 300)
    assert [b.open_time for b in out.bars] == [t, t + timedelta(minutes=5)]
    assert out.bars[0].high == Decimal("1.2") and out.bars[0].volume == Decimal("10.0")
    assert out.rejected == {"duplicate": 1, "ohlc_inconsistent": 1, "non_positive": 1,
                            "malformed": 2, "misaligned": 1, "future": 1}
    assert out.forming == 1


def test_large_bars_are_kept_but_flagged():
    t0 = datetime(2026, 11, 24, 0, 0, tzinfo=UTC)
    rows = [row(t0 + timedelta(minutes=5 * i), o=1.1, h=1.1002, lo=1.0999, c=1.1001)
            for i in range(30)]
    rows.append(row(t0 + timedelta(minutes=150), o=1.1, h=1.2, lo=1.0, c=1.15))
    out = fx.check_bars(rows, OFF, NOW, 300)
    assert len(out.bars) == 31 and out.jumps == 1 and out.rejected == {}


def test_daily_bars_are_not_grid_checked():
    t = datetime(2026, 11, 20, 22, 0, tzinfo=UTC)                  # broker midnight
    assert len(fx.check_bars([row(t)], OFF, NOW, 86400, align=False).bars) == 1


def test_backfill_requests():
    t = datetime(2026, 11, 25, 9, 0, tzinfo=UTC)
    assert fx.need_backfill(0, None, t, "5m", recently_full=False)          # new series
    assert fx.need_backfill(200, t, t, "5m", recently_full=False)           # too short
    assert not fx.need_backfill(5000, t, t - timedelta(hours=2), "5m", recently_full=False)
    assert fx.need_backfill(5000, t, t + timedelta(hours=4), "5m", recently_full=False)
    assert not fx.need_backfill(200, t, t, "5m", recently_full=True)        # broker has no more
    assert not fx.need_backfill(600, t, t + timedelta(hours=1), "1h", recently_full=False)
    assert fx.need_backfill(600, t, t + timedelta(hours=2), "1h", recently_full=False)


def test_expected_bars_skip_the_weekend():
    fri = datetime(2026, 11, 27, 21, 0, tzinfo=UTC)                 # Friday 16:00 New York
    opens = fx.expected_opens(fri, fri + timedelta(days=3), 3600)
    assert opens[0] == fri and opens[1] == datetime(2026, 11, 29, 22, 0, tzinfo=UTC)
    assert all(t.weekday() not in (5,) for t in opens)


def series(start: datetime, end: datetime, seconds: int, skip=()) -> list[datetime]:
    return [t for t in fx.expected_opens(start, end, seconds) if t not in skip]


def test_health_ok_gaps_stale_closed_and_empty():
    window, grace = timedelta(days=1), fx.STALE_GRACE["forex"]
    last = datetime(2026, 11, 25, 11, 55, tzinfo=UTC)
    hole = {datetime(2026, 11, 25, 3, 0, tzinfo=UTC) + timedelta(minutes=5 * i)
            for i in range(4)}
    times = series(NOW - window, last, 300, hole)
    h = fx.series_health(times, NOW, 300, window=window, grace=grace)
    assert h["status"] == "ok" and h["missing"] == 4 and h["gaps"] == 1
    assert h["largest_gap_bars"] == 4 and 0.98 < h["coverage"] < 1
    old = [t for t in times if t <= datetime(2026, 11, 25, 11, 30, tzinfo=UTC)]
    h = fx.series_health(old, NOW, 300, window=window, grace=grace)
    assert h["status"] == "stale" and h["overdue_bars"] >= 1
    sat = datetime(2026, 11, 28, 12, 0, tzinfo=UTC)
    fri = series(sat - timedelta(days=2), sat, 300)
    h = fx.series_health(fri, sat, 300, window=window, grace=grace)
    assert h["status"] == "closed" and h["market_open"] is False and h["missing"] == 0
    assert fx.series_health([], NOW, 300, window=window, grace=grace)["status"] == "no data"


def test_gold_gets_a_longer_grace_for_its_daily_break():
    assert fx.STALE_GRACE["commodities"] > timedelta(hours=1) > fx.STALE_GRACE["forex"]
