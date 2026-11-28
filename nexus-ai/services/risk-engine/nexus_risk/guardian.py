"""The Guardian (unified brief, section H): one watchdog over the whole platform.

Every minute the Guardian worker (``nexus_api.guardian_worker``) collects what the other
components already publish and turns it into checks. This module is the pure part:
checks, debounced alerts and the emergency-shutdown decision. It reads; it never trades.

Five domains:
* **platform:** database and Redis reachability and latency, memory and disk, every
  worker running (not crash-looping), heartbeats of the market data, trader and notifier,
  and the orchestrator and risk book being refreshed;
* **market_data:** stored candles fresh and complete, prices consistent
  (open/high/low/close, no sudden suspect jump), live quotes fresh, not crossed and in
  line with the last candle, exchange reachability, and forex quotes from the Exness
  programs when the forex market is open;
* **models:** every ML model in use (none today: all failed their tests) fresh and not
  drifting; the forward record of every strategy module switched to OBSERVE or PAPER
  checked by the edge-decay monitor (``nexus_risk.edge_monitor``) against its test;
* **execution:** paper/demo orders answered in time, stop-losses verified after fills,
  rejections, slippage against the cost assumed in the tests, orders in an unknown state;
* **risk:** the kill switch, the trader's risk state, the cross-market risk book (losses
  against the limits, drawdown, open risk, positions, reconciliation) and the Exness demo
  halt.

Status: ``ok``, ``warn`` or ``critical``.

Alerts (``advance``): once per change, never repeated while a problem lasts.
* A warning is alerted after it has been seen in 2 consecutive runs.
* A critical problem is alerted at once.
* A recovery is alerted only if the problem was.

Emergency shutdown: the durable kill switch is engaged (only the owner clears it, with
2FA) on these conditions alone:
1. the combined drawdown is at or beyond the 5% pause while the kill switch is clear (a
   backstop for the orchestrator, which normally engages it);
2. a filled paper/demo position whose stop-loss has not been verified within 10 minutes
   (the adapters restore a missing stop or close the position; if they could not, stop).
"""

from __future__ import annotations

import statistics
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
from itertools import pairwise
from typing import Any

OK, WARN, CRITICAL = "ok", "warn", "critical"
RANK = {OK: 0, WARN: 1, CRITICAL: 2}
DOMAINS = ("platform", "market_data", "models", "execution", "risk")
CONFIRM_RUNS = 2                      # a warning must persist this many runs to alert

# Platform
DB_SLOW_SECONDS = 2.0
MEMORY_WARN, DISK_WARN = 0.90, 0.85
WORKER_RESTARTS_WARN = 3              # restarts in the last hour = crash loop
HEARTBEATS = {"market-data:candles": 180, "trader:{mode}": 180, "notifier": 600}
ORCHESTRATOR_MAX_AGE = timedelta(minutes=15)   # it runs every 5 minutes
RISK_BOOK_MAX_AGE = timedelta(minutes=15)
RISK_STATE_MAX_AGE = timedelta(minutes=10)
# Market data
COVERAGE_WARN = 0.98                  # share of expected bars present in the window
JUMP_WARN = 0.15                      # one 5-minute close-to-close move this large
BOOK_MAX_AGE = timedelta(seconds=120)
QUOTE_DEVIATION_WARN = 0.03           # live mid vs the last closed 5-minute close
FX_SYNC_MAX_AGE = timedelta(minutes=15)
# Execution
SENT_MAX_AGE = {"crypto_paper": timedelta(minutes=5), "exness_demo": timedelta(minutes=35)}
UNPROTECTED_MAX_AGE = timedelta(minutes=10)
REJECTS_WARN_24H = 5
SLIPPAGE_MIN_FILLS = 10
# Twice the slippage assumed in the tests (crypto 5 bps; forex about 0.1 pip plus spread
# noise). Measured against the decision price, so it includes the drift between the
# decision and the fill.
SLIPPAGE_WARN_BPS = {"crypto_paper": 10.0, "exness_demo": 2.0}
ORDER_STUCK_AGE = timedelta(minutes=5)
# Risk
LOSS_WARN_SHARE = 0.8                 # warn at 80% of a loss limit


