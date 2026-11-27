"""Strategy-module registry and forward OBSERVATION record (unified brief, Phase 3).

* ``sync_registry``: one ``strategy_modules`` row per module, with its settings (from the
  code) and its test result (from the committed results file). The owner's switch
  (``activation``) is never changed here.
* The worker: for each module the owner switched to OBSERVE, it computes signals on the
  candles NEXUS stores and records every signal of a bar that closed after the switch
  (``strategy_module_signals``, never duplicated). Each record is then resolved with
  ``nexus_strategies.modules.sim``, the same code as the historical test: entry at the
  next bar's open, the fixed stop, the 2R target, the time stop.

OBSERVE places no order, paper or real. It is a record of what a module would have done.

Data:
* crypto: the Binance candles NEXUS stores (5m, 1h, 4h);
* forex: the Exness bars the read-only bridge sends (M5 kept 45 days, H1 400 days); the FX
  day is built from H1. Forex costs use the spread quoted by the bridge when the signal is
  recorded, plus 0.1 pip slippage.

Differences from the historical test, stated on the dashboard:
* swap is not charged;
* the forex swing module's EMA200 is computed on about 285 FX days, not 10 years;
* the spread is the one at the signal, not each bar's.
"""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from nexus_common.logging import get_logger
from nexus_common.timeutil import utcnow
from nexus_config.settings import TIMEFRAME_SECONDS
from nexus_strategies import modules as mods
from nexus_strategies.frames import to_frame
from nexus_strategies.modules import sim
from nexus_strategies.modules.base import FX_DAY_OFFSET, resample

log = get_logger("nexus.research.module_observer")

RESULTS = Path(__file__).resolve().parent / "results"
RESULTS_GLOB = "strategy_modules_[0-9][0-9][0-9][0-9]-[0-9][0-9].json"
BEFORE_GLOB = "strategy_modules_before_costs_[0-9][0-9][0-9][0-9]-[0-9][0-9].json"
PREREG = "docs/research/2026-11-27-strategy-modules-preregistration.md"
STATUS_KEY = "nexus:modules:observer:status"
FX_STATUS_KEY = "nexus:broker:exness:status"
INTERVAL = 300
FX_SLIPPAGE_PIPS = 0.1
CRYPTO_FEE, CRYPTO_SLIPPAGE = 0.001, 0.0005
# Days of stored candles per timeframe each module reads (several times its longest
# average, so the averages have settled).
NEEDS: dict[str, dict[str, int]] = {
    "fx_scalp_pullback": {"5m": 3, "1h": 15},
    "fx_intraday_breakout": {"5m": 2, "1h": 200},
    "fx_swing_trend": {"1h": 400},
    "crypto_scalp_volume": {"5m": 3, "1h": 15},
    "crypto_breakout_retest": {"1h": 60, "4h": 200},
}
OBSERVABLE = tuple(NEEDS)
DROP_AFTER = timedelta(minutes=15)    # no execution bar this long after the deadline


def _latest(pattern: str) -> Path | None:
    files = sorted(RESULTS.glob(pattern))
    return files[-1] if files else None


def load_results() -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    main, before = _latest(RESULTS_GLOB), _latest(BEFORE_GLOB)
    return (json.loads(main.read_text()) if main else None,
            json.loads(before.read_text()) if before else None)


