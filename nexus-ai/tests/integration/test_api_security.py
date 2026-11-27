"""API authentication, authorization, CSRF and sensitive-action tests (real DB + Redis)."""

from __future__ import annotations

import json
import os
import time
from collections.abc import Iterator
from typing import Any

import pytest
import redis as sync_redis
from fastapi.testclient import TestClient
from nexus_api.main import create_app
from nexus_config import LIVE_TRADING_ACK_PHRASE, Settings
from nexus_db import make_session_factory, models, transaction
from nexus_security import generate_key_entry, totp
from nexus_security.passwords import hash_password
from sqlalchemy import Engine, select

from tests.integration.conftest import truncate

pytestmark = [pytest.mark.db, pytest.mark.redis]
PASSWORD = "correct-horse-battery-9"
CSRF = {"x-nexus-csrf": "1"}


@pytest.fixture
def client(migrated_engine: Engine, db_url: str) -> Iterator[TestClient]:
    url = os.environ.get("NEXUS_TEST_REDIS_URL")
    if not url:
        pytest.skip("NEXUS_TEST_REDIS_URL not set")
    sync_redis.from_url(url).flushdb()
    factory = make_session_factory(migrated_engine)
    with transaction(factory) as s:
        s.add(models.User(email="owner@example.test", password_hash=hash_password(PASSWORD),
                          role="owner"))
        s.add(models.User(email="viewer@example.test", password_hash=hash_password(PASSWORD),
                          role="viewer"))
    # Public-data fallbacks point at a closed local port: tests never reach Binance.
    settings = Settings(database_url=db_url, redis_url=url,
                        credential_encryption_keys=generate_key_entry("k1"),
                        binance_public_api_url="http://127.0.0.1:9/api/v3")
    with TestClient(create_app(settings)) as c:
        yield c
    sync_redis.from_url(url).flushdb()
    truncate(migrated_engine, "users, audit_logs, exchange_credentials, risk_events, "
                              "ai_models, model_evaluations")
    with migrated_engine.begin() as conn:
        from sqlalchemy import text
        conn.execute(text("UPDATE strategies SET is_enabled=false, parameters='{}'"))


def login(c: TestClient, email: str = "owner@example.test", **kw: Any) -> Any:
    return c.post("/api/v1/auth/login", json={"email": email, "password": PASSWORD, **kw},
                  headers=CSRF)


GET_ROUTES = ["/api/v1/overview", "/api/v1/portfolio", "/api/v1/exchanges", "/api/v1/signals",
              "/api/v1/positions", "/api/v1/orders", "/api/v1/trades", "/api/v1/performance",
              "/api/v1/strategies", "/api/v1/models", "/api/v1/risk", "/api/v1/system",
              "/api/v1/security", "/api/v1/markets/books", "/api/v1/auth/me",
              "/api/v1/risk/state", "/api/v1/markets/data-quality", "/api/v1/system/binance",
              "/api/v1/analysis/technical", "/api/v1/analysis/mtf",
              "/api/v1/decision/fusion", "/api/v1/decision/log", "/api/v1/spot-engine",
              "/api/v1/research/trend-forward", "/api/v1/research/library",
              "/api/v1/trading-bot"]


@pytest.mark.parametrize("path", GET_ROUTES)
def test_every_route_requires_auth(client: TestClient, path: str) -> None:
    assert client.get(path).status_code == 401


def test_all_routes_work_when_signed_in(client: TestClient) -> None:
    assert login(client).status_code == 200
    for path in GET_ROUTES:
        r = client.get(path)
        assert r.status_code == 200, (path, r.text)
    over = client.get("/api/v1/overview").json()
    assert over["mode"] == "paper" and over["config"]["futures_enabled"] is False


def test_session_cookie_flags_and_logout(client: TestClient) -> None:
    r = login(client)
    cookie = r.headers["set-cookie"].lower()
    assert "httponly" in cookie and "samesite=strict" in cookie
    assert client.post("/api/v1/auth/logout", headers=CSRF).status_code == 200
    assert client.get("/api/v1/auth/me").status_code == 401


def test_csrf_header_and_origin_required(client: TestClient) -> None:
    assert client.post("/api/v1/auth/login",
                       json={"email": "owner@example.test", "password": PASSWORD}).status_code == 403
    r = client.post("/api/v1/auth/login", json={"email": "owner@example.test",
                                                "password": PASSWORD},
                    headers={**CSRF, "origin": "https://evil.example"})
    assert r.status_code == 403
    login(client)
    r = client.post("/api/v1/risk/kill-switch", json={"reason": "csrf test"})
    assert r.status_code == 403


def test_wrong_password_generic_error_and_lockout(client: TestClient) -> None:
    for _ in range(5):
        r = client.post("/api/v1/auth/login", json={"email": "owner@example.test",
                                                    "password": "wrong-password"}, headers=CSRF)
        assert r.status_code == 401 and r.json()["detail"] == "invalid email or password"
    unknown = client.post("/api/v1/auth/login", json={"email": "nobody@example.test",
                                                      "password": "x"}, headers=CSRF)
    assert unknown.json()["detail"] == "invalid email or password"  # no user enumeration
    assert login(client).status_code == 423  # locked even with the right password


def test_ip_rate_limit(client: TestClient) -> None:
    codes = [client.post("/api/v1/auth/login", json={"email": f"x{i}@example.test",
                                                     "password": "x"}, headers=CSRF).status_code
             for i in range(22)]
    assert codes[-1] == 429


def test_viewer_cannot_mutate(client: TestClient) -> None:
    login(client, email="viewer@example.test")
    assert client.post("/api/v1/risk/kill-switch", json={"reason": "viewer try"},
                       headers=CSRF).status_code == 403
    assert client.patch("/api/v1/strategies/momentum", json={"enabled": False},
                        headers=CSRF).status_code == 403
    assert client.get("/api/v1/security").status_code == 403


def test_cannot_enable_unvalidated_strategy(client: TestClient) -> None:
    login(client)
    r = client.patch("/api/v1/strategies/momentum", json={"enabled": True}, headers=CSRF)
    assert r.status_code == 409 and "validation" in r.json()["detail"]
    assert client.patch("/api/v1/strategies/momentum", json={"enabled": False},
                        headers=CSRF).status_code == 200


