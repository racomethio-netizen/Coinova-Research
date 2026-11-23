"""Spot strategy engine PAPER runner (docs/SPOT_ENGINE_PLAN.md). Paper only.

Every closed 15m bar:
1. It loads stored closed candles and computes the regime and both strategies. This is the
   same code as the backtester: ``spot_regime``, ``spot_strategies`` and
   ``nexus_risk.spot_rules``.
2. It manages open positions: stop, TP1 partial exit, break even after fees, TP2, and an
   emergency exit.
3. It records every candidate signal with its checks and its reason: accepted, rejected,
   or blocked by a risk limit.
4. It places entries and exits through the existing ``OrderManager`` and ``PaperBroker``.
   These fill against the real order book with latency, partial fills, stale-book refusals
   and Binance order rules. Every order, fill and position is persisted.

It uses its own paper portfolio ``spot-engine`` (10,000 virtual USDT), separate from the
main trader. It refuses to run unless the trading mode is paper and the broker is a
``PaperBroker``. It never creates a live order.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Callable
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any

import pandas as pd
from nexus_common.decimals import floor_to_step
from nexus_common.logging import get_logger
from nexus_common.timeutil import utcnow
from nexus_config import Settings, TradingMode
from nexus_db import models, repo, transaction
from nexus_exchange.models import Side, TimeInForce
from nexus_risk.spot_rules import BookState, Position, SpotRiskConfig, entry_checks, manage_bar, size
from nexus_strategies.frames import to_frame
from nexus_strategies.spot_regime import classify_spot
from nexus_strategies.spot_strategies import breakout_retest, trend_pullback
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from nexus_execution import protection
from nexus_execution.brokers import PaperBroker
from nexus_execution.order_manager import (
    DuplicateOrder,
    InsufficientBalance,
    OrderIntent,
    OrderManager,
    fund_paper_portfolio,
)
from nexus_execution.pretrade import PreTradeDenied

log = get_logger("nexus.spot_paper")

PORTFOLIO = "spot-engine"
CAPITAL = Decimal(10_000)
STATUS_KEY = "nexus:spot_engine:status"
# Pre-registered backtest verdict (docs/research/2026-11-23-spot-engine-preregistration.md).
BACKTEST = {
    "date": "2026-11-23", "period": "2022-11-01..2026-11-22", "symbols": 10,
    "trend_pullback": {"result": "FAIL", "trades": 322, "profit_factor": 0.59,
                       "return_pct": -0.145, "max_drawdown": 0.153},
    "breakout_retest_v2": {"result": "FAIL", "trades": 270, "profit_factor": 0.71,
                           "return_pct": -0.108, "max_drawdown": 0.131},
    "btc_buy_and_hold_return_pct": 1.94,
    "source": "docs/research/2026-11-23-spot-engine-preregistration.md"}
EXCHANGE = "binance"
BARS = 12000                       # 15m bars loaded (125 days)
# New entries need this much history: three 4h EMA200 spans (16 x 200 x 3 bars of 15m).
# With fewer bars the 4h EMA200 still depends on where the data starts (4000 bars left it up
# to 3.5% away from the full-history value), so live signals would differ from the
# backtest's. Open positions are managed whatever the history length.
WARMUP_BARS = 9600
STALE_AFTER = timedelta(minutes=20)
ENTRY_SLIP_CAP = Decimal("0.002")  # IOC buy limit above the ask
EXIT_SLIP_CAP = Decimal("0.005")
Q = pd.Timedelta(minutes=15)


def resample(m15: pd.DataFrame, rule: str, n: int) -> pd.DataFrame:
    g = m15.resample(rule, label="left", closed="left")
    out = g.agg({"open": "first", "high": "max", "low": "min", "close": "last",
                 "volume": "sum"})
    return out[g.size() == n].dropna()


def latest_candidates(sym: str, m15: pd.DataFrame, btc_h4: pd.DataFrame | None
                      ) -> tuple[pd.DataFrame, list[dict[str, Any]]]:
    """Regime on 15m and the candidates of both strategies on the LATEST closed bar(s)."""
    h1, h4 = resample(m15, "1h", 4), resample(m15, "4h", 16)
    is_btc = sym == "BTC/USDT"
    reg = classify_spot(m15, "15m", h1, h4, btc_h4, is_btc=is_btc)
    out: list[dict[str, Any]] = []
    tp = trend_pullback(m15, h1, reg)
    last = tp.iloc[-1]
    if bool(last["candidate"]):
        out.append({"strategy": "trend_pullback", "timeframe": "15m", "bar": tp.index[-1],
                    **last.to_dict()})
    # A 1h decision exists only when the latest 15m bar closed the hour.
    if len(h1) and h1.index[-1] + pd.Timedelta(hours=1) == m15.index[-1] + Q:
        reg1 = classify_spot(h1, "1h", h1, h4, btc_h4, is_btc=is_btc)
        bo = breakout_retest(h1, reg1)
        b = bo.iloc[-1]
        if bool(b["candidate"]):
            out.append({"strategy": "breakout_retest_v2", "timeframe": "1h",
                        "bar": bo.index[-1], **b.to_dict()})
    return reg, out


def _exit_reason(kind: str, tp1_fraction: float) -> str:
    if kind == "tp1":
        return "take_profit" if tp1_fraction >= 1 else "partial_take_profit"
    return kind


def _jsonable(v: Any) -> Any:
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return None
    if isinstance(v, (bool, int, float, str)):
        return v
    try:
        return bool(v) if str(v) in ("True", "False") else float(v)
    except (TypeError, ValueError):
        return str(v)


@dataclass(frozen=True)
class EngineProfile:
    """What differs between engines that share this runner (spot engine, NEXUS bot)."""
    portfolio: str = PORTFOLIO
    status_key: str = STATUS_KEY
    redis_prefix: str = "nexus:spot_engine"
    engine: str = "spot"
    base_tf: str = "15m"
    bar: pd.Timedelta = Q
    bars: int = BARS
    warmup_bars: int = WARMUP_BARS
    warmup_note: str = "no new entries until the 4h EMA200 is settled"
    stale_after: timedelta = STALE_AFTER
    # None = this module's ``latest_candidates`` (looked up at call time).
    candidates: Callable[..., tuple[pd.DataFrame, list[dict[str, Any]]]] | None = None
    btc_per_4h: int = 16                       # base bars per 4h bar
    breakdown: tuple[str, ...] = ("h4_above_ema200", "h1_above_ema200", "h1_ema50_above_200",
                                  "h1_adx", "h1_structure", "h1_atr_pct", "quote_vol_24h")
    tp_r: tuple[float, float] = (1.5, 3.0)      # TP1 and target in R from the fill
    max_hold: timedelta | None = None          # time stop
    use_exit_signal: bool = False              # strategy invalidation exits
    portfolio_guards: bool = False             # peak-drawdown halt and protections
    monitor_only: bool = False                 # signals only, no orders


SPOT = EngineProfile()


class SpotPaperRunner:
    def __init__(self, settings: Settings, factory: sessionmaker[Session], redis: Any,
                 adapters: dict[str, Any], books: Any, broker: PaperBroker,
                 cfg: SpotRiskConfig | None = None, profile: EngineProfile | None = None
                 ) -> None:
        if settings.trading_mode is not TradingMode.PAPER or not isinstance(broker, PaperBroker):
            raise PermissionError("this runner is paper-only")
        self.settings, self.factory, self.redis = settings, factory, redis
        self.adapters, self.books, self.broker = adapters, books, broker
        self.cfg = cfg or SpotRiskConfig()
        self.p = profile or SPOT
        self.orders = OrderManager(factory, broker, redis=redis)
        self.portfolio_id = 0

    # ------------------------------------------------------------------ setup
    def bootstrap(self) -> int:
        with transaction(self.factory) as s:
            owner = s.scalar(select(models.User).where(models.User.role == "owner"))
            if owner is None:
                raise RuntimeError("no owner account")
            p = s.scalar(select(models.Portfolio).where(
                models.Portfolio.user_id == owner.id, models.Portfolio.mode == "paper",
                models.Portfolio.name == self.p.portfolio))
            if p is None:
                p = models.Portfolio(user_id=owner.id, name=self.p.portfolio, mode="paper",
                                     quote_currency="USDT", initial_capital=CAPITAL)
                s.add(p)
                s.flush()
                fund_paper_portfolio(s, p.id, repo.exchange_id(s, EXCHANGE), "USDT", CAPITAL)
                repo.record_audit(s, actor=f"system:{self.p.portfolio}",
                                  action="paper_portfolio_funded",
                                  target_type="portfolio", target_id=str(p.id),
                                  details={"virtual_capital": str(CAPITAL), "paper_only": True})
            self.portfolio_id = p.id
            return p.id

    def frames(self, symbol: str) -> pd.DataFrame:
        with transaction(self.factory) as s:
            pid = repo.pair_id(s, EXCHANGE, symbol)
            return to_frame(repo.load_candles(s, pid, self.p.base_tf, limit=self.p.bars))

    # ------------------------------------------------------------------ state
    def _positions(self) -> list[tuple[models.Position, dict[str, Any], str, bool]]:
        with transaction(self.factory) as s:
            rows = s.execute(select(models.Position, models.TradingPair.symbol)
                             .join(models.TradingPair,
                                   models.TradingPair.id == models.Position.pair_id)
                             .where(models.Position.portfolio_id == self.portfolio_id,
                                    models.Position.status == "open")).all()
            out = []
            for pos, sym in rows:
                entry = s.get(models.Order, pos.entry_order_id) if pos.entry_order_id else None
                out.append((pos, dict(entry.details or {}) if entry else {}, sym,
                            protection.partial_taken(s, pos.id)))
            return out

    def _cash_and_cooldown(self, now: datetime) -> tuple[float, datetime | None, int]:
        with transaction(self.factory) as s:
            usdt = s.scalar(select(models.Balance).where(
                models.Balance.portfolio_id == self.portfolio_id, models.Balance.asset == "USDT",
                models.Balance.source == "internal"))
            closed = s.execute(select(models.Position.realized_pnl, models.Position.closed_at)
                               .where(models.Position.portfolio_id == self.portfolio_id,
                                      models.Position.status == "closed")
                               .order_by(models.Position.closed_at.desc()).limit(10)).all()
        cash = float(usdt.free + usdt.locked) if usdt else 0.0
        streak = 0
        for pnl, _ in closed:
            if pnl < 0:
                streak += 1
            else:
                break
        cooldown = None
        if streak >= self.cfg.loss_streak and closed and closed[0][1] is not None:
            cooldown = closed[0][1] + self.cfg.cooldown
        return cash, cooldown, streak

    def _guards(self, now: datetime, equity: float) -> tuple[float, list[Any]]:
        """Peak equity (capital plus the running maximum of realised P&L, and equity now) and
        the active protection locks, both from the database so a restart keeps them."""
        from nexus_risk.protections import ClosedTrade, ProtectionConfig, evaluate

        with transaction(self.factory) as s:
            rows = s.execute(select(models.Position, models.TradingPair.symbol)
                             .join(models.TradingPair,
                                   models.TradingPair.id == models.Position.pair_id)
                             .where(models.Position.portfolio_id == self.portfolio_id,
                                    models.Position.status == "closed")
                             .order_by(models.Position.closed_at)).all()
            port = s.get(models.Portfolio, self.portfolio_id)
            capital = float(port.initial_capital or CAPITAL) if port is not None else equity
        run = peak = capital
        trades = []
        for pos, sym in rows:
            if pos.closed_at is None:
                continue
            pnl = float(pos.realized_pnl or 0)
            run += pnl
            peak = max(peak, run)
            notional = float(pos.avg_entry_price) * float(pos.quantity or 0) or 1.0
            trades.append(ClosedTrade(sym.replace("/", ""), "bot", pos.closed_at, pnl,
                                      pnl / notional, pos.exit_reason or ""))
        return max(peak, equity), evaluate(trades, now, ProtectionConfig(), equity_now=equity)

    async def _period_start(self, key: str, equity: float) -> float:
        k = f"{self.p.redis_prefix}:{key}"
        await self.redis.set(k, str(equity), nx=True, ex=9 * 86400)
        raw = await self.redis.get(k)
        return float(raw) if raw else equity

    # ------------------------------------------------------------------ cycle
    async def cycle(self, now: datetime | None = None) -> dict[str, Any]:
        from nexus_risk.kill_switch import KillSwitch

        now = now or utcnow()
        await self.orders.refresh_open()
        await self.orders.reconcile_unknown()
        symbols = list(self.settings.trading_pairs)
        data = {s: self.frames(s) for s in symbols}
        btc = data.get("BTC/USDT")
        if btc is None and "BTC/USDT" not in symbols:
            try:
                btc = self.frames("BTC/USDT")
            except LookupError:
                btc = None
        btc_h4 = resample(btc, "4h", self.p.btc_per_4h) if btc is not None and len(btc) else None
        killed = await KillSwitch(self.redis).is_engaged()
        books = {s: await self.books.get(EXCHANGE, s) for s in symbols}
        marks = {s: float(b.mid) for s, b in books.items() if b is not None and b.mid}
        held = self._positions()
        cash, cooldown, streak = self._cash_and_cooldown(now)
        equity = cash + sum(float(p.quantity) * marks.get(sym, float(p.avg_entry_price))
                            for p, _, sym, _ in held)
        st = BookState(equity, cash, [], await self._period_start(f"day:{now:%Y-%m-%d}", equity),
                       await self._period_start(f"week:{now:%G-%V}", equity),
                       losses_in_a_row=streak, cooldown_until=cooldown)
        if self.p.portfolio_guards:
            st.peak_equity, st.locks = self._guards(now, equity)
        status: dict[str, Any] = {"at": now.isoformat(), "live_trading": "DISABLED",
                                  "mode": "monitor" if self.p.monitor_only else "paper",
                                  "engine": self.p.engine, "kill_switch": killed,
                                  "symbols": {}, "risk": {}, "signals": []}
        # Positions as rule objects (levels from the entry order; stop from the position row).
        rule_pos: dict[int, tuple[Position, models.Position, str]] = {}
        for p, det, sym, tp1_done in held:
            entry = float(p.avg_entry_price)
            init = float(det.get("initial_stop") or p.stop_loss or entry * 0.98)
            rp = Position(sym.replace("/", ""), det.get("strategy", "?"), float(p.quantity), entry,
                          float(p.stop_loss or init), init, float(det.get("tp1") or entry),
                          float(det.get("target") or entry), p.opened_at, det.get("regime"),
                          tp1_done=tp1_done, highest=entry)
            rule_pos[p.id] = (rp, p, sym)
            st.positions.append(rp)
        for sym in symbols:
            m15 = data[sym]
            fresh = len(m15) > 0 and now - (m15.index[-1] + self.p.bar).to_pydatetime() \
                <= self.p.stale_after
            info: dict[str, Any] = {"fresh": fresh, "bars": len(m15)}
            status["symbols"][sym] = info
            if len(m15) == 0:
                continue
            warm = len(m15) >= self.p.warmup_bars
            reg, cands = (self.p.candidates or latest_candidates)(sym, m15, btc_h4)
            r = reg.iloc[-1]
            invalidated = self.p.use_exit_signal and any(bool(c.get("exit")) for c in cands)
            if not warm:
                info["note"] = (f"warming up: {len(m15)} of {self.p.warmup_bars} "
                                f"{self.p.base_tf} bars stored; {self.p.warmup_note}")
                cands = []
            info.update(regime=r["regime"], trend=r["trend"], btc_risk_off=bool(r["btc_risk_off"]),
                        breakdown={k: _jsonable(r.get(k)) for k in self.p.breakdown},
                        last_bar=str(m15.index[-1]), warm=warm)
            # (a) manage this symbol's positions on the latest closed bar
            bar = m15.iloc[-1]
            for pid, (rp, row, psym) in list(rule_pos.items()):
                if psym != sym:
                    continue
                book = books.get(sym)
                emergency = (not fresh) or r["regime"] == "LOW_LIQUIDITY"
                before_stop = rp.stop
                fills = [] if emergency else manage_bar(
                    rp, float(bar["open"]), float(bar["high"]), float(bar["low"]),
                    float(bar["close"]), None, self.cfg)
                if emergency and book is not None:
                    await self._exit(row, sym, Decimal(str(rp.qty)), "emergency", book)
                    continue
                timed_out = self.p.max_hold is not None and now - row.opened_at >= self.p.max_hold
                if not fills and (invalidated or timed_out) and book is not None:
                    await self._exit(row, sym, Decimal(str(rp.qty)),
                                     "invalidation" if invalidated else "time_limit", book)
                    continue
                for f in fills:
                    if book is None:
                        break
                    await self._exit(row, sym, Decimal(str(f.qty)), f.kind, book)
                closing = any(f.kind != "tp1" for f in fills)
                if rp.stop > before_stop and not closing:      # raised stop: persist it
                    with transaction(self.factory) as s:
                        db = s.get(models.Position, pid)
                        if db is not None and db.status == "open":
                            db.stop_loss = Decimal(str(round(rp.stop, 8)))
            # (b) candidates on this bar
            for c in cands:
                if self.p.engine == "bot":
                    info["last_eval"] = {"bar": str(c["bar"]), "entry": bool(c["entry"]),
                                         "reason": c.get("reason"),
                                         "checks": {k[4:]: bool(v) for k, v in c.items()
                                                    if k.startswith("chk_")}}
                if not c.get("record", True):
                    continue
                await self._candidate(sym, c, reg, st, books.get(sym), fresh, killed, now, status)
        status["risk"] = {
            "equity": equity, "cash": cash, "open_positions": len(held),
            "open_risk_pct": sum(p.open_risk() for p in st.positions) / equity if equity else None,
            "day_drawdown_pct": 1 - equity / st.day_start_equity if st.day_start_equity else None,
            "week_drawdown_pct": 1 - equity / st.week_start_equity if st.week_start_equity else None,
            "limits": {k: (str(v) if isinstance(v, timedelta) else v)
                       for k, v in asdict(self.cfg).items()},
            "losses_in_a_row": streak, "cooldown_until": cooldown.isoformat() if cooldown else None}
        await self.redis.set(self.p.status_key, json.dumps(status, default=str), ex=3600)
        return status

    async def _exit(self, row: models.Position, sym: str, qty: Decimal, kind: str, book: Any) -> None:
        rules = await self.adapters[EXCHANGE].market_rules(sym)
        qty = floor_to_step(min(qty, row.quantity), rules.amount_step)
        if qty <= 0 or book.best_bid is None:
            return
        bid = book.best_bid
        price = floor_to_step(bid * (1 - EXIT_SLIP_CAP), rules.price_tick)
        try:
            await self.orders.place(OrderIntent(
                portfolio_id=self.portfolio_id, mode="paper", exchange=EXCHANGE, symbol=sym,
                side=Side.SELL, amount=qty, price=price, purpose="exit", expected_price=bid,
                time_in_force=TimeInForce.IOC, position_id=row.id,
                details={"exit_reason": _exit_reason(kind, self.cfg.tp1_fraction),
                         "engine": self.p.engine}))
        except DuplicateOrder:
            pass

    async def _candidate(self, sym: str, c: dict[str, Any], reg: pd.DataFrame, st: BookState,
                         book: Any, fresh: bool, killed: bool, now: datetime,
                         status: dict[str, Any]) -> None:
        reasons: list[str] = [] if bool(c["entry"]) else [str(c["reason"])]
        risk_mult = float(reg["risk_mult"].iloc[-1])
        edge_note = None
        if bool(c["entry"]) and self.p.portfolio_guards:
            from nexus_execution import edge

            with transaction(self.factory) as s:
                es = edge.update(s, self.portfolio_id, c["strategy"])
            if es is not None and es.multiplier == 0:
                reasons.append("edge monitor STOP: " + "; ".join(es.reasons))
            elif es is not None and es.multiplier < 1:
                risk_mult *= es.multiplier
                edge_note = "edge monitor REDUCE: " + "; ".join(es.reasons)
        if bool(c["entry"]):
            if self.p.monitor_only:
                reasons.append("monitor mode: signals only, no orders")
            if killed:
                reasons.append("kill switch engaged")
            if not fresh:
                reasons.append("market data stale")
            if book is None or book.best_ask is None:
                reasons.append("no fresh order book")
            entry_ref = float(c["stop"]) + float(c["r"])          # the signal bar's close
            reasons += entry_checks(st, sym.replace("/", ""), entry_ref, float(c["stop"]),
                                    now, self.cfg, risk_mult=risk_mult, strategy=c["strategy"])
        checks = {k[4:]: bool(v) for k, v in c.items() if k.startswith("chk_")}
        rec = {"time": now.isoformat(), "symbol": sym, "strategy": c["strategy"],
               "timeframe": c["timeframe"], "bar": str(c["bar"]), "regime": reg["regime"].iloc[-1],
               "stop": float(c["stop"]), "tp1": float(c["tp1"]), "tp2": float(c["tp2"]),
               "score": float(c["score"]), "checks": checks, "reasons": reasons,
               **({"edge": edge_note} if edge_note else {})}
        order_id = None
        if not reasons and book is not None:
            rules = await self.adapters[EXCHANGE].market_rules(sym)
            ask = float(book.best_ask)
            qty, why = size(st.equity, st.cash, ask, float(c["stop"]), self.cfg,
                            risk_mult=risk_mult, slippage=float(ENTRY_SLIP_CAP),
                            step=float(rules.amount_step),
                            min_notional=float(rules.min_notional or 0))
            if why:
                reasons.append(why)
            else:
                r_unit = ask - float(c["stop"])
                price = floor_to_step(Decimal(str(ask)) * (1 + ENTRY_SLIP_CAP), rules.price_tick)
                try:
                    order_id = await self.orders.place(OrderIntent(
                        portfolio_id=self.portfolio_id, mode="paper", exchange=EXCHANGE,
                        symbol=sym, side=Side.BUY,
                        amount=floor_to_step(Decimal(str(qty)), rules.amount_step), price=price,
                        purpose="entry", expected_price=Decimal(str(ask)),
                        time_in_force=TimeInForce.IOC,
                        details={"strategy": c["strategy"], "engine": self.p.engine,
                                 "stop": str(round(float(c["stop"]), 8)),
                                 "initial_stop": str(round(float(c["stop"]), 8)),
                                 "tp1": str(round(ask + self.p.tp_r[0] * r_unit, 8)),
                                 "target": str(round(ask + self.p.tp_r[1] * r_unit, 8)),
                                 "regime": rec["regime"], "score": rec["score"],
                                 "checks": checks}))
                    rec["qty"], rec["risk_usdt"] = qty, qty * r_unit
                except (DuplicateOrder, InsufficientBalance, PreTradeDenied) as exc:
                    reasons.append(str(exc))
        rec["status"] = "executed" if order_id is not None else "rejected"
        rec["order_id"] = order_id
        self._record_signal(sym, c, rec)
        status["signals"].append(rec)

    def _record_signal(self, sym: str, c: dict[str, Any], rec: dict[str, Any]) -> None:
        from sqlalchemy.exc import IntegrityError

        try:
            with transaction(self.factory) as s:
                strat = s.scalar(select(models.Strategy.id).where(
                    models.Strategy.code == c["strategy"]))
                if strat is None:
                    return
                close = rec["stop"] + float(c["r"])               # the signal bar's close
                s.add(models.TradingSignal(
                    mode="paper", strategy_id=strat, pair_id=repo.pair_id(s, EXCHANGE, sym),
                    side="buy", timeframe=c["timeframe"], bar_time=pd.Timestamp(c["bar"]).to_pydatetime(),
                    entry_price=Decimal(str(round(close, 8))),
                    stop_loss=Decimal(str(round(rec["stop"], 8))) if rec["stop"] < close else None,
                    take_profit=Decimal(str(round(rec["tp2"], 8))) if rec["tp2"] > close else None,
                    confidence=Decimal(str(round(max(0.0, min(1.0, rec["score"])), 6))),
                    status=rec["status"],
                    rejection_reason="; ".join(rec["reasons"])[:2000] or None,
                    details={"engine": self.p.engine, "regime": rec["regime"],
                             "checks": rec["checks"],
                             "tp1": rec["tp1"], "order_id": rec["order_id"]}))
        except IntegrityError:
            pass                      # this bar's signal was already recorded


async def serve(settings: Settings, stop: asyncio.Event) -> None:
    from nexus_common.binance_guard import attach_weight_hooks, set_default_redis
    from nexus_common.redisutil import connect as connect_redis
    from nexus_db import make_engine, make_session_factory
    from nexus_exchange.factory import build_from_settings

    from nexus_execution.trader import BookCache, load_markets_when_allowed

    if settings.trading_mode is not TradingMode.PAPER:
        log.warning("spot_engine_not_started", reason="paper mode only")
        return
    engine = make_engine(settings.database_url.get_secret_value(), pool_size=1, max_overflow=1)
    factory = make_session_factory(engine)
    redis = connect_redis(settings.redis_url.get_secret_value())
    set_default_redis(redis)
    adapters: dict[str, Any] = {e.value: build_from_settings(e, settings)
                                for e in settings.enabled_exchanges if e.value == EXCHANGE}
    try:
        attach_weight_hooks(adapters, redis)
        if not await load_markets_when_allowed(adapters, redis, stop):
            return
        books = BookCache(adapters, redis=redis)
        broker = PaperBroker(books.get, taker_fee=settings.paper_fee_bps / 10_000,
                             maker_fee=settings.paper_fee_bps / 10_000,
                             extra_slippage=settings.paper_slippage_bps / 10_000,
                             latency_ms=settings.paper_latency_ms,
                             participation=settings.paper_book_participation,
                             max_book_age_s=settings.paper_max_book_age_seconds,
                             outage_rate=settings.paper_outage_rate)
        runner = SpotPaperRunner(settings, factory, redis, adapters, books, broker)
        runner.bootstrap()
        while not stop.is_set():
            try:
                await runner.cycle()
            except Exception as exc:          # one bad cycle must not stop the runner
                log.error("spot_engine_cycle_failed", error=f"{type(exc).__name__}: {exc}"[:300])
            now = utcnow()
            nxt = now.replace(minute=now.minute - now.minute % 15, second=40,
                              microsecond=0) + timedelta(minutes=15)
            try:
                await asyncio.wait_for(stop.wait(), timeout=(nxt - utcnow()).total_seconds())
            except TimeoutError:
                pass
    finally:
        for a in adapters.values():
            await a.close()
        await redis.aclose()
        engine.dispose()
