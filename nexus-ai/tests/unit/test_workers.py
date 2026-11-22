import asyncio
import sys

from nexus_api.workers import InlineWorkers, WorkerSupervisor, keep_awake
from nexus_config import Settings, TradingMode


def _settings(**kw):
    return Settings(database_url="postgresql+psycopg://x:y@localhost/db",
                    redis_url="redis://localhost:6379/0", **kw)


def test_off_by_default():
    assert WorkerSupervisor.from_settings(_settings()) is None


def test_never_starts_a_live_trader():
    s = _settings(run_workers=True).model_copy(update={"trading_mode": TradingMode.LIVE})
    assert WorkerSupervisor.from_settings(s) is None


def test_paper_mode_builds_both_workers():
    sup = WorkerSupervisor.from_settings(_settings(run_workers=True, trading_mode="paper"))
    assert sup is not None
    assert set(sup.commands) == {"market-data", "trader", "notifier", "news", "derivatives"}


def test_restarts_a_crashed_worker_and_stops_cleanly():
    async def go():
        sup = WorkerSupervisor({"crash": [sys.executable, "-c", "raise SystemExit(3)"],
                                "sleep": [sys.executable, "-c", "import time; time.sleep(60)"]},
                               base_delay=0.05, grace=5)
        sup.start()
        await asyncio.sleep(1.0)
        await sup.stop()
        return sup
    sup = asyncio.run(go())
    assert sup.restarts["crash"] >= 2
    assert sup.restarts["sleep"] == 0
    assert all(p.returncode is not None for p in sup.procs.values())


def test_inline_mode_selected():
    sup = WorkerSupervisor.from_settings(
        _settings(run_workers=True, trading_mode="paper", workers_mode="inline"))
    assert isinstance(sup, InlineWorkers)
    assert set(sup.workers) == {"market-data", "trader", "notifier", "news", "derivatives"}


def test_inline_restarts_crashes_and_stops_cleanly():
    calls = {"crash": 0, "steady": 0}

    async def crash(stop):
        calls["crash"] += 1
        raise RuntimeError("boom")

    async def steady(stop):
        calls["steady"] += 1
        await stop.wait()

    async def go():
        w = InlineWorkers({"crash": crash, "steady": steady}, base_delay=0.05, grace=2)
        w.start()
        await asyncio.sleep(0.6)
        await w.stop()
        return w
    w = asyncio.run(go())
    assert w.restarts["crash"] >= 2 and calls["crash"] >= 2
    assert calls["steady"] == 1 and w.restarts["steady"] == 0
    assert all(t.done() for t in w._tasks)


def test_keep_awake_requests_health_and_stops(monkeypatch):
    import httpx

    seen = []

    def handler(request):
        seen.append(str(request.url))
        return httpx.Response(200)

    real = httpx.AsyncClient
    monkeypatch.setattr(httpx, "AsyncClient",
                        lambda **kw: real(transport=httpx.MockTransport(handler), **kw))

    async def go():
        stop = asyncio.Event()
        task = asyncio.create_task(keep_awake("https://x.example/", stop, every=0.05))
        await asyncio.sleep(0.2)
        stop.set()
        await asyncio.wait_for(task, 1)
    asyncio.run(go())
    assert seen and all(u == "https://x.example/health/live" for u in seen)


def test_x_worker_only_with_a_token():
    sup = WorkerSupervisor.from_settings(_settings(run_workers=True, trading_mode="paper",
                                                   workers_mode="inline",
                                                   x_bearer_token="dummy"))
    assert isinstance(sup, InlineWorkers) and "x-sentiment" in sup.workers
