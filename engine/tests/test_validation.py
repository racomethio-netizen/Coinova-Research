from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from conftest import synthetic_candles

from nexus_engine.marketdata.validation import candle_problems, partition

NOW = datetime(2026, 2, 1, tzinfo=UTC)


def test_valid_candles_pass_and_are_sorted() -> None:
    cs = synthetic_candles(datetime(2026, 1, 1, tzinfo=UTC), 5)
    valid, rejected = partition(list(reversed(cs)), "1h", NOW)
    assert rejected == [] and [c.open_time for c in valid] == [c.open_time for c in cs]


def test_each_rule_rejects() -> None:
    c = synthetic_candles(datetime(2026, 1, 1, tzinfo=UTC), 1)[0]
    cases = {
        "misaligned_open_time": replace(c, open_time=c.open_time + timedelta(minutes=5), close_time=c.close_time + timedelta(minutes=5)),
        "unexpected_close_time": replace(c, close_time=c.close_time + timedelta(milliseconds=1)),
        "future_open_time": replace(c, open_time=NOW + timedelta(hours=1), close_time=NOW + timedelta(hours=2) - timedelta(milliseconds=1)),
        "final_before_close": replace(c, open_time=NOW - timedelta(minutes=30), close_time=NOW + timedelta(minutes=30) - timedelta(milliseconds=1)),
        "non_positive_price": replace(c, low=Decimal(0)),
        "negative_volume": replace(c, volume=Decimal(-1)),
        "inconsistent_ohlc": replace(c, high=c.open - 1),
        "non_finite_value": replace(c, close=Decimal("NaN")),
        "naive_timestamp": replace(c, open_time=c.open_time.replace(tzinfo=None)),
    }
    for reason, bad in cases.items():
        assert reason in candle_problems(bad, "1h", NOW), reason


def test_duplicates_in_a_batch_are_rejected_not_merged() -> None:
    c = synthetic_candles(datetime(2026, 1, 1, tzinfo=UTC), 1)[0]
    valid, rejected = partition([c, replace(c, close=c.close + 1)], "1h", NOW)
    assert len(valid) == 1 and valid[0] == c
    assert rejected[0].reasons == ("duplicate_in_batch",)
