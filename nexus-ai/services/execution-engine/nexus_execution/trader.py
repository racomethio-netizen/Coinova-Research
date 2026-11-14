"""Trading engine (paper or live; the broker decides). ``python -m nexus_execution.trader``

Cycle (every ``interval`` seconds):
1. Order maintenance: reconcile unknown orders, refresh open orders, cancel stale entries.
2. Exits for open positions: stop-loss / take-profit against the live bid.
3. Valuation + performance snapshot; drawdown shutdown engages the kill switch.
4. Entries: latest CLOSED-bar signals from ENABLED (validated) strategies only ->
   optional active-AI filter -> risk checks -> allocator -> sizing -> marketable limit
   order (IOC). Every decision is recorded on ``trading_signals``.

Paper mode uses real order books and is labeled ``paper`` everywhere.
"""

from __future__ import annotations

import asyncio
import signal as signals
import time
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any

import redis.asyncio as aioredis
from nexus_common import events
from nexus_common.decimals import ZERO, floor_to_step
from nexus_common.logging import configure_logging, get_logger
from nexus_common.timeutil import utcnow
from nexus_config import Settings, TradingMode, get_settings
from nexus_db import make_engine, make_session_factory, models, repo, transaction
from nexus_exchange.base import ExchangeAdapter
from nexus_exchange.errors import ExchangeError
from nexus_exchange.factory import build_from_settings
from nexus_exchange.models import OrderBook, Side, TimeInForce
from nexus_market.analytics import book_metrics
from nexus_risk.allocator import VenueState, choose_buy_venue
from nexus_risk.kill_switch import KillSwitch
from nexus_risk.limits import MarketCheck, RiskSnapshot, evaluate_portfolio, evaluate_trade
from nexus_risk.sizing import SizingInput, size_position
from nexus_strategies import ALL_STRATEGIES, Signal, Strategy
from nexus_strategies.frames import to_frame
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from nexus_execution.brokers import Broker, PaperBroker
from nexus_execution.order_manager import (
    DuplicateOrder,
    InsufficientBalance,
    OrderIntent,
    OrderManager,
    fund_paper_portfolio,
)

log = get_logger("nexus.trader")
BOOK_MAX_AGE = 15.0
HISTORY_BARS = 600
MAX_EXIT_SLIPPAGE = Decimal("0.005")


class BookCache:
    """Fresh REST order books (depth for fill simulation), cached a few seconds."""

    def __init__(self, adapters: dict[str, ExchangeAdapter], ttl: float = 5.0) -> None:
        self.adapters, self.ttl = adapters, ttl
        self._cache: dict[tuple[str, str], tuple[float, OrderBook]] = {}

    async def get(self, exchange: str, symbol: str) -> OrderBook | None:
        key = (exchange, symbol)
        hit = self._cache.get(key)
        if hit and time.monotonic() - hit[0] < self.ttl:
            return hit[1]
        try:
            book = await self.adapters[exchange].fetch_order_book(symbol, depth=50)
        except ExchangeError as exc:
            log.warning("book_unavailable", exchange=exchange, symbol=symbol, error=str(exc)[:200])
            return None
        if (utcnow() - book.timestamp).total_seconds() > BOOK_MAX_AGE:
            return None
        self._cache[key] = (time.monotonic(), book)
        return book


@dataclass
class Valuation:
    equity: Decimal
    cash: Decimal
    exposure: Decimal
    unrealized: Decimal
    realized: Decimal
    fees: Decimal
    open_positions: int
    held_assets: frozenset[str]
    complete: bool          # False if any holding could not be marked (state uncertain)


