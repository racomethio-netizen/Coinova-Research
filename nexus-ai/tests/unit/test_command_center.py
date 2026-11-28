"""The command centre (unified brief, section J): built only from recorded sources;
missing sources stay missing (never zero, never estimated)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal as D

import pytest
from nexus_api import command_center as cc
from nexus_risk import portfolio_book as pb

NOW = datetime(2026, 11, 25, 12, 0, tzinfo=UTC)


def ago(**kw: float) -> str:
    return (NOW - timedelta(**kw)).isoformat()


def test_max_drawdown():
    assert cc.max_drawdown([]) is None and cc.max_drawdown([100.0]) is None
    assert cc.max_drawdown([100, 110, 99, 120, 108]) == pytest.approx(0.1)
    assert cc.max_drawdown([100, 101, 102]) == 0.0


def test_forex_performance_from_the_demo_sync():
    assert cc.forex_performance(None, [], NOW)["connected"] is False
    demo = {"at": ago(minutes=2), "currency": "USD", "equity": "10120.5", "balance": "10100",
            "limits": {"day_start": "10050", "week_start": "10200", "peak": "10300"},
            "positions": [{}, {}]}
    hist = [{"t": ago(hours=3), "equity": "10000"}, {"t": ago(hours=2), "equity": "10300"},
            {"t": ago(hours=1), "equity": "10000"}]
    fx = cc.forex_performance(demo, hist, NOW)
    assert fx["label"] == "DEMO" and fx["connected"] is True
    assert fx["open_pnl"] == "20.50" and fx["day_pnl"] == "70.50" and fx["week_pnl"] == "-79.50"
    assert fx["drawdown"] == "0.0174" and fx["positions"] == 2
    assert fx["max_drawdown_recorded"] == pytest.approx(0.0291, abs=1e-4)
    old = cc.forex_performance(demo | {"at": ago(hours=1)}, [], NOW)
    assert old["connected"] is False and old["max_drawdown_recorded"] is None


def test_crypto_performance_from_the_paper_report():
    assert "No paper equity" in cc.crypto_performance(None, [])["note"]
    rep = {"equity": "10250.00", "initial_capital": "10000", "realized_pnl": "300",
           "unrealized_pnl": "-50", "closed_trades": 4, "win_rate": "0.5",
           "max_drawdown": "0.02", "current_drawdown": "0.01"}
    c = cc.crypto_performance(rep, [{"t": ago(hours=1), "equity": "10250"}])
    assert c["label"] == "PAPER" and c["total_pnl"] == "250.00" and c["total_return"] == "0.0250"
    assert c["closed_trades"] == 4 and "not real profits" in c["note"]


def test_signals_strategies_and_calibration():
    board = {"at": ago(minutes=3), "board": [
        {"symbol": "BTC/USDT", "market": "crypto", "action": "BUY", "regime": "TREND_UP",
         "module": "crypto_swing_trend", "expected_r": None, "blockers": [],
         "reasons": ["module passed"], "risk": {"approved": True},
         "execution": {"sent": True, "client_id": "nxOabc"}},
        {"symbol": "EURUSD", "market": "forex", "action": "NO_TRADE", "regime": "RANGE",
         "blockers": ["forex market closed", "no stored bars"], "execution": None}]}
    sig = cc.signals(board, NOW)
    assert sig["counts"] == {"BUY": 1, "SELL": 0, "NO_TRADE": 1} and sig["stale"] is False
    assert sig["rows"][0]["order"] == "sent" and sig["rows"][1]["why"][0] == "forex market closed"
    assert cc.signals(None, NOW)["stale"] is True
    assert cc.signals(board | {"at": ago(hours=1)}, NOW)["stale"] is True

    rows = [{"code": "crypto_swing_trend", "name": "Swing", "market": "crypto",
             "style": "swing", "activation": "PAPER",
             "validation": {"passed": True, "sharpe": 1.11, "max_drawdown": 0.3},
             "forward": {"signals": 0, "closed": 0}},
            {"code": "fx_scalp", "name": "Scalp", "market": "forex", "style": "scalping",
             "activation": "OBSERVE",
             "validation": {"passed": False, "trades": 900, "win_rate": 0.40, "mean_r": -0.1},
             "forward": {"signals": 60, "closed": 50, "win_rate": 0.20, "mean_r": -0.4}},
            {"code": "fx_swing", "name": "Swing FX", "market": "forex", "style": "swing",
             "activation": None, "validation": {"passed": False, "trades": 200,
                                                "win_rate": 0.35},
             "forward": {"signals": 5, "closed": 5, "win_rate": 0.4}}]
    guardian = {"checks": [{"domain": "models", "name": "module:fx_scalp", "status": "critical",
                            "detail": "edge lost"}]}
    strats = cc.strategies(rows, guardian, {"crypto": "PAPER", "forex": "RESEARCH"})
    assert strats[0]["test"]["sharpe"] == 1.11 and strats[0]["health"] is None
    assert strats[0]["paper_orders"] == "yes: passed its test, crypto switch PAPER"
    assert strats[1]["paper_orders"] == "no: it did not pass its test"
    assert cc.paper_orders(True, "forex", {"forex": "RESEARCH"}) == \
        "no: the forex switch is RESEARCH"
    assert strats[1]["health"] == {"status": "critical", "detail": "edge lost"}
    assert strats[2]["activation"] == "OFF"

    ml = cc.load_ml_results()
    assert ml is not None and set(ml["modules"]) == {"fx_scalp_pullback", "fx_intraday_breakout"}
    cal = cc.calibration(strats, ml, sig)
    assert cal["evidence"] == [{"symbol": "BTC/USDT", "action": "BUY",
                                "module": "crypto_swing_trend", "expected_r": None,
                                "test_trades": None, "test_win_rate": None, "p_value": None,
                                "sharpe": 1.11}]
    verdicts = {m["code"]: m["verdict"] for m in cal["modules"]}
    assert verdicts["crypto_swing_trend"].startswith("no tested win rate")
    assert verdicts["fx_scalp"] == "worse than tested"            # 20% vs 40%, n=50
    assert "too few to judge" in verdicts["fx_swing"]
    assert all(m["used"] is False and m["passed"] is False for m in cal["ml"])
    assert all(m["auc"] < 0.55 and m["brier_skill"] < 0.05 for m in cal["ml"])
    assert "no probability model passed" in cal["explanation"]


def test_exposure_positions_and_pnl_from_the_risk_book():
    assert cc.exposure(None, NOW)["stale"] is True and cc.positions(None) == []
    book = {"at": ago(minutes=4), "equity": "20000.00", "day_loss": "0.009",
            "week_loss": "-0.01", "drawdown": "0.02", "open_risk_usd": "150.00",
            "gross_notional_usd": "4000.00",
            "limits": {"daily_loss": "0.01", "weekly_loss": "0.025", "drawdown_pause": "0.05",
                       "max_positions": 3, "max_open_risk": "0.01",
                       "max_gross_leverage": "1"},
            "accounts": [{"name": "crypto_paper", "market": "crypto", "equity": "10000.00",
                          "reconciled": True, "positions": [
                              {"symbol": "BTC/USDT", "side": 1, "quantity": "0.01",
                               "entry": "60000", "price": "61000", "stop": "58000",
                               "unrealized_usd": "10.00", "risk_usd": "20.00",
                               "notional_usd": "610.00"}]},
                         {"name": "exness_demo", "market": "forex", "equity": "10000.00",
                          "reconciled": False, "positions": []}]}
    ex = cc.exposure(book, NOW)
    rows = {r["name"]: r for r in ex["rows"]}
    assert rows["Open risk (if every stop is hit)"]["used"] == pytest.approx(0.75)
    assert rows["Open risk (if every stop is hit)"]["status"] == "ok"
    assert rows["Loss today"]["status"] == "warn"                 # 90% of the 1% limit
    assert rows["Loss this week"]["used"] == 0.0                  # a gain uses no limit
    assert rows["Open positions"]["value"] == 1.0 and rows["Drawdown from the peak"][
        "status"] == "ok"
    assert rows["Gross exposure (leverage)"]["value"] == pytest.approx(0.2)
    assert [a["reconciled"] for a in ex["accounts"]] == [True, False]
    (pos,) = cc.positions(book)
    assert pos["side"] == "long" and pos["unrealized_usd"] == "10.00" and pos[
        "account"] == "crypto_paper"
    p = cc.pnl({"open_pnl": "5.00"}, {"total_pnl": "250.00"}, book)
    assert p["today_pct"] == pytest.approx(-0.009) and p["week_pct"] == pytest.approx(0.01)
    assert p["forex_open"] == "5.00" and p["crypto_total"] == "250.00"


def test_assemble_with_nothing_recorded_shows_missing_not_zero():
    doc = cc.assemble({}, {}, None, NOW)
    assert doc["status"]["real_money"] == "OFF" and doc["status"]["guardian"] is None
    assert doc["forex"]["connected"] is False and "equity" not in doc["crypto"]
    assert doc["signals"]["rows"] == [] and doc["positions"] == []
    assert doc["pnl"]["combined_equity"] is None and doc["drawdown"]["crypto_max"] is None
    assert doc["models"]["note"].startswith("The Guardian has not run")
    assert doc["calibration"]["ml"] == []


def test_book_positions_report_unrealized_usd():
    long_btc = pb.Position("crypto_paper", "crypto", "BTC/USDT", 1, D("0.01"), D("60000"),
                           D("58000"), D("61000"))
    assert long_btc.unrealized_usd() == D("10.00")
    short_jpy = pb.Position("exness_demo", "forex", "USDJPY", -1, D("10000"), D("150"),
                            D("151"), D("149"))
    assert short_jpy.unrealized_usd() == pytest.approx(D("67.11"), abs=D("0.01"))
    cross = pb.Position("exness_demo", "forex", "EURGBP", 1, D("1000"), D("0.85"), None,
                        D("0.86"))
    assert cross.unrealized_usd() is None
