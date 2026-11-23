"""NEXUS bot: one clearly defined strategy (EMA/RSI/MACD on 5m with 15m and 1h filters,
``nexus_strategies.bot_strategy``) run continuously on the configured Binance spot pairs.

Modes (``NEXUS_BOT_MODE``):
* off      the worker does not start;
* monitor  it evaluates every closed 5m bar and records qualifying signals, with no orders;
* paper    the same, plus orders through the OrderManager and PaperBroker. These fill
           against the LIVE Binance order book with latency, partial fills and Binance's
           order rules. The balance is simulated (10,000 USDT), and every order, fill and
           position is persisted.
* live     not offered by this worker. Real orders exist only through the main trader's
           live gate (owner 2FA approval, verified key with trading permission, funded
           account, kill switch clear), and only after the owner's explicit activation.

Risk (owner request 2026-11-23):
* 0.25% risk per trade, 0.5% maximum; 1% daily loss; 3% portfolio drawdown (halt until the
  owner reviews);
* at most 2 positions, one per pair, no averaging down, a mandatory stop;
* sizing from equity and the stop distance, fees and slippage included;
* Freqtrade-style protections (stop-loss guard, low-profit pair lock, cooldown).
"""

from __future__ import annotations

import asyncio
from datetime import timedelta
from typing import Any

import pandas as pd
from nexus_common.logging import get_logger
from nexus_common.timeutil import utcnow
from nexus_config import Settings, TradingMode
from nexus_risk.spot_rules import SpotRiskConfig
from nexus_strategies import bot_strategy

from nexus_execution.spot_paper import EngineProfile, SpotPaperRunner

log = get_logger("nexus.bot")

STATUS_KEY = "nexus:bot:status"
PORTFOLIO = "nexus-bot"

BOT_RISK = SpotRiskConfig(risk_per_trade=0.0025, max_risk_per_trade=0.005,
                          max_open_risk=0.005, max_daily_drawdown=0.01,
                          max_weekly_drawdown=0.03, max_drawdown=0.03, max_positions=2,
                          max_allocation=0.25, tp1_fraction=1.0)


def profile(monitor_only: bool = False) -> EngineProfile:
    return EngineProfile(
        portfolio=PORTFOLIO, status_key=STATUS_KEY, redis_prefix="nexus:bot", engine="bot",
        base_tf="5m", bar=pd.Timedelta(minutes=5), bars=3000, warmup_bars=2400,
        warmup_note="indicators (1h EMA50) still settling", stale_after=timedelta(minutes=10),
        candidates=bot_strategy.latest, btc_per_4h=48, breakdown=(),
        tp_r=(bot_strategy.BotParams().target_r, bot_strategy.BotParams().target_r),
        max_hold=timedelta(minutes=5 * bot_strategy.BotParams().max_hold_bars),
        use_exit_signal=True, portfolio_guards=True, monitor_only=monitor_only)


async def serve(settings: Settings, stop: asyncio.Event) -> None:
    from nexus_common.binance_guard import attach_weight_hooks, set_default_redis
    from nexus_common.redisutil import connect as connect_redis
    from nexus_db import make_engine, make_session_factory
    from nexus_exchange.factory import build_from_settings

    from nexus_execution.brokers import PaperBroker
    from nexus_execution.trader import BookCache, load_markets_when_allowed

    if settings.bot_mode == "off":
        log.info("bot_not_started", reason="NEXUS_BOT_MODE=off")
        return
    if settings.trading_mode is not TradingMode.PAPER:
        log.warning("bot_not_started", reason="the bot runs in monitor or paper mode only")
        return
    engine = make_engine(settings.database_url.get_secret_value(), pooled=False)
    factory = make_session_factory(engine)
    redis = connect_redis(settings.redis_url.get_secret_value())
    set_default_redis(redis)
    adapters: dict[str, Any] = {e.value: build_from_settings(e, settings)
                                for e in settings.enabled_exchanges if e.value == "binance"}
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
        runner = SpotPaperRunner(settings, factory, redis, adapters, books, broker,
                                 cfg=BOT_RISK, profile=profile(settings.bot_mode == "monitor"))
        runner.bootstrap()
        log.info("bot_started", mode=settings.bot_mode, pairs=list(settings.trading_pairs))
        while not stop.is_set():
            try:
                await runner.cycle()
            except Exception as exc:          # one bad cycle must not stop the bot
                log.error("bot_cycle_failed", error=f"{type(exc).__name__}: {exc}"[:300])
            now = utcnow()
            nxt = now.replace(minute=now.minute - now.minute % 5, second=25,
                              microsecond=0) + timedelta(minutes=5)
            try:
                await asyncio.wait_for(stop.wait(), timeout=(nxt - utcnow()).total_seconds())
            except TimeoutError:
                pass
    finally:
        for a in adapters.values():
            await a.close()
        await redis.aclose()
        engine.dispose()


async def main() -> None:
    import signal

    from nexus_common.logging import configure_logging
    from nexus_config import get_settings

    settings = get_settings()
    configure_logging(settings.log_level, json=settings.log_json)
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stop.set)
    await serve(settings, stop)


if __name__ == "__main__":
    asyncio.run(main())