def _enable_totp(client: TestClient) -> str:
    secret = client.post("/api/v1/auth/totp/setup", headers=CSRF).json()["secret"]
    assert client.post("/api/v1/auth/totp/enable", json={"code": "000000"},
                       headers=CSRF).status_code == 400
    import time
    code = totp._code(secret, int(time.time() // 30))
    assert client.post("/api/v1/auth/totp/enable", json={"code": code},
                       headers=CSRF).status_code == 200
    return secret


def _code(secret: str, offset: int = 0) -> str:
    import time
    return totp._code(secret, int(time.time() // 30) + offset)


def _fresh_code(secret: str, used: set[int]) -> str:
    """A valid code for a 30 s step not used yet in this test (codes are single-use; a
    step boundary between two requests must not make the test reuse one)."""
    import time
    now = int(time.time() // 30)
    step = next(st for st in (now, now + 1, now - 1) if st not in used)
    used.add(step)
    return totp._code(secret, step)


def test_kill_switch_engage_easy_clear_needs_totp(client: TestClient) -> None:
    login(client)
    assert client.post("/api/v1/risk/kill-switch", json={"reason": "manual stop"},
                       headers=CSRF).json() == {"engaged": True}
    assert client.get("/api/v1/risk").json()["kill_switch"]["actor"].startswith("user:")
    r = client.post("/api/v1/risk/kill-switch/clear", json={"reason": "resume"}, headers=CSRF)
    assert r.status_code == 403 and "two-factor" in r.json()["detail"]
    secret = _enable_totp(client)
    code = _code(secret, 1)
    ok = client.post("/api/v1/risk/kill-switch/clear", json={"reason": "resume after review",
                                                             "totp": code}, headers=CSRF)
    assert ok.status_code == 200
    replay = client.post("/api/v1/risk/kill-switch/clear", json={"reason": "again please",
                                                                 "totp": code}, headers=CSRF)
    assert replay.status_code == 403 and "already used" in replay.json()["detail"]


def test_login_requires_totp_once_enabled(client: TestClient) -> None:
    login(client)
    secret = _enable_totp(client)
    client.post("/api/v1/auth/logout", headers=CSRF)
    assert login(client).status_code == 401
    assert login(client, totp=_code(secret)).status_code == 200


def test_credentials_encrypted_and_never_returned(client: TestClient,
                                                  migrated_engine: Engine) -> None:
    login(client)
    secret = _enable_totp(client)
    body = {"exchange": "binance", "label": "main", "api_key": "AKIA-PLAINTEXT-KEY-123",
            "api_secret": "SUPER-SECRET-VALUE-456", "withdrawals_disabled_confirmed": False,
            "totp": _code(secret, -1)}
    assert client.post("/api/v1/security/credentials", json=body, headers=CSRF).status_code == 400
    body.update(withdrawals_disabled_confirmed=True, totp=_code(secret, 1))
    r = client.post("/api/v1/security/credentials", json=body, headers=CSRF)
    assert r.status_code == 200, r.text
    listing = client.get("/api/v1/exchanges").text
    assert "PLAINTEXT" not in listing and "SUPER-SECRET" not in listing
    assert r.json()["fingerprint"] in listing
    with transaction(make_session_factory(migrated_engine)) as s:
        cred = s.scalar(select(models.ExchangeCredential))
        assert cred is not None and b"SUPER-SECRET" not in cred.api_secret_encrypted
        actions = s.scalars(select(models.AuditLog.action)).all()
        assert "credential_added" in actions and "totp_enabled" in actions


def test_live_approval_needs_totp_and_exact_phrase(client: TestClient) -> None:
    login(client)
    secret = _enable_totp(client)
    bad = client.post("/api/v1/security/live-approval",
                      json={"approve": True, "acknowledgement": "yes", "totp": _code(secret, -1)},
                      headers=CSRF)
    assert bad.status_code == 400
    ok = client.post("/api/v1/security/live-approval",
                     json={"approve": True, "acknowledgement": LIVE_TRADING_ACK_PHRASE,
                           "totp": _code(secret, 1)}, headers=CSRF)
    assert ok.status_code == 200
    sec = client.get("/api/v1/security").json()
    assert "no owner approval recorded" not in sec["live_trading_blockers"]
    assert sec["live_trading_permitted"] is False  # config gates still block (paper mode)


def test_security_headers(client: TestClient) -> None:
    h = client.get("/health/live").headers
    assert "frame-ancestors 'none'" in h["content-security-policy"]
    assert h["x-frame-options"] == "DENY"


def test_binance_connection_add_list_test_disconnect(client: TestClient,
                                                    monkeypatch: pytest.MonkeyPatch) -> None:
    from nexus_api import connection

    seen: dict[str, Any] = {}

    async def fake_futures(api_key: str, secret: str, *, environment: str,
                           cafile: str | None = None) -> dict[str, Any]:
        seen.update(api_key=api_key, secret=secret, environment=environment)
        return {"ok": True, "wallet_balance_usdt": "15000", "position_mode": "one-way"}

    monkeypatch.setitem(connection.PROBES, "futures", fake_futures)
    login(client)
    secret = _enable_totp(client)
    body = {"exchange": "binance", "label": "testnet", "api_key": "TESTNET-KEY-0001",
            "api_secret": "TESTNET-SECRET-0002", "withdrawals_disabled_confirmed": True,
            "totp": _code(secret, 1), "environment": "testnet", "markets": ["futures"]}
    r = client.post("/api/v1/security/credentials", json=body, headers=CSRF)
    assert r.status_code == 200, r.text
    cred_id = r.json()["id"]

    listing = client.get("/api/v1/security/credentials")
    assert listing.status_code == 200
    assert "TESTNET-KEY" not in listing.text and "TESTNET-SECRET" not in listing.text
    row = listing.json()["credentials"][0]
    assert row["environment"] == "testnet" and row["markets"] == ["futures"]
    assert row["last_check"] is None

    t = client.post(f"/api/v1/security/credentials/{cred_id}/test", headers=CSRF)
    assert t.status_code == 200, t.text
    assert t.json()["result"]["ok"] is True and t.json()["result"]["environment"] == "testnet"
    assert seen == {"api_key": "TESTNET-KEY-0001", "secret": "TESTNET-SECRET-0002",
                    "environment": "testnet"}
    assert "TESTNET-SECRET" not in t.text
    row = client.get("/api/v1/security/credentials").json()["credentials"][0]
    assert row["last_check"]["futures"]["wallet_balance_usdt"] == "15000"
    assert row["last_verified_at"] is not None

    assert client.delete(f"/api/v1/security/credentials/{cred_id}", headers=CSRF).status_code == 200
    assert client.get("/api/v1/security/credentials").json()["credentials"] == []
    assert client.post(f"/api/v1/security/credentials/{cred_id}/test",
                       headers=CSRF).status_code == 404


def test_live_account_views(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    from nexus_api import account

    login(client)
    monkeypatch.setattr(account, "CACHE_SECONDS", 2)
    r = client.get("/api/v1/account/futures")
    assert r.status_code == 200 and r.json()["connected"] is False

    calls: list[str] = []

    async def fake_futures(api_key: str, secret: str, *, environment: str,
                           cafile: str | None = None) -> dict[str, Any]:
        calls.append(environment)
        assert (api_key, secret) == ("TESTNET-KEY-0001", "TESTNET-SECRET-0002")
        return {"account": {"wallet_balance": "15000"}, "positions": [], "open_orders": [],
                "last_7_days": {"realized_pnl": "0", "funding": "0", "commission": "0"}}

    monkeypatch.setattr(account, "futures_snapshot", fake_futures)
    secret = _enable_totp(client)
    body = {"exchange": "binance", "label": "testnet", "api_key": "TESTNET-KEY-0001",
            "api_secret": "TESTNET-SECRET-0002", "withdrawals_disabled_confirmed": True,
            "totp": _code(secret, 1), "environment": "testnet", "markets": ["futures"]}
    assert client.post("/api/v1/security/credentials", json=body, headers=CSRF).status_code == 200
    r = client.get("/api/v1/account/futures").json()
    assert r["connected"] and r["ok"] and r["environment"] == "testnet" and not r["cached"]
    assert r["data"]["account"]["wallet_balance"] == "15000"
    assert "TESTNET-SECRET" not in client.get("/api/v1/account/futures").text
    assert client.get("/api/v1/account/futures").json()["cached"] is True
    assert calls == ["testnet"]  # cached: Binance called once
    spot = client.get("/api/v1/account/spot").json()
    assert spot["connected"] is False  # key has futures access only

    # A Binance IP ban is remembered: no further calls until it ends.
    import ccxt.async_support as ccxt_async
    from nexus_common.timeutil import utcnow

    until_ms = int((utcnow().timestamp() + 7200) * 1000)

    async def banned_futures(*a: Any, **k: Any) -> dict[str, Any]:
        calls.append("banned")
        raise ccxt_async.DDoSProtection(
            f'binance {{"code":-1003,"msg":"Way too many requests; IP banned until {until_ms}."}}')

    monkeypatch.setattr(account, "futures_snapshot", banned_futures)
    import time
    time.sleep(2.1)  # let the cached success expire
    first = client.get("/api/v1/account/futures").json()
    assert first["ok"] is False and "temporarily blocked" in first["error"]
    time.sleep(2.1)
    second = client.get("/api/v1/account/futures").json()
    assert second["ok"] is False and "banned_until" in second
    assert calls.count("banned") == 1  # Binance was not called again during the ban
    assert client.get("/api/v1/account/margin").status_code == 422


def test_telegram_status_and_test_without_config(client: TestClient) -> None:
    login(client)
    r = client.get("/api/v1/telegram").json()
    assert r["configured"] is False and any(e["type"] == "daily_report" for e in r["events"])
    t = client.post("/api/v1/telegram/test", headers=CSRF)
    assert t.status_code == 400 and "not configured" in t.text


def test_loss_streak_reset_needs_totp(client: TestClient) -> None:
    login(client)
    r = client.get("/api/v1/risk").json()
    assert "loss_streak" in r
    body = {"reason": "reviewed the losing trades", "totp": "000000"}
    assert client.post("/api/v1/risk/streak/reset", json=body, headers=CSRF).status_code == 403
    secret = _enable_totp(client)
    body["totp"] = _code(secret, 1)
    ok = client.post("/api/v1/risk/streak/reset", json=body, headers=CSRF)
    assert ok.status_code == 200 and ok.json()["reset_at"]


def test_backtests_listing_filters_by_market(client: TestClient, migrated_engine: Engine) -> None:
    from datetime import UTC, datetime
    from decimal import Decimal

    with transaction(make_session_factory(migrated_engine)) as s:
        t = datetime(2026, 1, 1, tzinfo=UTC)
        for mkt in ("spot", "futures"):
            s.add(models.BacktestRun(
                status="succeeded", is_walk_forward=True,
                config={"market": mkt, "symbols": ["BTCUSDT"]} if mkt == "futures" else {},
                data_start=t, data_end=datetime(2026, 2, 1, tzinfo=UTC), fee_bps=Decimal(5),
                slippage_bps=Decimal(2),
                metrics={"summary": {"n": 3}, "passed": False, "failures": ["x"],
                         "windows": [{"test_start": "2026-01-01", "test": {"n": 3}}]}))
    login(client)
    allr = client.get("/api/v1/backtests").json()["runs"]
    fut = client.get("/api/v1/backtests?market=futures").json()["runs"]
    assert {r["market"] for r in allr} == {"spot", "futures"}
    assert len(fut) == 1 and fut[0]["symbols"] == ["BTCUSDT"] and fut[0]["windows"][0]["test"]["n"] == 3
    with migrated_engine.begin() as conn:
        from sqlalchemy import text
        conn.execute(text("DELETE FROM backtest_runs"))



def test_demo_keys_cannot_be_added(client: TestClient) -> None:
    """Binance Demo Trading is not supported (owner decision 2026-11-23)."""
    login(client)
    secret = _enable_totp(client)
    body = {"exchange": "binance", "label": "demo", "api_key": "DEMO-KEY-000001",
            "api_secret": "DEMO-SECRET-0002", "withdrawals_disabled_confirmed": True,
            "totp": _code(secret, 1), "environment": "demo", "markets": ["futures"]}
    assert client.post("/api/v1/security/credentials", json=body,
                       headers=CSRF).status_code == 422
    assert client.get("/api/v1/security/credentials").json()["credentials"] == []


def test_binance_reachability_is_owner_only(client: TestClient,
                                            monkeypatch: pytest.MonkeyPatch) -> None:
    from nexus_api import routes

    monkeypatch.setattr(routes, "BINANCE_PINGS", {})
    login(client, email="viewer@example.test")
    assert client.get("/api/v1/system/binance-reachability").status_code == 403
    client.post("/api/v1/auth/logout", headers=CSRF)
    login(client)
    r = client.get("/api/v1/system/binance-reachability")
    assert r.status_code == 200 and r.json()["hosts"] == {}


def test_order_test_endpoint_is_gone(client: TestClient) -> None:
    login(client)
    assert client.post("/api/v1/security/credentials/1/order-test",
                       headers=CSRF).status_code == 404


def test_x_endpoints_without_a_token(client: TestClient) -> None:
    assert client.get("/api/v1/x/status").status_code in (401, 403)
    login(client)
    st = client.get("/api/v1/x/status").json()
    assert st["configured"] is False and st["read_only"] is True
    assert st["coins"] == ["BTC", "ETH", "SOL", "XRP"]
    snap = client.get("/api/v1/x/sentiment").json()
    assert snap["available"] is False and "NEXUS_X_BEARER_TOKEN" in snap["message"]
    assert "never opens a trade" in snap["policy"]


def test_kill_switch_state_survives_redis_loss_and_close_needs_confirmation(
        client: TestClient) -> None:
    import os

    import redis as sync_redis
    from nexus_common import events

    login(client)
    assert client.post("/api/v1/risk/kill-switch/close-positions",
                       json={"mode": "paper", "confirm": "CLOSE PAPER POSITIONS"},
                       headers=CSRF).status_code == 409          # not engaged yet
    client.post("/api/v1/risk/kill-switch", json={"reason": "manual stop"}, headers=CSRF)
    sync_redis.Redis.from_url(os.environ["NEXUS_TEST_REDIS_URL"]).delete(
        events.KILL_SWITCH_KEY)                                  # simulate Redis data loss
    ks = client.get("/api/v1/risk").json()["kill_switch"]
    assert ks is not None and ks["reason"] == "manual stop"     # durable record
    st = client.get("/api/v1/risk/kill-switch/state").json()
    assert st["modes"]["paper"]["open_positions"] == []
    assert st["modes"]["live"]["confirm_phrase"] == "CLOSE ALL LIVE POSITIONS"
    bad = client.post("/api/v1/risk/kill-switch/close-positions",
                      json={"mode": "paper", "confirm": "yes"}, headers=CSRF)
    assert bad.status_code == 400
    live = client.post("/api/v1/risk/kill-switch/close-positions",
                       json={"mode": "live", "confirm": "CLOSE ALL LIVE POSITIONS"},
                       headers=CSRF)
    assert live.status_code == 403                               # live needs 2FA
    ok = client.post("/api/v1/risk/kill-switch/close-positions",
                     json={"mode": "paper", "confirm": "CLOSE PAPER POSITIONS"}, headers=CSRF)
    assert ok.status_code == 200 and ok.json()["requested"] is True
    assert client.get("/api/v1/risk/kill-switch/state").json()["modes"]["paper"][
        "close_all_requested"] is True


def test_agent_proposals_say_no_trade_without_data_and_reviews_empty(
        client: TestClient) -> None:
    login(client)
    r = client.get("/api/v1/agents/proposals")
    assert r.status_code == 200
    body = r.json()
    assert body["proposals"] and all(p["final_decision"] == "NO TRADE"
                                     for p in body["proposals"])
    p = body["proposals"][0]
    assert p["reasons_to_reject"] and p["risk_manager"]["verdict"] == "REJECT"
    assert len(p["agents"]) == 6 and "No guaranteed profits" in body["disclaimer"]
    rev = client.get("/api/v1/agents/reviews").json()
    assert rev["reviews"] == []


def test_execution_quality_endpoint_shows_simulation_settings(client: TestClient) -> None:
    login(client)
    r = client.get("/api/v1/execution/quality?mode=paper").json()
    assert r["simulation"]["latency_ms"] == 250 and r["simulation"]["outage_rate"] == 0.0


def test_websocket_stream_requires_owner_session_and_same_origin(client: TestClient) -> None:
    from starlette.websockets import WebSocketDisconnect

    host = "testserver"
    good = {"origin": f"http://{host}"}
    with pytest.raises(WebSocketDisconnect):                      # not signed in
        with client.websocket_connect("/api/v1/ws/stream", headers=good) as ws:
            ws.receive_json()
    login(client)
    with pytest.raises(WebSocketDisconnect):                      # cross-site origin
        with client.websocket_connect("/api/v1/ws/stream",
                                      headers={"origin": "https://evil.example"}) as ws:
            ws.receive_json()
    with pytest.raises(WebSocketDisconnect):                      # no origin at all
        with client.websocket_connect("/api/v1/ws/stream") as ws:
            ws.receive_json()
    with client.websocket_connect("/api/v1/ws/stream", headers=good) as ws:
        tick = ws.receive_json()
    assert tick["type"] == "tick" and tick["kill_switch"] is False
    assert {b["symbol"] for b in tick["books"]} >= {"BTC/USDT"}


def test_news_settings_overview_context_and_report_metrics(client: TestClient) -> None:
    login(client)
    n = client.get("/api/v1/news").json()
    assert n["available"] is False and n["blocks_entries"] is True        # fails closed
    st = client.get("/api/v1/settings/summary")
    assert st.status_code == 200
    body = st.json()
    assert body["live_trading_blockers"] and body["paper_simulation"]["latency_ms"] == 250
    text = st.text.lower()
    for secret in ("password", "secret_value", "bearer", "encryption_keys\": \""):
        assert secret not in text
    ctx = client.get("/api/v1/overview/context").json()
    assert ctx["risk_status"]["news_blocks_entries"] is True and ctx["regimes"] == []


def test_risk_state_floor_stricter_is_easy_relaxing_live_needs_totp(client: TestClient) -> None:
    login(client)
    state = client.get("/api/v1/risk/state").json()
    assert state["paper"]["owner_floor"] == "NORMAL" and state["live"]["current"] is None
    r = client.post("/api/v1/risk/state/floor", json={"mode": "live", "state": "CLOSE_ONLY",
                                                     "reason": "investigating"}, headers=CSRF)
    assert r.status_code == 200 and r.json()["owner_floor"] == "CLOSE_ONLY"
    assert client.get("/api/v1/risk/state").json()["live"]["owner_floor"] == "CLOSE_ONLY"
    relax = {"mode": "live", "state": "NORMAL", "reason": "all checked"}
    r = client.post("/api/v1/risk/state/floor", json=relax, headers=CSRF)
    assert r.status_code == 403 and "two-factor" in r.json()["detail"]
    secret = _enable_totp(client)
    r = client.post("/api/v1/risk/state/floor", json={**relax, "totp": _code(secret, 1)},
                    headers=CSRF)
    assert r.status_code == 200
    # EMERGENCY_STOP is the kill switch, not a floor; unknown states are refused.
    bad = client.post("/api/v1/risk/state/floor", json={"mode": "paper",
                                                       "state": "EMERGENCY_STOP",
                                                       "reason": "nope nope"}, headers=CSRF)
    assert bad.status_code == 422
    login(client, "viewer@example.test")
    denied = client.post("/api/v1/risk/state/floor", json={"mode": "paper", "state": "NORMAL",
                                                          "reason": "viewer try"}, headers=CSRF)
    assert denied.status_code == 403


def test_operating_mode_easy_to_lower_live_locked_and_ledger_needs_live_portfolio(
        client: TestClient) -> None:
    login(client)
    m = client.get("/api/v1/settings/operating-mode").json()
    assert m["mode"] == "PAPER_TRADING" and m["set_by"] is None
    assert m["paper_orders"] is True and m["live_orders"] is False
    assert any("no enabled strategy" in b for b in m["live_blockers"])
    r = client.post("/api/v1/settings/operating-mode",
                    json={"mode": "SIGNALS_ONLY", "reason": "watch first"}, headers=CSRF)
    assert r.status_code == 200
    m = client.get("/api/v1/settings/operating-mode").json()
    assert m["mode"] == "SIGNALS_ONLY" and m["set_by"].startswith("user:")
    live = {"mode": "LIVE_AUTONOMOUS", "reason": "go live now"}
    assert client.post("/api/v1/settings/operating-mode", json=live,
                       headers=CSRF).status_code == 400                       # phrase
    live["confirm"] = m["live_confirm_phrase"]
    r = client.post("/api/v1/settings/operating-mode", json=live, headers=CSRF)
    assert r.status_code == 403                                               # 2FA
    secret = _enable_totp(client)
    import time
    used = {int(time.time() // 30)}            # the step used to enable 2FA
    r = client.post("/api/v1/settings/operating-mode",
                    json={**live, "totp": _fresh_code(secret, used)}, headers=CSRF)
    assert r.status_code == 409 and "not permitted" in r.json()["detail"]
    assert client.get("/api/v1/settings/operating-mode").json()["mode"] == "SIGNALS_ONLY"
    fund = {"amount": "10", "confirm": "FUND LIVE LEDGER",
            "totp": _fresh_code(secret, used)}
    r = client.post("/api/v1/live/ledger/fund", json=fund, headers=CSRF)
    assert r.status_code == 409 and "no live portfolio" in r.json()["detail"]
    bad = client.post("/api/v1/live/ledger/fund", json={**fund, "confirm": "yes"}, headers=CSRF)
    assert bad.status_code == 400
    login(client, "viewer@example.test")
    assert client.post("/api/v1/settings/operating-mode",
                       json={"mode": "PAPER_TRADING", "reason": "viewer try"},
                       headers=CSRF).status_code == 403


def test_technical_analysis_routes_on_stored_candles(client: TestClient,
                                                     migrated_engine: Engine) -> None:
    from datetime import timedelta
    from decimal import Decimal as D

    import numpy as np
    from nexus_common.timeutil import utcnow
    from nexus_db import make_session_factory, repo

    factory = make_session_factory(migrated_engine)
    rng = np.random.default_rng(1)
    with transaction(factory) as s:
        pid = repo.upsert_trading_pair(
            s, exchange_id=repo.exchange_id(s, "binance"), symbol="BTC/USDT",
            exchange_symbol="BTCUSDT", base="BTC", quote="USDT", price_tick=D("0.01"),
            amount_step=D("0.00001"), min_amount=None, min_notional=None, maker_fee=None,
            taker_fee=None, synced_at=utcnow())
        start = utcnow().replace(minute=0, second=0, microsecond=0) - timedelta(hours=400)
        price, rows = 60_000.0, []
        for i in range(400):
            nxt = price * float(np.exp(rng.normal(0, 0.004)))
            rows.append(repo.CandleRow(start + timedelta(hours=i), D(str(round(price, 2))),
                                       D(str(round(max(price, nxt) * 1.001, 2))),
                                       D(str(round(min(price, nxt) * 0.999, 2))),
                                       D(str(round(nxt, 2))), D("10")))
            price = nxt
        repo.upsert_candles(s, pid, "1h", rows)
    try:
        login(client)
        tech = client.get("/api/v1/analysis/technical?symbol=BTC/USDT&timeframe=1h").json()
        assert tech["bars"] == 400 and tech["trend"]["ema_200"] is not None
        assert tech["momentum"]["rsi"] is not None and tech["limitations"]
        mtf = client.get("/api/v1/analysis/mtf?symbol=BTC/USDT&base=1h").json()
        assert set(mtf["timeframes"]) == {"1h"} and "5m" in mtf["missing_timeframes"]
        assert mtf["scores"]["trend_agreement"] is not None
        assert client.get("/api/v1/analysis/mtf?symbol=DOGE/USDT").status_code == 404
    finally:
        truncate(migrated_engine, "market_candles, trading_pairs")


def test_owner_approval_of_bot_strategy_needs_ack_and_totp(client: TestClient) -> None:
    login(client)
    url = "/api/v1/strategies/bot_ema_rsi_macd/owner-approval"
    ack = "I accept this strategy has not passed validation"
    r = client.post(url, json={"active": True, "acknowledgement": "yes"}, headers=CSRF)
    assert r.status_code == 400 and "type exactly" in r.json()["detail"]
    r = client.post(url, json={"active": True, "acknowledgement": ack}, headers=CSRF)
    assert r.status_code == 403                         # no two-factor configured or code
    assert client.post("/api/v1/strategies/momentum/owner-approval",
                       json={"active": True, "acknowledgement": ack},
                       headers=CSRF).status_code == 400   # only the bot strategy is offered
    secret = _enable_totp(client)
    used: set[int] = set()
    r = client.post(url, json={"active": True, "acknowledgement": ack,
                               "totp": _fresh_code(secret, used)}, headers=CSRF)
    assert r.status_code == 200 and r.json()["active"] is True
    status = client.get("/api/v1/trading-bot").json()["strategy"]["owner_approval"]
    assert status["active"] is True and status["actor"].startswith("user:")
    off = client.post(url, json={"active": False}, headers=CSRF)   # disabling: no 2FA
    assert off.status_code == 200 and off.json()["active"] is False


def test_exchange_capability_registry_refresh(client: TestClient,
                                              monkeypatch: pytest.MonkeyPatch) -> None:
    """Owner-only refresh; detection results are cached and served; Binance is skipped (not
    called) while its rate-limit circuit is open."""
    import json as _json
    from datetime import timedelta

    from nexus_common.timeutil import utcnow
    from nexus_exchange import registry

    calls: list[str] = []

    def fake(ex: str, **_kw: Any) -> dict[str, Any]:
        calls.append(ex)
        return {"exchange": ex, "ok": ex != "bybit", "checked_at": "t",
                **({"error": "RateLimitExceeded: 403 Forbidden"} if ex == "bybit" else {})}

    monkeypatch.setattr(registry, "detect", fake)
    assert client.post("/api/v1/exchanges/capabilities/refresh", json={},
                       headers=CSRF).status_code == 401            # not signed in
    login(client)
    r = sync_redis.from_url(os.environ["NEXUS_TEST_REDIS_URL"])
    r.set("nexus:binance:public_backoff:spot", (utcnow() + timedelta(hours=1)).isoformat(),
          ex=3600)
    res = client.post("/api/v1/exchanges/capabilities/refresh", json={}, headers=CSRF)
    assert res.status_code == 200, res.text
    doc = res.json()["exchanges"]
    assert calls == ["okx", "bybit", "kraken", "kucoin", "gate"]     # Binance not called
    assert doc["binance"]["ok"] is False and "skipped" in doc["binance"]["error"]
    assert doc["bybit"]["error"].startswith("RateLimitExceeded")
    cached = client.get("/api/v1/exchanges/capabilities").json()
    assert set(cached["exchanges"]) == {"binance", "okx", "bybit", "kraken", "kucoin", "gate"}
    assert _json.dumps(cached)                                        # serialisable


def test_metrics_endpoint_needs_token_and_exposes_health_only(
        migrated_engine: Engine, db_url: str, client: TestClient) -> None:
    from nexus_api.metrics import HELP, render

    # Without NEXUS_METRICS_TOKEN the endpoint does not exist.
    assert client.get("/metrics").status_code == 404
    url = os.environ["NEXUS_TEST_REDIS_URL"]
    settings = Settings(database_url=db_url, redis_url=url,
                        credential_encryption_keys=generate_key_entry("k1"),
                        binance_public_api_url="http://127.0.0.1:9/api/v3",
                        metrics_token="m" * 32)
    with TestClient(create_app(settings)) as c:
        assert c.get("/metrics").status_code == 401
        assert c.get("/metrics", headers={"Authorization": "Bearer wrong"}).status_code == 401
        r = c.get("/metrics", headers={"Authorization": "Bearer " + "m" * 32})
        assert r.status_code == 200 and r.headers["content-type"].startswith("text/plain")
        body = r.text
        assert "nexus_up 1" in body
        assert 'nexus_dependency_up{dependency="database"} 1' in body
        assert 'nexus_dependency_up{dependency="redis"} 1' in body
        assert 'nexus_binance_circuit_open{scope="spot"} 0' in body
        assert "nexus_kill_switch_engaged 0" in body
        assert "USDT" not in body or "nexus_market_data_age_seconds" in body   # no balances
    assert render([("a", {"x": "1"}, 2.0), ("a", {"x": "2"}, 3.5)], HELP) == (
        '# HELP a a\n# TYPE a gauge\na{x="1"} 2\na{x="2"} 3.5\n')


def test_edge_monitor_reset_needs_owner_totp(client: TestClient,
                                             migrated_engine: Engine) -> None:
    with migrated_engine.begin() as conn:
        from sqlalchemy import text
        conn.execute(text("""UPDATE strategies SET parameters = '{"edge_monitor": {"7":
            {"state": "STOP", "multiplier": 0, "reasons": ["CUSUM 5.0 >= 4.0"]}}}'::jsonb
            WHERE code = 'momentum'"""))
    login(client)
    mons = client.get("/api/v1/strategies/edge-monitor").json()["monitors"]
    assert any(m["strategy"] == "momentum" and m["state"] == "STOP" for m in mons)
    body = {"portfolio_id": 7, "reason": "reviewed the losing streak"}
    assert client.post("/api/v1/strategies/momentum/edge-reset", json=body,
                       headers=CSRF).status_code == 403            # no two-factor
    secret = _enable_totp(client)
    used: set[int] = set()
    ok = client.post("/api/v1/strategies/momentum/edge-reset",
                     json={**body, "totp": _fresh_code(secret, used)}, headers=CSRF)
    assert ok.status_code == 200, ok.text
    mons = client.get("/api/v1/strategies/edge-monitor").json()["monitors"]
    m = next(x for x in mons if x["strategy"] == "momentum")
    assert m["state"] == "NORMAL" and m["reset_by"].startswith("user:")


def test_testnet_round_trip_needs_totp_and_a_testnet_key(client: TestClient) -> None:
    login(client)
    assert client.post("/api/v1/system/testnet/round-trip", json={},
                       headers=CSRF).status_code == 403          # no two-factor
    secret = _enable_totp(client)
    used: set[int] = set()
    r = client.post("/api/v1/system/testnet/round-trip",
                    json={"totp": _fresh_code(secret, used)}, headers=CSRF)
    assert r.status_code == 409 and "TESTNET key" in r.json()["detail"]
    assert client.get("/api/v1/system/testnet/round-trip").json() == {"runs": []}


def test_strategy_test_and_grid_bot_endpoints(client: TestClient) -> None:
    assert client.get("/api/v1/research/freqtrade-community").status_code == 401
    assert client.get("/api/v1/grid-bot").status_code == 401
    login(client)
    r = client.get("/api/v1/research/freqtrade-community")
    assert r.status_code == 200
    doc = r.json()
    assert doc["summary"]["tested"] == len(doc["ranked"]) and doc["summary"]["passed"] == 0
    assert all(not x["passed"] and x["gate_failures"] for x in doc["ranked"])
    g = client.get("/api/v1/grid-bot")
    assert g.status_code == 200 and g.json()["paper"] is True
    assert client.post("/api/v1/grid-bot/restart", json={"reason": "x"},
                       headers=CSRF).status_code == 422          # reason too short
    ok = client.post("/api/v1/grid-bot/restart", json={"reason": "after stop-loss"},
                     headers=CSRF)
    assert ok.status_code == 200 and ok.json() == {"requested": True}


def test_adaptive_grid_endpoints_controls_and_limits(client: TestClient) -> None:
    import json as _json

    for path in ("/api/v1/adaptive-grid", "/api/v1/adaptive-grid/universe"):
        assert client.get(path).status_code == 401
    assert client.post("/api/v1/adaptive-grid/control", json={"action": "pause",
                       "reason": "testing"}, headers=CSRF).status_code == 401
    login(client)
    r = client.get("/api/v1/adaptive-grid")
    assert r.status_code == 200 and r.json()["paper"] is True and "PAPER" in r.json()["note"]
    bt = r.json()["backtest"]                     # the committed, pre-registered result
    assert bt["passed"] is False and not any(bt["gates"].values())
    assert bt["oos"]["net_return"] < 0 and "SIDEWAYS" in bt["by_regime"]
    assert client.get("/api/v1/adaptive-grid/universe").json() == {"spot": None,
                                                                   "futures": None}
    bad = client.post("/api/v1/adaptive-grid/control", json={"action": "go_live",
                      "reason": "testing"}, headers=CSRF)
    assert bad.status_code == 422                                # no such action exists
    ok = client.post("/api/v1/adaptive-grid/control", json={"action": "emergency_shutdown",
                     "reason": "drill"}, headers=CSRF)
    assert ok.status_code == 200
    rds = sync_redis.from_url(os.environ["NEXUS_TEST_REDIS_URL"])
    queued = [_json.loads(x) for x in rds.lrange("nexus:agrid:commands", 0, -1)]
    assert queued[-1]["action"] == "emergency_shutdown"
    over = client.post("/api/v1/adaptive-grid/limits", json={
        "limits": {"daily_loss": "0.05"}, "reason": "too loose"}, headers=CSRF)
    assert over.status_code == 422 and "daily_loss" in over.text
    good = client.post("/api/v1/adaptive-grid/limits", json={
        "limits": {"daily_loss": "0.005"}, "reason": "tighter"}, headers=CSRF)
    assert good.status_code == 200 and good.json()["limits"]["daily_loss"] == "0.005"
    audit = client.get("/api/v1/security").json()["audit"]
    assert {"agrid_emergency_shutdown", "agrid_limits"} <= {a["action"] for a in audit}


def test_rebalancing_research_endpoint(client: TestClient) -> None:
    assert client.get("/api/v1/research/rebalancing").status_code == 401
    login(client)
    r = client.get("/api/v1/research/rebalancing").json()
    assert r["backtest"]["summary"]["rebalancing_passed"] == ["R3_BTC40_ETH40_USDT20:band"]
    assert r["backtest"]["summary"]["xsmom_passed"] == []
    assert r["forward"]["paper"] is True and r["forward"]["days"] == 0


def test_batch3_research_endpoint(client: TestClient) -> None:
    assert client.get("/api/v1/research/batch3").status_code == 401
    login(client)
    r = client.get("/api/v1/research/batch3").json()
    s = r["backtest"]["summary"]
    assert s["grid_v2_passed"] is False and s["pairs_passed"] is False
    assert s["vol_target_passed"] == ["BTC_ETH_50_50"]
    assert r["forward"]["paper"] is True and r["forward"]["days"] == 0
    assert r["preregistration"].endswith("batch3-preregistration.md")


def test_market_making_research_endpoint(client: TestClient) -> None:
    assert client.get("/api/v1/research/market-making").status_code == 401
    login(client)
    r = client.get("/api/v1/research/market-making").json()
    assert r["backtest"]["summary"] == {"passed": [], "tested": 8}
    assert all(v["plain"]["pnl_before_fees"] < 0 for v in r["backtest"]["variants"].values())
    assert r["preregistration"].endswith("market-making-preregistration.md")


def test_statarb_research_endpoint(client: TestClient) -> None:
    assert client.get("/api/v1/research/statarb-ml").status_code == 401
    login(client)
    r = client.get("/api/v1/research/statarb-ml").json()
    assert r["backtest"]["summary"]["passed"] == []
    assert set(r["backtest"]["models"]) == {"ridge", "xgboost"}
    assert r["backtest"]["models"]["ridge"]["breakdown"]["price_moves"] > 0
    assert r["preregistration"].endswith("statarb-ml-preregistration.md")


def test_markets_overview_and_per_market_switch(client: TestClient) -> None:
    assert client.get("/api/v1/markets").status_code == 401
    login(client)
    m = client.get("/api/v1/markets").json()
    by = {x["market"]: x for x in m["markets"]}
    assert set(by) == {"crypto", "stocks", "commodities", "forex"}
    assert by["crypto"]["autonomy"] == "PAPER" and by["crypto"]["paper_orders"] is True
    assert by["stocks"]["autonomy"] == "PAPER" and by["stocks"]["allowed"] == [
        "OFF", "RESEARCH", "PAPER"]
    assert by["stocks"]["paper_forward"]["paper"] is True
    for k in ("commodities", "forex"):          # PAPER = orders on an Exness DEMO account
        assert by[k]["autonomy"] == "RESEARCH" and by[k]["allowed"] == [
            "OFF", "RESEARCH", "PAPER"]
    for k in ("stocks", "commodities", "forex"):
        assert all(v["connector"] in ("not_built", "read_only", "demo_only")
                   for v in by[k]["venues"] if v["role"] == "broker")
        assert by[k]["live_blockers"] and by[k]["paper_positions"] is None
    r = client.post("/api/v1/markets/forex/autonomy",
                    json={"autonomy": "LIVE", "reason": "try forex live"}, headers=CSRF)
    assert r.status_code in (400, 409)                       # never possible for forex
    r = client.post("/api/v1/markets/forex/autonomy",
                    json={"autonomy": "PAPER", "reason": "exness demo"}, headers=CSRF)
    assert r.status_code == 200, r.text
    r = client.post("/api/v1/markets/forex/autonomy",
                    json={"autonomy": "RESEARCH", "reason": "back to research"}, headers=CSRF)
    assert r.status_code == 200
    r = client.post("/api/v1/markets/stocks/autonomy",
                    json={"autonomy": "LIVE", "reason": "try stocks live"}, headers=CSRF)
    assert r.status_code == 409 and "no broker connector" in r.json()["detail"]
    m2 = client.get("/api/v1/research/multi-asset").json()
    assert m2["ndx_voltarget"]["passed"] is True
    assert m2["trend"]["summary"]["passed"] == []
    assert m2["fx_styles"]["summary"]["passed"] == []
    assert set(m2["fx_styles"]["strategies"]) == {"carry", "dollar_carry", "momentum", "value",
                                                  "combined"}
    nq = m2["nqs1"]
    assert nq["status"] == "result" and set(nq["result"]["profiles"]) == {
        "scalping", "intraday", "swing"}                     # not the data-quality file
    assert not any(p["walk_forward"]["passed"] for p in nq["result"]["profiles"].values())
    night = m2["night_scalper"]
    assert set(night["accounts"]) == {"Standard", "Raw Spread"}
    assert not any(a["passed"] for a in night["accounts"].values())
    grid = m2["binance_grid"]
    assert len(grid["configs"]) == 6 and not any(c["passed"] for c in grid["configs"].values())
    assert grid["original_run"]["harness_commit"] and grid["holding"]["net_return"] > 0
    league = m2["bot_league"]
    assert league["ranking_by_sharpe"][0] == "nexus_vol" and len(league["contenders"]) == 6
    exvol = m2["exness_voltarget"]
    assert set(exvol["portfolios"]) == {"gold", "nasdaq100", "gold_nasdaq_50_50"}
    assert not any(p["passed"] for p in exvol["portfolios"].values())
    assert client.post("/api/v1/markets/gold/autonomy",
                       json={"autonomy": "OFF", "reason": "unknown"},
                       headers=CSRF).status_code == 404
    r = client.post("/api/v1/markets/crypto/autonomy",
                    json={"autonomy": "RESEARCH", "reason": "watch crypto first"}, headers=CSRF)
    assert r.status_code == 200
    c = {x["market"]: x for x in client.get("/api/v1/markets").json()["markets"]}["crypto"]
    assert c["autonomy"] == "RESEARCH" and c["effective_mode"] == "SIGNALS_ONLY"
    assert c["paper_orders"] is False and c["set_by"].startswith("user:")
    live = {"autonomy": "LIVE", "reason": "go live on crypto"}
    assert client.post("/api/v1/markets/crypto/autonomy", json=live,
                       headers=CSRF).status_code == 400                       # phrase
    live["confirm"] = c["live_confirm_phrase"]
    assert client.post("/api/v1/markets/crypto/autonomy", json=live,
                       headers=CSRF).status_code == 403                       # 2FA
    r = client.post("/api/v1/markets/crypto/autonomy",
                    json={"autonomy": "PAPER", "reason": "back to paper"}, headers=CSRF)
    assert r.status_code == 200
    audit = client.get("/api/v1/security").json()["audit"]
    assert sum(1 for a in audit if a["action"] == "market_autonomy_set") >= 2


def _snapshot(**over: Any) -> dict[str, Any]:
    day = 86400
    now = int(time.time())
    base = now - now % day                              # today 00:00 UTC
    offset = 2                                          # broker server time UTC+2
    bars = [[base - day * (i + 1) + offset * 3600, 1.1, 1.2, 1.0, 1.15, 100]
            for i in range(3)][::-1]
    bars.append([base + offset * 3600, 1.15, 1.16, 1.14, 1.155, 5])     # forming: dropped
    snap = {"bridge_version": "1.0", "read_only": True, "server_utc_offset_hours": offset,
            "account": {"login": 87654321, "trade_mode": "demo", "currency": "USD",
                        "balance": "10000", "equity": "10010", "margin": "0",
                        "margin_free": "10010", "leverage": 2000, "server": "Exness-MT5Trial",
                        "company": "Exness"},
            "symbols": [
                {"name": n, "base_name": b, "digits": 5, "point": "0.00001",
                 "contract_size": cs, "volume_min": "0.01", "volume_step": "0.01",
                 "volume_max": "200", "swap_long": "-7.1", "swap_short": "1.2",
                 "spread_points": 12, "currency_base": cb, "currency_profit": "USD",
                 "bid": "1.0981", "ask": "1.0982", "quote_time": now}
                for n, b, cs, cb in (("EURUSDm", "EURUSD", "100000", "EUR"),
                                     ("XAUUSDm", "XAUUSD", "100", "XAU"),
                                     ("US500m", "US500", "1", "US500"))],
            "bars": {"EURUSDm": bars},
            "positions": [{"symbol": "EURUSDm", "side": "sell", "volume": "0.01",
                           "price_open": "1.1", "profit": "-1.5", "swap": "-0.2"}],
            "unresolved": ["USOIL"]}
    snap.update(over)
    return snap


def test_exness_bridge_tokens_and_read_only_snapshots(client: TestClient,
                                                      migrated_engine: Engine) -> None:
    url = "/api/v1/bridge/mt5/snapshot"
    assert client.post(url, json=_snapshot()).status_code == 401                # no token
    assert client.post(url, json=_snapshot(),
                       headers={"Authorization": "Bearer nxb_wrong"}).status_code == 401
    assert client.post("/api/v1/bridge-tokens", json={"name": "pc", "reason": "x"},
                       headers=CSRF).status_code == 401                          # signed out
    login(client)
    r = client.post("/api/v1/bridge-tokens", json={"name": "home pc", "reason": "exness demo"},
                    headers=CSRF)
    assert r.status_code == 200 and r.json()["token"].startswith("nxb_")
    token, bid = r.json()["token"], r.json()["id"]
    listed = client.get("/api/v1/bridge-tokens").json()["bridges"]
    assert listed[0]["name"] == "home pc" and "token" not in listed[0]       # never shown again
    auth = {"Authorization": f"Bearer {token}"}
    bad = client.post(url, json=_snapshot(read_only=False), headers=auth)
    assert bad.status_code == 409
    assert client.post(url, json=_snapshot(symbols=[{"name": "EUR;DROP", "base_name": "x"}]),
                       headers=auth).status_code == 422
    ok = client.post(url, json=_snapshot(), headers=auth)
    assert ok.status_code == 200, ok.text
    body = ok.json()
    assert body["bars_written"] == 3                                    # forming bar dropped
    assert set(body["need_history"]) == {"EURUSDm", "XAUUSDm", "US500m"}
    with migrated_engine.begin() as c:
        from sqlalchemy import text
        pairs = {r[0]: r[1:] for r in c.execute(text(
            "SELECT p.symbol, p.asset_class, p.instrument_type, p.contract_multiplier, "
            "p.exchange_symbol FROM trading_pairs p JOIN exchanges e ON e.id = p.exchange_id "
            "WHERE e.code = 'exness'"))}
        assert pairs["EURUSD"][:2] == ("forex", "fx_spot") and pairs["EURUSD"][3] == "EURUSDm"
        assert pairs["XAUUSD"][:2] == ("commodities", "cfd") and pairs["XAUUSD"][2] == 100
        assert pairs["US500"][:2] == ("stocks", "cfd")
        times = [r[0] for r in c.execute(text(
            "SELECT c.open_time FROM market_candles c JOIN trading_pairs p ON p.id = c.pair_id "
            "WHERE p.exchange_symbol = 'EURUSDm' ORDER BY 1"))]
        assert len(times) == 3 and all(t.hour == 0 for t in times)       # UTC+2 -> UTC
    m = {x["market"]: x for x in client.get("/api/v1/markets").json()["markets"]}
    ex = m["forex"]["exness"]
    assert ex["connected"] is True and ex["trade_mode"] == "demo" and ex["read_only"] is True
    assert ex["account"] == "***321" and "87654321" not in json.dumps(m)  # login masked
    assert [s["base_name"] for s in ex["symbols"]] == ["EURUSD"]
    assert [s["base_name"] for s in m["commodities"]["exness"]["symbols"]] == ["XAUUSD"]
    assert client.post(f"/api/v1/bridge-tokens/{bid}/revoke", headers=CSRF).status_code == 200
    assert client.post(url, json=_snapshot(), headers=auth).status_code == 401  # revoked
    audit = {a["action"] for a in client.get("/api/v1/security").json()["audit"]}
    assert {"bridge_token_created", "bridge_token_revoked"} <= audit
    with migrated_engine.begin() as c:
        c.execute(text("DELETE FROM market_candles WHERE source = 'mt5'"))
        c.execute(text("DELETE FROM trading_pairs WHERE exchange_id = "
                       "(SELECT id FROM exchanges WHERE code = 'exness')"))
        c.execute(text("DELETE FROM exchanges WHERE code = 'exness'"))


def test_exness_bridge_intraday_bars_backfill_and_data_health(
        client: TestClient, migrated_engine: Engine) -> None:
    from datetime import UTC, datetime, timedelta

    from nexus_market import fx_bars
    from sqlalchemy import text
    from sqlalchemy.orm import Session as OrmSession

    login(client)
    token = client.post("/api/v1/bridge-tokens", json={"name": "fx pc", "reason": "fx bars"},
                        headers=CSRF).json()["token"]
    auth = {"Authorization": f"Bearer {token}"}
    now, off = int(time.time()), 2
    m5_last, h1_last = now - now % 300 - 300, now - now % 3600 - 3600

    def rows(last: int, step: int, n: int) -> list[list[float]]:
        return [[last - step * i + off * 3600, 1.1, 1.1002, 1.1, 1.1001, 5]
                for i in range(n)][::-1]

    gold = [*rows(m5_last, 300, 10), [m5_last + off * 3600 - 3000, 2000, 1990, 2010, 2000, 1]]
    intraday = [{"name": "EURUSDm", "timeframe": "M5", "full": True,
                 "rows": rows(m5_last, 300, 1200)},
                {"name": "EURUSDm", "timeframe": "H1", "full": False,
                 "rows": rows(h1_last, 3600, 6)},
                {"name": "XAUUSDm", "timeframe": "M5", "full": False, "rows": gold}]
    bad = client.post("/api/v1/bridge/mt5/snapshot", headers=auth, json=_snapshot(
        intraday=[{"name": "EURUSDm", "timeframe": "M1", "rows": []}]))
    assert bad.status_code == 422                                   # M5 and H1 only
    r = client.post("/api/v1/bridge/mt5/snapshot", json=_snapshot(intraday=intraday),
                    headers=auth)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["intraday_written"] == 1200 + 6 + 10                # the bad gold bar refused
    assert body["need_intraday"] == ["EURUSDm/H1", "XAUUSDm/M5"]    # short series, H1 first
    with migrated_engine.begin() as c:
        got = dict(c.execute(text(
            "SELECT c.timeframe, count(*) FROM market_candles c JOIN trading_pairs p "
            "ON p.id = c.pair_id WHERE p.exchange_symbol = 'EURUSDm' AND c.source = 'mt5' "
            "AND c.timeframe IN ('5m', '1h') GROUP BY 1")).all())
        assert got == {"5m": 1200, "1h": 6}
        newest = c.execute(text(
            "SELECT max(c.open_time) FROM market_candles c JOIN trading_pairs p ON "
            "p.id = c.pair_id WHERE p.exchange_symbol = 'EURUSDm' AND c.timeframe = '5m'"
        )).scalar_one()
        assert int(newest.timestamp()) == m5_last                   # server time -> UTC
    again = client.post("/api/v1/bridge/mt5/snapshot", headers=auth, json=_snapshot(
        intraday=[{"name": "EURUSDm", "timeframe": "M5", "rows": rows(m5_last, 300, 36)}]))
    assert again.json()["need_intraday"] == []                      # 1200 stored, no gap
    health = client.get("/api/v1/markets/forex-data")
    assert health.status_code == 200, health.text
    h = health.json()
    assert h["bridge_connected"] is True and h["retention_days"] == {"5m": 45, "1h": 400}
    s5 = {(x["symbol"], x["timeframe"]): x for x in h["series"]}
    eur = s5[("EURUSD", "5m")]
    assert eur["status"] in ("ok", "closed") and eur["stored"] > 0
    assert eur["spread"] == "0.0001" and eur["spread_vs_m5_range"] == 0.5
    xau = s5[("XAUUSD", "5m")]["last_batch"]
    assert xau["rejected"] == {"ohlc_inconsistent": 1} and xau["stored"] == 10

    def send_gold(full: bool) -> list[str]:
        r = client.post("/api/v1/bridge/mt5/snapshot", headers=auth, json=_snapshot(
            intraday=[{"name": "XAUUSDm", "timeframe": "M5", "full": full,
                       "rows": rows(m5_last, 300, 10)}]))
        assert r.status_code == 200, r.text
        return list(r.json()["need_intraday"])

    assert send_gold(False) == ["XAUUSDm/M5"]                       # still short: asked
    assert send_gold(True) == []                                    # the broker's whole series
    assert send_gold(False) == []                                   # not asked again for hours
    with OrmSession(migrated_engine) as orm:
        pid = orm.execute(text("SELECT id FROM trading_pairs WHERE exchange_symbol = "
                               "'EURUSDm'")).scalar_one()
        old = datetime.fromtimestamp(m5_last, tz=UTC) - timedelta(days=50)
        orm.execute(text("INSERT INTO market_candles (pair_id, timeframe, open_time, open, "
                         "high, low, close, volume, is_closed, source) VALUES "
                         "(:p, '5m', :t, 1, 1, 1, 1, 0, true, 'mt5')"), {"p": pid, "t": old})
        assert fx_bars.prune(orm, [pid], datetime.now(UTC)) == 1   # only the old bar
        orm.commit()
    with migrated_engine.begin() as c:
        assert c.execute(text("SELECT count(*) FROM market_candles WHERE pair_id = :p AND "
                              "timeframe = '5m'"), {"p": pid}).scalar_one() == 1200
        c.execute(text("DELETE FROM market_candles WHERE source = 'mt5'"))
        c.execute(text("DELETE FROM trading_pairs WHERE exchange_id = "
                       "(SELECT id FROM exchanges WHERE code = 'exness')"))
        c.execute(text("DELETE FROM exchanges WHERE code = 'exness'"))


def test_exness_history_chunks_for_research(client: TestClient, migrated_engine: Engine,
                                            monkeypatch: pytest.MonkeyPatch) -> None:
    from datetime import UTC, datetime

    from nexus_api import bridge as br
    from sqlalchemy import text

    url = "/api/v1/bridge/mt5/history"
    login(client)
    token = client.post("/api/v1/bridge-tokens", json={"name": "history pc",
                                                       "reason": "history export"},
                        headers=CSRF).json()["token"]
    auth = {"Authorization": f"Bearer {token}"}
    off = 2                                              # broker server time UTC+2
    start = int(datetime(2024, 3, 4, tzinfo=UTC).timestamp())   # server wall clock

    def chunk(**over: Any) -> dict[str, Any]:
        body = {"bridge_version": "1.1", "read_only": True, "server_utc_offset_hours": off,
                "symbol": "EURUSDm", "base_name": "EURUSD", "timeframe": "H1",
                "period": "2024",
                "rows": [[start + i * 3600, 1.1, 1.2, 1.0, 1.15, 5] for i in range(3)]}
        body.update(over)
        return body

    assert client.post(url, json=chunk()).status_code == 401                   # no token
    assert client.post(url + "/manifest", json={}, headers=auth).json()["chunks"] == []
    assert client.post(url, json=chunk(read_only=False), headers=auth).status_code == 409
    assert client.post(url, json=chunk(period="2024-03"), headers=auth).status_code == 422
    assert client.post(url, json=chunk(base_name="XAUUSD"), headers=auth).status_code == 422
    assert client.post(url, json=chunk(rows=[[start, 1.1, 1.0, 1.2, 1.15, 5]]),
                       headers=auth).status_code == 422                     # high < low
    stray = [int(datetime(2023, 6, 1, tzinfo=UTC).timestamp()), 1.1, 1.2, 1.0, 1.1, 1]
    r = client.post(url, json=chunk(rows=[*chunk()["rows"], stray]), headers=auth)
    assert r.status_code == 200, r.text
    assert r.json()["bars_stored"] == 3                                     # 2023 bar dropped
    assert r.json()["first_open"] == "2024-03-03T22:00:00+00:00"            # UTC+2 -> UTC
    now = int(time.time())
    hour = now - now % 3600 + off * 3600                                    # forming, server
    cur = datetime.fromtimestamp(hour, tz=UTC).year
    r = client.post(url, json=chunk(period=str(cur), rows=[
        [hour - 3600, 1.1, 1.2, 1.0, 1.1, 1], [hour, 1.1, 1.2, 1.0, 1.1, 1]]), headers=auth)
    assert r.status_code == 200 and r.json()["bars_stored"] == 1           # forming dropped
    r = client.post(url, json=chunk(), headers=auth)                         # resend replaces
    assert r.status_code == 200
    man = client.post(url + "/manifest", json={}, headers=auth).json()
    assert sorted(c["period"] for c in man["chunks"]) == sorted(["2024", str(cur)])
    assert 0 < man["used_bytes"] < man["limit_bytes"]
    series = client.get("/api/v1/research/history").json()["series"]
    assert series == [{"symbol": "EURUSD", "timeframe": "H1", "chunks": 2, "bars": 4,
                       "first_open": "2024-03-03T22:00:00+00:00",
                       "last_open": series[0]["last_open"], "bytes": series[0]["bytes"],
                       "server_utc_offsets": [2]}]
    csv = client.get("/api/v1/research/history/EURUSD/H1.csv")
    assert csv.status_code == 200 and csv.headers["content-type"].startswith("text/csv")
    lines = csv.text.splitlines()
    assert lines[0] == "time,open,high,low,close,volume" and len(lines) == 5
    assert lines[1].startswith("2024-03-03T22:00:00Z,1.1,1.2,1.0,1.15,5")
    assert lines[1:] == sorted(lines[1:])
    assert client.get("/api/v1/research/history/EURUSD/M5.csv").status_code == 404
    assert client.get("/api/v1/research/history/eurusd/H1.csv").status_code == 422
    monkeypatch.setattr(br, "HISTORY_MAX_BYTES", 100)
    assert client.post(url, json=chunk(period="2023", rows=[stray]),
                       headers=auth).status_code == 507                    # storage cap
    client.post("/api/v1/auth/logout", headers=CSRF)
    assert client.get("/api/v1/research/history").status_code == 401
    with migrated_engine.begin() as c:
        c.execute(text("DELETE FROM history_chunks"))


def test_exness_demo_trading_is_demo_only_switch_gated_and_limited(
        client: TestClient, migrated_engine: Engine) -> None:
    from datetime import UTC, datetime, timedelta

    from nexus_api import mt5_demo
    from nexus_strategies import exness_demo as plan
    from sqlalchemy import text

    r = sync_redis.Redis.from_url(os.environ["NEXUS_TEST_REDIS_URL"])
    for k in r.scan_iter("nexus:mt5demo:*"):
        r.delete(k)
    login(client)
    token = client.post("/api/v1/bridge-tokens", json={"name": "demo pc",
                                                       "reason": "exness demo trading"},
                        headers=CSRF).json()["token"]
    auth = {"Authorization": f"Bearer {token}"}
    url = "/api/v1/bridge/mt5/demo/sync"
    today = datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
    rows = []
    for i in range(70, 0, -1):                         # closed daily bars, calm gold
        t = int((today - timedelta(days=i)).timestamp())
        c = 2000 * (1 + 0.003 * ((i % 3) - 1))
        rows.append([t, c, c + 1, c - 1, c])

    def body(**over: Any) -> dict[str, Any]:
        b = {"bridge_version": "1.0", "server_utc_offset_hours": 0,
             "account": {"login": 55555555, "trade_mode": "demo", "currency": "USD",
                         "balance": "10000", "equity": "10000", "margin": "0",
                         "margin_free": "10000", "leverage": 2000,
                         "server": "Exness-MT5Trial", "company": "Exness"},
             "symbols": [{"name": "XAUUSDm", "base_name": "XAUUSD", "point": 0.001,
                          "contract_size": 100, "volume_min": 0.01, "volume_step": 0.01,
                          "volume_max": 200, "bid": 2000.0, "ask": 2000.3},
                         {"name": "EURUSDm", "base_name": "EURUSD", "point": 0.00001,
                          "contract_size": 100000, "volume_min": 0.01, "volume_step": 0.01,
                          "volume_max": 200, "bid": 1.1, "ask": 1.10008}],
             "positions": [], "bars": {"XAUUSDm": {"D1": rows},
                                       "EURUSDm": {"H1": [[rows[-1][0], 1.1, 1.2, 1.0, 1.1]]}},
             "results": []}
        b.update(over)
        return b

    assert client.post(url, json=body()).status_code == 401                      # no token
    real = body(account=body()["account"] | {"trade_mode": "real"})
    assert client.post(url, json=real, headers=auth).status_code == 403          # real: never
    res = client.post(url, json=body(), headers=auth)
    assert res.status_code == 200, res.text
    out = res.json()
    assert out["demo_only"] is True and out["instructions"] == []
    assert out["gates"]["XAUUSD"]["entries_allowed"] is False
    assert "commodities switch is RESEARCH (PAPER needed)" in out["gates"]["XAUUSD"]["blocked_by"]
    assert client.post("/api/v1/markets/commodities/autonomy",
                       json={"autonomy": "PAPER", "reason": "gold on exness demo"},
                       headers=CSRF).status_code == 200
    out = client.post(url, json=body(), headers=auth).json()
    (ins,) = out["instructions"]                                   # EURUSD: too few bars
    gold = plan.Spec("XAUUSDm", 100, 0.01, 0.01, 200, 0.001)
    assert ins["action"] == "open" and ins["side"] == "buy" and ins["magic"] == plan.MAGIC
    assert ins["volume"] == plan.lots(0.25 * 10_000 / 2000.3, gold)
    assert ins["client_id"].startswith("nxG")
    acct = body()["account"]
    out = client.post(url, json=body(account=acct | {"equity": "9850"}), headers=auth).json()
    assert out["instructions"] == [] and "daily loss limit" in out["gates"]["XAUUSD"]["blocked_by"]
    out = client.post(url, json=body(account=acct | {"equity": "9400"}), headers=auth).json()
    assert "drawdown halt (owner reset required)" in out["gates"]["XAUUSD"]["blocked_by"]
    m = client.get("/api/v1/markets").json()
    assert m["exness_demo"]["demo_only"] is True and m["exness_demo"]["account"] == "***555"
    assert client.post("/api/v1/bridge-demo/reset-halt", json={"reason": "reviewed the demo"},
                       headers=CSRF).status_code == 200
    assert client.post("/api/v1/bridge-demo/reset-halt", json={"reason": "again please"},
                       headers=CSRF).status_code == 409
    audit = {a["action"] for a in client.get("/api/v1/security").json()["audit"]}
    assert {"mt5_demo_refused", "mt5_demo_instructions", "mt5_demo_halted",
            "mt5_demo_halt_reset"} <= audit
    client.post("/api/v1/markets/commodities/autonomy",
                json={"autonomy": "RESEARCH", "reason": "back to research"}, headers=CSRF)
    for k in r.scan_iter("nexus:mt5demo:*"):
        r.delete(k)
    r.delete(mt5_demo.STATUS_KEY)
    with migrated_engine.begin() as c:
        c.execute(text("SELECT 1"))
