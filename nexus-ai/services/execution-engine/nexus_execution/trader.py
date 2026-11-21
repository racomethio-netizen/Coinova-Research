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

from nexus_common import events
from nexus_common.decimals import ZERO, floor_to_step
from nexus_common.logging import configure_logging, get_logger
from nexus_common.redisutil import connect as connect_redis
from nexus_common.timeutil import utcnow
from nexus_config import Settings, TradingMode, get_settings
from nexus_db import make_engine, make_session_factory, models, repo, transaction
from nexus_exchange.base import ExchangeAdapter
from nexus_exchange.errors import ExchangeError
from nexus_exchange.factory import build_from_settings
from nexus_exchange.models import OrderBook, Side, TimeInForce
from nexus_market.analytics import book_metrics
from nexus_risk.allocator import VenueState, choose_buy_venue
from nexus_risk.exits import ExitState, partial_take_profit, trailing_stop
from nexus_risk.kill_switch import KillSwitch
from nexus_risk.limits import MarketCheck, RiskSnapshot, evaluate_portfolio, evaluate_trade
from nexus_risk.sizing import SizingInput, size_position
from nexus_risk.streak import DEFAULT_POLICY, risk_multiplier
from nexus_strategies import ALL_STRATEGIES, Signal, Strategy
from nexus_strategies.frames import to_frame
from nexus_strategies.indicators import atr as atr_series
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from nexus_execution import protection
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
# Exit management: sell half at +1R once, then trail the rest at 2 x ATR(14, 1h).
PARTIAL_R = Decimal("1")
PARTIAL_FRACTION = Decimal("0.5")
TRAIL_ATR = Decimal("2")
TRAIL_TIMEFRAME = "1h"


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


