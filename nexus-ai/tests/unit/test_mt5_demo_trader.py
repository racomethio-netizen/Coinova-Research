"""The Windows demo trader against a fake MetaTrader5 module: demo-only guard (also before
every order), what it sends to NEXUS, order requests for open/close/modify, and no
duplicate orders (client ID found in the history)."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import numpy as np
import pytest

DIR = Path(__file__).resolve().parents[2] / "apps" / "mt5_bridge"
sys.path.insert(0, str(DIR))
spec = importlib.util.spec_from_file_location("nexus_mt5_demo_trader",
                                              DIR / "nexus_mt5_demo_trader.py")
assert spec and spec.loader
dt = importlib.util.module_from_spec(spec)
spec.loader.exec_module(dt)

NOW = 1_764_201_600.0


class FakeMT5:
    ACCOUNT_TRADE_MODE_DEMO, TIMEFRAME_D1, TIMEFRAME_H1 = 0, 16408, 16385
    TRADE_ACTION_DEAL, TRADE_ACTION_SLTP = 1, 6
    ORDER_TYPE_BUY, ORDER_TYPE_SELL, ORDER_TIME_GTC = 0, 1, 0
    ORDER_FILLING_FOK, ORDER_FILLING_IOC, ORDER_FILLING_RETURN = 0, 1, 2

    def __init__(self, trade_mode: int = 0) -> None:
        self.trade_mode = trade_mode
        self.sent: list[dict[str, Any]] = []
        self.history: list[Any] = []

    def account_info(self) -> Any:
        return SimpleNamespace(login=123456, trade_mode=self.trade_mode, currency="USD",
                               balance=10_000.0, equity=10_050.0, margin=0.0,
                               margin_free=10_050.0, leverage=2000, server="Exness-MT5Trial",
                               company="Exness")

    def last_error(self) -> Any:
        return (1, "ok")

    def symbols_get(self) -> list[Any]:
        return [SimpleNamespace(name=n) for n in ("XAUUSDm", "EURUSDm", "BTCUSDm")]

    def symbol_select(self, name: str, on: bool) -> bool:
        return True

    def symbol_info(self, name: str) -> Any:
        gold = name.startswith("XAU")
        return SimpleNamespace(point=0.001 if gold else 0.00001,
                               trade_contract_size=100.0 if gold else 100_000.0,
                               volume_min=0.01, volume_step=0.01, volume_max=200.0,
                               filling_mode=2)

    def symbol_info_tick(self, name: str) -> Any:
        return SimpleNamespace(time=int(NOW) - 20, bid=2000.0 if name.startswith("XAU")
                               else 1.1, ask=2000.3 if name.startswith("XAU") else 1.10008)

    def copy_rates_from_pos(self, name: str, tf: int, start: int, count: int) -> Any:
        assert start == 1                                         # closed bars only
        d = np.dtype([("time", "i8"), ("open", "f8"), ("high", "f8"), ("low", "f8"),
                      ("close", "f8")])
        return np.array([(int(NOW) - 3600 * (i + 1), 1.0, 1.1, 0.9, 1.05)
                         for i in range(3)], dtype=d)

    def positions_get(self) -> list[Any]:
        return [SimpleNamespace(ticket=9, symbol="EURUSDm", type=0, volume=0.1,
                                price_open=1.09, sl=1.085, tp=1.1, time=int(NOW) - 7200,
                                magic=dt.MAGIC, comment="nxE1r150"),
                SimpleNamespace(ticket=10, symbol="EURUSDm", type=1, volume=1.0,
                                price_open=1.2, sl=0, tp=0, time=int(NOW), magic=1,
                                comment="manual")]

    def history_orders_get(self, start: Any, end: Any) -> list[Any]:
        return self.history

    def orders_get(self) -> list[Any]:
        return []

    def order_send(self, req: dict[str, Any]) -> Any:
        self.sent.append(req)
        return SimpleNamespace(retcode=10009, comment="done")


def test_refuses_real_accounts_before_anything_and_before_every_order():
    with pytest.raises(dt.NotDemo):
        dt.collect(FakeMT5(trade_mode=2), {}, [], now=NOW)
    mt5 = FakeMT5()
    ins = [{"action": "open", "symbol": "EURUSDm", "side": "buy", "volume": 0.1, "sl": 1.09,
            "tp": 1.11, "client_id": "nxE1r150"}]
    mt5.trade_mode = 2                                           # switched to a real account
    with pytest.raises(dt.NotDemo):
        dt.execute(mt5, ins, now=NOW)
    assert mt5.sent == []


def test_collect_sends_demo_state_closed_bars_and_only_nexus_positions():
    body = dt.collect(FakeMT5(), {}, [{"client_id": "x", "retcode": 10009, "message": ""}],
                      now=NOW)
    assert body["account"]["trade_mode"] == "demo"
    assert [s["base_name"] for s in body["symbols"]] == ["XAUUSD", "EURUSD"]
    assert set(body["bars"]["XAUUSDm"]) == {"D1"} and set(body["bars"]["EURUSDm"]) == {"H1"}
    assert [p["ticket"] for p in body["positions"]] == [9]                 # magic filter
    assert body["server_utc_offset_hours"] == 0 and body["results"][0]["client_id"] == "x"


def test_execute_builds_requests_and_never_resends():
    mt5 = FakeMT5()
    ins = [{"action": "open", "symbol": "EURUSDm", "side": "buy", "volume": 0.1,
            "sl": 1.09, "tp": 1.11, "client_id": "nxE2r150"},
           {"action": "close", "symbol": "EURUSDm", "side": "sell", "volume": 0.1,
            "ticket": 9, "client_id": "nxEc9"},
           {"action": "modify", "symbol": "EURUSDm", "ticket": 9, "sl": 1.095, "tp": 1.1,
            "client_id": "nxEm9s109500"}]
    res = dt.execute(mt5, ins, now=NOW)
    assert [r["retcode"] for r in res] == [10009] * 3
    o, c, m = mt5.sent
    assert o["type"] == mt5.ORDER_TYPE_BUY and o["price"] == 1.10008 and o["sl"] == 1.09
    assert o["magic"] == dt.MAGIC and o["comment"] == "nxE2r150"
    assert o["type_filling"] == mt5.ORDER_FILLING_IOC                     # filling_mode 2
    assert c["position"] == 9 and c["type"] == mt5.ORDER_TYPE_SELL and c["price"] == 1.1
    assert m["action"] == mt5.TRADE_ACTION_SLTP and m["position"] == 9 and m["sl"] == 1.095
    mt5.sent.clear()
    mt5.history = [SimpleNamespace(comment="nxE2r150")]
    res = dt.execute(mt5, ins[:1], now=NOW)
    assert mt5.sent == [] and res[0]["message"] == "already sent; skipped"


def test_source_never_logs_in_or_handles_a_password():
    src = (DIR / "nexus_mt5_demo_trader.py").read_text()
    for call in ("mt5.login(", "password=", "password ="):
        assert call not in src