@dataclass(frozen=True)
class Check:
    domain: str
    name: str
    status: str
    detail: str
    shutdown: bool = False   # an emergency-shutdown condition
    alert: bool = True       # False: shown here, but its own component already alerts

    @property
    def key(self) -> str:
        return f"{self.domain}:{self.name}"

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def _age(at: str | None, now: datetime) -> timedelta | None:
    if not at:
        return None
    try:
        return now - datetime.fromisoformat(at)
    except ValueError:
        return None


def _mins(td: timedelta) -> str:
    s = int(td.total_seconds())
    return f"{s}s" if s < 120 else f"{s // 60} min"


def freshness(domain: str, name: str, at: str | None, now: datetime, max_age: timedelta,
              what: str, *, missing: str = WARN) -> Check:
    age = _age(at, now)
    if age is None:
        return Check(domain, name, missing, f"{what}: no record")
    if age > max_age:
        return Check(domain, name, WARN, f"{what}: last update {_mins(age)} ago "
                                         f"(expected within {_mins(max_age)})")
    return Check(domain, name, OK, f"{what}: updated {_mins(age)} ago")


# --------------------------------------------------------------------------- platform
def platform_checks(*, db_seconds: float | None, db_error: str | None, redis_error: str | None,
                    memory: float | None, disk: float | None) -> list[Check]:
    d = "platform"
    out = []
    if db_error is not None:
        out.append(Check(d, "database", CRITICAL, f"database unreachable ({db_error})"))
    elif db_seconds is not None and db_seconds > DB_SLOW_SECONDS:
        out.append(Check(d, "database", WARN, f"database slow: {db_seconds:.1f}s per query"))
    else:
        out.append(Check(d, "database", OK, f"database ok ({(db_seconds or 0) * 1000:.0f} ms)"))
    out.append(Check(d, "redis", CRITICAL, f"Redis unreachable ({redis_error})")
               if redis_error is not None else Check(d, "redis", OK, "Redis ok"))
    if memory is not None:
        out.append(Check(d, "memory", WARN if memory >= MEMORY_WARN else OK,
                         f"memory {memory:.0%} used"))
    if disk is not None:
        out.append(Check(d, "disk", WARN if disk >= DISK_WARN else OK, f"disk {disk:.0%} used"))
    return out


def worker_checks(status: dict[str, Any] | None, now: datetime) -> list[Check]:
    """From the worker supervisor's published status (``nexus_api.workers``)."""
    d = "platform"
    if not status:
        return [Check(d, "workers", WARN, "worker status not published (workers not "
                                          "started, or an older version)")]
    age = _age(status.get("at"), now)
    if age is None or age > timedelta(minutes=3):
        return [Check(d, "workers", WARN, "worker status is stale: the worker thread may "
                                          "be stuck")]
    out = []
    for name, w in sorted((status.get("workers") or {}).items()):
        state, n = w.get("state"), int(w.get("restarts_1h") or 0)
        err = f"; last error: {w['last_error']}" if w.get("last_error") else ""
        if n >= WORKER_RESTARTS_WARN:
            out.append(Check(d, f"worker:{name}", WARN,
                             f"{name} restarted {n} times in the last hour{err}"))
        elif state == "restarting":
            out.append(Check(d, f"worker:{name}", WARN, f"{name} crashed and is restarting"
                                                        f"{err}"))
        else:
            out.append(Check(d, f"worker:{name}", OK, f"{name} {state or 'running'}"))
    return out


def heartbeat_names(trader_mode: str) -> dict[str, int]:
    return {n.format(mode=trader_mode): s for n, s in HEARTBEATS.items()}


def heartbeat_checks(beats: dict[str, str | None], now: datetime,
                     trader_mode: str = "paper") -> list[Check]:
    return [freshness("platform", f"heartbeat:{n}", beats.get(n), now,
                      timedelta(seconds=s), f"{n} heartbeat")
            for n, s in heartbeat_names(trader_mode).items()]


