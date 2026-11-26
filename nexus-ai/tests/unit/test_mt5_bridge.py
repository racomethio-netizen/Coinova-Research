"""The Windows MT5 bridge, run against a fake MetaTrader5 module: symbol suffixes,
server-time offset, skipping the forming bar, full history once, positions, and that the
source has no trading call at all."""

from __future__ import annotations

import importlib.util
import re
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import numpy as np
import pytest

SRC = Path(__file__).resolve().parents[2] / "apps" / "mt5_bridge" / "nexus_mt5_bridge.py"
spec = importlib.util.spec_from_file_location("nexus_mt5_bridge", SRC)
assert spec and spec.loader
br = importlib.util.module_from_spec(spec)
spec.loader.exec_module(br)

NOW = 1_764_201_600.0                       # 2025-11-27 00:00 UTC
OFFSET = 2                                  # a broker on UTC+2 server time


class FakeMT5:
    TIMEFRAME_D1 = 16408

    def __init__(self) -> None:
        self.calls: list[tuple[str, Any]] = []
        self.names = ["EURUSDm", "XAUUSDm", "US500m", "BTCUSDm", "EURUSDm.x"]

    def account_info(self) -> Any:
        return SimpleNamespace(login=12345678, trade_mode=0, currency="USD", balance=10000.0,
                               equity=10012.5, margin=0.0, margin_free=10012.5, leverage=2000,
                               server="Exness-MT5Trial", company="Exness")

    def last_error(self) -> Any:
        return (1, "ok")

    def symbols_get(self) -> list[Any]:
        return [SimpleNamespace(name=n) for n in self.names]

    def symbol_select(self, name: str, on: bool) -> bool:
        return True

    def symbol_info(self, name: str) -> Any:
        return SimpleNamespace(description=name, digits=5 if "EUR" in name else 2,
                               point=0.00001, trade_contract_size=100000.0, volume_min=0.01,
                               volume_step=0.01, volume_max=200.0, swap_long=-7.1,
                               swap_short=1.2, spread=12, currency_base="EUR",
                               currency_profit="USD")

    def symbol_info_tick(self, name: str) -> Any:
        return SimpleNamespace(time=int(NOW) + OFFSET * 3600 - 30, bid=1.09812345,
                               ask=1.09824567)

    def copy_rates_from_pos(self, name: str, tf: int, start: int, count: int) -> Any:
        self.calls.append(("rates", (name, start, count)))
        dt = np.dtype([("time", "i8"), ("open", "f8"), ("high", "f8"), ("low", "f8"),
                       ("close", "f8"), ("tick_volume", "i8")])
        return np.array([(int(NOW) - 86400 * (i + 1) + OFFSET * 3600, 1.1, 1.2, 1.0, 1.15, 100)
                         for i in range(min(count, 3))], dtype=dt)

    def positions_get(self) -> list[Any]:
        return [SimpleNamespace(symbol="EURUSDm", type=1, volume=0.01, price_open=1.1,
                                profit=-1.5, swap=-0.2)]


def test_resolves_exness_suffixes_and_reports_missing():
    m, missing = br.resolve(["EURUSDm", "EURUSDm.x", "XAUUSD", "US500m"],
                            ["EURUSD", "XAUUSD", "US500", "USOIL"])
    assert m == {"EURUSD": "EURUSDm", "XAUUSD": "XAUUSD", "US500": "US500m"}
    assert missing == ["USOIL"]


def test_offset_needs_a_fresh_tick():
    assert br.measure_offset([int(NOW) + 7200 - 30], NOW) == 2
    assert br.measure_offset([int(NOW) - 2 * 86400], NOW) is None      # weekend: stale
    assert br.measure_offset([], NOW) is None


def test_collect_reads_without_trading_and_sends_full_history_once():
    mt5, state = FakeMT5(), {}
    p = br.collect(mt5, ["EURUSD", "XAUUSD", "US500", "USOIL"], set(), state, now=NOW)
    assert p["read_only"] is True and p["server_utc_offset_hours"] == OFFSET
    assert p["account"]["trade_mode"] == "demo" and p["account"]["leverage"] == 2000
    assert {s["base_name"] for s in p["symbols"]} == {"EURUSD", "XAUUSD", "US500"}
    assert p["unresolved"] == ["USOIL"]
    starts = {c[1][1] for c in mt5.calls}
    assert starts == {1}                                                # forming bar skipped
    assert {c[1][2] for c in mt5.calls} == {br.FULL_HISTORY}            # first time: full
    mt5.calls.clear()
    br.collect(mt5, ["EURUSD"], set(), state, now=NOW)
    assert {c[1][2] for c in mt5.calls} == {br.RECENT}                  # then recent only
    mt5.calls.clear()
    br.collect(mt5, ["EURUSD"], {"EURUSDm"}, state, now=NOW)
    assert {c[1][2] for c in mt5.calls} == {br.FULL_HISTORY}            # NEXUS asked
    assert p["positions"] == [{"symbol": "EURUSDm", "side": "sell", "volume": "0.01",
                               "price_open": "1.1", "profit": "-1.5", "swap": "-0.2"}]


def test_no_bars_when_the_time_zone_is_unknown(monkeypatch: pytest.MonkeyPatch):
    mt5 = FakeMT5()
    mt5.symbol_info_tick = lambda name: SimpleNamespace(time=int(NOW) - 3 * 86400,  # type: ignore[method-assign]
                                                        bid=1.1, ask=1.2)
    monkeypatch.delenv("NEXUS_SERVER_UTC_OFFSET", raising=False)
    p = br.collect(mt5, ["EURUSD"], set(), {}, now=NOW)
    assert p["bars"] == {} and p["symbols"]
    monkeypatch.setenv("NEXUS_SERVER_UTC_OFFSET", "0")
    p = br.collect(mt5, ["EURUSD"], set(), {}, now=NOW)
    assert p["bars"] and p["server_utc_offset_hours"] == 0


def test_source_has_no_trading_calls_and_refuses_plain_http():
    src = SRC.read_text()
    for forbidden in ("order_send", "order_check", "order_calc", "positions_close",
                      "Close(", "TRADE_ACTION", "ORDER_TYPE"):
        assert forbidden not in src, forbidden
    assert not re.search(r"\bpassword\s*=", src)
    with pytest.raises(ValueError):
        br.post("http://example.com", "t", {})
