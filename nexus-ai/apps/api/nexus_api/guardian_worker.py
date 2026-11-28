"""The Guardian worker (unified brief, section H): ``python -m nexus_api.guardian_worker``.

Every minute it collects what the platform publishes and checks it
(``nexus_risk.guardian``: platform, market data, models, execution, risk).

* **Report:** ``nexus:guardian:report`` (Redis), shown on the dashboard's Guardian page
  and served by ``GET /api/v1/guardian``.
* **Alerts:** debounced, once per change. Each one is a ``system_events`` row (component
  ``guardian``) and a notification (Telegram when configured, otherwise the server log).
* **Kill switch restore:** if the durable record says the kill switch is engaged but
  Redis lost the key, the Guardian restores it. It never clears it.
* **Emergency shutdown:** on a shutdown condition (see ``nexus_risk.guardian``) it
  engages the durable kill switch as ``system:guardian``. The engagement is written to
  the audit log, a risk event and a system event. Only the owner clears it, with 2FA.

It reads and protects; it never opens or closes a position itself. With the kill switch
engaged, the trader and the Exness demo sync block new entries, and positions keep their
stops.

One instance leads (a Redis lock), so the overlap of two servers during a deploy does
not double alerts. The previous run's state is kept in the report, so a restart does not
repeat alerts for problems already reported.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import time
import uuid
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from nexus_common import events
from nexus_common.logging import get_logger
from nexus_common.timeutil import utcnow
from nexus_config import Settings, TradingMode
from nexus_risk import guardian as g

log = get_logger("nexus.guardian")

REPORT_KEY = "nexus:guardian:report"
LEADER_KEY = "nexus:guardian:leader"
INTERVAL = 60
LEADER_TTL = 150
ACTOR = "system:guardian"
TIMEFRAMES = ("5m", "1h", "4h")      # what the trader and the orchestrator read
PRICE_BARS = 13                      # the last hour of 5-minute candles
ALLOCATION_MODULES = ("crypto_swing_trend",)   # forward record kept in equity, not in R


def container_memory() -> float | None:
    """Share of the container's memory limit in use (cgroup v2, then v1), else of the
    machine's (``/proc/meminfo``)."""
    from nexus_notify.monitor import memory_usage

    for cur, cap in (("/sys/fs/cgroup/memory.current", "/sys/fs/cgroup/memory.max"),
                     ("/sys/fs/cgroup/memory/memory.usage_in_bytes",
                      "/sys/fs/cgroup/memory/memory.limit_in_bytes")):
        try:
            used, limit = Path(cur).read_text().strip(), Path(cap).read_text().strip()
            if limit != "max" and int(limit) < 1 << 50:
                return int(used) / int(limit)
        except (OSError, ValueError):
            continue
    try:
        return memory_usage()
    except (OSError, ValueError):
        return None


def trader_mode(settings: Settings) -> str:
    return "live" if settings.trading_mode is TradingMode.LIVE else "paper"