# --------------------------------------------------------------------------- market data
def series_checks(series: list[dict[str, Any]]) -> list[Check]:
    """Stored candle series (``nexus_market.derived.SeriesQuality.as_dict``)."""
    out = []
    for q in series:
        name = f"candles:{q['symbol']}:{q['timeframe']}"
        label = f"{q['symbol']} {q['timeframe']} candles"
        if q.get("newest") is None:
            out.append(Check("market_data", name, WARN, f"{label}: none stored"))
        elif q.get("stale"):
            out.append(Check("market_data", name, WARN, f"{label}: stale, newest closed "
                                                        f"{q.get('age_seconds')}s ago"))
        elif q.get("coverage") is not None and q["coverage"] < COVERAGE_WARN:
            out.append(Check("market_data", name, WARN,
                             f"{label}: {q['missing']} bars missing in the window "
                             f"(coverage {q['coverage']:.1%})"))
        else:
            out.append(Check("market_data", name, OK, f"{label}: fresh and complete"))
    return out


def price_checks(symbol: str, candles: list[tuple[float, float, float, float]]) -> list[Check]:
    """The latest closed 5-minute candles, oldest first, as (open, high, low, close)."""
    name = f"prices:{symbol}"
    if not candles:
        return [Check("market_data", name, WARN, f"{symbol}: no recent 5m candles")]
    for o, h, lo, c in candles:
        if min(o, h, lo, c) <= 0 or h < max(o, c, lo) or lo > min(o, c, h):
            return [Check("market_data", name, CRITICAL,
                          f"{symbol}: inconsistent candle (open {o}, high {h}, low {lo}, "
                          f"close {c})")]
    closes = [c for *_, c in candles]
    jumps = [abs(b / a - 1) for a, b in pairwise(closes) if a > 0]
    if jumps and max(jumps) >= JUMP_WARN:
        return [Check("market_data", name, WARN,
                      f"{symbol}: a {max(jumps):.1%} move in one 5-minute bar: verify the "
                      f"price against another source")]
    return [Check("market_data", name, OK, f"{symbol}: last {len(candles)} candles consistent")]


def quote_checks(symbol: str, book: dict[str, Any] | None, last_close: float | None,
                 now: datetime) -> list[Check]:
    name = f"quote:{symbol}"
    if not book:
        return [Check("market_data", name, WARN, f"{symbol}: no live quote")]
    age = _age(book.get("ts"), now)
    bid, ask = float(book.get("bid") or 0), float(book.get("ask") or 0)
    if bid <= 0 or ask <= 0 or bid >= ask:
        return [Check("market_data", name, CRITICAL, f"{symbol}: invalid quote "
                                                     f"(bid {bid}, ask {ask})")]
    if age is None or age > BOOK_MAX_AGE:
        return [Check("market_data", name, WARN, f"{symbol}: live quote is stale")]
    mid = (bid + ask) / 2
    if last_close and abs(mid / last_close - 1) >= QUOTE_DEVIATION_WARN:
        return [Check("market_data", name, WARN,
                      f"{symbol}: live price {mid:g} is {abs(mid / last_close - 1):.1%} from "
                      f"the last stored close {last_close:g}")]
    return [Check("market_data", name, OK, f"{symbol}: live quote fresh ({_mins(age)} old)")]


def exchange_check(code: str, health: dict[str, Any] | None) -> Check:
    if health is None:
        return Check("market_data", f"exchange:{code}", WARN, f"{code}: no health probe",
                     alert=False)
    return Check("market_data", f"exchange:{code}", OK if health.get("ok") else WARN,
                 f"{code}: {'reachable' if health.get('ok') else 'unreachable'}"
                 f" ({health.get('detail') or 'ok'})", alert=False)


