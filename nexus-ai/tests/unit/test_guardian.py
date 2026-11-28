"""The Guardian's checks, debounced alerts and emergency-shutdown decision
(``nexus_risk.guardian``), and the worker status the supervisors publish."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

from nexus_risk import guardian as g

NOW = datetime(2026, 11, 25, 12, 0, tzinfo=UTC)          # a Wednesday: forex open


def ago(**kw: float) -> str:
    return (NOW - timedelta(**kw)).isoformat()


def by_name(checks: list[g.Check]) -> dict[str, g.Check]:
    return {c.name: c for c in checks}


def test_platform_and_workers():
    c = by_name(g.platform_checks(db_seconds=0.12, db_error=None, redis_error=None,
                                  memory=0.95, disk=0.5))
    assert c["database"].status == g.OK and c["redis"].status == g.OK
    assert c["memory"].status == g.WARN and c["disk"].status == g.OK
    down = by_name(g.platform_checks(db_seconds=None, db_error="OperationalError",
                                     redis_error="ConnectionError", memory=None, disk=None))
    assert down["database"].status == g.CRITICAL and down["redis"].status == g.CRITICAL
    slow = g.platform_checks(db_seconds=3.0, db_error=None, redis_error=None, memory=None,
                             disk=None)
    assert slow[0].status == g.WARN and "slow" in slow[0].detail

    assert g.worker_checks(None, NOW)[0].status == g.WARN
    assert "stuck" in g.worker_checks({"at": ago(minutes=10), "workers": {}}, NOW)[0].detail
    w = by_name(g.worker_checks({"at": ago(seconds=20), "workers": {
        "trader": {"state": "running", "restarts_1h": 0},
        "news": {"state": "running", "restarts_1h": 4, "last_error": "TimeoutError"},
        "bot": {"state": "restarting", "restarts_1h": 1, "last_error": "boom"}}}, NOW))
    assert w["worker:trader"].status == g.OK
    assert w["worker:news"].status == g.WARN and "4 times" in w["worker:news"].detail
    assert w["worker:bot"].status == g.WARN and "boom" in w["worker:bot"].detail

    hb = by_name(g.heartbeat_checks({"market-data:candles": ago(seconds=30),
                                     "trader:paper": ago(minutes=10)}, NOW))
    assert hb["heartbeat:market-data:candles"].status == g.OK
    assert hb["heartbeat:trader:paper"].status == g.WARN
    assert hb["heartbeat:notifier"].detail.endswith("no record")
    assert "heartbeat:trader:live" in by_name(g.heartbeat_checks({}, NOW, "live"))


def test_market_data_validation():
    s = by_name(g.series_checks([
        {"symbol": "BTC/USDT", "timeframe": "5m", "newest": ago(minutes=5), "stale": False,
         "age_seconds": 30, "coverage": 1.0, "missing": 0},
        {"symbol": "BTC/USDT", "timeframe": "1h", "newest": ago(hours=5), "stale": True,
         "age_seconds": 14_000, "coverage": 1.0, "missing": 0},
        {"symbol": "ETH/USDT", "timeframe": "5m", "newest": ago(minutes=5), "stale": False,
         "age_seconds": 30, "coverage": 0.9, "missing": 100},
        {"symbol": "ETH/USDT", "timeframe": "4h", "newest": None}]))
    assert s["candles:BTC/USDT:5m"].status == g.OK
    assert s["candles:BTC/USDT:1h"].status == g.WARN and "stale" in s["candles:BTC/USDT:1h"].detail
    assert "100 bars missing" in s["candles:ETH/USDT:5m"].detail
    assert "none stored" in s["candles:ETH/USDT:4h"].detail

    good = [(100.0, 101.0, 99.0, 100.5), (100.5, 102.0, 100.0, 101.0)]
    assert g.price_checks("BTC/USDT", good)[0].status == g.OK
    bad = g.price_checks("BTC/USDT", [(100.0, 99.0, 98.0, 100.5)])        # high < close
    assert bad[0].status == g.CRITICAL and "inconsistent" in bad[0].detail
    assert g.price_checks("BTC/USDT", [(1.0, 1.0, 0.0, 1.0)])[0].status == g.CRITICAL
    jump = g.price_checks("BTC/USDT", [(100, 100, 100, 100), (100, 125, 100, 120)])
    assert jump[0].status == g.WARN and "20.0% move" in jump[0].detail
    assert g.price_checks("BTC/USDT", [])[0].status == g.WARN

    q = {"bid": "100.0", "ask": "100.1", "ts": ago(seconds=5)}
    assert g.quote_checks("BTC/USDT", q, 100.0, NOW)[0].status == g.OK
    assert g.quote_checks("BTC/USDT", None, 100.0, NOW)[0].status == g.WARN
    assert g.quote_checks("BTC/USDT", q | {"ts": ago(minutes=5)}, 100.0, NOW)[0].status == g.WARN
    assert g.quote_checks("BTC/USDT", q | {"bid": "100.2"}, 100.0, NOW)[0].status == g.CRITICAL
    far = g.quote_checks("BTC/USDT", q, 90.0, NOW)[0]
    assert far.status == g.WARN and "11.2%" in far.detail

    assert g.exchange_check("binance", {"ok": True}).status == g.OK
    ex = g.exchange_check("binance", {"ok": False, "detail": "timeout"})
    assert ex.status == g.WARN and not ex.alert          # the health monitor alerts itself

    closed = g.forex_checks(None, None, NOW, market_open=False)
    assert closed[0].status == g.OK and "closed" in closed[0].detail
    assert g.forex_checks(None, None, NOW, market_open=True)[0].status == g.WARN
    fx = by_name(g.forex_checks(
        {"at": ago(minutes=30), "symbols": [{"base_name": "EURUSD", "bid": 1.1, "ask": 1.0}]},
        {"at": ago(minutes=2)}, NOW, market_open=True))
    assert fx["forex:demo_sync"].status == g.WARN
    assert fx["forex:quotes"].status == g.CRITICAL and fx["forex:bridge"].status == g.OK


def test_model_health():
    assert "no ML model in use" in g.ml_model_checks([], NOW)[0].detail
    m = by_name(g.ml_model_checks([
        {"name": "a", "version": "1", "last_prediction": ago(minutes=10), "drift_passed": True},
        {"name": "b", "version": "2", "last_prediction": ago(hours=5), "drift_passed": None},
        {"name": "c", "version": "3", "last_prediction": ago(minutes=1),
         "drift_passed": False}], NOW))
    assert m["ml:a"].status == g.OK and m["ml:b"].status == g.WARN
    assert "drift" in m["ml:c"].detail and m["ml:c"].status == g.WARN

    healthy = [1.5, -1.0, 2.0, -1.0, 1.8, -1.0, 2.0, -1.0, 1.5, -1.0, 2.0, -1.0]
    losing = [-1.0] * 16
    mods = by_name(g.module_checks([
        {"code": "ok_mod", "activation": "OBSERVE", "net_r": healthy, "ref_mean_r": 0.3},
        {"code": "dead_mod", "activation": "PAPER", "net_r": losing, "ref_mean_r": 0.2},
        {"code": "new_mod", "activation": "OBSERVE", "net_r": []},
        {"code": "crypto_swing_trend", "activation": "PAPER", "allocation": True}]))
    assert mods["module:ok_mod"].status == g.OK and "12 forward signals" in mods[
        "module:ok_mod"].detail
    assert mods["module:dead_mod"].status == g.CRITICAL
    assert "consider switching it OFF" in mods["module:dead_mod"].detail
    assert not mods["module:dead_mod"].shutdown                 # an alert, not a shutdown
    assert "no resolved" in mods["module:new_mod"].detail
    assert "allocation module" in mods["module:crypto_swing_trend"].detail


def test_execution_monitoring_and_unprotected_position_shutdown():
    def intent(cid: str, status: str, venue: str = "crypto_paper", **ev_ago: float) -> dict:
        kind = "sent" if status == "sent" else "filled"
        return {"client_id": cid, "venue": venue, "symbol": "BTC/USDT", "side": "buy",
                "kind": "bracket", "status": status,
                "events": [{"kind": "created", "at": ago(hours=1)},
                           {"kind": kind, "at": ago(**ev_ago)}]}

    ok = g.intent_checks([intent("nxOa", "sent", seconds=30),
                          intent("nxOb", "filled", minutes=2)], 0, [], NOW)
    assert by_name(ok)["orders"].status == g.OK
    c = by_name(g.intent_checks([
        intent("nxOslow", "sent", minutes=8),
        intent("nxOdemo", "sent", "exness_demo", minutes=20),    # demo: 35 min allowed
        intent("nxOnaked", "filled", minutes=12)], 6, [], NOW))
    assert c["intent:nxOslow"].status == g.WARN and "never resent" in c["intent:nxOslow"].detail
    assert "intent:nxOdemo" not in c
    naked = c["intent:nxOnaked"]
    assert naked.status == g.CRITICAL and naked.shutdown
    assert c["rejections"].status == g.WARN
    assert g.shutdown_reasons(list(c.values())) == [naked.detail]

    fills = [{"venue": "crypto_paper", "slippage_bps": 15}] * 12 + \
        [{"venue": "exness_demo", "slippage_bps": 0.5}] * 3
    s = by_name(g.intent_checks([], 0, fills, NOW))
    assert s["slippage:crypto_paper"].status == g.WARN and "15.0 bps" in s[
        "slippage:crypto_paper"].detail
    assert s["slippage:exness_demo"].status == g.OK and "need 10" in s[
        "slippage:exness_demo"].detail

    o = by_name(g.order_checks(unknown=1, stuck=2))
    assert o["orders:unknown"].status == g.CRITICAL and o["orders:stuck"].status == g.WARN
    assert all(x.status == g.OK for x in g.order_checks(0, 0))


def test_risk_monitoring_and_drawdown_backstop():
    assert g.kill_check(None, False).status == g.OK
    k = g.kill_check({"actor": "user:1", "reason": "manual"}, False)
    assert k.status == g.CRITICAL and not k.alert          # the kill switch alerts itself
    assert g.kill_check({"actor": "user:1", "reason": "x"}, True).alert   # restored: alert

    assert g.risk_state_check(None, NOW).status == g.WARN
    assert g.risk_state_check({"state": "NORMAL", "at": ago(minutes=20)}, NOW).status == g.WARN
    st = g.risk_state_check({"state": "NO_NEW_TRADES", "reasons": ["daily loss limit"],
                             "at": ago(minutes=1)}, NOW)
    assert st.status == g.WARN and "daily loss limit" in st.detail

    limits = {"daily_loss": "0.01", "weekly_loss": "0.025", "drawdown_pause": "0.05",
              "max_positions": 3, "max_open_risk": "0.01"}
    book = {"at": ago(minutes=3), "equity": "10000.00", "day_loss": "0.0085",
            "week_loss": "0.026", "drawdown": "0.01", "open_risk_usd": "50.00",
            "limits": limits,
            "accounts": [{"name": "crypto_paper", "reconciled": True, "positions": [{}]},
                         {"name": "exness_demo", "reconciled": False, "positions": []}]}
    b = by_name(g.book_checks(book, NOW, kill_engaged=False))
    assert b["risk_book"].status == g.OK
    assert b["day_loss"].status == g.WARN                       # 85% of the limit
    assert b["week_loss"].status == g.CRITICAL and "blocked" in b["week_loss"].detail
    assert b["drawdown"].status == g.OK and b["positions"].status == g.OK
    assert b["open_risk"].status == g.OK and "0.50%" in b["open_risk"].detail
    assert b["reconciliation"].status == g.WARN and "exness_demo" in b["reconciliation"].detail
    assert g.shutdown_reasons(list(b.values())) == []

    deep = by_name(g.book_checks(book | {"drawdown": "0.051"}, NOW, kill_engaged=False))
    assert deep["drawdown"].status == g.CRITICAL and deep["drawdown"].shutdown
    held = by_name(g.book_checks(book | {"drawdown": "0.051"}, NOW, kill_engaged=True))
    assert not held["drawdown"].shutdown                        # already engaged
    assert g.book_checks(None, NOW, kill_engaged=False)[0].status == g.WARN
    assert g.book_checks(book | {"at": ago(hours=1)}, NOW, kill_engaged=False)[0].status == \
        g.WARN

    assert g.demo_halt_check(None).status == g.OK
    assert g.demo_halt_check({"reason": "5% drawdown"}).status == g.CRITICAL


def test_alerts_are_debounced_once_per_change():
    warn = g.Check("platform", "memory", g.WARN, "memory 95% used")
    crit = g.Check("platform", "redis", g.CRITICAL, "Redis unreachable")
    ok_mem = g.Check("platform", "memory", g.OK, "memory 50% used")

    state, alerts = g.advance({}, [warn, crit], NOW)
    assert [(c.name, k) for c, k in alerts] == [("redis", "problem")]   # critical: at once
    state, alerts = g.advance(state, [warn, crit], NOW + timedelta(minutes=1))
    assert [(c.name, k) for c, k in alerts] == [("memory", "problem")]  # warn: 2nd run
    since = state["platform:memory"]["since"]
    state, alerts = g.advance(state, [warn, crit], NOW + timedelta(minutes=2))
    assert alerts == [] and state["platform:memory"]["since"] == since  # never repeated
    state, alerts = g.advance(state, [ok_mem, crit], NOW + timedelta(minutes=3))
    assert [(c.name, k) for c, k in alerts] == [("memory", "recovered")]
    # A warning that clears before it is confirmed is never alerted either way.
    state, alerts = g.advance(state, [warn], NOW + timedelta(minutes=4))
    assert [k for _, k in alerts] == ["recovered"]          # only redis: no longer reported
    state, alerts = g.advance(state, [ok_mem], NOW + timedelta(minutes=5))
    assert alerts == []
    # Escalation alerts again; a quiet check (alert=False) is tracked but not alerted.
    state, _ = g.advance({}, [warn], NOW)
    state, _ = g.advance(state, [warn], NOW)
    state, alerts = g.advance(state, [g.Check("platform", "memory", g.CRITICAL, "99%")], NOW)
    assert [(c.status, k) for c, k in alerts] == [(g.CRITICAL, "problem")]
    quiet = g.Check("risk", "kill_switch", g.CRITICAL, "engaged", alert=False)
    state, alerts = g.advance({}, [quiet], NOW)
    assert alerts == [] and state["risk:kill_switch"]["status"] == g.CRITICAL


def test_summary_counts_problems_per_domain():
    checks = [g.Check("platform", "a", g.OK, ""), g.Check("risk", "b", g.WARN, ""),
              g.Check("risk", "c", g.CRITICAL, "")]
    s = g.summary(checks)
    assert s["overall"] == g.CRITICAL
    assert s["domains"]["risk"] == {"status": g.CRITICAL, "problems": 2, "checks": 2}
    assert s["domains"]["models"] == {"status": g.OK, "problems": 0, "checks": 0}


def test_supervisors_publish_worker_states():
    from nexus_api.workers import InlineWorkers, publish_loop

    published: list[dict] = []

    async def publish(doc: dict) -> None:
        published.append(doc)

    async def crash(stop):
        raise RuntimeError("boom")

    async def steady(stop):
        await stop.wait()

    async def go():
        w = InlineWorkers({"crash": crash, "steady": steady}, base_delay=0.05, grace=2,
                          publish=publish)
        w.start()
        await asyncio.sleep(0.4)
        snap = w.states.snapshot()
        await w.stop()
        return snap

    snap = asyncio.run(go())
    assert snap["workers"]["steady"]["state"] == "running"
    crashed = snap["workers"]["crash"]
    assert crashed["restarts_1h"] >= 2 and "RuntimeError: boom" in crashed["last_error"]
    assert published and "workers" in published[0]           # published at start

    async def failing(doc: dict) -> None:
        raise ConnectionError("redis down")

    async def once():
        from nexus_api.workers import WorkerStates

        stop = asyncio.Event()
        task = asyncio.create_task(publish_loop(WorkerStates(["x"]), failing, stop, every=0.05))
        await asyncio.sleep(0.12)
        stop.set()
        await task                                             # survives the failure

    asyncio.run(once())


def test_guardian_alerts_are_formatted_for_telegram():
    from nexus_notify.formatting import format_event

    t = format_event({"type": "guardian_alert", "domain": "risk", "check": "drawdown",
                      "status": "critical", "kind": "problem", "detail": "drawdown <5%>"})
    assert t is not None and "CRITICAL" in t and "&lt;5%&gt;" in t
    r = format_event({"type": "guardian_alert", "domain": "platform", "status": "ok",
                      "kind": "recovered", "detail": "memory 50% used"})
    assert r is not None and "recovered" in r