def collect_db(s: Any, settings: Settings, now: datetime) -> dict[str, Any]:
    """Everything the checks need from the database (blocking: run in a thread)."""
    from nexus_db import models, repo
    from nexus_execution import intents as ix
    from nexus_market.derived import series_quality
    from sqlalchemy import desc, func, select, text

    t0 = time.monotonic()
    s.execute(text("SELECT 1"))
    out: dict[str, Any] = {"db_seconds": time.monotonic() - t0,
                           "kill_durable": repo.kill_switch_db_state(s)}
    ex = settings.signal_exchange
    series, prices = [], {}
    for sym in settings.trading_pairs:
        try:
            pid = repo.pair_id(s, ex, sym)
        except LookupError:
            continue
        series += [series_quality(s, ex, sym, pid, tf, now=now).as_dict() for tf in TIMEFRAMES]
        prices[sym] = [(float(r.open), float(r.high), float(r.low), float(r.close))
                       for r in repo.load_candles(s, pid, "5m", limit=PRICE_BARS)]
    out["series"], out["prices"] = series, prices

    ml = []
    for m in s.scalars(select(models.AiModel).where(models.AiModel.status == "active")):
        last = s.scalar(select(func.max(models.AiPrediction.created_at)).where(
            models.AiPrediction.model_id == m.id))
        ev = s.scalar(select(models.ModelEvaluation).where(
            models.ModelEvaluation.model_id == m.id,
            models.ModelEvaluation.evaluation_type == "live_drift")
            .order_by(desc(models.ModelEvaluation.created_at)).limit(1))
        ml.append({"name": m.name, "version": m.version,
                   "last_prediction": last.isoformat() if last else None,
                   "drift_passed": ev.passed if ev is not None else None})
    out["ml_models"] = ml

    mods = []
    for m in s.scalars(select(models.StrategyModule).where(
            models.StrategyModule.activation.in_(("OBSERVE", "PAPER")))):
        sig = models.StrategyModuleSignal
        r = [] if m.code in ALLOCATION_MODULES else list(s.scalars(
            select(sig.net_r).where(sig.module_code == m.code, sig.net_r.is_not(None))
            .order_by(sig.resolved_at, sig.id)))
        v = m.validation or {}
        mods.append({"code": m.code, "activation": m.activation,
                     "allocation": m.code in ALLOCATION_MODULES,
                     "net_r": [float(x) for x in r],
                     "ref_mean_r": v.get("mean_r") if v.get("passed") else None})
    out["modules"] = mods

    it = models.ExecutionIntent
    out["intents"] = [{"client_id": r.client_id, "venue": r.venue, "symbol": r.symbol,
                       "side": "buy" if r.side > 0 else "sell", "kind": r.kind,
                       "status": r.status, "events": r.events or []}
                      for r in s.scalars(select(it).where(it.status.in_(ix.ACTIVE)))]
    out["rejected_24h"] = int(s.scalar(select(func.count()).select_from(it).where(
        it.status.in_(("rejected", "expired")), it.updated_at >= now - timedelta(hours=24)))
        or 0)
    out["fills"] = [{"venue": v, "slippage_bps": b} for v, b in s.execute(
        select(it.venue, it.slippage_bps).where(it.slippage_bps.is_not(None))
        .order_by(desc(it.created_at)).limit(60))]

    o, mode = models.Order, trader_mode(settings)
    out["orders_unknown"] = int(s.scalar(select(func.count()).select_from(o).where(
        o.mode == mode, o.status == "unknown")) or 0)
    out["orders_stuck"] = int(s.scalar(select(func.count()).select_from(o).where(
        o.mode == mode, o.status.in_(("pending_submit", "submitted")),
        o.created_at < now - g.ORDER_STUCK_AGE)) or 0)
    return out


async def _json(redis: Any, key: str) -> Any:
    raw = await redis.get(key)
    if raw is None:
        return None
    return json.loads(raw.decode() if isinstance(raw, bytes) else raw)


async def collect_redis(redis: Any, settings: Settings) -> dict[str, Any]:
    from nexus_execution.trader import RISK_STATE_KEY
    from nexus_risk.kill_switch import KillSwitch

    from nexus_api import bridge, mt5_demo
    from nexus_api import orchestrator_worker as ow
    from nexus_api import portfolio_risk as pr
    from nexus_api.workers import STATUS_KEY as WORKERS_KEY

    mode = trader_mode(settings)
    ex = settings.signal_exchange
    out: dict[str, Any] = {"kill": await KillSwitch(redis).status()}
    beats = {}
    for name in g.heartbeat_names(mode):
        raw = await redis.get(events.heartbeat_key(name))
        beats[name] = raw.decode() if isinstance(raw, bytes) else raw
    out["beats"] = beats
    out["workers"] = await _json(redis, WORKERS_KEY)
    out["board"] = await _json(redis, ow.STATUS_KEY)
    out["risk_state"] = await _json(redis, RISK_STATE_KEY.format(mode=mode))
    out["book"] = await _json(redis, pr.STATUS_KEY)
    out["demo"] = await _json(redis, mt5_demo.STATUS_KEY)
    out["bridge"] = await _json(redis, bridge.STATUS_KEY)
    out["halt"] = await _json(redis, mt5_demo.HALT_KEY)
    out["books"] = {sym: await _json(redis, events.book_key(ex, sym))
                    for sym in settings.trading_pairs}
    out["health"] = await _json(redis, events.health_key(ex))
    out["previous"] = await _json(redis, REPORT_KEY)
    return out