def forex_checks(demo: dict[str, Any] | None, bridge: dict[str, Any] | None, now: datetime,
                 market_open: bool) -> list[Check]:
    d = "market_data"
    if not market_open:
        return [Check(d, "forex", OK, "forex market closed")]
    if not demo and not bridge:
        return [Check(d, "forex", WARN, "no forex data: the Exness programs on your PC "
                                        "are not running")]
    out = []
    if demo:
        out.append(freshness(d, "forex:demo_sync", demo.get("at"), now, FX_SYNC_MAX_AGE,
                             "Exness demo trader sync"))
        bad = [s.get("base_name") or s.get("name") for s in demo.get("symbols") or []
               if not (0 < float(s.get("bid") or 0) < float(s.get("ask") or 0))]
        out.append(Check(d, "forex:quotes", CRITICAL, f"invalid forex quotes: {bad}") if bad
                   else Check(d, "forex:quotes", OK, "forex quotes consistent"))
    if bridge:
        out.append(freshness(d, "forex:bridge", bridge.get("at"), now, FX_SYNC_MAX_AGE,
                             "Exness read-only bridge"))
    return out


# --------------------------------------------------------------------------- models
def ml_model_checks(models: list[dict[str, Any]], now: datetime) -> list[Check]:
    """``models``: active ML models with ``last_prediction`` (ISO) and ``drift_passed``
    (the latest live drift evaluation, None if never evaluated)."""
    d = "models"
    if not models:
        return [Check(d, "ml", OK, "no ML model in use (none passed its out-of-sample test)")]
    out = []
    for m in models:
        name = f"ml:{m['name']}"
        age = _age(m.get("last_prediction"), now)
        if age is None or age > timedelta(hours=2):
            out.append(Check(d, name, WARN, f"{m['name']} {m['version']}: no prediction in "
                                            "the last 2 hours"))
        elif m.get("drift_passed") is False:
            out.append(Check(d, name, WARN, f"{m['name']} {m['version']}: the latest live "
                                            "drift check failed"))
        else:
            out.append(Check(d, name, OK, f"{m['name']} {m['version']}: predicting, no drift "
                                          "alarm"))
    return out


def module_checks(modules: list[dict[str, Any]]) -> list[Check]:
    """Strategy modules switched to OBSERVE or PAPER: their forward record (net R of each
    resolved signal, in order) against the edge-decay monitor. The reference is the
    module's tested mean R when it passed its test, otherwise 0."""
    from nexus_risk.edge_monitor import EdgeState, evaluate

    d = "models"
    out = []
    for m in modules:
        r = [float(x) for x in m.get("net_r") or []]
        ref = float(m["ref_mean_r"]) if m.get("ref_mean_r") is not None else 0.0
        name, label = f"module:{m['code']}", f"{m['code']} ({m['activation']})"
        if m.get("allocation"):
            out.append(Check(d, name, OK, f"{label}: an allocation module; its forward record "
                                          "is the trend paper test (equity, not R)"))
            continue
        if not r:
            out.append(Check(d, name, OK, f"{label}: no resolved forward signals yet"))
            continue
        st = evaluate(r, ref_mean=ref)
        mean = statistics.fmean(r)
        summary = f"{len(r)} forward signals, mean {mean:+.2f}R (test {ref:+.2f}R)"
        if st.state is EdgeState.STOP:
            out.append(Check(d, name, CRITICAL, f"{label}: edge lost ({'; '.join(st.reasons)})"
                                                f"; {summary}: consider switching it OFF"))
        elif st.state is EdgeState.REDUCE:
            out.append(Check(d, name, WARN, f"{label}: edge weakening "
                                            f"({'; '.join(st.reasons)}); {summary}"))
        else:
            out.append(Check(d, name, OK, f"{label}: {summary}"))
    return out


# --------------------------------------------------------------------------- execution
def _event_at(intent: dict[str, Any], kind: str) -> str | None:
    return next((e.get("at") for e in reversed(intent.get("events") or [])
                 if e.get("kind") == kind), None)


