"""Strategy orchestrator (unified brief, section E): one BUY / SELL / NO_TRADE decision per
instrument, forex and crypto, with every reason.

Principles (as the crypto signal fusion, ``fusion.py``):

* **Only evidence can start a trade.** A strategy module's signal can become BUY or SELL
  only if that module PASSED its pre-registered test with a positive mean result after
  costs (``ModuleEvidence``). Indicators, regime, session, liquidity and events are
  context: they can block, never vote.
* **Blockers** (any one gives NO_TRADE):
  * the market switch is OFF;
  * the kill switch is engaged;
  * the risk state stops new entries;
  * market data is stale;
  * the forex market is closed;
  * a scheduled economic event is in its blackout window;
  * the spread is too wide for the volatility, or crypto volume is too thin.
* **Signal quality** (per candidate):
  * the planned reward/risk is at least 2:1;
  * the signal bar has just closed (it is still actionable);
  * a trend or breakout module's direction matches the regime;
  * no trend or breakout module trades in a HIGH_VOLATILITY regime.
* **Choice:** among the eligible candidates, the highest tested mean result after costs
  (expected net profitability) wins.
* **This is a proposal.** The independent risk engine and the execution layer still
  decide; nothing here sizes or places an order.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta
from typing import Any

BUY, SELL, NO_TRADE = "BUY", "SELL", "NO_TRADE"


@dataclass(frozen=True)
class Rules:
    max_spread_vs_atr: float = 0.25          # forex: spread above a quarter of the H1 ATR
    min_quote_volume_24h: float = 20_000_000  # crypto: USDT traded in 24 hours
    stale_after: timedelta = timedelta(hours=2, minutes=10)   # H1 base bars
    fresh_signal: timedelta = timedelta(minutes=15)           # a bracket signal's shelf life
    min_reward_risk: float = 2.0


DEFAULT = Rules()


@dataclass(frozen=True)
class ModuleEvidence:
    code: str
    name: str
    passed: bool | None          # its pre-registered test; None: never tested
    mean_r: float | None         # mean result per trade after costs, in R
    trades: int | None
    kind: str = "bracket"        # "bracket" (trend/breakout signals) or "portfolio"
    summary: str = ""            # the test result in words, for the reasons


@dataclass(frozen=True)
class Signal:
    module: str
    side: int                    # +1 buy, -1 sell
    bar_close: datetime          # when the signal bar closed
    entry_ref: float | None      # the signal bar's close
    stop: float | None
    reward_risk: float | None
    source: str                  # "observer" | "forward_test"
    note: str = ""


@dataclass(frozen=True)
class Inputs:
    symbol: str
    market: str                  # "forex" | "crypto"
    now: datetime
    snapshot: dict[str, Any]     # nexus_analysis.intelligence.snapshot
    event_risk: dict[str, Any] | None
    market_switch: str           # OFF / RESEARCH / PAPER / LIVE
    kill_switch: bool
    entries_allowed: bool | None  # risk state; None = unknown
    signals: tuple[Signal, ...] = ()
    evidence: dict[str, ModuleEvidence] = field(default_factory=dict)
    covering: tuple[str, ...] = ()   # modules that trade this instrument


@dataclass
class Verdict:
    symbol: str
    market: str
    action: str
    decided_at: str
    module: str | None = None
    side: int | None = None
    entry_ref: float | None = None
    stop: float | None = None
    target: float | None = None
    reward_risk: float | None = None
    expected_r: float | None = None
    regime: str | None = None
    reasons: list[str] = field(default_factory=list)
    blockers: list[str] = field(default_factory=list)
    candidates: list[dict[str, Any]] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def blockers(inp: Inputs, r: Rules = DEFAULT) -> list[str]:
    snap, b = inp.snapshot, []
    if inp.market_switch == "OFF":
        b.append(f"{inp.market} switch is OFF")
    if inp.kill_switch:
        b.append("kill switch engaged")
    if inp.entries_allowed is False:
        b.append("risk state does not allow new entries")
    elif inp.entries_allowed is None:
        b.append("risk state unknown (fails closed)")
    if snap.get("insufficient_history"):
        b.append("not enough stored price history")
    elif snap.get("as_of"):
        age = inp.now - datetime.fromisoformat(snap["as_of"])
        if age > r.stale_after and snap.get("session") != "closed":
            b.append(f"market data is stale ({int(age.total_seconds() // 60)} min old)")
    if inp.market == "forex" and snap.get("session") == "closed":
        b.append("forex market closed")
    ev = inp.event_risk or {}
    if ev.get("blocked"):
        act = (ev.get("active") or [{}])[0]
        b.append(f"scheduled event: {act.get('title')} (new entries paused until "
                 f"{act.get('blackout_to')})")
    liq = snap.get("liquidity") or {}
    if inp.market == "forex" and liq.get("spread_vs_atr") is not None \
            and liq["spread_vs_atr"] > r.max_spread_vs_atr:
        b.append(f"spread is {liq['spread_vs_atr']:.2f} of the hourly ATR "
                 f"(above {r.max_spread_vs_atr})")
    if inp.market == "crypto" and liq.get("quote_volume_24h") is not None \
            and liq["quote_volume_24h"] < r.min_quote_volume_24h:
        b.append("24-hour volume below 20 million USDT")
    return b


def assess(sig: Signal, ev: ModuleEvidence | None, regime: str | None, now: datetime,
           market: str, r: Rules = DEFAULT) -> tuple[bool, list[str]]:
    """Whether one module signal may become a trade, with the reasons it may not."""
    why = []
    if ev is None or not ev.passed:
        res = f"{ev.mean_r:+.3f}R per trade after costs" if ev and ev.mean_r is not None \
            else "no result"
        why.append(f"{ev.name if ev else sig.module} has no proven edge (failed its "
                   f"test: {res})")
    elif ev.mean_r is not None and ev.mean_r <= 0:
        why.append("tested mean result after costs is not positive")
    if market == "crypto" and sig.side < 0:
        why.append("spot crypto cannot sell short")
    if ev is None or ev.kind == "bracket":
        if sig.reward_risk is None or sig.reward_risk < r.min_reward_risk:
            why.append("planned reward/risk below 2:1")
        if now - sig.bar_close > r.fresh_signal:
            why.append("signal bar is no longer the latest (not actionable)")
        if regime == "HIGH_VOLATILITY":
            why.append("high-volatility regime")
        elif (sig.side > 0 and regime != "TRENDING_UP") or \
                (sig.side < 0 and regime != "TRENDING_DOWN"):
            why.append(f"direction does not match the regime ({regime or 'unknown'})")
    return not why, why


def orchestrate(inp: Inputs, r: Rules = DEFAULT) -> Verdict:
    regime = inp.snapshot.get("regime")
    v = Verdict(inp.symbol, inp.market, NO_TRADE, inp.now.isoformat(), regime=regime)
    v.blockers = blockers(inp, r)
    eligible: list[tuple[float, Signal, ModuleEvidence]] = []
    for sig in inp.signals:
        ev = inp.evidence.get(sig.module)
        ok, why = assess(sig, ev, regime, inp.now, inp.market, r)
        v.candidates.append({"module": sig.module, "side": "buy" if sig.side > 0 else "sell",
                             "source": sig.source, "bar_close": sig.bar_close.isoformat(),
                             "eligible": ok, "why_not": why, "note": sig.note,
                             "expected_r": ev.mean_r if ev else None})
        if ok and ev is not None:
            eligible.append((ev.mean_r if ev.mean_r is not None else 0.0, sig, ev))
    if v.blockers:
        v.reasons.append("NO_TRADE: " + "; ".join(v.blockers))
        return v
    if not eligible:
        if not inp.covering:
            v.reasons.append("no strategy module trades this instrument")
        elif not inp.signals:
            proven = [c for c in inp.covering if (e := inp.evidence.get(c)) and e.passed]
            v.reasons.append("no signal now" + ("" if proven else
                             "; and no module for this instrument has a proven edge"))
        else:
            v.reasons.append("signals present, but none may trade: " + "; ".join(
                f"{c['module']}: {', '.join(c['why_not'])}" for c in v.candidates))
        return v
    exp, sig, ev = max(eligible, key=lambda t: t[0])
    v.action = BUY if sig.side > 0 else SELL
    v.module, v.side, v.expected_r = sig.module, sig.side, exp
    v.entry_ref, v.stop, v.reward_risk = sig.entry_ref, sig.stop, sig.reward_risk
    if sig.entry_ref is not None and sig.stop is not None and sig.reward_risk is not None:
        v.target = sig.entry_ref + sig.side * sig.reward_risk * abs(sig.entry_ref - sig.stop)
    evidence = ev.summary or (f"tested mean {exp:+.3f}R per trade after costs over "
                              f"{ev.trades or 0} trades")
    v.reasons.append(f"{ev.name}: {sig.note or ('buy' if sig.side > 0 else 'sell')}; "
                     f"{evidence}")
    v.reasons.append("a proposal only: the risk engine must approve before any order")
    return v
