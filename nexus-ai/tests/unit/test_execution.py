from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal as D

import pytest
from nexus_exchange.models import OrderBook, OrderRequest, Side, TimeInForce, new_client_order_id
from nexus_execution.accounting import FillEvent, Ledger, PositionState, apply_fill
from nexus_execution.paper import simulate_resting, simulate_submit

B, Q = "BTC", "USDT"


class TestAccounting:
    def test_buy_then_partial_and_full_sell(self) -> None:
        p = apply_fill(PositionState(), FillEvent("buy", D(100), D(2), D("0.2"), Q), base=B, quote=Q)
        assert p.quantity == D(2) and p.cost_basis == D("200.2")
        assert p.avg_entry_price == D("100.1")
        p = apply_fill(p, FillEvent("buy", D(110), D(1), D("0.11"), Q), base=B, quote=Q)
        assert p.cost_basis == D("310.31") and p.quantity == D(3)
        p = apply_fill(p, FillEvent("sell", D(120), D(1), D("0.12"), Q), base=B, quote=Q)
        # proceeds 119.88 - released 310.31/3
        assert p.realized_pnl == D("119.88") - D("310.31") / 3
        p = apply_fill(p, FillEvent("sell", D(90), D(2), D("0.18"), Q), base=B, quote=Q)
        assert p.quantity == 0 and p.cost_basis == 0
        total_in = D("200.2") + D("110.11")
        total_out = D("119.88") + D("179.82")
        assert p.realized_pnl == total_out - total_in  # exact, no rounding drift
        assert p.fees_paid_quote == D("0.61")

    def test_fee_in_base_reduces_quantity(self) -> None:
        p = apply_fill(PositionState(), FillEvent("buy", D(100), D(1), D("0.001"), B), base=B, quote=Q)
        assert p.quantity == D("0.999") and p.cost_basis == D(100)
        assert p.fees_paid_quote == D("0.100")

    def test_third_asset_fee_not_converted(self) -> None:
        p = apply_fill(PositionState(), FillEvent("buy", D(100), D(1), D("0.01"), "BNB"), base=B,
                       quote=Q)
        assert p.cost_basis == D(100) and p.fees_paid_quote == 0

    def test_no_short_selling(self) -> None:
        with pytest.raises(ValueError, match="no shorting"):
            apply_fill(PositionState(D(1), D(100)), FillEvent("sell", D(1), D(2), D(0), Q), base=B,
                       quote=Q)

    def test_ledger(self) -> None:
        led = Ledger({Q: D(1000), B: D(0)}, {Q: D(0), B: D(0)}).lock(Q, D("100.3"))
        assert led.free[Q] == D("899.7") and led.locked[Q] == D("100.3")
        led = led.settle_fill(FillEvent("buy", D(100), D(1), D("0.1"), Q), base=B, quote=Q,
                              from_locked=True)
        assert led.locked[Q] == D("0.2") and led.free[B] == D(1)
        led = led.unlock(Q, D("0.2"))
        assert led.free[Q] == D("899.9") and led.locked[Q] == 0
        with pytest.raises(ValueError):
            led.lock(Q, D(10_000))


def book(bids: list[tuple[str, str]], asks: list[tuple[str, str]]) -> OrderBook:
    return OrderBook("BTC/USDT", tuple((D(p), D(a)) for p, a in bids),
                     tuple((D(p), D(a)) for p, a in asks), datetime(2026, 1, 1, tzinfo=UTC))


def req(side: Side, amount: str, price: str, tif: TimeInForce = TimeInForce.GTC) -> OrderRequest:
    return OrderRequest(new_client_order_id(), "BTC/USDT", side, D(amount), D(price),
                        time_in_force=tif)


class TestPaperSim:
    BOOK = book([("99", "1"), ("98", "5")], [("100", "1"), ("101", "2"), ("105", "10")])

    def test_walks_book_up_to_limit(self) -> None:
        r = simulate_submit(req(Side.BUY, "2.5", "101"), self.BOOK, taker_fee=D("0.001"),
                            extra_slippage=D(0))
        assert [(f.price, f.amount) for f in r.fills] == [(D(100), D(1)), (D(101), D("1.5"))]
        assert r.remaining == 0 and not r.resting
        assert r.fills[0].fee == D("0.1") and all(f.trade_id.startswith("paper-") for f in r.fills)

    def test_extra_slippage_capped_at_limit(self) -> None:
        r = simulate_submit(req(Side.BUY, "1", "100"), self.BOOK, taker_fee=D(0),
                            extra_slippage=D("0.01"))
        assert r.fills[0].price == D(100)  # never worse than the limit

    def test_ioc_remainder_cancelled_gtc_rests(self) -> None:
        ioc = simulate_submit(req(Side.BUY, "5", "101", TimeInForce.IOC), self.BOOK,
                              taker_fee=D(0), extra_slippage=D(0))
        assert ioc.remaining == D(2) and not ioc.resting
        gtc = simulate_submit(req(Side.BUY, "5", "101"), self.BOOK, taker_fee=D(0),
                              extra_slippage=D(0))
        assert gtc.remaining == D(2) and gtc.resting

    def test_sell_side(self) -> None:
        r = simulate_submit(req(Side.SELL, "3", "98"), self.BOOK, taker_fee=D(0),
                            extra_slippage=D(0))
        assert [(f.price, f.amount) for f in r.fills] == [(D(99), D(1)), (D(98), D(2))]

    def test_resting_fills_only_when_crossed(self) -> None:
        none = simulate_resting(Side.BUY, D("99.5"), D(1), self.BOOK, maker_fee=D("0.0005"))
        assert none.fills == () and none.resting
        crossed = book([("98", "1")], [("99.4", "0.4"), ("99.6", "5")])
        r = simulate_resting(Side.BUY, D("99.5"), D(1), crossed, maker_fee=D("0.0005"))
        assert r.fills[0].price == D("99.5") and r.fills[0].amount == D("0.4")
        assert r.fills[0].is_maker and r.remaining == D("0.6")
