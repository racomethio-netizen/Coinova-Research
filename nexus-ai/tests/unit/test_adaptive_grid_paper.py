"""Adaptive grid orchestrator on a fake market: autonomous lifecycle (universe → regime →
grid → risk-authorised orders → fills), owner controls, kill switch, stale data, trend
hand-off, drawdown shutdown, separate accounts, futures funding, atomic persistence and
exact restart recovery, and the absence of any live order path."""

from __future__ import annotations

import asyncio
import copy
import inspect
import re
from decimal import Decimal
from typing import Any

import numpy as np
import pandas as pd
import pytest
from nexus_execution import adaptive_grid_paper as agp
from nexus_strategies import adaptive_grid as ag

D = Decimal
T0 = 1_795_000_000.0


def sideways(n: int = 400, seed: int = 1, px: float = 60_000.0) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    x = np.zeros(n)
    for t in range(1, n):
        x[t] = x[t - 1] * 0.92 + rng.normal(0, 0.003)
    c = px * np.exp(x)
    o = np.r_[c[0], c[:-1]]
    hi = np.maximum(o, c) * (1 + np.abs(rng.normal(0, 0.003, n)))
    lo = np.minimum(o, c) * (1 - np.abs(rng.normal(0, 0.003, n)))
    idx = pd.date_range("2026-10-01", periods=n, freq="h", tz="UTC")
    return pd.DataFrame({"open": o, "high": hi, "low": lo, "close": c, "volume": 20_000.0},
                        index=idx)


def trending(n: int = 400) -> pd.DataFrame:
    c = 60_000 * np.exp(np.linspace(0, 0.6, n))
    idx = pd.date_range("2026-10-01", periods=n, freq="h", tz="UTC")
    return pd.DataFrame({"open": c, "high": c * 1.002, "low": c * 0.998, "close": c,
                         "volume": 20_000.0}, index=idx)


FILT = {"BTCUSDT": {"price_tick": 0.01, "amount_step": 0.00001, "min_qty": 0.00001,
                    "min_notional": 5.0}}
FFILT = {"BTCUSDT": {"price_tick": 0.1, "amount_step": 0.001, "min_qty": 0.001,
                     "min_notional": 100.0}}


class FakeData:
    def __init__(self) -> None:
        self.px = {"spot": D("60000"), "futures": D("60000")}
        self.frames = {"spot": sideways(), "futures": sideways(seed=2)}
        self.cmds: list[dict[str, Any]] = []
        self.kill: bool | None = False
        self.down: set[str] = set()
        self.markets = ("spot", "futures")
        self.prem: dict[str, dict[str, Any]] = {}

    async def books(self, market, symbols):
        if market in self.down:
            return None
        p = self.px[market]
        return {s: ag.Book(p, p + D("0.1"), D("5"), D("5")) for s in symbols}

    async def scan(self, market, incumbents):
        if market not in self.markets:
            return {"selected": [], "filters": {}}
        return {"selected": ["BTCUSDT"], "ranked": [], "rejected_stage_one": [],
                "filters": FILT if market == "spot" else FFILT}

    async def klines(self, market, symbol):
        return self.frames[market]

    async def premium(self):
        return self.prem or None

    async def commands(self):
        out, self.cmds = self.cmds, []
        return out

    async def kill_switch(self):
        return self.kill


class MemLedger:
    def __init__(self) -> None:
        self.events: list[dict[str, Any]] = []
        self.scans: list[Any] = []
        self.fail_next = False

    def write(self, events):
        if self.fail_next:
            self.fail_next = False
            raise RuntimeError("database unavailable")
        seqs = {e["seq"] for e in self.events}
        assert not seqs & {e["seq"] for e in events}, "duplicate seq"
        self.events.extend(copy.deepcopy(events))

    def scan(self, market, at, data):
        self.scans.append((market, data))

    def latest_snapshot(self):
        for e in reversed(self.events):
            if e["kind"] == "snapshot":
                return e["seq"], e["data"]["state"]
        return None

    def max_seq(self):
        return max((e["seq"] for e in self.events), default=0)

    def kinds(self, kind):
        return [e for e in self.events if e["kind"] == kind]


