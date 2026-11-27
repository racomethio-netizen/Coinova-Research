"""Strategy-module registry and forward observation (no database): the committed test
results become each module's validation summary, signals only after the switch and only
of closed bars, symbols in NEXUS's stored format, and resolution with the shared
simulator (open while unknown, closed with R, dropped when no bar came)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pandas as pd
import pytest
from nexus_backtest import module_observer as mo
from nexus_strategies import modules as mods

NOW = datetime(2026, 11, 25, 12, 2, tzinfo=UTC)


def test_committed_results_give_every_module_a_validation_summary():
    results, before = mo.load_results()
    assert results is not None and before is not None
    for code in mods.MODULES:
        v = mo.validation_summary(code, results, before)
        assert v is not None and v["evidence"] == mods.get(code).evidence
    assert mo.validation_summary("crypto_swing_trend", results, before)["passed"] is True
    for code in ("fx_scalp_pullback", "fx_intraday_breakout", "fx_swing_trend",
                 "crypto_scalp_volume", "crypto_breakout_retest"):
        v = mo.validation_summary(code, results, before)
        assert v["passed"] is False and v["trades"] > 0
        assert v["mean_r"] < 0 and v["mean_r_before_costs"] is not None
    assert mo.validation_summary("fx_scalp_pullback", None, None) is None


def test_symbols_and_schedule():
    assert mo.db_symbol(mods.get("crypto_scalp_volume"), "BTCUSDT") == "BTC/USDT"
    assert mo.db_symbol(mods.get("fx_scalp_pullback"), "EURUSD") == "EURUSD"
    assert "crypto_swing_trend" not in mo.OBSERVABLE           # it has its own forward test
    at = lambda h, m: datetime(2026, 11, 25, h, m, tzinfo=UTC)  # noqa: E731
    assert mo.scan_due("fx_intraday_breakout", at(8, 0))
    assert not mo.scan_due("fx_intraday_breakout", at(14, 0))
    assert mo.scan_due("fx_swing_trend", at(22, 5)) and not mo.scan_due("fx_swing_trend", at(9, 0))
    assert mo.scan_due("crypto_breakout_retest", at(9, 3))
    assert not mo.scan_due("crypto_breakout_retest", at(9, 30))
    assert mo.scan_due("fx_scalp_pullback", at(3, 0))


def test_new_signals_only_after_the_switch_and_only_closed_bars(monkeypatch):
    spec = mods.get("crypto_scalp_volume")
    idx = pd.DatetimeIndex([NOW - timedelta(hours=2), NOW - timedelta(minutes=30),
                            NOW - timedelta(minutes=3)]).as_unit("ns")
    fake = mods.base.make_signals(idx, [1, 1, 1], [1.0, 1.0, 1.0], 2.0, 48)
    monkeypatch.setitem(mods.MODULES, spec.code,
                        __import__("dataclasses").replace(spec, signals=lambda f, c: fake))
    out = mo.new_signals(mods.get(spec.code), {}, NOW - timedelta(hours=1), NOW)
    assert list(out.index) == [idx[1]]           # before the switch / still forming: no


def bars(start: datetime, closes: list[float], step: int = 300) -> pd.DataFrame:
    idx = pd.date_range(start, periods=len(closes), freq=f"{step}s", tz="UTC").as_unit("ns")
    c = pd.Series(closes, index=idx)
    return pd.DataFrame({"open": c.shift(1).fillna(c.iloc[0]), "high": c + 1, "low": c - 1,
                         "close": c, "volume": 1.0}, index=idx)


def rec(**kw: object) -> SimpleNamespace:
    base = {"signal_time": NOW - timedelta(hours=1), "side": 1, "stop": 95.0, "rr": 2.0,
            "max_bars": 48, "exit_at": None, "spread": None, "symbol": "BTCUSDT"}
    return SimpleNamespace(**(base | kw))


def test_resolution_open_closed_and_dropped():
    spec = mods.get("crypto_scalp_volume")
    sig = NOW - timedelta(hours=1)
    flat = bars(sig + timedelta(minutes=5), [100.0] * 5)
    upd = mo.resolve(spec, rec(), flat, NOW)
    assert upd is not None and upd["status"] == "open" and upd["entry"] > 0
    up = bars(sig + timedelta(minutes=5), [100.0, 100.0, 130.0])
    upd = mo.resolve(spec, rec(), up, NOW)
    assert upd is not None and upd["status"] == "closed" and upd["reason"] == "target"
    assert float(upd["net_r"]) == pytest.approx(2.0 - 0.001 * (float(upd["entry"])
                                                                + float(upd["exit_price"]))
                                                / float(upd["risk"]), abs=1e-4)
    empty = bars(sig, [100.0]).iloc[:0]
    assert mo.resolve(spec, rec(), empty, sig + timedelta(minutes=10)) is None   # too early
    gone = mo.resolve(spec, rec(), empty, NOW)
    assert gone is not None and gone["status"] == "dropped"
    late = mo.resolve(spec, rec(), bars(sig + timedelta(hours=2), [100.0]), NOW)
    assert late is not None and late["status"] == "dropped"
    beyond = mo.resolve(spec, rec(stop=150.0), flat, NOW)
    assert beyond is not None and beyond["status"] == "dropped"