def intent_checks(active: list[dict[str, Any]], rejected_24h: int,
                  fills: list[dict[str, Any]], now: datetime) -> list[Check]:
    """``active``: execution intents still pending/sent/filled/protected; ``fills``:
    recent fills with ``venue`` and ``slippage_bps``, newest first."""
    d = "execution"
    out = []
    for it in active:
        cid, venue = it["client_id"], it["venue"]
        label = f"{it['symbol']} {it['side']} ({venue}, {cid})"
        if it["status"] == "sent":
            age = _age(_event_at(it, "sent"), now)
            if age is not None and age > SENT_MAX_AGE[venue]:
                out.append(Check(d, f"intent:{cid}", WARN,
                                 f"{label}: no answer from the broker for {_mins(age)} "
                                 "(never resent; reconciled by its ID)"))
        elif it["status"] == "filled" and it.get("kind") == "bracket":
            age = _age(_event_at(it, "filled"), now)
            if age is not None and age > UNPROTECTED_MAX_AGE:
                out.append(Check(d, f"intent:{cid}", CRITICAL,
                                 f"{label}: filled {_mins(age)} ago and its stop-loss is "
                                 "still not verified", shutdown=True))
    if not out:
        out.append(Check(d, "orders", OK, f"{len(active)} active paper/demo orders, all "
                                          "answered and protected in time"))
    out.append(Check(d, "rejections", WARN if rejected_24h >= REJECTS_WARN_24H else OK,
                     f"{rejected_24h} orders rejected or expired in the last 24 hours"))
    for venue, limit in SLIPPAGE_WARN_BPS.items():
        bps = [float(f["slippage_bps"]) for f in fills
               if f["venue"] == venue and f.get("slippage_bps") is not None][:20]
        if len(bps) < SLIPPAGE_MIN_FILLS:
            out.append(Check(d, f"slippage:{venue}", OK,
                             f"{venue}: {len(bps)} fills measured (need "
                             f"{SLIPPAGE_MIN_FILLS} to judge)"))
            continue
        avg = statistics.fmean(bps)
        out.append(Check(d, f"slippage:{venue}", WARN if avg > limit else OK,
                         f"{venue}: average slippage {avg:.1f} bps over the last {len(bps)} "
                         f"fills (warning above {limit:g} bps)"))
    return out


def order_checks(unknown: int, stuck: int) -> list[Check]:
    d = "execution"
    return [Check(d, "orders:unknown", CRITICAL if unknown else OK,
                  f"{unknown} paper orders in an unknown state (reconciled by client order "
                  "ID; new orders blocked meanwhile)" if unknown else
                  "no order in an unknown state"),
            Check(d, "orders:stuck", WARN if stuck else OK,
                  f"{stuck} paper orders waiting for an answer for more than 5 minutes"
                  if stuck else "no order waiting for an answer")]


# --------------------------------------------------------------------------- risk
def kill_check(kill: dict[str, Any] | None, restored: bool) -> Check:
    if kill is None:
        return Check("risk", "kill_switch", OK, "kill switch clear")
    how = " (restored from the audit log)" if restored else ""
    return Check("risk", "kill_switch", CRITICAL,
                 f"kill switch ENGAGED by {kill.get('actor')}: {kill.get('reason')}{how}; "
                 "only you can clear it (Risk page, with 2FA)", alert=restored)


def risk_state_check(state: dict[str, Any] | None, now: datetime) -> Check:
    f = freshness("risk", "risk_state", (state or {}).get("at"), now, RISK_STATE_MAX_AGE,
                  "trader risk state")
    if f.status != OK or state is None:
        return f
    if state.get("state") != "NORMAL":
        return Check("risk", "risk_state", WARN, f"risk state {state.get('state')}: "
                                                 f"{'; '.join(state.get('reasons') or [])}",
                     alert=False)
    return Check("risk", "risk_state", OK, "risk state NORMAL")


