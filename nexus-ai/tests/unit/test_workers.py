import asyncio
import sys

from nexus_api.workers import InlineWorkers, WorkerSupervisor, keep_awake
from nexus_config import Settings, TradingMode


def _settings(**kw):
    return Settings(database_url="postgresql+psycopg://x:y@localhost/db",
                    redis_url="redis://localhost:6379/0", **kw)


def test_off_by_default():
    assert WorkerSupervisor.from_settings(_settings()) is None


def test_live_mode_runs_only_live_workers():
    """Live mode on one server: market data, the (gated) live trader and monitoring. No
    paper bot, no paper forward tests. Before this fix live mode started NO worker at all,
    so the owner's live switch could never trade."""
    from nexus_api.workers import LIVE_WORKERS

    s = _settings(run_workers=True, workers_mode="inline").model_copy(
        update={"trading_mode": TradingMode.LIVE})
    sup = WorkerSupervisor.from_settings(s)
    assert isinstance(sup, InlineWorkers) and set(sup.workers) == set(LIVE_WORKERS)
    assert "trader" in sup.workers and "bot" not in sup.workers
    proc = WorkerSupervisor.from_settings(s.model_copy(update={"workers_mode": "process"}))
    assert isinstance(proc, WorkerSupervisor) and set(proc.commands) == set(LIVE_WORKERS)


def test_paper_mode_builds_both_workers():
    sup = WorkerSupervisor.from_settings(_settings(run_workers=True, trading_mode="paper"))
    assert sup is not None
    assert set(sup.commands) == {"market-data", "trader", "notifier", "news", "derivatives",
                                 "carry-paper", "forward-taker", "decision-log",
                                 "trend-paper", "spot-engine", "library-paper", "bot", "grid-paper",
                                 "adaptive-grid", "rebalance-paper",
                                 "voltarget-paper", "multiasset-research",
                                 "module-observer", "orchestrator"}


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
    assert set(sup.workers) == {"market-data", "trader", "notifier", "news", "derivatives",
                                 "carry-paper", "forward-taker", "decision-log",
                                 "trend-paper", "spot-engine", "library-paper", "bot", "grid-paper",
                                 "adaptive-grid", "rebalance-paper",
                                 "voltarget-paper", "multiasset-research",
                                 "module-observer", "orchestrator"}


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


def test_production_inline_workers_run_on_their_own_thread():
    sup = WorkerSupervisor.from_settings(
        _settings(run_workers=True, trading_mode="paper", workers_mode="inline"))
    assert isinstance(sup, InlineWorkers) and sup.threaded


def test_threaded_workers_never_block_the_api_loop():
    """A worker making a blocking call (like a sync DB query) must not stall the API loop."""
    import threading
    import time

    seen = {}

    async def blocking(stop):
        seen["thread"] = threading.current_thread().name
        while not stop.is_set():
            time.sleep(0.3)  # noqa: ASYNC251 - deliberately blocks ITS loop only
            await asyncio.sleep(0)

    async def crash(stop):
        raise RuntimeError("boom")

    async def go():
        w = InlineWorkers({"blocking": blocking, "crash": crash}, base_delay=0.05, grace=2,
                          threaded=True)
        w.start()
        ticks, t0 = 0, time.monotonic()
        while time.monotonic() - t0 < 0.6:        # the API loop keeps ticking meanwhile
            await asyncio.sleep(0.01)
            ticks += 1
        await w.stop()
        return w, ticks
    w, ticks = asyncio.run(go())
    assert seen["thread"] == "nexus-workers"
    assert ticks > 30                             # ~60 expected; blocked loop would give ~2
    assert w.restarts["crash"] >= 1               # restarted (its loop is shared with the blocker)
    assert all(t.done() for t in w._tasks) and not w._thread.is_alive()


def test_guard_default_redis_is_per_event_loop():
    import threading

    from nexus_common import binance_guard as g

    async def register(name):
        g.set_default_redis(name)
        await asyncio.sleep(0.05)
        return g.default_redis()

    out = {}
    t = threading.Thread(target=lambda: out.setdefault("worker", asyncio.run(register("w"))))
    t.start()
    out["api"] = asyncio.run(register("api"))
    t.join()
    assert out == {"worker": "w", "api": "api"}


def test_inline_workers_start_one_by_one_and_stop_during_the_wait():
    import time

    started: dict[str, float] = {}

    def make(name):
        async def serve(stop):
            started[name] = time.monotonic()
            await stop.wait()
        return serve

    async def go(wait: float):
        w = InlineWorkers({n: make(n) for n in ("a", "b", "c")}, grace=2, stagger=0.2)
        t0 = time.monotonic()
        w.start()
        await asyncio.sleep(wait)
        await w.stop()
        return t0, time.monotonic() - t0

    t0, _ = asyncio.run(go(0.6))
    assert started["a"] - t0 < 0.1
    assert 0.15 < started["b"] - t0 < 0.35 and 0.35 < started["c"] - t0 < 0.55
    started.clear()
    _, took = asyncio.run(go(0.05))               # stop before b and c start
    assert set(started) == {"a"} and took < 0.5


def test_production_inline_workers_are_staggered():
    from nexus_api.workers import STAGGER_SECONDS
    sup = WorkerSupervisor.from_settings(
        _settings(run_workers=True, trading_mode="paper", workers_mode="inline"))
    assert isinstance(sup, InlineWorkers) and sup.stagger == STAGGER_SECONDS > 0
    assert list(sup.workers)[:2] == ["market-data", "trader"]   # data and trader first
