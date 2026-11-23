"""Exchange capability registry: summary from CCXT data, unknowns stay unknown, errors are
reported and never filled in."""

from __future__ import annotations

from typing import Any

import pytest
from nexus_exchange import registry


def _m(sym: str, **kw: Any) -> dict[str, Any]:
    base, quote = sym.split("/")[0], sym.split("/")[1].split(":")[0]
    return {"symbol": sym, "base": base, "quote": quote, "active": True,
            "precision": {"price": 0.1, "amount": 0.001},
            "limits": {"amount": {"min": 0.001}, "cost": {"min": 5}, "leverage": {}},
            "maker": 0.0002, "taker": 0.0005, **kw}


MARKETS = {
    "BTC/USDT": _m("BTC/USDT", spot=True),
    "ETH/USDT": _m("ETH/USDT", spot=True),
    "OLD/USDT": _m("OLD/USDT", spot=True, active=False),
    "BTC/USDT:USDT": _m("BTC/USDT:USDT", swap=True, linear=True, settle="USDT",
                        contractSize=0.001),
    "BTC/USD:BTC": _m("BTC/USD:BTC", swap=True, inverse=True, settle="BTC"),
}


def test_summary_counts_active_usdt_markets_and_keeps_unknowns() -> None:
    has = {"createMarketOrder": True, "createPostOnlyOrder": "emulated",
           "createReduceOnlyOrder": None, "setLeverage": False, "setMarginMode": True}
    r = registry.summarise("okx", MARKETS, has, {"watchOrders": True},
                           {"test": {"rest": "x"}}, 100.0)
    assert r["spot"]["usdt_markets"] == 2                     # inactive excluded
    assert r["usdt_perpetual"]["markets"] == 1                # inverse excluded
    assert r["usdt_perpetual"]["sample"]["contract_size"] == 0.001
    assert r["usdt_perpetual"]["leverage_limits"].startswith("not public")
    c = r["capabilities"]
    assert c["market_order"] is True and c["post_only"] == "emulated"
    assert c["reduce_only"] is None and c["stop_loss_order"] is None    # unknown, not False
    assert c["set_leverage"] is False and r["margin_modes"] == ["isolated", "cross"]
    assert r["websocket"]["ws_orders"] is True and r["sandbox"] is True
    assert r["nexus"]["spot_adapter"] is False


def test_detect_reports_errors_instead_of_guessing(monkeypatch: pytest.MonkeyPatch) -> None:
    import ccxt

    class Blocked:
        def __init__(self, _opts: Any) -> None:
            pass

        def load_markets(self) -> Any:
            raise ccxt.ExchangeNotAvailable("451 Service unavailable from a restricted location")

    monkeypatch.setattr(ccxt, "bybit", Blocked)
    r = registry.detect("bybit")
    assert r["ok"] is False and "restricted location" in r["error"]
    assert "spot" not in r and r["nexus"]["note"]
