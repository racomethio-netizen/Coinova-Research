from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal as D
from typing import Any

import pytest
from nexus_config import RiskSettings
from nexus_risk.allocator import VenueState, check_sell_venue, choose_buy_venue, reconcile_balances
from nexus_risk.kill_switch import KillSwitch
from nexus_risk.limits import MarketCheck, RiskSnapshot, evaluate_portfolio, evaluate_trade
from nexus_risk.sizing import SizingInput, size_position

NOW = datetime(2026, 6, 1, 12, tzinfo=UTC)


def sizing(**kw: Any) -> SizingInput:
    base = dict(equity=D(10_000), cash_available=D(10_000), entry_price=D(100), stop_price=D(98),
                risk_per_trade=D("0.0025"), fee_rate=D("0.001"), slippage_rate=D("0.0005"),
                exposure_now=D(0), max_exposure=D("0.5"), asset_exposure_now=D(0),
                max_asset_concentration=D("0.25"), amount_step=D("0.001"))
    base.update(kw)
    return SizingInput(**base)  # type: ignore[arg-type]


class TestSizing:
    def test_risk_budget_includes_costs(self) -> None:
        r = size_position(sizing())
        # per-unit loss = 2 + 100*0.0015 + 98*0.0015 = 2.297 ; budget = 25
        assert r.binding_constraint == "risk"
        assert r.quantity == D("10.883")  # floor(25 / 2.297, 0.001)
        assert r.risk_amount <= D(25)
        assert r.risk_amount == D("10.883") * D("2.297")

    def test_exposure_cap(self) -> None:
        r = size_position(sizing(stop_price=D("99.9"), exposure_now=D(4_900)))
        assert r.binding_constraint == "exposure"
        assert r.notional <= D(100)

    def test_concentration_and_cash_caps(self) -> None:
        assert size_position(sizing(stop_price=D("99.9"))).binding_constraint == "concentration"
        r = size_position(sizing(stop_price=D("99.9"), cash_available=D(50)))
        assert r.binding_constraint == "cash" and r.notional < D(50)

    @pytest.mark.parametrize(("kw", "msg"), [
        ({"stop_price": D(100)}, "below entry"),
        ({"stop_price": D(101)}, "below entry"),
        ({"equity": D(0)}, "equity"),
        ({"exposure_now": D(5_000)}, "no room"),
        ({"min_notional": D(5_000)}, "below exchange minimum"),
        ({"min_amount": D(1_000)}, "below exchange minimum"),
    ])
    def test_rejections(self, kw: dict[str, Any], msg: str) -> None:
        r = size_position(sizing(**kw))
        assert not r.approved and msg in (r.rejected_reason or "")

    def test_never_exceeds_risk_budget_property(self) -> None:
        import random
        rng = random.Random(3)
        for _ in range(500):
            entry = D(str(round(rng.uniform(1, 100_000), 2)))
            stop = entry * D(str(round(rng.uniform(0.9, 0.999), 4)))
            r = size_position(sizing(entry_price=entry, stop_price=stop,
                                     amount_step=D("0.00001"), cash_available=D(10**9),
                                     max_exposure=D("0.5"), max_asset_concentration=D("0.5")))
            if r.approved:
                assert r.risk_amount <= D(25) + D("1e-12")
                assert r.notional * (1 + D("0.0015")) <= D(5_000) + D("1e-9")


def snapshot(**kw: Any) -> RiskSnapshot:
    base = dict(equity=D(10_000), day_start_equity=D(10_000), week_start_equity=D(10_000),
                peak_equity=D(10_000), open_positions=0, pending_entries=0, exposure=D(0),
                held_assets=frozenset(), kill_switch=False, unreconciled_orders=0,
                balances_reconciled=True, as_of=NOW)
    base.update(kw)
    return RiskSnapshot(**base)  # type: ignore[arg-type]


MKT = MarketCheck("BTC", True, D(2), 1.0)