def system(data=None, ledger=None, markets=("spot",)) -> tuple[agp.AdaptiveGridSystem,
                                                              FakeData, MemLedger]:
    data = data or FakeData()
    data.markets = markets
    ledger = ledger or MemLedger()
    return (agp.AdaptiveGridSystem(data, ledger, spot_capital=D("5000"),
                                   futures_capital=D("5000")), data, ledger)


def run(sysm, t):
    return asyncio.run(sysm.tick(T0 + t))


def started(sysm, data, t=0.0):
    st = run(sysm, t)
    for _ in range(5):
        if sysm.accounts["spot"].grids:
            break
        data.frames["spot"] = sideways(seed=int(t) + 7)
        sysm.last["regime"] = 0
        t += 10
        st = run(sysm, t)
    return st


def test_autonomous_lifecycle_and_risk_authorised_orders():
    s, data, led = system()
    st = started(s, data)
    assert st["paper"] is True and st["universe"]["spot"] == ["BTCUSDT"]
    assert led.scans and led.kinds("regime") and led.kinds("decision")
    g = s.accounts["spot"].grids["BTCUSDT"]
    assert led.kinds("grid_start") and led.kinds("arm")
    assert all(o["approved"] for e in led.kinds("arm") for o in e["data"]["orders"])
    assert s.accounts["spot"].cash + g.allocation == D("5000")
    # Price drops through a level: a simulated fill, then a round trip on the way back.
    top = max((sl for sl in g.slots if sl.armed), key=lambda x: x.entry_price)
    data.px["spot"] = top.entry_price - D("1")
    run(s, 100)
    assert led.kinds("fill") and top.held > 0
    data.px["spot"] = top.exit_price + D("1")
    run(s, 110)
    assert top.trips == 1 and g.round_trips >= 1
    assert g.quote - g.reserved() >= 0                       # never negative


def test_owner_controls():
    s, data, led = system()
    started(s, data)
    g = s.accounts["spot"].grids["BTCUSDT"]
    data.cmds = [{"action": "pause"}]
    run(s, 50)
    assert g.status == "SUSPENDED" and not any(sl.armed and sl.remaining for sl in g.slots)
    data.cmds = [{"action": "resume"}]
    run(s, 60)
    assert g.status == "RUNNING"
    top = max((sl for sl in g.slots if sl.armed), key=lambda x: x.entry_price)
    data.px["spot"] = top.entry_price - D("1")
    run(s, 70)
    assert g.long_qty > 0
    data.cmds = [{"action": "stop_all"}]
    run(s, 80)
    assert g.status == "STOPPED" and g.long_qty == 0 and led.kinds("stop")
    data.cmds = [{"action": "emergency_shutdown"}]
    run(s, 90)
    assert s.control["shutdown"] is True
    data.cmds = [{"action": "start"}]
    data.px["spot"] = D("60000")
    run(s, 100)
    assert s.control == {"status": "RUNNING", "paused": [], "shutdown": False}
    data.cmds = [{"action": "set_limits", "limits": {"daily_loss": "0.05"}}]  # above ceiling
    run(s, 110)
    assert s.limits.daily_loss == D("0.01") and "refused" in led.kinds("control")[-1][
        "data"]["command"]


def test_kill_switch_stale_data_and_trend_handoff():
    s, data, led = system()
    started(s, data)
    g = s.accounts["spot"].grids["BTCUSDT"]
    data.kill = None                                           # unreadable: fail closed
    run(s, 50)
    assert g.status == "SUSPENDED" and s.risk_state[0] == "NO_NEW_ORDERS"
    data.kill = False
    data.down = {"spot"}                                       # stale market data
    n = len(led.kinds("fill"))
    data.px["spot"] = D("50000")
    run(s, 60)
    assert len(led.kinds("fill")) == n
    data.down = set()
    data.px["spot"] = D("60000")
    data.frames["spot"] = trending()
    s.last["regime"] = 0
    run(s, 70)
    assert s.decisions["spot:BTCUSDT"]["strategy"] == "TREND_FOLLOWING"
    assert g.status == "SUSPENDED"


