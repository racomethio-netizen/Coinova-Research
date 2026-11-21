"""Trade proposal from the agents, then an independent, deterministic Risk Manager review.

The Risk Manager uses only rules and numbers (no agent opinions) and its REJECT is final:
``final_decision`` is "NO TRADE" whenever it rejects, whatever the agents say. "No trade"
is a normal, explained outcome. Proposals are advisory; only the trader executes, and it
applies the same gates again.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import Any

from nexus_risk.sizing import SizingInput, size_position

from nexus_agents.agents import (
    AgentReport,
    derivatives_agent,
    market_intelligence_agent,
    portfolio_agent,
    quant_agent,
    sentiment_agent,
    technical_agent,
)

MIN_REWARD_RISK = Decimal(2)
STALE_MINUTES = 15


@dataclass
class Context:
    symbol: str
    market: dict[str, Any] | None
    strategies: list[dict[str, Any]]
    portfolio: dict[str, Any]
    risk: Any                                   # nexus_config.RiskSettings
    news_risk: float | None
    news_threshold: float
    kill_switch: bool
    now: datetime
    x_coin: dict[str, Any] | None = None
    x_alerts: list[dict[str, Any]] = field(default_factory=list)
    x_block: str | None = None
    fee_rate: Decimal = Decimal("0.001")
    slippage_rate: Decimal = Decimal("0.0005")
    futures_enabled: bool = False


def _d(x: Any) -> Decimal | None:
    return None if x is None else Decimal(str(x))


def _confidence(reports: list[AgentReport], direction: str | None) -> float:
    """Weighted agreement of the available agents with the proposed direction."""
    if direction is None:
        return 0.0
    sign = 1 if direction == "long" else -1
    num = den = 0.0
    for r in reports:
        if r.view in ("unavailable", "against_trading") or r.confidence <= 0:
            continue
        num += r.confidence * max(-1.0, min(1.0, sign * r.score / 60))
        den += r.confidence
    return round(max(0.0, num / den), 2) if den else 0.0


def risk_manager(ctx: Context, prop: dict[str, Any]) -> dict[str, Any]:
    """Independent rule check. Any failed rule rejects. Returns verdict + reasons."""
    reasons: list[str] = []
    m = ctx.market or {}
    if not ctx.market or m.get("error"):
        reasons.append("no valid market analysis")
    if prop["direction"] is None:
        reasons.append("no setup scores high enough to consider")
    if prop["direction"] == "short" and not ctx.futures_enabled:
        reasons.append("short not possible: spot only, futures disabled")
    if ctx.kill_switch:
        reasons.append("kill switch engaged")
    if ctx.news_risk is None:
        reasons.append("news risk unknown")
    elif ctx.news_risk > ctx.news_threshold:
        reasons.append(f"news risk {ctx.news_risk:.2f} above {ctx.news_threshold:.2f}")
    if ctx.x_block:
        reasons.append(ctx.x_block)
    regime = m.get("regime")
    if regime in (None, "high_vol"):
        reasons.append(f"regime {regime or 'unknown'}: no trading")
    if not prop["strategy"]:
        reasons.append(f"no strategy validated for regime {regime}")
    rr = _d(prop.get("reward_risk"))
    if prop["direction"] and (rr is None or rr < MIN_REWARD_RISK):
        reasons.append(f"reward/risk {rr} below {MIN_REWARD_RISK}")
    if prop["direction"] and not prop.get("size_ok"):
        reasons.append(f"position sizing: {prop.get('size_note')}")
    if m.get("as_of"):
        age = (ctx.now - datetime.fromisoformat(m["as_of"])).total_seconds() / 60
        if age > STALE_MINUTES:
            reasons.append(f"market data {age:.0f} min old")
    p = ctx.portfolio
    if int(p.get("open_positions") or 0) >= ctx.risk.max_open_positions:
        reasons.append("maximum open positions reached")
    eq = Decimal(str(p.get("equity") or 0))
    if eq <= 0:
        reasons.append("equity unknown")
    elif Decimal(str(p.get("exposure") or 0)) >= eq * ctx.risk.max_portfolio_exposure:
        reasons.append("maximum portfolio exposure reached")
    if ctx.symbol.split("/")[0] in set(p.get("held_assets") or []):
        reasons.append("already holding this asset")
    for h in (m.get("hypotheses") or []):
        if h.get("side") == prop["direction"] and h.get("decision") != "ENTER":
            reasons += [f"NO TRADE rule: {r}" for r in h.get("no_trade_reasons") or []]
    return {"verdict": "REJECT" if reasons else "APPROVE", "reasons": reasons,
            "note": "Rule-based and independent of the agents; a REJECT cannot be overridden."}


def build_proposal(ctx: Context) -> dict[str, Any]:
    m = ctx.market or {}
    reports = [
        market_intelligence_agent(ctx.market, kill_switch=ctx.kill_switch,
                                  x_alerts=ctx.x_alerts, x_block=ctx.x_block, now=ctx.now),
        technical_agent(ctx.market),
        quant_agent(ctx.market, ctx.strategies),
        derivatives_agent(ctx.market),
        sentiment_agent(ctx.x_coin, ctx.news_risk, ctx.news_threshold),
        portfolio_agent(ctx.portfolio, ctx.symbol, ctx.risk),
    ]
    hyps = m.get("hypotheses") or []
    best = max(hyps, key=lambda h: h.get("score") or 0, default=None)
    direction = best["side"] if best and (best.get("score") or 0) >= 40 else None
    regime = m.get("regime")
    strategy = next((s["code"] for s in ctx.strategies if s.get("enabled") and str(regime) in
                     (((s.get("validation") or {}).get("allowed_regimes")) or [])), None)
    prop: dict[str, Any] = {
        "symbol": ctx.symbol, "direction": direction, "regime": regime, "strategy": strategy,
        "entry": None, "stop_loss": None, "take_profits": [], "reward_risk": None,
        "quantity": None, "notional": None, "risk_amount": None, "risk_pct": None,
        "size_ok": False, "size_note": "no direction"}
    if direction and best:
        entry, stop, target = _d(best["entry"]), _d(best["stop"]), _d(best["target"])
        assert entry is not None and stop is not None and target is not None
        r = abs(entry - stop)
        tp1 = entry + r if direction == "long" else entry - r
        prop.update(entry=str(entry), stop_loss=str(stop),
                    take_profits=[{"level": "TP1 (1R, partial)", "price": str(tp1)},
                                  {"level": "TP2 (2R)", "price": str(target)}],
                    reward_risk=str(best.get("reward_risk")))
        eq = Decimal(str(ctx.portfolio.get("equity") or 0))
        if direction == "long" and eq > 0:
            sz = size_position(SizingInput(
                equity=eq, cash_available=Decimal(str(ctx.portfolio.get("cash") or eq)),
                entry_price=entry, stop_price=stop, risk_per_trade=ctx.risk.risk_per_trade,
                fee_rate=ctx.fee_rate, slippage_rate=ctx.slippage_rate,
                exposure_now=Decimal(str(ctx.portfolio.get("exposure") or 0)),
                max_exposure=ctx.risk.max_portfolio_exposure, asset_exposure_now=Decimal(0),
                max_asset_concentration=ctx.risk.max_asset_concentration,
                amount_step=Decimal("0.00001")))
            prop.update(size_ok=sz.approved, size_note=sz.rejected_reason or "ok",
                        quantity=str(sz.quantity), notional=str(sz.notional.quantize(
                            Decimal("0.01"))), risk_amount=str(sz.risk_amount.quantize(
                                Decimal("0.01"))),
                        risk_pct=str((sz.risk_amount / eq).quantize(Decimal("0.0001"))))
        elif direction == "short":
            prop["size_note"] = "not sized: shorting is not available (spot only)"
    rm = risk_manager(ctx, prop)
    contradictions = [c for r in reports for c in r.contradictions]
    if best:
        contradictions += [f"setup: {x}" for x in best.get("reasons_against") or []]
    tech = next(r for r in reports if r.agent == "technical")
    sent = next(r for r in reports if r.agent == "sentiment")
    final = "TRADE CANDIDATE (paper)" if rm["verdict"] == "APPROVE" else "NO TRADE"
    return {
        **prop, "generated_at": ctx.now.isoformat(),
        "confidence": _confidence(reports, direction),
        "technical_evidence": tech.evidence + ([f"setup: {x}" for x in best.get(
            "reasons_for") or []] if best else []),
        "sentiment_evidence": sent.evidence, "contradicting_evidence": contradictions,
        "reasons_to_reject": rm["reasons"], "risk_manager": rm, "final_decision": final,
        "explanation": _explain(final, rm, direction, regime, strategy),
        "agents": [r.as_dict() for r in reports],
    }


def _explain(final: str, rm: dict[str, Any], direction: str | None, regime: Any,
             strategy: str | None) -> str:
    if final != "NO TRADE":
        return (f"All rules pass for a {direction} using {strategy} in regime {regime}. "
                "Execution is still up to the trader, which re-checks every gate.")
    top = rm["reasons"][:3]
    return "No trade: " + "; ".join(top) + (" ..." if len(rm["reasons"]) > 3 else "") + "."
