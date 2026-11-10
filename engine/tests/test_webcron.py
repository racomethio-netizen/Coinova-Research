"""Web cron caller: slots, ordering, auth header, failure handling, overlap protection. No network."""

import threading
from datetime import UTC, datetime, timedelta

import httpx
import pytest

from nexus_engine.config import ConfigError, load_settings
from nexus_engine.ops.webcron import SCHEDULE, CronEntry, WebCron

SECRET = "s" * 40
T = datetime(2026, 11, 10, 10, 30, 15, tzinfo=UTC)


def recorder(fail: dict[str, int] | None = None) -> tuple[list[str], httpx.MockTransport]:
    calls: list[str] = []

    def handle(req: httpx.Request) -> httpx.Response:
        assert req.method == "POST" and req.headers["authorization"] == f"Bearer {SECRET}"
        job = req.url.path.rsplit("/", 1)[-1]
        calls.append(job)
        return httpx.Response((fail or {}).get(job, 200), json={"ok": True})

    return calls, httpx.MockTransport(handle)


def test_slots_match_the_former_render_schedule() -> None:
    by = {e.name: e for e in SCHEDULE}
    assert by["notifications"].slot(T) == datetime(2026, 11, 10, 10, 30, tzinfo=UTC)
    assert by["markets"].slot(T) == datetime(2026, 11, 10, 10, 30, tzinfo=UTC)
    assert by["markets"].slot(T.replace(minute=34)) == datetime(2026, 11, 10, 10, 30, tzinfo=UTC)
    assert by["hourly"].slot(T) == datetime(2026, 11, 10, 10, 7, tzinfo=UTC)
    assert by["hourly"].slot(T.replace(minute=5)) == datetime(2026, 11, 10, 9, 7, tzinfo=UTC)
    assert by["daily"].slot(T) == datetime(2026, 11, 10, 3, 23, tzinfo=UTC)
    assert by["daily"].slot(T.replace(hour=2)) == datetime(2026, 11, 9, 3, 23, tzinfo=UTC)


def test_each_slot_runs_once_with_jobs_in_order() -> None:
    calls, transport = recorder()
    wc = WebCron("https://app.example.invalid/", SECRET, transport=transport, inline=True)
    assert set(wc.tick(T)) == {"notifications", "markets", "hourly", "daily"}
    assert calls == ["deliver-notifications", "refresh-markets", "evaluate-alerts",
                     "refresh-history", "refresh-fundamentals", "compute-scores", "cleanup"]  # fmt: skip
    assert wc.tick(T + timedelta(seconds=20)) == []
    assert wc.tick(T + timedelta(minutes=1)) == ["notifications"]
    assert sorted(wc.tick(T + timedelta(minutes=5))) == ["markets", "notifications"]
    assert all(s["last_status"] == "ok" for s in wc.status().values())


def test_a_failed_job_stops_its_entry_and_is_reported_without_the_secret(caplog: pytest.LogCaptureFixture) -> None:
    calls, transport = recorder(fail={"refresh-fundamentals": 500})
    wc = WebCron("https://app.example.invalid", SECRET, schedule=(SCHEDULE[2],), transport=transport, inline=True)
    wc.tick(T)
    assert calls == ["refresh-history", "refresh-fundamentals"]  # compute-scores not attempted
    st = wc.status()["hourly"]
    assert st["last_status"] == "failed:refresh-fundamentals:500" and st["consecutive_failures"] == 1
    assert SECRET not in caplog.text


def test_network_errors_are_contained() -> None:
    def boom(req: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=req)

    wc = WebCron("https://app.example.invalid", SECRET, schedule=(SCHEDULE[0],), transport=httpx.MockTransport(boom), inline=True)
    wc.tick(T)
    assert wc.status()["notifications"]["last_status"] == "failed:deliver-notifications:ConnectError"


def test_an_entry_is_skipped_while_its_previous_run_is_still_going() -> None:
    release = threading.Event()
    started = threading.Event()
    calls: list[str] = []

    def slow(req: httpx.Request) -> httpx.Response:
        calls.append(req.url.path)
        started.set()
        release.wait(5)
        return httpx.Response(200)

    entry = CronEntry("fast", ("deliver-notifications",), timedelta(minutes=1))
    wc = WebCron("https://app.example.invalid", SECRET, schedule=(entry,), transport=httpx.MockTransport(slow))
    assert wc.tick(T) == ["fast"]
    assert started.wait(5)
    assert wc.tick(T + timedelta(minutes=1)) == []  # previous run still in flight
    assert wc.status()["fast"]["last_status"] == "skipped_running"
    release.set()
    thread = wc.state["fast"].thread
    assert thread is not None
    thread.join(5)
    assert wc.tick(T + timedelta(minutes=1, seconds=5)) == ["fast"]  # claimed once the slot is free
    wc.state["fast"].thread.join(5)  # type: ignore[union-attr]
    assert len(calls) == 2


def test_settings_require_both_values_and_https_in_production() -> None:
    with pytest.raises(ConfigError, match="set both"):
        load_settings(web_cron_url="https://app.example.invalid")
    with pytest.raises(ConfigError, match="at least 32"):
        load_settings(web_cron_url="https://app.example.invalid", cron_secret="short")
    with pytest.raises(ConfigError, match="https") as e:
        load_settings(app_env="staging", web_cron_url="http://app.example.invalid", cron_secret=SECRET)
    assert SECRET not in str(e.value)
    assert load_settings(web_cron_url="http://127.0.0.1:3000", cron_secret=SECRET).cron_secret is not None
