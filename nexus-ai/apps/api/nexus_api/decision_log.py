"""Decision log: records the signal-fusion decision for every pair after each closed 15m
bar (brief Phase 13, "decision logs").

A row is written when anything material changed since the pair's previous row (the
decision, strategy, regime, risk state, blockers, setups, eligible strategies or the
model's availability), and otherwise at least once an hour, so the log stays small
enough for the free database (about 50 rows a day for two pairs). Rows are append-only;
rows older than 180 days are pruned once a day.

The log records what the decision layer concluded and why. Orders are placed only by the
trader after the Risk Manager approves; the API joins each row with any trader signal
for the same pair and bar to show the execution result.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any

from nexus_common import memory
from nexus_common.logging import get_logger
from nexus_common.timeutil import utcnow
from nexus_config import Settings
from nexus_db import models, transaction
from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session, sessionmaker

log = get_logger("nexus.decision_log")

HEARTBEAT = timedelta(hours=1)
RETENTION = timedelta(days=180)
BAR = timedelta(minutes=15)
MODE = "paper"    # the fusion view reads the paper risk state


def compact_strategies(sources: dict[str, Any]) -> dict[str, Any]:
    """Every strategy's selector verdict, kept short: eligible or the first reason why
    not, plus robust expectancy and sample size; and which strategies had a setup."""
    out = {code: {"eligible": v.get("eligible"),
                  "why_not": (v.get("why_not") or [None])[0],
                  "robust_expectancy": v.get("robust_expectancy"),
                  "oos_trades_in_regime": v.get("oos_trades_in_regime")}
           for code, v in (sources.get("strategies") or {}).items()}
    return {"strategies": out, "setups_now": list(sources.get("setups_now") or [])}


def fingerprint(sig: dict[str, Any]) -> str:
    """What makes a decision materially different from the previous one."""
    strategies = (sig.get("sources") or {}).get("strategies") or {}
    key = {"decision": sig.get("decision"), "strategy": sig.get("strategy"),
           "regime": sig.get("regime"),
           "risk": (sig.get("context") or {}).get("risk_state"),
           "blockers": sorted(sig.get("blockers") or []),
           "setups": sorted((sig.get("sources") or {}).get("setups_now") or []),
           "eligible": sorted(c for c, v in strategies.items() if v.get("eligible")),
           "model": ((sig.get("sources") or {}).get("model") or {}).get("available")}
    return hashlib.sha256(json.dumps(key, sort_keys=True, default=str).encode()).hexdigest()


def should_record(prev: tuple[datetime, str] | None, bar_time: datetime, fp: str) -> bool:
    """``prev``: (bar_time, fingerprint) of the pair's last row."""
    if prev is None:
        return True
    prev_bar, prev_fp = prev
    if bar_time <= prev_bar:
        return False                     # this bar (or an older one) is already logged
    return fp != prev_fp or bar_time - prev_bar >= HEARTBEAT


def _dec(v: Any) -> Decimal | None:
    return None if v is None else Decimal(str(v))


def row_from_signal(sig: dict[str, Any], *, decided_at: datetime) -> dict[str, Any] | None:
    snap = sig.get("snapshot")
    if not snap:
        return None                       # no stored candles: nothing to identify
    bar_time = datetime.fromisoformat(snap["bar_open"])
    ctx = sig.get("context") or {}
    sources = sig.get("sources") or {}
    blockers = list(sig.get("blockers") or [])
    reasons = list(sig.get("reasons") or [])
    rejection = None
    if sig.get("decision") in ("NO_TRADE", "WAIT"):
        rejection = blockers[0] if blockers else next(
            (r for r in reasons if not r.startswith("SHORT not available")), None)
    return {
        "mode": MODE, "exchange": snap["exchange"], "symbol": sig["symbol"],
        "bar_time": bar_time, "decided_at": decided_at,
        "snapshot_id": (f"{snap['exchange']}:{sig['symbol']}:{snap['timeframe']}@"
                        f"{snap['bar_open']}|book@{snap.get('book_at') or 'none'}")[:160],
        "decision": sig["decision"], "strategy": sig.get("strategy"),
        "regime": sig.get("regime"), "risk_state": ctx.get("risk_state"),
        "probability": _dec(sig.get("probability")),
        "expected_return": _dec(sig.get("expected_return")),
        "features": {"context": ctx, "mtf_scores": sig.get("mtf_scores") or {},
                     "regimes_by_timeframe": sig.get("regimes_by_timeframe") or {},
                     "entry": sig.get("entry"), "stop": sig.get("stop"),
                     "target": sig.get("target"), "reward_risk": sig.get("reward_risk")},
        "models_used": dict(sources.get("model") or {}),
        "strategy_signals": compact_strategies(sources),
        "risk_checks": {"risk_state": ctx.get("risk_state"),
                        "entries_allowed": ctx.get("entries_allowed"),
                        "blockers": blockers, "conflicts": list(sig.get("conflicts") or [])},
        "reasons": {"reasons": reasons, "fingerprint": fingerprint(sig)},
        "rejection_reason": rejection,
    }