class TradingEngine:
    def __init__(self, settings: Settings, factory: sessionmaker[Session], redis: Any,
                 adapters: dict[str, ExchangeAdapter], broker: Broker,
                 books: BookCache) -> None:
        self.settings, self.factory, self.redis = settings, factory, redis
        self.adapters, self.broker, self.books = adapters, broker, books
        self.mode = broker.mode
        self.orders = OrderManager(factory, broker, redis=redis)
        self.kill = KillSwitch(redis)
        self.portfolio_id: int | None = None
        self.stop = asyncio.Event()
        self._last_snapshot: datetime | None = None
        self._seen_bars: dict[tuple[str, str], datetime] = {}

    # ------------------------------------------------------------------ bootstrap
    def bootstrap(self) -> int:
        with transaction(self.factory) as s:
            owner = s.scalar(select(models.User).where(models.User.role == "owner"))
            if owner is None:
                raise RuntimeError("no owner account: run `python -m nexus_api.cli create-owner`")
            p = s.scalar(select(models.Portfolio).where(
                models.Portfolio.user_id == owner.id, models.Portfolio.mode == self.mode,
                models.Portfolio.name == "main"))
            if p is None:
                capital = self.settings.paper_initial_capital if self.mode == "paper" else None
                p = models.Portfolio(user_id=owner.id, name="main", mode=self.mode,
                                     quote_currency=self.settings.quote_currency,
                                     initial_capital=capital)
                s.add(p)
                s.flush()
                if self.mode == "paper" and capital:
                    venues = list(self.adapters)
                    share = floor_to_step(capital / len(venues), Decimal("0.01"))
                    for code in venues:
                        fund_paper_portfolio(s, p.id, repo.exchange_id(s, code),
                                             self.settings.quote_currency, share)
                    repo.record_audit(s, actor="system:trader", action="paper_portfolio_funded",
                                      target_type="portfolio", target_id=str(p.id),
                                      details={"virtual_capital": str(capital),
                                               "per_exchange": str(share), "venues": venues})
            self.portfolio_id = p.id
            return p.id

    # ------------------------------------------------------------------ valuation
    async def valuation(self) -> Valuation:
        assert self.portfolio_id is not None
        with transaction(self.factory) as s:
            bal = s.execute(select(models.Balance, models.Exchange.code).join(models.Exchange)
                            .where(models.Balance.portfolio_id == self.portfolio_id,
                                   models.Balance.source == "internal")).all()
            positions = s.execute(select(models.Position, models.Exchange.code,
                                         models.TradingPair.symbol)
                                  .join(models.Exchange,
                                        models.Exchange.id == models.Position.exchange_id)
                                  .join(models.TradingPair,
                                        models.TradingPair.id == models.Position.pair_id)
                                  .where(models.Position.portfolio_id == self.portfolio_id)).all()
        quote = self.settings.quote_currency
        cash = sum((b.free + b.locked for b, _ in bal if b.asset == quote), ZERO)
        equity, exposure, unrealized, realized, fees = cash, ZERO, ZERO, ZERO, ZERO
        complete, held, n_open = True, set(), 0
        for pos, code, symbol in positions:
            realized += pos.realized_pnl
            fees += pos.fees_paid
            if pos.status == "closed":
                continue
            n_open += 1
            held.add(pos.base_asset)
            book = await self.books.get(code, symbol)
            if book is None or book.best_bid is None:
                complete = False
                mark = pos.avg_entry_price  # flagged incomplete: no new risk taken
            else:
                mark = book.best_bid
            exposure += mark * pos.quantity
            unrealized += mark * pos.quantity - pos.cost_basis
        equity += exposure
        return Valuation(equity, cash, exposure, unrealized, realized, fees, n_open,
                         frozenset(held), complete)

    def period_starts(self, equity: Decimal, now: datetime) -> tuple[Decimal, Decimal, Decimal]:
        day0 = now.replace(hour=0, minute=0, second=0, microsecond=0)
        week0 = day0 - timedelta(days=day0.weekday())
        with transaction(self.factory) as s:
            def first_after(t: datetime) -> Decimal | None:
                return s.scalar(select(models.PerformanceSnapshot.equity).where(
                    models.PerformanceSnapshot.portfolio_id == self.portfolio_id,
                    models.PerformanceSnapshot.captured_at >= t)
                    .order_by(models.PerformanceSnapshot.captured_at).limit(1))
            peak = s.scalar(select(func.max(models.PerformanceSnapshot.peak_equity)).where(
                models.PerformanceSnapshot.portfolio_id == self.portfolio_id))
            # All queries must run INSIDE the transaction block (a closed session would
            # silently reopen and leave a connection idle-in-transaction).
            day_start, week_start = first_after(day0), first_after(week0)
        return (day_start or equity, week_start or equity, max(peak or equity, equity))

    async def snapshot(self, v: Valuation, peak: Decimal) -> None:
        now = utcnow()
        if self._last_snapshot and now - self._last_snapshot < timedelta(minutes=5):
            return
        self._last_snapshot = now
        dd = (peak - v.equity) / peak if peak > 0 else ZERO
        with transaction(self.factory) as s:
            s.add(models.PerformanceSnapshot(
                portfolio_id=self.portfolio_id, mode=self.mode, captured_at=now, equity=v.equity,
                cash=v.cash, exposure=v.exposure, realized_pnl=v.realized,
                unrealized_pnl=v.unrealized, fees_paid=v.fees, peak_equity=peak,
                drawdown=max(ZERO, dd), open_positions=v.open_positions))

    # ------------------------------------------------------------------ exits
    async def manage_exits(self) -> None:
        with transaction(self.factory) as s:
            rows = s.execute(select(models.Position, models.Exchange.code,
                                    models.TradingPair.symbol)
                             .join(models.Exchange,
                                   models.Exchange.id == models.Position.exchange_id)
                             .join(models.TradingPair,
                                   models.TradingPair.id == models.Position.pair_id)
                             .where(models.Position.portfolio_id == self.portfolio_id,
                                    models.Position.status == "open",
                                    models.Position.quantity > 0)).all()
        for pos, code, symbol in rows:
            book = await self.books.get(code, symbol)
            if book is None or book.best_bid is None:
                continue
            bid = book.best_bid
            reason = None
            if pos.stop_loss is not None and bid <= pos.stop_loss:
                reason = "stop_loss"
            elif pos.take_profit is not None and bid >= pos.take_profit:
                reason = "take_profit"
            if reason is None:
                continue
            rules = await self.adapters[code].market_rules(symbol)
            qty = floor_to_step(pos.quantity, rules.amount_step)
            price = floor_to_step(bid * (1 - MAX_EXIT_SLIPPAGE), rules.price_tick) \
                if reason == "stop_loss" else floor_to_step(bid, rules.price_tick)
            if qty <= 0:
                continue
            try:
                await self.orders.place(OrderIntent(
                    portfolio_id=pos.portfolio_id, mode=self.mode, exchange=code, symbol=symbol,
                    side=Side.SELL, amount=qty, price=price, purpose="exit",
                    expected_price=bid, time_in_force=TimeInForce.IOC, position_id=pos.id,
                    details={"exit_reason": reason}))
            except DuplicateOrder:
                continue
            await self.notify({"type": reason, "mode": self.mode, "exchange": code,
                               "symbol": symbol, "bid": str(bid), "quantity": str(qty)})

    # ------------------------------------------------------------------ entries
    def enabled_strategies(self) -> list[tuple[int, Strategy]]:
        with transaction(self.factory) as s:
            rows = s.scalars(select(models.Strategy).where(models.Strategy.is_enabled)).all()
            out = []
            for r in rows:
                v = (r.parameters or {}).get("validation") or {}
                params = (r.parameters or {}).get("params")
                if not v.get("passed") or params is None:
                    continue  # enabled flag without a passed validation is ignored
                out.append((r.id, ALL_STRATEGIES[r.code](**params)))
            return out

    def frames(self, exchange: str, symbol: str, tfs: tuple[str, ...]) -> dict[str, Any]:
        with transaction(self.factory) as s:
            pid = repo.pair_id(s, exchange, symbol)
            return {tf: to_frame(repo.load_candles(s, pid, tf, limit=HISTORY_BARS)) for tf in tfs}

    async def evaluate_entries(self, v: Valuation, day0: Decimal, week0: Decimal,
                               peak: Decimal) -> None:
        strategies = self.enabled_strategies()
        if not strategies:
            return
        signal_exchange = self.settings.signal_exchange
        for strat_id, strat in strategies:
            for symbol in self.settings.trading_pairs:
                frames = self.frames(signal_exchange, symbol, strat.timeframes)
                if any(f.empty for f in frames.values()):
                    continue
                sig = strat.latest(frames, symbol)
                last_bar = frames[strat.base_tf].index[-1].to_pydatetime()
                key = (strat.code, symbol)
                if self._seen_bars.get(key) == last_bar:
                    continue
                self._seen_bars[key] = last_bar
                if sig is not None:
                    await self.handle_signal(strat_id, sig, v, day0, week0, peak)

    async def handle_signal(self, strat_id: int, sig: Signal, v: Valuation, day0: Decimal,
                            week0: Decimal, peak: Decimal) -> None:
        signal_id = self._record_signal(strat_id, sig)
        if signal_id is None:
            return  # already processed (idempotent)
        unknown = self._count_unknown()
        snap = RiskSnapshot(
            equity=v.equity, day_start_equity=day0, week_start_equity=week0, peak_equity=peak,
            open_positions=v.open_positions, pending_entries=self._pending_entries(),
            exposure=v.exposure, held_assets=v.held_assets,
            kill_switch=await self.kill.is_engaged(), unreconciled_orders=unknown,
            balances_reconciled=v.complete, as_of=utcnow())
        base = sig.symbol.split("/")[0]
        venues = await self.venues(sig.symbol)
        best = min((vs for vs in venues if vs.best_ask), key=lambda vs: vs.best_ask or 0,
                   default=None)
        book = await self.books.get(best.exchange, sig.symbol) if best else None
        metrics = book_metrics(book) if book else None
        check = MarketCheck(base, bool(best and best.healthy),
                            metrics.spread_bps if metrics else None,
                            (utcnow() - book.timestamp).total_seconds() if book else None)
        decision = evaluate_trade(snap, check, self.settings.risk)
        if not decision.approved:
            await self._decide(sig, signal_id, "rejected", "; ".join(decision.reasons))
            return
        # Size against the cheapest eligible venue (allocator re-checks cash/depth).
        entry_ref = Decimal(repr(sig.entry_price))
        stop = Decimal(repr(sig.stop))
        target = Decimal(repr(sig.target))
        trial = choose_buy_venue(venues, notional=Decimal(1), now=utcnow())
        if trial.exchange is None:
            await self._decide(sig, signal_id, "rejected", f"no venue: {trial.rejected}")
            return
        venue = next(x for x in venues if x.exchange == trial.exchange)
        rules = await self.adapters[venue.exchange].market_rules(sig.symbol)
        assert venue.best_ask is not None
        # Re-anchor stop/target to the venue's executable price (same distances).
        entry = venue.best_ask
        stop_px = entry - (entry_ref - stop)
        target_px = entry + (target - entry_ref)
        sizing = size_position(SizingInput(
            equity=v.equity, cash_available=venue.cash_available(), entry_price=entry,
            stop_price=stop_px, risk_per_trade=self.settings.risk.risk_per_trade,
            fee_rate=venue.taker_fee, slippage_rate=self.settings.paper_slippage_bps / 10_000,
            exposure_now=v.exposure, max_exposure=self.settings.risk.max_portfolio_exposure,
            asset_exposure_now=ZERO,
            max_asset_concentration=self.settings.risk.max_asset_concentration,
            amount_step=rules.amount_step, min_amount=rules.min_amount,
            min_notional=rules.min_notional))
        if not sizing.approved:
            await self._decide(sig, signal_id, "rejected", f"sizing: {sizing.rejected_reason}")
            return
        alloc = choose_buy_venue(venues, notional=sizing.notional, now=utcnow())
        if alloc.exchange != venue.exchange:
            await self._decide(sig, signal_id, "rejected", f"allocator: {alloc.reason} {alloc.rejected}")
            return
        price = floor_to_step(entry * (1 + self.settings.paper_slippage_bps / 10_000),
                              rules.price_tick)
        try:
            order_id = await self.orders.place(OrderIntent(
                portfolio_id=self.portfolio_id or 0, mode=self.mode, exchange=venue.exchange,
                symbol=sig.symbol, side=Side.BUY, amount=sizing.quantity, price=price,
                purpose="entry", expected_price=entry, time_in_force=TimeInForce.IOC,
                signal_id=signal_id,
                details={"strategy_id": strat_id, "strategy": sig.strategy, "stop": str(stop_px),
                         "target": str(target_px), "regime": sig.regime}))
        except (DuplicateOrder, InsufficientBalance) as exc:
            await self._decide(sig, signal_id, "rejected", str(exc))
            return
        await self._decide(sig, signal_id, "executed", None)
        with transaction(self.factory) as s:
            placed = s.get(models.Order, order_id)
            order_status = placed.status if placed else "unknown"
        await self.notify({"type": "order_submitted", "mode": self.mode, "order_id": order_id,
                           "status": order_status,
                           "strategy": sig.strategy, "symbol": sig.symbol,
                           "exchange": venue.exchange, "quantity": str(sizing.quantity),
                           "price": str(price), "stop": str(stop_px), "target": str(target_px)})

    async def venues(self, symbol: str) -> list[VenueState]:
        out = []
        for code, adapter in self.adapters.items():
            try:
                rules = await adapter.market_rules(symbol)
            except ExchangeError:
                out.append(VenueState(code, False, False, ZERO, ZERO, None, None, None,
                                      Decimal("0.001"), None, False))
                continue
            book = await self.books.get(code, symbol)
            metrics = book_metrics(book) if book else None
            healthy = await self._healthy(code)
            with transaction(self.factory) as s:
                row = s.scalar(select(models.Balance).join(models.Exchange).where(
                    models.Balance.portfolio_id == self.portfolio_id,
                    models.Exchange.code == code, models.Balance.source == "internal",
                    models.Balance.asset == self.settings.quote_currency))
            fee = rules.taker_fee if rules.taker_fee is not None else Decimal("0.001")
            fee = max(fee, self.settings.paper_fee_bps / 10_000)  # never below configured
            out.append(VenueState(
                exchange=code, lists_pair=True, healthy=healthy,
                quote_free=row.free if row else ZERO, quote_reserved=ZERO,
                best_ask=book.best_ask if book else None, best_bid=book.best_bid if book else None,
                ask_depth_quote=metrics.ask_depth_quote if metrics else None, taker_fee=fee,
                balances_synced_at=row.synced_at if row else None, balance_mismatch=False))
        return out

    async def _healthy(self, code: str) -> bool:
        import json
        raw = await self.redis.get(events.health_key(code))
        return bool(raw and json.loads(raw).get("ok"))

    # ------------------------------------------------------------------ records
    def _record_signal(self, strat_id: int, sig: Signal) -> int | None:
        try:
            with transaction(self.factory) as s:
                row = models.TradingSignal(
                    mode=self.mode, strategy_id=strat_id,
                    pair_id=repo.pair_id(s, self.settings.signal_exchange, sig.symbol),
                    side="buy", timeframe=sig.timeframe, bar_time=sig.bar_time,
                    entry_price=Decimal(repr(sig.entry_price)), stop_loss=Decimal(repr(sig.stop)),
                    take_profit=Decimal(repr(sig.target)),
                    confidence=Decimal(repr(round(sig.confidence, 6))),
                    details={"regime": sig.regime, "params": sig.details.get("params")})
                s.add(row)
                s.flush()
                return row.id
        except IntegrityError:
            return None

    async def _decide(self, sig: Signal, signal_id: int, status: str, reason: str | None) -> None:
        self._set_signal(signal_id, status, reason)
        await self.notify({"type": "signal", "mode": self.mode, "strategy": sig.strategy,
                           "symbol": sig.symbol, "entry": sig.entry_price, "stop": sig.stop,
                           "target": sig.target, "status": status, "reason": reason})

    def _set_signal(self, signal_id: int, status: str, reason: str | None) -> None:
        with transaction(self.factory) as s:
            row = s.get(models.TradingSignal, signal_id)
            assert row is not None
            row.status, row.rejection_reason = status, reason
        log.info("signal_decision", signal_id=signal_id, status=status, reason=reason)

    def _count_unknown(self) -> int:
        with transaction(self.factory) as s:
            return int(s.scalar(select(func.count()).select_from(models.Order).where(
                models.Order.mode == self.mode, models.Order.status == "unknown")) or 0)

    def _pending_entries(self) -> int:
        with transaction(self.factory) as s:
            return int(s.scalar(select(func.count()).select_from(models.Order).where(
                models.Order.portfolio_id == self.portfolio_id, models.Order.purpose == "entry",
                models.Order.status.in_(("pending_submit", "submitted", "open",
                                         "partially_filled", "unknown")))) or 0)

    async def notify(self, payload: dict[str, Any]) -> None:
        await self.redis.xadd(events.NOTIFY_STREAM, events.encode(payload),
                              maxlen=events.STREAM_MAXLEN, approximate=True)

    # ------------------------------------------------------------------ cycle
    async def cycle(self) -> None:
        await self.orders.reconcile_unknown()
        await self.orders.refresh_open()
        await self.orders.cancel_stale(timedelta(minutes=5))
        await self.manage_exits()
        v = await self.valuation()
        day0, week0, peak = self.period_starts(v.equity, utcnow())
        await self.snapshot(v, peak)
        port = evaluate_portfolio(RiskSnapshot(
            equity=v.equity, day_start_equity=day0, week_start_equity=week0, peak_equity=peak,
            open_positions=v.open_positions, pending_entries=0, exposure=v.exposure,
            held_assets=v.held_assets, kill_switch=False, unreconciled_orders=0,
            balances_reconciled=True, as_of=utcnow()), self.settings.risk)
        for kind, severity in port.events:
            await self._risk_event(kind, severity, v)
        if port.shutdown and not await self.kill.is_engaged():
            await self.kill.engage(f"max drawdown reached ({self.mode})", actor="system:risk")
        await self.evaluate_entries(v, day0, week0, peak)
        await self.redis.set(events.heartbeat_key(f"trader:{self.mode}"), utcnow().isoformat(),
                             ex=300)

    async def _risk_event(self, kind: str, severity: str, v: Valuation) -> None:
        key = f"nexus:risk_event:{self.mode}:{kind}:{utcnow().date()}"
        if not await self.redis.set(key, "1", nx=True, ex=86_400):
            return  # once per day per kind
        with transaction(self.factory) as s:
            repo.record_risk_event(s, mode=self.mode, portfolio_id=self.portfolio_id,
                                   event_type=kind, severity=severity,
                                   details={"equity": str(v.equity)})
        await self.notify({"type": "risk_warning", "mode": self.mode, "event": kind,
                           "severity": severity, "equity": str(v.equity)})

    async def run(self, interval: float) -> None:
        await asyncio.to_thread(self.bootstrap)
        log.info("trader_started", mode=self.mode, portfolio_id=self.portfolio_id)
        while not self.stop.is_set():
            try:
                await self.cycle()
            except Exception as exc:  # never die silently; log and keep protecting capital
                log.error("cycle_failed", error=f"{type(exc).__name__}: {exc}"[:500])
            try:
                await asyncio.wait_for(self.stop.wait(), timeout=interval)
            except TimeoutError:
                pass


