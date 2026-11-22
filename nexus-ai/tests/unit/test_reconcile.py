"""Balance comparison rule: the exchange must hold at least what NEXUS believes it holds."""

from __future__ import annotations

from decimal import Decimal as D

from nexus_common.timeutil import utcnow
from nexus_execution.reconcile import ReconResult, ReconStatus, compare, tolerance


def test_equal_balances_match() -> None:
    m, u = compare("binance", {"USDT": D(100), "BTC": D("0.01")},
                   {"USDT": D(100), "BTC": D("0.01")})
    assert m == [] and u == {}


def test_exchange_holding_less_is_a_mismatch_with_the_shortfall() -> None:
    m, _ = compare("binance", {"USDT": D(100), "BTC": D("0.01")},
                   {"USDT": D(100), "BTC": D("0.005")})
    assert len(m) == 1 and m[0].asset == "BTC" and D(m[0].shortfall) == D("0.005")


def test_missing_asset_on_the_exchange_is_a_mismatch() -> None:
    m, _ = compare("binance", {"ETH": D("0.2")}, {})
    assert [x.asset for x in m] == ["ETH"]


def test_extra_funds_on_the_exchange_are_reported_not_blocking() -> None:
    m, u = compare("binance", {"USDT": D(100)}, {"USDT": D(150), "BNB": D(1)})
    assert m == [] and u == {"binance:USDT": "50", "binance:BNB": "1"}


def test_tiny_rounding_differences_are_tolerated() -> None:
    assert tolerance(D(100)) == D("0.1")
    m, _ = compare("binance", {"USDT": D(100)}, {"USDT": D("99.95")})
    assert m == []
    m, _ = compare("binance", {"USDT": D(100)}, {"USDT": D("99.8")})
    assert len(m) == 1


def test_blocking_statuses() -> None:
    now = utcnow()
    assert ReconResult(ReconStatus.MISMATCH, now).blocks_entries
    assert ReconResult(ReconStatus.UNAVAILABLE, now).blocks_entries
    assert not ReconResult(ReconStatus.OK, now).blocks_entries
    assert not ReconResult(ReconStatus.NOT_APPLICABLE, now).blocks_entries