def last_rows(s: Session) -> dict[tuple[str, str], tuple[datetime, str]]:
    """Latest (bar_time, fingerprint) per (exchange, symbol)."""
    out: dict[tuple[str, str], tuple[datetime, str]] = {}
    q = (select(models.DecisionLog.exchange, models.DecisionLog.symbol,
                models.DecisionLog.bar_time, models.DecisionLog.reasons)
         .where(models.DecisionLog.mode == MODE)
         .order_by(models.DecisionLog.bar_time.desc()).limit(500))
    for ex, sym, bar, reasons in s.execute(q):
        out.setdefault((ex, sym), (bar, (reasons or {}).get("fingerprint", "")))
    return out


def store(factory: sessionmaker[Session], signals: list[dict[str, Any]],
          now: datetime) -> int:
    """Write the rows that ``should_record`` allows; idempotent per pair and bar."""
    written = 0
    with transaction(factory) as s:
        prev = last_rows(s)
        for sig in signals:
            row = row_from_signal(sig, decided_at=now)
            if row is None:
                continue
            fp = row["reasons"]["fingerprint"]
            if not should_record(prev.get((row["exchange"], row["symbol"])),
                                 row["bar_time"], fp):
                continue
            new_id = s.scalar(insert(models.DecisionLog).values(**row)
                              .on_conflict_do_nothing(constraint="one_decision_per_bar")
                              .returning(models.DecisionLog.id))
            written += new_id is not None
    return written


def prune(factory: sessionmaker[Session], now: datetime) -> int:
    with transaction(factory) as s:
        res = s.execute(delete(models.DecisionLog).where(
            models.DecisionLog.decided_at < now - RETENTION - timedelta(minutes=5)))
        return int(getattr(res, "rowcount", 0) or 0)


async def step(factory: sessionmaker[Session], redis: Any, settings: Settings) -> int:
    from nexus_api import fusion_view

    values = await fusion_view.redis_inputs(redis, settings)

    def run() -> dict[str, Any]:
        with transaction(factory) as s:
            return fusion_view.compute(s, values, settings)

    out = await asyncio.to_thread(run)
    # Share with the dashboard endpoint so it does not compute the same thing again.
    await redis.set("nexus:decision:fusion", json.dumps(out, default=str), ex=60)
    written = await asyncio.to_thread(store, factory, out["signals"], utcnow())
    memory.release()
    return written


async def serve(settings: Settings, stop: asyncio.Event) -> None:
    from nexus_common.redisutil import connect as connect_redis
    from nexus_db import make_engine, make_session_factory

    engine = make_engine(settings.database_url.get_secret_value())
    factory = make_session_factory(engine)
    redis = connect_redis(settings.redis_url.get_secret_value())
    last_prune: datetime | None = None
    try:
        while not stop.is_set():
            now = utcnow()
            try:
                written = await step(factory, redis, settings)
                if written:
                    log.info("decisions_logged", rows=written)
                if last_prune is None or now - last_prune > timedelta(days=1):
                    pruned = await asyncio.to_thread(prune, factory, now)
                    last_prune = now
                    if pruned:
                        log.info("decisions_pruned", rows=pruned)
            except Exception as exc:   # keep logging; a failed bar is simply not logged
                log.error("decision_log_failed", error=f"{type(exc).__name__}: {exc}"[:300])
            # Run 45 s after each 15m bar closes (candles are stored by then).
            nxt = now.replace(minute=now.minute - now.minute % 15, second=45,
                              microsecond=0) + BAR
            try:
                await asyncio.wait_for(stop.wait(), timeout=(nxt - utcnow()).total_seconds())
            except TimeoutError:
                pass
    finally:
        await redis.aclose()
        engine.dispose()


def main() -> None:   # pragma: no cover - process entry point
    import signal

    from nexus_config import get_settings

    async def run() -> None:
        stop = asyncio.Event()
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGTERM, signal.SIGINT):
            loop.add_signal_handler(sig, stop.set)
        await serve(get_settings(), stop)

    asyncio.run(run())


if __name__ == "__main__":
    main()
