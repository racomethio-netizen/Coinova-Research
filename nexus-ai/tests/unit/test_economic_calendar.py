"""The economic calendar: official dates converted to UTC across daylight saving, the
blackout windows, whole-day blocks when no time is published, currency exposure, and
coverage warnings."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from nexus_news import calendar as cal


def test_committed_calendar_loads_and_every_event_has_an_official_source():
    c = cal.load()
    assert len(c.events) >= 90
    for name, src in c.sources.items():
        assert src["url"].startswith("https://www.") or src["url"].startswith("https://"), name
        assert src["read_on"] and src["covers_until"] and src["currency"]
    assert {e.source for e in c.events} == set(c.sources)


def test_times_convert_to_utc_across_daylight_saving():
    c = cal.load()
    fomc = {e.start.date().isoformat(): e for e in c.events if e.source == "FOMC"}
    assert fomc["2026-12-09"].start == datetime(2026, 12, 9, 19, 0, tzinfo=UTC)   # EST
    assert fomc["2027-06-09"].start == datetime(2027, 6, 9, 18, 0, tzinfo=UTC)    # EDT
    cpi = [e for e in c.events if e.source == "BLS_CPI"]
    assert cpi[0].start == datetime(2026, 12, 10, 13, 30, tzinfo=UTC)
    snb = next(e for e in c.events if e.source == "SNB")
    assert snb.start == datetime(2026, 12, 10, 8, 30, tzinfo=UTC)                 # 09:30 CET


def test_unknown_times_block_the_whole_local_day():
    c = cal.load()
    boe = next(e for e in c.events if e.source == "BOE")
    assert not boe.time_known and boe.end - boe.start == timedelta(days=1)
    assert boe.start == datetime(2026, 12, 17, 0, 0, tzinfo=UTC)                  # London, GMT


def test_blackout_by_currency_and_window():
    c = cal.load()
    at = datetime(2026, 12, 9, 19, 45, tzinfo=UTC)          # 45 min after the Fed statement
    assert cal.event_risk("EURUSD", "forex", at, c)["blocked"] is True
    assert cal.event_risk("BTCUSDT", "crypto", at, c)["blocked"] is True     # USD exposure
    assert cal.event_risk("XAUUSD", "forex", at, c)["blocked"] is True
    later = cal.event_risk("EURUSD", "forex", at + timedelta(hours=2), c)
    assert later["blocked"] is False
    quiet = cal.event_risk("AUDUSD", "forex", datetime(2026, 12, 1, 12, tzinfo=UTC), c)
    assert quiet["blocked"] is False
    assert any(e["source"] == "RBA" for e in quiet["upcoming"])
    rba = cal.event_risk("AUDUSD", "forex", datetime(2026, 12, 7, 3, tzinfo=UTC), c)
    assert rba["blocked"] is True and rba["active"][0]["source"] == "RBA"
    assert cal.currencies("USDJPY", "forex") == ("USD", "JPY")
    assert cal.currencies("ETH/USDT", "crypto") == ("USD",)


def test_coverage_warning_near_the_end_of_the_published_schedules():
    c = cal.load()
    now = cal.event_risk("EURUSD", "forex", datetime(2026, 12, 1, tzinfo=UTC), c)
    assert now["coverage_warning"] == []
    late = cal.event_risk("EURUSD", "forex", datetime(2027, 12, 1, tzinfo=UTC), c)
    assert set(late["coverage_warning"]) == {"EUR", "USD"}
