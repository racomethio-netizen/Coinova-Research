"""Agent functions: each reads plain inputs and returns an AgentReport (view -100..100)."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Any

TFS = ("15m", "1h", "4h", "1d")


@dataclass
class AgentReport:
    agent: str
    view: str                     # bullish | bearish | neutral | against_trading | unavailable
    score: int                    # -100 (bearish) .. +100 (bullish); 0 when unavailable
    confidence: float             # 0..1
    evidence: list[str] = field(default_factory=list)
    contradictions: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def _view(score: float) -> str:
    return "bullish" if score >= 20 else "bearish" if score <= -20 else "neutral"


def _clip(x: float) -> int:
    return round(max(-100.0, min(100.0, x)))


def unavailable(agent: str, why: str) -> AgentReport:
    return AgentReport(agent, "unavailable", 0, 0.0, warnings=[why])


# ------------------------------------------------------------------ technical
def technical_agent(market: dict[str, Any] | None) -> AgentReport:
    """Trend, structure, EMAs, VWAP, RSI, MACD, ATR, volume across 15m/1h/4h/1d."""
    if not market or market.get("error"):
        return unavailable("technical", (market or {}).get("error") or "no market analysis")
    tfs = market.get("timeframes") or {}
    ev: list[str] = []
    contra: list[str] = []
    pts = 0.0
    weights = {"15m": 0.5, "1h": 1.0, "4h": 1.5, "1d": 1.5}
    used = 0
    for tf in TFS:
        v = tfs.get(tf) or {}
        if v.get("error") or not v:
            continue
        used += 1
        w = weights[tf]
        trend = v.get("trend")
        if trend in ("up", "uptrend", "bullish"):
            pts += 12 * w
            ev.append(f"{tf}: market structure up")
        elif trend in ("down", "downtrend", "bearish"):
            pts -= 12 * w
            ev.append(f"{tf}: market structure down")
        close, e50, e200 = v.get("close"), v.get("ema50"), v.get("ema200")
        if close and e50 and e200:
            if close > e50 > e200:
                pts += 8 * w
                ev.append(f"{tf}: price above EMA50 above EMA200")
            elif close < e50 < e200:
                pts -= 8 * w
                ev.append(f"{tf}: price below EMA50 below EMA200")
        hist = v.get("macd_hist")
        if hist is not None:
            pts += (4 if hist > 0 else -4) * w
        rsi = v.get("rsi")
        if rsi is not None:
            if rsi >= 70:
                contra.append(f"{tf}: RSI {rsi:.0f} overbought")
                pts -= 4 * w
            elif rsi <= 30:
                contra.append(f"{tf}: RSI {rsi:.0f} oversold")
                pts += 4 * w
        if v.get("structure_event"):
            ev.append(f"{tf}: {v['structure_event']}")
    if used == 0:
        return unavailable("technical", "no timeframe had enough closed bars")
    price, vwap = market.get("price"), market.get("vwap_24h")
    if price and vwap:
        above = price > vwap
        pts += 6 if above else -6
        ev.append(f"price {'above' if above else 'below'} 24h VWAP")
    if market.get("support") and price:
        ev.append(f"support {market['support']}, resistance {market.get('resistance')}")
    vz = ((tfs.get("1h") or {}).get("volume_z"))
    if vz is not None and abs(vz) >= 2:
        ev.append(f"1h volume {vz:+.1f} sd from normal")
    score = _clip(pts)
    trends = [((tfs.get(tf) or {}).get("trend")) for tf in TFS]
    ups = sum(t in ("up", "uptrend", "bullish") for t in trends)
    downs = sum(t in ("down", "downtrend", "bearish") for t in trends)
    if ups and downs:
        contra.append(f"timeframes disagree ({ups} up, {downs} down)")
    agreement = abs(ups - downs) / max(1, used)
    conf = round(min(1.0, 0.3 + 0.7 * agreement) * (used / len(TFS)), 2)
    return AgentReport("technical", _view(score), score, conf, ev, contra)


# ------------------------------------------------------------------ quant
def quant_agent(market: dict[str, Any] | None, strategies: list[dict[str, Any]]) -> AgentReport:
    """Regime, volatility, BTC correlation, and the out-of-sample expected value of the
    strategies in the CURRENT regime (from validation, not from hope)."""
    if not market or market.get("error"):
        return unavailable("quant", "no market analysis")
    regime = market.get("regime")
    ev, contra, warn = [f"regime: {regime or 'unknown'}"], [], []
    ap = market.get("atr_percentile")
    if ap is not None:
        ev.append(f"volatility percentile {ap:.0%}")
        if ap > 0.9:
            warn.append("volatility in the top 10% of its range")
    if market.get("btc_correlation_1h") is not None:
        ev.append(f"1h correlation with BTC {market['btc_correlation_1h']:+.2f}")
    best: tuple[str, float, int] | None = None
    usable = []
    for st in strategies:
        val = st.get("validation") or {}
        edge = (val.get("regime_edges") or {}).get(str(regime))
        if edge and edge.get("expectancy") is not None:
            exp = float(edge["expectancy"])
            if best is None or exp > best[1]:
                best = (st["code"], exp, int(edge.get("n") or 0))
        if str(regime) in (val.get("allowed_regimes") or []):
            usable.append(st["code"])
    if usable:
        ev.append(f"validated for this regime: {', '.join(usable)}")
    if best is None:
        contra.append("no out-of-sample statistics for this regime")
        return AgentReport("quant", "against_trading", 0, 0.5, ev, contra, warn)
    code, exp, n = best
    ev.append(f"best out-of-sample expectancy in {regime}: {code} {exp:+.2%} per trade "
              f"({n} trades)")
    if not usable:
        contra.append("no strategy has a validated positive edge in this regime: "
                      "expected value of trading is negative")
        return AgentReport("quant", "against_trading", 0, 0.8, ev, contra, warn)
    return AgentReport("quant", "neutral", 0, 0.6, ev, contra, warn)


# ------------------------------------------------------------------ derivatives
def derivatives_agent(market: dict[str, Any] | None) -> AgentReport:
    """Funding, basis, open interest trend, positioning ratios and liquidations. Missing
    pieces are reported as unavailable, never guessed."""
    if not market or market.get("error"):
        return unavailable("derivatives", "no market analysis")
    fr = market.get("funding_rate")
    if fr is None:
        return AgentReport("derivatives", "unavailable", 0, 0.0,
                           warnings=[market.get("futures_data_note") or "funding unavailable"])
    ev = [f"last funding rate {fr:+.4%} per 8h"]
    contra: list[str] = []
    warn: list[str] = []
    score = 0.0
    if fr > 0.0005:
        contra.append("high positive funding: crowded longs pay to hold, squeeze risk down")
        score -= 25
    elif fr < -0.0005:
        contra.append("negative funding: crowded shorts, squeeze risk up")
        score += 25
    else:
        ev.append("funding near neutral: no crowding signal")
    basis = market.get("basis")
    if basis is not None:
        ev.append(f"perp basis {basis:+.3%} vs index")
        if abs(basis) > 0.002:
            contra.append(f"stretched basis {basis:+.2%}: leverage-driven pricing")
    else:
        warn.append("basis not available")
    oi24, price24 = market.get("oi_change_24h"), market.get("change_24h")
    if oi24 is not None:
        ev.append(f"open interest {oi24:+.1%} in 24h")
        if price24 is not None:
            if oi24 > 0.05 and price24 > 0:
                ev.append("rising price with rising open interest: new longs entering")
                score += 10
            elif oi24 > 0.05 and price24 < 0:
                ev.append("falling price with rising open interest: new shorts entering")
                score -= 10
            elif oi24 < -0.05:
                ev.append("open interest falling: positions closing, weaker conviction")
    else:
        warn.append("open-interest history not available")
    top = market.get("top_traders_position_ratio")
    if top is not None:
        ev.append(f"top-trader long/short position ratio {top:.2f}")
    acc = market.get("accounts_long_short_ratio")
    if acc is not None:
        ev.append(f"accounts long/short ratio {acc:.2f} (counts accounts, weak signal)")
        if acc > 3:
            contra.append("retail accounts heavily long: crowded")
    taker = market.get("taker_buy_sell_ratio")
    if taker is not None:
        ev.append(f"taker buy/sell volume ratio {taker:.2f} (1h)")
    liq = market.get("liquidations") or {}
    if liq:
        ll, sl = liq.get("longs_liquidated_1h"), liq.get("shorts_liquidated_1h")
        ev.append(f"liquidations 1h: longs {ll:,.0f} / shorts {sl:,.0f} USDT (lower bound)")
        if ll and sl is not None and ll > 3 * max(sl, 1) and ll > 1_000_000:
            contra.append("long liquidation cascade in the last hour")
            score -= 15
        if sl and ll is not None and sl > 3 * max(ll, 1) and sl > 1_000_000:
            ev.append("short squeeze in the last hour")
            score += 10
    else:
        warn.append("liquidation feed not available")
    pieces = sum(x is not None for x in (basis, oi24, top, acc, taker)) + bool(liq)
    conf = round(0.3 + 0.1 * pieces, 2)
    return AgentReport("derivatives", _view(score), _clip(score), min(conf, 0.8), ev, contra,
                       warn)


# ------------------------------------------------------------------ sentiment
def sentiment_agent(x_coin: dict[str, Any] | None, news_risk: float | None,
                    news_threshold: float) -> AgentReport:
    ev: list[str] = []
    contra: list[str] = []
    warn: list[str] = []
    if news_risk is None:
        warn.append("news risk unknown (monitor not reporting)")
    else:
        ev.append(f"news risk {news_risk:.2f} (blocks above {news_threshold:.2f})")
        if news_risk > news_threshold:
            contra.append("high-impact news window")
    if not x_coin or x_coin.get("score") is None:
        warn.append("no usable X sentiment for this coin")
        return AgentReport("sentiment", "unavailable" if news_risk is None else "neutral", 0,
                           0.1 if news_risk is not None else 0.0, ev, contra, warn)
    s = int(x_coin["score"])
    conf = float(x_coin.get("confidence") or 0)
    ev.append(f"X sentiment {s:+d} ({x_coin.get('label')}), confidence {conf:.0%}, "
              f"{x_coin.get('posts_used')} posts from {x_coin.get('unique_authors')} authors")
    warn += list(x_coin.get("warnings") or [])
    if abs(s) >= 70:
        contra.append("extreme crowd sentiment: contrarian risk")
    return AgentReport("sentiment", _view(s), s, round(min(conf, 0.8), 2), ev, contra, warn)


# ------------------------------------------------------------------ portfolio
def portfolio_agent(portfolio: dict[str, Any], symbol: str, risk: Any) -> AgentReport:
    base = symbol.split("/")[0]
    eq = float(portfolio.get("equity") or 0)
    exp = float(portfolio.get("exposure") or 0)
    n = int(portfolio.get("open_positions") or 0)
    held = set(portfolio.get("held_assets") or [])
    ev = [f"equity {eq:,.2f}, exposure {exp / eq:.0%} of max "
          f"{float(risk.max_portfolio_exposure):.0%}" if eq else "equity unknown",
          f"open positions {n} of {risk.max_open_positions}"]
    contra: list[str] = []
    if eq <= 0:
        contra.append("equity unknown")
    if n >= risk.max_open_positions:
        contra.append("maximum open positions reached")
    if eq and exp >= eq * float(risk.max_portfolio_exposure):
        contra.append("maximum portfolio exposure reached")
    if base in held:
        contra.append(f"already holding {base}: no averaging or duplicate exposure")
    view = "against_trading" if contra else "neutral"
    return AgentReport("portfolio", view, 0, 1.0, ev, contra)


# ------------------------------------------------------------------ market intelligence
def market_intelligence_agent(market: dict[str, Any] | None, *, kill_switch: bool,
                              x_alerts: list[dict[str, Any]], x_block: str | None,
                              now: datetime) -> AgentReport:
    ev: list[str] = []
    contra: list[str] = []
    warn: list[str] = []
    if kill_switch:
        contra.append("kill switch engaged")
    if x_block:
        contra.append(x_block)
    for a in x_alerts[:5]:
        line = (f"{a.get('label')} ({a.get('status')}, {a.get('credible_sources')} credible "
                f"sources): {str(a.get('headline'))[:120]}")
        (contra if a.get("status") == "confirmed" and a.get("direction", 0) < 0
         and a.get("high_impact") else ev).append(line)
    if market and market.get("as_of"):
        age = (now - datetime.fromisoformat(market["as_of"])).total_seconds() / 60
        if age > 15:
            contra.append(f"market data is {age:.0f} min old")
        else:
            ev.append(f"market data {age:.0f} min old")
    if market and market.get("regime") == "high_vol":
        warn.append("unstable, high-volatility market")
    if not market or market.get("error"):
        warn.append("market analysis unavailable")
    view = "against_trading" if contra else "neutral"
    return AgentReport("market_intelligence", view, 0, 0.7, ev, contra, warn)
