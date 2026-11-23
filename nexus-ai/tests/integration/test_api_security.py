"""API authentication, authorization, CSRF and sensitive-action tests (real DB + Redis)."""

from __future__ import annotations

import os
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
