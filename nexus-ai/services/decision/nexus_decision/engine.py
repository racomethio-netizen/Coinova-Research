"""Scores a candidate trade 0-100 from independent confirmations, then gates it.

Score components (sum to 100). Each is earned only when its rule is true:
  trend alignment (higher-timeframe structure agrees)      20
  structure event (BOS/CHoCH in the trade direction)       20
  level or zone confluence (entry at support/demand)       15
  liquidity sweep in the trade direction                   15
  volume expansion (volume z-score >= 1)                   10
  order-book imbalance in the trade direction              10
  BTC not moving against the trade (correlation check)     10

A trade is only ENTER if the score meets ``min_score`` AND no NO TRADE reason applies.
Every decision lists the earned and missed components, so the reasons are inspectable.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal

from nexus_decision.no_trade import DEFAULT, NoTradeInputs, Thresholds, no_trade_reasons

WEIGHTS: dict[str, int] = {
    "trend_alignment": 20, "structure_event": 20, "level_confluence": 15,
    "liquidity_sweep": 15, "volume_expansion": 10, "book_imbalance": 10, "btc_not_against": 10,
}


@dataclass(frozen=True)
class MarketSnapshot:
    side: str                             # "long" or "short"
    entry: Decimal
    stop: Decimal
    target: Decimal | None
    higher_trend: str                     # "up" | "down" | "undefined"
    structure_event: str | None           # e.g. "bos_up", "choch_down"
    at_level: bool                        # entry within tolerance of support/zone
    sweep: bool
    volume_z: float | None
    book_imbalance: float | None          # (bid-ask)/(bid+ask), -1..1
    btc_corr: float | None                # rolling correlation with BTC
    btc_trend: str | None                 # BTC structure trend


@dataclass(frozen=True)
class Decision:
    action: str                           # "ENTER" or "NO_TRADE"
    score: int
    reward_risk: float | None
    earned: tuple[str, ...]
    missed: tuple[str, ...]
    reasons: tuple[str, ...] = field(default_factory=tuple)
    explanation: str = ""


def _score(s: MarketSnapshot) -> tuple[int, list[str], list[str]]:
    want_up = s.side == "long"
    earned: list[str] = []
    missed: list[str] = []

    def check(name: str, ok: bool) -> None:
        (earned if ok else missed).append(name)

    check("trend_alignment", s.higher_trend == ("up" if want_up else "down"))
    ev = s.structure_event or ""
    check("structure_event", ev in (("bos_up", "choch_up") if want_up else ("bos_down", "choch_down")))
    check("level_confluence", s.at_level)
    check("liquidity_sweep", s.sweep)
    check("volume_expansion", s.volume_z is not None and s.volume_z >= 1.0)
    imb = s.book_imbalance
    check("book_imbalance", imb is not None and ((imb > 0.1) if want_up else (imb < -0.1)))
    btc_ok = (
        s.btc_corr is None or s.btc_trend is None or s.btc_corr < 0.5
        or s.btc_trend in ("up" if want_up else "down", "undefined")
    )
    check("btc_not_against", btc_ok)
    total = sum(WEIGHTS[n] for n in earned)
    return total, earned, missed


def decide(s: MarketSnapshot, x: NoTradeInputs, *, min_score: int = 60,
           thresholds: Thresholds | None = None) -> Decision:
    t = thresholds or DEFAULT
    score, earned, missed = _score(s)
    rr = _reward_risk(s)
    x = NoTradeInputs(**{**x.__dict__, "reward_risk": rr,
                         "setup_present": x.setup_present or bool(s.structure_event or s.sweep
                                                                  or s.at_level)})
    reasons = no_trade_reasons(x, t)
    if score < min_score:
        reasons.append(f"score {score} below the minimum {min_score}")
    action = "NO_TRADE" if reasons else "ENTER"
    explanation = _explain(action, s, score, rr, earned, missed, reasons)
    return Decision(action, score, rr, tuple(earned), tuple(missed), tuple(reasons), explanation)


def _reward_risk(s: MarketSnapshot) -> float | None:
    if s.target is None:
        return None
    risk = (s.entry - s.stop) if s.side == "long" else (s.stop - s.entry)
    reward = (s.target - s.entry) if s.side == "long" else (s.entry - s.target)
    if risk <= 0 or reward <= 0:
        return None
    return float(reward / risk)


def _explain(action: str, s: MarketSnapshot, score: int, rr: float | None,
             earned: list[str], missed: list[str], reasons: list[str]) -> str:
    rr_text = "n/a" if rr is None else f"{rr:.2f}"
    if action == "ENTER":
        return (f"ENTER {s.side}: score {score}/100, risk/reward {rr_text}. Confirmed by "
                f"{', '.join(earned)}.")
    return (f"NO TRADE: score {score}/100, risk/reward {rr_text}. "
            + ("Blocked by: " + "; ".join(reasons) + "." if reasons else ""))
