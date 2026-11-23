"""Pre-trade checks (NautilusTrader-style last gate before the broker)."""

from __future__ import annotations

from decimal import Decimal as D
from typing import Any

from nexus_exchange.models import Side
from nexus_execution.pretrade import PreTradeLimits, check


def run(**kw: Any) -> list[str]:
    base: dict[str, Any] = dict(purpose="entry", side=Side.BUY, amount=D("0.01"),
                                price=D(60_000), expected_price=D(60_000),
                                quote_total=D(10_000), entries_last_minute=0,
                                kill_switch=False, owner_floor=None, limits=PreTradeLimits())
    base.update(kw)
    return check(**base)


def test_normal_entry_passes() -> None:
    assert run() == []


def test_each_rule_refuses_an_entry() -> None:
    assert "notional" in run(amount=D("0.1"))[0]                      # 6,000 > 5,000
    assert "from the expected" in run(price=D(64_000))[0]             # 6.7% away
    assert run(kill_switch=True) == ["REDUCING: kill switch engaged"]
    assert run(kill_switch=None) == ["REDUCING: kill switch state unreadable"]
    assert "CLOSE_ONLY" in run(owner_floor="CLOSE_ONLY")[0]
    assert run(owner_floor="REDUCED_RISK") == []
    assert "submit rate" in run(entries_last_minute=10)[0]


def test_exits_always_pass() -> None:
    assert run(purpose="exit", side=Side.SELL, amount=D(5), price=D(1), kill_switch=None,
               owner_floor="EMERGENCY_STOP", entries_last_minute=99) == []
