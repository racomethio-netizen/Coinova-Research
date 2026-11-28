"""Execution intents: client IDs, lots rounded down, slippage sign, stop verification, and
the Exness demo management steps (restore a missing stop, close an unprotected position,
half off at +1R with the stop to break-even)."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal as D
from types import SimpleNamespace

from nexus_execution import intents as ix

NOW = datetime(2026, 12, 1, 12, tzinfo=UTC)


def intent(**kw: object) -> SimpleNamespace:
    base = dict(client_id="nxOabc", side=1, stop=D("1.0980"), target=D("1.1040"),
                module="m", partial_taken=False, fill_price=D("1.1000"), events=[])
    return SimpleNamespace(**(base | kw))


def pos(**kw: object) -> dict[str, object]:
    return {"ticket": 7, "symbol": "EURUSDm", "side": "buy", "volume": 0.3,
            "price_open": 1.1, "sl": 1.098, "tp": 1.104} | kw


def test_ids_lots_slippage_and_stop_check():
    a, b = ix.new_client_id(), ix.new_client_id()
    assert a != b and ix.is_intent_id(a) and len(a) <= 31 and a.isalnum()
    assert ix.lots(D(25_400), D(100_000), D("0.01"), D("0.01"), D(200)) == D("0.25")
    assert ix.lots(D(400), D(100_000), D("0.01"), D("0.01"), D(200)) == 0      # below min lot
    assert ix.slippage(1, D(100), D("100.05")) == (D("0.05"), D("5"))
    assert ix.slippage(-1, D(100), D("100.05"))[0] == D("-0.05")               # sold higher
    assert ix.stop_ok(1, D("1.098"), D("1.09801"), D("0.00002"))
    assert not ix.stop_ok(1, D("1.098"), D(0), D("0.00002"))


def test_demo_open_and_management():
    it = intent()
    o = ix.demo_open(it, "EURUSDm", D("0.30"))
    assert o["action"] == "open" and o["sl"] == 1.098 and o["client_id"] == "nxOabc"
    ins, what = ix.demo_manage(it, pos(), 1.1005, 1.1006, 0.00001, 0.01, 0.01)
    assert ins == [] and what == ""                                     # protected, not 1R
    ins, what = ix.demo_manage(it, pos(sl=0.0), 1.1, 1.1001, 0.00001, 0.01, 0.01)
    assert what == "stop_fix" and ins[0]["action"] == "modify" and ins[0]["sl"] == 1.098
    tried = intent(events=[{"kind": "stop_fix"}, {"kind": "stop_fix"}])
    ins, what = ix.demo_manage(tried, pos(sl=0.0), 1.1, 1.1001, 0.00001, 0.01, 0.01)
    assert what == "unprotected" and ins[0]["action"] == "close"
    ins, what = ix.demo_manage(it, pos(), 1.1021, 1.1022, 0.00001, 0.01, 0.01)  # +1R
    assert what == "partial" and ins[0]["volume"] == 0.15 and ins[1]["sl"] == 1.1
    done = intent(partial_taken=True)
    assert ix.demo_manage(done, pos(sl=1.1), 1.103, 1.1031, 0.00001, 0.01, 0.01) == ([], "")
