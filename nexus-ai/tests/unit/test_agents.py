"""Agents, proposals, the independent Risk Manager and trade review."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal as D
from typing import Any

from nexus_agents import Context, build_proposal, review_trade, technical_agent
from nexus_agents.agents import derivatives_agent, quant_agent, sentiment_agent
from nexus_config import RiskSettings

NOW = datetime(2026, 11, 21, 12, 0, tzinfo=UTC)


def tf(trend: str, close: float, e50: float, e200: float, hist: float, rsi: float) -> dict:
    return {"bars": 1000, "trend": trend, "close": close, "ema50": e50, "ema200": e200,
            "macd_hist": hist, "rsi": rsi, "volume_z": 0.5, "structure_event": None}


def market(*, regime: str = "trend_up", score: int = 70, decision: str = "ENTER",
           bullish: bool = True, funding: float | None = 0.0001) -> dict[str, Any]:
    t = (tf("up", 105, 100, 90, 1, 60) if bullish else tf("down", 95, 100, 110, -1, 40))
    return {
        "symbol": "BTC/USDT", "as_of": (NOW - timedelta(minutes=2)).isoformat(),
        "price": 105.0, "vwap_24h": 103.0 if bullish else 107.0, "support": 100.0,
        "resistance": 110.0, "regime": regime, "atr_percentile": 0.5,
        "btc_correlation_1h": None, "funding_rate": funding, "open_interest": 1000.0,
        "timeframes": {k: t for k in ("15m", "1h", "4h", "1d")},
        "hypotheses": [
            {"side": "long", "entry": 105.0, "stop": 101.0, "target": 113.0,
             "reward_risk": 2.0, "score": score, "decision": decision,
             "reasons_for": ["higher timeframe up"], "reasons_against": [],
             "no_trade_reasons": [] if decision == "ENTER" else ["score below 60"]},
            {"side": "short", "entry": 105.0, "stop": 109.0, "target": 97.0,
             "reward_risk": 2.0, "score": 10, "decision": "NO TRADE",
             "reasons_for": [], "reasons_against": [], "no_trade_reasons": ["x"]}]}


def strategies(allowed: list[str] | None = None, enabled: bool = True) -> list[dict]:
    edges = {"trend_up": {"n": 100, "expectancy": 0.004 if allowed else -0.004,
                          "profit_factor": 1.4 if allowed else 0.5}}
    return [{"code": "trend_following", "enabled": enabled,
             "validation": {"passed": bool(allowed), "regime_edges": edges,
                            "allowed_regimes": allowed or []}}]


def ctx(**kw: Any) -> Context:
    base = dict(symbol="BTC/USDT", market=market(), strategies=strategies(["trend_up"]),
                portfolio={"equity": "10000", "cash": "10000", "exposure": "0",
                           "open_positions": 0, "held_assets": []},
                risk=RiskSettings(), news_risk=0.1, news_threshold=0.7, kill_switch=False,
                now=NOW)
    base.update(kw)
    return Context(**base)  # type: ignore[arg-type]


def test_technical_agent_reads_all_timeframes() -> None:
    up = technical_agent(market())
    down = technical_agent(market(bullish=False))
    assert up.view == "bullish" and up.score > 50 and down.view == "bearish"
    assert any("EMA50" in e for e in up.evidence) and up.confidence > 0.5
    assert technical_agent({"error": "no data"}).view == "unavailable"


def test_quant_agent_reports_negative_expected_value() -> None:
    r = quant_agent(market(), strategies(None))
    assert r.view == "against_trading"
    assert any("expected value of trading is negative" in c for c in r.contradictions)


def test_derivatives_and_sentiment_flag_crowding_and_missing_data() -> None:
    assert "crowded longs" in " ".join(derivatives_agent(market(funding=0.001)).contradictions)
    assert derivatives_agent(market(funding=None)).view == "unavailable"
    s = sentiment_agent(None, None, 0.7)
    assert s.view == "unavailable" and any("news risk unknown" in w for w in s.warnings)
    hot = sentiment_agent({"score": 85, "confidence": 0.9, "label": "very bullish",
                           "posts_used": 40, "unique_authors": 20, "warnings": []}, 0.1, 0.7)
    assert hot.confidence <= 0.8 and any("contrarian" in c for c in hot.contradictions)


def test_full_approval_path_and_sizing() -> None:
    p = build_proposal(ctx())
    assert p["risk_manager"]["verdict"] == "APPROVE" and p["final_decision"].startswith("TRADE")
    assert p["direction"] == "long" and p["strategy"] == "trend_following"
    assert [t["level"] for t in p["take_profits"]] == ["TP1 (1R, partial)", "TP2 (2R)"]
    assert D(p["risk_pct"]) <= D("0.005") and p["size_ok"]
    assert {a["agent"] for a in p["agents"]} == {
        "market_intelligence", "technical", "quant", "derivatives", "sentiment", "portfolio"}


def test_no_validated_strategy_means_no_trade_even_with_bullish_agents() -> None:
    p = build_proposal(ctx(strategies=strategies(None)))
    assert p["final_decision"] == "NO TRADE"
    assert any("no strategy validated" in r for r in p["reasons_to_reject"])
    assert p["explanation"].startswith("No trade:")


def test_risk_manager_cannot_be_overridden() -> None:
    cases = {
        "kill switch engaged": ctx(kill_switch=True),
        "news risk unknown": ctx(news_risk=None),
        "X: confirmed hack": ctx(x_block="X: confirmed hack"),
        "regime high_vol": ctx(market=market(regime="high_vol")),
        "maximum open positions": ctx(portfolio={"equity": "10000", "open_positions": 3,
                                                  "held_assets": [], "exposure": "0"}),
        "already holding": ctx(portfolio={"equity": "10000", "open_positions": 1,
                                          "held_assets": ["BTC"], "exposure": "1000"}),
        "NO TRADE rule": ctx(market=market(decision="NO TRADE", score=50)),
    }
    for needle, c in cases.items():
        p = build_proposal(c)
        assert p["final_decision"] == "NO TRADE", needle
        assert any(needle in r for r in p["reasons_to_reject"]), (needle, p["reasons_to_reject"])


def test_short_is_never_approved_on_spot() -> None:
    m = market(bullish=False)
    m["hypotheses"][1].update(score=80, decision="ENTER", no_trade_reasons=[])
    m["hypotheses"][0].update(score=10)
    p = build_proposal(ctx(market=m))
    assert p["direction"] == "short" and p["final_decision"] == "NO TRADE"
    assert any("spot only" in r for r in p["reasons_to_reject"])


def test_stale_market_data_is_rejected() -> None:
    m = market()
    m["as_of"] = (NOW - timedelta(minutes=40)).isoformat()
    p = build_proposal(ctx(market=m))
    assert any("min old" in r for r in p["reasons_to_reject"])


# ------------------------------------------------------------------ review
def entry(fill: str = "100", expected: str = "100", stop: str | None = "98",
          qty: str = "1", mult: str = "1") -> dict:
    d = {"strategy": "trend_following", "regime": "trend_up", "risk_multiplier": mult}
    if stop:
        d["stop"] = stop
    return {"average_fill_price": fill, "expected_price": expected, "filled_amount": qty,
            "details": d}


def test_disciplined_loss_is_a_good_trade_and_strategy_failure() -> None:
    r = review_trade({"id": 1, "realized_pnl": "-2", "exit_reason": "stop_loss"}, entry(),
                     [{"average_fill_price": "97.99", "expected_price": "98"}],
                     slippage_bps_assumed=D(5))
    assert r["labels"][0] == "good trade" and "strategy failure" in r["labels"]
    assert "good execution" in r["labels"] and r["outcome"] == "loss"


def test_win_without_stop_is_a_bad_trade() -> None:
    r = review_trade({"id": 2, "realized_pnl": "5", "exit_reason": "take_profit"},
                     entry(stop=None), [], slippage_bps_assumed=D(5))
    assert r["labels"][0] == "bad trade" and "rule violation" in r["labels"]


def test_poor_execution_and_risk_failure() -> None:
    r = review_trade({"id": 3, "realized_pnl": "-4", "exit_reason": "stop_loss"},
                     entry(fill="100.5", expected="100"),
                     [{"average_fill_price": "96", "expected_price": "98"}],
                     slippage_bps_assumed=D(5))
    assert "poor execution" in r["labels"] and "risk failure" in r["labels"]
    assert r["labels"][0] == "bad trade"