def validation_summary(code: str, results: dict[str, Any] | None,
                       before: dict[str, Any] | None) -> dict[str, Any] | None:
    if results is None or code not in results.get("modules", {}):
        return None
    res = results["modules"][code]
    spec = mods.get(code)
    if code == "crypto_swing_trend":
        rep = res["replication"]
        st = rep.get("strategy", {})
        return {"passed": bool(rep.get("passed")), "evidence": spec.evidence,
                "note": spec.note, "run_at": results.get("run_at"),
                "preregistration": "docs/research/2026-11-23-crypto-trends-preregistration.md",
                "sharpe": st.get("sharpe"), "max_drawdown": st.get("max_drawdown"),
                "cagr": st.get("cagr"), "gates": rep.get("gates")}
    m, m2 = res["metrics"], res["metrics_2x_costs"]
    return {"passed": bool(res.get("passed")), "evidence": spec.evidence, "note": spec.note,
            "run_at": results.get("run_at"), "preregistration": PREREG,
            "period": results["periods"][spec.market],
            "trades": m.get("trades"), "win_rate": m.get("win_rate"),
            "mean_r": m.get("mean_r"), "profit_factor": m.get("profit_factor"),
            "max_drawdown": m.get("max_drawdown"), "p_value": m.get("p_value"),
            "mean_r_2x_costs": m2.get("mean_r"), "gates": res.get("gates"),
            "years": {y: v["mean_r"] for y, v in m.get("years", {}).items()},
            "mean_r_before_costs": ((before or {}).get("modules", {}).get(code) or {})
            .get("mean_r")}


def sync_registry(s: Any) -> int:
    """Insert missing modules (OFF) and refresh settings and test results. Returns the
    number of modules. Never changes the owner's switch."""
    from nexus_db import models

    results, before = load_results()
    for code, spec in mods.MODULES.items():
        row = s.get(models.StrategyModule, code)
        if row is None:
            row = models.StrategyModule(code=code, activation="OFF")
            s.add(row)
        row.name, row.market, row.style = spec.name, spec.market, spec.style
        row.evidence = spec.evidence
        row.config = json.loads(json.dumps(spec.config_dict(), default=str))
        row.validation = validation_summary(code, results, before)
    s.flush()
    return len(mods.MODULES)


# ------------------------------------------------------------------------------ data


def _venue(spec: mods.ModuleSpec) -> str:
    return "exness" if spec.market == "forex" else "binance"


def db_symbol(spec: mods.ModuleSpec, symbol: str) -> str:
    """NEXUS stores Binance pairs as "BTC/USDT" and Exness symbols by base name."""
    if spec.market == "crypto" and symbol.endswith("USDT") and "/" not in symbol:
        return f"{symbol[:-4]}/USDT"
    return symbol


def _pip(sym: str) -> float:
    return 0.01 if sym.endswith("JPY") else 0.0001


def load_frames(s: Any, spec: mods.ModuleSpec, symbol: str, now: datetime,
                spread: float | None) -> dict[str, pd.DataFrame] | None:
    """The module's frames from stored candles, or None when data or the forex spread is
    missing (nothing is estimated)."""
    from nexus_db import repo

    if spec.market == "forex" and spread is None:
        return None
    try:
        pid = repo.pair_id(s, _venue(spec), db_symbol(spec, symbol))
    except LookupError:
        return None
    frames: dict[str, pd.DataFrame] = {}
    for tf, days in NEEDS[spec.code].items():
        df = to_frame(repo.load_candles(s, pid, tf, start=now - timedelta(days=days)))
        if df.empty:
            return None
        df.index = pd.DatetimeIndex(df.index).as_unit("ns")
        df.index.name = None
        if spec.market == "forex":
            df["spread"] = float(spread or 0.0)
        frames[tf] = df
    if spec.market == "forex":
        frames["1d"] = resample(frames["1h"], "1D", offset=FX_DAY_OFFSET, min_bars=6)
    if spec.code == "crypto_breakout_retest" and symbol != "BTCUSDT":
        try:
            btc = repo.pair_id(s, "binance", db_symbol(spec, "BTCUSDT"))
        except LookupError:
            return None
        b4 = to_frame(repo.load_candles(s, btc, "4h", start=now - timedelta(days=200)))
        b4.index = pd.DatetimeIndex(b4.index).as_unit("ns")
        b4.index.name = None
        frames["btc_4h"] = b4
    return frames


def costs_for(spec: mods.ModuleSpec, symbol: str) -> sim.Costs:
    if spec.market == "forex":
        return sim.Costs("forex", slippage=FX_SLIPPAGE_PIPS * _pip(symbol))
    return sim.Costs("crypto", slippage=CRYPTO_SLIPPAGE, fee=CRYPTO_FEE)


