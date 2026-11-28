"""The strategy orchestrator's decision rules: only a module that passed its test can
start a trade; blockers always win; signal quality (2:1, fresh bar, regime direction,
volatility); spot crypto never sells short; the best tested result is chosen."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from nexus_decision import orchestrator as o

NOW = datetime(2026, 11, 25, 12, 5, tzinfo=UTC)
SNAP = {"insufficient_history": False, "as_of": (NOW - timedelta(minutes=5)).isoformat(),
        "regime": "TRENDING_UP", "session": "London / New York overlap",
        "liquidity": {"spread_vs_atr": 0.05, "quote_volume_24h": 5e9}}
PASS = o.ModuleEvidence("good", "Good module", True, 0.12, 400)
FAIL = o.ModuleEvidence("bad", "Bad module", False, -0.10, 6000)
SWING = o.ModuleEvidence("crypto_swing_trend", "Crypto swing", True, None, None,
                         kind="portfolio", summary="passed (Sharpe 1.11)")


def sig(module: str = "good", side: int = 1, age: int = 2, rr: float = 2.0) -> o.Signal:
    return o.Signal(module, side, NOW - timedelta(minutes=age), 1.10, 1.09, rr, "observer")


def inp(**kw: object) -> o.Inputs:
    base = dict(symbol="EURUSD", market="forex", now=NOW, snapshot=SNAP,
                event_risk={"blocked": False}, market_switch="RESEARCH", kill_switch=False,
                entries_allowed=True, signals=(sig(),),
                evidence={"good": PASS, "bad": FAIL, "crypto_swing_trend": SWING},
                covering=("good", "bad"))
    return o.Inputs(**(base | kw))  # type: ignore[arg-type]


def test_a_passed_module_with_a_fresh_aligned_signal_buys():
    v = o.orchestrate(inp())
    assert v.action == o.BUY and v.module == "good" and v.expected_r == 0.12
    assert abs(v.target - (1.10 + 2 * 0.01)) < 1e-12 and v.reward_risk == 2.0
    assert any("risk engine must approve" in r for r in v.reasons)


def test_failed_modules_never_trade():
    v = o.orchestrate(inp(signals=(sig("bad"),)))
    assert v.action == o.NO_TRADE and v.candidates[0]["eligible"] is False
    assert "no proven edge" in v.candidates[0]["why_not"][0]


def test_every_blocker_gives_no_trade():
    cases = {
        "switch is OFF": dict(market_switch="OFF"),
        "kill switch": dict(kill_switch=True),
        "risk state": dict(entries_allowed=False),
        "unknown": dict(entries_allowed=None),
        "stale": dict(snapshot=SNAP | {"as_of": (NOW - timedelta(hours=3)).isoformat()}),
        "market closed": dict(snapshot=SNAP | {"session": "closed"}),
        "scheduled event": dict(event_risk={"blocked": True, "active": [
            {"title": "Fed FOMC rate decision", "blackout_to": "x"}]}),
        "spread": dict(snapshot=SNAP | {"liquidity": {"spread_vs_atr": 0.4}}),
        "history": dict(snapshot={"insufficient_history": True}),
    }
    for word, kw in cases.items():
        v = o.orchestrate(inp(**kw))
        assert v.action == o.NO_TRADE and any(word in b for b in v.blockers), word
    thin = o.orchestrate(inp(market="crypto", symbol="BTC/USDT",
                             snapshot=SNAP | {"liquidity": {"quote_volume_24h": 1e6}}))
    assert thin.action == o.NO_TRADE and "volume" in thin.blockers[0]


def test_signal_quality_checks():
    assert o.orchestrate(inp(signals=(sig(rr=1.5),))).action == o.NO_TRADE
    assert o.orchestrate(inp(signals=(sig(age=40),))).action == o.NO_TRADE   # not fresh
    assert o.orchestrate(inp(signals=(sig(side=-1),))).action == o.NO_TRADE  # vs regime
    hv = o.orchestrate(inp(snapshot=SNAP | {"regime": "HIGH_VOLATILITY"}))
    assert hv.action == o.NO_TRADE and "high-volatility" in hv.candidates[0]["why_not"][0]
    down = o.orchestrate(inp(signals=(sig(side=-1),),
                             snapshot=SNAP | {"regime": "TRENDING_DOWN"}))
    assert down.action == o.SELL
    spot = o.orchestrate(inp(market="crypto", symbol="ETH/USDT", signals=(sig(side=-1),),
                             snapshot=SNAP | {"regime": "TRENDING_DOWN"}))
    assert spot.action == o.NO_TRADE and "short" in spot.candidates[0]["why_not"][0]


def test_portfolio_module_skips_bracket_checks_and_best_evidence_wins():
    swing = o.Signal("crypto_swing_trend", 1, NOW - timedelta(hours=11), None, None, None,
                     "forward_test", "target 60% vs held 40%")
    v = o.orchestrate(inp(market="crypto", symbol="BTC/USDT", signals=(swing,),
                          snapshot=SNAP | {"regime": "RANGING"},
                          covering=("crypto_swing_trend",)))
    assert v.action == o.BUY and v.module == "crypto_swing_trend"
    assert "Sharpe 1.11" in v.reasons[0]
    better = o.ModuleEvidence("great", "Great", True, 0.3, 300)
    two = o.orchestrate(inp(signals=(sig("good"), sig("great")),
                            evidence={"good": PASS, "great": better}))
    assert two.module == "great"


def test_no_signal_and_no_module_explanations():
    v = o.orchestrate(inp(signals=(), covering=("bad",)))
    assert v.action == o.NO_TRADE and "no module for this instrument has a proven edge" \
        in v.reasons[0]
    gold = o.orchestrate(inp(symbol="XAUUSD", signals=(), covering=()))
    assert "no strategy module trades this instrument" in gold.reasons[0]