def build_checks(db: dict[str, Any] | None, db_error: str | None, rd: dict[str, Any] | None,
                 redis_error: str | None, settings: Settings, now: datetime, *,
                 restored: bool, disk: float | None,
                 memory: float | None) -> list[g.Check]:
    from nexus_common.sessions import is_open

    db, rd = db or {}, rd or {}
    checks = g.platform_checks(db_seconds=db.get("db_seconds"), db_error=db_error,
                               redis_error=redis_error, memory=memory, disk=disk)
    if redis_error is None:
        checks += g.worker_checks(rd.get("workers"), now)
        checks += g.heartbeat_checks(rd.get("beats") or {}, now, trader_mode(settings))
        checks.append(g.freshness("platform", "orchestrator", (rd.get("board") or {}).get("at"),
                                  now, g.ORCHESTRATOR_MAX_AGE, "strategy orchestrator"))
    if db_error is None:
        checks += g.series_checks(db.get("series") or [])
        for sym, candles in (db.get("prices") or {}).items():
            checks += g.price_checks(sym, candles)
    if redis_error is None:
        for sym, book in (rd.get("books") or {}).items():
            last = (db.get("prices") or {}).get(sym) or []
            checks += g.quote_checks(sym, book, last[-1][3] if last else None, now)
        checks.append(g.exchange_check(settings.signal_exchange, rd.get("health")))
        checks += g.forex_checks(rd.get("demo"), rd.get("bridge"), now, is_open("fx_24x5", now))
    if db_error is None:
        checks += g.ml_model_checks(db.get("ml_models") or [], now)
        checks += g.module_checks(db.get("modules") or [])
        checks += g.intent_checks(db.get("intents") or [], int(db.get("rejected_24h") or 0),
                                  db.get("fills") or [], now)
        checks += g.order_checks(int(db.get("orders_unknown") or 0),
                                 int(db.get("orders_stuck") or 0))
    kill = rd.get("kill") if redis_error is None else db.get("kill_durable")
    checks.append(g.kill_check(kill, restored))
    if redis_error is None:
        checks.append(g.risk_state_check(rd.get("risk_state"), now))
        checks += g.book_checks(rd.get("book"), now, kill_engaged=kill is not None)
        checks.append(g.demo_halt_check(rd.get("halt")))
    return checks


def _record(factory: Any, alerts: list[tuple[g.Check, str]], shutdown: str | None,
            mode: str) -> None:
    from nexus_db import repo, transaction

    sev = {g.OK: "info", g.WARN: "warning", g.CRITICAL: "critical"}
    with transaction(factory) as s:
        for c, kind in alerts:
            head = "recovered" if kind == "recovered" else c.status.upper()
            repo.record_system_event(s, component="guardian", event_type=c.name[:48],
                                     severity=sev[c.status],
                                     message=f"{c.domain}: {head}: {c.detail}"[:2000],
                                     details={"domain": c.domain, "check": c.name,
                                              "status": c.status, "kind": kind})
        if shutdown:
            repo.record_audit(s, actor=ACTOR, action="kill_switch_engaged",
                              details={"reason": shutdown})
            repo.record_risk_event(s, mode=mode, event_type="kill_switch",
                                   severity="critical",
                                   details={"reason": shutdown, "actor": ACTOR})
            repo.record_system_event(s, component="guardian", event_type="emergency_shutdown",
                                     severity="critical", message=shutdown[:2000],
                                     details={"actor": ACTOR})


async def lead(redis: Any, instance: str) -> bool:
    """True when this instance runs the checks (it holds or takes the lock)."""
    if await redis.set(LEADER_KEY, instance, nx=True, ex=LEADER_TTL):
        return True
    holder = await redis.get(LEADER_KEY)
    holder = holder.decode() if isinstance(holder, bytes) else holder
    if holder == instance:
        await redis.expire(LEADER_KEY, LEADER_TTL)
        return True
    return False


