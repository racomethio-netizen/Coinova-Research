"""The command centre (unified brief, section J): every element of the brief's dashboard
on one page, built only from what NEXUS already records. Nothing is estimated; a missing
source is shown as missing.

Sources:
* forex performance: the Exness DEMO account (last sync) and its hourly equity from the
  syncs;
* crypto performance: the crypto PAPER book (``portfolio_report``) and its equity
  snapshots;
* six strategies: each module's pre-registered test and its forward observation record;
* market regime and BUY/SELL/NO TRADE: the strategy orchestrator's board (every 5
  minutes);
* signal confidence and calibration: the evidence behind each signal (its module's
  test), tested vs forward win rates, and the ML filters' measured calibration;
* open positions, P&L, drawdown and exposure: the cross-market risk book (every 5
  minutes) and the two accounts;
* model health and execution status: the Guardian (every minute) and the execution
  intents.
"""

from __future__ import annotations

import json
import math
from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

CACHE_KEY = "nexus:command-center"
CACHE_SECONDS = 30
CURVE_DAYS = 30
MIN_FORWARD_FOR_CALIBRATION = 20     # forward trades before tested vs forward is judged
WARN_SHARE = 0.8                     # a limit 80% used is shown as a warning


def _d(x: Any) -> Decimal | None:
    if x is None:
        return None
    try:
        return Decimal(str(x))
    except (InvalidOperation, ValueError):
        return None


def _s(x: Decimal | None, q: str = "0.01") -> str | None:
    return None if x is None else str(x.quantize(Decimal(q)))


def _age(at: str | None, now: datetime) -> float | None:
    if not at:
        return None
    try:
        return (now - datetime.fromisoformat(at)).total_seconds()
    except ValueError:
        return None


def max_drawdown(values: list[float]) -> float | None:
    """Largest fall from a running peak, as a fraction (None for fewer than 2 points)."""
    if len(values) < 2:
        return None
    peak, worst = values[0], 0.0
    for v in values:
        peak = max(peak, v)
        if peak > 0:
            worst = max(worst, (peak - v) / peak)
    return worst


# --------------------------------------------------------------------------- performance
def forex_performance(demo: dict[str, Any] | None, history: list[dict[str, Any]],
                      now: datetime) -> dict[str, Any]:
    out: dict[str, Any] = {"label": "DEMO", "account": "Exness demo (MetaTrader 5)",
                           "curve": history}
    if not demo:
        return out | {"connected": False,
                      "note": "No sync yet: the Exness demo trader on your PC is not running."}
    eq, bal = _d(demo.get("equity")), _d(demo.get("balance"))
    lim = demo.get("limits") or {}
    day0, week0, peak = _d(lim.get("day_start")), _d(lim.get("week_start")), _d(lim.get("peak"))
    age = _age(demo.get("at"), now)
    dd = (peak - eq) / peak if peak and eq is not None and peak > 0 else None
    return out | {
        "connected": age is not None and age < 600, "at": demo.get("at"),
        "currency": demo.get("currency"), "equity": _s(eq), "balance": _s(bal),
        "open_pnl": _s(eq - bal) if eq is not None and bal is not None else None,
        "day_pnl": _s(eq - day0) if eq is not None and day0 else None,
        "week_pnl": _s(eq - week0) if eq is not None and week0 else None,
        "drawdown": _s(max(dd, Decimal(0)), "0.0001") if dd is not None else None,
        "max_drawdown_recorded": (None if (m := max_drawdown(
            [float(h["equity"]) for h in history])) is None else round(m, 4)),
        "positions": len(demo.get("positions") or []),
        "note": "A demo account: virtual money. Equity history is NEXUS's hourly record "
                "of the syncs; MT5 holds the full trade history."}


def crypto_performance(report: dict[str, Any] | None,
                       curve: list[dict[str, Any]]) -> dict[str, Any]:
    out: dict[str, Any] = {"label": "PAPER", "account": "Crypto paper book (Binance prices)",
                           "curve": curve}
    if report is None or report.get("equity") is None:
        return out | {"note": "No paper equity recorded yet."}
    eq, init = _d(report["equity"]), _d(report.get("initial_capital"))
    return out | {
        "equity": _s(eq), "initial_capital": _s(init),
        "total_pnl": _s(eq - init) if eq is not None and init else None,
        "total_return": _s((eq - init) / init, "0.0001") if eq is not None and init else None,
        **{k: report.get(k) for k in ("realized_pnl", "unrealized_pnl", "fees_paid",
                                      "closed_trades", "win_rate", "profit_factor",
                                      "expectancy", "max_drawdown", "current_drawdown",
                                      "sharpe", "sharpe_note", "as_of")},
        "note": "Simulated fills on real prices: not real profits."}


