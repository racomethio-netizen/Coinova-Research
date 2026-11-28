"""Strategy orchestrator worker (unified brief, section E).

Every 5 minutes it decides BUY / SELL / NO_TRADE for each instrument with
``nexus_decision.orchestrator`` and publishes the board in Redis for the dashboard:
* crypto: the configured Binance pairs;
* forex and gold: the Exness symbols with stored bars.

A decision row (``orchestrator_decisions``) is written when an instrument's decision
changes, and at least once an hour. Rows are pruned after 180 days.

Inputs:
* the market-intelligence snapshot from stored candles; higher-timeframe bars are
  cached until a new bar can have closed, so the database is not re-read every cycle;
* the economic-event blackout;
* the market switches, the kill switch (Redis or the durable audit log) and the paper
  risk state;
* the strategy modules' test results;
* their signals:
  * the crypto swing module's daily target from its forward paper test;
  * the forward observation records of modules switched to OBSERVE.

Nothing here places an order; a BUY or SELL is a proposal for the risk engine.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
from datetime import UTC, datetime, timedelta
from typing import Any

import pandas as pd
from nexus_common.logging import get_logger
from nexus_common.timeutil import utcnow
from nexus_config import Settings
from nexus_decision import orchestrator as orch

log = get_logger("nexus.orchestrator")

STATUS_KEY = "nexus:orchestrator:board"
RISK_KEY = "nexus:risk:state:paper"
INTERVAL = 300
HEARTBEAT = timedelta(hours=1)
RETENTION = timedelta(days=180)
SWING = "crypto_swing_trend"
SWING_BAND = 0.20                 # the trend rule's own rebalancing band (20% of target)
TF_SECONDS = {"1h": 3600, "4h": 14_400, "1d": 86_400}


class FrameCache:
    """Stored candles per (venue, symbol, timeframe), re-read only when a newer bar can
    exist: a new period has started, or the newest stored bar is behind and 4 minutes
    have passed since the last read (bars from the Exness bridge arrive late)."""

    def __init__(self) -> None:
        self._d: dict[tuple[str, str, str], tuple[datetime, int, pd.DataFrame]] = {}

    def get(self, s: Any, venue: str, symbol: str, tf: str, limit: int,
            now: datetime) -> pd.DataFrame:
        from nexus_db import repo
        from nexus_strategies.frames import to_frame

        step = TF_SECONDS[tf]
        bucket = int(now.timestamp()) // step
        key = (venue, symbol, tf)
        hit = self._d.get(key)
        if hit is not None:
            read_at, b, df = hit
            behind = df.empty or (df.index[-1] + pd.Timedelta(seconds=2 * step)
                                  <= pd.Timestamp(now))
            if b == bucket and not (behind and now - read_at > timedelta(minutes=4)):
                return df
        try:
            pid = repo.pair_id(s, venue, symbol)
            df = to_frame(repo.load_candles(s, pid, tf, limit=limit))
        except LookupError:
            df = to_frame([])
        self._d[key] = (now, bucket, df)
        return df


def instruments(settings: Settings) -> list[tuple[str, str, str]]:
    """(market, NEXUS symbol, module symbol)."""
    from nexus_market import fx_bars

    out = [("crypto", p, p.replace("/", "")) for p in settings.trading_pairs]
    out += [("forex", s, s) for s in fx_bars.INTRADAY_SYMBOLS]
    return out


def evidence(rows: list[Any]) -> dict[str, orch.ModuleEvidence]:
    out = {}
    for r in rows:
        v = r.validation or {}
        if r.code == SWING:
            summary = (f"passed its pre-registered test (Sharpe {v.get('sharpe', 0):.2f}, "
                       f"worst drop {100 * (v.get('max_drawdown') or 0):.0f}%) and runs as "
                       "a forward paper test")
            out[r.code] = orch.ModuleEvidence(r.code, r.name, v.get("passed"), None, None,
                                              kind="portfolio", summary=summary)
        else:
            out[r.code] = orch.ModuleEvidence(r.code, r.name, v.get("passed"),
                                              v.get("mean_r"), v.get("trades"))
    return out


def swing_signals(s: Any, now: datetime) -> dict[str, orch.Signal]:
    """The crypto swing module's stance per coin from its latest forward-test day: buy
    when its target allocation is above what it holds by more than its band, sell when
    below. A record older than 26 hours gives no signal."""
    from nexus_backtest.trend_paper import RUN
    from nexus_db import models
    from sqlalchemy import select

    row = s.scalar(select(models.ForwardTestEvent).where(
        models.ForwardTestEvent.run == RUN, models.ForwardTestEvent.kind == "day")
        .order_by(models.ForwardTestEvent.at.desc()).limit(1))
    if row is None:
        return {}
    close = row.at + timedelta(days=1)
    if now - close > timedelta(hours=26):
        return {}
    out = {}
    for sym, target in (row.data.get("targets") or {}).items():
        held = float((row.data.get("weights") or {}).get(sym, 0.0))
        t = float(target)
        if abs(t - held) > max(SWING_BAND * max(t, held), 0.01):
            side = 1 if t > held else -1
            out[sym] = orch.Signal(SWING, side, close, None, None, None, "forward_test",
                                   f"trend rule target {t:.0%} of its sleeve vs {held:.0%} "
                                   "held")
    return out


def observer_signals(s: Any, now: datetime) -> dict[str, list[orch.Signal]]:
    """Fresh signals recorded by the forward observer (modules on OBSERVE)."""
    from nexus_db import models
    from nexus_strategies import modules as mods
    from sqlalchemy import select

    sig = models.StrategyModuleSignal
    rows = s.scalars(select(sig).where(sig.signal_time >= now - timedelta(days=2)))
    out: dict[str, list[orch.Signal]] = {}
    for r in rows:
        spec = mods.MODULES.get(r.module_code)
        if spec is None:
            continue
        close = r.signal_time + timedelta(seconds={"5m": 300, "1h": 3600, "1d": 86400}
                                          [spec.signal_tf])
        if now - close > orch.DEFAULT.fresh_signal:
            continue
        out.setdefault(r.symbol, []).append(orch.Signal(
            r.module_code, int(r.side), close, float(r.entry) if r.entry else None,
            float(r.stop), float(r.rr), "observer"))
    return out


def fingerprint(v: orch.Verdict) -> str:
    key = json.dumps([v.action, v.module, v.side, v.regime, sorted(v.blockers),
                      sorted((c["module"], c["eligible"]) for c in v.candidates)])
    return hashlib.sha256(key.encode()).hexdigest()


def step_sync(factory: Any, cache: FrameCache, settings: Settings, killed_redis: bool,
              risk: dict[str, Any] | None, spreads: dict[str, float], now: datetime
              ) -> list[dict[str, Any]]:
    from decimal import Decimal

    from nexus_analysis.intelligence import snapshot
    from nexus_backtest.module_observer import sync_registry
    from nexus_common.markets import Market, effective
    from nexus_db import models, repo, transaction
    from nexus_news import calendar as econ
    from nexus_strategies import modules as mods
    from nexus_strategies.modules.base import FX_DAY_OFFSET, resample
    from sqlalchemy import delete, func, select

    cal = econ.load()
    board = []
    with transaction(factory) as s:
        sync_registry(s)
        ev = evidence(list(s.scalars(select(models.StrategyModule))))
        switches = repo.market_autonomy_db_state(s)
        killed = killed_redis or repo.kill_switch_db_state(s) is not None
        swing = swing_signals(s, now)
        observed = observer_signals(s, now)
        last = {sym: at for sym, at in s.execute(
            select(models.OrchestratorDecision.symbol,
                   func.max(models.OrchestratorDecision.decided_at))
            .where(models.OrchestratorDecision.decided_at >= now - 2 * HEARTBEAT)
            .group_by(models.OrchestratorDecision.symbol)).all()}
        last_fp = {r.symbol: r.fingerprint for r in s.scalars(
            select(models.OrchestratorDecision).where(
                models.OrchestratorDecision.decided_at >= now - 2 * HEARTBEAT)
            .order_by(models.OrchestratorDecision.decided_at))}
        for market, sym, msym in instruments(settings):
            venue = settings.signal_exchange if market == "crypto" else "exness"
            if market == "crypto":
                frames = {tf: cache.get(s, venue, sym, tf, 600, now) for tf in ("1h", "4h", "1d")}
            else:
                h1 = cache.get(s, venue, sym, "1h", 2500, now)
                frames = {"1h": h1}
                if not h1.empty:
                    frames["1d"] = resample(h1, "1D", offset=FX_DAY_OFFSET, min_bars=6)
            snap = snapshot(sym, market, frames, "1h", now, spread=spreads.get(sym),
                            event_risk=econ.event_risk(sym, market, now, cal))
            covering = tuple(c for c, m in mods.MODULES.items() if msym in m.symbols)
            signals = list(observed.get(msym, []))
            if msym in swing:
                signals.append(swing[msym])
            mk = Market.CRYPTO if market == "crypto" else Market.FOREX
            if sym.startswith(("XAU", "XAG")):
                mk = Market.COMMODITIES
            inp = orch.Inputs(sym, market, now, snap, snap.get("event_risk"),
                              effective(mk, switches).value, killed,
                              None if risk is None else bool(risk.get("entries_allowed")),
                              tuple(signals), ev, covering)
            v = orch.orchestrate(inp)
            fp = fingerprint(v)
            d = v.as_dict() | {"snapshot": {k: snap.get(k) for k in (
                "price", "as_of", "session", "regime", "liquidity", "volatility")}}
            board.append(d)
            prev = last.get(sym)
            if prev is None or last_fp.get(sym) != fp or now - prev >= HEARTBEAT:
                s.add(models.OrchestratorDecision(
                    decided_at=now, market=market, symbol=sym, action=v.action,
                    module=v.module, side=v.side,
                    entry_ref=Decimal(repr(v.entry_ref)) if v.entry_ref is not None else None,
                    stop=Decimal(repr(v.stop)) if v.stop is not None else None,
                    target=Decimal(repr(v.target)) if v.target is not None else None,
                    reward_risk=Decimal(repr(v.reward_risk)) if v.reward_risk else None,
                    expected_r=Decimal(repr(round(v.expected_r, 6)))
                    if v.expected_r is not None else None,
                    regime=v.regime, reasons=v.reasons, blockers=v.blockers,
                    candidates=v.candidates, context=json.loads(json.dumps(
                        d["snapshot"], default=str)), fingerprint=fp))
        if now.hour == 3 and now.minute < 5:
            s.execute(delete(models.OrchestratorDecision).where(
                models.OrchestratorDecision.decided_at < now - RETENTION))
    return board


async def fx_spreads(redis: Any) -> dict[str, float]:
    from nexus_backtest.module_observer import fx_spreads as spreads

    return await spreads(redis)


async def step(factory: Any, redis: Any, settings: Settings, cache: FrameCache,
               now: datetime | None = None) -> list[dict[str, Any]]:
    from nexus_risk.kill_switch import KillSwitch

    now = now or utcnow()
    killed = await KillSwitch(redis).status() is not None
    raw = await redis.get(RISK_KEY)
    risk = json.loads(raw) if raw else None
    spreads = await fx_spreads(redis)
    board = await asyncio.to_thread(step_sync, factory, cache, settings, killed, risk,
                                    spreads, now)
    await redis.set(STATUS_KEY, json.dumps({"at": now.isoformat(), "board": board},
                                           default=str), ex=1800)
    trades = [b["symbol"] for b in board if b["action"] != orch.NO_TRADE]
    if trades:
        log.info("orchestrator_proposals", symbols=trades)
    return board


async def serve(settings: Settings, stop: asyncio.Event) -> None:
    from nexus_common.redisutil import connect as connect_redis
    from nexus_db import make_engine, make_session_factory

    engine = make_engine(settings.database_url.get_secret_value(), pooled=False)
    factory = make_session_factory(engine)
    redis = connect_redis(settings.redis_url.get_secret_value(), max_connections=1)
    cache = FrameCache()
    try:
        while not stop.is_set():
            try:
                await step(factory, redis, settings, cache)
            except Exception as exc:
                log.error("orchestrator_failed", error=f"{type(exc).__name__}: {exc}"[:300])
            now = datetime.now(UTC)
            wait = INTERVAL - (now.timestamp() % INTERVAL) + 50
            try:
                await asyncio.wait_for(stop.wait(), timeout=wait)
            except TimeoutError:
                pass
    finally:
        await redis.aclose()
        engine.dispose()


def main() -> None:   # pragma: no cover - process entry point
    import signal

    from nexus_common.logging import configure_logging
    from nexus_config import get_settings

    settings = get_settings()
    configure_logging(settings.log_level, json=settings.log_json)

    async def run() -> None:
        stop = asyncio.Event()
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(sig, stop.set)
        await serve(settings, stop)

    asyncio.run(run())


if __name__ == "__main__":  # pragma: no cover
    main()
