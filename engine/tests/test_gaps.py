from datetime import UTC, datetime, timedelta

from hypothesis import given
from hypothesis import strategies as st

from nexus_engine.marketdata.gaps import find_gaps

H = timedelta(hours=1)
T0 = datetime(2026, 1, 1, tzinfo=UTC)


def at(*hours: int) -> list[datetime]:
    return [T0 + H * h for h in hours]


def test_no_gaps() -> None:
    assert find_gaps(at(0, 1, 2, 3), "1h", T0, T0 + 3 * H) == []


def test_leading_middle_and_trailing_gaps() -> None:
    assert find_gaps(at(1, 2, 5), "1h", T0, T0 + 7 * H) == [(T0, T0 + H), (T0 + 3 * H, T0 + 5 * H), (T0 + 6 * H, T0 + 8 * H)]


def test_empty_store_is_one_gap() -> None:
    assert find_gaps([], "1h", T0, T0 + 2 * H) == [(T0, T0 + 3 * H)]


@given(st.sets(st.integers(min_value=0, max_value=60)))
def test_gaps_plus_present_cover_the_range_exactly(present: set[int]) -> None:
    end = T0 + 60 * H
    gaps = find_gaps(at(*present), "1h", T0, end)
    missing = set()
    for s, e in gaps:
        assert s < e
        t = s
        while t < e:
            missing.add(t)
            t += H
    assert missing.isdisjoint(set(at(*present)))
    assert missing | set(at(*present)) == set(at(*range(61)))