# --------------------------------------------------------------------------- signals
def signals(board_doc: dict[str, Any] | None, now: datetime) -> dict[str, Any]:
    if not board_doc:
        return {"rows": [], "counts": {}, "stale": True,
                "note": "The strategy orchestrator has not published a board yet."}
    rows = []
    for v in board_doc.get("board") or []:
        ex = v.get("execution") or {}
        rows.append({
            "symbol": v.get("symbol"), "market": v.get("market"), "action": v.get("action"),
            "regime": v.get("regime"), "module": v.get("module"), "expected_r": v.get("expected_r"),
            "reward_risk": v.get("reward_risk"), "entry_ref": v.get("entry_ref"),
            "stop": v.get("stop"), "target": v.get("target"),
            "why": (v.get("blockers") or [])[:3] or (v.get("reasons") or [])[:2],
            "risk_approved": (v.get("risk") or {}).get("approved"),
            "order": ("sent" if ex.get("sent") else ex.get("why")) if ex else None})
    age = _age(board_doc.get("at"), now)
    return {"at": board_doc.get("at"), "age_seconds": age,
            "stale": age is None or age > 900, "rows": rows,
            "counts": {a: sum(1 for r in rows if r["action"] == a)
                       for a in ("BUY", "SELL", "NO_TRADE")}}


# --------------------------------------------------------------------------- strategies
def paper_orders(passed: bool | None, market: str, switches: dict[str, str]) -> str:
    """Whether the orchestrator can turn this module's signals into paper/demo orders: only
    a module that passed its test, with its market's switch at PAPER (the module's own
    switch only controls the OBSERVE record)."""
    sw = switches.get(market)
    if not passed:
        return "no: it did not pass its test"
    if sw != "PAPER":
        return f"no: the {market} switch is {sw or 'not set'}"
    return f"yes: passed its test, {market} switch PAPER"


def strategies(rows: list[dict[str, Any]], guardian: dict[str, Any] | None,
               switches: dict[str, str] | None = None) -> list[dict[str, Any]]:
    """``rows``: per module code, name, market, style, activation, validation, forward."""
    health = {c["name"].removeprefix("module:"): c for c in (guardian or {}).get("checks") or []
              if c.get("domain") == "models" and c.get("name", "").startswith("module:")}
    out = []
    for m in rows:
        v, f = m.get("validation") or {}, m.get("forward") or {}
        h = health.get(m["code"])
        out.append({
            "code": m["code"], "name": m["name"], "market": m["market"], "style": m["style"],
            "activation": m.get("activation") or "OFF",
            "paper_orders": paper_orders(v.get("passed"), m["market"], switches or {}),
            "test": {"passed": v.get("passed"),
                     **{k: v.get(k) for k in ("trades", "win_rate", "mean_r", "profit_factor",
                                              "p_value", "max_drawdown", "sharpe", "cagr",
                                              "period")}},
            "forward": {k: f.get(k) for k in ("signals", "open", "closed", "win_rate", "mean_r",
                                              "total_r")},
            "health": {"status": h["status"], "detail": h["detail"]} if h else None})
    return out