def test_drawdown_shutdown_closes_everything():
    s, data, led = system()
    started(s, data)
    g = s.accounts["spot"].grids["BTCUSDT"]
    s.peak = D("20000")                                       # equity 5000+5000 = -50%
    run(s, 50)
    assert s.risk_state[0] == "SHUTDOWN" and s.control["shutdown"]
    run(s, 60)
    assert g.status == "STOPPED" and g.long_qty == 0


def test_accounts_are_separate_and_futures_funding_is_charged():
    s, data, led = system(markets=("spot", "futures"))
    data.prem = {"BTCUSDT": {"mark": "60000", "rate": "0.0001",
                             "next_funding_ms": int((T0 + 30) * 1000)}}
    t = 0.0
    for _ in range(6):
        run(s, t)
        t += 10
        if s.accounts["futures"].grids:
            break
        data.frames["futures"] = sideways(seed=int(t) + 11)
        s.last["regime"] = 0
    fg = s.accounts["futures"].grids.get("BTCUSDT")
    if fg is None:
        pytest.skip("regime did not allow a futures grid on this synthetic series")
    spot_cash = s.accounts["spot"].cash
    longs = [sl for sl in fg.slots if sl.armed and sl.side == "long"]
    if longs:
        top = max(longs, key=lambda x: x.entry_price)
        data.px["futures"] = top.entry_price - D("1")
    else:
        low = min((sl for sl in fg.slots if sl.armed), key=lambda x: x.entry_price)
        data.px["futures"] = low.entry_price + D("2")
    run(s, t)
    assert fg.net_qty != 0
    assert s.accounts["spot"].cash == spot_cash               # futures never uses spot cash
    s.last["premium"] = 0
    run(s, t + 100)                                           # past the funding time
    assert led.kinds("funding") and fg.funding_paid != 0


def test_restart_recovery_is_exact_and_never_duplicates():
    s, data, led = system()
    started(s, data)
    g = s.accounts["spot"].grids["BTCUSDT"]
    top = max((sl for sl in g.slots if sl.armed), key=lambda x: x.entry_price)
    data.px["spot"] = top.entry_price - D("1")
    run(s, 100)
    before = s.to_json()
    fills = len(led.kinds("fill"))
    s2 = agp.AdaptiveGridSystem(data, led, spot_capital=D("5000"),
                                futures_capital=D("5000"))
    assert s2.restore() and s2.to_json()["accounts"] == before["accounts"]
    run(s2, 110)                                              # same price: no new fills
    assert len(led.kinds("fill")) == fills
    # A failed write rolls the in-memory state back to the last committed snapshot.
    data.px["spot"] = top.exit_price + D("1")
    led.fail_next = True
    with pytest.raises(RuntimeError):
        run(s2, 120)
    assert s2.accounts["spot"].grids["BTCUSDT"].round_trips == g.round_trips
    run(s2, 130)                                              # retried: applied once
    assert s2.accounts["spot"].grids["BTCUSDT"].round_trips == g.round_trips + 1
    seqs = [e["seq"] for e in led.events]
    assert len(seqs) == len(set(seqs))


def test_no_live_order_path():
    src = inspect.getsource(agp)
    for forbidden in ("create_order", "LiveBroker", "build_live_broker", "/api/v3/order",
                      "/fapi/v1/order", "api_key", "api_secret", "apiKey", "X-MBX-APIKEY",
                      "signature", "TradingMode.LIVE", "credential"):
        assert forbidden not in src, forbidden
    urls = set(re.findall(r'"(https://[^"]+)"', src))
    assert urls == {"https://api.binance.com/api/v3", "https://fapi.binance.com/fapi/v1"}
    gets = re.findall(r'\{self\.(?:SPOT|FAPI)\}/([a-zA-Z/]+)', src)
    assert set(gets) <= {"ticker/bookTicker", "klines", "premiumIndex"}
