"""Paper fill simulation against REAL order books (no orders are sent anywhere)."""

from __future__ import annotations

from decimal import Decimal as D

import pytest
from nexus_config import ExchangeCode, Settings
from nexus_exchange.factory import build_from_settings
from nexus_exchange.models import OrderRequest, OrderStatus, Side, TimeInForce, new_client_order_id
from nexus_execution.brokers import PaperBroker

pytestmark = pytest.mark.live_exchange


@pytest.mark.parametrize("code", [ExchangeCode.BINANCE, ExchangeCode.KRAKEN])
async def test_paper_ioc_buy_fills_at_real_book_prices(code: ExchangeCode) -> None:
    adapter = build_from_settings(code, Settings())
    try:
        await adapter.load_markets()
        book = await adapter.fetch_order_book("BTC/USDT", depth=20)
        rules = await adapter.market_rules("BTC/USDT")

        async def books(_: str, __: str):  # type: ignore[no-untyped-def]
            return book

        broker = PaperBroker(books, taker_fee=D("0.001"), maker_fee=D("0.001"),
                             extra_slippage=D("0.0005"))
        assert book.best_ask is not None and book.best_bid is not None
        qty = max(rules.min_amount or rules.amount_step, rules.amount_step * 10)
        limit = (book.best_ask * D("1.001")).quantize(rules.price_tick)
        req = OrderRequest(new_client_order_id(), "BTC/USDT", Side.BUY, qty, limit,
                           time_in_force=TimeInForce.IOC)
        upd = await broker.submit(req, exchange=code.value)
        assert upd.status is OrderStatus.FILLED, upd
        filled = sum(f.amount for f in upd.fills)
        assert filled == qty
        for f in upd.fills:
            assert book.best_ask <= f.price <= limit       # real prices, never past the limit
            assert f.fee == f.price * f.amount * D("0.001")
            assert f.trade_id.startswith("paper-")
        # A limit far below the market never fills (no invented liquidity).
        far = OrderRequest(new_client_order_id(), "BTC/USDT", Side.BUY, qty,
                           (book.best_bid * D("0.9")).quantize(rules.price_tick),
                           time_in_force=TimeInForce.IOC)
        none = await broker.submit(far, exchange=code.value)
        assert none.fills == () and none.status is OrderStatus.CANCELED
    finally:
        await adapter.close()