# --------------------------------------------------------------------------- confidence
def calibration(strats: list[dict[str, Any]], ml: dict[str, Any] | None,
                sigs: dict[str, Any]) -> dict[str, Any]:
    """How much a signal can be trusted, from measurements only."""
    by_code = {s["code"]: s for s in strats}
    evidence = []
    for r in sigs.get("rows") or []:
        if r["action"] not in ("BUY", "SELL") or not r.get("module"):
            continue
        t = (by_code.get(r["module"]) or {}).get("test") or {}
        evidence.append({"symbol": r["symbol"], "action": r["action"], "module": r["module"],
                         "expected_r": r.get("expected_r"), "test_trades": t.get("trades"),
                         "test_win_rate": t.get("win_rate"), "p_value": t.get("p_value"),
                         "sharpe": t.get("sharpe")})
    mods = []
    for s in strats:
        tw, fw = s["test"].get("win_rate"), s["forward"].get("win_rate")
        n = int(s["forward"].get("closed") or 0)
        if tw is None:
            verdict, gap = "no tested win rate (a portfolio rule)", None
        elif n < MIN_FORWARD_FOR_CALIBRATION or fw is None:
            verdict, gap = (f"{n} forward trades: too few to judge "
                            f"(needs {MIN_FORWARD_FOR_CALIBRATION})"), None
        else:
            gap = fw - tw
            se = math.sqrt(max(tw * (1 - tw), 1e-9) / n)
            verdict = ("in line with the test" if abs(gap) <= 2 * se else
                       "worse than tested" if gap < 0 else "better than tested")
        mods.append({"code": s["code"], "name": s["name"], "tested_win_rate": tw,
                     "forward_win_rate": fw, "forward_trades": n, "gap": gap,
                     "verdict": verdict})
    ml_rows = []
    for code, m in ((ml or {}).get("modules") or {}).items():
        c = m.get("classification") or {}
        ml_rows.append({"module": code, "name": m.get("name"), "auc": c.get("roc_auc"),
                        "auc_by_model": m.get("auc_by_model_raw"), "brier": c.get("brier"),
                        "brier_base_rate": c.get("brier_ref"),
                        "brier_skill": c.get("brier_skill"), "ece": c.get("ece"),
                        "test_years": (ml or {}).get("test_years"), "passed": m.get("passed"),
                        "used": False})
    return {
        "evidence": evidence, "modules": mods, "ml": ml_rows,
        "ml_not_tested": (ml or {}).get("not_tested") or {},
        "ml_run_at": (ml or {}).get("run_at"),
        "explanation": (
            "NEXUS shows no probability for a signal, because no probability model passed "
            "its out-of-sample test: the ML filters (LightGBM, XGBoost, CatBoost, "
            "isotonic-calibrated) scored an AUC of about 0.5 and a negative Brier skill, "
            "no better than the base rate. A signal's confidence is the evidence of the "
            "module behind it: its tested mean result after costs, its number of trades and "
            "its significance. Calibration is checked by comparing each module's tested win "
            "rate with its forward record.")}


# --------------------------------------------------------------------------- risk
def _limit_row(name: str, value: float | None, limit: float | None, fmt: str) -> dict[str, Any]:
    used = value / limit if value is not None and limit else None
    status = "ok" if used is None or used < WARN_SHARE else "warn" if used < 1 else "critical"
    return {"name": name, "value": value, "limit": limit, "used": used, "status": status,
            "format": fmt}


def exposure(book: dict[str, Any] | None, now: datetime) -> dict[str, Any]:
    if not book:
        return {"rows": [], "accounts": [], "stale": True,
                "note": "The cross-market risk book has not been built yet."}
    lim = book.get("limits") or {}
    eq = float(book.get("equity") or 0)

    def f(k: str) -> float | None:
        x = book.get(k)
        return float(x) if x is not None else None

    n = sum(len(a.get("positions") or []) for a in book.get("accounts") or [])
    rows = [
        _limit_row("Open risk (if every stop is hit)",
                   float(book.get("open_risk_usd") or 0) / eq if eq else None,
                   float(lim.get("max_open_risk") or 0) or None, "pct"),
        _limit_row("Loss today", f("day_loss"), float(lim.get("daily_loss") or 0) or None, "pct"),
        _limit_row("Loss this week", f("week_loss"), float(lim.get("weekly_loss") or 0) or None,
                   "pct"),
        _limit_row("Drawdown from the peak", f("drawdown"),
                   float(lim.get("drawdown_pause") or 0) or None, "pct"),
        _limit_row("Open positions", float(n), float(lim.get("max_positions") or 0) or None,
                   "count"),
        _limit_row("Gross exposure (leverage)",
                   float(book.get("gross_notional_usd") or 0) / eq if eq else None,
                   float(lim.get("max_gross_leverage") or 0) or None, "x")]
    for r in rows:      # a loss shown as negative (a gain) uses none of its limit
        if r["value"] is not None and r["value"] < 0 and r["format"] == "pct":
            r["used"], r["status"] = 0.0, "ok"
    age = _age(book.get("at"), now)
    return {"at": book.get("at"), "stale": age is None or age > 900,
            "equity": book.get("equity"), "open_risk_usd": book.get("open_risk_usd"),
            "gross_notional_usd": book.get("gross_notional_usd"), "rows": rows,
            "accounts": [{"name": a.get("name"), "market": a.get("market"),
                          "equity": a.get("equity"), "reconciled": a.get("reconciled"),
                          "note": a.get("note")} for a in book.get("accounts") or []]}