def scan_due(code: str, now: datetime) -> bool:
    """Only scan when the module's signal bars can have closed (saves database load)."""
    mins = now.hour * 60 + now.minute
    if code == "fx_intraday_breakout":
        return 7 * 60 <= mins <= 11 * 60 + 30
    if code == "fx_swing_trend":
        return now.hour in (22, 23)
    if code == "crypto_breakout_retest":
        return now.minute < 15
    return True


# --------------------------------------------------------------------------- records


def new_signals(spec: mods.ModuleSpec, frames: dict[str, pd.DataFrame], since: datetime,
                now: datetime) -> pd.DataFrame:
    """Signals of bars that opened at or after ``since`` and have closed by ``now``."""
    assert spec.signals is not None
    sig = spec.signals(frames, spec.config)
    if sig.empty:
        return sig
    close = sig.index + pd.Timedelta(seconds=TIMEFRAME_SECONDS[spec.signal_tf])
    return sig[(sig.index >= pd.Timestamp(since)) & (close <= pd.Timestamp(now))]


def resolve(spec: mods.ModuleSpec, rec: Any, bars_df: pd.DataFrame, now: datetime
            ) -> dict[str, Any] | None:
    """The record's update from the bars since its signal: None while nothing is known
    yet; otherwise the fields to set (status open with the fill, closed, or dropped)."""
    period = TIMEFRAME_SECONDS[spec.signal_tf]
    close = pd.Timestamp(rec.signal_time) + pd.Timedelta(seconds=period)
    deadline = close + spec.max_entry_delay
    df = bars_df.copy()
    if spec.market == "forex":
        df["spread"] = float(rec.spread or 0)
    if df.empty:
        if pd.Timestamp(now) > deadline + DROP_AFTER:
            return {"status": "dropped", "note": "no execution bar in time (data gap)"}
        return None
    bars = sim.Bars.from_frame(df, TIMEFRAME_SECONDS[spec.exec_tf])
    i = sim.first_entry(bars, int(close.value), int(spec.max_entry_delay.total_seconds() * 1e9))
    if i is None:
        if pd.Timestamp(now) > deadline + DROP_AFTER:
            return {"status": "dropped", "note": "no execution bar in time (data gap)"}
        return None
    exit_at = int(pd.Timestamp(rec.exit_at).value) if rec.exit_at is not None else None
    tr = sim.simulate(bars, i, int(rec.side), float(rec.stop), float(rec.rr),
                      int(rec.max_bars), costs_for(spec, rec.symbol), exit_at=exit_at)
    if tr is None:
        return {"status": "dropped", "note": "the entry bar opened beyond the stop"}

    def ts(ns: int) -> datetime:
        out: datetime = pd.Timestamp(ns, tz="UTC").to_pydatetime()
        return out

    out: dict[str, Any] = {"entry_time": ts(tr.entry_time), "entry": Decimal(repr(tr.entry)),
                           "target": Decimal(repr(tr.target)), "risk": Decimal(repr(tr.risk))}
    if tr.reason == "end_of_data":
        return out | {"status": "open"}
    return out | {"status": "closed", "exit_time": ts(tr.exit_time),
                  "exit_price": Decimal(repr(tr.exit)), "reason": tr.reason,
                  "net_r": Decimal(repr(round(tr.net_r, 6)))}


