"""Signal fusion: one structured, explained decision per symbol.

Principles (deep-upgrade brief, Phase 7):

* Only sources with OUT-OF-SAMPLE evidence can create a trade: a strategy that the selector
  finds eligible in the current regime, or an activated (validated) model. Indicators,
  multi-timeframe scores, order book, funding, BTC context and news are CONTEXT: they appear
  as reasons, conflicts or blockers but never add "votes". Correlated indicators therefore
  cannot be double-counted, and models trained on the same data are not treated as
  independent confirmations.
* Probability and expected return come from measured evidence: the strategy's out-of-sample
  win rate and net expectancy in this regime (with the sample size), or the model's
  calibrated probability. No arbitrary confidence scores.
* Output is LONG, SHORT, WAIT (a validated edge exists for this regime but there is no
  setup now, or sources conflict) or NO_TRADE (no validated edge, or a hard blocker).
* The independent risk engine still decides afterwards; fusion never sizes an order on its
  own (the position size here is a recommendation from the same sizing rules).
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta
from typing import Any

from nexus_decision.selector import Assessment

LONG, SHORT, WAIT, NO_TRADE = "LONG", "SHORT", "WAIT", "NO_TRADE"
TF_MINUTES = {"1m": 1, "3m": 3, "5m": 5, "15m": 15, "30m": 30, "1h": 60, "2h": 120,
              "4h": 240, "6h": 360, "12h": 720, "1d": 1440}


@dataclass(frozen=True)
class Candidate:
    """A strategy's signal on the latest CLOSED bar (long only on spot)."""

    strategy: str
    timeframe: str
    bar_time: datetime
    entry: float
    stop: float
    target: float


@dataclass(frozen=True)
class ModelView:
    available: bool
    reason: str | None = None              # why not available
    version: str | None = None
    feature_version: str | None = None
    probability: float | None = None       # calibrated
    expected_return: float | None = None   # net, per trade
    disagreement: float | None = None
    out_of_distribution: bool = False


@dataclass(frozen=True)
class Context:
    regime: str | None
    risk_state: str | None                 # NORMAL ... EMERGENCY_STOP
    entries_allowed: bool
    data_fresh: bool
    spread_bps: float | None
    max_spread_bps: float = 25.0
    news_block: str | None = None
    x_block: str | None = None
    mtf_conflict: bool | None = None
    htf_trend: float | None = None
    btc_trend: float | None = None          # for non-BTC symbols
    funding_24h: float | None = None
    futures_enabled: bool = False
    max_disagreement: float = 0.10


@dataclass
class FusedSignal:
    symbol: str
    market: str
    decision: str
    generated_at: str
    expires_at: str | None = None
    strategy: str | None = None
    timeframe: str | None = None
    entry_type: str | None = None
    entry: float | None = None
    stop: float | None = None
    target: float | None = None
    reward_risk: float | None = None
    probability: float | None = None
    probability_source: str | None = None
    expected_return: float | None = None
    regime: str | None = None
    position_size_note: str | None = None
    model_version: str | None = None
    feature_version: str | None = None
    reasons: list[str] = field(default_factory=list)
    conflicts: list[str] = field(default_factory=list)
    blockers: list[str] = field(default_factory=list)
    sources: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def _blockers(ctx: Context, model: ModelView) -> list[str]:
    b = []
    if not ctx.entries_allowed:
        b.append(f"risk state {ctx.risk_state or 'unknown'} does not allow new trades")
    if not ctx.data_fresh:
        b.append("market data stale or missing")
    if ctx.spread_bps is None:
        b.append("no live order book (spread unknown)")
    elif ctx.spread_bps > ctx.max_spread_bps:
        b.append(f"spread {ctx.spread_bps:.1f} bps > {ctx.max_spread_bps:.0f} bps")
    if ctx.news_block:
        b.append(f"news: {ctx.news_block}")
    if ctx.x_block:
        b.append(f"X: {ctx.x_block}")
    if ctx.regime == "high_vol":
        b.append("high-volatility regime (never traded)")
    if model.available and model.out_of_distribution:
        b.append("model input is out of distribution")
    return b


