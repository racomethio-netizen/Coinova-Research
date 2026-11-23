"""Decision log: what is recorded, and when."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from nexus_api.decision_log import (
    compact_strategies,
    fingerprint,
    row_from_signal,
    should_record,
)

T = datetime(2026, 11, 23, 5, 0, tzinfo=UTC)


def sig(**kw: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "symbol": "BTC/USDT", "decision": "NO_TRADE", "regime": "range",
        "strategy": None, "probability": None, "expected_return": None,
        "blockers": ["news: news risk unknown"],
        "reasons": ["SHORT not available: spot is long-only and futures are disabled",
                    "hard blocker present"],
        "conflicts": [],
        "sources": {"strategies": {"trend_supertrend": {
            "eligible": False, "why_not": ["validation failed", "regime not allowed"],
            "robust_expectancy": None, "oos_trades_in_regime": 0}},
            "model": {"available": False, "reason": "no validated model is active"},
            "setups_now": []},
        "mtf_scores": {"trend": 0.2}, "regimes_by_timeframe": {"1h": "range"},
        "context": {"risk_state": "NORMAL", "entries_allowed": True, "spread_bps": 0.1},
        "snapshot": {"exchange": "binance", "timeframe": "15m",
                     "bar_open": T.isoformat(), "book_at": "2026-11-23T05:15:30+00:00"},
    }
    base.update(kw)
    return base


def test_compact_keeps_first_reason_and_setups() -> None:
    c = compact_strategies(sig()["sources"])
    assert c["strategies"]["trend_supertrend"] == {
        "eligible": False, "why_not": "validation failed", "robust_expectancy": None,
        "oos_trades_in_regime": 0}
    assert c["setups_now"] == []


def test_fingerprint_ignores_noise_but_sees_material_change() -> None:
    a = sig()
    b = sig(mtf_scores={"trend": 0.9}, context={"risk_state": "NORMAL", "spread_bps": 3})
    assert fingerprint(a) == fingerprint(b)       # inputs moved, decision basis did not
    assert fingerprint(a) != fingerprint(sig(decision="WAIT"))
    assert fingerprint(a) != fingerprint(sig(blockers=[]))
    assert fingerprint(a) != fingerprint(sig(context={"risk_state": "CLOSE_ONLY"}))
    setups = sig()
    setups["sources"] = {**setups["sources"], "setups_now": ["vwap_trend"]}
    assert fingerprint(a) != fingerprint(setups)


def test_should_record() -> None:
    fp = "x"
    assert should_record(None, T, fp)                                  # first row
    assert not should_record((T, fp), T, "y")                          # same bar: never twice
    assert not should_record((T, fp), T + timedelta(minutes=15), fp)   # unchanged
    assert should_record((T, fp), T + timedelta(minutes=15), "y")      # changed
    assert should_record((T, fp), T + timedelta(hours=1), fp)          # hourly heartbeat
    assert not should_record((T, fp), T - timedelta(minutes=15), "y")  # older bar


def test_row_fields() -> None:
    row = row_from_signal(sig(), decided_at=T + timedelta(minutes=15, seconds=45))
    assert row is not None
    assert row["snapshot_id"] == ("binance:BTC/USDT:15m@2026-11-23T05:00:00+00:00"
                                  "|book@2026-11-23T05:15:30+00:00")
    assert row["rejection_reason"] == "news: news risk unknown"
    assert row["risk_checks"]["entries_allowed"] is True
    assert row["models_used"]["available"] is False
    assert row["reasons"]["fingerprint"] == fingerprint(sig())
    # Without a blocker the reason (not the generic SHORT note) explains the NO_TRADE.
    r2 = row_from_signal(sig(blockers=[], reasons=[
        "SHORT not available: spot is long-only and futures are disabled",
        "no strategy or model has a validated edge in the range regime"]), decided_at=T)
    assert r2 is not None and r2["rejection_reason"].startswith("no strategy or model")
    # A pair without stored candles has no snapshot: nothing to log.
    assert row_from_signal({"symbol": "ETH/USDT", "decision": "NO_TRADE"}, decided_at=T) is None