def step_sync(factory: Any, spreads: dict[str, float], now: datetime) -> dict[str, int]:
    from nexus_db import models, repo, transaction
    from sqlalchemy import select
    from sqlalchemy.dialects.postgresql import insert

    stats = {"modules": 0, "recorded": 0, "closed": 0, "dropped": 0}
    with transaction(factory) as s:
        sync_registry(s)
        active = list(s.scalars(select(models.StrategyModule).where(
            models.StrategyModule.activation == "OBSERVE",
            models.StrategyModule.code.in_(OBSERVABLE))))
        stats["modules"] = len(active)
        for row in active:
            spec = mods.get(row.code)
            if row.activated_at is None or not scan_due(row.code, now):
                continue
            for sym in spec.symbols:
                spread = spreads.get(sym)
                frames = load_frames(s, spec, sym, now, spread)
                if frames is None:
                    continue
                sig = new_signals(spec, frames, row.activated_at, now)
                for t, r in sig.iterrows():
                    res = s.execute(insert(models.StrategyModuleSignal).values(
                        module_code=row.code, symbol=sym, signal_time=t.to_pydatetime(),
                        side=int(r["side"]), stop=Decimal(repr(float(r["stop"]))),
                        rr=Decimal(repr(float(r["rr"]))), max_bars=int(r["max_bars"]),
                        exit_at=None if pd.isna(r["exit_at"]) else r["exit_at"].to_pydatetime(),
                        spread=Decimal(repr(spread)) if spread is not None else None,
                    ).on_conflict_do_nothing(
                        index_elements=["module_code", "symbol", "signal_time"])
                        .returning(models.StrategyModuleSignal.id))
                    stats["recorded"] += int(res.first() is not None)
        open_recs = list(s.scalars(select(models.StrategyModuleSignal).where(
            models.StrategyModuleSignal.status == "open")))
        for rec in open_recs:
            spec = mods.get(rec.module_code)
            try:
                pid = repo.pair_id(s, _venue(spec), db_symbol(spec, rec.symbol))
            except LookupError:
                continue
            bars_df = to_frame(repo.load_candles(s, pid, spec.exec_tf, start=rec.signal_time))
            bars_df.index = pd.DatetimeIndex(bars_df.index).as_unit("ns")
            upd = resolve(spec, rec, bars_df, now)
            if upd is None:
                continue
            for k, v in upd.items():
                setattr(rec, k, v)
            if upd["status"] != "open":
                rec.resolved_at = now
                stats["closed" if upd["status"] == "closed" else "dropped"] += 1
    return stats


async def fx_spreads(redis: Any) -> dict[str, float]:
    raw = await redis.get(FX_STATUS_KEY)
    if not raw:
        return {}
    out = {}
    for x in json.loads(raw).get("symbols", []):
        try:
            bid, ask = float(x["bid"]), float(x["ask"])
        except (KeyError, TypeError, ValueError):
            continue
        if np.isfinite(bid) and np.isfinite(ask) and ask >= bid > 0:
            out[str(x["base_name"]).upper()] = ask - bid
    return out


async def step(factory: Any, redis: Any, now: datetime | None = None) -> dict[str, int]:
    now = now or utcnow()
    spreads = await fx_spreads(redis)
    stats = await asyncio.to_thread(step_sync, factory, spreads, now)
    await redis.set(STATUS_KEY, json.dumps({"at": now.isoformat(), **stats}), ex=3600)
    if stats["recorded"] or stats["closed"] or stats["dropped"]:
        log.info("module_observer_step", **stats)
    return stats


async def serve(settings: Any, stop: asyncio.Event) -> None:
    from nexus_common.redisutil import connect as connect_redis
    from nexus_db import make_engine, make_session_factory

    engine = make_engine(settings.database_url.get_secret_value(), pooled=False)
    factory = make_session_factory(engine)
    redis = connect_redis(settings.redis_url.get_secret_value(), max_connections=1)
    try:
        while not stop.is_set():
            try:
                await step(factory, redis)
            except Exception as exc:
                log.error("module_observer_failed", error=f"{type(exc).__name__}: {exc}"[:300])
            now = datetime.now(UTC)
            wait = INTERVAL - (now.timestamp() % INTERVAL) + 40   # after the 5m bars land
            try:
                await asyncio.wait_for(stop.wait(), timeout=wait)
            except TimeoutError:
                pass
    finally:
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


if __name__ == "__main__":  # pragma: no cover
    asyncio.run(main())