def positions(book: dict[str, Any] | None) -> list[dict[str, Any]]:
    out = []
    for a in (book or {}).get("accounts") or []:
        for p in a.get("positions") or []:
            out.append({"account": a.get("name"), "market": a.get("market"),
                        "symbol": p.get("symbol"),
                        "side": "long" if int(p.get("side") or 0) > 0 else "short",
                        **{k: p.get(k) for k in ("quantity", "entry", "price", "stop",
                                                 "unrealized_usd", "risk_usd",
                                                 "notional_usd")}})
    return out


def pnl(fx: dict[str, Any], crypto: dict[str, Any], book: dict[str, Any] | None) -> dict[str, Any]:
    def neg(x: Any) -> float | None:
        return -float(x) if x is not None else None

    return {"combined_equity": (book or {}).get("equity"),
            "today_pct": neg((book or {}).get("day_loss")),
            "week_pct": neg((book or {}).get("week_loss")),
            "drawdown_pct": (book or {}).get("drawdown"),
            "crypto_total": crypto.get("total_pnl"), "crypto_realized": crypto.get("realized_pnl"),
            "crypto_unrealized": crypto.get("unrealized_pnl"),
            "forex_open": fx.get("open_pnl"), "forex_today": fx.get("day_pnl"),
            "forex_week": fx.get("week_pnl"),
            "note": "Crypto is a paper book and forex a demo account: no real money."}


def drawdowns(fx: dict[str, Any], crypto: dict[str, Any], book: dict[str, Any] | None
              ) -> dict[str, Any]:
    lim = (book or {}).get("limits") or {}
    return {"combined_current": (book or {}).get("drawdown"),
            "pause_at": lim.get("drawdown_pause"),
            "crypto_current": crypto.get("current_drawdown"),
            "crypto_max": crypto.get("max_drawdown"),
            "forex_current": fx.get("drawdown"),
            "forex_max_recorded": fx.get("max_drawdown_recorded")}


# --------------------------------------------------------------------------- health
def domain(guardian: dict[str, Any] | None, name: str, now: datetime) -> dict[str, Any]:
    if not guardian:
        return {"status": None, "checks": [], "note": "The Guardian has not run yet."}
    age = _age(guardian.get("at"), now)
    checks = [c for c in guardian.get("checks") or [] if c.get("domain") == name]
    return {"status": ((guardian.get("domains") or {}).get(name) or {}).get("status"),
            "stale": age is None or age > 180, "at": guardian.get("at"),
            "checks": sorted(checks, key=lambda c: {"critical": 0, "warn": 1}.get(
                c.get("status"), 2))}


def execution(summary: dict[str, Any], guardian: dict[str, Any] | None,
              now: datetime) -> dict[str, Any]:
    return {**summary, "guardian": domain(guardian, "execution", now),
            "note": "Paper and demo orders only. Each is sent once and its stop-loss is "
                    "verified after the fill."}


def load_ml_results() -> dict[str, Any] | None:
    """The newest committed ML filter result (``ml_filter_YYYY-MM.json``)."""
    try:
        import nexus_backtest

        files = sorted(Path(nexus_backtest.__file__).parent.joinpath("results")
                       .glob("ml_filter_20[0-9][0-9]-[0-9][0-9].json"))
        return json.loads(files[-1].read_text()) if files else None
    except (OSError, ValueError, ImportError):
        return None


