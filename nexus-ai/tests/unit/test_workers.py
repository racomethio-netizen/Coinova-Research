import asyncio
import sys

from nexus_api.workers import WorkerSupervisor
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
    assert sup is not None and set(sup.commands) == {"market-data", "trader"}


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
