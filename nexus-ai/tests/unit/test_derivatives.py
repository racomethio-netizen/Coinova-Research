"""Derivatives context: parsing Binance's documented public formats, liquidations, agent."""

from __future__ import annotations

import asyncio
import json
import time
from typing import Any

from nexus_agents.agents import derivatives_agent
from nexus_market.derivatives import LiquidationTracker, liquidation_stream, parse_snapshot

PREMIUM = {"symbol": "BTCUSDT", "markPrice": "60060.0", "indexPrice": "60000.0",
           "lastFundingRate": "0.00010000", "nextFundingTime": 1763769600000}
OI_NOW = {"openInterest": "80000.123", "symbol": "BTCUSDT", "time": 1763740000000}


def hist(field: str, values: list[float]) -> list[dict[str, Any]]:
    return [{"symbol": "BTCUSDT", field: str(v), "timestamp": 1763650000000 + i * 3_600_000}
            for i, v in enumerate(values)]


def test_parse_snapshot_from_documented_formats() -> None:
    oi = hist("sumOpenInterestValue", [4.0e9] + [4.2e9] * 23 + [4.4e9])   # 25 hourly rows
    snap = parse_snapshot("BTCUSDT", PREMIUM, OI_NOW, list(reversed(oi)),
                          hist("longShortRatio", [1.5] * 25), hist("longShortRatio", [1.1]),
                          hist("buySellRatio", [0.9]))
    assert snap["funding_rate"] == 0.0001 and abs(snap["basis"] - 0.001) < 1e-12
    assert abs(snap["oi_change_24h"] - 0.1) < 1e-9 and abs(snap["oi_change_1h"] - (4.4 / 4.2 - 1)) < 1e-9
    assert snap["accounts_long_short_ratio"] == 1.5 and snap["top_traders_position_ratio"] == 1.1
    assert snap["taker_buy_sell_ratio"] == 0.9 and snap["open_interest"] == 80000.123
    empty = parse_snapshot("BTCUSDT", {}, {}, [], [], [], [])
    assert empty["basis"] is None and empty["oi_change_24h"] is None


def force(sym: str, side: str, qty: str, price: str, t_ms: int) -> dict[str, Any]:
    return {"e": "forceOrder", "E": t_ms, "o": {"s": sym, "S": side, "o": "LIMIT", "f": "IOC",
                                                "q": qty, "p": price, "ap": price,
                                                "X": "FILLED", "z": qty, "T": t_ms}}


def test_liquidation_tracker_sides_windows_and_filter() -> None:
    now = int(time.time() * 1000)
    t = LiquidationTracker({"BTCUSDT"})
    assert t.add(force("BTCUSDT", "SELL", "2", "60000", now - 10 * 60_000))     # long liq
    assert t.add(force("BTCUSDT", "BUY", "1", "60000", now - 5 * 3_600_000))    # short liq
    assert not t.add(force("DOGEUSDT", "SELL", "100", "0.1", now))              # not traded
    s = t.summary("BTCUSDT", now)
    assert s["longs_liquidated_1h"] == 120_000 and s["shorts_liquidated_1h"] == 0
    assert s["shorts_liquidated_24h"] == 60_000 and "lower bound" in s["note"]


def test_liquidation_stream_reads_a_real_websocket_and_reconnects() -> None:
    import websockets

    class R:
        def __init__(self) -> None:
            self.kv: dict[str, Any] = {}

        async def set(self, k: str, v: str, ex: int | None = None) -> None:
            self.kv[k] = v

    async def go() -> tuple[LiquidationTracker, R, int]:
        connections = 0

        async def handler(ws: Any) -> None:
            nonlocal connections
            connections += 1
            now = int(time.time() * 1000)
            await ws.send(json.dumps(force("BTCUSDT", "SELL", "1", "50000", now)))
            await ws.close()                      # drop: client must reconnect

        async with websockets.serve(handler, "127.0.0.1", 0) as server:
            port = server.sockets[0].getsockname()[1]
            t, r, stop = LiquidationTracker({"BTCUSDT"}), R(), asyncio.Event()
            task = asyncio.create_task(liquidation_stream(t, r, stop, f"ws://127.0.0.1:{port}"))
            await asyncio.sleep(1.8)
            stop.set()
            await asyncio.wait_for(task, 5)
        return t, r, connections

    t, r, connections = asyncio.run(go())
    assert connections >= 2                                      # reconnected after a drop
    assert t.summary("BTCUSDT")["longs_liquidated_1h"] >= 50_000
    assert "nexus:liquidations:status" in r.kv


def test_derivatives_agent_uses_oi_basis_ratios_and_liquidations() -> None:
    m = {"funding_rate": 0.001, "basis": 0.004, "oi_change_24h": 0.12, "change_24h": -0.03,
         "accounts_long_short_ratio": 3.5, "top_traders_position_ratio": 1.2,
         "taker_buy_sell_ratio": 0.8,
         "liquidations": {"longs_liquidated_1h": 5_000_000, "shorts_liquidated_1h": 200_000}}
    r = derivatives_agent(m)
    text = " ".join(r.contradictions + r.evidence)
    assert "crowded longs" in text and "stretched basis" in text
    assert "new shorts entering" in text and "long liquidation cascade" in text
    assert r.view == "bearish" and r.confidence == 0.8
    thin = derivatives_agent({"funding_rate": 0.0001})
    assert any("not available" in w for w in thin.warnings) and thin.confidence == 0.3