def book_checks(book: dict[str, Any] | None, now: datetime, *,
                kill_engaged: bool) -> list[Check]:
    """The cross-market risk book (``nexus_api.portfolio_risk.summary``)."""
    d = "risk"
    f = freshness(d, "risk_book", (book or {}).get("at"), now, RISK_BOOK_MAX_AGE,
                  "cross-market risk book")
    if f.status != OK or book is None:
        return [f]
    lim = book.get("limits") or {}
    out = [f]
    for key, label, limit_key in (("day_loss", "daily loss", "daily_loss"),
                                  ("week_loss", "weekly loss", "weekly_loss")):
        v, limit = book.get(key), lim.get(limit_key)
        if v is None or limit is None:
            continue
        v, limit = float(v), float(limit)
        status = CRITICAL if v >= limit else WARN if v >= LOSS_WARN_SHARE * limit else OK
        tail = ": new entries blocked" if status == CRITICAL else ""
        out.append(Check(d, key, status, f"{label} {v:.2%} of the {limit:.1%} limit{tail}"))
    dd, pause = book.get("drawdown"), lim.get("drawdown_pause")
    if dd is not None and pause is not None:
        dd, pause = float(dd), float(pause)
        if dd >= pause:
            out.append(Check(d, "drawdown", CRITICAL,
                             f"drawdown {dd:.2%} reached the {pause:.0%} pause",
                             shutdown=not kill_engaged))
        else:
            out.append(Check(d, "drawdown", WARN if dd >= LOSS_WARN_SHARE * pause else OK,
                             f"drawdown {dd:.2%} (pause at {pause:.0%})"))
    eq = float(book.get("equity") or 0)
    accounts = book.get("accounts") or []
    n = sum(len(a.get("positions") or []) for a in accounts)
    max_pos = int(lim.get("max_positions") or 0)
    if max_pos:
        out.append(Check(d, "positions", CRITICAL if n > max_pos else OK,
                         f"{n} open positions (limit {max_pos})"))
    risk_usd, max_risk = float(book.get("open_risk_usd") or 0), float(lim.get("max_open_risk")
                                                                       or 0)
    if eq > 0 and max_risk:
        share = risk_usd / eq
        out.append(Check(d, "open_risk", CRITICAL if share > max_risk * 1.05 else OK,
                         f"open risk {share:.2%} of equity (limit {max_risk:.1%})"))
    unrec = [a["name"] for a in accounts if not a.get("reconciled")]
    out.append(Check(d, "reconciliation", WARN if unrec else OK,
                     f"not reconciled (entries blocked there): {', '.join(unrec)}" if unrec
                     else f"{len(accounts)} accounts reconciled"))
    return out


def demo_halt_check(halt: dict[str, Any] | None) -> Check:
    if not halt:
        return Check("risk", "exness_demo_halt", OK, "Exness demo account not halted")
    return Check("risk", "exness_demo_halt", CRITICAL,
                 f"Exness demo trading halted: {halt.get('reason') or halt}; you reset it",
                 alert=False)


# --------------------------------------------------------------------------- summary
def worst(checks: list[Check]) -> str:
    return max((c.status for c in checks), key=RANK.__getitem__, default=OK)


def summary(checks: list[Check]) -> dict[str, Any]:
    by = {d: [c for c in checks if c.domain == d] for d in DOMAINS}
    return {"overall": worst(checks),
            "domains": {d: {"status": worst(cs), "problems": sum(c.status != OK for c in cs),
                            "checks": len(cs)} for d, cs in by.items()}}


def shutdown_reasons(checks: list[Check]) -> list[str]:
    return [c.detail for c in checks if c.shutdown and c.status == CRITICAL]


def advance(prev: dict[str, dict[str, Any]], checks: list[Check], now: datetime
            ) -> tuple[dict[str, dict[str, Any]], list[tuple[Check, str]]]:
    """The new per-check state and the alerts to send, as (check, "problem" or
    "recovered"). See the module docstring for the rules."""
    state: dict[str, dict[str, Any]] = {}
    alerts: list[tuple[Check, str]] = []
    for c in checks:
        p = prev.get(c.key) or {}
        same = p.get("status") == c.status
        count = int(p.get("count") or 0) + 1 if same else 1
        alerted = p.get("alerted", OK)
        if c.status != OK and alerted != c.status and (c.status == CRITICAL
                                                       or count >= CONFIRM_RUNS):
            if c.alert:
                alerts.append((c, "problem"))
            alerted = c.status
        elif c.status == OK and alerted != OK:
            if c.alert:
                alerts.append((c, "recovered"))
            alerted = OK
        state[c.key] = {"status": c.status, "count": count, "alerted": alerted,
                        "since": p.get("since") if same and p.get("since") else now.isoformat()}
    for key, p in prev.items():
        if key not in state and p.get("alerted", OK) != OK:
            domain, _, name = key.partition(":")
            alerts.append((Check(domain, name, OK, "no longer reported"), "recovered"))
    return state, alerts