def close_all_key(mode: str) -> str:
    """Redis flag set by the owner (API, audit-logged) to close all positions of a mode."""
    return f"nexus:kill:close_all:{mode}"


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
            if await self._advance_exits(pos, code, symbol, bid):
                continue  # a partial exit was placed this cycle
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

    def _atr(self, symbol: str) -> Decimal | None:
        frame = self.frames(self.settings.signal_exchange, symbol, (TRAIL_TIMEFRAME,))[TRAIL_TIMEFRAME]
        if frame is None or len(frame) < 30:
            return None
        value = float(atr_series(frame).iloc[-1])
        return None if value != value else Decimal(repr(value))  # NaN -> None

    async def _advance_exits(self, pos: models.Position, code: str, symbol: str,
                             bid: Decimal) -> bool:
        """Partial take-profit at +1R (once), then an ATR trailing stop. The stop only
        ever moves up. Returns True when a partial exit is placed or still working."""
        if pos.stop_loss is None or pos.avg_entry_price <= 0:
            return False
        with transaction(self.factory) as s:
            taken = protection.partial_taken(s, pos.id)
            in_flight = protection.partial_in_flight(s, pos.id)
            first_stop = protection.initial_stop(s, pos)
        if in_flight:
            return True
        rules = await self.adapters[code].market_rules(symbol)
        if not taken:
            if first_stop is None or first_stop >= pos.avg_entry_price:
                return False
            state = ExitState(pos.avg_entry_price, first_stop, bid, pos.quantity)
            qty, _ = partial_take_profit(state, last=bid, amount_step=rules.amount_step,
                                         r_multiple=PARTIAL_R, fraction=PARTIAL_FRACTION)
            too_small = rules.min_notional is not None and qty * bid < rules.min_notional
            too_small = too_small or (rules.min_amount is not None and qty < rules.min_amount)
            if qty <= 0 or too_small:
                return False
            try:
                await self.orders.place(OrderIntent(
                    portfolio_id=pos.portfolio_id, mode=self.mode, exchange=code,
                    symbol=symbol, side=Side.SELL, amount=qty,
                    price=floor_to_step(bid, rules.price_tick), purpose="exit",
                    expected_price=bid, time_in_force=TimeInForce.IOC, position_id=pos.id,
                    details={"exit_reason": protection.PARTIAL_REASON}))
            except DuplicateOrder:
                return True
            self._set_stop(pos, max(pos.stop_loss, pos.avg_entry_price))
            await self.notify({"type": "take_profit", "mode": self.mode, "exchange": code,
                               "symbol": symbol, "bid": str(bid), "quantity": str(qty),
                               "partial": True})
            return True
        atr = self._atr(symbol)
        if atr is None:
            return False
        key = f"nexus:exit:highest:{pos.id}"
        raw = await self.redis.get(key)
        seen = Decimal(raw.decode() if isinstance(raw, bytes) else raw) if raw else bid
        highest = max(seen, bid)
        await self.redis.set(key, str(highest), ex=30 * 86400)
        trailed = trailing_stop(ExitState(pos.avg_entry_price, pos.stop_loss, highest,
                                          pos.quantity, True), last=bid, atr=atr,
                                trail_atr=TRAIL_ATR)
        new_stop = floor_to_step(trailed.stop, rules.price_tick)
        if new_stop > pos.stop_loss:
            self._set_stop(pos, new_stop)
        return False

    def _set_stop(self, pos: models.Position, stop: Decimal) -> None:
        """Raise (never lower) the stored stop for a long position."""
        with transaction(self.factory) as s:
            row = s.get(models.Position, pos.id, with_for_update=True)
            if row is not None and row.status == "open" and (
                    row.stop_loss is None or stop > row.stop_loss):
                row.stop_loss = stop
        if pos.stop_loss is None or stop > pos.stop_loss:
            pos.stop_loss = stop

    async def _streak(self) -> tuple[Decimal, str | None]:
        """Risk multiplier from the losing streak since the owner's last reset."""
        since = await protection.streak_reset_at(self.redis)
        with transaction(self.factory) as s:
            state = protection.streak_state(s, self.portfolio_id or 0, since)
        mult = risk_multiplier(state)
        if mult == 0:
            return mult, (f"loss streak: entries paused after {state.losses} consecutive losses "
                          "(owner reset required)")
        if mult < 1:
            return mult, (f"loss streak: {state.losses} consecutive losses, risk reduced to "
                          f"{mult * 100:.0f}% (pause after {DEFAULT_POLICY.pause_after})")
        return mult, None

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
        block = (await self._news_block() or await self._x_block(sig.symbol)
                 or self._regime_block(strat_id, sig) or self._cooldown_block(sig.symbol))
        if block is not None:
            await self._decide(sig, signal_id, "rejected", block)
            return
        unknown = self._count_unknown()
        snap = RiskSnapshot(
            equity=v.equity, day_start_equity=day0, week_start_equity=week0, peak_equity=peak,
            open_positions=v.open_positions, pending_entries=self._pending_entries(),
            exposure=v.exposure, held_assets=v.held_assets,
            kill_switch=await self._kill_active(), unreconciled_orders=unknown,
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
        streak_mult, streak_note = await self._streak()
        if streak_mult == 0:
            await self._decide(sig, signal_id, "rejected", streak_note)
            return
        proceed, explanation = await self._decision_gate(sig, v, day0, week0, book, metrics, best)
        if not proceed:
            await self._decide(sig, signal_id, "rejected", explanation)
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
            stop_price=stop_px, risk_per_trade=self.settings.risk.risk_per_trade * streak_mult,
            fee_rate=venue.taker_fee, slippage_rate=self.settings.paper_slippage_bps / 10_000,
            exposure_now=v.exposure, max_exposure=self.settings.risk.max_portfolio_exposure,
            asset_exposure_now=ZERO,
            max_asset_concentration=self.settings.risk.max_asset_concentration,
            amount_step=rules.amount_step, min_amount=rules.min_amount,
            min_notional=rules.min_notional))
        if not sizing.approved:
            await self._decide(sig, signal_id, "rejected", f"sizing: {sizing.rejected_reason}")
            return
        corr = self._correlation_block(sig.symbol, sizing.notional, v.equity)
        if corr is not None:
            await self._decide(sig, signal_id, "rejected", corr)
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
                         "target": str(target_px), "regime": sig.regime,
                         "risk_multiplier": str(streak_mult), "streak_note": streak_note}))
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

    # ------------------------------------------------------------------ Phase 2 gates
    def _regime_block(self, strat_id: int, sig: Signal) -> str | None:
        """Trade only in market regimes where this strategy's own out-of-sample edge
        passed the validation gates (recorded by validate-strategies)."""
        with transaction(self.factory) as s:
            row = s.get(models.Strategy, strat_id)
            val = ((row.parameters if row else None) or {}).get("validation") or {}
        allowed = val.get("allowed_regimes")
        if allowed is None:
            return "no validated regime record for this strategy (re-run validation)"
        if sig.regime is None:
            return "market regime unknown"
        if sig.regime not in allowed:
            return (f"regime {sig.regime} is outside the strategy's validated regimes "
                    f"({', '.join(allowed) or 'none'})")
        return None

    def _cooldown_block(self, symbol: str) -> str | None:
        """No re-entry into a pair for a while after a LOSING exit of any kind (stop-loss,
        trailing stop, manual): no revenge trading."""
        minutes = self.settings.risk.cooldown_after_stop_minutes
        since = utcnow() - timedelta(minutes=minutes)
        with transaction(self.factory) as s:
            closed = s.scalar(
                select(models.Position.closed_at)
                .join(models.TradingPair, models.TradingPair.id == models.Position.pair_id)
                .where(models.Position.portfolio_id == self.portfolio_id,
                       models.TradingPair.symbol == symbol,
                       models.Position.status == "closed",
                       models.Position.realized_pnl < 0,
                       models.Position.closed_at >= since)
                .order_by(models.Position.closed_at.desc()).limit(1))
        if closed is None:
            return None
        until = closed + timedelta(minutes=minutes)
        return (f"cooldown: losing exit on {symbol} at {closed:%H:%M} UTC; "
                f"no re-entry until {until:%H:%M} UTC")

    def _correlation_block(self, symbol: str, notional: Decimal, equity: Decimal) -> str | None:
        """Assets that move together count as one exposure. Correlation of 1h returns over
        the last 30 days; when it cannot be measured it is assumed to be 1 (conservative)."""
        from nexus_risk.correlation import correlated_exposure_ok

        base, quote = symbol.split("/")
        with transaction(self.factory) as s:
            rows = s.execute(
                select(models.Position.base_asset, models.Position.quantity,
                       models.Position.avg_entry_price)
                .where(models.Position.portfolio_id == self.portfolio_id,
                       models.Position.status == "open", models.Position.quantity > 0)).all()
        held: dict[str, Decimal] = {}
        for asset, qty, px in rows:
            if asset != base:                  # same asset: concentration limit applies
                held[asset] = held.get(asset, ZERO) + qty * px
        if not held:
            return None
        returns = {}
        for asset in [base, *held]:
            try:
                close = self.frames(self.settings.signal_exchange, f"{asset}/{quote}",
                                    ("1h",))["1h"]["close"].astype(float)
                returns[asset] = close.tail(721).pct_change().dropna()
            except Exception:
                returns[asset] = None
        corr: dict[tuple[str, str], float] = {}
        for asset in held:
            a, b = returns.get(base), returns.get(asset)
            rho = None
            if a is not None and b is not None:
                joined = a.to_frame("a").join(b.to_frame("b"), how="inner")
                if len(joined) >= 200:
                    rho = float(joined["a"].corr(joined["b"]))
            corr[(base, asset)] = 1.0 if rho is None or rho != rho else rho
        ok, why = correlated_exposure_ok(
            new_asset=base, new_notional=notional, equity=equity, held=held,
            correlations=corr, threshold=self.settings.risk.correlation_threshold,
            max_fraction=self.settings.risk.max_correlated_exposure)
        return None if ok else why

    async def _news_score(self) -> float | None:
        import json as _json

        from nexus_news.service import KEY as NEWS_KEY

        raw = await self.redis.get(NEWS_KEY)
        if raw is None:
            return None
        score = _json.loads(raw).get("score")
        return None if score is None else float(score)

    async def _decision_gate(self, sig: Signal, v: Valuation, day0: Decimal, week0: Decimal,
                             book: OrderBook | None, metrics: Any, best: VenueState | None
                             ) -> tuple[bool, str]:
        """Score the setup (0-100), apply the NO TRADE gates and the 1:2 minimum.

        Built from the same candles the strategy used plus the live order book. Returns
        (proceed, explanation). The explanation is stored with the signal either way.
        """
        import math

        from nexus_analysis import liquidity_sweeps, market_structure, rolling_correlation, support_resistance
        from nexus_decision import MarketSnapshot, NoTradeInputs, decide
        from nexus_risk.limits import loss_fraction
        from nexus_strategies.indicators import atr as atr_fn
        from nexus_strategies.indicators import rolling_percentile, volume_z

        frames = self.frames(self.settings.signal_exchange, sig.symbol, (sig.timeframe, "1h", "4h"))
        base, htf = frames[sig.timeframe], frames["1h"]
        if base.empty or htf.empty or len(base) < 200:
            return False, "NO TRADE: insufficient candle history for analysis"
        st_base = market_structure(base).iloc[-1]
        st_htf = market_structure(htf).iloc[-1]
        sr = support_resistance(base).iloc[-1]
        sw = liquidity_sweeps(base).iloc[-1]
        atr_s = atr_fn(base, 14)
        a = float(atr_s.iloc[-1])
        entry, stop, target = float(sig.entry_price), float(sig.stop), float(sig.target)
        sup = float(sr["support"]) if not math.isnan(float(sr["support"])) else None
        at_level = sup is not None and abs(entry - sup) <= 0.5 * a
        vz = volume_z(base["volume"], 50).iloc[-1]
        btc_corr = btc_trend = None
        if sig.symbol != "BTC/USDT":
            btc = self.frames(self.settings.signal_exchange, "BTC/USDT", (sig.timeframe, "1h"))
            if not btc[sig.timeframe].empty and not btc["1h"].empty:
                corr = rolling_correlation(base["close"], btc[sig.timeframe]["close"], 96)
                btc_corr = None if math.isnan(float(corr.iloc[-1])) else float(corr.iloc[-1])
                btc_trend = market_structure(btc["1h"]).iloc[-1]["trend"]
        atr_rank = rolling_percentile(atr_s, 2000, min_periods=200).iloc[-1]
        snap = MarketSnapshot(
            side="long", entry=Decimal(repr(entry)), stop=Decimal(repr(stop)),
            target=Decimal(repr(target)), higher_trend=str(st_htf["trend"]),
            structure_event=(None if st_base["event"] is None or (
                isinstance(st_base["event"], float) and math.isnan(st_base["event"]))
                else str(st_base["event"])),
            at_level=at_level, sweep=bool(sw["sweep_low"]),
            volume_z=None if math.isnan(float(vz)) else float(vz),
            book_imbalance=metrics.imbalance if metrics else None,
            btc_corr=btc_corr, btc_trend=None if btc_trend is None else str(btc_trend))
        stop_frac = (entry - stop) / entry if entry > 0 else 0.0
        size_frac = min(float(self.settings.risk.risk_per_trade) / stop_frac, 0.25) if stop_frac > 0 else 0.0
        news = await self._news_score()
        inputs = NoTradeInputs(
            now=utcnow(), data_time=book.timestamp if book else None,
            exchange_healthy=bool(best and best.healthy),
            spread_bps=metrics.spread_bps if metrics else None,
            depth_quote=metrics.ask_depth_quote if metrics else None,
            order_quote=v.equity * Decimal(repr(size_frac)),
            atr_percentile=None if math.isnan(float(atr_rank)) else float(atr_rank),
            news_risk=news,
            daily_loss_limit_reached=loss_fraction(day0, v.equity) >= self.settings.risk.daily_loss_limit,
            weekly_loss_limit_reached=loss_fraction(week0, v.equity) >= self.settings.risk.weekly_loss_limit,
            kill_switch=await self._kill_active(), reward_risk=None,
            confirmations_conflict=False, regime=sig.regime, setup_present=True)
        d = decide(snap, inputs, min_score=60)
        return d.action == "ENTER", d.explanation

    async def _x_block(self, symbol: str) -> str | None:
        """Pause new entries on CONFIRMED high-impact negative X news for this coin (or the
        whole market). X data can only block; missing X data never blocks (it is optional)."""
        import json as _json

        from nexus_social.service import RISK as X_RISK

        raw = await self.redis.get(X_RISK)
        if raw is None:
            return None
        blocks = _json.loads(raw).get("blocks") or {}
        base = symbol.split("/")[0].upper()
        reason = blocks.get(base) or blocks.get("MARKET")
        return str(reason) if reason else None

    async def _news_block(self) -> str | None:
        """Block entries on unknown or high-impact news. Missing data is unknown, not calm."""
        import json as _json

        from nexus_news.service import KEY as NEWS_KEY

        raw = await self.redis.get(NEWS_KEY)
        if raw is None:
            return "news risk unknown (monitor has not reported)"
        data = _json.loads(raw)
        if data.get("unknown_sources"):
            return f"news risk unknown (no data from {', '.join(data['unknown_sources'])})"
        score = data.get("score")
        if score is None:
            return "news risk unknown"
        if score > self.settings.news_block_threshold:
            return f"high-impact news window (risk {score:.2f} > {self.settings.news_block_threshold})"
        return None

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
    # ------------------------------------------------------------------ kill switch
    async def _sync_kill_switch(self) -> None:
        """Restore the kill switch from the durable audit record when Redis lost it. If
        that record cannot be read, entries are blocked for this cycle (fail closed)."""
        try:
            with transaction(self.factory) as s:
                durable = repo.kill_switch_db_state(s)
            self._kill_db_unknown = False
        except Exception as exc:
            log.error("kill_switch_db_unreadable", error=type(exc).__name__)
            self._kill_db_unknown = True
            return
        if await self.kill.restore(durable):
            await self.notify({"type": "emergency_shutdown", "reason":
                               f"restored after state loss: {durable and durable.get('reason')}",
                               "actor": "system:kill_switch"})

    async def _kill_active(self) -> bool:
        return getattr(self, "_kill_db_unknown", False) or await self.kill.is_engaged()

    async def kill_actions(self) -> None:
        """While engaged: cancel every resting ENTRY order of this portfolio, and close open
        positions if the owner requested it (one IOC sell per position per cycle)."""
        with transaction(self.factory) as s:
            ids = list(s.scalars(select(models.Order.id).where(
                models.Order.portfolio_id == self.portfolio_id,
                models.Order.purpose == "entry",
                models.Order.status.in_(("submitted", "open", "partially_filled")))))
        for oid in ids:
            try:
                await self.orders.cancel(oid, "kill switch engaged")
            except Exception as exc:  # reconcile next cycle; never resubmit
                log.error("kill_cancel_failed", order_id=oid, error=type(exc).__name__)
        if await self.redis.get(close_all_key(self.mode)):
            open_left = await self.close_all_positions()
            if open_left == 0:
                await self.redis.delete(close_all_key(self.mode))
                with transaction(self.factory) as s:
                    repo.record_audit(s, actor="system:kill_switch",
                                      action="kill_switch_positions_closed",
                                      details={"mode": self.mode})

    async def close_all_positions(self) -> int:
        """Place a reduce-only style IOC sell for each open position (bounded slippage).
        Returns how many positions were still open at the start of this pass."""
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
                log.warning("kill_close_no_book", symbol=symbol)
                continue
            rules = await self.adapters[code].market_rules(symbol)
            qty = floor_to_step(pos.quantity, rules.amount_step)
            if qty <= 0:
                continue
            price = floor_to_step(book.best_bid * (1 - MAX_EXIT_SLIPPAGE), rules.price_tick)
            try:
                await self.orders.place(OrderIntent(
                    portfolio_id=pos.portfolio_id, mode=self.mode, exchange=code,
                    symbol=symbol, side=Side.SELL, amount=qty, price=price, purpose="exit",
                    expected_price=book.best_bid, time_in_force=TimeInForce.IOC,
                    position_id=pos.id, details={"exit_reason": "kill_switch_close"}))
            except DuplicateOrder:
                continue
        return len(rows)

    async def cycle(self) -> None:
        await self._sync_kill_switch()
        await self.orders.reconcile_unknown()
        await self.orders.refresh_open()
        await self.orders.cancel_stale(timedelta(minutes=5))
        if await self._kill_active():
            await self.kill_actions()
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
            reason = f"max drawdown reached ({self.mode})"
            await self.kill.engage(reason, actor="system:risk")
            with transaction(self.factory) as s:
                repo.record_audit(s, actor="system:risk", action="kill_switch_engaged",
                                  details={"reason": reason})
        from nexus_common.botstate import BotState, get_state

        state = await get_state(self.redis)
        if state is BotState.RUNNING:
            await self.evaluate_entries(v, day0, week0, peak)
        else:
            log.info("entries_paused", state=state.value)
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
        while not self.stop.is_set():
            try:
                await asyncio.to_thread(self.bootstrap)
                break
            except RuntimeError as exc:  # e.g. fresh deployment before the owner exists
                log.warning("trader_waiting", reason=str(exc))
                await self.redis.set(events.heartbeat_key(f"trader:{self.mode}"),
                                     utcnow().isoformat(), ex=300)
                try:
                    await asyncio.wait_for(self.stop.wait(), timeout=60)
                except TimeoutError:
                    pass
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


async def serve(settings: Settings, stop: asyncio.Event) -> None:
    """Run until ``stop`` is set (used by main() and by the API's inline worker mode)."""
    engine = make_engine(settings.database_url.get_secret_value())
    factory = make_session_factory(engine)
    redis = connect_redis(settings.redis_url.get_secret_value())
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
    async def relay() -> None:
        await stop.wait()
        trader.stop.set()

    relay_task = asyncio.create_task(relay())
    try:
        await trader.run(settings.trader_interval_seconds)
    finally:
        relay_task.cancel()
        for a in adapters.values():
            await a.close()
        await redis.aclose()
        engine.dispose()


async def main() -> None:
    settings = get_settings()
    configure_logging(settings.log_level, json=settings.log_json)
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signals.SIGINT, signals.SIGTERM):
        loop.add_signal_handler(sig, stop.set)
    await serve(settings, stop)


if __name__ == "__main__":
    asyncio.run(main())

