from datetime import UTC, datetime, timedelta

import pytest
from hypothesis import given
from hypothesis import strategies as st

from nexus_engine.timeframes import TIMEFRAMES, close_time_for, floor_to, is_aligned, last_closed_open_time, open_times, tf_delta

dts = st.datetimes(min_value=datetime(2017, 1, 1), max_value=datetime(2035, 1, 1), timezones=st.just(UTC))  # noqa: DTZ001 - hypothesis bounds must be naive; tz added by timezones=


def test_floor_and_last_closed() -> None:
    now = datetime(2026, 3, 4, 13, 37, 5, tzinfo=UTC)
    assert floor_to(now, "15m") == datetime(2026, 3, 4, 13, 30, tzinfo=UTC)
    assert floor_to(now, "4h") == datetime(2026, 3, 4, 12, tzinfo=UTC)
    assert floor_to(now, "1d") == datetime(2026, 3, 4, tzinfo=UTC)
    assert last_closed_open_time(now, "1h") == datetime(2026, 3, 4, 12, tzinfo=UTC)


def test_close_time_is_last_millisecond() -> None:
    t = datetime(2026, 1, 1, tzinfo=UTC)
    assert close_time_for(t, "1h") == datetime(2026, 1, 1, 0, 59, 59, 999000, tzinfo=UTC)


def test_naive_datetimes_are_rejected() -> None:
    with pytest.raises(ValueError):
        floor_to(datetime(2026, 1, 1), "1h")  # noqa: DTZ001


def test_open_times_inclusive_range() -> None:
    start = datetime(2026, 1, 1, 0, 10, tzinfo=UTC)
    end = datetime(2026, 1, 1, 3, tzinfo=UTC)
    assert list(open_times(start, end, "1h")) == [datetime(2026, 1, 1, h, tzinfo=UTC) for h in (1, 2, 3)]


@given(dts, st.sampled_from(TIMEFRAMES))
def test_floor_properties(dt: datetime, tf: str) -> None:
    f = floor_to(dt, tf)
    assert is_aligned(f, tf)
    assert f <= dt < f + tf_delta(tf)
    # The last closed candle has fully closed and is the newest such candle.
    last = last_closed_open_time(dt, tf)
    assert close_time_for(last, tf) < dt
    assert last + tf_delta(tf) + tf_delta(tf) > dt - timedelta(microseconds=1)