async def main() -> None:
    settings = get_settings()
    configure_logging(settings.log_level, json=settings.log_json)
    engine = make_engine(settings.database_url.get_secret_value())
    factory = make_session_factory(engine)
    redis = aioredis.from_url(settings.redis_url.get_secret_value())
    if settings.trading_mode is TradingMode.LIVE:
        from nexus_execution.live_gate import build_live_broker, credentialed_adapters
        with transaction(factory) as s:
            adapters = credentialed_adapters(settings, s)
    else:
        adapters = {e.value: build_from_settings(e, settings) for e in settings.enabled_exchanges}
    for a in adapters.values():
        await a.load_markets()
    books = BookCache(adapters)
    if settings.trading_mode is TradingMode.LIVE:
        with transaction(factory) as s:
            broker: Broker = await build_live_broker(settings, s, redis, adapters)
    else:
        broker = PaperBroker(books.get, taker_fee=settings.paper_fee_bps / 10_000,
                             maker_fee=settings.paper_fee_bps / 10_000,
                             extra_slippage=settings.paper_slippage_bps / 10_000,
                             quote_asset=settings.quote_currency)
    trader = TradingEngine(settings, factory, redis, adapters, broker, books)
    loop = asyncio.get_running_loop()
    for sig in (signals.SIGINT, signals.SIGTERM):
        loop.add_signal_handler(sig, trader.stop.set)
    try:
        await trader.run(settings.trader_interval_seconds)
    finally:
        for a in adapters.values():
            await a.close()
        await redis.aclose()
        engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())