def fuse(symbol: str, *, now: datetime, candidates: list[Candidate],
         assessments: list[Assessment], model: ModelView, ctx: Context,
         market: str = "spot") -> FusedSignal:
    sig = FusedSignal(symbol=symbol, market=market, decision=NO_TRADE,
                      generated_at=now.isoformat(), regime=ctx.regime)
    eligible = {a.strategy: a for a in assessments if a.eligible}
    sig.sources = {
        "strategies": {a.strategy: {"eligible": a.eligible, "why_not": list(a.reasons),
                                    "robust_expectancy": a.lower_bound,
                                    "oos_trades_in_regime": a.trades} for a in assessments},
        "model": {"available": model.available, "reason": model.reason,
                  "version": model.version},
        "setups_now": [c.strategy for c in candidates],
    }
    # Context, reported but never voting.
    if ctx.mtf_conflict:
        sig.conflicts.append("lower and higher timeframes lean opposite ways")
    if ctx.btc_trend is not None and ctx.btc_trend < -0.3:
        sig.conflicts.append("BTC trend is down")
    if ctx.funding_24h is not None and ctx.funding_24h > 0.0005:
        sig.conflicts.append(f"crowded longs (funding {ctx.funding_24h:.4%} per 8 h)")
    if model.available and model.disagreement is not None \
            and model.disagreement > ctx.max_disagreement:
        sig.conflicts.append(f"models disagree (spread {model.disagreement:.2f})")
    sig.blockers = _blockers(ctx, model)
    if not ctx.futures_enabled:
        sig.reasons.append("SHORT not available: spot is long-only and futures are disabled")
    if sig.blockers:
        sig.reasons.append("hard blocker present")
        return sig
    has_edge = bool(eligible) or (model.available and model.expected_return is not None)
    if not has_edge:
        sig.reasons.append(f"no strategy or model has a validated edge in the "
                           f"{ctx.regime or 'unknown'} regime")
        return sig
    setups = [c for c in candidates if c.strategy in eligible]
    if not setups and not (model.available and (model.expected_return or 0) > 0):
        sig.decision = WAIT
        sig.reasons.append("a validated edge exists for this regime, but no setup on the "
                           "latest closed bar")
        return sig
    if setups:
        best = max(setups, key=lambda c: eligible[c.strategy].lower_bound or -1e9)
        a = eligible[best.strategy]
        if model.available and model.expected_return is not None and model.expected_return <= 0:
            sig.decision = WAIT
            sig.conflicts.append("the validated model expects a loss after costs")
            sig.reasons.append(f"{best.strategy} has a setup, but the model disagrees")
            return sig
        risk = best.entry - best.stop
        sig.decision = LONG
        sig.strategy, sig.timeframe, sig.entry_type = best.strategy, best.timeframe, \
            "marketable limit (IOC) at the next bar"
        sig.entry, sig.stop, sig.target = best.entry, best.stop, best.target
        sig.reward_risk = (best.target - best.entry) / risk if risk > 0 else None
        if model.available and model.probability is not None:
            sig.probability, sig.probability_source = model.probability, \
                f"calibrated model {model.version}"
            sig.model_version, sig.feature_version = model.version, model.feature_version
        else:
            sig.probability = a.edge.get("win_rate")
            sig.probability_source = (f"out-of-sample win rate of {best.strategy} in "
                                      f"{ctx.regime} (n={a.trades})")
        sig.expected_return = a.expectancy
        minutes = TF_MINUTES.get(best.timeframe, 60)
        sig.expires_at = (best.bar_time + timedelta(minutes=2 * minutes)).isoformat()
        sig.reasons.append(f"{best.strategy} setup on the {best.timeframe} bar of "
                           f"{best.bar_time.isoformat()}; validated in {ctx.regime} "
                           f"(robust expectancy {a.lower_bound:.3%} per trade)")
        sig.position_size_note = ("risk engine sizes from equity x risk per trade / stop "
                                  "distance" + ("; halved while timeframes conflict"
                                                if ctx.mtf_conflict else ""))
        return sig
    # Model-only long (no strategy setup): the model must be validated and positive.
    sig.decision = LONG
    sig.probability, sig.probability_source = model.probability, \
        f"calibrated model {model.version}"
    sig.expected_return = model.expected_return
    sig.model_version, sig.feature_version = model.version, model.feature_version
    sig.reasons.append("validated model expects a positive net return")
    return sig