# --------------------------------------------------------------------------- assembly
def collect_db(s: Any, now: datetime) -> dict[str, Any]:
    from nexus_common import opmode
    from nexus_common.markets import Market, effective
    from nexus_db import models, repo
    from nexus_execution.reports import portfolio_report
    from nexus_strategies import modules as mods
    from sqlalchemy import desc, func, select, text

    from nexus_api.routes import _module_forward

    port = s.scalar(select(models.Portfolio).where(models.Portfolio.mode == "paper")
                    .order_by(models.Portfolio.id).limit(1))
    report = portfolio_report(s, port.id) if port is not None else None
    curve = [] if port is None else [
        {"t": h.isoformat(), "equity": str(eq)} for h, eq in s.execute(text(
            "SELECT DISTINCT ON (date_trunc('hour', captured_at)) "
            "date_trunc('hour', captured_at) AS h, equity FROM performance_snapshots "
            "WHERE portfolio_id = :p AND captured_at >= :since "
            "ORDER BY date_trunc('hour', captured_at), captured_at DESC"),
            {"p": port.id, "since": now - timedelta(days=CURVE_DAYS)}).all()]
    mod_rows = []
    for code, spec in mods.MODULES.items():
        row = s.get(models.StrategyModule, code)
        mod_rows.append({"code": code, "name": spec.name, "market": spec.market,
                         "style": spec.style,
                         "activation": row.activation if row is not None else "OFF",
                         "validation": row.validation if row is not None else None,
                         "forward": _module_forward(s, code)})
    it = models.ExecutionIntent
    by_status = {str(k): int(n) for k, n in s.execute(
        select(it.status, func.count()).group_by(it.status)).all()}
    slip = s.scalar(select(func.avg(it.slippage_bps)).where(it.slippage_bps.is_not(None)))
    recent = [{"client_id": r.client_id, "venue": r.venue, "symbol": r.symbol,
               "side": "buy" if r.side > 0 else "sell", "kind": r.kind, "status": r.status,
               "module": r.module, "fill_price": str(r.fill_price) if r.fill_price else None,
               "slippage_bps": float(r.slippage_bps) if r.slippage_bps is not None else None,
               "stop_verified": r.stop_verified, "created_at": r.created_at.isoformat(),
               "last_event": (r.events or [{}])[-1].get("detail")}
              for r in s.scalars(select(it).order_by(desc(it.created_at)).limit(8))]
    switches = repo.market_autonomy_db_state(s)
    op = opmode.parse((repo.operating_mode_db_state(s) or {}).get("mode"))
    return {"report": report, "curve": curve, "modules": mod_rows,
            "execution": {"by_status": by_status, "total": sum(by_status.values()),
                          "avg_slippage_bps": float(slip) if slip is not None else None,
                          "recent": recent},
            "switches": {m.value: effective(m, switches).value
                         for m in (Market.CRYPTO, Market.FOREX, Market.COMMODITIES)},
            "operating_mode": op.value}


def assemble(db: dict[str, Any], rd: dict[str, Any], ml: dict[str, Any] | None,
             now: datetime) -> dict[str, Any]:
    """The page, from the database part and the Redis part (pure; unit-tested)."""
    fx = forex_performance(rd.get("demo"), rd.get("demo_history") or [], now)
    crypto = crypto_performance(db.get("report"), db.get("curve") or [])
    book, guardian = rd.get("book"), rd.get("guardian")
    sigs = signals(rd.get("board"), now)
    strats = strategies(db.get("modules") or [], guardian, db.get("switches") or {})
    g_age = _age((guardian or {}).get("at"), now)
    return {
        "at": now.isoformat(),
        "status": {"guardian": (guardian or {}).get("overall"),
                   "guardian_stale": g_age is None or g_age > 180,
                   "kill_switch": rd.get("kill"), "operating_mode": db.get("operating_mode"),
                   "switches": db.get("switches") or {}, "real_money": "OFF",
                   "risk_state": (rd.get("risk_state") or {}).get("state")},
        "forex": fx, "crypto": crypto, "strategies": strats, "signals": sigs,
        "calibration": calibration(strats, ml, sigs),
        "positions": positions(book), "pnl": pnl(fx, crypto, book),
        "drawdown": drawdowns(fx, crypto, book), "exposure": exposure(book, now),
        "models": domain(guardian, "models", now),
        "execution": execution(db.get("execution") or {}, guardian, now)}


async def build(s: Any, redis: Any, now: datetime) -> dict[str, Any]:
    import asyncio

    from nexus_execution.trader import RISK_STATE_KEY
    from nexus_risk.kill_switch import KillSwitch

    from nexus_api import mt5_demo
    from nexus_api import orchestrator_worker as ow
    from nexus_api import portfolio_risk as pr
    from nexus_api.guardian_worker import REPORT_KEY

    async def js(key: str) -> Any:
        raw = await redis.get(key)
        return json.loads(raw) if raw else None

    rd: dict[str, Any] = {"board": await js(ow.STATUS_KEY), "book": await js(pr.STATUS_KEY),
                          "guardian": await js(REPORT_KEY), "demo": await js(mt5_demo.STATUS_KEY),
                          "risk_state": await js(RISK_STATE_KEY.format(mode="paper")),
                          "kill": await KillSwitch(redis).status()}
    hist = await redis.zrangebyscore(mt5_demo.EQUITY_KEY,
                                     int((now - timedelta(days=CURVE_DAYS)).timestamp()), "+inf")
    rd["demo_history"] = [json.loads(h) for h in hist]
    db = await asyncio.to_thread(collect_db, s, now)
    return assemble(db, rd, load_ml_results(), now)