async def step(factory: Any, redis: Any, settings: Settings, instance: str,
               now: datetime | None = None,
               last_state: dict[str, Any] | None = None) -> dict[str, Any] | None:
    """One Guardian run. None when another instance leads. ``last_state``: this
    instance's previous run (so a Redis outage does not repeat alerts); otherwise the
    state kept in the last report."""
    from nexus_db import transaction
    from nexus_notify.monitor import disk_usage
    from nexus_risk.kill_switch import KillSwitch

    now = now or utcnow()
    try:
        if not await lead(redis, instance):
            return None
    except Exception as exc:  # Redis down: still check and record (the outage itself)
        log.error("guardian_lock_failed", error=type(exc).__name__)

    db: dict[str, Any] | None = None
    db_error = redis_error = None
    try:
        def load() -> dict[str, Any]:
            with transaction(factory) as s:
                return collect_db(s, settings, now)
        db = await asyncio.wait_for(asyncio.to_thread(load), 30)
    except Exception as exc:
        db_error = type(exc).__name__
        log.error("guardian_db_failed", error=f"{db_error}: {exc}"[:300])
    rd: dict[str, Any] | None = None
    try:
        rd = await asyncio.wait_for(collect_redis(redis, settings), 15)
    except Exception as exc:
        redis_error = type(exc).__name__
        log.error("guardian_redis_failed", error=f"{redis_error}: {exc}"[:300])

    kill = KillSwitch(redis)
    restored = False
    if rd is not None and db is not None and db.get("kill_durable") and rd.get("kill") is None:
        with contextlib.suppress(Exception):
            restored = await kill.restore(db["kill_durable"])
            rd["kill"] = await kill.status()
    try:
        disk: float | None = disk_usage()
    except OSError:
        disk = None
    checks = build_checks(db, db_error, rd, redis_error, settings, now, restored=restored,
                          disk=disk, memory=container_memory())
    prev = last_state if last_state is not None else \
        ((rd or {}).get("previous") or {}).get("state") or {}
    state, alerts = g.advance(prev, checks, now)

    reasons = g.shutdown_reasons(checks)
    shutdown, actions = None, []
    if restored:
        actions.append("kill switch restored from the audit log")
    if reasons and redis_error is None and (rd or {}).get("kill") is None:
        shutdown = "Guardian emergency shutdown: " + "; ".join(reasons)
        try:
            await kill.engage(shutdown, actor=ACTOR)
            actions.append(shutdown)
        except Exception as exc:
            log.critical("guardian_shutdown_failed", error=type(exc).__name__)
            shutdown = None
    if alerts or shutdown:
        try:
            await asyncio.to_thread(_record, factory, alerts, shutdown, trader_mode(settings))
        except Exception as exc:  # the database may be the failing component
            log.error("guardian_record_failed", error=type(exc).__name__)
    for c, kind in alerts:
        log.warning("guardian_alert", domain=c.domain, check=c.name, status=c.status,
                    kind=kind, detail=c.detail[:300])
        if redis_error is None:
            with contextlib.suppress(Exception):
                await redis.xadd(events.NOTIFY_STREAM, events.encode({
                    "type": "guardian_alert", "domain": c.domain, "check": c.name,
                    "status": c.status, "kind": kind, "detail": c.detail}),
                    maxlen=events.STREAM_MAXLEN, approximate=True)

    report = {"at": now.isoformat(), **g.summary(checks),
              "checks": [{**c.as_dict(), "since": state[c.key]["since"]} for c in checks],
              "shutdown_conditions": reasons, "actions": actions, "state": state,
              "alerts_sent": len(alerts), "interval_seconds": INTERVAL}
    if redis_error is None:
        with contextlib.suppress(Exception):
            await redis.set(REPORT_KEY, json.dumps(report, default=str), ex=1800)
            await redis.set(events.heartbeat_key("guardian"), now.isoformat(), ex=600)
    return report


async def serve(settings: Settings, stop: asyncio.Event) -> None:
    from nexus_common.redisutil import connect as connect_redis
    from nexus_db import make_engine, make_session_factory

    engine = make_engine(settings.database_url.get_secret_value(), pooled=False)
    factory = make_session_factory(engine)
    redis = connect_redis(settings.redis_url.get_secret_value(), max_connections=1)
    instance = uuid.uuid4().hex
    last_state: dict[str, Any] | None = None
    try:
        while not stop.is_set():
            try:
                rep = await step(factory, redis, settings, instance, last_state=last_state)
                last_state = rep["state"] if rep is not None else None
                if rep is not None and rep["overall"] != g.OK:
                    log.info("guardian_run", overall=rep["overall"],
                             problems=[c["name"] for c in rep["checks"]
                                       if c["status"] != g.OK][:10])
            except Exception as exc:
                log.error("guardian_failed", error=f"{type(exc).__name__}: {exc}"[:300])
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(stop.wait(), timeout=INTERVAL)
    finally:
        with contextlib.suppress(Exception):
            holder = await redis.get(LEADER_KEY)
            if (holder.decode() if isinstance(holder, bytes) else holder) == instance:
                await redis.delete(LEADER_KEY)       # hand over at once on a clean stop
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


if __name__ == "__main__":   # pragma: no cover
    main()
