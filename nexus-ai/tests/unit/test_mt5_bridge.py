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
    TIMEFRAME_D1, TIMEFRAME_H1, TIMEFRAME_M5 = 16408, 16385, 5

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
        self.calls.append(("rates", (name, start, count, tf)))
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


def test_intraday_bars_recent_by_default_whole_series_when_asked():
    mt5, state = FakeMT5(), {"sent": {"EURUSDm", "XAUUSDm"}}
    p = br.collect(mt5, ["EURUSD", "XAUUSD", "US500"], set(), state, now=NOW,
                   intraday=["EURUSD", "XAUUSD"])
    got = {(x["name"], x["timeframe"]): x for x in p["intraday"]}
    assert set(got) == {("EURUSDm", "H1"), ("EURUSDm", "M5"), ("XAUUSDm", "H1"),
                        ("XAUUSDm", "M5")}                       # US500: daily bars only
    assert not any(x["full"] for x in p["intraday"])
    counts = {(c[1][0], c[1][3]): c[1][2] for c in mt5.calls}
    assert counts[("EURUSDm", mt5.TIMEFRAME_M5)] == br.INTRADAY_RECENT["M5"]
    assert counts[("EURUSDm", mt5.TIMEFRAME_H1)] == br.INTRADAY_RECENT["H1"]
    assert {c[1][1] for c in mt5.calls} == {1}                   # forming bars skipped
    mt5.calls.clear()
    state["need_intraday"] = ["EURUSDm/H1", "XAUUSDm/H1", "EURUSDm/M5"]
    p = br.collect(mt5, ["EURUSD", "XAUUSD"], set(), state, now=NOW,
                   intraday=["EURUSD", "XAUUSD"])
    full = sorted((x["name"], x["timeframe"]) for x in p["intraday"] if x["full"])
    assert full == [("EURUSDm", "H1"), ("XAUUSDm", "H1")]        # at most 2 per cycle
    counts = {(c[1][0], c[1][3]): c[1][2] for c in mt5.calls}
    assert counts[("EURUSDm", mt5.TIMEFRAME_H1)] == br.INTRADAY_FULL["H1"]
    assert counts[("EURUSDm", mt5.TIMEFRAME_M5)] == br.INTRADAY_RECENT["M5"]
    assert br.collect(mt5, ["EURUSD"], set(), {}, now=NOW)["intraday"] == []


def test_no_bars_when_the_time_zone_is_unknown(monkeypatch: pytest.MonkeyPatch):
    mt5 = FakeMT5()
    mt5.symbol_info_tick = lambda name: SimpleNamespace(time=int(NOW) - 3 * 86400,  # type: ignore[method-assign]
                                                        bid=1.1, ask=1.2)
    monkeypatch.delenv("NEXUS_SERVER_UTC_OFFSET", raising=False)
    p = br.collect(mt5, ["EURUSD"], set(), {}, now=NOW, intraday=["EURUSD"])
    assert p["bars"] == {} and p["intraday"] == [] and p["symbols"]
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


# ------------------------------------------------------------------ history export
class HistoryMT5(FakeMT5):
    TIMEFRAME_M5, TIMEFRAME_H1 = 5, 16385

    def __init__(self, empty_first: int = 0) -> None:
        super().__init__()
        self.empty_left = empty_first

    def copy_rates_range(self, name: str, tf: int, start: Any, end: Any) -> Any:
        self.calls.append(("range", (name, tf, start, end)))
        if self.empty_left:
            self.empty_left -= 1
            return np.array([])
        step = {self.TIMEFRAME_M5: 300, self.TIMEFRAME_H1: 3600, self.TIMEFRAME_D1: 86400}[tf]
        t0, t1 = int(start.timestamp()), int(end.timestamp())
        dt = np.dtype([("time", "i8"), ("open", "f8"), ("high", "f8"), ("low", "f8"),
                       ("close", "f8"), ("tick_volume", "i8")])
        times = list(range(max(t0, t1 - 3 * step), t1 + step, step))[:3] if tf != 16408 \
            else [t1 - 2 * step, t1 - step, t1]      # last D1 bar is still forming
        return np.array([(t, 1.1, 1.2, 1.0, 1.15, 10) for t in times], dtype=dt)


def test_history_periods_months_years_and_all():
    now = br.datetime(2026, 11, 27, 10, tzinfo=br.UTC)
    m5 = br.periods("M5", 3, now)
    assert m5[0][0] == "2023-11" and m5[-1][0] == "2026-11" and len(m5) == 37
    assert m5[-1][2] == now                                   # current month ends now
    h1 = br.periods("H1", 12, now)
    assert [p[0] for p in h1][:2] == ["2015", "2016"] and h1[-1][0] == "2026"
    assert br.periods("D1", 0, now)[0][0] == "all"
    assert br._complete("2026-10", "M5", now) and not br._complete("2026-11", "M5", now)
    assert br._complete("2025", "H1", now) and not br._complete("2026", "H1", now)
    assert not br._complete("all", "D1", now)


def test_export_skips_held_chunks_retries_and_never_trades():
    mt5 = HistoryMT5(empty_first=2)
    sent: list[tuple[str, dict[str, Any]]] = []
    # NOW is 2025-11-27: the H1 plan covers 2014-2025.
    held = [{"symbol": "EURUSD", "timeframe": "H1", "period": str(y), "bars": 6000,
             "last_open": "x"} for y in range(2014, 2025)]           # complete years held
    held.append({"symbol": "EURUSD", "timeframe": "H1", "period": "2025", "bars": 10,
                 "last_open": "x"})                                   # current year: resend

    def send(path: str, body: dict[str, Any]) -> dict[str, Any]:
        sent.append((path, body))
        if path.endswith("/manifest"):
            return {"chunks": held}
        return {"bars_stored": len(body["rows"])}

    sleeps: list[float] = []
    stats = br.export_history(mt5, send, ["EURUSD", "GBPUSD"], OFFSET, now=NOW,
                              sleep=sleeps.append)
    posted = [b for p, b in sent if p.endswith("/history")]
    h1 = sorted(b["period"] for b in posted if b["timeframe"] == "H1")
    assert h1 == ["2025"]                         # only the unfinished year is resent
    assert {b["timeframe"] for b in posted} == {"D1", "H1", "M5"}
    assert all(b["read_only"] is True and b["base_name"] == "EURUSD" and
               b["symbol"] == "EURUSDm" and b["server_utc_offset_hours"] == OFFSET
               for b in posted)
    assert stats["sent"] == len(posted) and stats["skipped"] == 11
    assert sleeps[:2] == [5, 10]                  # the terminal was still downloading
    assert all(c[0] in ("range", "rates") for c in mt5.calls)


def test_export_to_folder_writes_utc_csv_without_the_forming_bar(tmp_path: Path):
    mt5 = HistoryMT5()
    stats = br.export_history(mt5, lambda p, b: {}, ["EURUSD"], OFFSET, now=NOW,
                              sleep=lambda s: None, out_dir=tmp_path)
    d1 = (tmp_path / "EURUSD_D1.csv").read_text().splitlines()
    assert d1[0] == "time,open,high,low,close,volume"
    assert len(d1) == 3                           # 2 closed bars; the forming one dropped
    assert d1[-1].startswith("2025-11-26T00:00:00Z")          # server 02:00 -> 00:00 UTC
    assert (tmp_path / "EURUSD_H1.csv").exists() and (tmp_path / "EURUSD_M5.csv").exists()
    assert stats["sent"] == 0 and stats["bars"] > 0