class TestLimits:
    def test_clean_state_approved(self) -> None:
        assert evaluate_trade(snapshot(), MKT, RiskSettings()).approved

    @pytest.mark.parametrize(("kw", "fragment"), [
        ({"equity": D(9_900)}, "daily loss"),
        ({"equity": D(9_800), "day_start_equity": D(9_850)}, "weekly loss"),
        ({"kill_switch": True}, "kill switch"),
        ({"unreconciled_orders": 1}, "unknown state"),
        ({"balances_reconciled": False}, "not reconciled"),
        ({"open_positions": 2}, "max open positions"),
        ({"open_positions": 1, "pending_entries": 1}, "max open positions"),
        ({"exposure": D(5_000)}, "exposure"),
        ({"held_assets": frozenset({"BTC"})}, "duplicate"),
    ])
    def test_blocks(self, kw: dict[str, Any], fragment: str) -> None:
        d = evaluate_trade(snapshot(**kw), MKT, RiskSettings())
        assert not d.approved and any(fragment in r for r in d.reasons), d.reasons

    def test_just_below_daily_limit_warns_but_allows(self) -> None:
        d = evaluate_trade(snapshot(equity=D(9_901)), MKT, RiskSettings())
        assert d.approved and ("daily_loss_warning", "warning") in d.events

    def test_drawdown_shutdown(self) -> None:
        d = evaluate_portfolio(snapshot(equity=D(9_500), peak_equity=D(10_000),
                                        day_start_equity=D(9_500), week_start_equity=D(9_500)),
                               RiskSettings())
        assert d.shutdown and not d.approved
        assert ("max_drawdown_shutdown", "critical") in d.events

    @pytest.mark.parametrize("mkt", [replace(MKT, exchange_healthy=False),
                                     replace(MKT, spread_bps=D(40)),
                                     replace(MKT, spread_bps=None),
                                     replace(MKT, price_age_seconds=None),
                                     replace(MKT, price_age_seconds=120)])
    def test_market_conditions(self, mkt: MarketCheck) -> None:
        assert not evaluate_trade(snapshot(), mkt, RiskSettings()).approved


def venue(name: str, **kw: Any) -> VenueState:
    base = dict(exchange=name, lists_pair=True, healthy=True, quote_free=D(5_000),
                quote_reserved=D(0), best_ask=D(100), best_bid=D("99.9"),
                ask_depth_quote=D(100_000), taker_fee=D("0.001"),
                balances_synced_at=NOW - timedelta(seconds=30), balance_mismatch=False)
    base.update(kw)
    return VenueState(**base)  # type: ignore[arg-type]


class TestAllocator:
    def test_cheapest_effective_price_wins(self) -> None:
        a = choose_buy_venue([venue("binance", best_ask=D("100.05"), taker_fee=D("0.001")),
                              venue("kraken", best_ask=D(100), taker_fee=D("0.0026"))],
                             notional=D(1_000), now=NOW)
        assert a.exchange == "binance"

    @pytest.mark.parametrize(("kw", "why"), [
        ({"healthy": False}, "unhealthy"), ({"lists_pair": False}, "not listed"),
        ({"balance_mismatch": True}, "mismatch"), ({"balances_synced_at": None}, "stale"),
        ({"balances_synced_at": NOW - timedelta(hours=1)}, "stale"),
        ({"quote_reserved": D(4_500)}, "insufficient cash"),
        ({"ask_depth_quote": D(2_000)}, "depth"), ({"best_ask": None}, "no quote"),
    ])
    def test_rejections(self, kw: dict[str, Any], why: str) -> None:
        a = choose_buy_venue([venue("binance", **kw)], notional=D(1_000), now=NOW)
        assert a.exchange is None and why in a.rejected[0][1]

    def test_sell_must_use_position_venue(self) -> None:
        assert check_sell_venue(venue("kraken"), now=NOW).exchange == "kraken"
        assert check_sell_venue(venue("kraken", healthy=False), now=NOW).exchange is None

    def test_reconcile(self) -> None:
        diffs = reconcile_balances({"BTC": D("0.5"), "USDT": D(100)},
                                   {"BTC": D("0.5"), "USDT": D("99.5"), "ETH": D(1)})
        assert [(d.asset, d.difference) for d in diffs] == [("ETH", D(1)), ("USDT", D("-0.5"))]
        assert reconcile_balances({"USDT": D(100)}, {"USDT": D("99.995")},
                                  tolerance={"USDT": D("0.01")}) == []


class FakeRedis:
    def __init__(self, broken: bool = False) -> None:
        self.data: dict[str, Any] = {}
        self.stream: list[Any] = []
        self.broken = broken

    async def get(self, k: str) -> Any:
        if self.broken:
            raise ConnectionError("down")
        return self.data.get(k)

    async def set(self, k: str, v: Any, **_: Any) -> None:
        self.data[k] = v

    async def delete(self, k: str) -> None:
        self.data.pop(k, None)

    async def xadd(self, name: str, fields: Any, **_: Any) -> None:
        self.stream.append(fields)


class TestKillSwitch:
    async def test_engage_and_clear(self) -> None:
        r = FakeRedis()
        ks = KillSwitch(r)
        assert not await ks.is_engaged()
        await ks.engage("drawdown", actor="system:risk")
        assert await ks.is_engaged() and len(r.stream) == 1
        with pytest.raises(PermissionError):
            await ks.clear(actor="system:risk", reason="auto")
        with pytest.raises(ValueError):
            await ks.clear(actor="user:1", reason="")
        await ks.clear(actor="user:1", reason="reviewed losses, resuming paper")
        assert not await ks.is_engaged()

    async def test_fails_closed(self) -> None:
        assert await KillSwitch(FakeRedis(broken=True)).is_engaged() is True
