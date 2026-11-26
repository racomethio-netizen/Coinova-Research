"""Paper grid worker on a fake Redis and an in-memory ledger: starts from the live book,
records every fill, pauses on the kill switch, records gaps on a stale book, stays
stopped after a stop-loss until the owner restarts, and resumes after a restart."""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

import pytest
from nexus_common import events
from nexus_config import Settings
from nexus_execution import grid_paper as gp

D = Decimal
T0 = datetime(2026, 11, 23, 12, tzinfo=UTC)


class FakeRedis:
    def __init__(self) -> None:
        self.kv: dict[str, Any] = {}

    async def get(self, k: str) -> Any:
        return self.kv.get(k)

    async def set(self, k: str, v: Any, ex: int | None = None) -> None:
        self.kv[k] = v

    async def getdel(self, k: str) -> Any:
        return self.kv.pop(k, None)


class MemLedger:
    def __init__(self) -> None:
        self.rows: list[dict[str, Any]] = []

    def latest(self):
        for r in reversed(self.rows):
            if r["kind"] != "gap" and "state" in r["data"]:
                return r["run"], r["seq"], r["data"]
        return None

    def append(self, run, seq, symbol, kind, at, data):
        self.rows.append({"run": run, "seq": seq, "kind": kind, "at": at,
                          "data": json.loads(json.dumps(data, default=str))})


def settings() -> Settings:
    return Settings(database_url="postgresql+psycopg://x:y@localhost/db",
                    redis_url="redis://localhost:6379/0")


def runner(monkeypatch: pytest.MonkeyPatch, redis: FakeRedis, ledger: MemLedger,
           price: list[Decimal]) -> gp.GridRunner:
    r = gp.GridRunner(settings(), factory=None, redis=redis, ledger=ledger)
    # A week of hourly candles between 90,000 and 99,000.
    monkeypatch.setattr(r, "_candles", lambda: ([D("99000")] * 168, [D("90000")] * 168))

    async def book():
        p = price[0]
        if p <= 0:
            return None
        return p - D("1"), p + D("1")
    monkeypatch.setattr(r, "_book", book)
    return r


def test_fit_levels_keeps_steps_above_fees():
    assert gp.fit_levels(D("100"), D("104")) == 8          # 4% wide: 0.5% steps
    assert gp.fit_levels(D("100"), D("112")) == 20         # capped at 20
    assert gp.fit_levels(D("100"), D("100.5")) == 3        # never fewer than 3


def test_design_refuses_without_history_and_builds_a_valid_grid():
    cfg, why = gp.design("BTC/USDT", [D("1")] * 5, [D("1")] * 5, D("1"), D("1000"))
    assert cfg is None and "hourly candles" in why[0]
    cfg, why = gp.design("BTC/USDT", [D("99000")] * 168, [D("90000")] * 168, D("95000"),
                         D("1000"))
    assert cfg is not None and why == []
    assert cfg.upper / cfg.lower - 1 <= D("0.1201")
    assert gp.config_from_json(gp.config_to_json(cfg)) == cfg


def test_runs_fills_pauses_and_stops(monkeypatch):
    redis, ledger, price = FakeRedis(), MemLedger(), [D("95000")]
    r = runner(monkeypatch, redis, ledger, price)

    async def go():
        st = await r.cycle(T0)
        assert st["paper"] is True and st["grid"]["status"] == "RUNNING"
        assert ledger.rows[0]["kind"] == "start"
        assert D(ledger.rows[0]["data"]["worst_case_loss"]) <= D("100")   # <= 10% of 1,000
        lv = [D(x) for x in st["grid"]["levels"]]
        below = max(p for p in lv if p < D("95000"))
        price[0] = below - 5                                  # ask trades through a buy
        await r.cycle(T0 + timedelta(seconds=10))
        assert [x["kind"] for x in ledger.rows].count("fill") >= 1
        # Kill switch engaged: no fills while paused.
        redis.kv[events.KILL_SWITCH_KEY] = json.dumps({"reason": "test"})
        n = len(ledger.rows)
        price[0] = D("99500")
        st = await r.cycle(T0 + timedelta(seconds=20))
        assert st["kill_switch_pause"] and len(ledger.rows) == n
        del redis.kv[events.KILL_SWITCH_KEY]
        # Stale book: a gap is recorded once per hour, nothing else.
        price[0] = D("0")
        await r.cycle(T0 + timedelta(seconds=30))
        await r.cycle(T0 + timedelta(seconds=40))
        assert [x["kind"] for x in ledger.rows].count("gap") == 1
        # Crash below the stop: everything is sold and the grid stays stopped.
        price[0] = D("85000")
        st = await r.cycle(T0 + timedelta(minutes=1))
        assert st["grid"]["status"] == "STOPPED" and st["grid"]["base"] == "0"
        assert any(x["kind"] == "stop" for x in ledger.rows)
        price[0] = D("95000")
        st = await r.cycle(T0 + timedelta(minutes=2))
        assert st["grid"]["status"] == "STOPPED"               # no automatic restart
        # Owner restart: a fresh grid on a new run.
        redis.kv[gp.RESTART_KEY] = "user:1"
        st = await r.cycle(T0 + timedelta(minutes=3))
        assert st["grid"]["status"] == "RUNNING" and ledger.rows[-1]["kind"] in (
            "restart", "mark")
        assert any(x["kind"] == "restart" for x in ledger.rows)
    asyncio.run(go())


def test_resumes_exact_state_after_restart(monkeypatch):
    redis, ledger, price = FakeRedis(), MemLedger(), [D("95000")]
    r = runner(monkeypatch, redis, ledger, price)
    asyncio.run(r.cycle(T0))
    price[0] = D("93000")
    asyncio.run(r.cycle(T0 + timedelta(seconds=10)))
    before = r.state.to_json()
    r2 = runner(monkeypatch, redis, ledger, price)
    r2.bootstrap()
    assert r2.state is not None and r2.state.to_json() == before and r2.run == r.run
    assert r2.seq == r.seq
